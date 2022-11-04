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

__all__ = ["CONFIG_SCHEMA"]

import yaml

CONFIG_SCHEMA = yaml.safe_load(
    """
$schema: http://json-schema.org/draft-07/schema#
$id: https://github.com/lsst-ts/ts_dimm/blob/master/python/lsst/ts/dimm/config_schema.py
title: DIMM v3
description: Schema for DIMM configuration files
type: object
properties:
    instances:
      type: array
      description: Configuration for each DIMM instance.
      minItem: 1
      items:
        type: object
        properties:
          sal_index:
            type: integer
            description: SAL index of the DIMM instance.
            minimum: 1
          measurement_validity:
            type: number
            description: >-
              Specify how long does the DIMM measurements are valid for, in seconds.
          controller:
            type: string
            enum:
              - sim
              - astelco
              - soar
          config:
            description: Configuration for the DIMM model.
            type: object
        required:
          - sal_index
          - measurement_validity
          - controller
"""
)
