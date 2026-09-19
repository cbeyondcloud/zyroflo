"""T433 - a hand-edited `null` in a run record must REFUSE, not traceback.

A committed `state.json` is hand-editable, so a field can be valid JSON yet be a
bare `null`. Two readers format the display columns with a `:<N` spec -
`state.py --list` (`{r['phase']:<4}`) and `status.py` (`{r['phase']:<4}`) - and
`None.__format__` raises `TypeError`, which `main`'s `except (ValueError,
RuntimeError)` cannot catch. Both doors handed the operator a bare traceback at
exit 1 for the one document class they exist to read back.

A CLI's contract is a PROCESS: an in-process `main()` call renders a crash and a
refusal identically (both are a failing test), so every case below is SPAWNED
and asserts on the exit code and the operator-visible text. Red at the pre-fix
source: each null case exits 1 with `TypeError`/`Traceback`. Green after: a
clean refusal that NAMES the null field, at a controlled non-zero exit. The
positive controls (a well-formed record, and a healthy run beside a damaged one)
render normally so the fix is a refusal, not a blanket rejection.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import state as L  # noqa: E402

STATE = ROOT / "tools" / "sdlc" / "state.py"
STATUS = ROOT / "tools" / "sdlc" / "status.py"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / ".sdlc").mkdir(parents=True)
    return tmp_path


def _null_field(target: Path, run_id: str, field: str) -> None:
    """Hand-edit a run's `state.json` so one field is a JSON `null`, the way an
    operator with an editor and a mistake would."""
    path = L.run_dir(target, run_id) / L.STATE_NAME
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc[field] = None
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")


def _spawn(script: Path, target: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--target", str(target), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)


# ── door 1: state.py --list ──────────────────────────────────────────────────


def test_state_list_refuses_a_null_phase_instead_of_a_traceback(target):
    rid = L.create(target, "requirement", "hand edited to a null phase")["run_id"]
    _null_field(target, rid, "phase")

    p = _spawn(STATE, target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out and "TypeError" not in out, out
    assert p.returncode == 2, out                       # nothing readable -> refusal
    assert rid in out, out                              # the row is not dropped
    assert "unreadable" in out.lower(), out             # named as damaged
    assert "phase" in out.lower(), out                  # the field is named


def test_state_list_refuses_a_null_status(target):
    rid = L.create(target, "requirement", "hand edited to a null status")["run_id"]
    _null_field(target, rid, "status")

    p = _spawn(STATE, target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out and "TypeError" not in out, out
    assert p.returncode == 2, out
    assert rid in out and "status" in out.lower(), out


# ── door 2: status.py ────────────────────────────────────────────────────────


def test_status_run_refuses_a_null_phase_instead_of_a_traceback(target):
    rid = L.create(target, "requirement", "hand edited to a null phase")["run_id"]
    _null_field(target, rid, "phase")

    p = _spawn(STATUS, target, "--run", rid)
    out = p.stdout + p.stderr
    assert "Traceback" not in out and "TypeError" not in out, out
    assert p.returncode != 0, out                       # a controlled refusal
    assert rid in out, out
    assert "could not be read" in out.lower() or "unreadable" in out.lower(), out
    assert "phase" in out.lower(), out                  # the field is named


def test_status_verify_flags_a_null_field_as_a_problem(target):
    rid = L.create(target, "requirement", "hand edited to a null phase")["run_id"]
    _null_field(target, rid, "phase")

    p = _spawn(STATUS, target, "--verify")
    out = p.stdout + p.stderr
    assert "Traceback" not in out and "TypeError" not in out, out
    assert p.returncode == 1, out                       # --verify: a problem exists
    assert rid in out and "phase" in out.lower(), out


# ── positive controls: a healthy record still renders at exit 0 ──────────────


def test_state_list_renders_a_well_formed_record_at_exit_zero(target):
    rid = L.create(target, "requirement", "a run that is fine")["run_id"]
    p = _spawn(STATE, target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert p.returncode == 0, out
    assert rid in out and "unreadable" not in out.lower(), out


def test_status_run_renders_a_well_formed_record_at_exit_zero(target):
    rid = L.create(target, "requirement", "a run that is fine")["run_id"]
    p = _spawn(STATUS, target, "--run", rid)
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert p.returncode == 0, out
    assert rid in out and "could not be read" not in out.lower(), out


def test_a_null_record_does_not_hide_the_healthy_run_beside_it(target):
    """The `--list` idiom (T131): one damaged record must not abort the whole
    listing, so a mix stays exit 0 with the healthy run named and the damaged
    one flagged - the operator is not told least at the moment something is
    wrong."""
    healthy = L.create(target, "requirement", "a run that is fine")["run_id"]
    broken = L.create(target, "requirement", "a run with a null phase")["run_id"]
    _null_field(target, broken, "phase")

    p = _spawn(STATE, target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out and "TypeError" not in out, out
    assert p.returncode == 0, out                       # a mix is not a total loss
    assert healthy in out, "a damaged record hid the healthy run"
    assert broken in out and "unreadable" in out.lower(), out
