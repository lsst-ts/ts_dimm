
import asyncio
import logging

import SALPY_DIMM

from lsst.ts.salobj import base_csc

from .model import Model

__all__ = ['DIMMCSC']

SEEING_LOOP_DONE = 101
""" Seeing loop done (`int`).

This error code is published in `SALPY_DIMM.DIMM_logevent_errorCodeC` if the coroutine that
gets new seeing data from the controller finishes while the CSC is in enable state.
"""
TELEMETRY_LOOP_DONE = 102
""" Telemetry loop done (`int`).

This error code is published in `SALPY_DIMM.DIMM_logevent_errorCodeC` if the coroutine that
monitors the health and status of the DIMM finishes while the CSC is in enable state.
"""


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

        # publish settingVersions
        settingVersions_topic = self.evt_settingVersions.DataType()
        settingVersions_topic.recommendedSettingsVersion = \
            self.model.config['settingVersions']['recommendedSettingsVersion']
        settingVersions_topic.recommendedSettingsLabels = self.model.get_settings()

        self.evt_settingVersions.put(settingVersions_topic)

        self.loop_die_timeout = 5  # how long to wait for the loops to die?

        self.telemetry_loop_running = False
        self.telemetry_loop_task = None

        self.seeing_loop_running = False
        self.seeing_loop_task = None

        self.health_monitor_loop_task = asyncio.ensure_future(self.health_monitor())

    def end_start(self, id_data):
        """End do_start; called after state changes
        but before command acknowledged.

        This method call setup on the model, passing the selected setting.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.model.setup(id_data.data.settingsToApply)

    def end_enable(self, id_data):
        """End do_enable; called after state changes
        but before command acknowledged.

        This method will call `start` on the model controller and start the telemetry
        and seeing monitoring loops.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        self.model.controller.start()
        self.telemetry_loop_task = asyncio.ensure_future(self.telemetry_loop())
        self.seeing_loop_task = asyncio.ensure_future(self.seeing_loop())

    def begin_disable(self, id_data):
        """Begin do_disable; called before state changes.

        This method will try to gracefully stop the telemetry and seeing loops by setting
        the running flag to False, then stops the model controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.telemetry_loop_running = False
        self.seeing_loop_running = False

        self.model.controller.stop()

    async def do_disable(self, id_data):
        """Transition to from `State.ENABLED` to `State.DISABLED`.

        After switching from enable to disable, wait for telemetry and seeing loop to
        finish. If they take longer then a timeout to finish, cancel the future.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self._do_change_state(id_data, "disable", [base_csc.State.ENABLED], base_csc.State.DISABLED)

        await self.wait_loop(self.telemetry_loop_task)

        await self.wait_loop(self.seeing_loop_task)

    def begin_standby(self, id_data):
        """Begin do_standby; called before the state changes.

        Before transitioning to standby, unset the model controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.model.unset_controller()

    async def telemetry_loop(self):
        """Telemetry loop coroutine. This method should only be running if the component is enabled. It will get
        the state of the model controller and output it to the telemetry stream at the heartbeat interval.
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

    async def seeing_loop(self):
        """Seeing loop coroutine. This method is responsible for getting new measurements from the DIMM
        controller and and output them as events. The choice of SAL Events instead of SAL Telemetry comes
        from the fact that the measurements are not periodic. They may take different amount of time depending
        of the star being used to measure seeing, be interrupted during the selection of a new target and so
        on. The model controller can just raise an exception in case of an error and the health loop will
        catch it and take appropriate actions.
        """
        if self.seeing_loop_running:
            raise IOError('Seeing loop still running...')
        self.seeing_loop_running = True

        while self.seeing_loop_running:
            data = await self.model.controller.get_measurement()
            data_topic = self.evt_dimmMeasurement.DataType()
            for info in data:
                setattr(data_topic, info, data[info])
            self.evt_dimmMeasurement.put(data_topic)

    async def health_monitor(self):
        """This loop monitors the health of the DIMM controller and the seeing and telemetry loops. If an issue happen
        it will output the `errorCode` event and put the component in FAULT state.
        """
        while True:
            if self.summary_state == base_csc.State.ENABLED:
                if self.seeing_loop_task.done():
                    error_topic = self.evt_errorCode.DataType()
                    error_topic.errorCode = SEEING_LOOP_DONE
                    error_topic.errorReport = 'Seeing loop died while in enable state.'
                    error_topic.traceback = str(self.seeing_loop_task.exception().with_traceback())
                    self.evt_errorCode.put(error_topic)

                    self.summary_state = base_csc.State.FAULT

                if self.telemetry_loop_task.done():
                    error_topic = self.evt_errorCode.DataType()
                    error_topic.errorCode = TELEMETRY_LOOP_DONE
                    error_topic.errorReport = 'Telemetry loop died while in enable state.'
                    error_topic.traceback = str(self.telemetry_loop_task.exception().with_traceback())
                    self.evt_errorCode.put(error_topic)

                    self.summary_state = base_csc.State.FAULT

            await asyncio.sleep(base_csc.HEARTBEAT_INTERVAL)

    async def wait_loop(self, loop):
        """A utility method to wait for a task to die or cancel it and handle the aftermath.

        Parameters
        ----------
        loop : _asyncio.Future

        """
        # wait for telemetry loop to die or kill it if timeout
        timeout = True
        for i in range(self.loop_die_timeout):
            if loop.done():
                timeout = False
                break
            await asyncio.sleep(base_csc.HEARTBEAT_INTERVAL)
        if timeout:
            loop.cancel()
        try:
            await loop
        except asyncio.CancelledError:
            self.log.info('Loop cancelled...')
        except Exception as e:
            # Something else may have happened. I still want to disable as this will stop the loop on the
            # target production
            self.log.exception(e)
