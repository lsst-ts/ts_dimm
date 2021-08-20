"""Sphinx configuration file for TSSW package"""

from documenteer.conf.pipelinespkg import *  # noqa


project = "ts_dimm"
html_theme_options["logotext"] = project  # type: ignore # noqa
html_title = project
html_short_title = project

intersphinx_mapping["ts_xml"] = ("https://ts-xml.lsst.io", None)  # type: ignore # noqa
