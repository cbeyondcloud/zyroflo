"""Item 121 — the requirements the harness enforced and the SOPs never stated.

Item 114's walk found three of item 33's class in ONE run: a phase instructed to
do something only run creation can do (F1), a merge gate needing the run folder
on the branch with no procedure saying to put it there (F9), and a deploy that
reads configuration from a branch nobody was told to carry it onto (O5).

They live in one file because they are one finding wearing three hats, and
because no existing file owns all of `stage`, `release` and three SOPs.

**Where a mechanism can carry the requirement it does; where only prose can, the
prose says why there is no check.** `6a00bfc` settled the shape: a path a caller
must get right is a path that will eventually be got wrong.

**Item 126 adds the other direction, and it is the same question.** Item 121
asked whether the SOPs state what the harness ENFORCES; item 126 asks whether
the harness supplies what the SOPs STATE. Walk 2 found it did not, in the file
item 121 had just edited: P0's SOP says `baseline` is read from the run and
that the prompt states it, while the value was declared on P5 alone. A second
file for the inverse of one question is the two-homes shape this project keeps
fixing, so both live here.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import intake as adapter  # noqa: E402
from tools.sdlc import release as REL  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402


def _git(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(where), *args],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: repo" + chr(10) + "gate_profile: solo" + chr(10)
        + 'test_command: "pytest -q"' + chr(10),
        encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    return root


def run_at(ws: Path, phase: str = "P5") -> str:
    """A run folder at `phase`, without going through intake - these tests are
    about what a PROMPT states, not about how a run is opened."""
    rid = "20260902-000000-000000"
    folder = ledger.runs_dir(ws) / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"run_id": rid, "phase": phase, "status": "running"}),
        encoding="utf-8")
    return rid

# ── item 121: three requirements the harness enforced and never passed on ───
#
# All three are item 33's recorded class — *a requirement the runner is never
# told* — found in ONE run by item 114's walk:
#
#   F1  P0's SOP said to run the baseline suite and record it. Only
#       `intake.open_run` writes that block, so the instruction was addressed
#       to somebody who could no longer act on it.
#   F9  The merge gate requires the run folder on the BRANCH. No SOP said so,
#       and it failed after a push and an open pull request.
#   O5  The deploy runs from the run's worktree, so repository config fixed on
#       master never reaches it.
#
# Where a mechanism can carry the requirement it does; where only prose can,
# the prose says so and says why there is no check.


def _intake_json(root: Path, run_id: str, baseline: dict | None) -> None:
    folder = ledger.run_dir(root, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    doc = {} if baseline is None else {"baseline": baseline}
    (folder / "00-intake.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_baseline_is_resolved_from_the_run_not_asked_of_a_caller(ws):
    """**F1.** The prompt asked a CALLER for P0's recorded pass count, and no
    caller downstream of P0 possesses it — `intake.open_run` is the only writer
    of that block. This is item 109's fix for `worktree`, one value along."""
    baseline = [v for s in ST.STAGES if s.phase == "P5"
                for v in s.values if v.key == "baseline"]
    assert baseline and baseline[0].source == "run", (
        "the prompt still asks a caller for a value only P0 can have written")


def test_supplying_the_baseline_is_refused_rather_than_ignored(ws):
    """The other half of a run-sourced value, which `resolve` already enforces:
    a supplied value nothing reads is the same defect as a value nobody
    supplies, arriving from the other side."""
    rid = run_at(ws, "P5")
    stage = [s for s in ST.STAGES if s.phase == "P5"][0]
    with pytest.raises(ST.StageError) as caught:
        ST.resolve(ws, stage, rid, {"baseline": "41"})
    assert "resolves baseline from the run" in str(caught.value)


def test_a_baseline_that_did_not_run_says_so_in_words_this_phase_can_act_on(ws):
    """**The sharpest half of F1, and it must not be propagated.**

    `intake`'s own note reads *"pass --baseline to execute it"* — sound advice
    at run creation and useless by the time a phase prompt is rendered, which
    is the defect one layer on. What a phase CAN do is stated instead, and
    whose act the remedy is.
    """
    rid = run_at(ws, "P5")
    _intake_json(ws, rid, {"ran": False, "exit_code": None, "tests": None,
                           "note": "recorded but NOT run - pass --baseline to "
                                   "execute it"})
    said = ST._baseline_for(ws, rid)
    assert "NOT measured" in said
    assert "OPERATOR" in said and "not this phase" in said
    assert "inventing a number" in said


