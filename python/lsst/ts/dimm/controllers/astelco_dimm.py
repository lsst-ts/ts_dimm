# This file is part of ts_dimm.
#
# Developed for the Vera Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the Vera Rubin Observatory
# Project (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
from collections import defaultdict
import enum
import re
import time

import numpy as np
import yaml

from .base_dimm import BaseDIMM, DIMMStatus
from lsst.ts.utils import index_generator


__all__ = ["AstelcoDIMM", "AstelcoCommand"]


index_gen = index_generator()

_LOCAL_HOST = "127.0.0.1"
_DEFAULT_PORT = 65432


def return_string():
    return str


_CmdType = defaultdict(return_string)

_CmdType["1"] = int
_CmdType["2"] = float


class CMDStatus(enum.IntEnum):
    DONE = enum.auto()
    ABORTED = enum.auto()
    WAITING = enum.auto()
    TIMEOUT = enum.auto()


class AstelcoCommand:
    """Represent the command interaction with the astelco controller."""

    def __init__(self, cmd, obj):
        self.id = next(index_gen)
        self.cmd = cmd
        self.object = obj
        self.send_time = time.time()
        self.cmd_complete_evt = asyncio.Event()
        self.cmd_complete_evt.clear()

        self.received = []
        self.events = []
        self.dtype = str
        self.status = None
        self.allstatus = []
        self.run = False
        self.ok = False
        self.complete = False
        self.data = []
        self.complete_time = None

    def encode(self):
        self.send_time = time.time()
        return f"{self.id} {self.cmd} {self.object}\r\n".encode()


