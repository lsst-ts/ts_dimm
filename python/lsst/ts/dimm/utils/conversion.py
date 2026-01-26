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
    "convert_to_int",
    "convert_to_float",
    "convert_dimm_measurement_data",
    "dict_to_namespace",
]

import math
import types


def convert_to_int(value):
    """Convert a value to an int, or 0 in case the conversion fails.

    Parameters
    ----------
    value: `str` or `int`
        The value to convert.

    Returns
    -------
    `int`
        The value converted to an int, or 0 in case the conversion fails.

    """
    try:
        return int(value)
    except ValueError:
        return 0


def convert_to_float(value):
    """Convert a value to a float, or math.nan in case the conversion
    fails.

    Parameters
    ----------
    value: `str` or `float`
        The value to convert.

    Returns
    -------
    `float`
        The value converted to a float, or math.nan in case the conversion
        fails.

    """
    try:
        return float(value)
    except ValueError:
        return math.nan


def convert_dimm_measurement_data(data):
    """Prepare the DIMM measurement event for sending by converting the
    DIMM measurement data values to the expected data types, or to math.nan
    if the conversion fails.

    Parameters
    ----------
    data: `dict`
        The DIMM measurement data dict.

    Returns
    -------
    converted_data: `dict`
        The DIMM measurement data with the values converted to the expected
        data types.
    """
    converted_float_data = dict(
        [
            (key, convert_to_float(data[key]))
            for key in {
                "timestamp",
                "secz",
                "fwhm",
                "fwhmx",
                "fwhmy",
                "r0",
                "dx",
                "dy",
                "flux",
                "fluxL",
                "scintL",
                "strehlL",
                "fluxR",
                "scintR",
                "strehlR",
            }
        ]
    )

    converted_int_data = dict(
        [
            (key, convert_to_int(data[key]))
            for key in {
                "hrNum",
                "nimg",
            }
        ]
    )

    converted_data = {**converted_float_data, **converted_int_data}
    return converted_data


def dict_to_namespace(d):
    """Converts a nested dict[str, Any] to type SimpleNamespace"""
    if isinstance(d, dict):
        return types.SimpleNamespace(**{k: dict_to_namespace(v) for k, v in d.items()})
    elif isinstance(d, list):
        return [dict_to_namespace(item) for item in d]
    else:
        return d
