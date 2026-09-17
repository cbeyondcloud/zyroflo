"""The installer's reporting and refusal layer: what a blocked install actually says.

Three defects measured by spawning the installer, all in the half of `sdlc-init.py`
an adopter reads only when something went wrong — which is the half they are certain
to act on, because it is the only thing standing between them and what they wanted.

* **The flag a refusal offers must be a flag the parser has.** Two blocked cadence
  refusals handed over `--replay` and `--publish-snapshot`; the parser's are
  `--corpus-replay` and `--snapshot-cadence`, and both printed commands answered
  `error: unrecognized arguments`, exit 2. This is the class of items 134 and 156 — a
  document handing over a command nobody can run — arriving inside a refusal. The fix
  spells each flag ONCE and the test derives the accepted set from `--help`, so a
  rename breaks this suite rather than the adopter.

* **Four causes are not one sentence.** Every blocked op rendered as
  `x BLOCKED (markers unbalanced)` while four separate sites produce that status, and
  two workflow files that DO NOT EXIST were reported as having unbalanced markers —
  contradicting the NOTE two lines below. Absent versus damaged is this project's most
  repeated distinction and the repair for one is nothing like the repair for the other.

* **An empty verifier answer is could-not-look.** `estate_verify_rows` turned an exit-0
  empty result into zero rows, so the estate preflight printed NOTHING about the
  vendored binaries. An absent binary and a mismatched digest both report correctly at
  non-zero; it is SILENCE that did not — CLAUDE.md's headline rule at the supply-chain
  preflight. The word for it is the one this repository already uses in
  `tools/field_run.py`, `tools/corpus_replay.py` and `tools/graph/validate.py`:
  *could not look*, never folded into either an all-clear or a finding.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"

#: A long flag as the adopter would copy it out of a message.
FLAG = re.compile(r"--[a-z][a-z0-9-]+")


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_reporting", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_reporting"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()


def spawn(*args: str) -> subprocess.CompletedProcess:
    """The installer as a PROCESS. An in-process call cannot tell a refusal from a
    traceback (item 117), and `unrecognized arguments` is argparse exiting, not
    returning."""
    return subprocess.run([sys.executable, str(INSTALLER), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)


@pytest.fixture(scope="module")
def parser_flags() -> set[str]:
    """Every long flag this parser accepts, read off its OWN help. Derived, never
    restated: a refusal that repeats a flag spelling is the same defect one release
    later."""
    proc = spawn("--help")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    found = set(FLAG.findall(proc.stdout))
    assert "--workspace" in found, f"--help did not parse as flags: {proc.stdout[:400]}"
    return found


@pytest.fixture
def repo(tmp_path) -> Path:
    """A target the cadence workflows would install into: a git repo with .github/."""
    root = tmp_path / "target"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return root


# ── T303 · the flag a refusal offers is one the parser accepts ─────────────────

CADENCE_REFUSALS = ("no_replay_job_reason", "no_snapshot_job_reason")


@pytest.mark.parametrize("fn", CADENCE_REFUSALS)
def test_a_cadence_refusal_names_only_flags_the_parser_accepts(fn, parser_flags,
                                                               repo):
    """Both reasons each refusal can give, against the parser's own option set."""
    reason = getattr(si, fn)(repo, None, ROOT)
    assert reason, f"{fn} gave no reason for a missing workspace"
    outside = repo.parent / "elsewhere" / "workspace.toml"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("[components]\n", encoding="utf-8")
    escaped = getattr(si, fn)(repo, outside, ROOT)
    assert escaped, f"{fn} accepted a workspace outside the checkout"
    for text in (reason, escaped):
        named = set(FLAG.findall(text))
        assert named, f"{fn} named no flag at all: {text}"
        unknown = named - parser_flags
        assert not unknown, (
            f"{fn} hands the adopter {sorted(unknown)}, which this parser does not "
            f"accept; it has {sorted(f for f in parser_flags if 'snapshot' in f or 'replay' in f)}")


