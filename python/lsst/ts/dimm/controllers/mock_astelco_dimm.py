# This file is part of ts_dimm.
#
# Developed for the Vera C. Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
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

__all__ = ["MockAstelcoDIMM"]

import asyncio
import dataclasses
import enum
import math
import random
import re
import time
import types

from lsst.ts import tcpip, utils

from .astelco_enums import (
    TERMINATOR,
    AmebaMode,
    AmebaState,
    PowerState,
    RainState,
    ScopeMotionState,
    ServiceControl,
    ServiceFailState,
    ServiceState,
    SkyStatus,
    VariableType,
)

random.seed(47)

# Parked position of telescope.
ScopeParkedPosition = types.SimpleNamespace(
    ra=0.0,  # hours
    dec=0.0,  # degrees
    az=0.0,  # degrees
    alt=0.0,  # degrees
)


# Range of random values DIMM measurements.
ScopeDataRange = types.SimpleNamespace(
    ra=(0, 24),
    dec=(-90, 45),
    az=(-180, 180),
    alt=(5, 85),  # must be <90, due to primitive airmass calculation
)


# Range of random values DIMM measurements.
# The values for seeing are mean and std dev for a Gaussian distribution
# the others are min and max for a uniform distribution
DIMMDataRange = types.SimpleNamespace(
    seeing=(0.5, 2.5),  # mean, std dev
    flux_left=(10000, 20000),
    flux_right=(10000, 20000),
    flux_rms_left=(10000, 20000),
    flux_rms_right=(10000, 20000),
    strehl_left=(0.1, 0.9),
    strehl_right=(0.1, 0.9),
)


class CommandError(Exception):
    pass


class BaseModule:
    """Base class for modules and submodules.

    Set class attribute ``_settable_fields`` to all field names
    that the DIMM allows the user to set. Notes:

    * ``_settable_fields`` only applies to the `set_field` method.
    * All users are assumed to be authorized to set all settable fields.
    * The default is that no fields are settable, since so few are.
    """

    _settable_fields = {}

    async def set_field(self, fieldname, value_str):
        """Set a field via the SET command.

        Override as needed to simulate operation.

        Parameters
        ----------
        fieldname : `str`
            Variable name.
        value_str : `str`
            New value, as a string.
            If the field type is ``str`` then ``value_str``
            must begin and end with ``"``.
            Otherwise the value must be castable to the correct type.

        Raises
        ------
        RuntimeError
            If the field is not settable (``fieldname`` is not in
            ``_settable_fields``).
        ValueError
            If:

            * The field is of type `str` and ``value_str``
              does not start and end with a double quote (``"``).
            * The field is not of type `str` and ``value_str``
              cannot be cast to the field type.
        """
        if fieldname not in self._settable_fields:
            if getattr(self, fieldname, None) is None:
                raise RuntimeError(f"field {fieldname!r} does not exist")
            else:
                raise RuntimeError(f"field {fieldname!r} is read-only")
        field_type = type(getattr(self, fieldname))
        if field_type is str:
            if value_str[0] != '"' or value_str[-1] != '"':
                raise ValueError("All strings must be enclosed in double quotes")
            setattr(self, fieldname, value_str[1:-1])
        else:
            if issubclass(field_type, enum.IntEnum):
                new_value = field_type(int(value_str))
            else:
                new_value = field_type(value_str)
            setattr(self, fieldname, new_value)


@dataclasses.dataclass
class BaseAmebaTargetSubmodule(BaseModule):
    name = ""
    ra = 0.0
    dec = 0.0
    brightness = 0.0
    color = 0.0


class AmebaCurrentSubmodule(BaseAmebaTargetSubmodule):
    stellar_classfile: str = "aclassfile"
    start_time = 0.0


class AmebaManualSubmodule(BaseAmebaTargetSubmodule):
    stellar_class = "G5III"
    stellar_classfile = "aclassfile"
    _settable_fields = {"name", "ra", "dec", "brightness", "color", "stellar_class"}


