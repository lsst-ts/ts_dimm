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
__all__ = ["OpenTplConnection"]

import asyncio
import math
import re

from lsst.ts.tcpip import LOCAL_HOST, close_stream_writer
from lsst.ts.utils import index_generator, make_done_future

from .astelco_enums import TERMINATOR

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


def assert_command_not_none(cmdid, command):
    """Raise CommandError if the command is None"""
    if command is None:
        raise CommandError(f"Unrecognized command {cmdid}")


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


def connection_handler(coroutine):
    """A decorator that will handle issues with the tcp/ip connection."""

    async def connection_handler_wrapper(self, *args, **kwargs):
        try:
            if not self.connected:
                raise RuntimeError("Not connected")
            return await coroutine(self, *args, **kwargs)
        except Exception:
            if self.dimm_error_callback:
                self.dimm_error_callback()
            await self.disconnect()
            raise

    return connection_handler_wrapper


class OpenTplConnection:
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

    def __init__(self, log, simulate):
        self.dimm_error_callback = None

        self.config = None
        self.log = log
        self.simulate = simulate

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

        self.reply_loop_task = make_done_future()

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

        if self.simulate:
            self.config.host = LOCAL_HOST

    async def unset(self) -> None:
        await self.disconnect()
        await super().unset()

    async def start(self):
        """Start DIMM. Overwrites method from base class."""
        await self.connect()

    def stop(self):
        """Stop the TCP connection."""

        self.connect_task.cancel()

    async def connect(self, port=0):
        """Connect to the DIMM controller's TCP/IP."""
        self.reply_loop_task.cancel()

        try:
            await self.reply_loop_task
        except asyncio.CancelledError:
            pass  # expected result

        if self.connected:
            self.log.error("Already connected")
            return

        host = self.config.host
        if port == 0:
            port = self.config.port

        self.log.info(f"Connecting to Astelco DIMM at {host}:{port}")
        self.connect_task = asyncio.create_task(
            asyncio.open_connection(host=host, port=port)
        )

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

    async def disconnect(self):
        """Disconnect from the spectrograph controller's TCP/IP port."""

        self.log.debug("Disconnect TCP connection")
        self.reply_loop_task.cancel()

        try:
            await self.reply_loop_task
        except asyncio.CancelledError:
            pass  # expected result

        if self.connected:
            try:
                await self.write_cmdstr("DISCONNECT")
            except Exception:
                self.log.exception("Error trying to disconnect. Ignoring.")
        writer = self.writer
        self.reader = None
        self.writer = None
        if writer is not None:
            self.log.info("Closing stream writer.")
            await close_stream_writer(writer)

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

    @connection_handler
    async def write_cmdstr(self, cmdstr):
        """Write a command string to the T2SA, after adding a terminator.

        Parameters
        ----------
        cmdstr : `str`
            The message to write, as a string with no terminator.
        """
        cmdbytes = cmdstr.encode() + TERMINATOR
        self.log.debug(f"Write to T2SA: {cmdbytes}")
        self.writer.write(cmdbytes)
        await self.writer.drain()

    @connection_handler
    async def read_reply(self):
        """Read a reply from the T2SA.

        Return the reply after decoding and stripping surrounding whitespace
        and terminators.
        """
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
