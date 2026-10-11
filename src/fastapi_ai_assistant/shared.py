#!/usr/bin/env python3

import os
import tomllib
from pathlib import Path

##################
# --- CONFIG --- #
##################
# config.toml in the current directory, unless FASTAPI_AI_ASSISTANT_CONFIG points elsewhere.
CONFIG_PATH = Path(os.environ.get("FASTAPI_AI_ASSISTANT_CONFIG", "config.toml"))

with CONFIG_PATH.open(mode="rb") as fp:
    CONFIG = tomllib.load(fp)
