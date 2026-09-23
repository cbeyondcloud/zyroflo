"""T465: a ledger operation that REFUSES has written nothing.

Every operation that records an audit event makes its own durable write first -
`state.create` the run folder, `state.advance` the move, `approve.record` the
approval - and appends its event after. `events.append` is where the chain is
checked, so its refusal used to arrive AFTER the write it was meant to go with:
the command said "refused" at exit 2 while the move, or the approval, stood.
Measured on main after PR #35: a run closed with no `run_closed`, a run moved to
P1, and an approval recorded AGAIN on every "refused" retry, in the append-only
ledger that unblocks gates.

The trigger is ordinary. `events.jsonl` is committed with its run, so a merge
conflict leaves exactly the unreadable line these tests plant; and a key set
twice is the commonest `profile.yml` slip there is.

A CLI's contract is a process, so every operation here is SPAWNED - under `-S`
and from a cwd outside the harness, as an adopter's plain python runs it (T463).
Each refusal has a control beside it: the same command on an intact chain
succeeds and writes. Without one, a refusal for some OTHER reason - a wrong flag
also exits 2, and also moves nothing - would pass for the one under test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.sdlc import events

ROOT = Path(__file__).resolve().parents[2]
SDLC = ROOT / "tools" / "sdlc"
#: What a merge conflict leaves in a committed `events.jsonl`.
CONFLICT = "<<<<<<< HEAD\n"
APPROVER = "reviewer@example.invalid"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "repo" / ".sdlc").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    return tmp_path / "repo"


def _tool(repo: Path, tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-S", str(SDLC / tool), "--target", str(repo), *args],
                          cwd=repo.parent / "elsewhere", stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


def _said(done: subprocess.CompletedProcess) -> str:
    return done.stdout + done.stderr


def _open(repo: Path, run_id: str) -> Path:
    done = _tool(repo, "state.py", "--new", "--title", "a change", "--id", run_id)
    assert done.returncode == 0, _said(done)
    return repo / ".sdlc" / "runs" / run_id


def _break(run: Path) -> None:
    with (run / events.EVENTS).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(CONFLICT)


def _kinds(run: Path) -> list[str]:
    return [json.loads(line)["event"]
            for line in (run / events.EVENTS).read_text(encoding="utf-8").splitlines()]


def _files(repo: Path) -> dict[str, bytes]:
    """Every file under the target, by path - what "wrote nothing" is measured against."""
    return {p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*")) if p.is_file()}


def _refused_by_the_chain(done: subprocess.CompletedProcess) -> None:
    """Refused BY the chain check - not by argparse, and not by a traceback."""
    assert done.returncode == 2, _said(done)
    assert "Traceback" not in done.stderr, done.stderr
    assert "not readable" in _said(done) or "unreadable" in _said(done), _said(done)


def test_a_run_the_profile_refuses_is_never_half_opened(repo):
    profile = repo / ".sdlc" / "profile.yml"
    profile.write_text("gate_profile: solo\ngate_profile: team\n", encoding="utf-8")
    before = _files(repo)
    done = _tool(repo, "state.py", "--new", "--title", "a change", "--id", "r1")
    assert done.returncode == 2, _said(done)
    assert "set twice" in _said(done), _said(done)
    assert _files(repo) == before, "a refused run left files behind"
    # So the SAME id opens once the profile is fixed - the retry a half-opened
    # folder used to refuse as "already exists".
    profile.write_text("gate_profile: solo\n", encoding="utf-8")
    assert _kinds(_open(repo, "r1"))[:2] == [events.RUN_OPENED, events.STEP_STARTED]


def test_a_state_move_on_a_broken_chain_moves_nothing(repo):
    control = _open(repo, "intact")
    done = _tool(repo, "state.py", "--advance", "intact", "--status", "closed")
    assert done.returncode == 0, _said(done)
    assert _kinds(control)[-1] == events.RUN_CLOSED

    _break(_open(repo, "r1"))
    before = _files(repo)
    done = _tool(repo, "state.py", "--advance", "r1", "--status", "closed")
    _refused_by_the_chain(done)
    assert _files(repo) == before, "the refused move was recorded"


def test_a_stage_move_on_a_broken_chain_moves_nothing(repo):
    """`stage.advance` - the driving tool's everyday move - saves through
    `state.advance` and then writes T462's step timing, so it inherits the
    refusal rather than carrying one of its own."""
    control = _open(repo, "intact")
    done = _tool(repo, "stage.py", "--run", "intact", "--advance", "P1", "--force",
                 "--note", "moved on")
    assert done.returncode == 0, _said(done)
    assert json.loads((control / "state.json").read_text(encoding="utf-8"))["phase"] == "P1"

    _break(_open(repo, "r1"))
    before = _files(repo)
    done = _tool(repo, "stage.py", "--run", "r1", "--advance", "P1", "--force",
                 "--note", "moved on")
    _refused_by_the_chain(done)
    assert _files(repo) == before, "the refused move was recorded"


def test_an_approval_on_a_broken_chain_is_not_recorded_even_on_retry(repo):
    """The worst of the four: the approvals ledger is what unblocks a gate, it is
    append-only, and a person told "refused" tries again - each retry used to
    record one more approval nobody could take back."""
    def approve(run_id: str) -> subprocess.CompletedProcess:
        return _tool(repo, "approve.py", "--run", run_id, "--gate", "G1",
                     "--verdict", "approve", "--approver", APPROVER)

    def at_the_gate(run_id: str) -> Path:
        run = _open(repo, run_id)
        done = _tool(repo, "stage.py", "--run", run_id, "--advance", "P2", "--force",
                     "--note", "at the gate")
        assert done.returncode == 0, _said(done)
        return run

    control = at_the_gate("intact")
    done = approve("intact")
    assert done.returncode == 0, _said(done)
    assert len((control / "approvals.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert _kinds(control)[-1] == events.GATE_DECIDED

    _break(at_the_gate("r1"))
    before = _files(repo)
    for attempt in (1, 2):
        _refused_by_the_chain(approve("r1"))
        after = _files(repo)
        assert after == before, (f"attempt {attempt} wrote "
                                 f"{sorted(k for k in after if after[k] != before.get(k))}")


def test_the_preflight_asks_the_question_append_asks(repo):
    """One predicate, asked twice: before the caller writes, and again at the
    append. The preflight names the line and says nothing was written, which is
    true only because it runs first; `append` keeps its own words."""
    run = _open(repo, "r1")
    events.require_appendable(repo, "r1")            # intact
    events.require_appendable(repo, "never-opened")  # no chain yet: nothing to break
    _break(run)                                      # after run_opened, step_started
    with pytest.raises(events.EventError, match=r"line 3 .*before anything was written"):
        events.require_appendable(repo, "r1")
    with pytest.raises(events.EventError, match="refusing to append past a break"):
        events.append(repo, "r1", events.RUN_CLOSED, "P0", status="closed")