def test_a_baseline_that_ran_states_what_it_measured(ws):
    """The control: the value must carry the number when there is one, or the
    phase is told nothing useful in the ordinary case."""
    rid = run_at(ws, "P5")
    _intake_json(ws, rid, {"ran": True, "exit_code": 0, "tests": 41,
                           "note": "baseline green"})
    said = ST._baseline_for(ws, rid)
    assert "exit 0" in said and "41 test(s)" in said and "baseline green" in said


def test_absent_and_unreadable_intake_are_different_sentences(ws):
    """Two different repairs: P0 never recorded one, versus the document is
    damaged. Folding them would hide which of the two happened."""
    rid = run_at(ws, "P5")
    folder = ledger.run_dir(ws, rid)
    (folder / "00-intake.json").unlink(missing_ok=True)
    absent = ST._baseline_for(ws, rid)
    (folder / "00-intake.json").write_text("{not json", encoding="utf-8")
    broken = ST._baseline_for(ws, rid)
    assert "is not there" in absent and "never recorded" in absent
    assert "could not be read" in broken and "repair the document" in broken
    assert absent != broken


def test_the_p0_sop_no_longer_instructs_a_baseline_it_cannot_record(ws):
    """The instruction was the part that was wrong, and the missing mechanism
    is missing ON PURPOSE: `intake` measures the baseline on the clean tree at
    run creation, so one recorded later describes a different tree."""
    text = (ST.HARNESS / ST.SOP_DIR / "P0-intake.md").read_text(encoding="utf-8")
    assert "Do NOT run the suite here" in text
    assert "describes a *different\n   tree*" in text or "different\n   tree" in text
    assert "re-open it with `--baseline`" in text


# ── F9: the run folder belongs on the branch ────────────────────────────────


def _repo_with_run(tmp_path: Path, run_id: str) -> Path:
    root = tmp_path / "pub"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text("scope: repo\n", encoding="utf-8")
    (root / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "base")
    folder = ledger.runs_dir(root) / run_id
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"run_id": run_id}),
                                       encoding="utf-8")
    _git(root, "checkout", "-q", "-b", f"sdlc/{run_id}")
    (root / "code.py").write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", "code.py")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "the change, without the run folder")
    return root


def test_a_branch_without_its_run_folder_is_refused_before_the_push(tmp_path):
    """**F9, and the point is WHERE it is caught.**

    The gate was already correct; it fired in CI, after a branch was published
    and a pull request opened on a public fork. Asking the same question here
    moves the refusal to before anything leaves the machine.
    """
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    said = REL.unpublished_run(root, rid, f"sdlc/{rid}")
    assert said and ".sdlc/runs/" + rid in said[0]
    assert "committed with the code it describes" in said[0]


def test_committing_the_run_folder_clears_the_refusal(tmp_path):
    """The control. A check that never passes is one somebody deletes."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "carry the run folder")
    assert REL.unpublished_run(root, rid, f"sdlc/{rid}") == []


def test_the_check_is_silent_when_it_cannot_tell(tmp_path):
    """Could-not-look is not a refusal. A directory that is not a checkout, or
    a branch git does not know, is somebody else's error — blocking a push on
    it would be this check inventing one."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    assert REL.unpublished_run(root, rid, "no-such-branch") == []
    assert REL.unpublished_run(tmp_path / "not-a-repo", rid, "main") == []


