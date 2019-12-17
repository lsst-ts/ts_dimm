import unittest
import asynctest
import asyncio
import numpy as np

from lsst.ts import salobj

from lsst.ts.dimm import dimm_csc

np.random.seed(47)

index_gen = salobj.index_generator()
SHORT_TIMEOUT = 5.


class Harness:
    def __init__(self, index, config_dir, initial_simulation_mode):
        salobj.test_utils.set_random_lsst_dds_domain()
        # import pdb; pdb.set_trace()
        self.csc = dimm_csc.DIMMCSC(index=index,
                                    config_dir=config_dir,
                                    initial_simulation_mode=initial_simulation_mode)
        self.remote = salobj.Remote(domain=self.csc.domain, name="DIMM", index=index)

    async def __aenter__(self):
        await self.csc.start_task
        await self.remote.start_task
        return self

    async def __aexit__(self, *args):
        await self.remote.close()
        await self.csc.close()


class TestDIMMCSC(asynctest.TestCase):

    async def test_standard_state_transitions(self):
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

        commands = ("start", "enable", "disable", "exitControl", "standby")
        index = next(index_gen)
        self.assertGreater(index, 0)

        async with Harness(index, config_dir=None, initial_simulation_mode=1) as harness:

            # Check initial state
            current_state = await harness.remote.evt_summaryState.next(flush=False,
                                                                       timeout=SHORT_TIMEOUT)

            self.assertEqual(harness.csc.summary_state, salobj.State.STANDBY)
            self.assertEqual(current_state.summaryState, salobj.State.STANDBY)

            # Check that settingVersions was published and matches expected values
            setting_versions = await harness.remote.evt_settingVersions.next(flush=False,
                                                                             timeout=SHORT_TIMEOUT)
            self.assertIsNotNone(setting_versions)

            for bad_command in commands:
                if bad_command in ("start", "exitControl"):
                    continue  # valid command in STANDBY state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=SHORT_TIMEOUT)

            # send start; new state is DISABLED
            cmd_attr = getattr(harness.remote, f"cmd_start")
            harness.remote.evt_summaryState.flush()
            id_ack = await cmd_attr.start(timeout=120)
            state = await harness.remote.evt_summaryState.next(flush=False, timeout=SHORT_TIMEOUT)
            self.assertEqual(id_ack.ack, salobj.SalRetCode.CMD_COMPLETE)
            self.assertEqual(id_ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)
            self.assertEqual(state.summaryState, salobj.State.DISABLED)

            for bad_command in commands:
                if bad_command in ("enable", "standby"):
                    continue  # valid command in DISABLED state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=SHORT_TIMEOUT)

            # send enable; new state is ENABLED
            cmd_attr = getattr(harness.remote, f"cmd_enable")
            harness.remote.evt_summaryState.flush()
            id_ack = await cmd_attr.start(timeout=SHORT_TIMEOUT)
            state = await harness.remote.evt_summaryState.next(flush=False, timeout=SHORT_TIMEOUT)
            self.assertEqual(id_ack.ack, salobj.SalRetCode.CMD_COMPLETE)
            self.assertEqual(id_ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.ENABLED)
            self.assertEqual(state.summaryState, salobj.State.ENABLED)

            self.assertIsNotNone(harness.csc.telemetry_loop_task)
            self.assertIsNotNone(harness.csc.seeing_loop_task)

            for bad_command in commands:
                if bad_command == "disable":
                    continue  # valid command in ENABLE state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(timeout=SHORT_TIMEOUT)

            # check that received telemetry topic from dimm
            try:
                await harness.remote.tel_status.next(
                    flush=True, timeout=salobj.base_csc.HEARTBEAT_INTERVAL*5)
            except asyncio.TimeoutError:
                self.assertTrue(False, 'No status published by DIMM')

            # check that received measurement from dimm
            try:
                await harness.remote.evt_dimmMeasurement.next(flush=False,
                                                              timeout=10)
            except asyncio.TimeoutError:
                self.assertTrue(False, 'No measurement published by DIMM.')

            # send disable; new state is DISABLED
            cmd_attr = getattr(harness.remote, f"cmd_disable")
            # this CMD may take some time to complete
            id_ack = await cmd_attr.start(timeout=60.)
            self.assertEqual(id_ack.ack, salobj.SalRetCode.CMD_COMPLETE)
            self.assertEqual(id_ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)


if __name__ == '__main__':
    unittest.main()