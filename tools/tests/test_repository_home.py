"""The repository is named where it now lives (T650, the owner's decision 38; T689, decision 59).

The repository has moved twice: on 2026-10-14 from the `sumitdevgupto` account to the
`cbeyondcloud` organisation, and on 2026-10-21 renamed to `zyroflo` at publication (T689, the
open-core split). A badge or a schema id naming a retired home points at a repository that is no
longer there, and nothing fails when it does: GitHub redirects a browser, so the drift is
invisible from a README.

Every tracked file is read, so a link pasted from an old page is caught wherever it lands. The
retired homes are built from their parts, never written whole, so this file does not name them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import schema  # noqa: E402

RETIRED_HOMES = ("/".join(("sumitdevgupto", "sdlc-harness")),
                 "/".join(("cbeyondcloud", "sdlc-harness")))
NEW_HOME = "cbeyondcloud/zyroflo"


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout
    names = [name for name in out.decode("utf-8").split("\0") if name]
    # An empty listing would make the scan below pass having looked at nothing.
    assert len(names) > 100, f"git ls-files listed {len(names)} files - nothing was looked at"
    return names


def test_no_tracked_file_names_a_retired_home():
    found = {home: [name for name in _tracked() if (ROOT / name).is_file()
                    and home.encode() in (ROOT / name).read_bytes()]
             for home in RETIRED_HOMES}
    assert not any(found.values()), (f"these tracked files name a retired home of the "
                                     f"repository; it lives at {NEW_HOME} now: {found}")


def test_the_package_metadata_names_the_new_home():
    """A published package says where it lives and under which licence (T692)."""
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["urls"]["Repository"] == f"https://github.com/{NEW_HOME}"
    assert project["urls"]["Issues"] == f"https://github.com/{NEW_HOME}/issues"
    assert project["license"] == {"text": "MIT"}
    assert project["readme"] == "README.md"


def test_the_schema_ids_and_the_badges_name_the_new_home():
    """The positive control: the places T650 named still say the new home (T693 retired the
    Actions badge), so the scan above cannot pass merely because the lines it looks for were
    deleted."""
    assert schema.BASE_ID == f"https://github.com/{NEW_HOME}/schemas"
    for name in ("events", "policy", "profile", "workspace"):
        text = (ROOT / schema.SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        assert f'"$id": "https://github.com/{NEW_HOME}/schemas/{name}.schema.json"' in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"https://img.shields.io/github/stars/{NEW_HOME}?style=social" in readme


LOCAL_CI_BADGE = ("[![CI: runs locally](https://img.shields.io/badge/CI-runs_locally-blue.svg)]"
                  "(CONTRIBUTING.md#run-ci-before-you-ask-for-a-merge)")


def test_the_ci_badge_says_ci_runs_locally():
    """CI runs on this machine (`tools/ci_local.py`), never on push (T460), so an Actions badge
    reports only the last run someone dispatched - "cancelled" at publication - and says nothing
    about main (T693). The badge says where CI runs, and links to the section that says how."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "actions/workflows/ci.yml/badge.svg" not in readme
    assert LOCAL_CI_BADGE in readme
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "\n## Run CI before you ask for a merge\n" in contributing