def test_push_problems_asks_the_question_before_the_push(tmp_path):
    """Composed, not merely present. `push` refuses first and pushes second, so
    the check has to be reachable THROUGH `push_problems` — the producer and
    the consumer, which is this project's most-found gap."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    said = REL.push_problems(root, rid, f"sdlc/{rid}")
    assert any("does not carry" in p for p in said), said


def test_the_release_sop_states_the_step_and_names_the_hole_in_the_check(tmp_path):
    """The mechanism has a hole a test cannot close: P8 permits a
    `vcs.push_command` the runner invokes itself, which never reaches
    `release.push`. Both the step and the reason are in the SOP."""
    text = (ST.HARNESS / ST.SOP_DIR / "P8-release.md").read_text(encoding="utf-8")
    assert "carries the run folder before publishing" in text
    assert "goes around that check" in text
    assert "git ls-tree" in text


def test_the_implementation_sop_says_what_belongs_in_the_commit(tmp_path):
    """P4 committed code per tracker row and never said the run folder is part
    of the change. Discovering a forgotten `git add` at CI costs a published
    branch."""
    text = (ST.HARNESS / ST.SOP_DIR / "P4-implementation.md").read_text(
        encoding="utf-8")
    assert "The run folder is part of the change" in text


def test_the_deploy_sop_states_the_branch_requirement_and_why_nothing_checks(tmp_path):
    """**O5.** Prose, deliberately: a branch differing from its base is the
    NORMAL case, so a check would fire on every run and be switched off — the
    shape `prepass.py`'s own docstring names. The SOP says the requirement and
    says why there is no mechanism."""
    text = (ST.HARNESS / ST.SOP_DIR / "P9-deploy.md").read_text(encoding="utf-8")
    assert "carries the deploy configuration you expect" in text
    assert "Nothing checks this, deliberately" in text
    assert "switched off" in text


# ── item 126: the harness must supply what the SOP says it states ───────────
#
# Walk 2's W3. P0's SOP has said since item 36 that "`baseline` states what P0
# actually recorded, read from the run itself" and that step 3 should "read
# what was recorded (the prompt states it)" — while `Value("baseline", ...)`
# was declared on P5 ALONE, so P0's prompt could never state it.
#
# The runner behaved perfectly. It looked, did not find it, and followed the
# SOP's next paragraph exactly: tell the operator to close the run and re-open
# it with `--baseline`. The run's baseline had been recorded correctly — build
# green, test command, exit codes, item 111's separate build baseline, all of
# it — so the procedure's advice was to throw away a run that was fine.

INPUTS = "## Inputs"

SPEC126 = ("# The shortener refuses valid URLs\n\n"
           "A host with a capital letter is refused; so is the `museum` TLD.\n")


def _inputs_section(text: str) -> str | None:
    """An SOP's own statement of what the prompt hands over, or None.

    **Scoped to a DECLARING SECTION rather than to the whole document**, which
    is item 100's rule and item 123's second helping of it: a reader that takes
    every backticked token as a claim reports a document's ordinary
    cross-references as defects, and the only way past is to write worse
    documents. `## Inputs` is where an SOP says what the prompt carries; a
    backticked word in a step is prose about a concept, and a runner has no
    reason to read it as a promise about the value block.
    """
    lines = text.splitlines()
    start = next((n for n, ln in enumerate(lines)
                  if ln.strip().lower() == INPUTS.lower()), None)
    if start is None:
        return None
    end = next((n for n in range(start + 1, len(lines))
                if lines[n].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def unsupplied(stage: ST.Stage, section: str) -> list[str]:
    """Prompt values this section names that this phase's prompt cannot state.

    **One direction only, deliberately.** A value the contract states and the
    SOP never mentions is not a defect — the prompt states it verbatim, so the
    runner has it, and P5 names none of its four. The defect is the other way
    round: a procedure promising something that is not there.
    """
    every = {v.key for s in ST.STAGES for v in s.values}
    supplied = {v.key for v in ST.COMMON} | {v.key for v in stage.values}
    named = set(re.findall(r"`([a-z][a-z0-9-]+)`", section)) & every
    return sorted(named - supplied)


def _declared_by(key: str) -> list[str]:
    return sorted({s.phase for s in ST.STAGES for v in s.values if v.key == key})


@pytest.fixture
def measured(tmp_path: Path) -> Path:
    """A repository whose baseline command is real and instant.

    `allow_dirty` at the call sites because these tests are about what the
    PROMPT states; intake's clean-tree refusal is item 31's and is held there.
    """
    root = tmp_path / "measured"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "schema_version: 1" + chr(10) + "scope: repo" + chr(10)
        + "gate_profile: solo" + chr(10) + 'test_command: "exit 0"' + chr(10),
        encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    return root


def test_no_sop_names_a_prompt_value_its_phase_cannot_supply():
    """**The derived guard**, and the mechanism is worth more than the fix.

    Every SOP against its own phase's contract, so a procedure promising a
    value the prompt cannot carry fails here by name rather than in front of a
    runner somebody has already paid for. A roster of known-good pairs would go
    stale the first time a thirteenth phase or a fourth value appeared.
    """
    looked, problems = 0, []
    for stage in ST.STAGES:
        sop = ST.HARNESS / ST.SOP_DIR / f"{stage.phase}-{stage.name}.md"
        section = _inputs_section(sop.read_text(encoding="utf-8"))
        assert section is not None, (
            f"{stage.phase}'s SOP has no '{INPUTS}' section, so this sweep would "
            f"read nothing and pass — which is how a guard stops guarding")
        looked += 1
        problems += [f"{stage.phase} names `{k}`, declared only by "
                     f"{_declared_by(k)}" for k in unsupplied(stage, section)]
    assert looked == len(ST.STAGES), "the sweep did not reach every phase"
    assert problems == [], problems


def test_the_sweep_reports_the_defect_the_walk_actually_hit():
    """**The positive control, and it is the historical defect itself** — P0's
    real SOP against P0's real contract with `baseline` taken back out.

    Item 115's lesson: a guard nobody can watch fail is a guard nobody has
    checked, and four of its seven mutations survived for exactly that reason.
    """
    p0 = ST.by_phase("P0")
    before = p0._replace(
        values=tuple(v for v in p0.values if v.key != "baseline"))
    text = (ST.HARNESS / ST.SOP_DIR / "P0-intake.md").read_text(encoding="utf-8")
    assert unsupplied(before, _inputs_section(text)) == ["baseline"]


def test_a_value_named_outside_the_inputs_section_is_not_read_as_a_promise():
    """The scope decision, pinned where it can fail rather than left in a
    comment. P2 and P6 both write `pack-out` in their steps, and a step is
    prose about a concept — reading prose as a claim is what made `defect.py`
    file seven false obligations and `adr.py` name three sibling artifacts as
    files a change would touch."""
    doc = ("## Inputs" + chr(10) * 2 + "```" + chr(10) + "stage.py --prompt"
           + chr(10) + "```" + chr(10) * 2 + "## Steps" + chr(10) * 2
           + "1. Write the pack to `pack-out`." + chr(10))
    assert ST.by_phase("P3").values == (), "P3 gained values; pick another phase"
    assert unsupplied(ST.by_phase("P3"), _inputs_section(doc)) == []


def test_an_sop_with_no_inputs_section_is_a_refusal_not_an_empty_answer():
    """**A mutation survivor, and it was a real gap.** Every SOP carries an
    `## Inputs` section today, so the sweep's `is not None` assertion had
    nothing anywhere proving it can fire — and a reader answering `""` for a
    document whose section it could not find is a guard that has quietly
    stopped guarding, while still printing like a pass.

    Absent is not measured-zero. The same distinction items 110, 118 and 124
    each had to make, arriving here in the reader rather than in a count.
    """
    steps_only = "# P0" + chr(10) * 2 + "## Steps" + chr(10) * 2 + "1. Go."
    assert _inputs_section(steps_only) is None

    present = "## Inputs" + chr(10) * 2 + "It names nothing at all."
    assert _inputs_section(present) == present, (
        "a section that exists and names nothing must still read as a section")


def test_p0_is_told_the_baseline_its_own_sop_says_the_prompt_states(ws):
    """The fix, in one line. Declared on P0 rather than struck from the SOP,
    because intake MEASURES the baseline at run creation and reporting it is
    what P0 is for — the direction item 121 took making this value run-sourced.
    """
    rid = run_at(ws, "P0")
    assert "baseline" in ST.resolve(ws, ST.by_phase("P0"), rid, {})


def test_the_p0_prompt_states_what_intake_actually_measured(measured):
    """**The composition this defect existed for want of.**

    `intake.baseline` had tests and `render_prompt` had tests and nothing ran
    one THROUGH the other — the producer/consumer gap this project has found
    more often than any other class, and the reason P0 could tell an operator
    to throw away a run whose baseline was recorded correctly.
    """
    rec = adapter.open_run(measured, SPEC126, allow_dirty=True,
                           run_baseline=True)
    recorded = json.loads(
        (ledger.run_dir(measured, rec["run_id"]) / adapter.INTAKE_JSON)
        .read_text(encoding="utf-8"))["baseline"]
    assert recorded["ran"] and recorded["exit_code"] == 0, recorded

    text = ST.render_prompt(measured, rec["run_id"], "P0")
    line = next((ln for ln in text.splitlines() if ln.startswith("baseline: ")),
                None)
    assert line is not None, "P0's prompt states no baseline at all"
    assert "exit 0" in line, line


def test_a_p0_prompt_names_an_unmeasured_baseline_rather_than_omitting_it(measured):
    """The walk's own case, and the distinction it could not draw. A value that
    is ABSENT and a value that is UNSTATED call for opposite acts — re-open the
    run, versus report a harness defect — and an absence that is NAMED is one a
    phase can report instead of guessing at."""
    rec = adapter.open_run(measured, SPEC126, allow_dirty=True)
    line = next(ln for ln in ST.render_prompt(measured, rec["run_id"], "P0")
                .splitlines() if ln.startswith("baseline: "))
    assert "NOT measured" in line
    assert "OPERATOR" in line, "the sentence must say whose act the repair is"


def test_supplying_the_baseline_at_p0_is_refused(ws):
    """Item 109's rule for the FIFTH time, after `worktree`, `baseline` at P5,
    `pack-out` and `spec`: a supplied value nothing reads is the same defect as
    a value nobody supplies, arriving from the other side."""
    rid = run_at(ws, "P0")
    with pytest.raises(ST.StageError, match="resolves baseline from the run"):
        ST.resolve(ws, ST.by_phase("P0"), rid, {"baseline": "41"})


def test_the_p0_sop_tells_the_runner_how_to_tell_absent_from_unstated():
    """The half the mechanism cannot carry. Once the prompt states a baseline,
    the two failures a runner can meet are a value saying NOT measured and no
    value at all — and on walk 2 it met the second and acted on the first."""
    flat = " ".join((ST.HARNESS / ST.SOP_DIR / "P0-intake.md")
                    .read_text(encoding="utf-8").split())
    assert "says which case you are in" in flat
    assert "that is a defect in the harness and not a run opened without one" \
        in flat


# ── item 128: an SOP step must hand over a command, not describe one ────────
#
# Walk 3's W11, and it is the same family as item 126: item 121 asked whether
# the SOPs state what the harness ENFORCES, item 126 whether the harness
# supplies what the SOPs STATE, and this asks whether what an SOP states can
# actually be RUN.
#
# P2 was the only SOP with steps that named an operation and no command line,
# and both of its two cost the same paid call. Step 1 said "the `graph-build`
# workflow owns freshness" — a document, not a command — and the runner went
# hunting for a `graph-build.py` that does not exist. Step 2 gave the `--diff`
# invocation in full and left the seed case as prose, so the runner invented an
# absolute path and `pack.py` answered about a component nobody had written.


def _p2() -> str:
    return (ST.HARNESS / ST.SOP_DIR / "P2-scoping.md").read_text(encoding="utf-8")


def test_the_p2_sop_names_the_command_that_answers_freshness():
    """Every other step in this SOP gives a command line; step 1 named a
    workflow. The `graph-build` workflow still owns the POLICY - what changed is
    that the step now says which command answers the question."""
    flat = " ".join(_p2().split())
    assert "build.py --target <repo> --check-fresh" in flat
    assert "Exit 2 means stale and is not a stop" in flat, (
        "a runner reading 2 as fatal halts on a graph this SOP explicitly "
        "allows scoping over")


def test_the_p2_sop_shows_the_seed_form_and_not_only_the_diff_form():
    """The defect in one line: the `--diff` invocation was given in full and
    the case a runner actually meets at P2 - there is no diff until P4 - was
    left as *seed from the files the requirements name*."""
    flat = " ".join(_p2().split())
    assert "--seed <component>/<path inside it>" in flat
    assert "never an absolute path" in flat


def test_the_p2_sops_own_seed_example_is_readable_by_the_reader_it_teaches():
    """**Item 123's rule, and the crossing this item exists for.**

    Enforcing a form the procedure never states is item 33's class; teaching a
    form the reader refuses is the same defect from the other side. So the
    SOP's own placeholder is LIFTED FROM THE DOCUMENT rather than retyped here,
    filled in, and put through `pack.py`'s real reader.
    """
    from tools.graph.pack import seed_problem

    example = next(ln for ln in _p2().splitlines() if "--seed " in ln)
    shape = example.split("--seed ", 1)[1].split(" --", 1)[0]
    assert shape == "<component>/<path inside it>", shape

    filled = (shape.replace("<component>", "orders-api")
                   .replace("<path inside it>", "src/main/java/Main.java"))
    assert seed_problem(filled, {"orders-api"}) == "", (
        "the SOP teaches a seed shape its own reader refuses")
    # Built rather than written: the leak guard bans a literal
    # drive-letter path, and this item is about that very shape.
    absolute = chr(68) + ":/repo/src/Main.java"
    assert seed_problem(absolute, {"orders-api"}) != "", (
        "the shape the SOP forbids must actually be refused, or the paragraph "
        "is advice with nothing behind it")


def test_every_command_a_p2_step_shows_names_a_cli_that_ships():
    """The narrow, checkable half of W11. Not a general command-line grammar -
    see the ruling in `test_stages.py`, which measured that and rejected it -
    just: a `.py` shown in a fenced block of THIS SOP must be a file that
    exists, because these two lines are what a runner copies."""
    shipped = {p.name for p in (ST.HARNESS / "tools").rglob("*.py")}
    cited = set(re.findall(r"([a-z_]+\.py)", _p2()))
    assert cited, "the sweep found no command at all - it would pass vacuously"
    assert cited <= shipped, f"{sorted(cited - shipped)} ships nowhere"


# ── item 131: no shipped command may --set a value the CLI refuses ──────────
#
# `resolve` refuses a supplied run-sourced value by design (item 109's rule),
# and two shipped documents told a reader to pass one: P5's Inputs block and
# `sdlc_templates/SKILL.md`'s P4 example - the one command an adopter's own
# skill file hands them. Both spawned to exit 2, so a runner following either
# verbatim could not render the prompt at all.
#
# **Item 126's `unsupplied` cannot see this and should not**: it answers a
# different question - does the SOP NAME a value the phase's prompt cannot
# state - and the prompt states run-sourced values perfectly well. This is the
# second question, and it needs its own reader.


def _fenced_lines(text: str) -> set[int]:
    """The line numbers inside a ``` fence.

    **This is a DECLARATION, and that is the whole reason this guard exists at
    all.** Item 128 measured a command-line guard over SOP prose and rejected
    it: four findings, four false positives, every one from reading a sentence
    as an instruction. Its ruling named what would overturn that - *"a
    runnable-command GRAMMAR the SOPs actually declare - a fenced block marked
    as a command, say - so the reader is reading a DECLARATION instead of
    guessing"*. The fence exists and is used consistently, and measured on the
    real tree it separates two real commands from two correct prose
    explanations with ZERO false positives. So item 128's ruling is amended on
    its own terms rather than contradicted.
    """
    inside, found = False, set()
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            found.add(n)
    return found


def run_sourced_sets(text: str) -> list[str]:
    """Every `--set <run-sourced key>=` inside a fenced command in this text."""
    keys = {v.key for s in ST.STAGES for v in s.values if v.source == "run"}
    keys |= {v.key for v in ST.COMMON}
    inside = _fenced_lines(text)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if n not in inside:
            continue
        out += [f"line {n}: --set {m}=" for m in
                re.findall(r"--set\s+([a-z][a-z0-9-]*)=", line) if m in keys]
    return out


def test_no_shipped_command_tells_anybody_to_set_a_run_sourced_value():
    """The derived guard. `resolve` refuses these by name, so a document that
    hands one over is a runner that cannot start - and this swept the SOPs, the
    templates and the workflows rather than the two files a review happened to
    look at, which is how it found two more than the review did."""
    looked, offenders = 0, []
    for root in ("sdlc", "sdlc_templates", "workflows"):
        for path in sorted((ST.HARNESS / root).rglob("*.md")):
            looked += 1
            for said in run_sourced_sets(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name} {said}")
    assert looked >= 15, f"only {looked} document(s) swept"
    assert offenders == [], offenders


def test_prose_explaining_the_refusal_is_not_read_as_an_instruction():
    """**The boundary, and it is the reason item 128 rejected this guard once.**
    P2 and P6 both say *"Passing `--set pack-out=` is refused rather than
    ignored"* - which is the harness explaining itself, not telling anybody to
    do it. A reader that could not tell those apart would push the documents to
    get worse, which is `defect.py`'s and `adr.py`'s recorded cost."""
    for phase, name in (("P2", "P2-scoping.md"), ("P6", "P6-review.md")):
        text = (ST.HARNESS / ST.SOP_DIR / name).read_text(encoding="utf-8")
        assert "--set pack-out=" in text, f"{name} no longer explains the refusal"
        assert run_sourced_sets(text) == [], (
            f"{name}'s prose is being read as a command")


def test_the_set_guard_can_fail():
    """A guard nobody can watch fail is a guard nobody has checked."""
    planted = ("Run it:" + chr(10) + "```" + chr(10)
               + "stage.py --phase P5 --prompt --set baseline=12" + chr(10)
               + "```" + chr(10) + "and never `--set worktree=<dir>` in prose."
               + chr(10))
    assert run_sourced_sets(planted) == ["line 3: --set baseline="]