@dataclasses.dataclass
class ScopeStatusSubmodule(BaseModule):
    list: str = ""

    async def set_field(self, fieldname, value_str):
        """```clear``` is a write-only field that clears ``list``.

        The value is ignored. The real system will only clear errors
        that can be cleared.
        """
        if fieldname != "clear":
            raise RuntimeError(f"field {fieldname!r} does not exist or is read-only")
        self.list = ""


@dataclasses.dataclass
class ServiceSubmodule(BaseModule):
    state = ServiceState.NOT_RUNNING
    fail_state = ServiceFailState.NO_FAILURE
    control = ServiceControl.STOP
    _settable_fields = {"control"}


@dataclasses.dataclass
class BaseToplevelModule(BaseModule):
    version: int = 0x00010100
    service = ServiceSubmodule()


class DIMMModule(BaseToplevelModule):
    seeing = 0.0
    seeing_lowfreq = 0.0
    flux_left = 0.0
    flux_right = 0.0
    flux_rms_left = 0.0
    flux_rms_right = 0.0
    airmass = 0.0
    strehl_left = 0.0
    strehl_right = 0.0
    timestamp = 0.0


class DomeModule(BaseToplevelModule):
    position = 0.0
    position_sidea = 0.0
    position_sideb = 0.0
    temperature = 0.0
    power_state = PowerState.PARKED


class MeteoModule(BaseToplevelModule):
    pass


class ScopeModule(BaseToplevelModule):
    status = ScopeStatusSubmodule()
    ra = ScopeParkedPosition.ra
    dec = ScopeParkedPosition.dec
    az = ScopeParkedPosition.az
    alt = ScopeParkedPosition.alt
    focus = 0
    motion_state = ScopeMotionState.PARKED
    power_state = PowerState.PARKED


class AmebaModule(BaseToplevelModule):
    manual = AmebaManualSubmodule()
    current = AmebaCurrentSubmodule()
    mode = AmebaMode.AUTO
    state = AmebaState.INACTIVE
    sun_alt_condition = 0.0
    start_time = 0.0
    finish_time = 0.0
    _settable_fields = {"mode"}


@dataclasses.dataclass
class Config:
    """Configuration to specify operating conditions.

    Initialized to the default values listed in manual
    dev-dimm-tt-meto_spec-en_V1-2.pdf section 6.1 Startup conditions

    Attributes
    ----------
    HumLow : `float`
        Maximum allowed relative humidity (%)
    WindLow : `float`
        Maximum allowed wind speed (m/s)
    TempStart : `float`
        Maximum allowed sky temperature (C)
    """

    HumLow = 97.0
    WindLow = 9.0
    TempStart = -20.0


class WeatherModule(BaseModule):
    """Weather data.

    The following fields are initialized to values that prohibit
    automatic operation (the others are not relevant).
    This is allegedly what the DIMM does, though we may be
    bypassing that:

    * wind
    * rh
    * rain
    """

    temp_amb = 0.0  # ambient temperature (C)
    wind = Config.WindLow * 1.1  # wind speed (m/s)
    wind_dir = 0.0  # wind direction; unknown frame; not used
    rh = 100.0  # relative humidity (%)
    temp_dew = 0.0  # dew point temperature (C); not used
    pressure = 0.0  # air pressure (mBar)
    rain = RainState.PRECIPITATION
    _settable_fields = {
        "temp_amb",
        "wind",
        "wind_dir",
        "rh",
        "temp_dew",
        "pressure",
        "rain",
    }


class SkyModule(BaseModule):
    """Sky data.

    The following fields are initialized to values that prohibit
    automatic operation (the others are not relevant).
    This is allegedly what the DIMM does, though we may be
    bypassing that:

    * status
    * temp
    """

    status = SkyStatus.PRECIPTATING
    temp = Config.TempStart + 1
    _settable_fields = {
        "status",
        "temp",
    }


