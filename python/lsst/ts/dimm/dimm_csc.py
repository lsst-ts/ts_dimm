
import asyncio
import logging

import SALPY_DIMM

from lsst.ts.salobj import base_csc

from .model import Model

__all__ = ['DIMMCSC']


class DIMMCSC(base_csc.BaseCsc):
    """
    Commandable SAL Component to interface with the LSST DIMM.
    """

    def __init__(self, index):
        """
        Initialize DIMM CSC.

        Parameters
        ----------
        index : int
            Index for the DIMM. This enables the control of multiple DIMMs.
        """
        self.log = logging.getLogger("DIMM-CSC[%i]" % index)

        self.model = Model()  # instantiate the model so I can have the settings once the component is up

        super().__init__(SALPY_DIMM, index)

        # set/publish summaryState
        self.summary_state = base_csc.State.STANDBY

        # publish settingVersions
        settingVersions_topic = self.evt_settingVersions.DataType()
        settingVersions_topic.recommendedSettingsVersion = \
            self.model.config['settingVersions']['recommendedSettingsVersion']
        settingVersions_topic.recommendedSettingsLabels = self.model.get_settings()

        self.evt_settingVersions.put(settingVersions_topic)

        self.telemetry_loop_running = False

    def end_start(self, id_data):
        """End do_start; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.model.setup(id_data.data.settingsToApply)

    def end_enable(self, id_data):
        """End do_enable; called after state changes
        but before command acknowledged.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        asyncio.ensure_future(self.telemetry_loop())

    def begin_disable(self, id_data):
        """Begin do_disable; called before state changes.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.telemetry_loop_running = False

    async def telemetry_loop(self):
        """

        Returns
        -------

        """
        if self.telemetry_loop_running:
            raise IOError('Telemetry loop still running...')
        self.telemetry_loop_running = True

        while self.telemetry_loop_running:
            state = self.model.controller.get_status()
            state_topic = self.tel_status.DataType()
            state_topic.status = state['status']
            state_topic.hrNum = state['hrnum']
            state_topic.altitude = state['altitude']
            state_topic.azimuth = state['azimuth']
            state_topic.ra = state['ra']
            state_topic.decl = state['dec']

            self.tel_status.put(state_topic)

            await asyncio.sleep(base_csc.HEARTBEAT_INTERVAL)