# ── item 132: a phase must be handed the documents its procedure reads ───────
#
# **Walk 4 found this and it cost a paid runner.** P2's SOP step 2 said to seed
# the impact pack from "a file the requirements name". P2's prompt stated
# `phase, run, target, sop, artifacts-out, component, pack-out` and named no
# document at all — so goose located the harness, ran the freshness check,
# understood the `COMPONENT/RELPATH` seed form item 128 had just taught it, and
# then asked a human which file to seed from and exited 0.
#
# It is item 125's defect one phase along. Item 125 gave P0 and P1 the spec and
# made P1's Inputs name its documents; P2 needs the REQUIREMENTS and was not
# carried along. Item 126's guard cannot see it, as item 126's own row
# predicted — that sweep reads `## Inputs` for VALUE KEYS, and this SOP named
# no key and no document.
#
# Measured across all twelve before designing the fix: six phases name the
# document they read, in `## Inputs` and NOWHERE ELSE, and P2 alone described
# one without naming it. So the `## Inputs` scope item 126 established carries
# this guard unchanged.

DOCUMENT_IN_PROSE = re.compile(r"`([0-9A-Z]{2}-[a-z0-9-]+\.(?:md|json|patch))`")


def _owed_by(stage: ST.Stage) -> set[str]:
    """Everything this phase writes — produced, conditional, evidence or after
    its gate. An SOP naming its OWN artifact is not reading somebody else's,
    which is the whole distinction `reads` is about."""
    return (set(stage.produces) | set(stage.conditional)
            | set(stage.evidence or ()) | set(stage.after_gate or ()))