def format_message(msg):
    """Format a message that is appended to a reply.

    Return "" if msg is blank. Otherwise prepend a space
    and surround msg with double quotes.
    """
    if not msg:
        return ""
    return f' "{msg}"'


class MockAstelcoDIMM(tcpip.OneClientServer):
    """Mock Astelco DIMM.

    Parameters
    ----------
    port : `int`
        Port to listen to. Use 0 to pick a free port (recommended
        for unit tests).
    log : `logging.Logger`
        Logger.
    require_authentication : `bool`
        If true then the client must authenticate before issuing commands.

    Notes
    -----
    Limitations include:

    * Only supports AUTH PLAIN and does not check username or password.
    * Does not support encryption (ENC).
    * Does not handle the ABORT command.
    * Does not handle GET or SET of variable names with [],
      e.g. AXIS[0,1] or AXIS[0-1].
    * Does not try to decode escape sequences in string values
      for the SET command.
    * Measurements are only simulated in automatic mode, not manual mode.
    * In automatic mode:

        * The measurement and telescope target and values are are random.
          Each value should be individually plausible, but there is no
          attempt at reasonable correlation between related values.
          For example scope ra, dec are not properly related to az, el,
          and there is no correlaction between dimm x and y measurements,
          nor dimm left and right measurements.
        * Telescope slew duration is the same between every measurement.
        * Telescope az and el change instantly to the new target at the start
          of each slew, rather than gradually as the telescope slews.
        * Dome and telescope unparking and parking are instantaneous.
        * There is no timeout for weather data. Once acceptable weather data
          is set automatic operation begins, and does not end
          until unacceptable weather data is set.
    """

    def __init__(self, port, log, require_authentication):
        self.require_authentication = require_authentication

        self.authenticated = not require_authentication

        self.command_loop_task = utils.make_done_future()

        self.config = Config()
        self.ameba = AmebaModule()
        self.dimm = DIMMModule()
        self.dome = DomeModule()
        self.meteo = MeteoModule()
        self.scope = ScopeModule()
        self.weather = WeatherModule()
        self.sky = SkyModule()
        self.auto_loop_task = utils.make_done_future()
        # Slew time between targets (seconds).
        self.slew_duration = 1
        # Measurement interval (seconds).
        self.measurement_duration = 1
        # Event that is set every time a new measurement is made in auto mode.
        # Intended for unit tests, which may clear this event and then
        # wait for it.
        self.auto_measurement_event = asyncio.Event()

        regex_methods = (
            (r"auth (?P<method>[^ ]+) +(?P<args>.+)$", self.do_auth),
            ("disconnect", self.do_disconnect),
            (r"(?P<cmdid>\d+) +get (?P<arg>.+)", self.do_get),
            (r"(?P<cmdid>\d+) +set (?P<arg>.+)", self.do_set),
        )
        # list of complied regex: do_x method to call
        self.dispatchers = tuple(
            (re.compile(regex, re.IGNORECASE), method)
            for regex, method in regex_methods
        )

        # List of modules that SET and GET may access
        self.module_names = {"ameba", "dimm", "scope", "meteo", "weather", "sky"}

        super().__init__(
            name="MockAstelcoDIMM",
            host=tcpip.LOCAL_HOST,
            port=port,
            log=log,
            connect_callback=self.connect_callback,
        )

    async def auto_loop(self):
        """Simulate taking measurements in auto mode.

        This is primitive. Limitations:

        * The scope RA and Dec are not connected to az and alt.
        * There is a pause for slewing, but the new target ra, dec, az,
          and alt are set at the start of each slew and not updated
          at any other time (except when parking).
        * Dome position_sidea, position_side_b and temperature are always 0.
        * Parking and unparking the telescope is instantaneous.
        * Opening and closing the dome is instantaneous.
        * The AMEBA.STATE values are probably not very realistic.
        """
        try:
            self.log.info("Automatic loop begins")
            self.dome.power_state = PowerState.POWERED_UP
            self.scope.power_state = PowerState.POWERED_UP
            self.dome.position = self.scope.az
            while True:
                # Set scope target and pause pretending to slew
                self.ameba.state = AmebaState.SLEWING
                self.scope.motion_state = ScopeMotionState.SLEWING
                self.scope.ra = random.uniform(*ScopeDataRange.ra)
                self.scope.dec = random.uniform(*ScopeDataRange.dec)
                self.scope.az = random.uniform(*ScopeDataRange.az)
                self.scope.alt = random.uniform(*ScopeDataRange.alt)

                await asyncio.sleep(self.slew_duration)
                self.ameba.state = AmebaState.MONITORING
                self.scope.motion_state = ScopeMotionState.TRACKING

                # Pause for measurement and set fake measurement data.
                await asyncio.sleep(self.measurement_duration)
                self.dimm.seeing = random.normalvariate(*DIMMDataRange.seeing)
                self.dimm.seeing_lowfreq = self.dimm.seeing * 0.9  # arbitrary
                self.dimm.flux_left = random.uniform(*DIMMDataRange.flux_left)
                self.dimm.flux_right = random.uniform(*DIMMDataRange.flux_right)
                self.dimm.flux_rms_left = random.uniform(*DIMMDataRange.flux_rms_left)
                self.dimm.flux_rms_right = random.uniform(*DIMMDataRange.flux_rms_right)
                self.dimm.airmass = 1 / math.cos(math.radians(self.scope.alt))
                self.dimm.strehl_left = random.uniform(*DIMMDataRange.strehl_left)
                self.dimm.strehl_right = random.uniform(*DIMMDataRange.strehl_right)
                self.dimm.timestamp = time.time()
                self.auto_measurement_event.set()
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("Automatic loop failed")
        finally:
            self.log.info("Automatic loop ends")
            self.dome.position = 0
            self.scope.motion_state = ScopeMotionState.PARKED
            self.scope.ra = ScopeParkedPosition.ra
            self.scope.dec = ScopeParkedPosition.dec
            self.scope.az = ScopeParkedPosition.az
            self.scope.alt = ScopeParkedPosition.alt
            self.dome.power_state = PowerState.PARKED
            self.scope.power_state = PowerState.PARKED

    def can_open(self):
        """Do the weather conditions permit starting operation?

        The conditions are from manual dev-dimm-tt-meto_spec-en_V1-2.pdf
        section 6.1 Startup conditions
        """
        return (
            self.weather.rh <= self.config.HumLow
            and self.weather.wind <= self.config.WindLow
            and self.weather.rain == RainState.DRY
            and self.sky.status == SkyStatus.CLEAR
            and self.sky.temp <= self.config.TempStart
        )

    def connect_callback(self, server):
        if self.connected and self.command_loop_task.done():
            self.command_loop_task = asyncio.create_task(self.command_loop())

    async def command_loop(self):
        if self.require_authentication:
            welcome_msg = "TPL2 2.0 CONN 1 AUTH PLAIN ENC"
        else:
            welcome_msg = "TPL2 2.0 CONN 1 AUTH ENC"
        await self.write_msg(welcome_msg)

        cmdid_regex = re.compile(r"(\d+) *(.*)")
        try:
            while self.connected:
                cmd_bytes = await self.reader.readuntil(TERMINATOR)
                self.log.debug(f"Read {cmd_bytes!r}")
                cmd_str = cmd_bytes.decode().strip()

                cmdid_match = cmdid_regex.match(cmd_str)
                if cmdid_match is None:
                    cmdid = 0
                    cmdbody = cmd_str
                else:
                    cmdid = cmdid_match.group(1)
                    cmdbody = cmdid_match.group(2)

                for regex, func in self.dispatchers:
                    match = regex.match(cmd_str)
                    if match is not None:
                        break
                else:
                    # Unrecognized command
                    await self.write_command_state(cmdid, "ERROR UNKNOWN", cmdbody)
                    await self.write_command_state(cmdid, "FAILED", "Unknown command")
                    continue

                try:
                    await func(**match.groupdict())
                except CommandError as e:
                    await self.write_command_state(cmdid, "FAILED", str(e))
                except Exception as e:
                    self.log.exception(
                        f"Command handler {func} failed for command {cmd_str}"
                    )
                    await self.write_command_state(cmdid, "FAILED", str(e))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            self.log.warning("Connection lost")
        except Exception:
            self.log.exception("Error in command_loop; disconnect")
            asyncio.create_task(self.close_client())
            raise

    async def do_auth(self, method, args):
        """Handle the AUTH command.

        Parameters
        ----------
        method : `str`
            Authentication method; must be "plain" (any case)
        args : `str`
            Username and password; ignored.
        """
        if method.lower() != "plain":
            await self.write_msg("AUTH UNSUPPORTED")
        else:
            self.authenticated = True
            await self.write_msg("AUTH OK 20 20")

    async def do_disconnect(self):
        """Handle the DISCONNECT command."""
        await self.write_msg("DISCONNECT OK")
        await self.close_client()

    async def do_get(self, cmdid, arg):
        """Handle the GET command.

        Parameters
        ----------
        cmdid : `str`
            Command ID; string representation of an int.
        arg : `str`
            One or more variables or variable properties to get,
            separated by ";". Variable properties are of the form
            {variablename}!{propertyname}.
        """
        if not self.authenticated:
            await self.write_not_authenticated(cmdid)
            return

        await self.write_command_state(cmdid, "OK")
        for varname_property in re.split(r"; *", arg):
            try:
                varname, _, property = varname_property.partition("!")
                value = self.get_field(varname)
                match property.lower():
                    case "":
                        await self.write_data_inline(cmdid, varname, value)
                    case "type":
                        if isinstance(value, enum.IntEnum):
                            vartype = VariableType.INT
                        else:
                            vartype = {
                                str: VariableType.STRING,
                                int: VariableType.INT,
                                float: VariableType.FLOAT,
                            }.get(type(value), VariableType.NULL)
                        await self.write_data_inline(
                            cmdid, varname_property, vartype.value
                        )
                    case _:
                        await self.write_data_error(
                            cmdid=cmdid,
                            varname=varname,
                            error_code=15,
                            message=f"unsupported property {property}",
                        )
            except Exception as e:
                self.log.warning(f"{cmdid} GET {varname} failed: {e!r}")
                await self.write_data_error(
                    cmdid=cmdid, varname=varname, error_code=15, message=str(e)
                )
        await self.write_command_state(cmdid, "COMPLETE")

    async def do_set(self, cmdid, arg):
        """Handle the SET command.

        Parameters
        ----------
        cmdid : `str`
            Command ID; string representation of an int.
        arg : `str`
            One or more variables to set, each in the form
            "{variable_name}={value}" and separated by ";".
        """
        if not self.authenticated:
            await self.write_not_authenticated(cmdid)
            return

        await self.write_command_state(cmdid, "OK")
        for varname_value in re.split(r";", arg):
            varname, value_str = varname_value.split("=")
            value_str = value_str.strip()
            try:
                await self.set_field(varname, value_str)
                await self.write_data_ok(cmdid, varname)
            except Exception as e:
                await self.write_data_error(
                    cmdid, varname=varname, error_code=15, message=str(e)
                )
        await self.write_command_state(cmdid, "COMPLETE")
        if self.ameba.mode == AmebaMode.AUTO and self.can_open():
            if self.auto_loop_task.done():
                self.auto_loop_task = asyncio.create_task(self.auto_loop())
        else:
            self.auto_loop_task.cancel()

    def get_field(self, varname):
        """Get the value of a field."""
        attr_names = varname.lower().split(".")
        return self._hierarchical_get_attr(attr_names)

    async def set_field(self, varname, value_str):
        """Set the value of a field."""
        attr_names = varname.lower().split(".")
        module_attr_names = attr_names[:-1]
        field_name = attr_names[-1]
        module = self._hierarchical_get_attr(module_attr_names)
        await module.set_field(field_name, value_str)

    async def write_command_state(self, cmdid, state, message=""):
        """Write a COMMAND state message.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        state : `str`
            Command state. Write one of these states before
            returning any data for the command:

            * OK
            * ERROR UNAUTHENTICATED
            * ERROR IDBUSY <err cmdid>
            * ERROR IDRANGE <err cmdid>
            * ERROR SYNTAX
            * ERROR TOOMANY
            * ERROR UNKNOWN

            Then write one of these states when the command is finished:

            * COMPLETE
            * ABORTEDBY <abort cmdid>
            * FAILED  # expected if was an ERROR status

        message : `str`
            Optional additional message.
        """
        await self.write_msg(f"{cmdid} COMMAND {state}{format_message(message)}")

    async def write_data_error(self, cmdid, varname, error_code, message=""):
        """Write a DATA ERROR message.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        varname : `str`
            Dotted name of variable.
        error_code : `int`
            Error code describing the failure.
        message : `str`
            Optional additional message.
        """
        await self.write_msg(
            f"{cmdid} DATA ERROR {varname.upper()} FAILED "
            f"{error_code}{format_message(message)}"
        )

    async def write_data_inline(self, cmdid, varname, value):
        """Write a DATA INLINE message.

        This is the standard reply (with the requested data)
        for each variable in a GET command.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        varname : `str`
            Dotted name of variable, in uppercase.
        value : `typing.Any`
            Value of variable.
        """
        await self.write_msg(f"{cmdid} DATA INLINE {varname.upper()}={value}")

    async def write_data_ok(self, cmdid, varname):
        """Write a DATA OK message.

        This is the standard reply for each variable in a SET message.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        varname : `str`
            Dotted name of variable, in uppercase.
        """
        await self.write_msg(f"{cmdid} DATA OK {varname}")

    async def write_event_info(self, cmdid, varname, encm, value):
        """Write an EVENT INFO message.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        varname : `str`
            Dotted name of variable, in uppercase.
        """
        await self.write_msg(f"{cmdid} EVENT INFO {varname}:{encm}: {value}")

    async def write_event_error(self, cmdid, varname, encm):
        """Write an EVENT ERROR message.

        Parameters
        ----------
        cmdid : `int`
            Client-assigned command ID.
        varname : `str`
            Dotted name of variable, in uppercase.
        """
        await self.write_msg(f"{cmdid} EVENT ERROR {varname}:{encm}")

    async def write_not_authenticated(self, cmdid):
        """Write the messages that indicate a command failed
        due to lack of authentication."""
        await self.write_command_state(cmdid, "ERROR UNAUTHENTICATED")
        await self.write_command_state(cmdid, "FAILED")

    async def write_msg(self, msg):
        """Write a message, after adding the standard terminator.

        Parameters
        ----------
        msg : `str`
            The message to write (with no terminator).
        """
        if not self.connected:
            raise RuntimeError("Not connected")
        msg_bytes = msg.encode() + TERMINATOR
        self.log.debug(f"Writing {msg_bytes!r}")
        self.writer.write(msg_bytes)
        await self.writer.drain()

    def _hierarchical_get_attr(self, attr_names):
        """Get a variable from a hierarchical list of attr names.

        The attr_names should be lowercase.

        Raises
        ------
        RuntimeError
            If attr_names[0] is not in self.module_names.
        """
        if attr_names[0] not in self.module_names:
            raise CommandError(f"{attr_names[0]} not a valid module name")
        attr = self
        for attr_name in attr_names:
            attr = getattr(attr, attr_name)
        if isinstance(attr, str):
            return f'"{attr}"'
        else:
            return attr
