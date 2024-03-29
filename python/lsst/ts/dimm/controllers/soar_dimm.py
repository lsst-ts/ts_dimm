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

import asyncio
import warnings

import sqlalchemy

from .base_dimm import BaseDIMM, DIMMStatus

__all__ = ["SOARDIMM"]


class SOARDIMM(BaseDIMM):
    """This controller provides an interface with the SOAR telescope DIMM.
    This will connect to their sql database and publish the data to LSST
    middleware.

    This controller class is still under development.
    """

    def __init__(self, log, simulate):
        if simulate:
            raise RuntimeError("This DIMM does not yet support simulation.")
        super().__init__(log, simulate)

        warnings.warn(
            "This class is still under development and will not work as expected. If "
            "instantiated, it will start a coroutine that is responsible for grabbing "
            "the DIMM data from a sql database but the loop won't do anything. The CSC "
            "will look like is running but it will not grab or publish any data."
        )

        self.uri = "mysql://user:password@host/database/"
        """The uri address to connect to the DIMM database."""
        self.table = "Pachon_seeing"
        """Name of the table to query"""
        self.check_interval = 180.0
        """The interval to wait before checking the database for new data."""

        self.engine = None

        # self.db_query = "select * from {} order by ut desc limit 1"

        self.measurement_loop = None
        self.measurement_start = None
        self.measurement_queue = []
        self.last_measurement = None

    async def setup(self, config):
        """Setup SOARDIMM.

        Parameters
        ----------
        config : `object`
            Configuration object
        """

        self.uri = config.uri
        self.check_interval = config.check_interval
        self.engine = sqlalchemy.create_engine(self.uri, pool_recycle=3600)

    async def start(self):
        """Start DIMM. Overwrites method from base class."""
        self.status["status"] = DIMMStatus["RUNNING"]
        self.measurement_loop = asyncio.create_task(self.check_db_loop())

    async def stop(self):
        """Stop DIMM. Overwrites method from base class."""
        self.measurement_loop.cancel()
        self.status["status"] = DIMMStatus["INITIALIZED"]

    async def check_db_loop(self):
        """Coroutine to check the database for new measurements."""

        while True:
            # self.measurement_queue.append(measurement)

            await asyncio.sleep(self.check_interval)

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

    async def _get_mysql(self):
        """Connect to the CTIO database and get the seeing data."""

        connection = self.engine.connect()

        result = connection.execute(self.db_query)
        row = result.fetchone()

        connection.close()
        return row
