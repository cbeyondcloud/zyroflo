"""The driver: unattended execution BETWEEN the human gates. Item 106.

What is worth testing here is almost entirely what the driver REFUSES to do.
It adds no vocabulary — `phases[]`, `gate_profile`, `phase_runners:` and
`stage.verify` already define the pipeline — so its whole contribution is
executing that definition and stopping everywhere a human is meant to be.

**No runner is ever really spawned.** Every test injects a fake `run`, because
the claim under test is the LOOP's behaviour, and a suite that spent money to
check a control flow would be its own counter-argument.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools import provider_smoke as smoke  # noqa: E402

#: A command that RESOLVES. Item 149 made `--plan` refuse a plan whose
#: runner could not start, and a fixture naming `fake-writer` was
#: modelling a broken profile by accident. Built from `sys.executable`
#: rather than written down, so no machine path reaches a tracked file.
RUNNER_CMD = '"' + sys.executable + '" -c pass'


#: solo: G2 and G4 halt, G1/G3 do not — so a walk from P0 reaches P3's gate.
PROFILE = (
    "stack: python\ngate_profile: solo\n"
    "phases: [intake, requirements, design, implementation, test, review, "
    "triage, closeout]\n"
    'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n'
    "runners:\n"
    "  - name: writer\n"
    f'    command: \'{RUNNER_CMD}\'\n'
    "    capabilities: chat\n"
    "  - name: builder\n"
    f'    command: \'{RUNNER_CMD}\'\n'
    "    capabilities: tools\n"
    "phase_runners:\n"
    "  intake: writer\n"
    "  requirements: writer\n"
    "  design: writer\n")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    return r


@pytest.fixture
def run_id(repo: Path) -> str:
    rec = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                              "## What\n\nA small change.\n")
    return rec["run_id"]


def answering(text: str = "the phase's document, written by a model\n",
              code: int = 0):
    """A fake runner that answers. Records what it was handed."""
    seen: list[dict] = []

    def run(command, prompt, cwd, timeout=3600):
        seen.append({"command": command, "prompt": prompt, "cwd": cwd})
        return code, text

    run.seen = seen                                    # type: ignore[attr-defined]
    return run


# ── it executes the definition the profile already states ───────────────────


def test_a_routed_phase_runs_and_the_run_advances(repo, run_id):
    run = answering()
    found = D.step(repo, run_id, {}, run=run)
    assert found["stop"] == ""
    assert found["runner"] == "writer"
    assert found["advanced_to"] == "P1"
    assert ledger.load(repo, run_id)["phase"] == "P1"


def test_the_runner_gets_the_contract_shape(repo, run_id):
    """RUNNER_CONTRACT section 1: the prompt is the final argument and the cwd
    is the target. The driver spawns through one function so this holds for
    every phase."""
    run = answering()
    D.step(repo, run_id, {}, run=run)
    (call,) = run.seen
    assert call["command"] == RUNNER_CMD
    assert call["cwd"] == repo
    assert "P0" in call["prompt"] and str(repo) in call["prompt"]


def test_the_walk_stops_at_the_first_hard_gate(repo, run_id):
    """`solo` halts at G2, which stands at the end of P3. The walk runs P0, P1
    and P3 — skipping P2, which this profile does not activate — and stops
    where a human is meant to be."""
    steps = D.drive(repo, run_id, {}, run=answering())
    ran = [s["phase"] for s in steps]
    assert ran == ["P0", "P1", "P3"], ran
    assert steps[-1]["stop"] == D.GATE
    assert "G2" in steps[-1]["why"]
    assert ledger.load(repo, run_id)["phase"] == "P3", "it moved past its gate"


def test_a_taken_gate_lets_the_walk_continue(repo, run_id):
    """The gate is a stop, not a wall: once a human approves, re-invoking
    continues — `state.json` is the state, so the loop is resumable."""
    D.drive(repo, run_id, {}, run=answering())
    assert ledger.load(repo, run_id)["phase"] == "P3"
    A.record(repo, run_id, "G2", "approve", "a.person@example.invalid",
             notes="looks right")
    found = D.step(repo, run_id, {}, run=answering())
    assert found["phase"] == "P3"
    assert found["stop"] == "", found.get("why")
    assert found["advanced_to"] == "P4", "the approved gate did not release it"


# ── every stop is a different human act, and they are not one word ──────────


def test_an_unrouted_phase_stops_rather_than_guessing(repo, run_id):
    """Unmapped means whoever is driving drives — and the driver is not a
    whoever. It never picks a runner the profile did not name.

    No values are supplied here, and that is not laziness: `stage.resolve`
    REFUSES a value the phase does not name, by the same rule that refuses a
    `run`-sourced one — a supplied value nothing reads is the same defect as a
    value nobody supplies. The first draft passed `component`/`branch` into
    P3 and got that refusal instead of the stop it meant to test."""
    D.drive(repo, run_id, {}, run=answering())
    A.record(repo, run_id, "G2", "approve", "a@b.invalid", notes="ok")
    D.step(repo, run_id, {}, run=answering())              # P3 -> P4
    assert ledger.load(repo, run_id)["phase"] == "P4"
    found = D.step(repo, run_id, {}, run=answering())
    assert found["stop"] == D.UNROUTED
    assert "not a whoever" in found["why"]


def test_a_routing_refusal_stops_before_anything_is_spawned(repo, tmp_path, run_id):
    """A chat runner on an acting phase is item 105's refusal. The driver must
    surface it BEFORE spending, so the order of checks is the design."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE + "  implementation: writer\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    ledger.advance(repo, run_id, phase="P4", note="jump for the test")
    run = answering()
    found = D.step(repo, run_id, {"component": "repo", "branch": "b"}, run=run)
    assert found["stop"] == D.REFUSED
    assert "cannot act" in found["why"]
    assert run.seen == [], "it spawned a runner it had already refused"


def test_a_missing_caller_value_stops_and_names_it(repo, run_id):
    """P4 needs a component and a branch only a caller knows. A blank line
    would produce a runner that cannot finish while behaving perfectly, which
    RUNNER_CONTRACT section 9 records — so it is a stop, by name."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE + "  implementation: builder\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    ledger.advance(repo, run_id, phase="P4", note="jump")
    run = answering()
    found = D.step(repo, run_id, {}, run=run)
    assert found["stop"] == D.NEEDS_VALUE
    assert "component" in found["why"]
    assert run.seen == []


def test_a_runner_that_fails_stops_with_its_transcript(repo, run_id):
    found = D.step(repo, run_id, {}, run=answering("it broke\n", code=3))
    assert found["stop"] == D.RAN_BADLY
    assert found["exit_code"] == 3
    assert Path(found["transcript"]).read_text(encoding="utf-8") == "it broke\n"


def test_a_phase_that_still_owes_something_stops(repo, run_id):
    """A `tools` runner is expected to write its own artifacts. When it writes
    nothing, the phase owes its document and the walk stops — the driver does
    not paper over an acting runner's silence.

    Tested at P1, deliberately: P0's `00-intake.md` is written by
    `intake.open_run` itself, so it is the one phase that can never owe its
    document — which is what the first draft of this test got wrong."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE.replace("  requirements: writer",
                                 "  requirements: builder"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    D.step(repo, run_id, {}, run=answering())              # P0 -> P1
    found = D.step(repo, run_id, {}, run=answering())
    assert found["phase"] == "P1"
    assert found["stop"] == D.OWED
    assert "10-requirements.md" in found["why"]


def _spawn_drive(repo: Path, run_id: str, spend: str,
                 *sets: str) -> subprocess.CompletedProcess:
    """The drive door as a PERSON meets it — a real process, its exit code and
    the text it printed. `drive.py` routes runners, so this is safe to spawn
    only because the profile points every routed runner at `python -c pass`
    (RUNNER_CMD): nothing is spent. Calling `D.main` in-process would read the
    same record back as a dict and could not tell a printed refusal from a
    traceback (CLAUDE.md: a CLI's contract is a process). `sets` are `--set
    KEY=VALUE` pairs a phase's prompt requires (P4 needs component and branch)."""
    args = [sys.executable, str(ROOT / "tools" / "sdlc" / "drive.py"),
            "--target", str(repo), "--run", run_id, "--spend", spend]
    for pair in sets:
        args += ["--set", pair]
    return subprocess.run(args, capture_output=True, text=True, timeout=300)


def _record_then_delete(repo: Path, run_id: str, rel: str, body: str) -> str:
    """Write, PIN (item 139 keeps the bytes) and then remove `rel` — the state a
    re-run meets when a pinned document was deleted out from under it: recorded,
    with kept bytes, and gone from the run folder. Returns the digest it was
    pinned under, so a test can DERIVE the exact restore command rather than
    typing a second copy of it."""
    rd = ledger.run_dir(repo, run_id)
    (rd / rel).write_text(body, encoding="utf-8")
    ledger.record_artifact(repo, run_id, rel)
    digest = ledger.load(repo, run_id)["artifacts"][rel]
    (rd / rel).unlink()
    return digest


def test_a_recorded_produces_artifact_gone_missing_is_shown_its_restore_at_the_door(
        repo, run_id):
    """**T392.** A `produces` document that was pinned and then went missing is
    reported by BOTH `stage.verify` (it is not in the run folder) and
    `stage.stale` (its recorded bytes moved). The driver asks `verify` first
    (`drive.py:540`) and returns before it asks `stale` (`drive.py:547`), so the
    person met `verify`'s sentence — and only `stale`'s carried the followable
    `--restore`. Measured at the real door, spawned, so this asserts what a
    person READS rather than what a function returns; the routed runner is
    `python -c pass`, so nothing is spent.

    `design` is routed to an ACTING (`tools`) runner on purpose: the chat glue
    (`drive.py:530`) rewrites a missing document for a `chat` phase, so verify's
    missing-artifact branch is only reachable through the driver on a `tools`
    route."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE.replace("  design: writer", "  design: builder"),
                 encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    D.drive(repo, run_id, {}, run=answering())            # walk to P3 (builder owes)
    assert ledger.load(repo, run_id)["phase"] == "P3"

    digest = _record_then_delete(repo, run_id, "30-design.md",
                                 "# Design\n\nreal design bytes\n")
    followable = ledger.restore_command(run_id, "30-design.md", digest)

    proc = _spawn_drive(repo, run_id, "writer,builder")
    seen = proc.stdout + proc.stderr
    assert proc.returncode == 1, seen                     # OWED is a thing to FIX
    assert "OWED" in seen, seen
    # Pin the PROSE and the remedy SEPARATELY. `--artifact 30-design.md` sits
    # inside the remedy's own argument, so a bare `"30-design.md" in seen` is
    # satisfied by the remedy alone, and a mutant that drops the name from the
    # prose survives it. This substring is the prose and nothing but the prose.
    assert "owes 30-design.md, which is not in the run folder" in seen, seen
    assert followable in seen, (
        "the door named the missing artifact but not the --restore that clears "
        f"it: verify's remedy-less sentence shadowed stale's.\n{seen}")


def _p4_dir_recorded_then_deleted(repo: Path, run_id: str, key: str) -> None:
    """Jump the run to P4, create the `40-impl/` directory with a file, PIN it
    under `key` — the spelling the record happens to hold — and delete it. The
    record legitimately carries a directory under BOTH `40-impl` and `40-impl/`
    (`artifact_key` keeps a trailing slash a caller typed, and old records are
    not rewritten), which is the whole of what T392 round 2 is about."""
    ledger.advance(repo, run_id, phase="P4", note="to implementation")
    d = ledger.run_dir(repo, run_id) / "40-impl"
    d.mkdir(parents=True, exist_ok=True)
    (d / "patch.diff").write_text("diff --git a b\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, key)
    shutil.rmtree(d)


def test_a_recorded_produces_directory_is_shown_its_remedy_under_either_spelling(
        repo, run_id):
    """**T392 round 2.** `verify`'s remedy guard matched the RAW relative path,
    but a directory artifact (`40-impl/`) is carried in the record under both
    spellings. Recorded with the slash the person got the remedy; recorded
    stripped they got the bare sentence and no remedy — the defect this row
    removes, still live for one spelling. Both doors must read the SAME, so the
    expectation here is the OTHER spelling's own output, not a copy of the code
    under test. `implementation` is a `tools` runner (no chat glue) and P4's
    prompt needs component/branch, so nothing is spent and nothing refuses."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE + "  implementation: builder\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")

    slash_run = run_id
    stripped_run = entry.open_run(
        repo, "title: a change\n\nkind: requirement\n\n"
              "## What\n\nA small change.\n")["run_id"]   # identical intake
    _p4_dir_recorded_then_deleted(repo, slash_run, "40-impl/")
    _p4_dir_recorded_then_deleted(repo, stripped_run, "40-impl")

    slash = _spawn_drive(repo, slash_run, "builder", "component=repo", "branch=b")
    stripped = _spawn_drive(repo, stripped_run, "builder",
                            "component=repo", "branch=b")
    assert slash.returncode == 1 and stripped.returncode == 1, (
        slash.stdout, slash.stderr, stripped.stdout, stripped.stderr)
    assert "OWED" in stripped.stdout and "40-impl/" in stripped.stdout, stripped.stdout
    # A remedy actually FOLLOWS the sentence — guards against both spellings
    # going bare (the ` - ` is verify's remedy separator, absent from a bare
    # sentence which ends at `run folder`).
    assert " run folder - " in stripped.stdout, stripped.stdout
    # The stripped spelling now reads exactly like the slash one: the remedy no
    # longer depends on which spelling the record happens to hold. A directory
    # remedy carries no run id, so the two doors are byte-identical.
    assert stripped.stdout == slash.stdout, (
        "the trailing-slash spelling is treated differently from the stripped "
        f"one.\n  stripped={stripped.stdout!r}\n  slash={slash.stdout!r}")