def undeclared_reads(stage: ST.Stage, section: str) -> list[str]:
    """Documents this section names that the phase neither writes nor declares.

    **One direction only, and for item 126's reason.** A document declared in
    `reads` that the SOP never mentions is not a defect — the prompt states it
    verbatim with its status, so the runner has it. The defect is the other way
    round: a procedure pointing at a document the prompt never hands over.
    """
    named = set(DOCUMENT_IN_PROSE.findall(section))
    return sorted(named - _owed_by(stage) - set(stage.reads))


def test_every_document_an_sop_names_as_an_input_is_declared_in_reads():
    """**The derived guard.** Every SOP's `## Inputs` against its own phase's
    contract, so a procedure pointing at a document the prompt does not carry
    fails here by name rather than in front of a runner somebody has paid for.

    A roster of known-good pairs would go stale the first time a thirteenth
    phase or a ninth document appeared — which is item 126's argument, and the
    reason this sweep lives beside it rather than in a file of its own.
    """
    looked, problems = 0, []
    for stage in ST.STAGES:
        sop = ST.HARNESS / ST.SOP_DIR / f"{stage.phase}-{stage.name}.md"
        section = _inputs_section(sop.read_text(encoding="utf-8"))
        assert section is not None, (
            f"{stage.phase}'s SOP has no '{INPUTS}' section, so this sweep "
            f"would read nothing and pass — which is how a guard stops guarding")
        looked += 1
        problems += [f"{stage.phase}'s Inputs names {d} and its contract does "
                     f"not declare it in reads" for d in
                     undeclared_reads(stage, section)]
    assert looked == len(ST.STAGES), "the sweep did not reach every phase"
    assert problems == [], problems


