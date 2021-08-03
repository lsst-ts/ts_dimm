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

import time
import datetime
import asyncio

from .base_dimm import BaseDIMM, DIMMStatus

import numpy as np

__all__ = ["SimDIMM"]


class SimDIMM(BaseDIMM):
    """This controller provides a simmulated DIMM interface that can be used
    for testing and mocking a real DIMM.
    """

    def __init__(self, log):
        super().__init__(log)

        self.avg_seeing = 0.5  # average seeing (arcsec)
        self.std_seeing = 0.1  # standard deviation (arcsec)
        self.chance_failure = 0.0  # chance that the dimm will fail (in 1/100)
        self.time_in_target = {"min": 2, "max": 6}  # in hours
        self.exposure_time = {"min": 2, "max": 6, "std": 5}  # in seconds

        self.measurement_loop = None
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None
        self.last_exposure_time = 0.0

        self.current_hrnum = 0
        self.current_exptime = 0

    async def setup(self, config):
        """Setup SimDim.

        Parameters
        ----------
        config : `object`
            Configuration object
        """
        self.status["status"] = DIMMStatus["INITIALIZED"]

        if config.avg_seeing < 0.0:
            raise IOError(
                "Avg seeing must be larger than zero. Got %f" % config.avg_seeing
            )
        self.avg_seeing = config.avg_seeing

        if config.std_seeing < 0.0:
            raise IOError(
                "Std seeing must be larger than zero. Got %f" % config.std_seeing
            )
        self.std_seeing = config.std_seeing

        if not (0.0 <= config.chance_failure <= 100.0):
            raise IOError("Chance of failure must be between 0 and 100.")
        self.chance_failure = config.chance_failure

        if hasattr(config, "min_time_in_target"):
            self.time_in_target["min"] = config.min_time_in_target

        if hasattr(config, "max_time_in_target"):
            self.time_in_target["max"] = config.max_time_in_target

        if hasattr(config, "min_exposure_time"):
            self.exposure_time["min"] = config.min_exposure_time

        if hasattr(config, "max_exposure_time"):
            self.exposure_time["max"] = config.max_exposure_time

        if hasattr(config, "std_exposure_time"):
            self.exposure_time["std"] = config.std_exposure_time

    async def start(self):
        """Start DIMM. Overwrites method from base class."""
        self.status["status"] = DIMMStatus["RUNNING"]
        self.measurement_loop = asyncio.create_task(self.generate_measurements())

    async def stop(self):
        """Stop DIMM. Overwrites method from base class."""
        self.measurement_loop.cancel()
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def new_measurement(self):
        """Generate a new measurement for the simulated DIMM.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic SAL
            Event.
        """
        self.measurement_start = datetime.datetime.now()

        modified_exptime = self.current_exptime + np.random.uniform(
            -self.exposure_time["std"] / 2.0, self.exposure_time["std"] / 2.0
        )
        if modified_exptime < self.exposure_time["min"]:
            modified_exptime = self.current_exptime
        elif modified_exptime > self.exposure_time["max"]:
            modified_exptime = self.exposure_time["max"]

        self.last_exposure_time = modified_exptime

        measurement = dict()
        measurement["hrNum"] = self.current_hrnum
        measurement["timestamp"] = self.measurement_start.timestamp()
        measurement["secz"] = 1.0
        measurement["fwhmx"] = np.random.normal(self.avg_seeing, self.std_seeing)
        measurement["fwhmy"] = np.random.normal(self.avg_seeing, self.std_seeing)
        measurement["fwhm"] = (measurement["fwhmx"] + measurement["fwhmy"]) / 2.0
        measurement["r0"] = np.random.normal(15.0, 5.0)
        measurement["nimg"] = 1
        measurement["dx"] = 0.0
        measurement["dy"] = 0.0
        measurement["fluxL"] = np.random.randint(10000, 20000)
        measurement["scintL"] = 0
        measurement["strehlL"] = 0
        measurement["fluxR"] = np.random.randint(10000, 20000)
        measurement["scintR"] = 0
        measurement["strehlR"] = 0
        measurement["flux"] = (measurement["fluxL"] + measurement["fluxR"]) / 2.0

        self.last_measurement = measurement

        return measurement

    async def new_hrnum(self):
        """Generate a new target for the DIMM. This is basically a new id
        (hrnum) and exposure time.
        """
        self.current_hrnum = np.random.randint(0, 800)
        delta_time = self.exposure_time["max"] - self.exposure_time["min"]
        rand = np.random.random()
        self.current_exptime = (rand * delta_time) + self.exposure_time["min"]

    async def generate_measurements(self):
        """Coroutine to generate measurements."""

        start_time_hrnum = datetime.datetime.now()
        time_in_hrnum = (
            np.random.uniform(self.time_in_target["min"], self.time_in_target["max"])
            * 60.0
            * 60.0
        )
        await self.new_hrnum()

        while True:
            if time.time() > start_time_hrnum.timestamp() + time_in_hrnum:
                start_time_hrnum = datetime.datetime.now()
                time_in_hrnum = np.random.uniform(
                    self.time_in_target["min"], self.time_in_target["max"]
                )
                await self.new_hrnum()
            measurement = await self.new_measurement()
            self.measurement_queue.append(measurement)
            await asyncio.sleep(self.last_exposure_time)

    async def get_measurement(self):
        """Coroutine to wait and return new seeing measurements.

        Returns
        -------
        measurement : dict
            A dictionary with the same values of the dimmMeasurement topic
            SAL Event.
        """

        while True:
            if len(self.measurement_queue) > 0:
                return self.measurement_queue.pop(0)
            else:
                await asyncio.sleep(1)