def test_pinned_digest_prefers_the_exact_spelling_and_drops_no_pin():
    """**T392 rounds 3-4.** A directory artifact is carried in the record under
    either spelling, and BOTH can be present at once as two pins of one artifact
    with two digests. The lookup prefers the EXACT contract spelling
    deterministically - not the insertion-order winner a collapsed
    `{k.rstrip('/'): v}` map would have kept - and finds the pin whichever
    spelling the record holds, in EITHER direction. Digests here are obvious
    non-secrets (one repeated character), built inline."""
    exact, stripped = "sha256:" + "a" * 64, "sha256:" + "c" * 64
    assert S._pinned_digest({"40-impl/": exact}, "40-impl/") == exact
    assert S._pinned_digest({"40-impl": stripped}, "40-impl/") == stripped
    # both present, two digests: the exact spelling wins in EITHER dict order,
    # so nothing depends on which pin happened to be inserted last
    assert S._pinned_digest({"40-impl/": exact, "40-impl": stripped},
                            "40-impl/") == exact
    assert S._pinned_digest({"40-impl": stripped, "40-impl/": exact},
                            "40-impl/") == exact
    assert S._pinned_digest({"10-x.md": exact}, "40-impl/") is None
    # round 4: the RECORD may hold the SLASHED spelling of a key the contract
    # owes UNslashed - a file pinned as `30-design.md/`. The lookup tries the
    # slashed candidate too, so the pin is found rather than lost (round 3
    # stripped only the contract side and regressed exactly this).
    assert S._pinned_digest({"30-design.md/": exact}, "30-design.md") == exact
    # an empty exact pin must not SHADOW a real one under a sibling spelling:
    # a non-empty digest wins, and empty ('') stays distinct from absent (None)
    # so the caller's `pin is not None` test keeps its meaning.
    assert S._pinned_digest({"40-impl/": "", "40-impl": stripped}, "40-impl/") == stripped
    assert S._pinned_digest({"40-impl/": ""}, "40-impl/") == ""
    assert S._pinned_digest({}, "40-impl/") is None


def test_a_directory_recorded_under_the_stripped_spelling_is_not_called_unrecorded(
        repo, run_id):
    """**T392 round 3.** `verify`'s 'wrote but never recorded' arm was widened
    to match the record slash-insensitively too. A directory present on disk and
    pinned under `40-impl` (the stripped spelling) must NOT be reported as
    unrecorded merely because the contract spells it `40-impl/`. Restoring the
    raw comparison leaves every other test green, so this pins the widened arm."""
    ledger.advance(repo, run_id, phase="P4", note="to implementation")
    d = ledger.run_dir(repo, run_id) / "40-impl"
    d.mkdir(parents=True, exist_ok=True)
    (d / "patch.diff").write_text("diff --git a b\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl")          # stripped spelling
    problems = S.verify(repo, run_id, "P4", include_run_wide=False,
                        include_conditional=False)
    assert not any("never recorded" in p for p in problems), problems
    assert problems == [], problems       # the directory verifies clean


def test_a_produces_pinned_under_a_trailing_slash_is_found_by_the_unslashed_contract(
        repo, run_id):
    """**T392 round 4.** `artifact_key` keeps whatever trailing slash a caller
    typed, so a file can be pinned as `30-design.md/` though the contract owes
    `30-design.md`. Round 3's fallback stripped only the CONTRACT's spelling and
    never the record's, so this pin went unfound and the person met the bare
    sentence again - this row's own defect, reintroduced for one spelling. The
    lookup now also tries the slashed key. Spawned door, `python -c pass`, so
    nothing is spent; `design` is a tools runner so the glue cannot rewrite the
    missing file."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE.replace("  design: writer", "  design: builder"),
                 encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    D.drive(repo, run_id, {}, run=answering())            # walk to P3
    assert ledger.load(repo, run_id)["phase"] == "P3"

    rd = ledger.run_dir(repo, run_id)
    (rd / "30-design.md").write_text("# Design\n\nreal design bytes\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "30-design.md/")   # SLASHED spelling
    digest = ledger.load(repo, run_id)["artifacts"]["30-design.md/"]
    (rd / "30-design.md").unlink()
    # `_remedy` names the CONTRACT spelling (`30-design.md`), the one the loop
    # holds; derived from the ledger, not typed.
    followable = ledger.restore_command(run_id, "30-design.md", digest)

    proc = _spawn_drive(repo, run_id, "writer,builder")
    seen = proc.stdout + proc.stderr
    assert proc.returncode == 1, seen
    assert "owes 30-design.md, which is not in the run folder" in seen, seen
    assert followable in seen, (
        "a pin stored under the slashed spelling was not found by the unslashed "
        f"contract, so the door fell back to the bare sentence.\n{seen}")


# ── the chat glue, bounded ──────────────────────────────────────────────────


def test_a_chat_runners_answer_becomes_the_phases_document(repo, run_id):
    """The ONE piece of glue: a chat runner returns text, the phase owes a
    file. Recorded as an artifact like any hand-written one, so gates judge it
    identically."""
    D.step(repo, run_id, {}, run=answering())              # P0 -> P1
    D.step(repo, run_id, {}, run=answering("# Requirements\n\nEARS criteria\n"))
    written = ledger.run_dir(repo, run_id) / "10-requirements.md"
    assert "EARS criteria" in written.read_text(encoding="utf-8")
    assert "10-requirements.md" in ledger.load(repo, run_id).get("artifacts", {})


def test_the_glue_never_overwrites_what_a_runner_wrote_itself(repo, run_id):
    """A runner that wrote its own document is not second-guessed."""
    D.step(repo, run_id, {}, run=answering())              # P0 -> P1
    own = ledger.run_dir(repo, run_id) / "10-requirements.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("written by the runner\n", encoding="utf-8")
    D.step(repo, run_id, {}, run=answering("stdout chatter\n"))
    assert own.read_text(encoding="utf-8") == "written by the runner\n"


def test_the_glue_is_limited_to_single_document_phases():
    """P4 owes a DIRECTORY; no stdout blob can stand in for a worktree of
    changes, so the glue declines rather than writing something plausible."""
    assert D._document_for(S.by_phase("P0")) == "00-intake.md"
    assert D._document_for(S.by_phase("P4")) is None


# ── the walls: what this module may never do ────────────────────────────────


def reaches_approve(source: str) -> list[str]:
    """Every way `source` could record a gate: importing `approve`, or naming
    its CLI in a string it could spawn. Docstrings are exempt — this module's
    own docstring names `approve.py` while explaining the rule, which is the
    false positive item 48 paid for."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if "approve" in (node.module or ""):
                found.append(f"from {node.module}")
            found += [f"import {a.name}" for a in node.names
                      if "approve" in a.name]
        elif isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names
                      if "approve" in a.name]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "approve.py" in node.value or "approve.record" in node.value:
                found.append(f"names {node.value[:40]!r}")
    return found


def test_the_approve_wall_can_actually_fail():
    """**The wall's own guard.** A sweep that found nothing would make the
    assertion below vacuous while staying green — the mirror-not-a-test shape
    this project has now met three times. Proven against source that offends."""
    assert reaches_approve("from tools.sdlc import approve\n")
    assert reaches_approve("import tools.sdlc.approve\n")
    assert reaches_approve('CMD = "python tools/sdlc/approve.py --gate G3"\n')
    assert reaches_approve('"""A docstring naming approve.py."""\nx = 1\n') == [], \
        "prose about the rule reads as breaking it"


def test_the_driver_can_never_approve_a_gate():
    """**The structural wall.** A driver that could record a gate would make
    every gate in the profile decorative — so it neither imports `approve` nor
    names its CLI anywhere it could be spawned."""
    assert reaches_approve(Path(D.__file__).read_text(encoding="utf-8")) == []


