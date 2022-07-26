####
DIMM
####

.. image:: https://img.shields.io/badge/SAL-API-gray.svg
    :target: https://ts-xml.lsst.io/sal_interfaces/DIMM.html
.. image:: https://img.shields.io/badge/GitHub-gray.svg
    :target: https://github.com/lsst-ts/ts_dimm
.. image:: https://img.shields.io/badge/Jira-gray.svg
    :target: https://jira.lsstcorp.org/issues/?jql=labels+%3D+ts_dimm
.. image:: https://img.shields.io/badge/Jenkins-gray.svg
    :target: https://tssw-ci.lsst.org/job/LSST_Telescope-and-Site/job/ts_dimm/

Overview
========

CSC to control and read seeing data from Differential Image Motion Monitor (DIMM) systems at and near Vera C. Rubin Observatory.

The DIMM CSC controls and reads seeing data from two Astelco DIMMs at Rubin C. Observatory.
The Astelco DIMM systems run autonomously.
Each Astelco DIMM consists of a 12 inch Ritchey-Chrétien telescope taking images of target stars at a specific cadence.
The aperture of the DIMM telescope consists of a mask with a hole on one side and a prism on the other side.
Due to turbulence in the atmosphere, the wave fronts arriving at the two holes slightly differ, allowing for seeing measurements.

The DIMM CSC can also read seeing data from a DIMM at SOAR, which is a completely different system than the Astelco DIMMs.

.. _lsst.ts.dimm.user_guide:

User Guide
==========

Start the DIMM CSC as follows:

.. prompt:: bash

    python dimm_csc

Stop the DIMM CSC by sending it to the OFFLINE state.

See `DIMM SAL communication interface <https://ts-xml.lsst.io/sal_interfaces/DIMM.html>`_ for commands, events and telemetry.

Configuration
-------------

Configuration is defined by `this schema <https://github.com/lsst-ts/ts_dimm/blob/master/python/lsst/ts/dimm/config_schema.py>`_.

Configuration files are located in the `ts_config_ocs repo <https://github.com/lsst-ts/ts_config_ocs>`_.

Simulator
---------

The DIMM CSC includes a simplistic simulation mode, for testing purpose.
To run the DIMM CSC in simulation mode:

.. prompt:: bash

    python dimm_csc --simulate

Developer Guide
===============

Documentation focused on the classes used, API's, and how to participate to the development of the DIMM software packages.
The Developer Guide also includes links to documentation for the Astelco DIMM.

.. toctree::
    developer_guide
    :maxdepth: 1

Version History
===============
The version history of the DIMM is found at the following link.

.. toctree::
    version_history
    :maxdepth: 1
