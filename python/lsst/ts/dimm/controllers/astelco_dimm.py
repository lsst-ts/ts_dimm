import time
import re
import asyncio
from collections import defaultdict
import enum
import numpy as np

from .base_dimm import BaseDIMM, DIMMStatus

import SALPY_Environment

from lsst.ts.salobj import Remote, index_generator


__all__ = ['AstelcoDIMM', 'AstelcoCommand']


index_gen = index_generator()

_LOCAL_HOST = "127.0.0.1"
_DEFAULT_PORT = 65432


def return_string():
    return str


_CmdType = defaultdict(return_string)

_CmdType['1'] = int
_CmdType['2'] = float


class CMDStatus(enum.IntEnum):
    DONE = enum.auto()
    ABORTED = enum.auto()
    WAITING = enum.auto()
    TIMEOUT = enum.auto()


class AstelcoCommand:
    """Represent the command interaction with the astelco controller.
    """

    id = 0
    cmd = None
    object = None
    received = []
    events = []
    dtype = str
    status = None
    allstatus = []
    ok = False
    complete = False
    data = []

    def __init__(self, cmd, obj):
        self.id = next(index_gen)
        self.cmd = cmd
        self.object = obj
        self.send_time = time.time()

    def encode(self):
        self.send_time = time.time()
        return f"{self.id} {self.cmd} {self.object}\r\n".encode()


