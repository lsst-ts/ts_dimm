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
import math
import types
import traceback

from . import __version__
from .config_schema import CONFIG_SCHEMA
from .controllers.base_dimm import DIMMStatus
from lsst.ts.dimm import controllers
from lsst.ts import salobj

__all__ = ["DIMMCSC"]

available_controllers = {
    "sim": controllers.SimDIMM,
    "soar": controllers.SOARDIMM,
    "astelco": controllers.AstelcoDIMM,
}

SEEING_LOOP_DONE = 101
"""Seeing loop done (`int`).

This error code is published in `DIMM_logevent_errorCodeC` if the coroutine
that gets new seeing data from the controller finishes while the CSC is in
enable state.
"""
TELEMETRY_LOOP_DONE = 102
"""Telemetry loop done (`int`).

This error code is published in `DIMM_logevent_errorCodeC` if the coroutine
that monitors the health and status of the DIMM finishes while the CSC is in
enable state.
"""
CONTROLLER_START_FAILED = 103
"""Controller Start Failed (`int).

This error code is published in `DIMM_logevent_errorCodeC` if the coroutine
that starts the controller fails while transititng to enable state.
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
    version = __version__

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
        super().__init__(
            "DIMM",
            index=index,
            config_schema=CONFIG_SCHEMA,
            config_dir=config_dir,
            initial_state=initial_state,
            simulation_mode=simulation_mode,
        )

        # A remote to weather station data
        self.ws_remote = salobj.Remote(
            self.domain,
            "WeatherStation",
            1,
            readonly=True,
            include=[
                "weather",
                "windSpeed",
                "windDirection",
                "dewPoint",
                "precipitation",
                "snowDepth",
            ],
        )

        self.controller = None

        self.loop_die_timeout = 5  # how many heartbeats to wait for the loops to die?

        self.telemetry_loop_running = False
        self.telemetry_loop_task = None

        self.seeing_loop_running = False
        self.seeing_loop_task = None

        self.csc_running = True
        self.health_monitor_loop_task = asyncio.create_task(self.health_monitor())

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
            await self.setup(config)
            if config.controller == "astelco":
                self.controller.ws_remote = self.ws_remote
        elif self.simulation_mode == 1:
            self.log.debug(
                "Simulation mode is on. Using default simulation controller."
                "Configuration will be ignored."
            )
            await self.setup(SIM_CONFIG)

    async def setup(self, config):
        """Setup the controller with the given setting.

        Parameters
        ----------
        config : `object`
            Namespace with configuration.
        """
        if self.controller is not None:
            self.log.debug("Controller already set. Unsetting.")
            await self.unset_controller()

        self.controller = available_controllers[config.controller](self.log)
        await self.controller.setup(config)

    async def unset_controller(self):
        """Unset controller. This will call unset method on controller and make
        controller = None.
        """
        await self.controller.unset()
        self.controller = None

    async def end_enable(self, id_data):
        """End do_enable; called after state changes but before command
        acknowledged.

        This method will call `start` on the controller and start the telemetry
        and seeing monitoring loops.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """

        try:
            await self.controller.start()
        except Exception:
            self.log.exception(
                "Failed starting the controller.", report="DIMM reported error state."
            )
            self.fault(code=CONTROLLER_START_FAILED)
        self.telemetry_loop_task = asyncio.create_task(self.telemetry_loop())
        self.seeing_loop_task = asyncio.create_task(self.seeing_loop())

        await super().end_enable(id_data)

    async def begin_disable(self, id_data):
        """Begin do_disable; called before state changes.

        This method will try to gracefully stop the telemetry and seeing loops
        by setting the running flag to False, then stops the controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        self.cmd_disable.ack_in_progress(id_data, timeout=60)
        self.telemetry_loop_running = False
        self.seeing_loop_running = False

        try:
            await self.controller.stop()
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
        except Exception:
            self.log.exception("Error trying to stop the telemetry loop. Continuing.")

        try:
            await self.wait_loop(self.seeing_loop_task)
        except Exception:
            self.log.exception("Error trying to stop the seeing loop. Continuing.")

        await super().end_disable(id_data)

    async def begin_standby(self, id_data):
        """Begin do_standby; called before the state changes.

        Before transitioning to standby, unset the controller.

        Parameters
        ----------
        id_data : `CommandIdData`
            Command ID and data
        """
        try:
            await self.unset_controller()
        except Exception:
            self.log.exception("Error unsetting controller. Continuing.")

        await super().begin_standby(id_data)
        self.cmd_standby.ack_in_progress(id_data, timeout=60)

    def convert_int(self, value):
        """Convert a value to an int, or 0 in case the conversion fails.

        Parameters
        ----------
        value: `str` or `int`
            The value to convert.

        Returns
        -------
        `int`
            The value converted to an int, or 0 in case the conversion fails.

        """
        try:
            return int(value)
        except ValueError:
            return 0

    def convert_float(self, value):
        """Convert a value to a float, or math.nan in case the conversion
        fails.

        Parameters
        ----------
        value: `str` or `float`
            The value to convert.

        Returns
        -------
        `float`
            The value converted to a float, or math.nan in case the conversion
            fails.

        """
        try:
            return float(value)
        except ValueError:
            return math.nan

    def prepare_status_telemetry(self, state):
        """Prepare the status telemetry for sending by converting the DIMM
        state data values to the expected data types, or to math.nan if the
        conversion fails.

        Parameters
        ----------
        state : `dict`
            Dictionary with DIMM status.

        Returns
        -------
        status_topic: `tel_status.DataType`
            The telescope status telemetry, ready for sending.
        """
        status_topic = self.tel_status.DataType()

        status_topic.status = self.convert_int(state["status"])
        status_topic.hrNum = self.convert_int(state["hrnum"])
        status_topic.altitude = self.convert_float(state["altitude"])
        status_topic.azimuth = self.convert_float(state["azimuth"])
        status_topic.ra = self.convert_float(state["ra"])
        status_topic.decl = self.convert_float(state["dec"])

        return status_topic

    async def telemetry_loop(self):
        """Telemetry loop coroutine. This method should only be running if the
        component is enabled. It will get the state of the controller and
        output it to the telemetry stream at the heartbeat interval.
        """
        if self.telemetry_loop_running:
            raise IOError("Telemetry loop still running...")
        self.telemetry_loop_running = True

        try:
            while self.telemetry_loop_running:
                state = await self.controller.get_status()
                state_topic = self.prepare_status_telemetry(state)

                try:
                    self.tel_status.put(state_topic)
                except ValueError:
                    self.log.debug(f"Ignoring bad telescope state {state}")

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

    def convert_dimmMeasurement_data(self, data):
        """Prepare the DIMM measurement event for sending by converting the
        DIMM measurement data values to the expected data types, or to math.nan
        if the conversion fails.

        Parameters
        ----------
        data: `dict`
            The DIMM measurement data dict.

        Returns
        -------
        converted_data: `dict`
            The DIMM measurement data with the values converted to the expected
            data types.
        """
        converted_data = {
            "timestamp": self.convert_float(data["timestamp"]),
            "hrNum": self.convert_int(data["hrNum"]),
            "secz": self.convert_float(data["secz"]),
            "fwhm": self.convert_float(data["fwhm"]),
            "fwhmx": self.convert_float(data["fwhmx"]),
            "fwhmy": self.convert_float(data["fwhmy"]),
            "r0": self.convert_float(data["r0"]),
            "nimg": self.convert_int(data["nimg"]),
            "dx": self.convert_float(data["dx"]),
            "dy": self.convert_float(data["dy"]),
            "flux": self.convert_float(data["flux"]),
            "fluxL": self.convert_float(data["fluxL"]),
            "scintL": self.convert_float(data["scintL"]),
            "strehlL": self.convert_float(data["strehlL"]),
            "fluxR": self.convert_float(data["fluxR"]),
            "scintR": self.convert_float(data["scintR"]),
            "strehlR": self.convert_float(data["strehlR"]),
        }

        return converted_data

    async def seeing_loop(self):
        """Seeing loop coroutine. This method is responsible for getting new
        measurements from the DIMM controller and and output them as events.
        The choice of SAL Events instead of SAL Telemetry comes from the fact
        that the measurements are not periodic. They may take different amounts
        of time depending on the star being used to measure seeing, be
        interrupted during the selection of a new target and so on. The
        controller can just raise an exception in case of an error and the
        health loop will catch it and take appropriate actions.
        """
        if self.seeing_loop_running:
            raise IOError("Seeing loop still running...")
        self.seeing_loop_running = True

        while self.seeing_loop_running:
            # Initialize variable so it can be logged later
            data = None
            try:
                data = await self.controller.get_measurement()
                if data is not None:
                    converted_data = self.convert_dimmMeasurement_data(data)
                    self.evt_dimmMeasurement.set_put(**converted_data)
                await asyncio.sleep(self.heartbeat_interval)
            except ValueError:
                self.log.debug(f"Ignoring bad data {data}")
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
            asyncio.run(self.controller.stop())
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