def test_the_flag_a_blocked_cadence_install_prints_actually_runs(repo, parser_flags):
    """The adopter's next act: copy the flag out of the NOTE and run it. A process,
    because argparse answers an unknown flag with exit 2 and prints no plan."""
    proc = spawn("--target", str(repo), "--corpus-replay", "--snapshot-cadence",
                 "--dry-run", "--no-graph")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cadence = (si.REPLAY_WORKFLOW_REL, si.SNAPSHOT_WORKFLOW_REL)
    notes = [l for l in proc.stdout.splitlines()
             if "NOTE:" in l and any(rel in l for rel in cadence)]
    assert len(notes) == 2, f"expected both cadence NOTEs, got {notes}"
    offered = {f for note in notes for f in FLAG.findall(note)}
    assert offered, f"neither NOTE offered a flag: {notes}"
    for flag in sorted(offered):
        run = spawn(flag, "--target", str(repo), "--dry-run", "--no-graph")
        combined = run.stdout + run.stderr
        assert run.returncode != 2 and "unrecognized arguments" not in combined, (
            f"the refusal offered {flag} and the parser answered "
            f"exit {run.returncode}: {combined.strip().splitlines()[-1:]}")


@pytest.mark.parametrize("flag", [si.FLAG_SNAPSHOT_CADENCE, si.FLAG_CORPUS_REPLAY])
def test_a_cadence_refusal_offers_the_flag_that_asked_for_it(flag, repo):
    """*Accepted* and *correct* are two claims, and the weld above proves only the
    first: swap the two constants at their use sites and every offered flag is still
    one this parser has, so all of the above stays green while the snapshot refusal
    hands the adopter `--corpus-replay`. That is the original defect one turn worse —
    exit 2 is loud, whereas a flag that runs and writes a different workflow is exit 0
    and silent.

    The association is DERIVED, not restated: one cadence flag blocks exactly one
    workflow, so the NOTE the run produces belongs to the flag the run was given, and
    the only thing typed here is the input."""
    proc = spawn("--target", str(repo), flag, "--dry-run", "--no-graph")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cadence = (si.REPLAY_WORKFLOW_REL, si.SNAPSHOT_WORKFLOW_REL)
    notes = [l for l in proc.stdout.splitlines()
             if "NOTE:" in l and any(rel in l for rel in cadence)]
    assert len(notes) == 1, (
        f"one cadence flag should block exactly its own workflow, got {notes}")
    named = set(FLAG.findall(notes[0]))
    assert named == {flag}, (
        f"{flag} blocked a workflow and the NOTE offers {sorted(named)}; an adopter "
        f"who runs what it printed gets exit 0 and still no workflow: {notes[0]}")


# ── T304 · each blocked cause says what it is ─────────────────────────────────

def _blocked(ops: list[dict]) -> dict:
    blocked = [o for o in ops if o["status"] == "blocked"]
    assert len(blocked) == 1, f"expected exactly one blocked op, got {ops}"
    return blocked[0]


def test_an_unbalanced_shared_file_is_the_site_that_says_markers(repo):
    """Producing site 1 of 4: `plan_op`'s block merge. The only cause for which
    *markers unbalanced* is the truth."""
    target = repo / "CLAUDE.md"
    target.write_text(f"{si.BLOCK_BEGIN}\nmine\n{si.BLOCK_BEGIN}\n", encoding="utf-8")
    op = _blocked([si.plan_op(repo, "CLAUDE.md", "body", kind="block")])
    assert "marker" in op["cause"], op
    assert target.exists(), "this cause is a file that EXISTS and is damaged"


def test_an_unrunnable_check_generator_says_so_and_not_markers(repo, tmp_path):
    """Producing site 2 of 4: the ledger check is produced by spawning the harness's
    own emitter, and a harness without one cannot produce it."""
    empty = tmp_path / "not-a-harness"
    (empty / "tools" / "sdlc").mkdir(parents=True)
    op = _blocked(si.ledger_gate_ops(repo, empty))
    assert "marker" not in op["cause"], op
    assert not op["path"].exists(), "the check file was never written"


@pytest.mark.parametrize("fn", ["snapshot_job_ops", "replay_job_ops"])
def test_a_cadence_job_with_no_workspace_says_so_and_not_markers(fn, repo):
    """Producing sites 3 and 4 of 4: the two scheduled workflows. Each is a file that
    was NEVER WRITTEN, so *markers unbalanced* names a repair with nothing to repair."""
    op = _blocked(getattr(si, fn)(repo, None, ROOT))
    assert "marker" not in op["cause"], op
    assert not op["path"].exists(), "the workflow was never written"


