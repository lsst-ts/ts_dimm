# This file is part of ts_environment.
#
# Developed for the LSST Telescope and Site Systems.
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import unittest, asyncio, pathlib
import numpy as np

from lsst.ts import salobj
from lsst.ts.dimm import DIMM

# Aliases
state = salobj.State

np.random.seed(47)
index_gen = salobj.index_generator()

STD_TIMEOUT: int = 2  # standard command timeout (sec)
LONG_TIMEOUT: int = 20  # time limit for starting a SAL component (sec)

TEST_CONFIG_DIR = \
    pathlib.Path(__file__).resolve().parents[1].joinpath("tests", "data", "config")

class Harness:
    def __init__(self, initial_state=state.STANDBY,
                 config_dir=None, initial_simulation_mode=0):
        salobj.test_utils.set_random_lsst_dds_domain()
        # import pdb; pdb.set_trace()

        self.csc = DIMM(initial_state=initial_state, config_dir=config_dir,
                        initial_simulation_mode=initial_simulation_mode)
        self.remote = salobj.Remote(domain=self.csc.domain, name="DIMM")

    async def __aenter__(self):
        await asyncio.gather(self.csc.start_task,
                             self.remote.start_task)
        return self

    async def __aexit__(self, *args):
        await self.csc.close()

class TestDIMM(unittest.TestCase):
    def test_standard_state_transitions(self):
        """Test standard CSC state transitions.

        The initial state is STANDBY.
        The standard commands and associated state transitions are:

        * enterControl: OFFLINE to STANDBY
        * start: STANDBY to DISABLED
        * enable: DISABLED to ENABLED

        * disable: ENABLED to DISABLED
        * standby: DISABLED to STANDBY
        * exitControl: STANDBY, FAULT to OFFLINE (quit)
        """
        async def doit():
            async with Harness(config_dir=TEST_CONFIG_DIR) as harness:
                csc = harness.csc
                ss_remote_evt = harness.remote.evt_summaryState

                self.assertEqual(csc.summary_state, state.STANDBY)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=LONG_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.STANDBY)

                # send start; new state is DISABLED
                await harness.remote.cmd_start.start(timeout=STD_TIMEOUT)
                self.assertEqual(csc.summary_state, state.DISABLED)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=STD_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.DISABLED)

                # send enable; new state is ENABLED
                await harness.remote.cmd_enable.start(timeout=STD_TIMEOUT)
                self.assertEqual(csc.summary_state, state.ENABLED)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=STD_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.ENABLED)

                # send disable; new state is DISABLED
                await harness.remote.cmd_disable.start(timeout=LONG_TIMEOUT)
                self.assertEqual(csc.summary_state, state.DISABLED)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=STD_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.DISABLED)

                # send standby; new state is STANDBY
                await harness.remote.cmd_standby.start(timeout=STD_TIMEOUT)
                self.assertEqual(csc.summary_state, state.STANDBY)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=STD_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.STANDBY)

                # send exitControl; new state is OFFLINE
                await harness.remote.cmd_exitControl.start(timeout=STD_TIMEOUT)
                self.assertEqual(csc.summary_state, state.OFFLINE)
                ss_remote = await ss_remote_evt.next(flush=False, timeout=STD_TIMEOUT)
                self.assertEqual(ss_remote.summaryState, state.OFFLINE)

        asyncio.get_event_loop().run_until_complete(doit())

if __name__ == '__main__':
    unittest.main()
