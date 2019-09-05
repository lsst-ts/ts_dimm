# This file is part of ts_environment.
#
# Developed for the LSST Telescope and Site Systems.
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

__all__ = ['Model']

import os, yaml, logging

from lsst.ts.dimm import controllers

available_controllers = {'sim': controllers.SimDIMM,
                         'soar': controllers.SOARDIMM,
                         'astelco': controllers.AstelcoDIMM}

class Model:
    """
    """
    def __init__(self, log):
        self.log = log

        self.config_path = \
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/config.yaml')

        with open(self.config_path, 'r') as stream:
            self.config = yaml.load(stream)

        self.controller = None

    def get_settings(self):
        """Get a comma separated string with the valid setting.

        Returns
        -------
        valid_settings : str

        """
        valid_settings = ''

        n_set = len(self.config['settingVersions']['recommendedSettingsLabels'])

        for i, label in enumerate(self.config['settingVersions']['recommendedSettingsLabels']):
            valid_settings += label
            if i < n_set-1:
                valid_settings += ','

        return valid_settings

    def setup(self, setting):
        """Setup the model with the given setting.

        Parameters
        ----------
        setting : str
            A string with the selected setting label. Must match one on the configuration file.

        Returns
        -------

        """
        if len(setting) == 0:
            setting = self.config['settingVersions']['recommendedSettingsVersion']
            self.log.debug('Received empty setting label. Using default: %s', setting)

        if setting not in self.config['settingVersions']['recommendedSettingsLabels']:
            raise IOError('Setting %s not a valid label. Must be one of %s.',
                          setting,
                          self.get_settings())

        if self.controller is not None:
            self.log.debug('Controller already set. Unsetting.')
            self.unset_controller()

        self.controller = available_controllers[self.config['setting'][setting]['type']](self.log)
        self.controller.setup(**self.config['setting'][setting]['configuration'])

    def unset_controller(self):
        """Unset controller.

        This will call unset method on controller and make controller = None.

        Returns
        -------

        """
        self.controller.unset()
        self.controller = None
