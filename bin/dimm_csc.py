#!/usr/bin/env python

import asyncio
import argparse

from lsst.ts.dimm import dimm_csc, version

parser = argparse.ArgumentParser("Start the DIMM CSC")
parser.add_argument("--version", action="version", version=version.__version__)
parser.add_argument("-v", "--verbose", dest="verbose", action='count', default=0,
                    help="Set the verbosity for console logging.")
parser.add_argument("index", help="ScriptQueue CSC.", default=1, type=int)

args = parser.parse_args()

csc = dimm_csc.DIMMCSC(index=args.index)
asyncio.get_event_loop().run_until_complete(csc.done_task)