def test_the_reads_sweep_reports_the_defect_walk_4_hit():
    """**The positive control, and it is this item's own fix taken back out.**

    P2's real SOP against P2's real contract with `reads` emptied. Item 115's
    lesson: a guard nobody can watch fail is a guard nobody has checked, and
    four of its seven mutations survived for exactly that reason.
    """
    p2 = ST.by_phase("P2")
    text = (ST.HARNESS / ST.SOP_DIR / "P2-scoping.md").read_text(encoding="utf-8")
    assert undeclared_reads(p2, _inputs_section(text)) == []
    before = p2._replace(reads=())
    assert undeclared_reads(before, _inputs_section(text)) == ["10-requirements.md"]


def test_every_document_a_phase_reads_is_written_by_some_phase():
    """The other direction: a `reads` entry nothing produces is a pointer to a
    document that cannot exist, which is worse than no pointer at all."""
    orphans = []
    for stage in ST.STAGES:
        for name in stage.reads:
            if ST.produced_by(name) is None:
                orphans.append(f"{stage.phase} reads {name}, which no phase writes")
    assert orphans == [], orphans


def test_a_reads_entry_nothing_writes_is_caught():
    """The positive control for the second direction."""
    assert ST.produced_by("10-requirements.md").phase == "P1"
    assert ST.produced_by("ZZ-nobody-writes-this.md") is None


