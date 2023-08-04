.. _version_history:Version_History:

===============
Version History
===============

v0.9.1
------

* Fix measurement expiration timestamp.

v0.9.0
------

* Replace WeatherStation CSC with ESS:301.
* Modernize CI build script.
* Update to support ts-pre-commit.

v0.8.3
------

* Update pre-commit configuration.
* Run isort in the entire package.
* In ``tests/test_mock_astelco_dimm.py``, remove unnecessary log setup.
* In ``tests/data/config/_init.yaml``, add ``measurement_validity``.
* In ``tests/test_csc.py``:

  * Remove unnecessary logging setup (just run ``pytest --log-cli-level DEBUG``).
  * Add check for dimmMeasurement expiration time.

* In ``dimm_csc.py``, implement backward compatible change to set expiration date information of dimmMeasurement topic.
* In ``pyproject.toml``, add ``isort`` configuration.
* In ``config_schema.py``:

  * Add ``measurement_validity`` configuration parameter.
  * Make ``sal_index``, ``measurement_validity`` and ``controller`` required parameters.


v0.8.2
------

* `AstelcoDIMM`:

  * In ``status_loop`` only execute handler and check for command completion if command is not ``None``.
  * Get Ra/Dec from the AMEBA module instead of the SCOPE module.

Requires:

* ts_salobj 7
* ts_idl 3.2
* ts_tcpip
* IDL file for DIMM from ts_xml 12

v0.8.1
------

* `AstelcoDIMM`:

    * Improve handling of invalid values returned by the GET command.
    * ``status_loop`` was running far too often, needlessly stressing the DIMM.
    * Fix a memory leak due to accumulation of `AstelcoCommand` instances in ``running_commands``
      (a leak made far worse by ``status_loop`` running far too often).
    
* ``Jenkinsfile``:

    * Pull in missing ts_tcpip package.
    * Modernize the format.

Requires:

* ts_salobj 7
* ts_idl 3.2
* ts_tcpip
* IDL file for DIMM from ts_xml 12

v0.8.0
------

* Add a ``simulate`` constructor argument to `BaseDIMM` and subclasses.
* Add `MockAstelcoDIMM`
* `AstelcoDIMM`:

  * Manage a `MockAstelcoDIMM` in simulation mode.
  * Overhaul command execution and reply parsing.
  * Do not return a measurement until a new one has been taken.

* Modernize the documentation and include Astelco communication manuals.

Requires:

* ts_salobj 7
* ts_idl 3.2
* ts_tcpip
* IDL file for DIMM from ts_xml 12

v0.7.0
------

* Switch to pyproject.toml.

Requires:

* ts_salobj 7
* ts_idl 3.2
* IDL file for DIMM from ts_xml 12

v0.6.1
------

* Add stubs for new commands

v0.6.0
------
* Prepare for salobj 7.

Requires:

* ts_salobj 7
* ts_idl 3.2
* IDL file for DIMM from ts_xml 11

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