def test_the_four_blocked_causes_are_four_sentences_not_one(repo, tmp_path):
    """The pin on the defect itself: one render served four producers, so all four
    printed the first one's cause."""
    (repo / "CLAUDE.md").write_text(
        f"{si.BLOCK_BEGIN}\nmine\n{si.BLOCK_BEGIN}\n", encoding="utf-8")
    empty = tmp_path / "bare"
    (empty / "tools" / "sdlc").mkdir(parents=True)
    causes = [
        _blocked([si.plan_op(repo, "CLAUDE.md", "body", kind="block")])["cause"],
        _blocked(si.ledger_gate_ops(repo, empty))["cause"],
        _blocked(si.snapshot_job_ops(repo, None, ROOT))["cause"],
        _blocked(si.replay_job_ops(repo, None, ROOT))["cause"],
    ]
    assert len(set(causes)) == 4, f"four producing sites share a cause: {causes}"


def test_a_file_that_does_not_exist_is_never_reported_as_damaged(repo):
    """The whole row, through the CLI: two workflow files that do not exist were
    reported as having unbalanced markers, contradicting the installer's own NOTE two
    lines below."""
    proc = spawn("--target", str(repo), "--corpus-replay", "--snapshot-cadence",
                 "--dry-run", "--no-graph")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in (si.REPLAY_WORKFLOW_REL, si.SNAPSHOT_WORKFLOW_REL):
        assert not (repo / rel).exists(), "precondition: the file is ABSENT"
        lines = [l for l in proc.stdout.splitlines() if rel in l]
        assert lines, f"{rel} is missing from the report entirely: {proc.stdout}"
        damaged = [l for l in lines if "marker" in l.lower()]
        assert not damaged, (
            f"{rel} does not exist and the report calls it damaged: {damaged}")
        # The mutation that survived the first pass: the cause was fixed and the NOTE
        # still told the adopter their absent workflow had been left untouched, which
        # sends them looking for a file that is not there.
        untouched = [l for l in lines if "left untouched" in l]
        assert not untouched, (
            f"{rel} was never written and the NOTE says it was left alone: {untouched}")


# ── T305 · an empty verifier answer is could-not-look ─────────────────────────

COULD_NOT_LOOK = ("could not look", "could NOT LOOK")


def _says_could_not_look(rows: list[str]) -> bool:
    text = "\n".join(rows).lower()
    return any(p.lower() in text for p in COULD_NOT_LOOK)


@pytest.mark.parametrize("code", [0, 1])
def test_a_silent_verifier_is_could_not_look_and_never_an_all_clear(monkeypatch, code):
    """CLAUDE.md's headline rule at the supply-chain preflight: an external binary
    returning nothing at exit 0 is not evidence of absence. Measured before the fix:
    zero rows, so the estate preflight printed nothing about the vendored binaries."""
    monkeypatch.setattr(si, "find_uv", lambda: "uv")
    monkeypatch.setattr(si, "run_capture", lambda cmd, cwd: (code, ""))
    rows = si.estate_verify_rows(ROOT)
    assert rows, "an empty verifier answer produced no row at all"
    assert _says_could_not_look(rows), rows
    assert any("binaries" in r for r in rows), rows
    # The mutation that survived the first pass: a row may carry the could-not-look
    # words AND an all-clear in the same breath, which is worse than either alone.
    text = "\n".join(rows).lower()
    for clear in ("verified", "all pinned", "ok rg", "no issues"):
        assert clear not in text, (
            f"a could-not-look row also claims {clear!r}, which is an all-clear: {rows}")


def test_a_verifier_that_spoke_is_not_reported_as_could_not_look(monkeypatch):
    """The control on the other side: a real answer must not acquire the new sentence,
    or could-not-look means nothing."""
    monkeypatch.setattr(si, "find_uv", lambda: "uv")
    monkeypatch.setattr(si, "run_capture",
                        lambda cmd, cwd: (0, "ok rg 14.1.0\nok fd 10.2.0\n"))
    rows = si.estate_verify_rows(ROOT)
    assert any("ok rg" in r for r in rows), rows
    assert not _says_could_not_look(rows), rows


