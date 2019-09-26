from setuptools import setup, find_namespace_packages

install_requires = []
tests_requires = ["pytest", "pytest-flake8"]
dev_requires = install_requires + tests_requires + ["documenteer[pipelines]"]

setup(
    name="ts_dimm",
    description="Installs python code for ts_dimm.",
    setup_requires=["setuptools_scm"],
    package_dir={"": "python"},
    packages=find_namespace_packages(where="python"),
    scripts=["bin/dimm_csc.py"],
    tests_require=tests_requires,
    extras_require={"dev": dev_requires},
    license="GPL"
)
