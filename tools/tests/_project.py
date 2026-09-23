"""A filled `sdlc-project.toml`, for tests that install into a scratch repository.

T442 made INIT stop-and-fill: with no project metadata it writes the template,
names the blanks, and exits 2 before installing anything. A test that installs
to exercise something ELSE seeds this first - one helper, so the fixture's shape
changes in one place when the metadata does.
"""
from __future__ import annotations

from pathlib import Path

FILLED = """schema_version = 1

[project]
name = "fixture"
description = "A scratch repository a test installs into"
owner = "test-suite"
criticality = "low"
"""

#: The same table, for a workspace TOML (appended after its top-level keys).
WORKSPACE_TABLE = "\n" + FILLED.split("\n", 2)[2]

#: What `project_gate` returns for FILLED - the dict `read_project` builds.
PROJECT = {"name": "fixture",
           "description": "A scratch repository a test installs into",
           "owner": "test-suite", "criticality": "low", "default_branch": "",
           "issue_tracker": "", "compliance": []}


def filled_gate(si):
    """For a test that drives `install()` IN-PROCESS to exercise something else:
    this module instance's gate answers as though FILLED were on disk. The gate
    itself is proven by `test_project_metadata.py`, spawned; this only keeps a
    fan-out or a chain test from being about the metadata. Returns `si`."""
    si.project_gate = lambda root, workspace, dry_run: (dict(PROJECT), 0)
    return si


def seed(root: Path) -> Path:
    """Write a complete `sdlc-project.toml` at `root` unless one is already
    there (a test that wrote its own keeps it); returns `root`."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sdlc-project.toml"
    if not path.exists():
        path.write_text(FILLED, encoding="utf-8")
    return root
