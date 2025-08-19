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
__all__ = ["AstelcoDIMM"]

import asyncio
import enum
import math
from collections import defaultdict
from statistics import mean

import yaml
from lsst.ts.utils import make_done_future, tai_from_utc

from ..utils import dict_to_namespace
from .astelco_enums import RainState, SkyStatus
from .base_dimm import AutomationMode, BaseDIMM, DIMMStatus
from .mock_astelco_dimm import MockAstelcoDIMM
from .open_tpl_connection import OpenTplConnection

# Interval between status requests (seconds)
STATUS_INTERVAL = 1.0


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


class AstelcoDIMM(BaseDIMM):
    """Client for an Astelco autonomous DIMM."""

    def __init__(self, log, simulate=False):
        super().__init__(log=log, simulate=simulate)

        self.config = None

        self.mock_master_port = None
        self.mock_meteo_port = None

        self.master = None
        self.meteo = None

        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

        # A remote to weather station data
        self.ws_remote = None

        # The DIMM WEATHER.RAIN is set based on two separate weather station
        # topics: evt_precipitation.
        self.is_raining = False
        self.is_snowing = False

        self.status_loop_task = make_done_future()

    async def setup(self, config):
        """Setup controller.

        Parameters
        ----------
        config : `object`
            Configuration object
        """
        self.config = dict_to_namespace(config)
        self.master = OpenTplConnection(self.log, self.simulate)
        self.meteo = OpenTplConnection(self.log, self.simulate)
        await self.master.setup(self.config.master)
        await self.meteo.setup(self.config.meteo)

    def get_config_schema(self):
        return yaml.safe_load(
            """
$schema: http://json-schema.org/draft-07/schema#
description: Schema for Astelco DIMM configuration with master and meteo endpoints.
type: object
properties:
  master:
    $ref: "#/definitions/connection"
  meteo:
    $ref: "#/definitions/connection"
required: [master, meteo]

definitions:
  connection:
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

        if self.ws_remote is None:
            self.status["status"] = DIMMStatus["ERROR"]
            raise RuntimeError("No WeatherStation remote available")

        if not self.simulate:
            # Set weather station callbacks.
            self.ws_remote.tel_temperature.callback = self.temperature_callback
            self.ws_remote.tel_relativeHumidity.callback = self.humidity_callback
            self.ws_remote.tel_pressure.callback = self.pressure_callback
            self.ws_remote.tel_airFlow.callback = self.air_flow_callback
            self.ws_remote.tel_dewPoint.callback = self.dew_point_callback
            self.ws_remote.evt_precipitation.callback = self.precipitation_callback

        # Set SKY to values that allow automatic operation if WEATHER data
        # is acceptable (as set by the weather station callbacks).
        # SKY.TEMP must be <= 20 to run (with the default DIMM config).
        await self.meteo.run_command("SET", "SKY.TEMP=-25.0")
        await self.meteo.run_command("SET", f"SKY.status={SkyStatus.CLEAR}")
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def stop(self):
        """Stop DIMM. Overwrites method from base class."""

        if self.ws_remote is not None:
            self.ws_remote.tel_temperature.callback = None
            self.ws_remote.tel_relativeHumidity.callback = None
            self.ws_remote.tel_pressure.callback = None
            self.ws_remote.tel_airFlow.callback = None
            self.ws_remote.tel_dewPoint.callback = None
            self.ws_remote.evt_precipitation.callback = None

        # TODO: Change to STOPPED?
        self.status["status"] = DIMMStatus["INITIALIZED"]

        # Tell the DIMM to close up (1=preciptating)
        if self.connected:
            await self.meteo.run_command("SET", f"SKY.status={SkyStatus.PRECIPTATING}")
            self.master.stop()
            self.meteo.stop()
            await self.disconnect()

    async def status_loop(self):
        """Monitor DIMM status and update `self.status` dictionary
        information.
        """
        self.log.debug("Status loop begins")
        try:
            while self.connected:
                status_cmd = await self.master.run_command(
                    "GET",
                    "AMEBA.MODE;SCOPE.RA;SCOPE.DEC;SCOPE.ALT;SCOPE.AZ",
                )

                # AMEBA.MODE is supposed to be an integer, but the only values
                # we have seen are "0" and "LOCKEDBY 21474836481"
                ameba_mode = status_cmd.get_value("AMEBA.MODE", dtype=str, bad_value="")
                self.status["ra"] = status_cmd.get_float("SCOPE.RA")
                self.status["dec"] = status_cmd.get_float("SCOPE.DEC")
                self.status["altitude"] = status_cmd.get_float("SCOPE.ALT")
                self.status["azimuth"] = status_cmd.get_float("SCOPE.AZ")

                self.log.debug(f"AMEBA.MODE = {ameba_mode}")
                # "0" means off, "1" means auto and "2" means manual.
                if ameba_mode != "0":
                    self.status["status"] = DIMMStatus["RUNNING"]

                await asyncio.sleep(STATUS_INTERVAL)
        except Exception:
            self.log.exception("Status loop failed")
            self.status["status"] = DIMMStatus["ERROR"]
        finally:
            self.log.debug("Status loop ends")

    async def ameba_loop(self):
        """Monitor DIMM status and update `self.status` dictionary
        information.
        """
        self.log.debug("AMEBA loop begins")
        try:
            while self.connected:
                status_cmd = await self.master.run_command(
                    "GET",
                    "AMEBA.MODE;AMEBA.STATE;AMEBA.SUN_ALT;AMEBA.CONDITION;AMEBA.START_TIME;AMEBA.FINISH_TIME",
                )
                self.ameba["mode"] = status_cmd.get_int("AMEBA.MODE", bad_value=-1)
                self.ameba["state"] = status_cmd.get_int("AMEBA.STATE", bad_value=-1)
                self.ameba["sunAltitude"] = status_cmd.get_float("AMEBA.SUN_ALT")
                self.ameba["condition"] = status_cmd.get_int(
                    "AMEBA.CONDITION", bad_value=-1
                )
                self.ameba["startTime"] = status_cmd.get_float("AMEBA.START_TIME")
                self.ameba["finishTime"] = status_cmd.get_float("AMEBA.FINISH_TIME")
                if not math.isnan(self.ameba["startTime"]):
                    self.ameba["startTime"] = tai_from_utc(self.ameba["startTime"])
                if not math.isnan(self.ameba["finishTime"]):
                    self.ameba["finishTime"] = tai_from_utc(self.ameba["finishTime"])

                await asyncio.sleep(STATUS_INTERVAL)
        except Exception:
            self.log.exception("AMEBA loop failed")
            self.status["status"] = DIMMStatus["ERROR"]
        finally:
            self.log.debug("AMEBA loop ends")

    async def connect(self):
        """Connect to the DIMM controller's TCP/IP."""
        try:
            if self.connected:
                self.log.error("Already connected")
                self.status["status"] = DIMMStatus["ERROR"]
                return

            self.status_loop_task.cancel()

            master_port = 0
            meteo_port = 0
            if self.simulate:
                self.mock_master_port = MockAstelcoDIMM(
                    port=0,
                    log=self.log,
                    require_authentication=not self.config.master.auto_auth,
                )
                dimm_state = self.mock_master_port.get_dimm_state()
                self.mock_meteo_port = MockAstelcoDIMM(
                    port=0,
                    log=self.log,
                    require_authentication=not self.config.meteo.auto_auth,
                    dimm_state=dimm_state,
                )
                await self.mock_master_port.start_task
                await self.mock_meteo_port.start_task
                master_port = self.mock_master_port.port
                meteo_port = self.mock_meteo_port.port

            await self.master.connect(port=master_port)
            await self.meteo.connect(port=meteo_port)

            if self.simulate:
                # In the long run this may need to be called at regular
                # intervals, but for now the mock controller
                # just needs it sent once.
                await self.report_good_mock_weather()

            # Start status and ameba loop. Wrapping `gather`
            # in `create_task` avoids an error when the
            # GatheringFuture is ignored.
            self.status_loop_task = asyncio.gather(
                self.status_loop(),
                self.ameba_loop(),
            )

        except Exception:
            self.log.exception("Error connecting to DIMM controller")
            self.status["status"] = DIMMStatus["ERROR"]
        else:
            self.status["status"] = DIMMStatus["INITIALIZED"]

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port."""

        self.log.debug("Disconnect")

        self.status_loop_task.cancel()
        try:
            await self.status_loop_task
        except asyncio.CancelledError:
            pass  # Expected

        await self.master.disconnect()
        await self.meteo.disconnect()

    def error_status_callback(self) -> None:
        self.status["status"] = DIMMStatus["ERROR"]

    async def set_automation_mode(self, mode: AutomationMode) -> None:
        """Sets the DIMM to off (0), automatic (1), or manual (2) operation.

        Parameter
        ---------
        mode : AutomationMode
            Desired DIMM operating mode.
        """
        await self.master.run_command("SET", f"AMEBA.MODE={mode.value}")
        await self.meteo.run_command("SET", f"AMEBA.MODE={mode.value}")

    async def get_measurement(self):
        """Wait and return new seeing measurements.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event.
        """

        prev_timestamp = 0.0
        try:
            timestamp_cmd = await self.master.run_command("GET", "DIMM.TIMESTAMP")
            timestamp = timestamp_cmd.get_float("DIMM.TIMESTAMP")
            if timestamp == prev_timestamp:
                return None
            prev_timestamp = timestamp

            seeing_cmd = await self.master.run_command(
                "GET",
                "DIMM.SEEING;DIMM.AIRMASS;"
                "DIMM.FLUX_LEFT;DIMM.FLUX_RIGHT;"
                "DIMM.STREHL_LEFT;DIMM.STREHL_RIGHT",
            )

            # seeing_lowfreq = AstelcoCommand("GET", "DIMM.SEEING_LOWFREQ")
            # flux_rms_left = AstelcoCommand("GET", "DIMM.FLUX_RMS_LEFT")
            # flux_rms_right = AstelcoCommand("GET", "DIMM.FLUX_RMS_RIGHT")
            measurement = dict()

            measurement["hrNum"] = 0
            measurement["timestamp"] = timestamp
            measurement["secz"] = seeing_cmd.get_float("DIMM.AIRMASS")
            measurement["fwhmx"] = -1
            measurement["fwhmy"] = -1
            measurement["fwhm"] = seeing_cmd.get_float("DIMM.SEEING")
            measurement["r0"] = -1
            measurement["nimg"] = 1
            measurement["dx"] = 0.0
            measurement["dy"] = 0.0
            measurement["fluxL"] = seeing_cmd.get_float("DIMM.FLUX_LEFT")
            measurement["scintL"] = 0
            measurement["strehlL"] = seeing_cmd.get_float("DIMM.STREHL_LEFT")
            measurement["fluxR"] = seeing_cmd.get_float("DIMM.FLUX_RIGHT")
            measurement["scintR"] = 0
            measurement["strehlR"] = seeing_cmd.get_float("DIMM.STREHL_RIGHT")
            measurement["flux"] = 0.0
            return measurement
        except Exception:
            self.log.exception("Error in get measurement")
            raise

    @property
    def connected(self):
        if all((self.master.connected, self.meteo.connected)):
            return True
        return False

    async def temperature_callback(self, data):
        """Sends information about ambient temperature (C) to the DIMM."""
        if data.numChannels > 0:
            await self.meteo.run_command(
                "SET",
                f"WEATHER.TEMP_AMB={mean(data.temperatureItem[:data.numChannels])}",
            )

    async def humidity_callback(self, data):
        """Sends information about humidity (%) to the DIMM."""
        await self.meteo.run_command("SET", f"WEATHER.RH={data.relativeHumidityItem}")

    async def pressure_callback(self, data):
        """Sends information about pressure (mBar) to the DIMM."""
        if data.numChannels > 1:
            # Pressure values are in Pa. Convert to mBar by dividing it by
            # 100.
            await self.meteo.run_command(
                "SET",
                f"WEATHER.PRESSURE={mean(data.pressureItem[:data.numChannels])/100.0}",
            )

    async def air_flow_callback(self, data):
        """Sends information about wind speed (m/s) and direction to the
        DIMM.
        """
        if data.speed >= 0.0:
            await self.meteo.run_command("SET", f"WEATHER.WIND={data.speed}")
        if data.direction >= 0.0:
            await self.meteo.run_command("SET", f"WEATHER.WIND_DIR={data.direction}")

    async def dew_point_callback(self, data):
        """Send dew point (C) to the DIMM."""
        if data.dewPoint > -99.0:
            await self.meteo.run_command("SET", f"WEATHER.TEMP_DEW={data.dewPoint}")

    async def precipitation_callback(self, data):
        """Set self.is_raining/self.is_snowing and update DIMM WEATHER.RAIN"""
        self.is_raining = data.raining
        self.is_snowing = data.snowing
        await self.set_weather_rain()

    async def set_weather_rain(self):
        """Set DIMM WEATHER.RAIN based on self.is_raining
        and self.is_snowing.
        """
        is_precipitating = self.is_raining or self.is_snowing
        rain_value = RainState.PRECIPITATION if is_precipitating else RainState.DRY
        await self.meteo.run_command("SET", f"WEATHER.RAIN={rain_value}")

    async def report_good_mock_weather(self):
        """Call the weather callbacks with data that allows auto operation.

        This can only be used if simulating.

        It calls the fewest callbacks required, so it will only enable
        operation if none of the other weather callbacks has been called
        with weather data that prevents automatic operation.

        Raises
        ------
        RuntimeError
            If not simulating.
        """
        if not self.simulate:
            raise RuntimeError("Only allowed in simulation mode")

        temperature_data = self.ws_remote.tel_temperature.DataType()
        temperature_data.numChannels = 1
        temperature_data.temperatureItem[0] = 0.0
        await self.temperature_callback(
            temperature_data,
        )
        await self.humidity_callback(
            self.ws_remote.tel_relativeHumidity.DataType(
                relativeHumidityItem=self.mock_meteo_port.config.HumLow * 0.9,
            )
        )
        pressure_data = self.ws_remote.tel_pressure.DataType()
        pressure_data.numChannels = 1
        pressure_data.pressureItem[0] = 0.5
        await self.pressure_callback(
            pressure_data,
        )

        await self.air_flow_callback(
            self.ws_remote.tel_airFlow.DataType(
                speed=self.mock_meteo_port.config.WindLow * 0.9,
                direction=90.0,
            )
        )
        await self.precipitation_callback(
            self.ws_remote.evt_precipitation.DataType(
                raining=False,
                snowing=False,
            )
        )

    @property
    def running_commands(self):
        running_master_commands = len(self.master.running_commands)
        running_meteo_commands = len(self.meteo.running_commands)
        return running_master_commands + running_meteo_commands
