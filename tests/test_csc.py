import unittest
import asyncio
import numpy as np

from lsst.ts import salobj

from lsst.ts.dimm import dimm_csc

import SALPY_DIMM

np.random.seed(47)

index_gen = salobj.index_generator()


class Harness:
    def __init__(self, index):
        salobj.test_utils.set_random_lsst_dds_domain()
        # import pdb; pdb.set_trace()
        self.csc = dimm_csc.DIMMCSC(index=index)
        self.remote = salobj.Remote(SALPY_DIMM, index)


class TestDIMMCSC(unittest.TestCase):

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

            commands = ("start", "enable", "disable", "exitControl", "standby")
            index = next(index_gen)
            self.assertGreater(index, 0)

            harness = Harness(index)

            # Check initial state
            current_state = await harness.remote.evt_summaryState.next(flush=False, timeout=1.)

            self.assertEqual(harness.csc.summary_state, salobj.State.STANDBY)
            self.assertEqual(current_state.summaryState, salobj.State.STANDBY)

            # Check that settingVersions was published and matches expected values
            setting_versions = await harness.remote.evt_settingVersions.next(flush=False, timeout=1.)
            self.assertEqual(setting_versions.recommendedSettingsVersion,
                             harness.csc.model.config['settingVersions']['recommendedSettingsVersion'])
            self.assertEqual(setting_versions.recommendedSettingsLabels,
                             harness.csc.model.get_settings())
            self.assertTrue(setting_versions.recommendedSettingsVersion in
                            setting_versions.recommendedSettingsLabels.split(','))
            self.assertTrue('simulation' in
                            setting_versions.recommendedSettingsLabels.split(','))

            for bad_command in commands:
                if bad_command in ("start", "exitControl"):
                    continue  # valid command in STANDBY state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=1.)

            # send start; new state is DISABLED
            cmd_attr = getattr(harness.remote, f"cmd_start")
            state_coro = harness.remote.evt_summaryState.next(flush=True, timeout=1.)
            start_topic = cmd_attr.DataType()
            start_topic.settingsToApply = 'simulation'  # user simulation setting.
            id_ack = await cmd_attr.start(start_topic, timeout=120)  # this one can take longer to execute
            state = await state_coro
            self.assertEqual(id_ack.ack.ack, harness.remote.salinfo.lib.SAL__CMD_COMPLETE)
            self.assertEqual(id_ack.ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)
            self.assertEqual(state.summaryState, salobj.State.DISABLED)

            for bad_command in commands:
                if bad_command in ("enable", "standby"):
                    continue  # valid command in DISABLED state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=1.)

            # send enable; new state is ENABLED
            cmd_attr = getattr(harness.remote, f"cmd_enable")
            state_coro = harness.remote.evt_summaryState.next(flush=True, timeout=1.)
            id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=1.)
            state = await state_coro
            self.assertEqual(id_ack.ack.ack, harness.remote.salinfo.lib.SAL__CMD_COMPLETE)
            self.assertEqual(id_ack.ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.ENABLED)
            self.assertEqual(state.summaryState, salobj.State.ENABLED)

            for bad_command in commands:
                if bad_command == "disable":
                    continue  # valid command in ENABLE state
                with self.subTest(bad_command=bad_command):
                    cmd_attr = getattr(harness.remote, f"cmd_{bad_command}")
                    with self.assertRaises(salobj.AckError):
                        id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=1.)

            # check that received telemetry topic from dimm
            try:
                await harness.remote.tel_status.next(flush=True, timeout=salobj.base_csc.HEARTBEAT_INTERVAL*5)
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
            id_ack = await cmd_attr.start(cmd_attr.DataType(), timeout=30.)
            self.assertEqual(id_ack.ack.ack, harness.remote.salinfo.lib.SAL__CMD_COMPLETE)
            self.assertEqual(id_ack.ack.error, 0)
            self.assertEqual(harness.csc.summary_state, salobj.State.DISABLED)

        asyncio.get_event_loop().run_until_complete(doit())


if __name__ == '__main__':
    unittest.main()