class AstelcoDIMM(BaseDIMM):
    """This controller provides an interface to Astelco autonomous DIMMs.
    Astelco is providing the DIMM hardware and software controller for LSST
    and this controller interface is responsible for interfacing with their
    software.
    """

    def __init__(self):
        super().__init__()

        self.host = _LOCAL_HOST
        self.port = _DEFAULT_PORT
        self.user = "admin"
        self.password = "admin"

        self.check_interval = 180.

        self.connection_timeout = 10.
        self.read_timeout = 10.

        self.connected = False

        self.read_level = None
        self.write_level = None

        self.connect_task = None
        self.reader = None
        self.writer = None

        self.cmd_lock = asyncio.Lock()
        self.controller_ready = False

        self.measurement_loop = None
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

        # A remote to weather station data
        self.ws_remote = Remote(SALPY_Environment)

        self.rain_value = False
        self.snow_value = False
        """Rain and snow values to be sent to the DIMM controller. This value
        is constructed with information from both rain and snow sensors which
        are captured by two different callback functions. I'll Keep it as a
        global value and set it whenever it is needed.
        """

        self._expect = [r'(?P<CMDID>\d+) DATA INLINE (?P<OBJECT>\S+)=(?P<VALUE>.+)',
                        r'(?P<CMDID>\d+) DATA OK (?P<OBJECT>\S+)',
                        r'(?P<CMDID>\d+) COMMAND (?P<STATUS>\S+)',
                        r'(?P<CMDID>\d+) EVENT ERROR (?P<OBJECT>\S+):(?P<ENCM>(.*?)\s*)']

    def start(self):
        """Start DIMM. Overwrites method from base class."""

        self.connect()

        # weather_callback updates information about:
        # - ambient_temp
        # - humidity
        # - pressure
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
        cmd = AstelcoCommand("SET", f"SKY.TEMP=-30.")
        asyncio.ensure_future(self.run_command(cmd))

        self.status['status'] = DIMMStatus['RUNNING']

    def stop(self):
        """Stop DIMM. Overwrites method from base class."""

        self.ws_remote.tel_weather.callback = None
        self.ws_remote.tel_windSpeed.callback = None
        self.ws_remote.tel_windDirection.callback = None
        self.ws_remote.tel_dewPoint.callback = None
        self.ws_remote.tel_precipitation.callback = None
        self.ws_remote.tel_snowDepth.callback = None

        # If the controller is stopped, force close out of the DIMM. If
        # will close anyway if value stops being updated.
        # To force stop of the DIMM we set this value to be higher than
        # the close operation limit (-10.).
        cmd = AstelcoCommand("SET", f"SKY.TEMP=0.")
        asyncio.ensure_future(self.run_command(cmd))

        self.status['status'] = DIMMStatus['INITIALIZED']
        asyncio.ensure_future(self.disconnect())

    async def status_loop(self):
        """Monitor DIMM status and update `self.status` dictionary
        information.
        """
        while True:
            try:

                scope_status = AstelcoCommand("GET", "SCOPE.STATUS")
                ra = AstelcoCommand("GET", "SCOPE.RA")
                dec = AstelcoCommand("GET", "SCOPE.DEC")
                altitude = AstelcoCommand("GET", "SCOPE.ALTITUDE")
                azimuth = AstelcoCommand("GET", "SCOPE.AZIMUTH")

                await asyncio.gather(scope_status,
                                     ra,
                                     dec,
                                     altitude,
                                     azimuth)

                self.status['ra'] = ra.data[0]
                self.status['dec'] = dec.data[0]
                self.status['altitude'] = altitude.data[0]
                self.status['azimuth'] = azimuth.data[0]

            except Exception:
                pass

            await asyncio.sleep(0.5)

    async def connect(self):
        """Connect to the DIMM controller's TCP/IP.
        """
        self.log.debug(f"connecting to: {self.host}:{self.port}")
        if self.connected:
            raise RuntimeError("Already connected.")

        self.connect_task = asyncio.open_connection(host=self.host, port=self.port)

        self.reader, self.writer = await asyncio.wait_for(self.connect_task,
                                                          timeout=self.connection_timeout)

        # Read welcome message
        await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                               timeout=self.read_timeout)

        read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                            timeout=self.read_timeout)

        if "TPL" not in read_bytes.decode().rstrip():
            raise RuntimeError("No welcome message from controller.")

        self.log.debug(f"connected: {read_bytes.decode().rstrip()} : Starting authentication")

        auth_str = f"AUTH PLAIN {self.user} {self.password}\r\n"

        # Write authentication
        self.writer.write(auth_str.encode())
        await self.writer.drain()

        # Get reply:

        read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                            timeout=self.read_timeout)

        s = re.search(r'AUTH\s+(?P<AUTH>\S+)\s+(?P<read_level>\d)\s+(?P<write_level>\d)\n',
                      read_bytes.decode())

        if not s[1] or s[1].group('AUTH') != 'OK':
            self.disconnect()
            raise RuntimeError('Not authorized.')

        self.read_level = int(s[1].group('read_level'))
        self.write_level = int(s[1].group('write_level'))

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port.
        """
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
            A dictionary with the same values of the dimmMeasurement topic SAL Event.
        """

        while True:
            try:
                timestamp = AstelcoCommand("GET", "DIMM.TIMESTAMP")
                await self.run_command(timestamp)

                if self.measurement_start is None or timestamp.data[0] > self.measurement_start:
                    self.measurement_start = timestamp.data[0]
                    return await self.new_measurement()

                await asyncio.sleep(self.check_interval)

            except Exception:
                return None

    async def new_measurement(self):
        """Generate a new measurement by querying DIMM controller
        information.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL Event.
        """

        measurement = dict()

        measurement['hrNum'] = -1
        measurement['timestamp'] = self.measurement_start

        altitude = AstelcoCommand("GET", "SCOPE.ALT")
        await self.run_command(altitude)
        measurement['secz'] = 1./np.cos(np.radians(90.-altitude.data[0]))

        measurement['fwhmx'] = -1
        measurement['fwhmy'] = -1

        seeing = AstelcoCommand("GET", "DIMM.SEEING")
        await self.run_command(seeing)

        measurement['fwhm'] = seeing.data[0]
        measurement['r0'] = -1
        measurement['nimg'] = 1
        measurement['dx'] = 0.
        measurement['dy'] = 0.
        measurement['fluxL'] = 0.
        measurement['scintL'] = 0
        measurement['strehlL'] = 0
        measurement['fluxR'] = 0
        measurement['scintR'] = 0
        measurement['strehlR'] = 0
        measurement['flux'] = 0.

        return measurement

    async def run_command(self, astelco_command, want_connection=False):
        """Send a command to the TCP/IP controller and process its replies.

        Parameters
        ----------
        astelco_command : `AstelcoCommand`
            The command to send.
        want_connection : bool
            Flag to specify if a connection is to be requested in case it is
            not connected.
        """

        self.log.debug(f"run_command: {astelco_command}")

        if not self.connected:
            if want_connection and self.connect_task is not None and not self.connect_task.done():
                await self.connect_task
            else:
                raise RuntimeError("Not connected and not trying to connect")
        async with self.cmd_lock:

            self.writer.write(astelco_command.encode())
            await self.writer.drain()

            while not astelco_command.complete:
                read_bytes = await asyncio.wait_for(self.reader.readuntil("\r\n".encode()),
                                                    timeout=self.read_timeout)

                for exp in self._expect:
                    re_exp = re.search(exp, read_bytes.decode())
                    if re_exp:
                        cmdid = int(re_exp[1].group('CMDID'))
                        if cmdid == astelco_command.id:
                            astelco_command.received.append(re_exp[2])
                            try:
                                if 'DATA INLINE' in re_exp[2]:
                                    if '!TYPE' in re_exp[2]:
                                        astelco_command.dtype = _CmdType[re_exp[1].group('VALUE')]
                                    else:
                                        self.commands_sent[cmdid].data.append(
                                            astelco_command.dtype(re_exp[1].group('VALUE').replace('"', '')))
                                elif 'COMMAND' in re_exp[2]:
                                    astelco_command.status = re_exp[1].group('STATUS')
                                    astelco_command.allstatus.append(re_exp[1].group('STATUS'))
                                    if astelco_command.status == 'OK':
                                        astelco_command.ok = True
                                    elif astelco_command.status == 'COMPLETE':
                                        astelco_command.complete = True

                                elif 'EVENT ERROR' in re_exp[2]:
                                    self.commands_sent[cmdid].events.append(re_exp[1].group('ENCM'))

                            except Exception as e:
                                self.log.error(f'Error in command: {re_exp[2].rstrip()}')
                                astelco_command.ok = False
                                astelco_command.complete = True
                                self.log.exception(e)
                                break

                if time.time() > astelco_command.send_time + self.cmd_timeout:
                    self.log.warning(f'Command {astelco_command.id} timed out! Marking as '
                                     f'complete with status TIMEOUT.')
                    astelco_command.complete = True
                    astelco_command.ok = False
                    astelco_command.status = 'TIMEOUT'
                    break

                await asyncio.sleep(0.05)

            return astelco_command.ok

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
        await self.run_command(cmd)

        cmd = AstelcoCommand("SET", f"WEATHER.RH={data.humidity}")
        await self.run_command(cmd)

        cmd = AstelcoCommand("SET", f"WEATHER.PRESSURE={data.pressure}")
        await self.run_command(cmd)

    async def wind_speed_callback(self, data):
        """Sends information about wind speed (m/s) to the DIMM.

        Uses 2 minutes average information from weather station if average
        contains a valid values (>0), otherwise sends instantaneous if valid
        and don't update if none are valid. Note that this may cause the
        DIMM to shut-off if to many cycles are lost.
        """
        if data.avg2M > 0.:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND={data.avg2M}")
            await self.run_command(cmd)
        elif data.value >= 0.:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND={data.value}")
            await self.run_command(cmd)

    async def wind_direction_callback(self, data):
        """Sends information about wind direction (degrees, clockwise from
         due north) to the DIMM.

        Uses 2 minutes average information from weather station if average
        contains a valid values (>0), otherwise sends instantaneous if valid
        and don't update if none are valid. Note that this may cause the
        DIMM to shut-off if to many cycles are lost.
        """
        if data.avg2M > 0.:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND_DIR={data.avg2M}")
            await self.run_command(cmd)
        elif data.value >= 0.:
            cmd = AstelcoCommand("SET", f"WEATHER.WIND_DIR={data.value}")
            await self.run_command(cmd)

    async def dew_point_callback(self, data):
        """Sends information about dew point (C) to the DIMM.

        Uses 1 minute average information from weather station if average
        contains a valid values (>0), otherwise don't update it. Note that
        this may cause the DIMM to shut-off if to many cycles are lost.
        """
        if data.avg1M > -99.:
            cmd = AstelcoCommand("SET", f"WEATHER.TEMP_DEW={data.avg1M}")
            await self.run_command(cmd)

    async def precipitation_callback(self, data):
        """Sends information about rain to the DIMM.

        0 = no precipitation
        1 = rain/snow
        """
        if data.prSum1M > -99.:
            self.rain_value = data.prSum1M > 0.
            rain_value = int(self.rain_value or self.snow_value)
            cmd = AstelcoCommand("SET", f"WEATHER.RAIN={rain_value}")
            await self.run_command(cmd)

    async def snow_depth_callback(self, data):
        """Sends information about snow to the DIMM.

        0 = no precipitation
        1 = rain/snow
        """
        if data.avg1M > -99.:
            self.snow_value = data.avg1M > 0.
            snow_value = int(self.snow_value or self.rain_value)
            cmd = AstelcoCommand("SET", f"WEATHER.RAIN={snow_value}")
            await self.run_command(cmd)
