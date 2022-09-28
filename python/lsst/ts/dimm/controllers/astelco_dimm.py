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
from collections import defaultdict
import enum
import math
import re

import yaml

from lsst.ts.tcpip import LOCAL_HOST, close_stream_writer
from lsst.ts.utils import index_generator, make_done_future
from .astelco_enums import TERMINATOR, RainState, SkyStatus

from .base_dimm import BaseDIMM, DIMMStatus
from .mock_astelco_dimm import MockAstelcoDIMM

# Interval between status requests (seconds)
STATUS_INTERVAL = 1.0

# Words that indicate that data for a specific variable
# could not be retrieved with a GET command.
# Check that the first word of the reported value matches any of these,
# since FAILED and LOCKEDBY replies include additional information.
BadDataReplies = {
    "BUSY",
    "DENIED",
    "DIMENSION",
    "FAILED",
    "INVALID",
    "LOCKEDBY",
    "TYPE",
    "UNKNOWN",
}


class CommandError(Exception):
    pass


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
    """An Astelco Command.

    Parameters
    ----------
    name : `str`
        Command name, e.g. "SET" or "GET"
    arg : `str`
        Command argument.
    """

    index_gen = index_generator()

    def __init__(self, name, arg):
        self.name = name
        self.arg = arg
        self.id = next(self.index_gen)
        self.done_task = asyncio.Future()

        self.replies = []
        self.events = []
        self.dtype = str
        self.status = None
        self.allstatus = []

        # Dict of variable name: (isok, value)
        # where isok is a bool and value is a string that is one of:
        #
        # * The variable's value, as a str, for a get command of a variable.
        #   The value may be NULL if unknown.
        # * The variable's property, as a str representation of an int,
        #   for a get command of a property. Note that the variable name
        #   will end with "!{property_name}", just like in the get command.
        # * "" for a set command, since only the isok flag is of interest.
        self.data = {}

    def format(self):
        return f"{self.id} {self.name} {self.arg}"

    def get_value(self, name, dtype, bad_value=None):
        """Get a variable's value.

        Parameters
        ----------
        name : `str`
            Name of variable to get. Case-blind.
        dtype : `type`
            Type of data expected.
        bad_value : `typing.Any`
            The value to return if the get command fails,
            or if the value is unknown.
        """
        isok, strvalue_or_none = self.data[name]
        if not isok:
            return bad_value
        if strvalue_or_none is None:
            return bad_value
        return dtype(strvalue_or_none)

    def get_float(self, name):
        return self.get_value(name=name, dtype=float, bad_value=math.nan)

    def get_int(self, name, bad_value=0):
        return self.get_value(name=name, dtype=int, bad_value=bad_value)

    def __str__(self):
        return self.format()

    def __repr__(self):
        return f"AstelcoCommand(id={self.id}, name={self.name}, arg={self.arg})"


def assert_command_not_none(cmdid, command):
    """Raise CommandError if the command is None"""
    if command is None:
        raise CommandError(f"Unrecognized command {cmdid}")


