
import os
import yaml

from lsst.ts.dimm import controllers

__all__ = ['Model']

available_controllers = {'sim': controllers.SimDIMM,
                         'soar': controllers.SOARDIMM,
                         'astelco': controllers.AstelcoDIMM}


class Model:
    """

    """
    def __init__(self, log):

        self.log = log

        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config/config.yaml')

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
        """Unset controller. This will call unset method on controller and make controller = None.

        Returns
        -------

        """
        self.controller.unset()
        self.controller = None
