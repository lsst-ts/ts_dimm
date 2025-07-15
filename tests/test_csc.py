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
import datetime
import pathlib
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from lsst.ts import dimm, salobj, utils
from lsst.ts.xml.enums.DIMM import AmebaMode

TEST_CONFIG_DIR = pathlib.Path(__file__).parents[1].joinpath("tests", "data", "config")
SHORT_TIMEOUT = 5
MEAS_TIMEOUT = 20


fixed_now = datetime.datetime(2025, 1, 1, 8, 59, 0, tzinfo=ZoneInfo("America/Santiago"))
real_sleep = asyncio.sleep
long_sleeps = []


class FixedDateTime(datetime.datetime):
    """A mock of datetime.now that always returns a pre-determined value."""

    @classmethod
    def now(cls, tz=None):
        return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)


async def capped_sleep(delay, *args, **kwargs):
    """A mock for asyncio.sleep.

    Any sleep requested for longer than 30 seconds will (1) be logged for
    later verification and (2) be shortened to 1 second.
    """

    global long_sleeps
    if delay > 30:
        long_sleeps.append(delay)
        await real_sleep(1, *args, **kwargs)
    else:
        await real_sleep(delay, *args, **kwargs)


class CscTestCase(salobj.BaseCscTestCase, unittest.IsolatedAsyncioTestCase):
    def basic_make_csc(self, initial_state, config_dir, simulation_mode, **kwargs):
        return dimm.DIMMCSC(
            index=1,
            initial_state=initial_state,
            config_dir=config_dir,
            simulation_mode=simulation_mode,
        )

    async def test_standard_state_transitions(self):
        async with self.make_csc(
            initial_state=salobj.State.STANDBY,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            await self.check_standard_state_transitions(
                enabled_commands=(
                    "gotoAltAz",
                    "gotoRaDec",
                    "changeDwellRate",
                    "changeMeasurementRate",
                    "setAmebaMode",
                ),
            )

    async def test_version(self):
        async with self.make_csc(
            initial_state=salobj.State.STANDBY,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            await self.assert_next_sample(
                self.remote.evt_softwareVersions,
                cscVersion=dimm.__version__,
                subsystemVersions="",
            )

    async def test_bin_script(self):
        await self.check_bin_script(name="DIMM", index=1, exe_name="run_dimm_csc")

    async def test_astelco_dimm_measurement(self):
        async with self.make_csc(
            initial_state=salobj.State.STANDBY,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            await salobj.set_summary_state(
                remote=self.remote, state=salobj.State.ENABLED
            )
            data = await self.assert_next_sample(
                self.remote.evt_dimmMeasurement, flush=True, timeout=MEAS_TIMEOUT
            )
            assert data.fwhm > 0.1
            assert data.fluxL > 1000
            assert data.fluxR > 1000
            if hasattr(data, "expiresAt"):
                assert data.expiresIn == self.csc.measurement_validity
                assert data.expiresAt == pytest.approx(
                    utils.utc_from_tai_unix(data.private_sndStamp) + data.expiresIn
                )
            # Make sure most commands have been purged from running_commands;
            # it may have a status command.
            assert len(self.csc.controller.running_commands) <= 1

            data2 = await self.assert_next_sample(
                self.remote.evt_dimmMeasurement, flush=True, timeout=MEAS_TIMEOUT
            )

            assert data2.fwhm != data.fwhm

    async def test_astelco_dimm_fault_on_disconnect(self):
        async with self.make_csc(
            initial_state=salobj.State.STANDBY,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            await salobj.set_summary_state(
                remote=self.remote, state=salobj.State.ENABLED
            )

            # wait for one measurement to arrive
            await self.remote.evt_dimmMeasurement.next(flush=True, timeout=MEAS_TIMEOUT)
            self.remote.evt_summaryState.flush()

            # close the mock controller
            await self.csc.controller.mock_dimm.close()

            await self.assert_next_summary_state(
                state=salobj.State.FAULT,
                flush=False,
                remote=self.remote,
            )
            assert not self.csc.controller.connected

    async def test_set_ameba_mode(self):
        async with self.make_csc(
            initial_state=salobj.State.ENABLED,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            await self.remote.cmd_setAmebaMode.set_start(mode=AmebaMode.Manual.value)
            await self.assert_next_sample(
                topic=self.remote.tel_ameba,
                mode=AmebaMode.Manual.value,
                flush=True,
            )

            self.remote.evt_summaryState.flush()
            await salobj.set_summary_state(
                remote=self.remote, state=salobj.State.STANDBY
            )
            await self.assert_next_summary_state(
                state=salobj.State.DISABLED,
                flush=False,
                remote=self.remote,
            )
            await self.assert_next_summary_state(
                state=salobj.State.STANDBY,
                flush=False,
                remote=self.remote,
            )
            assert not self.csc.controller

    async def test_ameba_off_today(self):
        """The CSC should be able to disable ameba mode at 9am today."""
        global long_sleeps
        global fixed_now

        fixed_now = datetime.datetime(
            2025, 1, 1, 8, 59, 0, tzinfo=ZoneInfo("America/Santiago")
        )
        long_sleeps.clear()

        with patch("datetime.datetime", FixedDateTime), patch(
            "asyncio.sleep", new=capped_sleep
        ):
            async with self.make_csc(
                initial_state=salobj.State.ENABLED,
                config_dir=TEST_CONFIG_DIR,
                simulation_mode=1,
            ):
                self.csc.controller.set_automation_mode = AsyncMock()

                await real_sleep(10)
                self.assertEqual(long_sleeps, [60])
                self.csc.controller.set_automation_mode.assert_awaited_with(
                    dimm.controllers.base_dimm.AutomationMode.OFF
                )

                self.remote.evt_summaryState.flush()
                await salobj.set_summary_state(
                    remote=self.remote,
                    state=salobj.State.STANDBY,
                )
                await self.assert_next_summary_state(
                    state=salobj.State.DISABLED,
                    flush=False,
                    remote=self.remote,
                )
                await self.assert_next_summary_state(
                    state=salobj.State.STANDBY,
                    flush=False,
                    remote=self.remote,
                )

    async def test_ameba_off_tomorrow(self):
        """The CSC should be able to disable ameba mode at 9am tomorrow."""
        global long_sleeps
        global fixed_now

        fixed_now = datetime.datetime(
            2025, 1, 1, 9, 1, 0, tzinfo=ZoneInfo("America/Santiago")
        )
        long_sleeps.clear()

        with patch("datetime.datetime", FixedDateTime), patch(
            "asyncio.sleep", new=capped_sleep
        ):
            async with self.make_csc(
                initial_state=salobj.State.ENABLED,
                config_dir=TEST_CONFIG_DIR,
                simulation_mode=1,
            ):
                self.csc.controller.set_automation_mode = AsyncMock()

                await real_sleep(10)
                self.assertEqual(long_sleeps, [86400 - 60])
                self.csc.controller.set_automation_mode.assert_awaited_with(
                    dimm.controllers.base_dimm.AutomationMode.OFF
                )

                self.remote.evt_summaryState.flush()
                await salobj.set_summary_state(
                    remote=self.remote,
                    state=salobj.State.STANDBY,
                )
                await self.assert_next_summary_state(
                    state=salobj.State.DISABLED,
                    flush=False,
                    remote=self.remote,
                )
                await self.assert_next_summary_state(
                    state=salobj.State.STANDBY,
                    flush=False,
                    remote=self.remote,
                )

    async def test_ameba_off_on_disable(self):
        """Automation mode should be turned off when the CSC disables."""
        global long_sleeps
        global fixed_now

        fixed_now = datetime.datetime(
            2025, 1, 1, 9, 1, 0, tzinfo=ZoneInfo("America/Santiago")
        )

        async with self.make_csc(
            initial_state=salobj.State.ENABLED,
            config_dir=TEST_CONFIG_DIR,
            simulation_mode=1,
        ):
            set_automation_mode = AsyncMock()
            self.csc.controller.set_automation_mode = set_automation_mode

            await asyncio.sleep(1)  # Let evt_summaryState propagate through...
            self.remote.evt_summaryState.flush()
            self.csc.controller.set_automation_mode.assert_not_awaited()

            await salobj.set_summary_state(
                remote=self.remote,
                state=salobj.State.STANDBY,
            )
            await self.assert_next_summary_state(
                state=salobj.State.DISABLED,
                flush=False,
                remote=self.remote,
            )
            await self.assert_next_summary_state(
                state=salobj.State.STANDBY,
                flush=False,
                remote=self.remote,
            )

            set_automation_mode.assert_awaited_with(
                dimm.controllers.base_dimm.AutomationMode.OFF
            )
