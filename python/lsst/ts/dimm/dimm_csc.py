
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
