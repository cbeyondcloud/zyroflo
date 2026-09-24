"""T471 - a profile dial means one thing to every reader.

`approve.signing_mode` lowercased `signing:` to validate it, but the mode actually
applied came from `policy.profile_dials`, which kept the raw text - and `effective`
and `tighten_only` keep only values already in their mode tuples. So `signing:
DSSE` fell to `none` and recorded unsigned approvals at exit 0 (found by the T468
review, re-run by the lead), and `gate_profile: Regulated` fell back to the
policy floor's regime the same way. The dials are now normalised once, in
`profile_dials`, and a value that is none of the modes is refused by name.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402


def _repo(tmp_path: Path, name: str, profile: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text("stack: python\n" + profile, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    return root


def _approve(root: Path) -> subprocess.CompletedProcess:
    """G2, recorded by a person - with no signing key, so a run that must sign
    cannot, and says so."""
    run = L.create(root, "requirement", "a change worth gating")["run_id"]
    R.park(root, run, "P3")
    env = {k: v for k, v in os.environ.items() if k != A.SIGNING_KEY_ENV}
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "approve.py"), "--target", str(root),
         "--run", run, "--gate", "G2", "--verdict", "approve",
         "--approver", "dev@example.invalid"],
        capture_output=True, text=True, timeout=300, env=env)


@pytest.mark.parametrize("spelling", ["dsse", "DSSE", "Dsse"])
def test_every_spelling_of_dsse_is_held_to_signing(tmp_path, spelling):
    done = _approve(_repo(tmp_path, "sign", f"gate_profile: solo\nsigning: {spelling}\n"))
    said = done.stdout + done.stderr
    assert done.returncode == 2, f"signing: {spelling} recorded an approval unsigned\n{said}"
    assert "signing: dsse" in said and "cannot be honoured" in said, said


def test_a_capitalised_regime_is_the_regime_it_names(tmp_path):
    """`regulated` requires dsse; read as unknown it fell back to the floor's
    `solo`, which requires nothing, and the approval stood unsigned."""
    root = _repo(tmp_path, "regime", "gate_profile: Regulated\n")
    assert P.profile_dials(root)["gate_profile"] == "regulated"
    done = _approve(root)
    said = done.stdout + done.stderr
    assert done.returncode == 2 and "gate_profile: regulated requires signing: dsse" in said, said


def test_the_suite_enforces_the_coverage_dial_the_floor_judged(tmp_path):
    """`suite.enforcement` read the raw text too: `HARD` passed the floor as hard
    and was then ENFORCED as the floor's advisory."""
    from tools.sdlc import suite
    assert suite.enforcement(_repo(tmp_path, "cov", "coverage_enforcement: HARD\n")) == "hard"


@pytest.mark.parametrize("key, value", [("signing", "dse"), ("gate_profile", "strict"),
                                        ("coverage_enforcement", "strict")])
def test_a_dial_that_is_no_mode_is_refused_by_name(tmp_path, key, value):
    """Read as none of its modes it would fall back to the floor without a word -
    the permissive direction, for a typo."""
    base = "" if key == "gate_profile" else "gate_profile: solo\n"
    root = _repo(tmp_path, key, f"{base}{key}: {value}\n")
    with pytest.raises(P.ProfileError, match=rf"{key}: '{value}' is not one of"):
        P.profile_dials(root)
    done = _approve(root)
    assert done.returncode == 2 and f"{key}: '{value}'" in done.stdout + done.stderr, \
        done.stdout + done.stderr
