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

import pathlib
import unittest

import pytest
from lsst.ts import dimm, salobj, utils

TEST_CONFIG_DIR = pathlib.Path(__file__).parents[1].joinpath("tests", "data", "config")
SHORT_TIMEOUT = 5
MEAS_TIMEOUT = 10


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
            data = await self.remote.evt_dimmMeasurement.next(
                flush=True, timeout=MEAS_TIMEOUT
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
