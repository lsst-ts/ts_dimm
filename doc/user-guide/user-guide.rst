.. |CSC_developer| replace::  *Wouter van Reeven <wvanreeven@lsst.org>*
.. |CSC_product_owner| replace:: *Brian Stalder <bstalder@lsst.org>*

.. _User_Guide:

###############
DIMM User Guide
###############

.. Update links and labels below
.. image:: https://img.shields.io/badge/GitHub-ts_dimm-green.svg
    :target: https://github.com/lsst-ts/ts_dimm
.. image:: https://img.shields.io/badge/Jenkins-ts_dimm-green.svg
    :target: https://tssw-ci.lsst.org/job/LSST_Telescope-and-Site/job/ts_dimm/
.. image:: https://img.shields.io/badge/Jira-ts_dimm-green.svg
    :target: https://jira.lsstcorp.org/issues/?jql=labels+%3D+ts_dimm
.. image:: https://img.shields.io/badge/ts_xml-DIMM-green.svg
    :target: https://ts-xml.lsst.io/sal_interfaces/DIMM.html


XML location can be found at the top of the :doc:`top of this page </index>`.

The Astelco DIMM consists of a 12 inch Ritchey-Chrétien telescope taking images of target stars at a specific cadence.
The aperture of the DIMM telescope consists of a mask with a hole on one side and a prism on the other side.
Due to turbulence in the atmosphere, the wave fronts arriving at the two holes slightly differ, allowing for seeing measurements.

DIMM Interface
==============

The DIMM hardware runs autonomously.
The DIMM CSC interacts with the hardware via the OpenTPL protocol and collects the DIMM telemetry.