def test_no_phase_declares_that_it_reads_its_own_artifact():
    """`reads` is about somebody ELSE's document. A phase listing its own would
    tell a runner to open a file it is there to write."""
    for stage in ST.STAGES:
        overlap = sorted(set(stage.reads) & _owed_by(stage))
        assert overlap == [], f"{stage.phase} reads what it writes: {overlap}"


def test_the_p2_prompt_names_the_document_its_own_sop_seeds_from(ws):
    """**The historical defect, stated as the harness behaviour it was.**

    Red at the pre-item SHA: P2's rendered prompt contained no document name of
    any kind, while its SOP told the runner to seed from one.
    """
    (ws / ".sdlc" / "graph").mkdir(parents=True, exist_ok=True)
    rid = run_at(ws, "P2")
    prompt = ST.render_prompt(ws, rid, "P2", {"component": "svc"})
    assert "10-requirements.md" in prompt, (
        "the phase told to seed from the requirements is not told they exist")
    assert "READS" in prompt


def test_a_document_the_profile_will_never_produce_is_named_as_such(ws):
    """**Item 125's rule from the other side, and the case is not hypothetical.**

    The shipped default `phases[]` omits scoping, so under the profile every
    adopter is handed `20-scope.md` is written by nobody — and P3's SOP names
    it. A runner told to read it and not told that hunts for a document nothing
    will ever write.
    """
    rid = run_at(ws, "P3")
    said = ST.reads_status(ws, rid, "20-scope.md")
    assert "NOT produced by this run" in said
    assert "P2 (scoping)" in said
    assert "not an active phase" in said


