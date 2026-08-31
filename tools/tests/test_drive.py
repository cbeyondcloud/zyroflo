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
    assert "would spawn: writer" in out
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
