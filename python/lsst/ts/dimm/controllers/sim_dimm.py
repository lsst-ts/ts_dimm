
import time
import datetime
import asyncio

import numpy as np

__all__ = ['SimDIMM']


SimStatus = {'NOTSET': 0,
             'INITIALIZED': 1 << 1,
             'RUNNING': 1 << 2,
             'ERROR': 1 << 3,
             }


class SimDIMM:
    """This controller provides a simmulated DIMM interface that can be used for testing and
mocking a real DIMM.
    """

    def __init__(self):

        self.status = {'status': SimStatus['NOTSET'],
                       'ra': 0.,
                       'dec': 0.,
                       'altitude': 0.,
                       'azimuth': 0.,
                       'hrnum': 0,
                       }

        self.avg_seeing = 0.5  # average seeing (arcsec)
        self.std_seeing = 0.1  # standard deviation (arcsec)
        self.chance_failure = 0.0  # chance that the dimm will fail (in 1/100)
        self.time_in_target = {'min': 2, 'max': 6}  # in hours
        self.exposure_time = {'min': 2, 'max': 6, 'std': 5}  # in seconds

        self.measurement_loop = None
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

        self.current_hrnum = 0
        self.current_exptime = 0

    def setup(self, avg_seeing, std_seeing, chance_failure, time_in_target, exposure_time):
        """Setup SimDim.

        Parameters
        ----------
        avg_seeing : float
            The average seeing in arcsec.
        std_seeing : float
            Standard deviation of seeing in arcsec.
        chance_failure : float
            Chance of dimm fail, in 1/100.
        time_in_target : dict(min, max)
            Dictionary with minimum and maximum time in target (in hours).
        exposure_time : dict(min, max, std)
            Dictionary with minimum, maximum and standard deviation for exposure time (in seconds).

        Returns
        -------

        """
        self.status['status'] = SimStatus['INITIALIZED']
        if avg_seeing < 0.:
            raise IOError('Avg seeing must be larger than zero. Got %f' % avg_seeing)
        self.avg_seeing = avg_seeing

        if std_seeing < 0.:
            raise IOError('Std seeing must be larger than zero. Got %f' % std_seeing)
        self.std_seeing = std_seeing

        if not (0. <= chance_failure <= 100.):
            raise IOError('Chance of failure must be between 0 and 100.')
        self.chance_failure = chance_failure

        if 'min' not in time_in_target or 'max' not in time_in_target:
            raise IOError('time_in_target must have min and max. Got %s' % time_in_target.keys())
        self.time_in_target = time_in_target

        if 'min' not in exposure_time or 'max' not in exposure_time or 'std' not in exposure_time:
            raise IOError('exposure_time must have min, max and std. Got %s' % exposure_time.keys())
        self.exposure_time = exposure_time

    def unset(self):
        """Unset SimDim."""
        self.status['status'] = SimStatus['NOTSET']

    def start(self):
        """Start DIMM."""
        self.status['status'] = SimStatus['RUNNING']
        self.measurement_loop = asyncio.ensure_future(self.generate_measurements())

    def stop(self):
        """Stop DIMM."""
        self.measurement_loop.cancel()
        self.status['status'] = SimStatus['INITIALIZED']

    def get_status(self):
        """Returns status of the DIMM.

        Returns
        -------
        status : dict
            Dictionary with DIMM status.

        """

        return self.status

    def new_measurement(self):
        """Generate a new measurement for the simulated DIMM.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL Event.
        """
        self.measurement_start = datetime.datetime.now()

        modified_exptime = self.current_exptime + np.random.uniform(-self.exposure_time['std'] / 2.,
                                                                    self.exposure_time['std'] / 2.)
        if modified_exptime < self.exposure_time['min']:
            modified_exptime = self.current_exptime
        elif modified_exptime > self.exposure_time['max']:
            modified_exptime = self.exposure_time['max']

        measurement = dict()
        measurement['hrNum'] = self.current_hrnum
        measurement['timestamp'] = self.measurement_start.timestamp()
        measurement['utDate'] = str(self.measurement_start.date())
        measurement['utTime'] = str(self.measurement_start.time())
        measurement['exposureTime'] = modified_exptime
        measurement['airmass'] = 1.
        measurement['scanLines'] = 0
        measurement['seeingCorr'] = np.random.normal(self.avg_seeing, self.std_seeing)
        measurement['seeingCorr2'] = 0
        measurement['seeingCorr6'] = 0
        measurement['flux'] = np.random.randint(10000, 20000)
        measurement['scintLeft'] = 0
        measurement['scintRight'] = 0
        measurement['strehlLeft'] = 0
        measurement['strehlRight'] = 0
        measurement['delta'] = 0

        self.last_measurement = measurement

        return measurement

    def new_hrnum(self):
        """Generate a new target for the DIMM. This is basically a new id (hrnum) and exposure time.
        """
        self.current_hrnum = np.random.randint(0, 800)
        self.current_exptime = (np.random.random()*(self.exposure_time['max'] -
                                                    self.exposure_time['min']) +
                                self.exposure_time['min'])

    async def generate_measurements(self):
        """Coroutine to generate measurements.
        """

        start_time_hrnum = datetime.datetime.now()
        time_in_hrnum = np.random.uniform(self.time_in_target['min'], self.time_in_target['max'])*60.*60.
        self.new_hrnum()

        while True:
            if time.time() > start_time_hrnum.timestamp() + time_in_hrnum:
                start_time_hrnum = datetime.datetime.now()
                time_in_hrnum = np.random.uniform(self.time_in_target['min'], self.time_in_target['max'])
                self.new_hrnum()
            measurement = self.new_measurement()
            self.measurement_queue.append(measurement)
            await asyncio.sleep(measurement['exposureTime'])

    async def get_measurement(self):
        """Coroutine to wait and return new seeing measurements.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL Event.
        """

        while True:
            if len(self.measurement_queue) > 0:
                return self.measurement_queue.pop(0)
            else:
                await asyncio.sleep(1)
