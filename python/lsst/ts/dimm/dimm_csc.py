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
import traceback
import types

from lsst.ts import salobj, utils
from lsst.ts.dimm import controllers

from . import __version__
from .config_schema import CONFIG_SCHEMA
from .controllers.base_dimm import DIMMStatus
from .utils.conversion import (
    convert_dimm_measurement_data,
    convert_to_float,
    convert_to_int,
)

__all__ = ["DIMMCSC", "run_dimm_csc"]

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


def run_dimm_csc():
    asyncio.run(DIMMCSC.amain(index=True))


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
            extra_commands={
                # DM-48873 Remove after Cycle 39 XML is phased out.
                "init",
                "park",
                "parkMount",
                "clearMountError",
                "defineFocus",
                "moveDomeSideA",
                "moveDomeSideB",
                "point",
                "runDimmManual",
                "setAmebaMode",
                "setManualTarget",
                "setProgramStatus",
                "setSky",
                "setWeather",
                "stop",
                "stopDimmManual",
                "offsetFocus",
                "offsetPointing",
            },
        )

        # A remote to weather station data
        self.ws_remote = salobj.Remote(
            self.domain,
            "ESS",
            301,
            readonly=True,
            include=[
                "temperature",
                "relativeHumidity",
                "pressure",
                "airFlow",
                "dewPoint",
                "precipitation",
            ],
        )

        # The controller and its state
        self.controller = None
        self.controller_running = False

        self.loop_die_timeout = 5  # how many heartbeats to wait for the loops to die?

        self.telemetry_loop_running = False
        self.telemetry_loop_task = None

        self.seeing_loop_running = False
        self.seeing_loop_task = utils.make_done_future()

        self.csc_running = True
        self.measurement_validity = None
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
        if self.controller is not None:
            self.log.debug("Controller already set. Unsetting.")
            await self.unset_controller()

        for instance in config.instances:
            if instance["sal_index"] == self.salinfo.index:
                break
        else:
            raise salobj.ExpectedError(
                f"No config found for sal_index={self.salinfo.index}"
            )

        settings = types.SimpleNamespace(**instance)
        controller_class = available_controllers[settings.controller]
        self.controller = controller_class(
            log=self.log, simulate=self.simulation_mode != 0
        )
        self.measurement_validity = settings.measurement_validity

        # TODO DM-33985 Improve the way the WeatherStation remote is
        #  initialized in the controller.
        if settings.controller == "astelco":
            self.controller.ws_remote = self.ws_remote

        config = settings.config
        config_schema = self.controller.get_config_schema()
        validator = salobj.DefaultingValidator(config_schema)
        config_dict = validator.validate(config)
        if not isinstance(config_dict, dict):
            raise RuntimeError(f"config {config!r} invalid: not a dict")
        controller_config = types.SimpleNamespace(**config_dict)

        await self.controller.setup(controller_config)

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
            self.log.exception("Failed starting the controller.")
            await self.fault(code=CONTROLLER_START_FAILED)
            raise RuntimeError(
                "Failed to start controller. Check configuration and make sure DIMM"
                "controller is alive and reachable by the CSC."
            )

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
        await self.cmd_disable.ack_in_progress(id_data, timeout=60)
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
        await self.cmd_standby.ack_in_progress(id_data, timeout=60)

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
        status_topic: `dict`
            The telescope status telemetry, ready for sending.
        """
        return dict(
            status=convert_to_int(state["status"]),
            hrNum=convert_to_int(state["hrnum"]),
            altitude=convert_to_float(state["altitude"]),
            azimuth=convert_to_float(state["azimuth"]),
            ra=convert_to_float(state["ra"]),
            decl=convert_to_float(state["dec"]),
        )

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
                self.controller_running = state["status"] == DIMMStatus["RUNNING"]
                self.log.debug(f"Controller running? {self.controller_running}")

                state_topic = self.prepare_status_telemetry(state)
                state_topic = self.clean_topic("tel_status", state_topic)
                try:
                    await self.tel_status.set_write(**state_topic)
                except ValueError:
                    self.log.debug(f"Ignoring bad telescope state {state}")

                if state["status"] == DIMMStatus["ERROR"]:
                    self.log.error("DIMM reported error state.")
                    await self.fault(
                        code=TELEMETRY_LOOP_DONE, report="DIMM reported error state."
                    )
                    break

                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("Error in telemetry loop.")
            await self.fault(
                code=TELEMETRY_LOOP_DONE,
                report="Error in telemetry loop.",
                traceback=traceback.format_exc(),
            )

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
                # Only send telemetry if the controller is operational
                if data is not None and self.controller_running:
                    converted_data = convert_dimm_measurement_data(data)
                    converted_data["expiresAt"] = (
                        converted_data["timestamp"] + self.measurement_validity
                    )
                    converted_data["expiresIn"] = self.measurement_validity
                    converted_data = self.clean_topic(
                        "evt_dimmMeasurement", converted_data
                    )

                    await self.evt_dimmMeasurement.set_write(**converted_data)
                await asyncio.sleep(self.heartbeat_interval)
            except ValueError:
                self.log.debug(f"Ignoring bad data {data}")
            except Exception:
                self.log.exception("Error in seeing loop.")
                await self.fault(
                    code=SEEING_LOOP_DONE,
                    report="Error in seeing loop.",
                    traceback=traceback.format_exc(),
                )
                break

    def clean_topic(self, topic_name, kwargs):
        """Adjusts the state_topic dictionary to conform with the XML schema.

        Adds the missing keys from the state_topic dictionary, setting
        those to the default of the correct data type. Removes keys from
        the dictionary that are not part of the schema.

        TODO: DM-48873 remove this function and all calls to it.

        Parameters
        ----------
        topic_name : str
            The name of the topic for the SAL schema to apply.

        kwargs : dict[str, Any]
            The dictionary to be cleaned up.

        Returns
        -------
        dict[str, Any]
            The cleaned dictionary.
        """
        # Get all the fields for either DDS or Kafka salobj (h/t Wouter)
        schema = {}
        if hasattr(self.salinfo, "metadata"):
            schema = set(self.salinfo.metadata.topic_info[topic_name].field_info.keys())
        elif hasattr(self.salinfo, "component_info"):
            schema = set(self.salinfo.component_info.topics[topic_name].fields.keys())
        base_attributes = {
            "private_identity",
            "private_origin",
            "private_rcvStamp",
            "private_revCode",
            "private_seqNum",
            "private_sndStamp",
            "salIndex",
        }
        schema -= base_attributes

        # Add missing keys with default values
        for key in schema:
            if key not in kwargs:
                kwargs[key] = 0  # Default value of zero

        # Remove keys that are not in the schema
        keys_to_remove = [key for key in kwargs if key not in schema]
        for key in keys_to_remove:
            del kwargs[key]

        return kwargs

    async def do_init(self, data):
        """Initialize program(s) by sending INIT via the Kornilov protocol.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_park(self, data):
        """Park program(s) on the DIMM via the Kornilov protocol.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_parkMount(self, data):
        """Park program(s) on the DIMM via the Kornilov protocol.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_clearMountError(self, data):
        """Reset telescope error conditions.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_gotoAltAz(self, data):
        """Move to Alt/AZ position.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_gotoRaDec(self, data):
        """Move to RA/DEC position.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_defineFocus(self, data):
        """Defines the current focus position to the position specified.

        Future telemetry will show the focuser at this position. The focuser
        is not physically moved by this command.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_moveDomeSideA(self, data):
        """Manually move the first dome side to the requested position.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_moveDomeSideB(self, data):
        """Manually move the second dome side to the requested position.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_point(self, data):
        """Start equatorial tracking at the set up location.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_runDimmManual(self, data):
        """Manually begin the DIMM control loop.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_setAmebaMode(self, data):
        """Set ameba mode (off, auto, or manual) through tt-master.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_setManualTarget(self, data):
        """Set up a manual target in the AMEBA module.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_setProgramStatus(self, data):
        """Send command(s) to change the state of program(s) on the DIMM.

        For each item, use a value from the ProgramControl enumeration to
        specify the desired action, or NoAction to leave the state of the
        program as it was.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_setSky(self, data):
        """Send sky sensor data to the DIMM.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_setWeather(self, data):
        """Send weather data to the DIMM.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_stop(self, data):
        """Discontinue tracking without otherwise moving the telescope.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_stopDimmManual(self, data):
        """Manually stop the DIMM control loop.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_offsetFocus(self, data):
        """Apply an offset to the focuser position.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_offsetPointing(self, data):
        """Apply an offset to the telescope's pointing.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_changeDwellRate(self, data):
        """Change how long to sample for in seconds.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def do_changeMeasurementRate(self, data):
        """Change sample integration.

        Parameters
        ----------
        data : A SALOBJ data object
            Contains the data as defined in the SAL XML file.
        """
        self.assert_enabled()
        raise salobj.ExpectedError("Not implemented yet.")

    async def fault(self, code, report, traceback=""):
        self.telemetry_loop_running = False
        self.seeing_loop_running = False

        try:
            await self.controller.stop()
        except Exception:
            self.log.exception("Error going to FAULT. Ignore.")
        await super().fault(code=code, report=report, traceback=traceback)

    async def health_monitor(self):
        """This loop monitors the health of the DIMM controller and the seeing
        and telemetry loops. If an issue happen it will output the `errorCode`
        event and put the component in FAULT state.
        """
        while self.csc_running:
            if self.summary_state == salobj.State.ENABLED:
                if self.seeing_loop_task is not None and self.seeing_loop_task.done():
                    error_report = "Seeing loop died while in enable state."
                    await self.fault(code=SEEING_LOOP_DONE, report=error_report)

                if (
                    self.telemetry_loop_task is not None
                    and self.telemetry_loop_task.done()
                ):
                    error_report = "Telemetry loop died while in enable state."
                    await self.fault(code=TELEMETRY_LOOP_DONE, report=error_report)

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
