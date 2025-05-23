# This file is part of ts_dimm.
#
# Developed for the Vera Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the Vera Rubin Observatory
# Project (https://www.lsst.org).
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

import abc
from enum import IntEnum

__all__ = ["BaseDIMM", "DIMMStatus"]

"""The status of the DIMM controller."""
DIMMStatus = {
    "NOTSET": 0,
    "INITIALIZED": 1 << 1,
    "RUNNING": 1 << 2,
    "ERROR": 1 << 3,
}


class AutomationMode(IntEnum):
    OFF = 0
    AUTO = 1
    MANUAL = 2


class BaseDIMM(abc.ABC):
    """Base class for DIMM controllers.

    This class defines the minimum set of methods required to operate a DIMM in
    the context of the LSST CSC environment. When developing a controller for a
    CSC, one should subclass this method and overwrite the methods as required
    to setup and operate the DIMM.
    """

    def __init__(self, log, simulate):
        self.status = {
            "status": DIMMStatus["NOTSET"],
            "ra": 0.0,
            "dec": 0.0,
            "altitude": 0.0,
            "azimuth": 0.0,
            "hrnum": 0,
        }
        self.ameba = {
            "mode": -1,
            "state": -1,
            "sunAltitude": float("nan"),
            "condition": -1,
            "startTime": 0,
            "finishTime": 0,
        }
        self.log = log.getChild(type(self).__name__)
        self.simulate = simulate

    async def setup(self, config):
        """Base DIMM setup method.

        When subclassing avoid using argv.

        Parameters
        ----------
        config : `object`
            Configuration object

        """
        pass

    @abc.abstractmethod
    def get_config_schema(self):
        """Get the configuration schema for this DIMM Controller.

        Returns
        -------
        `dict`
            The configuration schema in yaml format.
        """
        raise NotImplementedError()

    async def unset(self):
        """Set DIMM status to NOTSET."""
        self.status["status"] = DIMMStatus["NOTSET"]

    async def start(self):
        """Start the DIMM."""
        self.status["status"] = DIMMStatus["RUNNING"]

    async def stop(self):
        """Stop the DIMM."""
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def get_status(self):
        """Return status of the DIMM.

        Returns
        -------
        status : dict
            Dictionary with DIMM status.

        """
        return self.status

    async def get_ameba(self):
        """Return the AMEBA telemetry.

        Returns
        -------
        ameba : dict
            Dictionary with AMEBA status.
        """
        return self.ameba

    async def set_automation_mode(self, mode: AutomationMode) -> None:
        """Sets the DIMM to off (0), automatic (1), or manual (2) operation.

        Parameter
        ---------
        mode : AutomationMode
            Desired DIMM operating mode.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def get_measurement(self):
        """Return a new seeing measurement.

        Returns
        -------
        measurement : `dict` | `None`
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event. None if no new measurement is available yet.
        """
        raise NotImplementedError()
