
__all__ = ['BaseDIMM', 'DIMMStatus']

DIMMStatus = {'NOTSET': 0,
              'INITIALIZED': 1 << 1,
              'RUNNING': 1 << 2,
              'ERROR': 1 << 3,
              }


class BaseDIMM:
    """Base class for DIMM controllers.

This class defines the minimum set of methods required to operate a DIMM in the context of the
LSST CSC environment. When developing a controller for a CSC, one should subclass this method and
overwrite the methods as required to setup and operate the DIMM.
    """
    def __init__(self):
        self.status = {'status': DIMMStatus['NOTSET'],
                       'ra': 0.,
                       'dec': 0.,
                       'altitude': 0.,
                       'azimuth': 0.,
                       'hrnum': 0,
                       }

    def setup(self, **argv):
        """Base DIMM setup method.

        When subclassing avoid using argv.

        Parameters
        ----------
        argv :
            Named parameters

        """
        pass

    def unset(self):
        """Unset SimDim."""
        self.status['status'] = DIMMStatus['NOTSET']

    def start(self):
        """Start DIMM."""
        self.status['status'] = DIMMStatus['RUNNING']

    def stop(self):
        """Stop DIMM."""
        self.status['status'] = DIMMStatus['INITIALIZED']

    def get_status(self):
        """Returns status of the DIMM.

        Returns
        -------
        status : dict
            Dictionary with DIMM status.

        """
        return self.status
