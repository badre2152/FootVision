"""
config/ — project configuration package.

Historically this was a single top-level `config.py`. It now lives here as
`config/scoring_config.py` (see docs/decisions/ for the historical rationale
notes that used to live inline as comments in this file).

This __init__ re-exports everything from scoring_config so existing code
(`import config`, `config.SOME_WEIGHT`, `from config import SOME_WEIGHT`)
keeps working unchanged — this was a pure file-location move, no renamed
symbols, no behavior change.
"""
from .scoring_config import *  # noqa: F401,F403
from . import scoring_config  # noqa: F401
