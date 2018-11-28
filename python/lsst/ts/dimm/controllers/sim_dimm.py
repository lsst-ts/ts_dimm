

__all__ = ['SimDIMM']


SimStatus = {'NOTSET': 0,
             'INITIALIZED': 1 << 1,
             'RUNNING': 1 << 2,
             'ERROR': 1 << 3,
             }


class SimDIMM:

    def __init__(self):

        self.status = {'status': SimStatus['NOTSET'],
                       'ra': 0.,
                       'dec': 0.,
                       'altitude': 0.,
                       'azimuth': 0.,
                       'hrnum': 0,
                       }

    def setup(self):
        """Setup SimDim."""
        self.status['status'] = SimStatus['INITIALIZED']

    def unset(self):
        """Unset SimDim."""
        self.status['status'] = SimStatus['NOTSET']

    def start(self):
        """Start DIMM."""
        self.status['status'] = SimStatus['RUNNING']

    def stop(self):
        """Stop DIMM."""
        self.status['status'] = SimStatus['INITIALIZED']

    def get_status(self):
        """Returns status of the DIMM.

        Returns
        -------
        status : dict
            Dictionary with DIMM status.

        """

        return self.status
