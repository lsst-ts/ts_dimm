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

__all__ = ["BaseDIMM", "DIMMStatus"]

DIMMStatus = {
    "NOTSET": 0,
    "INITIALIZED": 1 << 1,
    "RUNNING": 1 << 2,
    "ERROR": 1 << 3,
}


class BaseDIMM(abc.ABC):
    """Base class for DIMM controllers.

    This class defines the minimum set of methods required to operate a DIMM in
    the context of the LSST CSC environment. When developing a controller for a
    CSC, one should subclass this method and overwrite the methods as required
    to setup and operate the DIMM.
    """

    def __init__(self, log):
        self.status = {
            "status": DIMMStatus["NOTSET"],
            "ra": 0.0,
            "dec": 0.0,
            "altitude": 0.0,
            "azimuth": 0.0,
            "hrnum": 0,
        }
        self.log = log

    def setup(self, config):
        """Base DIMM setup method.

        When subclassing avoid using argv.

        Parameters
        ----------
        config : `object`
            Configuration object

        """
        pass

    def unset(self):
        """Unset SimDim."""
        self.status["status"] = DIMMStatus["NOTSET"]

    def start(self):
        """Start DIMM."""
        self.status["status"] = DIMMStatus["RUNNING"]

    def stop(self):
        """Stop DIMM."""
        self.status["status"] = DIMMStatus["INITIALIZED"]

    def get_status(self):
        """Returns status of the DIMM.

        Returns
        -------
        status : dict
            Dictionary with DIMM status.

        """
        return self.status

    @abc.abstractmethod
    async def get_measurement(self):
        """Coroutine to wait and return new seeing measurements.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event.
        """
        raise NotImplementedError()
