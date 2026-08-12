"""`tools/requirements.txt` must pin exactly what `uv.lock` resolved.

The file exists for environments without uv, and its whole value is being the same
environment the suite was run against. A copy that drifts is **worse than no file at all**:
it installs versions nobody tested, silently, for exactly the people who have no lock to
fall back on.

Its header has said so since item 68. It drifted anyway. Found during item 88, and not by
looking: a regeneration run for an unrelated dependency change moved `tree-sitter` from
`0.26.0` to `0.25.2`, which read at first like the new dependency forcing a downgrade. It was
not. **The lock had said `0.25.2` all along** — the committed export was stale, and had been
handing anyone who used it a parser version this repository has never tested with.

Nothing detected that for as long as the only check was a human remembering to re-run a
command whose output nobody diffs. So the check is mechanical, reads the lock directly rather
than shelling out to `uv` (which the drop-in tier must never require), and names the exact
line to fix.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "uv.lock"
REQS = ROOT / "tools" / "requirements.txt"

#: `name==version` at the start of a line; continuation lines are uv's `# via` comments.
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(.+?)\s*$")


def _normalise(name: str) -> str:
    """PEP 503 name normalisation — the lock and the export disagree on `_` versus `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked() -> dict[str, str]:
    data = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {_normalise(p["name"]): p["version"]
            for p in data.get("package", []) if "version" in p}


def _pinned() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in REQS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if m := _PIN.match(line):
            out[_normalise(m.group(1))] = m.group(2)
    return out


def test_the_export_is_not_empty():
    """The guard on the guard: every assertion below passes vacuously against an empty file,
    so a regeneration that produced nothing would turn this green by having no subject."""
    pinned = _pinned()
    assert len(pinned) >= 10, (
        f"only {len(pinned)} pin(s) parsed out of {REQS.name}. Either the export is broken "
        f"or its format changed and this test can no longer read it — both are failures.")


def test_every_pin_matches_the_version_the_lock_resolved():
    """The drift item 88 found, as a test. A mismatch means someone edited this file, or
    changed a dependency without regenerating it, and the people who cannot use the lock
    would silently get the version nobody ran the suite against."""
    locked, mismatched, unknown = _locked(), [], []
    for name, version in sorted(_pinned().items()):
        if name not in locked:
            unknown.append(name)
        elif locked[name] != version:
            mismatched.append(f"{name}: requirements.txt says {version}, "
                              f"uv.lock resolved {locked[name]}")
    assert not mismatched and not unknown, (
        "tools/requirements.txt has drifted from uv.lock:\n  "
        + "\n  ".join(mismatched + [f"{n}: pinned but absent from the lock" for n in unknown])
        + f"\nRegenerate it with the command in {REQS.name}'s header, then restore that "
          f"header — `uv export` overwrites it.")


def test_the_generated_header_survived_the_last_regeneration():
    """`uv export` writes its own two-line header over the one that explains what this file
    is and why it must not be hand-edited. Losing it is how the next drift goes unexplained,
    so the documented procedure's own side effect is checked rather than remembered."""
    head = REQS.read_text(encoding="utf-8")[:2000]
    assert "GENERATED — do not hand-edit" in head, (
        "the explanatory header is gone — `uv export` overwrote it. Put it back; the command "
        "in the header says so, and this test exists because that instruction is easy to miss.")


def test_no_runtime_dependency_is_needed_to_check_this():
    """This reads `uv.lock` with the standard library. A check that required `uv` could not
    run on the drop-in tier, which is precisely the tier this file serves."""
    assert "tomllib" in sys.modules or sys.version_info >= (3, 11)
    assert LOCK.exists() and REQS.exists()
