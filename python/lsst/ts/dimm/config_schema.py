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
    title: DIMM v1
    description: Schema for DIMM configuration files
    type: object
    properties:
      controller:
        type: string
        enum:
          - sim
          - astelco
          - soar
        default: sim
    allOf:
    - if:
        properties:
          controller:
            const: sim
      then:
        properties:
          avg_seeing:
            type: number
            default: 0.5
            exclusiveMinimum: 0.0
          std_seeing:
            type: number
            default: 0.1
            exclusiveMinimum: 0.0
          chance_failure:
            type: number
            default: 0.0
            minimum: 0.0
            maximum: 1.0
          min_time_in_target:
            type: number
            default: 1.0
            minimum: 1.0
            maximum: 5.0
          max_time_in_target:
            type: number
            default: 5.0
            minimum: 5.0
            maximum: 8.0
          min_exposure_time:
            type: number
            default: 1
            minimum: 0.05
            maximum: 2.
          max_exposure_time:
            type: number
            default: 3
            minimum: 3.
            maximum: 5.
          std_exposure_time:
            type: number
            default: 0.1
            minimum: 0.1
            maximum: 0.5
    - if:
        properties:
          controller:
            const: astelco
      then:
        properties:
          host:
            type: string
            default: 127.0.0.1
          port:
            type: number
            default: 65432
          auto_auth:
            type: boolean
            default: false
          user:
            type: string
            default: admin
          password:
            type: string
            default: admin
    """
)
