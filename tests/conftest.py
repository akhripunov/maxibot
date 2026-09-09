"""Shared pytest configuration for the isolated QA suite."""

import os


os.environ.setdefault("MAX_CLIENT_MODE", "stub")
os.environ.setdefault("MAX_RUN_POLLING", "false")