def test_the_driver_has_no_force_flag():
    """`stage.py` has one because a human is standing there taking
    responsibility in the moment. An unattended loop that could force past a
    gate is the machine the halt rule exists to refuse.

    Prose naming the rule is not the rule being broken — and this test's first
    draft failed on its own module's docstring, which is exactly the false
    positive the test above is written by AST to avoid. Same fix, one line
    down: the third time this project has paid for that lesson."""
    import ast

    tree = ast.parse(Path(D.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    code = ast.unparse(tree)
    assert "--force" not in code, "the driver takes a force flag"
    assert "force=True" not in code, "the driver forces something"


def test_it_advances_through_the_layer_that_has_an_opinion(repo, run_id):
    """`stage.advance` re-checks the contract and records the routing;
    `ledger.advance` would move the run with no opinion at all. One opinion
    about whether a run may move, and the driver is not it."""
    import ast

    tree = ast.parse(Path(D.__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "advance"]
    assert calls, "it never advances at all"
    for call in calls:
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "phases", \
            "it advances around stage.py's opinion"


@pytest.mark.parametrize("var", ["PIPELINE_TOKEN", "BUILD_CREDENTIAL"])
def test_a_secret_never_reaches_a_transcript(repo, run_id, monkeypatch, var):
    """**`BUILD_CREDENTIAL` is the case that shipped broken.** This module's
    first cut restated `provider_smoke`'s secret-name pattern instead of
    importing it, and the copy dropped `CREDENTIAL` at birth - so a
    credential-named value was written verbatim into a durable transcript the
    original would have redacted. The pattern is imported now, and this
    parametrisation is what would have caught the drift."""
    monkeypatch.setenv(var, "not-a-real-credential-abcdefgh")
    found = D.step(repo, run_id, {},
                   run=answering("leaked not-a-real-credential-abcdefgh here"))
    text = Path(found["transcript"]).read_text(encoding="utf-8")
    assert "not-a-real-credential-abcdefgh" not in text
    assert "<redacted>" in text


def test_an_inline_credential_in_the_command_is_masked():
    """`safe` is scrub PLUS mask_inline: a key carried in the runner's own
    command line was never in the environment, so no value-match can find it -
    and the first cut recorded `record["command"]` verbatim."""
    from tools import provider_smoke as smoke

    assert "sk-abcdefghijklmnop" not in smoke.safe(
        "runner --api-key sk-abcdefghijklmnop", set())


# ── the credentials the HARNESS ITSELF supplies ─────────────────────────────
#
# The test above sets its secret in the SHELL, which is the half `os.environ`
# can see. `RUNNER_CONTRACT` §3 tells adopters to put the provider's key in the
# harness `.env`, `runner_env()` injects it at the spawn, and the run record is
# committed into the adopter's repository (item 121) - so the one set of
# credentials this harness ADDS was exactly the set the scrub could not see.
# These write a scratch `.env` and point `provider_smoke.HARNESS` at it; no
# real `.env` is read by any test in this file.

#: Invented sentinels: all-one-letter runs, entropy near zero, so no gitleaks
#: generic rule reads them as credentials. Seven characters is deliberate - it
#: is under `secret_values`' redaction floor, which exists so scrubbing cannot
#: mangle `true` or `8080` and never decided what a NAME means.
LONG_SENTINEL, SHORT_SENTINEL = "z" * 20, "y" * 7

#: A runner that prints the harness-supplied variables it was handed. The fake
#: `run=` used everywhere above cannot see this defect at all: the injected
#: environment only exists at a real spawn.
ECHO_ENV = ("import os\n"
            "print(\" \".join(k + \"=\" + v for k, v in sorted(os.environ.items())\n"
            "                if k.startswith(\"DRIVE_PROBE_\")))\n")


def _echoing_runner(repo: Path, tmp_path: Path) -> None:
    """Route the profile's runners at that process, built from `sys.executable`
    under `tmp_path` so no machine path is written down."""
    script = tmp_path / "echo_env.py"
    script.write_text(ECHO_ENV, encoding="utf-8")
    command = '"' + sys.executable + '" "' + str(script) + '"'
    (repo / ".sdlc" / "profile.yml").write_text(
        PROFILE.replace(RUNNER_CMD, command), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "echo")


def _harness_env(tmp_path, monkeypatch, **values: str) -> None:
    """A scratch harness `.env` carrying only invented values, with the names
    cleared from the shell so the file is genuinely the only source."""
    from tools import provider_smoke as smoke

    home = tmp_path / "harness"
    home.mkdir(exist_ok=True)
    (home / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()),
                               encoding="utf-8")
    monkeypatch.setattr(smoke, "HARNESS", home)
    for key in values:
        monkeypatch.delenv(key, raising=False)


def test_a_key_the_harness_itself_supplied_never_reaches_the_transcript(
        repo, run_id, tmp_path, monkeypatch):
    """**The scrub must be taken over the environment the driver SPAWNS
    with.** Harvesting from `os.environ` while spawning with `runner_env()`
    means the values the harness adds out of `.env` - the provider key, the
    only credential this harness is responsible for putting there - are the
    ones scrubbing cannot match, and they are written verbatim into a
    transcript the adopter commits."""
    _harness_env(tmp_path, monkeypatch, DRIVE_PROBE_API_KEY=LONG_SENTINEL)
    _echoing_runner(repo, tmp_path)
    found = D.step(repo, run_id, {})
    text = Path(found["transcript"]).read_text(encoding="utf-8")
    assert "DRIVE_PROBE_API_KEY=" in text, \
        f"the runner never received it, so nothing is under test: {text!r}"
    assert LONG_SENTINEL not in text
    assert D.REDACTED in text


def test_a_short_value_under_a_secret_name_is_withheld_from_both(
        repo, run_id, tmp_path, monkeypatch):
    """**One variable, one answer, in one run record.** `supplied_env` asks
    `secret_named` and redacts a seven-character `*_KEY`; the transcript asked
    `secret_values`, whose `len >= 8` floor exists so redaction cannot mangle
    an ordinary word like `true` - a rule about REPLACING TEXT that had become
    the rule about what a name means. A short key is still a key."""
    _harness_env(tmp_path, monkeypatch, DRIVE_PROBE_SHORT_KEY=SHORT_SENTINEL)
    _echoing_runner(repo, tmp_path)
    found = D.step(repo, run_id, {})
    text = Path(found["transcript"]).read_text(encoding="utf-8")
    assert "DRIVE_PROBE_SHORT_KEY=" in text, \
        f"the runner never received it, so nothing is under test: {text!r}"
    assert found["env"]["DRIVE_PROBE_SHORT_KEY"] == D.REDACTED, \
        "the record's half of this was already right"
    assert SHORT_SENTINEL not in text, "seven characters is still a key"


def test_the_scrub_and_the_spawn_read_one_environment(
        repo, run_id, tmp_path, monkeypatch):
    """The weld, and the reason this is not `os.environ` plus a second look at
    `.env`: replacing the module's ONE environment producer must move the
    spawn and the scrub together. This sentinel is in neither the shell nor any
    file, so a scrub that consults either misses it."""
    _echoing_runner(repo, tmp_path)
    monkeypatch.setattr(
        D, "runner_env",
        lambda: {**os.environ, "DRIVE_PROBE_WELD_KEY": LONG_SENTINEL})
    found = D.step(repo, run_id, {})
    text = Path(found["transcript"]).read_text(encoding="utf-8")
    assert "DRIVE_PROBE_WELD_KEY=" in text, "the spawn did not use it"
    assert LONG_SENTINEL not in text, "the scrub did not use it"


# ── the spend acknowledgement ───────────────────────────────────────────────


def test_the_cli_refuses_to_run_without_spend(repo, run_id, capsys):
    assert D.main(["--target", str(repo), "--run", run_id]) == 2
    err = capsys.readouterr().err
    assert "--spend is required" in err and "writer" in err


def test_the_cli_refuses_a_spend_that_does_not_name_every_runner(repo, run_id, capsys):
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(PROFILE + "  implementation: builder\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "route")
    assert D.main(["--target", str(repo), "--run", run_id,
                   "--spend", "writer"]) == 2
    assert "does not name builder" in capsys.readouterr().err


def test_plan_spawns_nothing_and_says_what_would_run(repo, run_id, capsys):
    """The phase not moving is NOT proof: a spawn of a command that does not
    exist fails, the step stops at RAN_BADLY, and the run stays put — so the
    mutation that made `--plan` drive the walk survived on that assertion
    alone. **The transcript directory is the observable proof**: it exists if
    and only if something was actually spawned."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 0
    out = capsys.readouterr().out
    # **Item 127 widened what `--plan` says, and the property is unchanged.**
    # It answered `would spawn: writer` - who, and never WHICH PHASES or WHERE
    # IT STOPS, which is the operator's actual question before spending.
    assert "writer" in out
    assert "P0 (intake): writer" in out, "the plan does not name the phases"
    assert "HALTS after this phase" in out, "the plan does not say where it stops"
    assert ledger.load(repo, run_id)["phase"] == "P0", "a plan moved the run"
    assert not (ledger.run_dir(repo, run_id) / D.TRANSCRIPTS).exists(), \
        "a plan spawned a runner"


def test_runners_ahead_reports_only_what_is_still_ahead(repo, run_id):
    assert D.runners_ahead(repo, run_id) == ["writer"]
    ledger.advance(repo, run_id, phase="P11", note="jump to the end")
    assert D.runners_ahead(repo, run_id) == [], "closeout is not routed"


# ── the real spawn, which nothing exercised ─────────────────────────────────
#
# Every test above injects a fake `run=`. That is right for the loop's logic
# and it left the module's ONLY real-binary path untested — so `spawn` shipped
# broken three ways at once: `shell=True` truncated every multi-line prompt at
# its first newline on Windows and ran the rest as commands, a `"` in any value
# escaped the quoting, and no environment was passed at all. CLAUDE.md is
# explicit: "Prefer proving a claim by running it over reasoning about it —
# invoke the real binary." These spawn one.


ECHO_ARGV = ("import json,sys,os;"
             "print(json.dumps({'argv': sys.argv[1:],"
             "'seen': os.environ.get('DRIVE_SPAWN_PROBE', '')}))")


def test_spawn_hands_a_multi_line_prompt_over_whole(tmp_path):
    """**The defect this file existed without catching.** A rendered prompt is
    ALWAYS multi-line — a header, a blank line, then the exact-value block — and
    the shell form delivered only the first line."""
    prompt = ("Execute phase P4 (implementation).\n"
              "\n"
              "component: widgets\n"
              "diff: /tmp/a path/change.diff" + chr(10))
    code, out = D.spawn(f'"{sys.executable}" -c "{ECHO_ARGV}"', prompt, tmp_path)
    assert code == 0, out
    argv = json.loads(out)["argv"]
    assert argv == [prompt], f"the runner did not receive the whole prompt: {argv!r}"


def test_spawn_does_not_let_a_prompt_reach_a_shell(tmp_path):
    """A connector-supplied issue title reaches later-phase prompts, so prompt
    text is not all harness-authored. Under `shell=True` this ran `echo`."""
    prompt = 'title" & echo INJECTED & "rest'
    code, out = D.spawn(f'"{sys.executable}" -c "{ECHO_ARGV}"', prompt, tmp_path)
    assert code == 0, out
    # The whole prompt arrives as ONE argument, and `echo` never ran: the word
    # appears only INSIDE the echoed argv, as data. Asserting its absence from
    # the output would be wrong - a correct run must contain it, quoted.
    assert json.loads(out)["argv"] == [prompt]
    assert len(out.strip().splitlines()) == 1, f"the shell ran something: {out!r}"


def test_spawn_gives_the_runner_the_harness_env(tmp_path, monkeypatch):
    """`RUNNER_CONTRACT` §3 says the provider's key belongs in the harness
    `.env`; the smoke passes `runner_env()` and the first cut of `spawn` passed
    nothing — so a runner could green the conformance gate and then fail here."""
    monkeypatch.setenv("DRIVE_SPAWN_PROBE", "carried")
    code, out = D.spawn(f'"{sys.executable}" -c "{ECHO_ARGV}"', "hi", tmp_path)
    assert code == 0, out
    assert json.loads(out)["seen"] == "carried"


def test_spawn_reports_an_unparseable_command_rather_than_raising(tmp_path):
    code, out = D.spawn('runner --flag "unbalanced', "hi", tmp_path)
    assert code == 127 and "could not be parsed" in out


# ── item 127: the driver's messages, and what it can resume from ────────────
#
# Walk 3's W4, W5, W9, W10 and W12, plus one item 131 created an hour before
# this landed. Every one of them is the same shape: two different facts
# reported as one word.


def _step_with(monkeypatch, code, output):
    """Drive one step against a fake runner returning exactly this."""
    return lambda command, prompt, cwd, timeout=3600: (code, output)


def test_a_runner_that_never_started_is_not_a_runner_that_ran(repo, run_id,
                                                              monkeypatch):
    """**W4.** `run_tool` returns `(127, "did not run: ...")` for a spawn
    failure and the driver read only the code, so `goose exited 127` described
    a process that RAN. On walk 3 the real reason - `[WinError 2] The system
    cannot find the file specified` - survived only in the transcript, and the
    two need opposite repairs: install it, versus debug it."""
    from tools import provider_smoke as smoke

    said = D.step(repo, run_id, {},
                  run=_step_with(monkeypatch, smoke.NOT_STARTED_CODE,
                                 f"{smoke.NOT_STARTED_NOTE}[WinError 2]"))
    assert said["stop"] == D.COULD_NOT_SPAWN
    assert "never started" in said["why"]
    assert "Nothing was spent" in said["why"]
    assert "not a runner that misbehaved" in said["why"]


def test_a_runner_that_ran_and_failed_is_still_ran_badly(repo, run_id,
                                                         monkeypatch):
    """The control. Exit 127 alone is NOT the discriminator - a shell says 127
    for *command not found* and a real runner may exit 127 for its own reasons
    - so the note's prefix, which only `run_tool` writes, is the other half."""
    said = D.step(repo, run_id, {},
                  run=_step_with(monkeypatch, 127, "command not found"))
    assert said["stop"] == D.RAN_BADLY

    said = D.step(repo, run_id, {}, run=_step_with(monkeypatch, 1, "boom"))
    assert said["stop"] == D.RAN_BADLY


def test_a_refused_value_is_not_reported_as_a_missing_one(repo, run_id,
                                                          monkeypatch):
    """**Item 127's own finding, from item 131 an hour earlier.**
    `StageError` had grown five meanings and every one was mapped to
    `needs-value`, which tells an operator to SUPPLY something. Four of the
    five are the opposite of a missing value.

    **Item 142 moved WHERE this is decided and kept the property.** The driver
    now asks the question of the whole walk rather than of one phase, so the
    refusal arrives before routing and names the phases it checked - and the
    stop is still REFUSED rather than `needs-value`, which is what this test
    has always been about.
    """
    said = D.step(repo, run_id, {"favourite-colour": "blue"},
                  run=_step_with(monkeypatch, 0, "unused"))
    assert said["stop"] == D.REFUSED, said
    assert "states a value called favourite-colour" in said["why"]
    assert "checked P0" in said["why"], said["why"]


def test_a_value_nobody_supplied_is_still_needs_value(repo, run_id, monkeypatch):
    """The control that keeps the distinction honest: a genuinely absent value
    still asks the operator to supply one, which is the only case where that
    sentence is true."""
    # P6 needs four CALLER values and is not routed in the shared fixture,
    # so it is routed here - to `builder`, because `writer` is a CHAT
    # runner and item 105 refuses one on an ACT phase, which is a routing
    # refusal that fires before the prompt is ever rendered.
    (repo / ".sdlc" / "profile.yml").write_text(
        PROFILE + "  review: builder" + chr(10),
        encoding="utf-8")
    ledger.advance(repo, run_id, "P6", note="for the test")
    said = D.step(repo, run_id, {}, run=_step_with(monkeypatch, 0, "unused"))
    assert said["stop"] == D.NEEDS_VALUE, said
    assert "nobody supplied" in said["why"]


def test_the_plan_names_the_phases_and_where_it_halts(repo, run_id, capsys):
    """**W5.** `runners_ahead` walked the phases and threw them away, so
    `--plan` answered *who* and never *which phases, or where it stops* - the
    operator's actual question before spending."""
    rows = D.plan_ahead(repo, run_id)
    assert [r["phase"] for r in rows][:2] == ["P0", "P1"]
    assert any(r["gate"] for r in rows), "no gate is reported at all"
    assert D.runners_ahead(repo, run_id) == ["writer"]


def test_plan_reads_spend_instead_of_advising_what_was_just_done(repo, run_id,
                                                                 capsys):
    """**W5's other half.** `--plan` returned BEFORE `args.spend` was read, so
    `--plan --spend goose` printed *acknowledge with --spend goose* - advice to
    do the thing that had just been done."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan",
                   "--spend", "writer"]) == 0
    out = capsys.readouterr().out
    assert "already names every runner ahead" in out
    assert "acknowledge with --spend" not in out


def test_a_stale_artifact_halts_the_walk_the_way_a_missing_one_does(repo, run_id,
                                                                    monkeypatch):
    """**W9.** A phase that never recorded its artifact halted the walk and one
    that recorded it and then wrote it AGAIN did not, though from the runner's
    side they are one failure. Nothing unsafe reached a gate - `approve`
    re-hashes - so the cost was WHERE it was caught."""
    folder = ledger.run_dir(repo, run_id)
    (folder / "00-intake.md").write_text("first", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "00-intake.md")
    (folder / "00-intake.md").write_text("moved since", encoding="utf-8")

    assert S.verify(repo, run_id, "P0") == [], (
        "verify must NOT change - approve.record calls it, and improving an "
        "artifact before approving it is the ordinary case")
    moved = S.stale(repo, run_id, "P0")
    assert moved and "bytes have moved" in moved[0]

    said = D.step(repo, run_id, {}, run=_step_with(monkeypatch, 0, "wrote it"))
    assert said["stop"] == D.STALE, said


def test_a_recorded_conditional_artifact_that_moved_is_stale_too(repo, run_id):
    """**T251(a).** `stale`'s owed set was `produces | evidence` and its test was
    `rel in owed`, a literal — while the one conditional entry in the table is a
    GLOB. So an ADR written, pinned and then rewritten, verbatim the failure the
    docstring says this exists to catch, was reported by nothing: the ledger said
    *changed since it was recorded* and `stale` said `[]`.

    `stale` asks about what was RECORDED, and drift is a property of a recording,
    not of a declaration class. An unrecorded ADR is still owed by nobody - this
    demands nothing new, it reports what somebody already chose to pin."""
    folder = ledger.run_dir(repo, run_id)
    (folder / "30-design.md").write_text("# design\n", encoding="utf-8")
    (folder / "30-adr-001.md").write_text("# adr one\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "30-design.md")
    ledger.record_artifact(repo, run_id, "30-adr-001.md")
    (folder / "30-adr-001.md").write_text("# rewritten after the pin\n",
                                          encoding="utf-8")

    assert any("30-adr-001.md" in row for row in
               ledger.verify_artifacts(repo, run_id)), (
        "the ledger must see the drift, or this test proves nothing about stale")
    moved = S.stale(repo, run_id, "P3")
    assert [row for row in moved if "30-adr-001.md" in row], moved
    assert "bytes have moved" in moved[0], moved
    assert S.verify(repo, run_id, "P3") == [], (
        "verify must NOT change - approve.record calls it, and improving an "
        "artifact before approving it is the ordinary case")


def test_a_drift_kind_that_carries_a_detail_is_stale_too(repo, run_id):
    """**T251(b).** `stale` grepped for the English sentence *changed since it
    was recorded*, which `state.verify_artifacts` emits only when the drift
    carries no `detail` - and kind `unhashable` ALWAYS carries one. So a
    recorded directory emptied after its pin was reported by the ledger and not
    by `stale`, and the consumer could never have matched that branch.

    Matching a producer's PROSE across a module boundary is the coupling: the
    kinds live in `state.drift` and only `state.py` knows their sentences."""
    folder = ledger.run_dir(repo, run_id)
    impl = folder / "40-impl"
    impl.mkdir(parents=True, exist_ok=True)
    (impl / "patch.diff").write_text("--- a\n+++ b\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl/")
    (impl / "patch.diff").unlink()

    kinds = {kind for rel, kind, _ in ledger.drift(
        repo, run_id, ledger.load(repo, run_id).get("artifacts", {}))
        if rel.rstrip("/") == "40-impl"}
    assert kinds == {"unhashable"}, kinds
    moved = S.stale(repo, run_id, "P4")
    assert [row for row in moved if "40-impl" in row], moved


def test_a_deleted_recorded_evidence_artifact_is_stale_and_only_stale(repo,
                                                                      run_id):
    """**T251(c), and it defends a ruling rather than repairs a bug.** `stale`
    reports all three of `state.drift`'s kinds, `missing` among them: an
    artifact somebody pinned and then DELETED has drifted, and the pin now
    covers nothing.

    Nothing defended that. Dropping `missing` from this function left every
    other test of it green, so the next person narrowing it takes the kind out
    for free and the drop looks like a simplification.

    Measured, and it is why the kind belongs here: `verify` names a missing
    RECORDED artifact for `produces` only - it walks `stage.produces` and
    nothing else - and says nothing at all for `conditional` or `evidence`. On
    two of the three declaration classes `stale` is the ONLY reporter, so
    carving `missing` out to avoid the one overlap would restore the exact
    blindness T251 exists to close, one kind over."""
    folder = ledger.run_dir(repo, run_id)
    (folder / "50-suite.json").write_text('{"passed": 1}\n', encoding="utf-8")
    ledger.record_artifact(repo, run_id, "50-suite.json")
    (folder / "50-suite.json").unlink()

    kinds = {kind for rel, kind, _ in ledger.drift(
        repo, run_id, ledger.load(repo, run_id).get("artifacts", {}))
        if rel == "50-suite.json"}
    assert kinds == {"missing"}, (
        f"the ledger must see the drift and call it missing, or this test "
        f"proves nothing about stale: {kinds}")

    moved = S.stale(repo, run_id, "P5")
    named = [row for row in moved if "50-suite.json" in row]
    assert named, moved
    assert "no longer in the run folder" in named[0], named

    assert not [row for row in S.verify(repo, run_id, "P5")
                if "50-suite.json" in row], (
        "verify reports a missing recorded artifact for `produces` only; if it "
        "ever names an evidence one, this row stops being the only reporter "
        "and the duplication is worth deciding again")


def test_a_phase_this_walk_already_delivered_is_advanced_not_re_run(repo, run_id,
                                                                    monkeypatch):
    """**W10, and the module's own docstring already promised a RESUMABLE
    walk.** After a human repaired an OWED stop by pinning the document the
    runner wrote, continuing re-ran the phase under a PAID runner and
    overwrote what had just been accepted."""
    folder = ledger.run_dir(repo, run_id)
    (folder / D.TRANSCRIPTS).mkdir(parents=True, exist_ok=True)
    (folder / D.TRANSCRIPTS / "P0.txt").write_text("ran", encoding="utf-8")
    # The phase stopped at OWED and a human then pinned what the runner wrote -
    # which is the exact situation W10 describes, and the only one a skip is
    # allowed for. A transcript alone says THIS RAN, not this succeeded.
    (folder / D.TRANSCRIPTS / "P0.stop").write_text(D.OWED, encoding="utf-8")
    (folder / "00-intake.md").write_text("what the runner wrote", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "00-intake.md")

    spawned = []

    def never(command, prompt, cwd, timeout=3600):
        spawned.append(command)
        return 0, "should not have been called"

    said = D.step(repo, run_id, {}, run=never)
    assert spawned == [], "the driver re-ran a phase it had already delivered"
    assert said.get("skipped")
    assert said["advanced_to"] == "P1"
    assert (folder / "00-intake.md").read_text(encoding="utf-8") == \
        "what the runner wrote", "the accepted document was overwritten"


def test_a_fresh_run_still_runs_p0(repo, run_id, monkeypatch):
    """**The control, and it is why the discriminator is the TRANSCRIPT.**
    `intake.open_run` writes AND records `00-intake.md` before the driver
    starts, so a phase-owes-nothing test would skip P0 on every fresh run.
    Measured, not reasoned about."""
    folder = ledger.run_dir(repo, run_id)
    (folder / "00-intake.md").write_text("written by intake", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "00-intake.md")
    assert not (folder / D.TRANSCRIPTS / "P0.txt").exists()

    spawned = []

    def once(command, prompt, cwd, timeout=3600):
        spawned.append(command)
        return 0, "ran"

    D.step(repo, run_id, {}, run=once)
    assert spawned, "the driver skipped P0 on a FRESH run - nothing ever ran it"


def test_w12_is_a_ruling_rather_than_a_mechanism():
    """**W12, ruled rather than built.** On walk 3 goose ended P2 with four
    unfinished to-dos and a question, and exited 0 - so the driver read a
    runner that had DECIDED TO STOP as one that finished. The harness cannot
    tell those apart: the runner returns 0 either way, and the exit code is the
    whole of what a process says. The OWED stop catches the consequence, which
    is why this is a ruling and not a stop.

    **What would overturn it**: a runner protocol in which a runner reports
    that it stopped and why - at which point the distinction is available and
    is worth a word of its own. Recorded here because a decision without its
    reversal condition is one the next person has to re-derive (item 124).
    """
    source = Path(D.__file__).read_text(encoding="utf-8")
    assert "COULD_NOT_SPAWN" in source
    # The ruling names no mechanism for a runner that stops politely, and that
    # absence is the decision - asserted so a future reader meets it.
    assert D.RAN_BADLY == "ran-badly" and D.OWED == "owed"


def test_the_outcome_of_a_step_is_recorded_where_the_next_one_reads_it(repo,
                                                                       run_id):
    """**The composition, and a mutation survivor proved it was missing.**

    `step` WRITES the outcome and `_last_stop` READS it, and the resume test
    above hand-wrote the file - so nothing exercised the pair. Removing the
    write entirely left every test green, which is the producer/consumer gap
    this project has found more often than any other class.

    Driven end to end: a step that stops at OWED, then a human pinning what the
    runner wrote, then a second step that must NOT spawn.
    """
    def wrote_but_did_not_pin(command, prompt, cwd, timeout=3600):
        (ledger.run_dir(repo, run_id) / "00-intake.md").write_text(
            "the runner's document", encoding="utf-8")
        return 0, "wrote it"

    first = D.step(repo, run_id, {}, run=wrote_but_did_not_pin)
    assert first["stop"] == D.STALE, first
    assert D._last_stop(repo, run_id, "P0") == D.STALE

    # The human repairs it the way walk 3's operator did.
    ledger.record_artifact(repo, run_id, "00-intake.md")
    (ledger.run_dir(repo, run_id) / D.TRANSCRIPTS / "P0.stop").write_text(
        D.OWED, encoding="utf-8")

    spawned = []
    second = D.step(repo, run_id, {},
                    run=lambda c, p, w, timeout=3600: (spawned.append(c), (0, ""))[1])
    assert spawned == [], "the repaired phase was re-run under a paid runner"
    assert second["advanced_to"] == "P1"


# ── item 143: the marker is STATE, not a phase's permanent character ────────


def counting(text: str = "the phase's document, written by a model\n",
             code: int = 0):
    """A fake runner that answers and records every spawn."""
    spawned: list[str] = []

    def run(command, prompt, cwd, timeout=3600):
        spawned.append(command)
        return code, text

    run.spawned = spawned                              # type: ignore[attr-defined]
    return run


def _repaired_owed_stop(repo: Path, run_id: str) -> Path:
    """The situation W10 describes: P0 stopped at OWED and a human then pinned
    what the runner wrote. Returns the run folder."""
    folder = ledger.run_dir(repo, run_id)
    (folder / D.TRANSCRIPTS).mkdir(parents=True, exist_ok=True)
    (folder / D.TRANSCRIPTS / "P0.txt").write_text("ran", encoding="utf-8")
    (folder / D.TRANSCRIPTS / "P0.stop").write_text(D.OWED, encoding="utf-8")
    (folder / "00-intake.md").write_text("what the runner wrote",
                                         encoding="utf-8")
    ledger.record_artifact(repo, run_id, "00-intake.md")
    return folder


def test_a_marker_is_cleared_when_the_phase_advances(repo, run_id):
    """**Item 143's first half, and the composition its absence hid.**

    `step` wrote the marker on every stop and NOTHING removed it, so what the
    file said outlived the condition it described. Driven through the ordinary
    path rather than hand-written: a runner fails (the marker is recorded), the
    next attempt succeeds and the run advances, and the marker must be gone -
    asserted through `_last_stop`, the reader, and not only the file.
    """
    folder = ledger.run_dir(repo, run_id)
    first = D.step(repo, run_id, {}, run=counting(code=1, text="boom"))
    assert first["stop"] == D.RAN_BADLY, first
    assert D._last_stop(repo, run_id, "P0") == D.RAN_BADLY

    second = D.step(repo, run_id, {}, run=counting())
    assert second["advanced_to"] == "P1", second
    assert not (folder / D.TRANSCRIPTS / "P0.stop").exists(), \
        "the marker outlived the attempt it described"
    assert D._last_stop(repo, run_id, "P0") == "", \
        "the reader still sees a stop the phase has since advanced past"
    assert (folder / D.TRANSCRIPTS / "P0.txt").exists(), \
        "what HAPPENED belongs in the transcript and is not cleared with it"


def test_a_phase_the_lifecycle_loops_back_to_is_run_again(repo, run_id):
    """**Item 143's first half, measured on the shape that costs.**

    A marker nothing clears makes W10's one-shot resume PERMANENT for that
    phase. Live on a real run: `P2.stop` said `owed` three days after a human
    had repaired it, and P2 verified clean and unstale - so a loop BACK to P2
    was skipped as already-delivered and advanced past, spawning nothing. The
    lifecycle's own diagram draws P5->P4, P7->P3 and P8->P4, so a backward move
    is the design rather than an edge, and it is a legal advance needing no
    `--force`.
    """
    folder = _repaired_owed_stop(repo, run_id)
    resumed = counting()
    said = D.step(repo, run_id, {}, run=resumed)
    assert resumed.spawned == [] and said["advanced_to"] == "P1", said

    # P1 delivers, so the run may legally move BACK to P0 - the way P5 returns
    # to P4 when the suite finds new failures.
    (folder / "10-requirements.md").write_text("the requirements",
                                               encoding="utf-8")
    ledger.record_artifact(repo, run_id, "10-requirements.md")
    S.advance(repo, run_id, "P0", note="the way P5 returns to P4")
    assert ledger.load(repo, run_id)["phase"] == "P0"

    again = counting()
    D.step(repo, run_id, {}, run=again)
    assert again.spawned, \
        "the phase the run looped BACK to was skipped as already-delivered"


def test_an_approved_gate_is_not_re_spawned(repo, run_id):
    """**Item 143's second half.** Only an `owed` stop was resumable, so a
    phase that stopped at its GATE kept a `gate` marker - and once the human
    approved, the next invocation re-ran the phase under a PAID runner and
    rewrote the document that gate had just pinned. A GATE stop is reached only
    after `verify` AND `stale` have both passed, so it is a STRONGER statement
    that the phase delivered than an OWED stop is.
    """
    walked = D.drive(repo, run_id, {}, run=answering())
    assert walked[-1]["stop"] == D.GATE and walked[-1]["phase"] == "P3", walked
    design = (ledger.run_dir(repo, run_id) / "30-design.md").read_bytes()

    A.record(repo, run_id, "G2", "approve", "owner", notes="looks right")
    second = counting(text="REWRITTEN by a second paid run\n")
    steps = D.drive(repo, run_id, {}, run=second)
    assert second.spawned == [], \
        "the approved phase was re-run under a paid runner"
    assert steps[0]["advanced_to"] == "P4", steps
    assert (ledger.run_dir(repo, run_id) / "30-design.md").read_bytes() == \
        design, "the approved document moved, which item 32 voids the gate over"


def test_a_gate_nobody_has_answered_yet_is_not_re_spawned(repo, run_id):
    """The half that costs most and looks least like a defect: re-invoking the
    driver while a human is still READING the document rewrote it under them.
    Nothing about the phase has changed, so nothing should be spent."""
    D.drive(repo, run_id, {}, run=answering())
    design = (ledger.run_dir(repo, run_id) / "30-design.md").read_bytes()

    second = counting(text="REWRITTEN while the human was reading it\n")
    steps = D.drive(repo, run_id, {}, run=second)
    assert second.spawned == [], "the phase was re-run while its gate was open"
    assert steps[0]["stop"] == D.GATE, steps
    assert (ledger.run_dir(repo, run_id) / "30-design.md").read_bytes() == design


def test_a_rejected_gate_makes_the_phase_run_again(repo, run_id):
    """**The discrimination, and the control that stops the fix above from
    killing the loop this project spent four items closing.**

    Green by design at the pre-item SHA - there a GATE stop always re-spawned,
    so the loop worked by accident. Once a GATE stop is resumable it works on
    purpose or not at all: a rejection is an instruction to do the phase AGAIN,
    item 136 carries the reason into the very next prompt, and skipping here
    would leave the run stopped at a gate nobody can ever satisfy.
    """
    D.drive(repo, run_id, {}, run=answering())
    A.record(repo, run_id, "G2", "reject", "owner",
             notes="the design does not say how it fails")

    again = counting()
    D.drive(repo, run_id, {}, run=again)
    assert again.spawned, \
        "the phase that must ANSWER a standing rejection was skipped"


def test_could_not_look_at_the_ledger_counts_as_a_rejection(repo, run_id,
                                                            monkeypatch):
    """**The ruling, pinned at the one place it is decidable.**

    `standing_rejection` answers a ledger it cannot read with the gate and a
    reason rather than raising, and the driver cannot tell that from a verdict:
    it re-runs, because an absent rejection and an unreadable one are not the
    same thing and the cheaper mistake is one runner call.
    """
    monkeypatch.setattr(D.phases, "standing_rejection",
                        lambda *a: ("G2", "",
                                    "(the approval ledger could not be read)"))
    assert D._must_answer_a_rejection(repo, run_id, "P3") is True

    monkeypatch.setattr(D.phases, "standing_rejection", lambda *a: ("", "", ""))
    assert D._must_answer_a_rejection(repo, run_id, "P3") is False


def test_an_unreadable_ledger_line_costs_nothing_and_stops_at_the_gate(repo,
                                                                       run_id):
    """**What the ruling above does NOT cover, measured rather than assumed -
    and it found a defect in another module.**

    A corrupt LINE never reaches the could-not-look branch: `read_chain`
    returns it as an `_unreadable` marker rather than raising and
    `live_entries` skips the marker, so no rejection is reported, this driver
    skips, and `halted_at` stops the walk at the gate - no spend, and a human
    standing where they need to be.

    **That was item 144, and it is fixed** (`approve.gate_state` asks
    `verify_chain` before it says anything about absence), so this asserts the
    true sentence: what the driver reports is the DAMAGE, named by line. The
    assertion is what item 143 left behind to say when the fix landed.
    """
    D.drive(repo, run_id, {}, run=answering())
    (ledger.run_dir(repo, run_id) / "approvals.jsonl").write_text(
        "{not json at all\n", encoding="utf-8")

    again = counting()
    said = D.step(repo, run_id, {}, run=again)
    assert again.spawned == [], "a corrupt ledger line cost a paid runner call"
    assert said["stop"] == D.GATE, said
    assert "not readable JSON" in said["why"], (
        "the driver reports a damaged ledger as an absent verdict - item 144")


@pytest.mark.parametrize("stop", [D.UNROUTED, D.REFUSED, D.NEEDS_VALUE,
                                  D.RAN_BADLY, D.COULD_NOT_SPAWN, D.STALE])
def test_a_stop_the_phase_did_not_survive_is_never_skipped(repo, run_id, stop):
    """The boundary of `RESUMABLE`, as behaviour rather than as a list.

    Green by design: none of these was skippable before either. They are here
    so that widening the tuple - the mutation that turns this fix into a driver
    which skips phases that never ran - fails by name.
    """
    folder = _repaired_owed_stop(repo, run_id)
    (folder / D.TRANSCRIPTS / "P0.stop").write_text(stop, encoding="utf-8")

    run = counting()
    D.step(repo, run_id, {}, run=run)
    assert run.spawned, f"a {stop} stop was skipped as already-delivered"
    assert stop not in D.RESUMABLE


# ── item 142: one map, judged once against the walk, read per phase ─────────


def _routed_through_review(repo: Path) -> None:
    """Route P5 and P6 as well - both are ACT phases, so a `chat` runner is a
    routing refusal (item 105) and `builder` is the tool-capable one."""
    (repo / ".sdlc" / "profile.yml").write_text(
        PROFILE + "  test: builder" + chr(10) + "  review: builder" + chr(10),
        encoding="utf-8")


P6_VALUES = {"diff": "d.patch", "component": "c", "prepass-out": "o",
             "hitl-target": "t"}


def test_a_phase_carries_a_value_the_next_one_reads(repo, run_id):
    """**Item 142, and it is the whole item.** `drive` handed the SAME map to
    every phase and `stage.resolve` refuses a key the current phase does not
    name, so supplying P6's four values was refused at P5 and supplying nothing
    stopped at P6 - and P5->P6->P7 has no gate to force the break an operator
    would otherwise make. Measured at the step, where the refusal was."""
    _routed_through_review(repo)
    ledger.advance(repo, run_id, "P5", note="for the test")

    at_p5 = answering()
    said = D.step(repo, run_id, dict(P6_VALUES), run=at_p5)
    assert said["stop"] != D.REFUSED, said
    assert at_p5.seen, "P5 was refused for values that belong to P6"
    assert "diff: d.patch" not in at_p5.seen[0]["prompt"], (
        "P5 was handed a value its own contract does not name")
    assert said.get("values") == [], said


def test_the_phase_that_reads_it_is_handed_it(repo, run_id):
    """The other half of the same claim: carrying is only safe if the value
    ARRIVES. `_step_once` had tests and `render_prompt` had tests and nothing
    ran one through the other with a map meant for a later phase."""
    _routed_through_review(repo)
    ledger.advance(repo, run_id, "P6", note="for the test")

    at_p6 = answering()
    D.step(repo, run_id, dict(P6_VALUES), run=at_p6)
    assert at_p6.seen, "P6 never ran"
    prompt = at_p6.seen[0]["prompt"]
    for key, value in P6_VALUES.items():
        assert f"{key}: {value}" in prompt, f"{key} never reached the prompt"


def test_a_value_no_phase_ahead_reads_is_refused_before_anything_spawns(repo,
                                                                        run_id):
    """A typo must still fail LOUDLY - the property item 109 exists to protect,
    and the one a per-phase filter would have silently destroyed. It names the
    phases it checked, because a refusal an operator cannot act on is the shape
    item 117 spent a whole item on."""
    run = answering()
    said = D.step(repo, run_id, {"compnent": "c"}, run=run)
    assert said["stop"] == D.REFUSED, said
    assert run.seen == [], "a walk that cannot read its values still spent"
    assert "compnent" in said["why"] and "checked P0" in said["why"]


def test_a_value_the_harness_states_itself_is_refused_by_name(repo, run_id):
    """Items 109, 121, 124, 125 and 126 each moved a value to `run`-sourced and
    each made supplying it a refusal. Filtering per phase would have turned all
    five back into silent drops, so the walk-level check refuses them too - and
    says WHERE the value actually comes from, which is what makes it fixable."""
    said = D.step(repo, run_id, {"baseline": "green"}, run=answering())
    assert said["stop"] == D.REFUSED, said
    assert "stated by the run itself" in said["why"], said["why"]


def test_a_value_the_profile_states_is_refused_rather_than_ignored(repo, run_id):
    """**Found while building item 142, and it is a third instance of the same
    class.** `resolve` reads a `profile:` value from `profile.yml` and never
    looks at `supplied`, and it is not `extra` either because the phase DOES
    name it - so supplying `block-on` was silently ignored. A value nothing
    reads is refused here whichever document states it.

    **Measured at the phase where the drop actually happens**: at P0 the key is
    `extra` and was refused anyway, so testing there would have proved nothing.
    At P6, which NAMES `block-on`, the caller's value was read by nobody and
    nothing said so.

    Filed as item 145 and FIXED there, so the two altitudes now agree: the
    driver refuses it for the whole walk and `stage.resolve` refuses it for the
    phase. This test holds the driver's half, which fires first and names the
    walk rather than the phase."""
    _routed_through_review(repo)
    ledger.advance(repo, run_id, "P6", note="for the test")

    run = answering()
    said = D.step(repo, run_id, {"block-on": "critical"}, run=run)
    assert said["stop"] == D.REFUSED, said
    assert run.seen == [], "a runner was paid to read a value nothing reads"
    assert "profile.yml" in said["why"], said["why"]


def test_the_span_is_judged_once_for_the_whole_walk(repo, run_id, monkeypatch):
    """**The subtlety that makes this correct rather than merely working.** The
    span SHRINKS as the walk advances, so re-deriving it per step would refuse
    the caller at P7 for the `diff` P6 had just read - turning the fix into the
    defect one phase along. Pinned by counting."""
    calls = []
    real = D.span_from

    def counted(target, run_id_):
        calls.append(run_id_)
        return real(target, run_id_)

    monkeypatch.setattr(D, "span_from", counted)
    steps = D.drive(repo, run_id, {}, run=answering())
    assert len(steps) > 1, "the walk took one step, so this proves nothing"
    assert len(calls) == 1, f"the span was derived {len(calls)} times"


def test_the_plan_says_which_phase_reads_each_value(repo, run_id, capsys):
    """The visible half: filtering is not a silent drop if the operator can see
    which phase each value is for BEFORE a runner is paid. `--plan` already
    computes the span (item 127's W5), which is why this reuses it rather than
    forming a second opinion about where the walk goes."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan",
                   "--set", "diff=d.patch"]) == 0
    assert "diff is read by P6" in capsys.readouterr().out


def test_the_plan_refuses_a_value_nothing_would_read(repo, run_id, capsys):
    """A plan carrying a value nothing reads is not a plan that runs, so saying
    so at exit 0 would be this module reporting a refusal as an answer."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan",
                   "--set", "compnent=c"]) == 2
    assert "!! no phase in this walk states a value called compnent" in \
        capsys.readouterr().out


# -- item 147: `--plan` answers about the REGIME, not the declaration --------


def _gated(rows):
    return {r["gate"]: r for r in rows if r["gate"]}


def test_a_gate_this_regime_does_not_halt_at_is_not_reported_as_halting(
        repo, run_id):
    """**Item 147, R4 of the review, and the fixture's own comment says it.**

    `plan_ahead` reported each phase's DECLARED gate and `--plan` printed
    *HALTS* for every one, while `stage.halted_at` says in its own docstring
    that *"which gates halt is the REGIME's answer, not this function's:
    `solo` does not stop at G3 and `team` does"*. This repository is `solo`,
    whose hard gates are G2 and G4 - so the plan an operator reads BEFORE
    deciding to spend claimed a stop at G3 that the walk goes straight
    through.
    """
    gated = _gated(D.plan_ahead(repo, run_id))
    assert gated["G2"]["halts"] is True
    assert gated["G4"]["halts"] is True
    assert gated["G3"]["halts"] is False, (
        "G3 is declared at P4 and this regime does not halt at it")


def test_the_plan_a_person_reads_says_which_of_the_three_it_is(repo, run_id,
                                                               capsys):
    """The sentence, not the field - item 96's rule about what a PERSON sees.
    Three facts calling for three acts: a stop to go and find, a phase the
    walk passes through, and a regime nobody could read."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 0
    out = capsys.readouterr().out
    assert "G2 HALTS after this phase" in out
    assert "G3 is declared here and this regime does not halt at it" in out
    assert "G3 HALTS" not in out


def test_an_unreadable_policy_refuses_the_plan_rather_than_softening_it(
        repo, run_id, capsys):
    """**My own design, refuted by running it, and the refutation is the
    finding.** The first cut answered `halts: None` for an unreadable
    `policy.yml`, reasoning that `--plan` reports rather than moves.

    Measured: the branch is UNREACHABLE. `runner_for` calls `policy.load` for
    every row, so an invalid constitution already ends `plan_ahead` with that
    reader's own by-line refusal, and an unreadable PROFILE stops
    `phases.active` earlier still. A defensive branch nobody can reach is a
    claim nobody has checked - and the refusal is the right answer anyway,
    because every line of this screen is read out of the document in question.
    """
    (repo / ".sdlc" / "policy.yml").write_text(
        "gate_profile_floor: [not, a, scalar\n", encoding="utf-8")
    with pytest.raises(guardrails.PolicyError):
        D.plan_ahead(repo, run_id)
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 2
    assert "policy.yml" in capsys.readouterr().out


def test_the_regime_is_read_once_for_the_whole_plan(repo, run_id, monkeypatch):
    """Item 142's subtlety one question along: the regime is a property of the
    RUN, not of a phase, so asking it per row would read two documents twelve
    times to answer one question twelve times."""
    calls = []
    real = D.hard_gates
    monkeypatch.setattr(D, "hard_gates",
                        lambda target: (calls.append(target), real(target))[1])
    rows = D.plan_ahead(repo, run_id)
    assert len(rows) > 1 and len(calls) == 1, calls


def test_the_plan_agrees_with_the_function_that_actually_halts_the_run(
        repo, run_id):
    """**The composition, and it is why this shipped.** `plan_ahead` had tests
    and `halted_at` had tests and nothing asked whether they AGREE - so the
    planner could say HALTS about a gate the walk drives straight past, which
    is the producer/consumer gap this project has found more often than any
    other.

    `halted_at` returns problems only for a gate that is hard AND not taken,
    so an untaken gate stopping the run is exactly the plan's `halts: True`.
    """
    for gate, row in _gated(D.plan_ahead(repo, run_id)).items():
        stops = bool(S.halted_at(repo, run_id, row["phase"]))
        assert stops is row["halts"], (
            f"{gate} at {row['phase']}: the plan says halts={row['halts']} "
            f"and halted_at says {stops}")


# -- item 138: a runner that did nothing is not one that re-did the work -----

#: P3 rather than P2, because this fixture's `phases:` does not activate
#: scoping - so G2 is the gate a rejection can stand at here. The mechanism is
#: the gate's, not the phase's.
DESIGN = "30-design.md"


@pytest.fixture
def at_design(repo: Path, run_id: str) -> Path:
    """The run parked at P3 with its design pinned - the state a re-run after
    a rejection begins from, and the state item 138 is about: delivered,
    verifying clean, and waiting on a human."""
    ledger.advance(repo, run_id, "P3", note="parked for the test")
    (ledger.run_dir(repo, run_id) / DESIGN).write_text(
        "the first design, naming the files\n", encoding="utf-8", newline="\n")
    ledger.record_artifact(repo, run_id, DESIGN)
    return repo


def _reject(repo: Path, run_id: str) -> None:
    A.record(repo, run_id, "G2", "reject", "lead@example.invalid",
             notes="this defers the decision instead of making it")


def _rewrite(repo: Path, run_id: str, body: str) -> None:
    (ledger.run_dir(repo, run_id) / DESIGN).write_text(
        body, encoding="utf-8", newline="\n")
    ledger.record_artifact(repo, run_id, DESIGN)


def test_a_document_identical_to_what_was_rejected_has_not_answered_it(
        at_design, run_id):
    """**Item 138, and it overturns item 127's W12 on that ruling's own
    terms.** W12 ruled that the OWED stop catches a runner which exits 0
    having done nothing. Measured on the case item 136 created, it does not:
    the phase HAD delivered, so verify was clean, the hash matched its pin,
    and UNCHANGED IS NOT STALE - so the driver reported GATE and was right by
    every check it had. goose had hit *no credits remaining*, written a
    365-byte transcript and exited 0.
    """
    _reject(at_design, run_id)
    said = S.unanswered(at_design, run_id, "P3")
    assert said and "byte-identical to what was rejected" in said[0]
    assert DESIGN in said[0]


def test_a_rewritten_document_is_an_answer(at_design, run_id):
    """The control. A discriminator that fired on the runner which DID the
    work would break the loop items 136 to 140 exist to make possible, at the
    one gate a human is waiting on."""
    _reject(at_design, run_id)
    _rewrite(at_design, run_id, "a real answer to the rejection\n")
    assert S.unanswered(at_design, run_id, "P3") == []


def test_unchanged_is_only_a_problem_while_a_rejection_stands(at_design,
                                                              run_id):
    """**The bound, and this item's own filing warned about it.** Outside a
    standing rejection an unchanged document is a legitimate answer - a runner
    may conclude it was already right, which is item 42's shape. The human's
    LIVE verdict is the only thing that contradicts it, so a withdrawn
    rejection must stop being answered."""
    assert S.unanswered(at_design, run_id, "P3") == []
    _reject(at_design, run_id)
    assert S.unanswered(at_design, run_id, "P3") != []
    A.record(at_design, run_id, "G2", "approve", "lead@example.invalid",
             notes="fine as it stands")
    assert S.unanswered(at_design, run_id, "P3") == [], (
        "a withdrawn rejection is still being answered - item 42's rule")


def test_the_refusal_names_a_restore_that_actually_works(at_design, run_id):
    """**The two items composed, which is the only thing that proves either.**
    Item 139 keeps the bytes; item 138 is the reader that sends somebody to
    them. A store with no reader is item 102's wall of digests and a refusal
    naming a command that does not run is item 147's, so the sentence is
    parsed and its command SPAWNED."""
    _reject(at_design, run_id)
    printed = S.unanswered(at_design, run_id, "P3")[0].split("kept: ")[1].strip()
    assert printed.startswith("tools/sdlc/state.py --target <repo> --restore ")
    args = printed.replace("<repo>", str(at_design)).split()
    done = subprocess.run([sys.executable, *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "restored ->" in done.stdout


def test_the_driver_stops_rather_than_reporting_a_gate(at_design, run_id):
    """**The composition that matters most.** `unanswered` having tests and
    the driver having tests is the arrangement that lets a finding sit unread
    - item 136 found the rejection's REASON unread and this found its pinned
    BYTES unread, in the same record. Reporting it as `gate` sends an operator
    to approve a document they have already refused."""
    _reject(at_design, run_id)
    # A runner that answers with something, and writes NOTHING - which is the
    # measured case: goose printed its banner, hit *no credits remaining* and
    # exited 0, leaving the document its previous attempt had already pinned.
    found = D.step(at_design, run_id, {}, run=answering())
    assert found["stop"] == D.UNANSWERED, found
    assert "byte-identical" in found["why"]


def test_an_unanswered_stop_is_never_skipped_and_clears_itself(at_design,
                                                               run_id):
    """**Item 143's rule, and why this needs no exemption.** A marker that
    outlives its cause is that item's defect; this one cannot, because the
    moment anybody changes the document the digests differ."""
    assert D.UNANSWERED not in D.RESUMABLE
    _reject(at_design, run_id)
    assert S.unanswered(at_design, run_id, "P3") != []
    _rewrite(at_design, run_id, "answered\n")
    assert S.unanswered(at_design, run_id, "P3") == []


def test_the_driver_does_not_report_a_gate_about_a_rejected_document(
        at_design, run_id):
    """**The behavioural red, naming no new API.** At the pre-item SHA the
    driver answers `gate` here and is right by every check it has: the phase
    delivered, verify is clean, and UNCHANGED IS NOT STALE. That is the whole
    defect - an operator is sent to approve the document they have already
    rejected, and their only tell was reading a 365-byte transcript by hand.
    """
    _reject(at_design, run_id)
    found = D.step(at_design, run_id, {}, run=answering())
    assert found["stop"] != "gate", (
        "the driver is waiting at a gate over bytes the same human just "
        "refused: " + str(found))


# -- item 149, free half: routed is not the same as runnable ----------------

#: Command -> whether it would fail to start. **Both answers in one table**,
#: because a sweep asserting only failures is satisfied by a rule that always
#: says "broken" and one asserting only successes by its opposite (item 115).
#: Keyed by a LABEL rather than by the command, because `RUNNER_CMD` is built
#: from `sys.executable` and pytest would otherwise put this machine's
#: interpreter path into every generated test id, and so into CI logs - a
#: private coordinate the leak guard cannot see, because it scans tracked
#: FILES and a node name is neither.
COMMANDS = {
    "an absolute path that resolves": (RUNNER_CMD, False),
    "a bare name on PATH": ("git --version", False),
    "one nothing has": ("definitely-not-a-program-anybody-has --go", True),
    "an empty command": ("", True),
    "an unbalanced quote": ('x "unbalanced', True),
}


@pytest.mark.parametrize("label", sorted(COMMANDS))
def test_a_command_that_could_not_start_is_named_before_anything_spawns(
        label):
    """**Item 149's free half, and the walk of 2026-09-06 is why.** A profile
    declared `goose run ...` and `goose` was not on that machine's PATH. Item
    127's `could-not-spawn` catches it AT the spawn - correctly, and with no
    spend - but `--plan`, whose whole job is answering *what would run* before
    anybody decides to pay, reported the runner as routed and said nothing."""
    command, broken = COMMANDS[label]
    assert bool(D.command_problem(command)) is broken, label


def test_it_asks_the_question_the_spawn_asks_rather_than_a_second_one():
    """**The weld (item 115).** `spawn` tokenises with
    `provider_smoke.split_runner`, so an unparseable command must be reported
    in the words that module already owns - two definitions of "what is the
    program here" would drift, which is the class this project finds most."""
    said = D.command_problem('x "unbalanced')
    assert "cannot be parsed" in said and "quotation" in said


def test_the_path_it_asks_about_is_the_one_the_spawn_will_use():
    """**The claim this rests on, pinned rather than assumed.** `spawn` runs
    under `smoke.runner_env()`, which adds a name from the harness `.env` ONLY
    where the shell does not already have it - and PATH is always set in a
    shell - so the two agree for this key by construction. Reading `.env` in a
    planning path would widen where a secret is loaded for a lookup that
    cannot differ; if that ever stops being true, this says so.
    """
    from tools import provider_smoke as smoke
    assert smoke.runner_env().get("PATH") == os.environ.get("PATH")


# -- item 149 on Windows: PATHEXT resolves what CreateProcess cannot launch --
#
# **Every test below runs on BOTH platforms and not one of them skips.**
# `tools/conftest.py` turns ANY skip into exit 1 under `SDLC_FORBID_SKIPS`,
# with no exemption of any kind, and that flag is set where the suite runs on
# Linux - so a test that cannot run on a platform cannot exist here. It has to
# assert that platform's own correct answer instead. Each therefore carries
# two branches: what Windows does, where `PATHEXT` and cmd.exe make a
# resolvable name unlaunchable; and what POSIX does, where `which` appends no
# suffix, requires the execute bit, and `command_problem` returns "" before
# the whole Windows rule. **The two halves do not always agree in direction** -
# `.exe` beside a suffixless path is cleared on Windows and refused on POSIX.
#
# The POSIX halves were MEASURED on a real Linux, with real files and a real
# fork/exec, not reasoned about. The cost is stated rather than hidden: a
# POSIX branch cannot exercise CreateProcess, so the Windows evidence still
# only exists where the suite is run on Windows - which CLAUDE.md calls a
# first-class target. What the second branch buys is that `command_problem`'s
# `os.name != "nt"` early return, the one line no Windows test can kill, is
# now asserted by six tests where it had one. Six is COUNTED, not estimated:
# it is how many node ids in this file go red on a real Linux when that early
# return is deleted.


def launchable_image() -> tuple[str, str]:
    """A real file THIS platform LAUNCHES, and arguments it exits 0 for.

    Only the SOURCE of the image is platform-named; every assertion built on
    it is the same sentence on both. `where.exe` ships with Windows and
    `sys.executable` is a real binary by definition, so neither half depends
    on anything being installed.
    """
    if os.name == "nt":
        found = shutil.which("where.exe")
        assert found, "no launchable image to copy - the control proves nothing"
        return found, "where"
    return sys.executable, "-c pass"


class _AsWindows:
    """`os` as the module under test sees it, with `name` answering "nt".

    **Setting `os.name` itself does not work and that was measured, not
    guessed**: `os.name` is what `pathlib` dispatches on, so patching the real
    module makes `Path(...)` raise `NotImplementedError: cannot instantiate
    'WindowsPath' on your system` on Linux - a green Windows run and a red CI
    job. Replacing the module reference INSIDE `drive` leaves `pathlib`'s own
    import alone, so the rule takes the Windows branch while paths stay this
    platform's. Every other attribute is the real one.
    """

    name = "nt"

    def __getattr__(self, attribute):
        return getattr(os, attribute)

#: The shim, keyed by LABEL: the value is the SUFFIX it is written with, and a
#: parametrise keyed by a built path would put this machine's temp directory
#: into every generated test id and so into CI logs (the coordinate the leak
#: guard cannot see, because it scans tracked FILES and a node id is neither).
SHIMS = {"the npm shape (.CMD, resolved by PATHEXT)": ".CMD",
         "the older shape (.bat)": ".bat"}


@pytest.fixture
def shim_on_path(tmp_path, monkeypatch, request):
    """A real cmd.exe shim on a real PATH; returns (bare name, marker path)."""
    suffix = SHIMS[request.param] if hasattr(request, "param") else ".CMD"
    marker = tmp_path / "it-ran.txt"
    shim = tmp_path / f"w143probe{suffix}"
    # The marker is written on its OWN line, before the arguments are echoed:
    # measured, `echo RAN %* > "marker"` loses its redirect to the very
    # truncation this fixture exists to demonstrate, and the file then proves
    # nothing about whether the child ran.
    shim.write_bytes(b"@echo off\r\necho STARTED > \"" +
                     str(marker).encode("utf-8") + b"\"\r\necho RAN %*\r\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return "w143probe", marker


@pytest.mark.parametrize("shim_on_path", sorted(SHIMS), indirect=True)
def test_a_shim_which_resolves_but_cannot_be_launched_is_named_by_the_plan(
        shim_on_path, tmp_path):
    """Two branches: the `PATHEXT` gap exists nowhere but Windows, and the
    POSIX half pins that it is not invented there.

    Item 149's own defect, arriving through the commonest Windows install
    shape. `shutil.which` honours `PATHEXT` and finds `w143probe.CMD`;
    `CreateProcess` appends only `.exe` and answers `[WinError 2]`. Measured
    before this was written: `--plan` printed no `!!` and exited 0 while the
    spawn returned `(127, 'did not run: [WinError 2] ...')` - the operator
    learning at the spawn exactly what this screen exists to tell them
    first.
    """
    name, _ = shim_on_path
    if os.name != "nt":
        # Measured: POSIX `which` appends NO suffix, so the bare name is not
        # found at all and the refusal names PATH rather than cmd.exe.
        assert shutil.which(name) is None, (
            "POSIX resolved a suffix it does not append - the premise is gone")
        said = D.command_problem(name)
        assert said, "--plan cleared a runner that is not on PATH"
        assert "is not on PATH" in said, said
        assert "cmd.exe" not in said, (
            f"a Windows reason was given for a POSIX refusal: {said!r}")
        return
    assert shutil.which(name), "the fixture did not put a shim on PATH"
    said = D.command_problem(name)
    assert said, "--plan cleared a runner CreateProcess cannot launch"
    assert "cmd.exe" in said


@pytest.mark.parametrize("shim_on_path", sorted(SHIMS), indirect=True)
def test_the_plan_and_the_spawn_give_one_answer_about_a_shim(
        shim_on_path, tmp_path):
    """**Both platforms, one set of assertions, and no branch is needed.**
    Measured on a real Linux: the fixture writes mode 644 and never chmods, so
    POSIX `which` refuses the file for want of the execute bit; the `.CMD`
    spelling does not exist on a case-sensitive filesystem; and the bare name
    gets no suffix appended. All three spellings are refused there too - for a
    different reason, in a different sentence - so every assertion below holds
    verbatim, the weld included, and the marker is still never written.

    **The weld, and it is the whole point of the fix.** `--plan` clearing a
    runner the spawn then refuses - or the reverse - is two opinions about one
    question, which is what let this sit. Named outright the shim STARTS, so
    the spawn cannot be left to `CreateProcess` to answer: it asks
    `command_problem`, and the marker proves the child never ran.

    **Every assertion here is load-bearing, and that took a second pass.** The
    first cut opened with `bool(problem) is did_not_start(...)` and `problem in
    out` - an equivalence two FALSES satisfy, and a substring test that every
    string passes when `problem` is `""`. Delete the refusal and both stayed
    green; the only line with teeth was `not marker.exists()`. `assert problem`
    is now stated FIRST, which is what makes the two lines below mean what a
    reader takes them to mean (item 115: a guard nobody can watch fail).
    """
    name, marker = shim_on_path
    prompt = "line one\nline two\nline three"
    for command in (name, f"{name}.cmd", f"{name}.CMD"):
        problem = D.command_problem(command)
        code, out = D.spawn(command, prompt, tmp_path)
        assert problem, f"--plan cleared {command!r}, a cmd.exe shim"
        assert bool(problem) is smoke.did_not_start(code, out), command
        assert problem in out, f"the spawn said something else: {out!r}"
    assert not marker.exists(), (
        "the shim RAN - a prompt that cannot be delivered intact was spawned "
        "anyway, and that is what a paid call costs")


def test_a_cmd_shim_really_does_truncate_the_prompt(shim_on_path, tmp_path):
    """cmd.exe is the truncator, and the POSIX branch is the inversion.

    **The positive control for the refusal**, because a rule with no
    demonstration of its reason is superstition a later reader deletes. This
    runs the shim through the same `run_tool` the spawn uses and reads what
    the child actually received: one line of three. If cmd.exe ever stops
    doing this, the refusal above stops being justified and this says so.
    """
    _, marker = shim_on_path
    shim = next(tmp_path.glob("w143probe.*"))
    prompt = "line one\nline two\nline three"
    if os.name != "nt":
        # No cmd.exe, so nothing truncates: given the execute bit the kernel
        # execs the file from its shebang and the WHOLE prompt arrives.
        # Measured, exit 0. This is the inversion, not a weaker control.
        shim.write_text("#!/bin/sh\nprintf '%s' \"$1\"\n", encoding="utf-8")
        shim.chmod(0o755)
        code, got = smoke.run_tool([str(shim), prompt], cwd=tmp_path)
        assert code == 0, f"the shim itself did not run: {got!r}"
        assert all(line in got for line in prompt.splitlines()), (
            f"POSIX delivered less than the whole prompt: {got!r}")
        return
    code, got = smoke.run_tool([str(shim), prompt], cwd=tmp_path)
    assert code == 0 and marker.exists(), (
        "the shim itself did not run - the control proves nothing")
    assert "line one" in got
    assert "line two" not in got and "line three" not in got, (
        f"cmd.exe delivered more than the first line: {got!r}")


def test_a_program_windows_can_launch_is_still_cleared(shim_on_path):
    """The same fixture's negative control, and it needs no branch at all: it
    never touches the shim, and a real interpreter resolves on both platforms.
    It only means anything beside the refusal.

    The control that stops the rule being "refuse everything on Windows": with
    a shim on PATH, a real interpreter still plans and still spawns.
    """
    assert D.command_problem(RUNNER_CMD) == ""


#: The two bytes every file Windows launches begins with. Written here rather
#: than copied out of `System32` so the fixture depends on no install.
IMAGE_BYTES = b"MZ" + b"\x90\x00" * 32

#: The shapes `shutil.which` resolves and `CreateProcess` cannot even FIND,
#: keyed by LABEL for the reason `SHIMS` is: a built path in a parametrise
#: reaches every generated node id. The value is the suffix written; `""` is a
#: name with no suffix at all, which is what a real install leaves beside its
#: shim. **Every one was cleared by the first cut of this rule**, which named
#: `.cmd` and `.bat` and so was silent about the rest of PATHEXT.
UNFINDABLE = {"PATHEXT's .COM, which CreateProcess never appends": ".COM",
              "a .vbs, which which() finds and the spawn searches for": ".vbs",
              "a .js, the same": ".js",
              "no suffix at all, an image the spawn still cannot find": ""}


@pytest.mark.parametrize("label", sorted(UNFINDABLE))
def test_a_bare_name_the_spawn_cannot_find_is_refused_by_the_plan(
        label, tmp_path, monkeypatch):
    """`PATHEXT` exists nowhere but Windows, and the POSIX branch says which
    of these four shapes that leaves findable there.

    **The deny-list's silence, measured.** `.cmd`/`.bat` closed the shape in
    front of us and left the identical defect live at four more. With a real
    executable image installed under each of these suffixes, `shutil.which`
    honours all of PATHEXT and finds every one, `CreateProcess` appends
    `.exe` to a bare name and consults nothing else, and every one answered
    `[WinError 2]` at the spawn. The payload here IS an image, so the only
    thing that can refuse it is the search half of the rule.
    """
    name = "w143find"
    payload = tmp_path / f"{name}{UNFINDABLE[label]}"
    payload.write_bytes(IMAGE_BYTES)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    if os.name != "nt":
        # The execute bit is granted so the SUFFIX rule is the only thing
        # still being measured; refusing a mode-644 file would be true and
        # useless. Measured: POSIX appends no suffix, so the three suffixed
        # shapes are simply not on PATH, while the suffixless one resolves
        # and is CLEARED - PATHEXT is a cmd.exe list, and refusing for it
        # here would stop a runner that works.
        payload.chmod(0o755)
        if UNFINDABLE[label]:
            assert shutil.which(name) is None, (
                "POSIX resolved a suffix it does not append")
            said = D.command_problem(name)
            assert said, "--plan cleared a runner that is not on PATH"
            assert "is not on PATH" in said, said
        else:
            assert shutil.which(name), "the fixture put nothing on PATH"
            assert D.command_problem(name) == "", (
                "--plan applied a Windows search rule where its reason does "
                "not exist")
        return
    assert shutil.which(name), "the fixture put nothing on PATH"
    said = D.command_problem(name)
    assert said, "--plan cleared a runner the spawn cannot even find"
    assert "appends only '.exe'" in said, said
    code, out = D.spawn(name, "line one\nline two", tmp_path)
    assert smoke.did_not_start(code, out) and said in out, out


def test_a_ps1_spelled_outright_is_refused_rather_than_cleared(
        tmp_path, monkeypatch):
    """`[WinError 193]` is a Windows answer; POSIX asks the execute bit, and
    the branch below deliberately asserts the direction that costs something.

    The half of the deny-list's silence that does not go through PATHEXT at
    all. `shutil.which('x.ps1')` resolves it - on Windows the execute check is
    "exists" - the first cut of the rule cleared it, and the spawn answers
    `[WinError 193] %1 is not a valid Win32 application`. It never reaches the
    not-on-PATH branch that was believed to catch it, which is why reasoning
    about it was not enough.
    """
    script = tmp_path / "w143ps.ps1"
    script.write_text("exit 5\r\n", encoding="ascii")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    if os.name != "nt":
        # The 755 CLEAR, deliberately, and not the 644 refusal: a mode-644
        # file is refused here for want of the execute bit, which is true and
        # has nothing to do with the guard under test - it would pass with
        # the Windows rule applied to every platform. At 755 POSIX says this
        # file IS launchable, "is it a PE image" is not a question POSIX
        # asks, and `--plan` must not refuse it. Over-refusal is the
        # dangerous direction, because a working runner stops working.
        script.chmod(0o755)
        assert shutil.which("w143ps.ps1"), "the fixture put nothing on PATH"
        assert D.command_problem("w143ps.ps1") == "", (
            "--plan applied the Windows image rule where its reason does not "
            "exist, and refused a file POSIX rules say is launchable")
        return
    assert shutil.which("w143ps.ps1"), "the fixture put nothing on PATH"
    said = D.command_problem("w143ps.ps1")
    assert said, "--plan cleared a runner CreateProcess answers 193 for"
    assert "not a Windows executable image" in said, said
    code, out = D.spawn("w143ps.ps1", "line one\nline two", tmp_path)
    assert smoke.did_not_start(code, out) and said in out, out


def test_a_runner_that_could_not_be_read_is_refused_rather_than_guessed(
        tmp_path, monkeypatch):
    """**The platform is INJECTED, and injecting it costs nothing here**: this
    test already fakes the path lookup and never spawns, so `os.name` is the
    only real-OS fact left and the branch it selects is pure. Precedent:
    `test_provider_smoke.py` injects the same name for the same reason.
    `executable_image`'s own third answer is asserted below, unfaked, on every
    platform.

    "Could not look" is not "not an image": the two refuse for different
    reasons and say so, because a refusal carrying the wrong reason sends the
    reader to the wrong file.
    """
    monkeypatch.setattr(D, "os", _AsWindows())
    monkeypatch.setattr(D.shutil, "which", lambda program, *a, **k: str(tmp_path))
    said = D.command_problem("w143unreadable")
    assert "could not be read" in said, said


def test_an_extensionless_image_spelled_outright_is_still_cleared(tmp_path):
    """Only the SOURCE of the image is platform-named; the assertion is the
    same sentence on both, and so is the real spawn that backs it.

    **The negative control for the search half, and the shape a suffix
    allow-list breaks.** Measured: a real executable image copied to a name
    with NO suffix and spelled as an absolute path STARTS. Windows appends
    `.exe` only where it has to SEARCH; a spelling that carries a directory
    separator is opened literally. So "the suffix is not `.exe`" is not a
    reason to refuse - a rule that treats it as one refuses a runner that
    works, which is the failure a deny-list was inverted to avoid and the one
    an over-eager allow-list walks straight into.

    A mutation dropping the separator branch of `spawn_opens` passes every
    other test in this section and fails this one.
    """
    real, args = launchable_image()
    copied = tmp_path / "w143plain"
    shutil.copy(real, copied)
    assert D.command_problem(str(copied)) == "", (
        "--plan refused a runner that starts")
    code, out = D.spawn(f'"{copied}" {args}', "a prompt", tmp_path)
    assert not smoke.did_not_start(code, out), out


def test_a_com_beside_a_suffixless_spelling_is_refused_rather_than_cleared(
        tmp_path):
    """`PATHEXT` exists nowhere else - and both platforms REFUSE this shape,
    in different sentences, which is the two-branch form at its cleanest.

    **This row's own defect, one shape over, inside the branch written to
    close it.** The control above is this test with the payload named
    `w143plain` instead of `w143plain.COM`, and the difference is everything:
    `shutil.which` applies the whole of `PATHEXT` to a token that carries a
    directory too, so the suffixless absolute spelling resolved to the `.COM`,
    `executable_image` said True because a real PE genuinely is one, and
    `--plan` cleared a runner the raw spawn answers `[WinError 2]` for.
    Measured twice before this was written.

    CreateProcess opens a separator-carrying token LITERALLY and, failing
    that, appends `.exe` and only `.exe`. Nothing here is absent: the image is
    on disk, `which` finds it, and it is still not the file the spawn opens.
    """
    real, _ = launchable_image()
    beside = tmp_path / "w143plain.COM"
    shutil.copy(real, beside)
    spelled = str(tmp_path / "w143plain")
    if os.name != "nt":
        # Measured: POSIX `which` applies no PATHEXT to a separator-carrying
        # token either, so the suffixless spelling resolves to nothing and
        # the refusal names PATH. Both platforms refuse; only the reason
        # differs, and the real spawn agrees with the plan on both.
        assert shutil.which(spelled) is None, (
            "POSIX resolved a suffix it does not append")
        said = D.command_problem(spelled)
        assert said, "--plan cleared a runner that is not on PATH"
        assert "is not on PATH" in said, said
        code, out = D.spawn(f'"{spelled}" x', "line one\nline two", tmp_path)
        assert smoke.did_not_start(code, out) and said in out, out
        return
    assert shutil.which(spelled), (
        "which() no longer resolves this - the premise is gone, not the bug")
    assert D.executable_image(shutil.which(spelled)) is True, (
        "the payload is not an image, so the image half would refuse it and "
        "this would pass for the wrong reason")
    said = D.command_problem(spelled)
    assert said, "--plan cleared a runner the spawn cannot even find"
    assert "appends only '.exe'" in said, said
    code, out = D.spawn(f'"{spelled}" where', "line one\nline two", tmp_path)
    assert smoke.did_not_start(code, out) and said in out, out


def test_the_one_suffix_the_spawn_does_append_to_a_path_is_still_cleared(
        tmp_path):
    """**The one place the two branches DISAGREE in direction**: Windows
    CLEARS this shape and POSIX REFUSES it, because appending `.exe` is a
    thing only CreateProcess does. Read the branch, not the name.

    **The other direction of the same branch, and it was open.** A mutation
    dropping the `.exe` append from the separator branch passed all 116 tests
    in this file: nothing asserted that the ONE suffix CreateProcess does add
    to a path still gets added. That is an over-refusal - the shape below
    STARTS, measured - and an over-refusal stops a working runner, which is
    worse than the gap it was meant to close.

    So the pair is asserted together: beside a suffixless spelling a `.COM`
    is refused (above) and an `.exe` is cleared (here). Only `.exe`, because
    only `.exe` is what CreateProcess appends.
    """
    real, args = launchable_image()
    shutil.copy(real, tmp_path / "w143only.exe")
    spelled = str(tmp_path / "w143only")
    assert not Path(spelled).exists(), (
        "the suffixless name exists, so the literal-open branch would answer "
        "and the append would never be reached")
    if os.name != "nt":
        # Measured: POSIX appends nothing, so the very file Windows launches
        # here is not found at all and is REFUSED. The verdict INVERTS, and
        # that is exactly what pins the append as a Windows-only behaviour
        # rather than something the rule does everywhere.
        assert shutil.which(spelled) is None, "POSIX appended '.exe'"
        said = D.command_problem(spelled)
        assert said, "--plan cleared a runner that is not on PATH"
        assert "is not on PATH" in said, said
        code, out = D.spawn(f'"{spelled}" {args}', "line one\nline two",
                            tmp_path)
        assert smoke.did_not_start(code, out) and said in out, out
        return
    assert D.command_problem(spelled) == "", (
        "--plan refused a runner that starts: CreateProcess appends '.exe' to "
        "a path with no extension")
    code, out = D.spawn(f'"{spelled}" {args}', "line one\nline two", tmp_path)
    assert not smoke.did_not_start(code, out), out


def test_a_cmd_spelled_outright_is_refused_for_the_prompt_not_for_the_launch(
        tmp_path):
    """cmd.exe is the truncator on Windows; on POSIX the same file is exec'd
    from its own shebang, so the branch below asserts it is CLEARED and that
    it really starts with the whole prompt.

    **The truncation refusal was pinned by nothing.** Emptying
    `SHELL_SUFFIXES` to `()` left the whole section green: an outright `.cmd`
    simply fell through to the image branch and was refused with a sentence
    that is measurably FALSE - "not a valid Win32 application" about a file
    measured STARTING at exit 5. A guard nobody can watch fail is item 115,
    and two causes delivered as one message is what this row exists to fix.

    So both halves are asserted here: that the truncation branch is the one
    that answers (delete it and the assertions below flip), and that a person
    meeting this refusal can tell it from the other two - it says the file
    WOULD START and names the prompt, rather than borrowing the "did not
    start" of the unfindable case or the "%1 is not a valid Win32
    application" of the not-an-image one.
    """
    shim = tmp_path / "w143trunc.cmd"
    shim.write_text("@echo off\r\necho RAN %*\r\n", encoding="ascii")
    spelled = str(shim)
    if os.name != "nt":
        # The suffix is not the question here: given the execute bit and a
        # shebang, POSIX runs this file directly and the whole prompt
        # arrives. Refusing it would be a Windows rule applied where cmd.exe
        # - the entire reason for the rule - does not exist.
        shim.write_text("#!/bin/sh\nprintf '%s' \"$1\"\n", encoding="utf-8")
        shim.chmod(0o755)
        assert D.command_problem(spelled) == "", (
            "--plan refused a runner POSIX launches directly")
        code, out = D.spawn(spelled, "line one\nline two", tmp_path)
        assert code == 0 and "line two" in out, out
        return
    assert D.executable_image(spelled) is False, (
        "a .cmd that IS an image would take the same branch either way, and "
        "this test would pass without the truncation rule existing")
    said = D.command_problem(spelled)
    assert said, "--plan cleared a .cmd, which truncates the prompt"
    assert "WOULD START" in said and "first line" in said, said
    assert "not a valid Win32 application" not in said, (
        f"the truncation refusal is wearing the not-an-image reason: {said!r}")
    assert "did not start" not in said, (
        f"the truncation refusal is wearing the unfindable reason: {said!r}")
    code, out = D.spawn(f'"{spelled}" arg', "line one\nline two", tmp_path)
    assert smoke.did_not_start(code, out) and said in out, out


def test_what_windows_can_launch_is_asked_of_the_file_not_of_its_suffix(
        tmp_path):
    """The allow-list's whole predicate, asserted where CI can run it.

    Measured before this was written: ONE real executable image copied to
    `.exe`, `.com`, `.vbs`, `.js`, `.foo`, `.ps1` and to a name with no suffix
    at all started from an outright spelling every time, while a TEXT `.ps1`,
    `.vbs` and `.js` each answered `[WinError 193]`. So the suffix is not the
    question and a list of suffixes cannot be the answer - which is what a
    deny-list of two of them proved by clearing four more shapes.

    The third answer is deliberate: a file that could not be READ is neither
    an image nor not one, and a caller that collapses the two reports a reason
    it did not establish.
    """
    image = tmp_path / "w143img.vbs"
    image.write_bytes(IMAGE_BYTES)
    text = tmp_path / "w143txt.exe"
    text.write_text("exit 5\r\n", encoding="ascii")
    assert D.executable_image(str(image)) is True, "a .vbs image IS launchable"
    assert D.executable_image(str(text)) is False, "an .exe suffix is not it"
    assert D.executable_image(str(tmp_path)) is None, "a directory cannot be read"


def test_a_cmd_named_bare_on_path_is_refused_only_where_cmd_exe_launches_it(
        tmp_path, monkeypatch):
    """The BARE-name spelling of the same question, which takes the OTHER
    branch of `spawn_opens` than the outright one above.

    A mutation dropping the `os.name != "nt"` guard is EQUIVALENT on Windows
    and cannot be killed there: every Windows test would still pass while the
    harness had begun refusing, on POSIX, a runner the kernel launches
    directly from its shebang with the whole prompt intact. This used to be
    the only test in the section CI could run, because every other one
    skipped; none of them skips now, and this one keeps its Windows half
    rather than trading it away.
    """
    shim = tmp_path / "w143probe.cmd"
    shim.write_text("#!/bin/sh\nprintf 'RAN %s' \"$1\"\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    if os.name == "nt":
        # Same file, same bare spelling: here cmd.exe is the launcher, so the
        # refusal names the PROMPT and says the file WOULD START.
        said = D.command_problem("w143probe.cmd")
        assert said, "--plan cleared a .cmd, which truncates the prompt"
        assert "WOULD START" in said and "first line" in said, said
        return
    assert D.command_problem("w143probe.cmd") == "", (
        "a .cmd is exec'd directly here - refusing it would be a Windows rule "
        "applied where its reason does not exist")
    code, out = D.spawn("w143probe.cmd", "line one\nline two", tmp_path)
    assert code == 0 and "line two" in out, out


def test_every_planned_phase_says_whether_its_runner_could_start(repo, run_id):
    """Routed is not runnable, and the row carries both."""
    rows = D.plan_ahead(repo, run_id)
    assert rows and all(row["problem"] == "" for row in rows if row["runner"])


def test_the_plan_a_person_reads_refuses_a_walk_that_could_not_run(
        repo, run_id, capsys):
    """**The composition, and it is the whole defect.** `command_problem`
    having tests and `--plan` having tests is exactly the arrangement that let
    this sit: the operator learns at the SPAWN what a lookup could have told
    them beforehand. Item 142 already ruled that a plan carrying a value
    nothing reads exits 2 because it is not a plan that runs; a plan naming a
    runner that cannot start is not one either.
    """
    text = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    (repo / ".sdlc" / "profile.yml").write_text(
        text.replace(f"'{RUNNER_CMD}'", "'no-such-runner-anywhere --go'"),
        encoding="utf-8")
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 2
    out = capsys.readouterr().out
    assert "is not on PATH" in out
    assert "did not start" in out
    assert "P0 routes to 'writer'" in out


def test_a_plan_whose_runners_all_resolve_is_not_refused(repo, run_id, capsys):
    """The negative control, and it is what stops this being a check nobody
    can watch NOT firing: the ordinary fixture resolves, so `--plan` stays at
    exit 0 and says nothing about PATH."""
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 0
    assert "is not on PATH" not in capsys.readouterr().out


# -- item 149, second half: a runner answers to an environment --------------

#: Value -> whether it is a SETTING worth recording, or something that could
#: be a LOCATION. **Both answers in one table** (item 115), and the location
#: cases are the ones that would ship into an adopter's repository.
#: **Built, never written down** - the leak guard caught this table's first
#: cut, which spelled a home directory out in full: the property under test is
#: "carries a separator", never "is somebody's real path", and item 108 built
#: a PEM banner this way for the same reason.
_POSIX_PATH = chr(47) + "a" + chr(47) + "b"
_WINDOWS_PATH = "C:" + chr(92) + "a" + chr(92) + "b"
_URL = "bolt:" + chr(47) * 2 + "host:7687"

VALUES = {
    "openai": True,
    "gpt-4o-mini": True,
    "claude-code": True,
    "neo4j": True,
    _WINDOWS_PATH: False,
    _POSIX_PATH: False,
    _URL: False,
    "two words": False,
    "": False,
    "x" * 41: False,
}


def _env(monkeypatch, rows: dict):
    """Make the harness `.env` say exactly `rows`, and the shell say nothing
    of it - so what `supplied_env` reports is what the HARNESS adds."""
    from tools import provider_smoke as smoke
    monkeypatch.setattr(smoke, "load_env", lambda path: dict(rows))
    for name in rows:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("value", sorted(VALUES))
def test_a_recorded_value_can_never_be_a_location(value, monkeypatch):
    """**The safety property, and my own first cut failed it.**

    The run record is COMMITTED into the adopter's repository (item 121), so
    anything recorded here ships there - and the first cut recorded every
    non-secret value, which would have written an absolute machine path out of
    `.env` into somebody else's repo. That is item 57's defect and item 120's,
    and this repository's own private-coordinate guard cannot catch it,
    because it scans THIS tree.
    """
    _env(monkeypatch, {"SOME_SETTING": value})
    got = D.supplied_env()["SOME_SETTING"]
    assert (got == value) is VALUES[value], (value, got)
    if not VALUES[value]:
        assert got == D.NOT_RECORDED


def test_a_credential_is_redacted_by_its_NAME_however_short_its_value_is(
        monkeypatch):
    """**Why `secret_named` was factored out of `secret_values`.** That
    function asks *name says secret AND the value is long enough that
    redaction cannot mangle an ordinary word* - right for scrubbing text,
    wrong for deciding whether to record a value, because a short key is
    still a key."""
    _env(monkeypatch, {"SOME_API_KEY": "short", "SOME_SETTING": "openai"})
    got = D.supplied_env()
    assert got["SOME_API_KEY"] == D.REDACTED
    assert got["SOME_SETTING"] == "openai"


def test_the_name_rule_has_one_home():
    """**The weld (item 115), and this module exists because a copy of that
    rule drifted at birth.** `secret_values` must ANSWER through
    `secret_named` rather than carrying a second regex."""
    from tools import provider_smoke as smoke
    assert smoke.secret_named("SOME_TOKEN") and not smoke.secret_named("MODEL")
    assert smoke.secret_values({"SOME_TOKEN": "longenoughvalue"})
    assert not smoke.secret_values({"MODEL": "longenoughvalue"})


def test_only_what_the_harness_adds_is_claimed(monkeypatch):
    """A name the SHELL already carries is the operator's, not this file's -
    `runner_env` lets an exported variable win, so claiming it would be the
    record overstating what the harness did."""
    from tools import provider_smoke as smoke
    monkeypatch.setattr(smoke, "load_env",
                        lambda path: {"FROM_FILE": "openai",
                                      "FROM_SHELL": "openai"})
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.setenv("FROM_SHELL", "whatever-the-operator-exported")
    got = D.supplied_env()
    assert "FROM_FILE" in got and "FROM_SHELL" not in got


def test_the_plan_shows_what_the_runner_will_be_handed(repo, run_id, capsys,
                                                       monkeypatch):
    """**The composition, and it is the screen the walk of 2026-09-06 needed.**

    `.env` carried `GOOSE_PROVIDER=claude-code` while goose's own config and
    the runner's note both said OpenAI, so the runner called "goose" would
    have invoked Claude while every document said otherwise - and nothing
    would have shown it. `--plan` is the one screen an operator reads BEFORE
    deciding to spend, so what the harness will hand over belongs on it.
    """
    _env(monkeypatch, {"SOME_PROVIDER": "not-what-the-note-says",
                       "SOME_API_KEY": "a-real-looking-credential"})
    assert D.main(["--target", str(repo), "--run", run_id, "--plan"]) == 0
    out = capsys.readouterr().out
    assert "the harness supplies" in out
    assert "SOME_PROVIDER=not-what-the-note-says" in out
    assert "a-real-looking-credential" not in out
    assert f"SOME_API_KEY={D.REDACTED}" in out


def test_every_step_records_the_environment_it_ran_under(repo, run_id,
                                                         monkeypatch):
    """Item 105 recorded the resolved runner's NAME and nothing else, and a
    runner answers as its environment tells it to - so "who" was answerable
    from the run and "under what" was not."""
    _env(monkeypatch, {"SOME_PROVIDER": "openai"})
    found = D.step(repo, run_id, {}, run=answering())
    assert found["env"]["SOME_PROVIDER"] == "openai"


def test_it_forms_no_opinion_about_what_an_environment_means(monkeypatch):
    """**The bound item 104 ruled.** A capability gate naming models cuts
    against a harness that names no vendor, and so would this: it records what
    the harness supplied and judges none of it, so a provider nobody here has
    heard of is recorded exactly like one that is."""
    _env(monkeypatch, {"SOME_PROVIDER": "a-vendor-nobody-here-knows"})
    assert D.supplied_env() == {"SOME_PROVIDER": "a-vendor-nobody-here-knows"}


# ── T353: the driver reads the run's phase, it never defaults it ───────────────
# W280. Both driver doors read `state.get("phase", ledger.PHASES[0])`, so an
# unreadable label became a silent P0 - `--plan` planned from the wrong place and
# `_step_once` could route and SPEND against a phase the run is not at.
# `state.phase_of` refuses by name instead.

_ABSENT = object()


def _relabel(repo: Path, run_id: str, phase) -> None:
    """Corrupt the run's phase label while its state.json still loads."""
    path = ledger.run_dir(repo, run_id) / ledger.STATE_NAME
    state = json.loads(path.read_text(encoding="utf-8"))
    if phase is _ABSENT:
        state.pop("phase", None)
    else:
        state["phase"] = phase
    path.write_text(json.dumps(state), encoding="utf-8")


def test_plan_refuses_an_invalid_phase_label_not_report_no_phases(repo, run_id):
    """`plan_ahead` walked from `state.get("phase", P0)`. An invalid label matched
    no active phase, so the plan came back EMPTY - a walk of nothing reported as a
    walk."""
    _relabel(repo, run_id, "P99")
    with pytest.raises(ledger.Unreadable, match=r"records the phase 'P99'"):
        D.plan_ahead(repo, run_id)


def test_plan_refuses_a_missing_phase_label_not_default_to_p0(repo, run_id):
    """The silent half: a MISSING label became P0 and the plan walked from the
    start of the lifecycle rather than from where the run is."""
    _relabel(repo, run_id, _ABSENT)
    with pytest.raises(ledger.Unreadable, match=r"records the phase None"):
        D.plan_ahead(repo, run_id)


def test_a_step_never_spends_against_an_unreadable_phase(repo, run_id):
    """`_step_once` read `state.get("phase", P0)`, so a run whose phase could not
    be read was routed and could be SPENT against a fabricated P0. `phase_of`
    refuses before the runner is ever reached."""
    def boom(*a, **k):
        raise AssertionError("a runner was spawned against an unreadable phase")
    _relabel(repo, run_id, _ABSENT)
    with pytest.raises(ledger.Unreadable, match=r"records the phase None"):
        D._step_once(repo, run_id, {}, run=boom)


def test_a_step_names_an_invalid_phase_as_the_runs_not_via_by_phase(repo, run_id):
    """The invalid twin, and the one that pins THIS door: a present-but-invalid
    label was read straight into `by_phase`, whose StageError is about a phase
    ARGUMENT, not the run's own unreadable phase. `phase_of` names it as the
    run's, at the door, before routing or spend - so reverting this line to the
    old `.get(...)` default surfaces as `by_phase`'s error, not `Unreadable`."""
    def boom(*a, **k):
        raise AssertionError("a runner was spawned against an unreadable phase")
    _relabel(repo, run_id, "P99")
    with pytest.raises(ledger.Unreadable, match=r"records the phase 'P99'"):
        D._step_once(repo, run_id, {}, run=boom)
