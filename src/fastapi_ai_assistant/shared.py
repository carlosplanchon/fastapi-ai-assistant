#!/usr/bin/env python3

import tomllib
from pathlib import Path

##################
# --- CONFIG --- #
##################
with Path("config.toml").open(mode="rb") as fp:
    CONFIG = tomllib.load(fp)
