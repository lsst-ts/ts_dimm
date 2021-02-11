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

from lsst.ts.dimm import controllers

__all__ = ["Model"]

available_controllers = {
    "sim": controllers.SimDIMM,
    "soar": controllers.SOARDIMM,
    "astelco": controllers.AstelcoDIMM,
}


class Model:
    """Model to operate generic DIMM controllers.
    """

    def __init__(self, log):
        self.log = log
        self.controller = None

    def setup(self, config):
        """Setup the model with the given setting.

        Parameters
        ----------
        config : `object`
            Namespace with configuration.
        """
        if self.controller is not None:
            self.log.debug("Controller already set. Unsetting.")
            self.unset_controller()

        self.controller = available_controllers[config.type](self.log)
        self.controller.setup(config)

    def unset_controller(self):
        """Unset controller. This will call unset method on controller and make
         controller = None.
        """
        self.controller.unset()
        self.controller = None
