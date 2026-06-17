"""
Pytest configuration for schwab-data-proxy tests.

Sets SCHWAB_SKIP_INIT=true before any test module is imported so the FastAPI
lifespan does not attempt to load /data/token.json in CI or clean local envs.
This must run before collection, which conftest.py guarantees.
"""

import os

os.environ.setdefault("SCHWAB_SKIP_INIT", "true")
