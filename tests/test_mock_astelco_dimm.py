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

import asyncio
import enum
import logging
import time
import unittest

from lsst.ts import dimm, tcpip, utils
from lsst.ts.dimm.controllers.astelco_enums import (
    TERMINATOR,
    AmebaMode,
    RainState,
    SkyStatus,
    VariableType,
)

STD_TIMEOUT = 5.0


class Command:
    """Represent the command interaction with the astelco controller."""

    id_generator = utils.index_generator()

    def __init__(self, name, arg):
        self.name = name
        self.arg = arg

        self.id = next(self.id_generator)
        self.done_task = asyncio.Future()
        self.replies = []
        self.start_time = time.monotonic()
        self.done_time = None

    def format(self):
        return f"{self.id} {self.name} {self.arg}"

    def __repr__(self):
        return f"Command(id={self.id}; name={self.name}; arg={self.arg}"


class MockAstelcoDIMMTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.log = logging.getLogger()

    async def asyncSetUp(self) -> None:
        # Dict of command ID: command
        self.commands_dict = dict()
        self.mock_dimm = dimm.controllers.MockAstelcoDIMM(
            port=0, log=self.log, require_authentication=True
        )
        await asyncio.wait_for(self.mock_dimm.start_task, timeout=STD_TIMEOUT)
        print(f"Mock DIMM running on port {self.mock_dimm.port}")
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(host=tcpip.LOCAL_HOST, port=self.mock_dimm.port),
            timeout=STD_TIMEOUT,
        )
        self.auth_reply = asyncio.Future()
        self.disconnect_reply = asyncio.Future()
        self.read_loop_task = asyncio.create_task(self.read_loop())

    async def asyncTearDown(self) -> None:
        if self.connected:
            await self.write_msg("DISCONNECT")
            await asyncio.wait_for(self.disconnect_reply, timeout=STD_TIMEOUT)
        self.read_loop_task.cancel()
        if self.connected:
            await tcpip.close_stream_writer(self.writer)

    @property
    def connected(self):
        return not (
            self.reader is None
            or self.writer is None
            or self.reader.at_eof()
            or self.writer.is_closing()
        )

    async def read_loop(self):
        try:
            # Parse welcome message
            reply = await self.read_next()
            assert reply.startswith("TPL2")
            assert "AUTH PLAIN" in reply

            while self.connected:
                reply = await self.read_next()
                if reply.startswith("DISCONNECT"):
                    assert not self.disconnect_reply.done()
                    self.disconnect_reply.set_result(reply)
                elif reply.startswith("AUTH"):
                    assert not self.auth_reply.done()
                    self.auth_reply.set_result(reply)
                else:
                    cmdid_str, rest = reply.split(" ", 1)
                    cmdid = int(cmdid_str)
                    command = self.commands_dict[cmdid]
                    command.replies.append(reply)
                    if "COMMAND " in rest:
                        if "COMPLETE" in rest or "FAILED" in rest:
                            assert not command.done_task.done()
                            command.done_time = time.monotonic()
                            command.done_task.set_result(None)
        except Exception as e:
            print(f"read_loop failed: {e!r}")
            raise

    async def run_command(
        self, name, arg, wait_done=True, should_pass=True, timeout=STD_TIMEOUT
    ):
        """Run one command with the specified arguments.

        Parameters
        ----------
        name : `str`
            Command name (case ignored), e.g. "GET" or "SET".
        arg : `str`
            Command argument.
        wait_done : `bool`
            Wait for the command to finish?
        should_pass : `bool`
            Should the command succceed? Ignored if wait_done is false.
        timeout : `float`
            Time limit for writing command
            and then for receiving the reply (seconds).
        """
        assert self.connected
        command = Command(name=name, arg=arg)
        self.commands_dict[command.id] = command
        await asyncio.wait_for(self.write_msg(command.format()), timeout=timeout)
        if wait_done:
            await asyncio.wait_for(command.done_task, timeout=timeout)
            if should_pass:
                assert "COMMAND OK" in command.replies[0]
                assert "COMMAND COMPLETE" in command.replies[-1]
            else:
                assert "COMMAND ERROR" in command.replies[0]
                assert "COMMAND FAILED" in command.replies[-1]
        return command

    async def read_next(self):
        assert self.connected
        reply_bytes = await self.reader.readuntil(TERMINATOR)
        return reply_bytes.decode().strip()

    async def write_msg(self, msg):
        assert self.connected
        self.writer.write(msg.encode() + TERMINATOR)
        await self.writer.drain()

    async def authenticate(self):
        assert not self.mock_dimm.authenticated
        await self.write_msg("AUTH PLAIN aname apassword")
        result = await asyncio.wait_for(self.auth_reply, timeout=STD_TIMEOUT)
        assert result.startswith("AUTH OK")
        assert self.mock_dimm.authenticated

    async def test_automatic_measurement(self):
        await self.authenticate()
        assert self.mock_dimm.auto_loop_task.done()
        assert not self.mock_dimm.can_open()
        # Speed up measurement, to make the test run faster.
        self.mock_dimm.slew_duration = 0.1
        self.mock_dimm.measurement_duration = 0.1
        # Enable automatic operation
        config = self.mock_dimm.config
        for arg in (
            f"AMEBA.MODE={AmebaMode.AUTO}",
            f"WEATHER.RH={config.HumLow * 0.9:0.2f}",
            f"WEATHER.WIND={config.WindLow * 0.9:0.2f}",
            f"WEATHER.RAIN={RainState.DRY}",
            f"SKY.STATUS={SkyStatus.CLEAR}",
            f"SKY.TEMP={config.TempStart - 0.1:0.2f}",
        ):
            await self.run_command("SET", arg)
        assert self.mock_dimm.can_open()
        assert not self.mock_dimm.auto_loop_task.done()

        # Auto mode is now running; wait for two measurements
        # and check that they differ from each other.
        measurement_timeout = (
            STD_TIMEOUT
            + self.mock_dimm.slew_duration
            + self.mock_dimm.measurement_duration
        )
        # dimm fields whose value we expect to be constant
        constant_fields = {"version"}
        # List of measurements, each a dict of field: value
        meas1 = None
        n_measurements = 0
        while n_measurements < 5:
            self.mock_dimm.auto_measurement_event.clear()
            await asyncio.wait_for(
                self.mock_dimm.auto_measurement_event.wait(),
                timeout=measurement_timeout,
            )
            if (
                meas1 is not None
                and meas1["timestamp"] == self.mock_dimm.dimm.timestamp
            ):
                continue
            else:
                n_measurements += 1

            meas0 = meas1
            meas1 = vars(self.mock_dimm.dimm).copy()
            if meas0 is not None:
                for field in meas0:
                    if field in constant_fields:
                        continue
                    assert meas0[field] != meas1[field]

    async def test_get_command(self):
        # Test that authentication is required
        good_get_arg = "AMEBA.CURRENT.NAME"
        command = await self.run_command("GET", good_get_arg, should_pass=False)
        assert len(command.replies) == 2
        assert "COMMAND ERROR UNAUTHENTICATED" in command.replies[0]
        assert "COMMAND FAILED" in command.replies[1]

        await self.authenticate()

        for arg, expected_value_or_values in (
            ("AMEBA.CURRENT.NAME", ""),
            ("AMEBA.CURRENT.NAME!TYPE", VariableType.STRING),
            ("AMEBA.CURRENT.RA;AMEBA.CURRENT.DEC", (0.0, 0.0)),
            ("AMEBA.CURRENT.BRIGHTNESS", 0.0),
            ("AMEBA.CURRENT.COLOR;AMEBA.CURRENT.COLOR!TYPE", (0.0, VariableType.FLOAT)),
            ("AMEBA.CURRENT.STELLAR_CLASSFILE", "aclassfile"),
            ("AMEBA.CURRENT.START_TIME", 0.0),
            ("AMEBA.MANUAL.NAME", ""),
            ("AMEBA.MANUAL.RA;AMEBA.MANUAL.RA", (0.0, 0.0)),
            ("AMEBA.MANUAL.BRIGHTNESS", 0.0),
            ("AMEBA.MANUAL.COLOR", 0.0),
            ("AMEBA.MANUAL.STELLAR_CLASS", "G5III"),
            ("AMEBA.MANUAL.STELLAR_CLASSFILE", "aclassfile"),
            ("WEATHER.TEMP_AMB;WEATHER.WIND_DIR", (0.0, 0.0)),
            ("WEATHER.RH", 100.0),
            ("WEATHER.TEMP_DEW", 0.0),
            ("WEATHER.PRESSURE", 0.0),
            (
                "WEATHER.RAIN;WEATHER.RAIN!TYPE",
                (RainState.PRECIPITATION, VariableType.INT),
            ),
        ):
            command = await self.run_command("GET", arg)
            if ";" in arg:
                fieldnames = arg.split(";")
                assert len(fieldnames) == len(expected_value_or_values)
                expected_values = expected_value_or_values
            else:
                fieldnames = [arg]
                expected_values = [expected_value_or_values]
            for i, expected_value in enumerate(expected_values):
                data_reply = command.replies[i + 1]
                assert "DATA INLINE" in data_reply
                read_fieldname, read_value = data_reply.split(" ", 3)[-1].split("=")
                assert read_fieldname == fieldnames[i]
                if isinstance(expected_value, str):
                    assert expected_value == read_value[1:-1]
                elif isinstance(expected_value, enum.IntEnum):
                    assert str(expected_value.value) == read_value
                else:
                    assert str(expected_value) == read_value

        # Test a nonexistent command
        command = await self.run_command("GGGET", good_get_arg, should_pass=False)
        assert len(command.replies) == 2
        assert "COMMAND ERROR UNKNOWN" in command.replies[0]
        assert "COMMAND FAILED" in command.replies[1]

        # Test getting a nonexistent variable. The command should succeed,
        # but each data message should be DATA ERROR.
        bad_varname = "NO.SUCH.VARIABLE"
        command = await self.run_command("GET", bad_varname, should_pass=True)
        await asyncio.wait_for(command.done_task, timeout=STD_TIMEOUT)
        assert len(command.replies) == 3
        assert "DATA ERROR" in command.replies[1]

    async def test_set_command(self):
        await self.authenticate()

        for arg in (
            "AMEBA.MODE=2",
            'AMEBA.MANUAL.NAME="new name"',
            "AMEBA.MANUAL.RA=1.1;AMEBA.MANUAL.DEC=2.2",
            "AMEBA.MANUAL.BRIGHTNESS=3.3",
            "AMEBA.MANUAL.COLOR=4.4",
            'AMEBA.MANUAL.STELLAR_CLASS="new stellar clas"',
            "WEATHER.TEMP_AMB=5.5",
            "WEATHER.WIND=6.6;WEATHER.WIND_DIR=7.7",
            "WEATHER.RH=8.8;WEATHER.TEMP_DEW=9.9;WEATHER.PRESSURE=10.1",
            "WEATHER.RAIN=1",
        ):
            command = await self.run_command("SET", arg)
            fieldname_values = arg.split(";")
            assert len(command.replies) == len(fieldname_values) + 2
            for i, fieldname_value in enumerate(fieldname_values):
                data_reply = command.replies[i + 1]
                assert "DATA OK " in data_reply
                fieldname, value_str = fieldname_value.split("=")
                assert fieldname in data_reply

                # Get and check the new field value
                get_command = await self.run_command("GET", fieldname)
                assert len(get_command.replies) == 3
                data_reply = get_command.replies[1]
                assert "DATA INLINE" in data_reply
                read_fieldname, read_value = data_reply.split(" ", 3)[-1].split("=")
                assert read_fieldname == fieldname
                assert read_value == value_str

        # Test setting bad values; the command should succeed
        # but each data message should be DATA ERROR.
        for arg in (
            "NO.SUCH.FIELD=0",
            'AMEBA.CURRENT.NAME="not writable"',
            "AMEBA.MANUAL.NAME=missing double quotes",
            "AMEBA.MANUAL.RA=not_a_float;AMEBA.MANUAL.DEC=not_a_float",
            "AMEBA.MANUAL.BRIGHTNESS=not_a_float",
            "AMEBA.MANUAL.COLOR=not_a_float",
            "AMEBA.MANUAL.STELLAR_CLASS=missing double quotes",
            'AMEBA.MANUAL.STELLAR_CLASSFILE="not writable"',
            "WEATHER.TEMP_AMB=not_a_float",
            "WEATHER.WIND=not_a_float;WEATHER.WIND_DIR=not_a_float",
            "WEATHER.RH=not_a_float;WEATHER.TEMP_DEW=not_a_float;WEATHER.PRESSURE=not_a_float",
            "WEATHER.RAIN=not_an_int",
            "WEATHER.RAIN=2",  # only 0 and 1 are allowed
        ):
            command = await self.run_command("SET", arg)
            fieldname_values = arg.split(";")
            assert len(command.replies) == len(fieldname_values) + 2
            for i, fieldname_value in enumerate(fieldname_values):
                data_reply = command.replies[i + 1]
                assert "DATA ERROR " in data_reply
                fieldname, value_str = fieldname_value.split("=")
                assert fieldname in data_reply