class AstelcoDIMM(BaseDIMM):
    r"""Client for an Astelco autonomous DIMM.

    Notes
    -----
    Known limitations:
    * The reply parser does not handle variable names with square brackets,
      such as AXIS[0-1] or AXIS[0] or AXIS[0,1]. Do not try to get or set
      variables defined this way.
    * The command writer and reply reader makes no attempt to encode special
      characters in strings. The only place we write strings (at present)
      is optionally writing username and password. Those will have to be
      handled specially if they contain special chars. The rules for sending
      string data are as follows:

      * Only ASCII is allowed.
      * Replace backslash, double quote and all chars not in range 32-255
        with their hex value as \xhh or octal value as \ooo.
      * Surround the string with double quotes.
    """

    def __init__(self, log, simulate=False):
        super().__init__(log=log, simulate=simulate)

        self.config = None

        self.connection_timeout = 10.0
        self.read_timeout = 10.0

        self.read_level = None
        self.write_level = None

        self.connect_task = make_done_future()
        self.reader = None
        self.writer = None

        # Lock to prevent commands from being sent
        # until authorization is complete.
        self.auth_lock = asyncio.Lock()
        # Dict of command ID: AstelcoCommand
        self.running_commands = {}
        self.cmd_max_size = 100
        self.controller_ready = False

        self.mock_dimm = None

        self.reply_loop_task = make_done_future()
        self.status_loop_task = make_done_future()
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

        # A remote to weather station data
        self.ws_remote = None

        # The DIMM WEATHER.RAIN is set based on two separate weather station
        # topics: tel_precipitation and tel_snowDepth.
        self.is_raining = False
        self.is_snowing = False

        # Dict of compiled regular expression: method to call
        # to handle replies from the DIMM. The reply is stripped of the
        # final \n and surrounding whitespace before matching.
        self.dispatcher_dict = {
            re.compile(regex_str): method
            for regex_str, method in (
                (
                    r"(?P<cmdid>\d+) +COMMAND +(?P<state>\S+)( +(?P<message>.*))?",
                    self.handle_command,
                ),
                (
                    r"^(?P<cmdid>\d+) +DATA +ERROR +(?P<name>\S+) +(?P<error>.+)$",
                    self.handle_data_error,
                ),
                (
                    r"^(?P<cmdid>\d+) +DATA +INLINE +(?P<name>\S+)=(?P<value>.+)$",
                    self.handle_data_inline,
                ),
                (
                    r"^(?P<cmdid>\d+) +DATA +OK +(?P<name>\S+)",
                    self.handle_data_ok,
                ),
                (
                    r"(?P<cmdid>\d+) +EVENT +(?P<event_type>\S+) +"
                    r"(?P<name>\S+):(?P<number>\d+)( +(?P<description>.+))?",
                    self.handle_event,
                ),
            )
        }

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
description: Schema for the Astelco DIMM.
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
            self.ws_remote.tel_weather.callback = self.weather_callback
            self.ws_remote.tel_windSpeed.callback = self.wind_speed_callback
            self.ws_remote.tel_windDirection.callback = self.wind_direction_callback
            self.ws_remote.tel_dewPoint.callback = self.dew_point_callback
            self.ws_remote.tel_precipitation.callback = self.precipitation_callback
            self.ws_remote.tel_snowDepth.callback = self.snow_depth_callback

        # Set SKY to values that allow automatic operation if WEATHER data
        # is acceptable (as set by the weather station callbacks).
        # SKY.TEMP must be <= 20 to run (with the default DIMM config).
        await self.run_command("SET", "SKY.TEMP=-25.0")
        await self.run_command("SET", f"SKY.status={SkyStatus.CLEAR}")
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def stop(self):
        """Stop DIMM. Overwrites method from base class."""

        # Tell the DIMM to close up (1=preciptating)
        await self.run_command("SET", f"SKY.status={SkyStatus.PRECIPTATING}")

        if self.ws_remote is None:
            self.status["status"] = DIMMStatus["ERROR"]
            raise RuntimeError("No WeatherStation remote available")

        self.ws_remote.tel_weather.callback = None
        self.ws_remote.tel_windSpeed.callback = None
        self.ws_remote.tel_windDirection.callback = None
        self.ws_remote.tel_dewPoint.callback = None
        self.ws_remote.tel_precipitation.callback = None
        self.ws_remote.tel_snowDepth.callback = None

        # TODO: Change to STOPPED?
        self.status["status"] = DIMMStatus["INITIALIZED"]
        await self.disconnect()

    async def status_loop(self):
        """Monitor DIMM status and update `self.status` dictionary
        information.
        """
        self.log.debug("Status loop begins")
        try:
            while self.connected:
                status_cmd = await self.run_command(
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
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("Status loop failed")
            self.status["status"] = DIMMStatus["ERROR"]
        finally:
            self.log.debug("Status loop ends")

    async def connect(self):
        """Connect to the DIMM controller's TCP/IP."""
        try:
            self.reply_loop_task.cancel()
            self.status_loop_task.cancel()

            if self.connected:
                self.log.error("Already connected")
                self.status["status"] = DIMMStatus["ERROR"]
                return

            if self.simulate:
                self.mock_dimm = MockAstelcoDIMM(
                    port=0,
                    log=self.log,
                    require_authentication=not self.config.auto_auth,
                )
                await self.mock_dimm.start_task
                host = LOCAL_HOST
                port = self.mock_dimm.port
            else:
                host = self.config.host
                port = self.config.port

            self.log.info(f"Connecting to Astelco DIMM at {host}:{port}")
            self.connect_task = asyncio.open_connection(host=host, port=port)

            self.reader, self.writer = await asyncio.wait_for(
                self.connect_task, timeout=self.connection_timeout
            )

            # Read welcome message
            reply = await asyncio.wait_for(self.read_reply(), timeout=self.read_timeout)
            if "TPL" not in reply:
                raise RuntimeError("No welcome message from controller")

            if not self.config.auto_auth:
                # Authenticate
                await self.write_cmdstr(
                    f'AUTH PLAIN "{self.config.user}" "{self.config.password}"'
                )

            # Get reply from auth. This is published even in auto_auth mode
            reply = await asyncio.wait_for(self.read_reply(), timeout=self.read_timeout)
            s = re.search(
                r"AUTH\s+(?P<AUTH>\S+)\s+(?P<read_level>\d+)\s+(?P<write_level>\d+)",
                reply,
            )
            if not s or s.group("AUTH") != "OK":
                await self.disconnect()
                raise RuntimeError("Not authorized")

            self.read_level = int(s.group("read_level"))
            self.write_level = int(s.group("write_level"))

            # Start loop to monitor replied.
            self.reply_loop_task = asyncio.create_task(self.reply_loop())

            if self.simulate:
                # In the long run this may need to be called at regular
                # intervals, but for now the mock controller
                # just needs it sent once.
                await self.report_good_mock_weather()

            # Start status loop
            self.status_loop_task = asyncio.create_task(self.status_loop())

        except Exception:
            self.log.exception("Error connecting to DIMM controller")
            self.status["status"] = DIMMStatus["ERROR"]
        else:
            self.status["status"] = DIMMStatus["INITIALIZED"]

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port."""

        self.log.debug("Disconnect")
        self.reply_loop_task.cancel()
        self.status_loop_task.cancel()
        if self.connected:
            await self.write_cmdstr("DISCONNECT")
        writer = self.writer
        self.reader = None
        self.writer = None
        if writer is not None:
            await close_stream_writer(writer)

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
            timestamp_cmd = await self.run_command("GET", "DIMM.TIMESTAMP")
            timestamp = timestamp_cmd.get_float("DIMM.TIMESTAMP")
            if timestamp == prev_timestamp:
                return None
            prev_timestamp = timestamp

            seeing_cmd = await self.run_command(
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

    async def run_command(self, name, arg, wait_done=True):
        """Send a command to the TCP/IP controller and process its replies.

        Parameters
        ----------
        name : `str`
            Command name, e.g. "SET" or "GET"
        arg : `str`
            Command argument.
        wait_done : `bool`, optional
            If True (the default), wait until the command is done.
        """
        if not self.connected:
            raise RuntimeError("Not connected")
        command = AstelcoCommand(name=name, arg=arg)
        self.running_commands[command.id] = command
        await self.write_cmdstr(command.format())
        if wait_done:
            await command.done_task
        return command

    async def write_cmdstr(self, cmdstr):
        """Write a command string to the T2SA, after adding a terminator.

        Parameters
        ----------
        cmdstr : `str`
            The message to write, as a string with no terminator.
        """
        if not self.connected:
            raise RuntimeError("Not connected")
        cmdbytes = cmdstr.encode() + TERMINATOR
        self.log.debug(f"Write to T2SA: {cmdbytes}")
        self.writer.write(cmdbytes)
        await self.writer.drain()

    async def read_reply(self):
        """Read a reply from the T2SA.

        Return the reply after decoding and stripping surrounding whitespace
        and terminators.
        """
        if not self.connected:
            raise RuntimeError("Not connected")

        reply_bytes = await self.reader.readuntil(TERMINATOR)
        self.log.debug(f"Read from T2SA: {reply_bytes}")
        return reply_bytes.decode().strip()

    async def reply_loop(self):
        """Handle reply from controller."""
        self.log.debug("Reply loop begins")
        try:
            while self.connected:
                reply = await self.read_reply()
                for regex, handler in self.dispatcher_dict.items():
                    match = regex.match(reply)
                    if match is not None:
                        kwargs = match.groupdict(default="")
                        cmdid = int(kwargs.pop("cmdid"))
                        command = self.running_commands.get(cmdid)
                        if command is not None:
                            command.replies.append(reply)
                            try:
                                handler(command=command, cmdid=cmdid, **kwargs)
                            except Exception:
                                self.log.exception(
                                    f"Reply handler {handler} failed on {reply!r}"
                                )
                            if command.done_task.done():
                                self.running_commands.pop(cmdid)
                            break
                else:
                    self.log.warning(f"Ignoring unrecognized reply {reply!r}")

        except asyncio.CancelledError:
            pass
        except (asyncio.IncompleteReadError, ConnectionResetError):
            self.log.warning("Connection lost; reply loop ending")
        except Exception:
            self.log.exception("Reply loop failed")
        finally:
            self.log.debug(f"Terminate {len(self.running_commands)} pending commands")
            # Cancel all running commands.
            while self.running_commands:
                command = self.running_commands.popitem()[1]
                if not command.done_task.done():
                    command.done_task.set_exception(asyncio.CancelledError())
            self.log.debug("Reply loop done")

    def handle_command(self, command, cmdid, state, message=""):
        """Handle a COMMAND {state} reply.

        If state is COMPLETE: set command.done_task result to None.
        If state is FAILED: set command.done_task exception to CommandError.
        Otherwise ignore the message because the other states are not
        terminal, and all replies are accumulated in command.replies,
        in case you want the information.

        Parameters
        ----------
        command : `AstelcoCommand`
            The command. Must not be None.
        cmdid : `int`
            The command ID.
        state : `str`
            The command state, e.g. FAILED or COMPLETE.
        message : `str`, optional
            Additional information. Used to set
        """
        assert_command_not_none(cmdid=cmdid, command=command)
        match state:
            case "FAILED":
                if not command.done_task.done():
                    command.done_task.set_exception(CommandError(message))
                else:
                    self.log.warning(
                        f"Cannot set {command} to error; it already finished"
                    )
            case "COMPLETE":
                if not command.done_task.done():
                    command.done_task.set_result(None)
                else:
                    self.log.warning(
                        f"Cannot set {command} to error; it already finished"
                    )

    def handle_data_error(self, command, cmdid, name, error):
        """Handle a DATA ERROR reply.

        DATA ERROR indicates that a SET or GET command failed for this
        variable, so set command.data[name] = (False, error)

        Parameters
        ----------
        command : `AstelcoCommand`
            The command. Must not be None.
        cmdid : `int`
            The command ID.
        name : `str`
            The specified variable name.
        error : `str`
            Error information.
        """
        assert_command_not_none(cmdid=cmdid, command=command)
        command.data[name] = (False, error)

    def handle_data_inline(self, command, cmdid, name, value):
        """Handle a DATA INLINE reply.

        DATA INLINE handles the successful result of a GET command
        for one variable, by setting command.data[name] as follows:

        * ``(False, reply)`` if the data could not be retrieved,
          where ``reply`` indicates what went wrong.
          The OpenTPL manual section ``4.2. GET — Retrieving data``
          has a table showing possible error replies.
        * ``(True, None)`` if the value is unknown (reported as NULL).
        * ``(True, strvalue)`` if the value is known.

          Notes:

          * ``strvalue`` will have surrounding double quotes stripped,
            if present (as they will be for a string-valued variable).
          * ``strvalue`` is always a string, because this callback doesn't know
            the type of each variable. Use AstelcoCommand.get_float or
            get_int to retrieve a value cast to a float or int.

        Parameters
        ----------
        command : `AstelcoCommand`
            The command. Must not be None.
        cmdid : `int`
            The command ID.
        name : `str`
            The specified variable name.
        value : `str`
            The value, as a string.
        """
        assert_command_not_none(cmdid=cmdid, command=command)
        first_word = value.split()[0]
        if first_word in BadDataReplies:
            self.log.warning(
                f"GET {name} failed: {value!r}; treating the value as unknown"
            )
            command.data[name] = (False, value)
        else:
            if first_word == "NULL":
                value = None
            elif value[0] == '"':
                # Trim double quotes from a string value
                value = value[1:-1]
            command.data[name] = (True, value)

    def handle_data_ok(self, command, cmdid, name):
        """Handle an DATA OK reply.

        DATA OK indicates that a SET command succeeded for this variable,
        so set command.data[name] = (True, "").

        Parameters
        ----------
        command : `AstelcoCommand`
            The command. Must not be None.
        cmdid : `int`
            The command ID.
        name : `str`
            The specified variable name.
        """
        assert_command_not_none(cmdid=cmdid, command=command)
        command.data[name] = (True, "")

    def handle_event(self, command, cmdid, event_type, name, number, description=""):
        """Handle an EVENT reply.

        We aren't relying on events, so just log it for now.

        Parameters
        ----------
        command : `AstelcoCommand` or None
            The command, if cmdid is not 0 and the command is running.
        cmdid : `int`
            The command ID; 0 for unsolicited replies.
        event_type : `str`
            The event type.
        name : `str`
            The specified variable name.
        number : `str`
            The specified number, as a string.
        description : `str`, optional
            More information.
        """
        self.log.info(f"Read event {event_type} {name}={number} {description}")

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

        await self.run_command(
            "SET",
            f"WEATHER.TEMP_AMB={data.ambient_temp};"
            f"WEATHER.RH={data.humidity};"
            f"WEATHER.PRESSURE={data.pressure}",
        )

    async def wind_speed_callback(self, data):
        """Sends information about wind speed (m/s) to the DIMM.

        Uses 2 minutes average information from weather station if average
        contains a valid values (>0), otherwise sends instantaneous if valid
        and don't update if none are valid. Note that this may cause the
        DIMM to shut-off if to many cycles are lost.
        """
        if data.avg2M > 0.0:
            await self.run_command("SET", f"WEATHER.WIND={data.avg2M}")
        elif data.value >= 0.0:
            await self.run_command("SET", f"WEATHER.WIND={data.value}")

    async def wind_direction_callback(self, data):
        """Send wind direction to the DIMM.

        The weather station and DIMM use the same convention
        for wind direction: clockwise from due north.

        Use 2 minutes average from weather station if valid,
        else instantaneous, if valid, else don't send anything.
        """
        if data.avg2M > 0.0:
            await self.run_command("SET", f"WEATHER.WIND_DIR={data.avg2M}")
        elif data.value >= 0.0:
            await self.run_command("SET", f"WEATHER.WIND_DIR={data.value}")

    async def dew_point_callback(self, data):
        """Send dew point (C) to the DIMM.

        Use 1 minute average from weather station, if valid,
        else don't send anything.
        """
        if data.avg1M > -99.0:
            await self.run_command("SET", f"WEATHER.TEMP_DEW={data.avg1M}")

    async def precipitation_callback(self, data):
        """Set self.is_raining and update DIMM WEATHER.RAIN"""
        if data.prSum1M > -99.0:
            self.is_raining = data.prSum1M > 0.0
        await self.set_weather_rain()

    async def snow_depth_callback(self, data):
        """Set self.is_snowing and update DIMM WEATHER.RAIN"""
        if data.avg1M > -99.0:
            self.is_snowing = data.avg1M > 0.0
        await self.set_weather_rain()

    async def set_weather_rain(self):
        """Set DIMM WEATHER.RAIN based on self.is_raining
        and self.is_snowing.
        """
        is_precipitating = self.is_raining or self.is_snowing
        rain_value = RainState.PRECIPITATION if is_precipitating else RainState.DRY
        await self.run_command("SET", f"WEATHER.RAIN={rain_value}")

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

        await self.weather_callback(
            self.ws_remote.tel_weather.DataType(
                ambient_temp=0,
                humidity=self.mock_dimm.config.HumLow * 0.9,
                pressure=0.5,
            )
        )
        await self.wind_speed_callback(
            self.ws_remote.tel_windSpeed.DataType(
                avg2M=self.mock_dimm.config.WindLow * 0.9,
                value=self.mock_dimm.config.WindLow * 0.9,
            )
        )
        await self.precipitation_callback(
            self.ws_remote.tel_precipitation.DataType(prSum1M=0)
        )