def test_a_verifier_that_found_a_problem_still_reports_the_finding(monkeypatch):
    """The other control: a finding is not could-not-look either. This shape already
    worked and must keep working."""
    monkeypatch.setattr(si, "find_uv", lambda: "uv")
    monkeypatch.setattr(si, "run_capture",
                        lambda cmd, cwd: (1, "!! fd: pinned but MISSING\n"))
    rows = si.estate_verify_rows(ROOT)
    assert any("MISSING" in r for r in rows), rows
    assert not _says_could_not_look(rows), rows
    assert any("issue" in r.lower() for r in rows), rows


# ── T337 · a shared file that EXISTS and cannot be READ is a THIRD state ───────
# Absent (`_read` -> None) and damaged (unbalanced markers) are the two states T304
# separated. A file that exists and is not valid UTF-8 is neither: pre-fix `_read` let
# the decode RAISE and `plan_op` caught only the marker error, so the installer left as
# a UnicodeDecodeError traceback (item 117 - a CLI handing over a traceback for the one
# document class it exists to refuse) instead of a reported could-not-look row. The read
# has two doors, `block_parts` and `plan_op`, both through `_read`.

def test_an_unreadable_shared_file_is_the_site_that_says_could_not_look(repo):
    """Producing site: `plan_op` reads the existing file to merge into it. A file that
    EXISTS and cannot be decoded is could-not-look, in the word the module already uses,
    never *markers unbalanced* (there is nothing to say the markers are damaged)."""
    target = repo / "CLAUDE.md"
    target.write_bytes(b"# hi\n\xff\xfe not utf-8\n")
    op = _blocked([si.plan_op(repo, "CLAUDE.md", "body", kind="block")])
    assert op["cause"] == si.BLOCKED_UNREADABLE, op
    assert "marker" not in op["cause"], op
    assert target.exists(), "this cause is a file that EXISTS and cannot be read"


def test_the_third_blocked_state_is_neither_absent_nor_damaged(repo):
    """The three states are distinct at the producing site: absent is no op at all (None,
    the installer writes it fresh); damaged is *markers unbalanced*; unreadable is its own
    cause. A damaged file must be VALID UTF-8 or it is the third state, not the second."""
    (repo / "CLAUDE.md").write_bytes(b"\xff\xfe\x00 not decodable\n")
    unreadable = _blocked([si.plan_op(repo, "CLAUDE.md", "body", kind="block")])["cause"]
    (repo / "AGENTS.md").write_text(
        f"{si.BLOCK_BEGIN}\nmine\n{si.BLOCK_BEGIN}\n", encoding="utf-8")
    damaged = _blocked([si.plan_op(repo, "AGENTS.md", "body", kind="block")])["cause"]
    assert unreadable == si.BLOCKED_UNREADABLE, unreadable
    assert damaged == si.BLOCKED_MARKERS, damaged
    assert unreadable != damaged, (unreadable, damaged)


def test_an_unreadable_governing_file_is_reported_not_a_traceback(repo):
    """The whole row, through the CLI - a PROCESS, because an in-process call cannot tell
    a refusal from a traceback (item 117). A governing file that exists and cannot be read
    left the installer as an exit-1 UnicodeDecodeError with no blocked row and no note. It
    must exit cleanly, name the file as could-not-look, and never call an on-disk file
    *was not written* (the secondary fold: the NOTE re-derived existence with
    Path.exists(), which swallows an OSError into *was not written*)."""
    (repo / "CLAUDE.md").write_bytes(b"# ok so far\n\xff\xfe then non-utf-8\n")
    proc = spawn("--target", str(repo), "--tools", "claude", "--claude-extras",
                 "--no-graph", "--dry-run")
    combined = proc.stdout + proc.stderr
    assert "Traceback (most recent call last)" not in combined, (
        f"an unreadable governing file left a traceback: ...{combined[-600:]}")
    assert proc.returncode == 0, combined
    rows = [l for l in proc.stdout.splitlines() if "CLAUDE.md" in l]
    assert rows, f"CLAUDE.md is missing from the report entirely: {proc.stdout}"
    blocked = [l for l in rows if "BLOCKED" in l]
    assert blocked, f"the unreadable file is not reported as blocked: {rows}"
    assert any(si.BLOCKED_UNREADABLE in l for l in blocked), (
        f"the blocked row does not name the could-not-look cause: {blocked}")
    assert not any("was not written" in l for l in rows), (
        f"a file that EXISTS on disk is reported as *was not written*: {rows}")
