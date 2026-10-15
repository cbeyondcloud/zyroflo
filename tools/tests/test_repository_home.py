"""The repository is named where it now lives (T650, the owner's decision 38).

The owner moved the repository to `github.com/cbeyondcloud/sdlc-harness` on 2026-10-14. A badge
or a schema id naming the old home points at a repository that is no longer there, and nothing
fails when it does: GitHub redirects a browser, so the drift is invisible from a README.

Every tracked file is read, so a link pasted from an old page is caught wherever it lands. The
old home is built from its parts, never written whole, so this file does not name it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import schema  # noqa: E402

OLD_HOME = "/".join(("sumitdevgupto", "sdlc-harness"))
NEW_HOME = "cbeyondcloud/sdlc-harness"


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout
    names = [name for name in out.decode("utf-8").split("\0") if name]
    # An empty listing would make the scan below pass having looked at nothing.
    assert len(names) > 100, f"git ls-files listed {len(names)} files - nothing was looked at"
    return names


def test_no_tracked_file_names_the_old_home():
    found = [name for name in _tracked()
             if (ROOT / name).is_file() and OLD_HOME.encode() in (ROOT / name).read_bytes()]
    assert not found, (f"these tracked files name the repository's old home, {OLD_HOME}; it "
                       f"lives at {NEW_HOME} now: {found}")


def test_the_schema_ids_and_the_badges_name_the_new_home():
    """The positive control: the six places T650 names say the new home, so the scan above
    cannot pass merely because the lines it looks for were deleted."""
    assert schema.BASE_ID == f"https://github.com/{NEW_HOME}/schemas"
    for name in ("events", "policy", "profile", "workspace"):
        text = (ROOT / schema.SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        assert f'"$id": "https://github.com/{NEW_HOME}/schemas/{name}.schema.json"' in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"https://github.com/{NEW_HOME}/actions/workflows/ci.yml/badge.svg" in readme
    assert f"https://img.shields.io/github/stars/{NEW_HOME}?style=social" in readme
