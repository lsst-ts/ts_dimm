# This file is part of ts_dimm.
#
# Developed for the LSST Data Management System.
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
import types
import pathlib
import traceback

from lsst.ts import salobj

from .model import Model
from .controllers.base_dimm import DIMMStatus

__all__ = ["DIMMCSC"]

SEEING_LOOP_DONE = 101
""" Seeing loop done (`int`).

This error code is published in `SALPY_DIMM.DIMM_logevent_errorCodeC` if the
coroutine that gets new seeing data from the controller finishes while the CSC
is in enable state.
"""
TELEMETRY_LOOP_DONE = 102
""" Telemetry loop done (`int`).

This error code is published in `SALPY_DIMM.DIMM_logevent_errorCodeC` if the
coroutine that monitors the health and status of the DIMM finishes while the
CSC is in enable state.
"""

SIM_CONFIG = types.SimpleNamespace(
    controller="sim",
    avg_seeing=0.5,
    std_seeing=0.1,
    chance_failure=0.0,
    time_in_target=2.0,
    exposure_time=2.0,
)


class DIMMCSC(salobj.ConfigurableCsc):
    """
    Commandable SAL Component to interface with the LSST DIMM.
    """

    valid_simulation_modes = (0, 1)

    def __init__(
        self,
        index,
        config_dir=None,
        initial_state=salobj.State.STANDBY,
        simulation_mode=0,
    ):
        """
        Initialize DIMM CSC.

        Parameters
        ----------
        index : int
            Index for the DIMM. This enables the control of multiple DIMMs.
        """
        schema_path = (
            pathlib.Path(__file__).resolve().parents[4].joinpath("schema", "DIMM.yaml")
        )

        super().__init__(
            "DIMM",
            index=index,
            schema_path=schema_path,
            config_dir=config_dir,
            initial_state=initial_state,
            simulation_mode=simulation_mode,
        )

        # A remote to weather station data
        self.ws_remote = salobj.Remote(self.domain, "WeatherStation", 1)

        self.model = Model(self.log)

        self.loop_die_timeout = 5  # nr. of heartbeats to wait for loops to die

        self.telemetry_loop_running = False
        self.telemetry_loop_task = None

        self.seeing_loop_running = False
        self.seeing_loop_task = None

        self.csc_running = True
        self.health_monitor_loop_task = asyncio.ensure_future(self.health_monitor())

    @staticmethod
    def get_config_pkg():
        return "ts_config_ocs"

    async def configure(self, config):
        """Override superclass configure method to implement CSC
        configuration.

        Parameters
        ----------
        config : `object`
            The configuration as described by the schema at ``schema_path``,
            as a struct-like object.

        """

        if self.simulation_mode == 0:
            self.log.debug(
                "Simulation mode is off. Configuring CSC for "
                f"{config.controller} controller."
            )
            self.model.setup(config)
            if config.controller == "astelco":
                self.model.controller.ws_remote = self.ws_remote
        elif self.simulation_mode == 1:
            self.log.debug(
                "Simulation mode is on. Using default simulation controller."
                "Configuration will be ignored."
            )
            self.model.setup(SIM_CONFIG)

    async def end_enable(self, id_data):
        """End do_enable; called after state changes
        but before command acknowledged.

        This method will call `start` on the model controller and start the
        telemetry and seeing monitoring loops.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        self.model.controller.start()
        self.telemetry_loop_task = asyncio.create_task(self.telemetry_loop())
        self.seeing_loop_task = asyncio.create_task(self.seeing_loop())

        await super().end_enable(id_data)

    async def begin_disable(self, id_data):
        """Begin do_disable; called before state changes.

        This method will try to gracefully stop the telemetry and seeing loops
        by setting the running flag to False, then stops the model controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.telemetry_loop_running = False
        self.seeing_loop_running = False

        try:
            self.model.controller.stop()
        except Exception:
            self.log.exception("Error in begin_disable. Continuing...")

        await super().begin_disable(id_data)

    async def end_disable(self, id_data):
        """Transition to from `State.ENABLED` to `State.DISABLED`.

        After switching from enable to disable, wait for telemetry and seeing
        loop to finish. If they take longer then a timeout to finish, cancel
        the future.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        try:
            await self.wait_loop(self.telemetry_loop_task)

            await self.wait_loop(self.seeing_loop_task)
        except Exception:
            self.log.exception("Error trying to disable CSC. Continuing.")

        await super().end_disable(id_data)

    async def begin_standby(self, id_data):
        """Begin do_standby; called before the state changes.

        Before transitioning to standby, unset the model controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        try:
            self.model.unset_controller()
        except Exception:
            self.log.exception("Error unsetting controller. Continuing.")

        await super().begin_standby(id_data)

    async def telemetry_loop(self):
        """Telemetry loop coroutine. This method should only be running if the
        component is enabled. It will get the state of the model controller and
        output it to the telemetry stream at the heartbeat interval.
        """
        if self.telemetry_loop_running:
            raise IOError("Telemetry loop still running...")
        self.telemetry_loop_running = True

        try:
            while self.telemetry_loop_running:
                state = self.model.controller.get_status()

                self.log.debug(f"state: {state}")

                self.tel_status.set_put(
                    altitude=float(state["altitude"]),
                    azimuth=float(state["azimuth"]),
                    ra=float(state["ra"]),
                    decl=float(state["dec"]),
                )

                if state["status"] == DIMMStatus["ERROR"]:
                    self.log.error("DIMM reported error state.")
                    self.fault(
                        code=TELEMETRY_LOOP_DONE, report="DIMM reported error state."
                    )
                    break

                await asyncio.sleep(self.heartbeat_interval)
        except Exception:
            self.log.exception("Error in telemetry loop.")
            self.fault(
                code=TELEMETRY_LOOP_DONE,
                report="Error in telemetry loop.",
                traceback=traceback.format_exc(),
            )

    async def seeing_loop(self):
        """Seeing loop coroutine. This method is responsible for getting new
        measurements from the DIMM controller and and output them as events.
        The choice of SAL Events  instead of SAL Telemetry comes from the fact
        that the measurements are not periodic. They may take different amount
        of time depending of the star being used to measure seeing, be
        interrupted during the selection of a new target and so on. The model
        controller can just raise an exception in case of an error and the
        health loop will catch it and take appropriate actions.
        """
        if self.seeing_loop_running:
            raise IOError("Seeing loop still running...")
        self.seeing_loop_running = True

        while self.seeing_loop_running:
            try:
                data = await self.model.controller.get_measurement()
                if data is not None:
                    self.evt_dimmMeasurement.set_put(**data)
                await asyncio.sleep(self.heartbeat_interval)
            except Exception:
                self.log.exception("Error in seeing loop.")
                self.fault(
                    code=SEEING_LOOP_DONE,
                    report="Error in seeing loop.",
                    traceback=traceback.format_exc(),
                )
                break

    def fault(self, code, report, traceback=""):
        self.telemetry_loop_running = False
        self.seeing_loop_running = False

        try:
            self.model.controller.stop()
        except Exception:
            self.log.exception("Error going to FAULT. Ignore.")
        super().fault(code=code, report=report, traceback=traceback)

    async def health_monitor(self):
        """This loop monitors the health of the DIMM controller and the seeing
        and telemetry loops. If an issue happen it will output the `errorCode`
        event and put the component in FAULT state.
        """
        while self.csc_running:
            if self.summary_state == salobj.State.ENABLED:
                if self.seeing_loop_task.done():
                    error_report = "Seeing loop died while in enable state."
                    self.evt_errorCode.set_put(
                        errorCode=SEEING_LOOP_DONE,
                        errorReport=error_report,
                        traceback=str(
                            self.seeing_loop_task.exception().with_traceback()
                        ),
                    )

                    self.fault(code=SEEING_LOOP_DONE, report=error_report)

                if self.telemetry_loop_task.done():
                    error_report = "Telemetry loop died while in enable state."
                    self.evt_errorCode.put(
                        errorCode=TELEMETRY_LOOP_DONE,
                        errorReport=error_report,
                        traceback=str(
                            self.telemetry_loop_task.exception().with_traceback()
                        ),
                    )

                    self.fault(code=TELEMETRY_LOOP_DONE, report=error_report)

            await asyncio.sleep(self.heartbeat_interval)

    async def wait_loop(self, loop):
        """A utility method to wait for a task to die or cancel it and handle
        the aftermath.

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
            await asyncio.sleep(self.heartbeat_interval)
        if timeout:
            loop.cancel()

        try:
            await asyncio.wait_for(loop, timeout=self.loop_die_timeout)
        except asyncio.CancelledError:
            self.log.info("Loop cancelled...")
        except Exception as e:
            # Something else may have happened. I still want to disable as this
            # will stop the loop on the target production
            self.log.exception(e)

    async def close(self, exception=None, cancel_start=True):
        """Makes sure CSC closes gratefully.

        Basically set `self.csc_running = False` and awaits for
        `health_monitor_loop_task` to complete.

        """

        self.csc_running = False
        await self.wait_loop(self.health_monitor_loop_task)

        await super().close(exception=exception, cancel_start=cancel_start)