class AstelcoDIMM(BaseDIMM):
    """This controller provides an interface to Astelco autonomous DIMMs.
    Astelco is providing the DIMM hardware and software controller for LSST
    and this controller interface is responsible for interfacing with their
    software.
    """

    def __init__(self, log):
        super().__init__(log)

        self.config = None

        self.check_interval = 180.0

        self.connection_timeout = 10.0
        self.read_timeout = 10.0

        self.read_level = None
        self.write_level = None

        self.connect_task = None
        self.reader = None
        self.writer = None

        self.cmd_lock = asyncio.Lock()
        self.cmd_list = {}
        self.cmd_max_size = 100
        self.controller_ready = False

        self.run_status_loop = True

        self.reply_handler_loop = None
        self.status_loop_future = None
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

        # A remote to weather station data
        self.ws_remote = None

        self.dimm_seeing = AstelcoCommand("GET", "EVENT")
        self.dimm_seeing_lowfreq = AstelcoCommand("GET", "EVENT")

        self.rain_value = False
        self.snow_value = False
        """Rain and snow values to be sent to the DIMM controller. This value
        is constructed with information from both rain and snow sensors which
        are captured by two different callback functions. I'll Keep it as a
        global value and set it whenever it is needed.
        """

        self._expect = [
            r"(?P<CMDID>\d+) DATA INLINE (?P<OBJECT>\S+)=(?P<VALUE>.+)",
            r"(?P<CMDID>\d+) DATA OK (?P<OBJECT>\S+)",
            r"(?P<CMDID>\d+) COMMAND (?P<STATUS>\S+)",
            r"(?P<CMDID>\d+) EVENT INFO (?P<OBJECT>\S+):(?P<ENCM>(.*?)\s*): (?P<VALUE>.+)",
            r"(?P<CMDID>\d+) EVENT ERROR (?P<OBJECT>\S+):(?P<ENCM>(.*?)\s*)",
        ]

    async def setup(self, config):
        """Setup controller.

        Parameters
        ----------
        config : `object`
            Configuration object
        """
        self.config = config

    def get_config_schema(self):
        return yaml.safe_load(
            """
$schema: http://json-schema.org/draft-07/schema#
description: Schema for RPiDataClient
type: object
properties:
  host:
    type: string
    default: 127.0.0.1
  port:
    type: integer
    default: 65432
  auto_auth:
    type: boolean
    default: false
  user:
    type: string
    default: admin
  password:
    type: string
    default: admin
"""
        )

    async def start(self):
        """Start DIMM. Overwrites method from base class."""

        await self.connect()

        # weather_callback updates information about:
        # - ambient_temp
        # - humidity
        # - pressure
        if self.ws_remote is None:
            self.status["status"] = DIMMStatus["ERROR"]
            raise RuntimeError("No WeatherStation remote available.")

        self.ws_remote.tel_weather.callback = self.weather_callback

        # self explanatory callbacks...
        self.ws_remote.tel_windSpeed.callback = self.wind_speed_callback
        self.ws_remote.tel_windDirection.callback = self.wind_direction_callback
        self.ws_remote.tel_dewPoint.callback = self.dew_point_callback
        self.ws_remote.tel_precipitation.callback = self.precipitation_callback
        self.ws_remote.tel_snowDepth.callback = self.snow_depth_callback

        # FIXME: Need to add callbacks for SKY module.
        # To force start of the DIMM we set this value to be lower than
        # the start operation limit (-20.).
        # cmd = AstelcoCommand("SET", f"SKY.TEMP=-30.")
        # self.cmd_list[cmd.id] = cmd
        # asyncio.create_task(self.run_command(cmd.id))
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def stop(self):
        """Stop DIMM. Overwrites method from base class."""

        if self.ws_remote is None:
            self.status["status"] = DIMMStatus["ERROR"]
            raise RuntimeError("No WeatherStation remote available.")

        self.ws_remote.tel_weather.callback = None
        self.ws_remote.tel_windSpeed.callback = None
        self.ws_remote.tel_windDirection.callback = None
        self.ws_remote.tel_dewPoint.callback = None
        self.ws_remote.tel_precipitation.callback = None
        self.ws_remote.tel_snowDepth.callback = None

        # FIXME: For action operations...
        # If the controller is stopped, force close out of the DIMM. If
        # will close anyway if value stops being updated.
        # To force stop of the DIMM we set this value to be higher than
        # the close operation limit (-10.).
        # cmd = AstelcoCommand("SET", f"SKY.TEMP=0.")
        # self.cmd_list[cmd.id] = cmd
        # asyncio.create_task(self.run_command(cmd.id))
        self.status_loop_future.cancel()

        # TODO: Change to STOPPED?
        self.status["status"] = DIMMStatus["INITIALIZED"]
        self.run_status_loop = False
        await self.disconnect()

    async def status_loop(self):
        """Monitor DIMM status and update `self.status` dictionary
        information.
        """
        while self.run_status_loop:
            try:
                ameba_mode = AstelcoCommand("GET", "AMEBA.MODE")
                scope_status = AstelcoCommand("GET", "SCOPE.STATUS")
                ra = AstelcoCommand("GET", "SCOPE.RA")
                dec = AstelcoCommand("GET", "SCOPE.DEC")
                altitude = AstelcoCommand("GET", "SCOPE.ALT")
                azimuth = AstelcoCommand("GET", "SCOPE.AZ")

                self.cmd_list[ameba_mode.id] = ameba_mode
                self.cmd_list[scope_status.id] = scope_status
                self.cmd_list[ra.id] = ra
                self.cmd_list[dec.id] = dec
                self.cmd_list[altitude.id] = altitude
                self.cmd_list[azimuth.id] = azimuth

                await asyncio.gather(
                    self.run_command(ameba_mode.id),
                    self.run_command(scope_status.id),
                    self.run_command(ra.id),
                    self.run_command(dec.id),
                    self.run_command(altitude.id),
                    self.run_command(azimuth.id),
                    ameba_mode.cmd_complete_evt.wait(),
                    scope_status.cmd_complete_evt.wait(),
                    ra.cmd_complete_evt.wait(),
                    dec.cmd_complete_evt.wait(),
                    altitude.cmd_complete_evt.wait(),
                    azimuth.cmd_complete_evt.wait(),
                )

                self.status["ra"] = ra.data[0]
                self.status["dec"] = dec.data[0]
                self.status["altitude"] = altitude.data[0]
                self.status["azimuth"] = azimuth.data[0]

                self.log.debug(f"AmebaMode = {ameba_mode.data}")
                # "0" means off, "1" means auto and "2" means manual.
                if ameba_mode.data[0] != "0":
                    self.status["status"] = DIMMStatus["RUNNING"]
                self.log.info(f"status: {self.status}")
            except Exception:
                self.log.exception("Error in status loop.")
                self.status["status"] = DIMMStatus["ERROR"]
                break

            await asyncio.sleep(1.0)

    async def connect(self):
        """Connect to the DIMM controller's TCP/IP."""
        async with self.cmd_lock:
            self.log.debug(f"connecting to: {self.config.host}:{self.config.port}")
            if self.connected:
                self.log.error("Already connected.")
                self.status["status"] = DIMMStatus["ERROR"]
                return

            try:
                self.connect_task = asyncio.open_connection(
                    host=self.config.host, port=self.config.port
                )

                self.reader, self.writer = await asyncio.wait_for(
                    self.connect_task, timeout=self.connection_timeout
                )

                # Read welcome message
                read_bytes = await asyncio.wait_for(
                    self.reader.readuntil("\n".encode()), timeout=self.read_timeout
                )

                if "TPL" not in read_bytes.decode().rstrip():
                    raise RuntimeError("No welcome message from controller.")

                self.log.debug(
                    f"connected: {read_bytes.decode().rstrip()} : Starting authentication"
                )

                if not self.config.auto_auth:
                    auth_str = (
                        f"AUTH PLAIN {self.config.user} {self.config.password}\r\n"
                    )

                    # Write authentication
                    self.writer.write(auth_str.encode())
                    await self.writer.drain()

                # Get reply from auth. This is published even in auto_auth mode

                read_bytes = await asyncio.wait_for(
                    self.reader.readuntil("\n".encode()), timeout=self.read_timeout
                )

                s = re.search(
                    r"AUTH\s+(?P<AUTH>\S+)\s+(?P<read_level>\d)\s+(?P<write_level>\d)\n",
                    read_bytes.decode(),
                )

                if not s or s.group("AUTH") != "OK":
                    await self.disconnect()
                    raise RuntimeError("Not authorized.")

                self.read_level = int(s.group("read_level"))
                self.write_level = int(s.group("write_level"))

                # Start loop to monitor replied.
                self.log.debug("Start controller reply handler.")
                self.reply_handler_loop = asyncio.create_task(self.reply_hander())

                # Start status loop
                self.log.debug("Start status loop.")
                self.run_status_loop = True
                self.status_loop_future = asyncio.create_task(self.status_loop())
            except Exception:
                self.log.exception("Error connecting to DIMM controller.")
                self.status["status"] = DIMMStatus["ERROR"]
            else:
                self.status["status"] = DIMMStatus["INITIALIZED"]

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port."""

        try:
            self.reply_handler_loop.cancel()
            await self.reply_handler_loop
        except asyncio.CancelledError:
            self.log.info("Reply handler task cancelled...")
        except Exception as e:
            # Something else may have happened. I still want to disable as this
            # will stop the loop on the target production
            self.log.exception(e)
        finally:
            self.reply_handler_loop = None

        self.log.debug("disconnect")

        writer = self.writer
        self.reader = None
        self.writer = None
        if writer:
            try:
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), timeout=2)
            finally:
                writer.close()

    async def get_measurement(self):
        """Wait and return new seeing measurements.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event.
        """

        try:
            timestamp = AstelcoCommand("GET", "DIMM.TIMESTAMP")
            seeing = AstelcoCommand("GET", "DIMM.SEEING")
            flux_left = AstelcoCommand("GET", "DIMM.FLUX_LEFT")
            flux_right = AstelcoCommand("GET", "DIMM.FLUX_RIGHT")
            airmass = AstelcoCommand("GET", "DIMM.AIRMASS")
            self.cmd_list[timestamp.id] = timestamp
            self.cmd_list[seeing.id] = seeing
            self.cmd_list[flux_left.id] = flux_left
            self.cmd_list[flux_right.id] = flux_right
            self.cmd_list[airmass.id] = airmass
            await asyncio.gather(
                self.run_command(timestamp.id),
                self.run_command(seeing.id),
                self.run_command(flux_left.id),
                self.run_command(flux_right.id),
                self.run_command(airmass.id),
                timestamp.cmd_complete_evt.wait(),
                seeing.cmd_complete_evt.wait(),
                flux_left.cmd_complete_evt.wait(),
                flux_right.cmd_complete_evt.wait(),
                airmass.cmd_complete_evt.wait(),
            )

            # seeing_lowfreq = AstelcoCommand("GET", "DIMM.SEEING_LOWFREQ")
            # flux_rms_left = AstelcoCommand("GET", "DIMM.FLUX_RMS_LEFT")
            # flux_rms_right = AstelcoCommand("GET", "DIMM.FLUX_RMS_RIGHT")
            # strehl_left = AstelcoCommand("GET", "DIMM.STREHL_LEFT")
            # strehl_right = AstelcoCommand("GET", "DIMM.STREHL_RIGHT")
            measurement = dict()

            measurement["hrNum"] = 0
            measurement["timestamp"] = timestamp.data[0]
            measurement["secz"] = airmass.data[0]
            measurement["fwhmx"] = -1
            measurement["fwhmy"] = -1
            measurement["fwhm"] = seeing.data[0]
            measurement["r0"] = -1
            measurement["nimg"] = 1
            measurement["dx"] = 0.0
            measurement["dy"] = 0.0
            measurement["fluxL"] = flux_left.data[0]
            measurement["scintL"] = 0
            measurement["strehlL"] = 0
            measurement["fluxR"] = flux_right.data[0]
            measurement["scintR"] = 0
            measurement["strehlR"] = 0
            measurement["flux"] = 0.0
            # await self.dimm_seeing.cmd_complete_evt.wait()
            return measurement
        except Exception:
            self.log.exception("Error in get measurement.")

    async def new_measurement(self):
        """Generate a new measurement by querying DIMM controller
        information.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event.
        """

        measurement = dict()

        measurement["hrNum"] = 0
        measurement["timestamp"] = self.measurement_start

        # altitude = AstelcoCommand("GET", "SCOPE.ALT")
        # self.cmd_list[altitude.id] = altitude
        # await self.run_command(altitude.id)
        # measurement['secz'] = 1./np.cos(np.radians(90.-altitude.data[0]))
        measurement["secz"] = 1.0

        measurement["fwhmx"] = -1
        measurement["fwhmy"] = -1

        # seeing = AstelcoCommand("GET", "DIMM.SEEING")
        # await self.run_command(seeing)

        measurement["fwhm"] = self.dimm_seeing.data
        self.dimm_seeing.cmd_complete_evt.clear()
        measurement["r0"] = -1
        measurement["nimg"] = 1
        measurement["dx"] = 0.0
        measurement["dy"] = 0.0
        measurement["fluxL"] = 0.0
        measurement["scintL"] = 0
        measurement["strehlL"] = 0
        measurement["fluxR"] = 0
        measurement["scintR"] = 0
        measurement["strehlR"] = 0
        measurement["flux"] = 0.0

        return measurement

    async def run_command(self, cmdid, want_connection=False):
        """Send a command to the TCP/IP controller and process its replies.

        Parameters
        ----------
        cmdid : `int`
            The id of the command to run. Command must be added to internal
            list or it will raise an exception.
        want_connection : bool
            Flag to specify if a connection is to be requested in case it is
            not connected.
        """

        self.log.debug(f"run_command: {self.cmd_list[cmdid].encode()}")

        async with self.cmd_lock:

            if not self.connected:
                if (
                    want_connection
                    and self.connect_task is not None
                    and not self.connect_task.done()
                ):
                    await self.connect_task
                else:
                    raise RuntimeError("Not connected and not trying to connect")
            elif cmdid not in self.cmd_list:
                raise RuntimeError(f"Command {cmdid} not in command list.")
            elif self.cmd_list[cmdid].run:
                raise RuntimeError(f"Command {cmdid} was already sent.")

            self.writer.write(self.cmd_list[cmdid].encode())
            self.cmd_list[cmdid].run = True
            await self.writer.drain()

    async def reply_hander(self):
        """Handle reply from controller. It will parse the responses and
        signals received from the controller and fill in the appropriate
        information.
        """

        while self.run_status_loop:
            try:
                self.log.debug("Wait for data")
                read_bytes = await asyncio.wait_for(
                    self.reader.readuntil("\n".encode()), timeout=None
                )
                self.log.debug(read_bytes)
                for exp in self._expect:

                    try:
                        re_exp = re.search(exp, read_bytes.decode().strip())
                    except Exception as e:
                        self.log.exception(e)
                        continue

                    if re_exp is not None:

                        cmdid = int(re_exp.group("CMDID"))

                        # cmdid == 0 are for events

                        if cmdid in self.cmd_list:
                            self.cmd_list[cmdid].received.append(re_exp)
                            try:
                                if "DATA INLINE" in read_bytes.decode():
                                    if "!TYPE" in read_bytes.decode():
                                        self.cmd_list[cmdid].dtype = _CmdType[
                                            re_exp.group("VALUE")
                                        ]
                                    else:
                                        self.cmd_list[cmdid].data.append(
                                            self.cmd_list[cmdid].dtype(
                                                re_exp.group("VALUE").replace('"', "")
                                            )
                                        )
                                    break
                                elif "COMMAND" in read_bytes.decode():
                                    self.cmd_list[cmdid].status = re_exp.group("STATUS")
                                    self.cmd_list[cmdid].allstatus.append(
                                        re_exp.group("STATUS")
                                    )
                                    if self.cmd_list[cmdid].status == "OK":
                                        self.cmd_list[cmdid].ok = True
                                    elif self.cmd_list[cmdid].status == "COMPLETE":
                                        self.cmd_list[cmdid].complete = True
                                        self.cmd_list[cmdid].complete_time = time.time()
                                        self.cmd_list[cmdid].cmd_complete_evt.set()
                                    break
                                elif "EVENT ERROR" in read_bytes.decode():
                                    self.cmd_list[cmdid].events.append(
                                        re_exp.group("ENCM")
                                    )
                                    break
                            except Exception:
                                self.cmd_list[cmdid].ok = False
                                self.cmd_list[cmdid].complete = True
                                self.cmd_list[cmdid].complete_time = time.time()
                                self.cmd_list[cmdid].cmd_complete_evt.set()
                                self.log.exception(
                                    "Error parsing command: "
                                    f"{read_bytes.decode().rstrip()}"
                                )
                        elif re_exp.group("OBJECT") == "DIMM.SEEING":
                            self.dimm_seeing.data = np.float(
                                re_exp.group("VALUE").split()[0]
                            )
                            self.dimm_seeing.complete_time = time.time()
                            self.dimm_seeing.cmd_complete_evt.set()
                            break
                        elif re_exp.group("OBJECT") == "DIMM.SEEING_LOWFREQ":
                            self.dimm_seeing_lowfreq.data = np.float(
                                re_exp.group("VALUE").split()[0]
                            )
                            self.dimm_seeing_lowfreq.complete_time = time.time()
                            self.dimm_seeing_lowfreq.cmd_complete_evt.set()
                            break
            except asyncio.IncompleteReadError as e:
                self.log.debug(f"Incomplete read error... Got {len(e.partial)}...")
                if len(e.partial) > 0:
                    self.log.debug(e.partial)
            except Exception as e:
                self.log.exception(e)
            finally:
                self.log.debug("Cleaning up")
                # Clean up old commands
                if len(self.cmd_list) > self.cmd_max_size:
                    for i in range(len(self.cmd_list) - self.cmd_max_size):
                        del_cmdid = next(iter(self.cmd_list))
                        self.log.debug(f"Deleting {del_cmdid}")
                        del self.cmd_list[del_cmdid]
                self.log.debug("Cleaning done")

    @property
    def connected(self):
        if None in (self.reader, self.writer):
            return False
        return True

    async def weather_callback(self, data):
        """Sends information about; ambient_temp (C), humidity (%) and
        pressure (mBar) to the DIMM.

        The DIMM uses weather information to stablish if it should operate or
        not. If information is not continuously publish the DIMM will close
        due to safety issues.
        """

        cmd = AstelcoCommand("SET", f"WEATHER.TEMP_AMB={data.ambient_temp}")
        self.cmd_list[cmd.id] = cmd
        await self.run_command(cmd.id)

        cmd = AstelcoCommand("SET", f"WEATHER.RH={data.humidity}")
        self.cmd_list[cmd.id] = cmd
        await self.run_command(cmd.id)

        cmd = AstelcoCommand("SET", f"WEATHER.PRESSURE={data.pressure}")
        self.cmd_list[cmd.id] = cmd
        await self.run_command(cmd.id)

    async def wind_speed_callback(self, data):
        """Sends information about wind speed (m/s) to the DIMM.

        Uses 2 minutes average information from weather station if average
        contains a valid values (>0), otherwise sends instantaneous if valid
        and don't update if none are valid. Note that this may cause the
        DIMM to shut-off if to many cycles are lost.
        """
        if data.avg2M > 0.0:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND={data.avg2M}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)
        elif data.value >= 0.0:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND={data.value}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)

    async def wind_direction_callback(self, data):
        """Sends information about wind direction (degrees, clockwise from
         due north) to the DIMM.

        Uses 2 minutes average information from weather station if average
        contains a valid values (>0), otherwise sends instantaneous if valid
        and don't update if none are valid. Note that this may cause the
        DIMM to shut-off if to many cycles are lost.
        """
        if data.avg2M > 0.0:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND_DIR={data.avg2M}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)
        elif data.value >= 0.0:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND_DIR={data.value}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)

    async def dew_point_callback(self, data):
        """Sends information about dew point (C) to the DIMM.

        Uses 1 minute average information from weather station if average
        contains a valid values (>0), otherwise don't update it. Note that
        this may cause the DIMM to shut-off if to many cycles are lost.
        """
        if data.avg1M > -99.0:
            cmd = AstelcoCommand("SET", f"WEATHER.TEMP_DEW={data.avg1M}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)

    async def precipitation_callback(self, data):
        """Sends information about rain to the DIMM.

        0 = no precipitation
        1 = rain/snow
        """
        if data.prSum1M > -99.0:
            self.rain_value = data.prSum1M > 0.0
            rain_value = int(self.rain_value or self.snow_value)
            cmd = AstelcoCommand("SET", f"WEATHER.RAIN={rain_value}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)

    async def snow_depth_callback(self, data):
        """Sends information about snow to the DIMM.

        0 = no precipitation
        1 = rain/snow
        """
        if data.avg1M > -99.0:
            self.snow_value = data.avg1M > 0.0
            snow_value = int(self.snow_value or self.rain_value)
            cmd = AstelcoCommand("SET", f"WEATHER.RAIN={snow_value}")
            self.cmd_list[cmd.id] = cmd
            await self.run_command(cmd.id)
