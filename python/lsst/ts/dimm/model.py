
from lsst.ts.dimm import controllers

__all__ = ['Model']

available_controllers = {'sim': controllers.SimDIMM,
                         'soar': controllers.SOARDIMM,
                         'astelco': controllers.AstelcoDIMM}


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
            self.log.debug('Controller already set. Unsetting.')
            self.unset_controller()

        self.controller = available_controllers[config.type](self.log)
        self.controller.setup(config)

    def unset_controller(self):
        """Unset controller. This will call unset method on controller and make controller = None.

        Returns
        -------

        """
        self.controller.unset()
        self.controller = None
