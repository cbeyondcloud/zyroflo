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
from tools.sdlc import stage as S  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

#: solo: G2 and G4 halt, G1/G3 do not — so a walk from P0 reaches P3's gate.
PROFILE = (
    "stack: python\ngate_profile: solo\n"
    "phases: [intake, requirements, design, implementation, test, review, "
    "triage, closeout]\n"
    'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n'
    "runners:\n"
    "  - name: writer\n"
    '    command: "fake-writer"\n'
    "    capabilities: chat\n"
    "  - name: builder\n"
    '    command: "fake-builder"\n'
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
    assert call["command"] == "fake-writer"
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
    five are the opposite of a missing value."""
    said = D.step(repo, run_id, {"favourite-colour": "blue"},
                  run=_step_with(monkeypatch, 0, "unused"))
    assert said["stop"] == D.REFUSED, said
    assert "states no value called" in said["why"]


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

    **FILED, NOT FIXED** (item 45's rule): the sentence they read is *G2 has no
    entry in the ledger*, so `approve.gate_state` reports an unreadable verdict
    as nobody having decided. Absent versus could-not-look, this project's most
    repeated distinction, inside the module that reads verdicts back.
    """
    D.drive(repo, run_id, {}, run=answering())
    (ledger.run_dir(repo, run_id) / "approvals.jsonl").write_text(
        "{not json at all\n", encoding="utf-8")

    again = counting()
    said = D.step(repo, run_id, {}, run=again)
    assert again.spawned == [], "a corrupt ledger line cost a paid runner call"
    assert said["stop"] == D.GATE, said
    assert "no entry in the ledger" in said["why"], (
        "the filed defect changed - an unreadable ledger is reported as an "
        "absent verdict, and this pin is what will say so when it is fixed")


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
