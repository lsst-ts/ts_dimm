.. _version_history:Version_History:

===============
Version History
===============
v0.6.1

* Add stubs for new commands

v0.6.0
------
* Prepare for salobj 7.

Requires:

* ts_salobj 7
* ts_idl 3.2
* IDL file for DIMM from ts_xml 11

v0.5.3
------
* Added documentation.

Requires:

* ts_salobj 6.3
* ts_idl 3.2
* IDL file for DIMM from ts_xml 9.1

v0.5.2
------
* Fixed an if statement so now the Astelco status is set correctly.

Requires:

* ts_salobj 6.3
* ts_idl 3.2
* IDL file for DIMM from ts_xml 9.1

v0.5.1
------
* Reverted several changes where ``controller`` was replaced with ``type``.
* Incorporated name change for Environment to WeatherStation.
  Also added support for the WeatherStation ``weather`` telemetry.
* Updated setup.cfgto the latest version.
* Corrected black and flake8 errors.
* Implement several fixes to dimm so it can work with the most recent version of the vendor controller.
  The DIMM controller is no longer publishing the data as it used to, so we have to rely on pooling to get the information.
  Also implemented several fixes to allow the CSC to capture failure conditions and close as needed.
* Implemented using Jenkins Shared Library.
* Migrated to salobj 6.3.
* Refactored the DIMM code and made sure that asyncio is used everywhere.
* Going to FAULT state if connection to the DIMM hardware fails.
* Ignoring bad data published by DIMM.
* Added initialization of the AstelcoCommands.
* Added handling of bad data before sending telemetry via DDS.
* Only sending dimmMeasurement telemetry now if the DIMM service is running.

Requires:

* ts_salobj 6.3

v0.5.0
------
* Administrative tag because v0.5.1.alpha.1 was already tagged.

v0.4.0
------
* Administrative tag because v0.5.1.alpha.1 was already tagged.

v0.3.0
------
* Added CLI build.
* Removed many f-types that broke the unit tests.
* Other minor code improvements.
* Migrated to salobj 6.

Requires:

* ts_salobj 6

v0.2.0
------
* Upgrade DIMM CSC to salobj 4 and make it a configurable CSC.
* Fix issue when loading controller in non-simulation mode.

Requires:

* ts_salobj 4

v0.1.1
------
* Added some modifications to account for latest interface provided by vendor.

v0.1.0
------
* Finished implementation of the basic DIMM CSC functionality.
* Added soar_dimm a controller interface to grab data from the SOAR DIMM database.
  The controller is still under development, we will access whether or not to finish it in the future.
* Adds more information regarding the state of SOAR dimm.
* Initial version of the AstelcoDIMM controller.
  This controller still needs some debugging but most of the communication functionality is implemented.
  Did not included any test of the interface, this will need to be done at a later stage specially because the interface itself is still very raw and will evolve considerably in the near term.
  This is mostly to test the communication and general functionality.
