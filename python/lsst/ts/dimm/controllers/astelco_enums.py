# This file is part of ts_dimm.
#
# Developed for the Vera C. Rubin Observatory Telescope and Site Systems.
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__all__ = [
    "TERMINATOR",
    "AmebaMode",
    "AmebaState",
    "AmebaSunAltCondition",
    "ServiceState",
    "ServiceFailState",
    "ServiceControl",
    "PowerState",
    "RainState",
    "ScopeMotionState",
    "SkyStatus",
    "VariableType",
]

import enum


# Unlike most line-based TCP/IP, which use "\r\n",
# the astelco only requires "\n".
TERMINATOR = b"\n"


class AmebaMode(enum.IntEnum):
    OFF = 0
    AUTO = 1
    MANUAL = 2


class AmebaState(enum.IntEnum):
    INACTIVE = 0
    WAITING = 1
    SLEWING = 2
    TRACKING = 3
    FOCUSING = 4
    MONITORING = 5


class AmebaSunAltCondition(enum.IntFlag):
    HUMIDITY_OK = 0x01
    WIND_OK = 0x02
    SKY_CLEAR = 0x04


class ServiceState(enum.IntEnum):
    NOT_RUNNING = 0
    RUNNING = 1


class ServiceFailState(enum.IntEnum):
    NO_FAILURE = 0
    FAILURE = 1


class ServiceControl(enum.IntEnum):
    STOP = 0
    START = 1
    RESTART = 2


class PowerState(enum.IntEnum):
    PARKED = 0
    POWERED_UP = 1


class RainState(enum.IntEnum):
    DRY = 0
    PRECIPITATION = 1


class ScopeMotionState(enum.IntEnum):
    ERROR = -2
    PARKED = -1
    STOPPED = 0
    SLEWING = 1
    TRACKING = 2


class SkyStatus(enum.IntEnum):
    CLEAR = 0
    LIGHTLY_CLOUDY = 1
    CLOUDY = 2
    PRECIPTATING = 3


class VariableType(enum.IntEnum):
    NULL = 0  # should not occur
    INT = 1
    FLOAT = 2
    STRING = 3
