#!/usr/bin/env python

import asyncio

from lsst.ts.dimm.dimm_csc import DIMMCSC

asyncio.run(DIMMCSC.amain(index=True))
