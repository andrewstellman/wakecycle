"""arunner - a batch orchestrator for AI coding agents.

The generic engine ships inside this package at ``arunner.engine`` (the tick
state machine, ticker, heartbeat helper, jobs expander, in-context core, demo
worker); the lifecycle CLI is ``arunner.cli`` (console script ``arunner``). The
Claude plugin lives in ``plugins/``. ``__version__`` is the single canonical
version every surface mirrors (FR-34).
"""
__version__ = "1.1.0"