def test_not_written_yet_is_a_different_sentence_from_nobody_writes_it(ws):
    """Three situations, three repairs — which is why they are three sentences
    and not one blank. `10-requirements.md` IS produced by an active phase, so
    its absence at P2 means P1 has not run, not that nothing will run it."""
    rid = run_at(ws, "P2")
    said = ST.reads_status(ws, rid, "10-requirements.md")
    assert "not written yet" in said
    assert "P1 (requirements)" in said
    assert "NOT produced by this run" not in said


def test_a_present_document_says_where_it_is(ws):
    """A path, not a bare 'yes' — the runner has to open it."""
    rid = run_at(ws, "P2")
    folder = ledger.run_dir(ws, rid)
    (folder / "10-requirements.md").write_text("the requirements",
                                               encoding="utf-8")
    said = ST.reads_status(ws, rid, "10-requirements.md")
    assert said.startswith("present at ")
    assert str(folder / "10-requirements.md") in said


def test_the_shipped_default_profile_really_does_omit_scoping():
    """The fact the absent case rests on, pinned where it can fail. If scoping
    ever joins the default, this test says so and the sentence above needs
    re-reading rather than silently describing a case that no longer exists."""
    template = (ST.HARNESS / "sdlc_templates" / "profile.yml").read_text(
        encoding="utf-8")
    line = next(ln for ln in template.splitlines() if ln.startswith("phases:"))
    assert "scoping" not in line, line
    assert "scoping" not in ST.SOLO_DEFAULT


def test_the_five_document_names_have_one_home_each():
    """Item 94's rule, and this item is why it applies here now. `reads` spells
    each of these a second time, so a bare literal in both `produces` and
    `reads` would be two decisions that agree today. Held against the contract
    rather than by counting literals, which a comment could satisfy."""
    for const, phase in ((ST.REQUIREMENTS, "P1"), (ST.SCOPE, "P2"),
                         (ST.DESIGN, "P3"), (ST.REVIEW, "P6"),
                         (ST.DEPLOY_PLAN, "P9")):
        assert ST.produced_by(const).phase == phase
    assert ST.by_phase("P2").reads == (ST.REQUIREMENTS,)
    assert ST.by_phase("P3").reads == (ST.REQUIREMENTS, ST.SCOPE)


def test_a_profile_that_cannot_be_read_says_so_rather_than_guessing(ws):
    """**Could-not-look is its own answer here as everywhere else**, and the
    first cut of this function got it wrong.

    Deciding whether a producing phase is active means reading the profile. If
    that read fails, the not-written-yet sentence would claim the phase IS
    active and merely has not run — a claim this branch is in no position to
    make. Item 117 made the profile REFUSE what it cannot read, and a tab is
    one of the things it refuses, so this drives the real refusal rather than a
    stubbed exception.

    Found by the mutation pass before a single mutation ran: two identical
    returns meant one anchor matched twice.
    """
    (ws / ".sdlc" / "profile.yml").write_text(
        "scope: repo" + chr(10) + "components:" + chr(10)
        + chr(9) + "- name: svc" + chr(10),
        encoding="utf-8")
    rid = run_at(ws, "P2")
    said = ST.reads_status(ws, rid, "10-requirements.md")
    assert "could not be read" in said
    assert "P1 (requirements) writes it" in said
    assert "not written yet" not in said


def test_the_unreadable_profile_case_is_reached_by_a_real_refusal(ws):
    """The control for the test above: prove the profile really is refused, so
    the assertion is not passing because some unrelated thing raised."""
    (ws / ".sdlc" / "profile.yml").write_text(
        "scope: repo" + chr(10) + "components:" + chr(10)
        + chr(9) + "- name: svc" + chr(10),
        encoding="utf-8")
    with pytest.raises(Exception) as caught:
        ST.active(ws)
    assert "tab" in str(caught.value).lower(), str(caught.value)


def test_a_document_no_phase_writes_is_reported_rather_than_crashing(ws):
    """**The mutation pass found this branch held by nothing.**

    `produced_by` had a test and `reads_status` had tests and nothing composed
    them, so removing this guard left every test green — the producer/consumer
    gap this project has found more often than any other, in the item that
    exists because a phase was not handed a document.

    The guard above keeps every real `reads` entry pointing at a phase that
    writes it, so nothing in the shipped contract reaches here today. That is
    a property of the contract, not of this function, and a function that
    raises AttributeError the day the contract changes is not defensive.
    """
    rid = run_at(ws, "P2")
    said = ST.reads_status(ws, rid, "ZZ-nobody-writes-this.md")
    assert "no phase in this contract produces it" in said
    assert "carry on" in said
