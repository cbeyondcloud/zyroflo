"""T443: custom-step readiness - the design admits one, proven, and no loader ships.

A step is a spec (`sdlc/STEP_SPEC.md`): front-matter contract, SOP body. The
twelve built-ins are written in it (test_stages holds them equal to `STAGES`).
Here: the parser's refusals, and a SYNTHETIC step - parsed from a spec, spliced
in `after: test` through `stage.register`, the one seam a future loader calls -
walked through the resolver, the run ledger, the approval ledger, `status` and
`merge_ready` with no module special-casing it.

It guards two real gaps, found while designing it and each re-introduced and
caught by the three end-to-end tests below: a spec's `gate: hard` entered
`GATES` as a BUILTIN, which only the regime makes hard - under `solo` a custom
hard stop would have been advisory - and nothing rebuilt the names that copy
the registry at import (`state.PHASES`, `approve.GATE_PHASE`), so a registered
step was one `state` would refuse to advance to.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import status as STATUS  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "dev@example.invalid"

LINT = """---
id: lint-report
name: lint-report
after: test
inputs: [50-tests.md]
outputs: [55-lint.md]
needs: none
gate: hard
---
# lint-report - run the linters and record what they said
"""


def _spec(**changes) -> str:
    lines = LINT.split("\n")
    for key, value in changes.items():
        lines = [f"{key}: {value}" if l.startswith(f"{key}:") else l for l in lines]
        if not any(l.startswith(f"{key}:") for l in lines):
            lines.insert(1, f"{key}: {value}")
    return "\n".join(lines)


# ── the parser ───────────────────────────────────────────────────────────────


def test_a_custom_spec_parses_to_the_record_the_registry_holds():
    step = ST.parse_step_spec(LINT)
    assert (step.phase, step.name, step.produces, step.reads, step.gate, step.needs) == (
        "lint-report", "lint-report", ("55-lint.md",), ("50-tests.md",),
        "G-lint-report", "")
    assert ST.spec_after(LINT) == "test"


@pytest.mark.parametrize("text, words", [
    ("id: x\n", "starts with a `---`"),
    ("---\nid: x\n", "never closed"),
    (_spec(outptus="[a.md]"), "unknown key 'outptus' - did you mean 'outputs'?"),
    (LINT.replace("outputs: [55-lint.md]\n", ""), "needs outputs"),
    (_spec(outputs="[]"), "outputs is empty"),
    (_spec(id="P12"), "P<n> is reserved"),
    (_spec(id="Lint_Report"), "lower-case words joined by hyphens"),
    (_spec(name="review", id="review-2"), "already exists"),
    (_spec(after="linting"), "after names 'linting', which is not a step"),
    (_spec(after="none"), "`after: none` is the first built-in step's alone"),
    (_spec(inputs="[60-review.md]"), "60-review.md is written by no step at or before 'test'"),
    (_spec(needs="gpu"), "needs 'gpu'"),
    (_spec(gate="G6"), "belongs to the built-in steps"),
    (_spec(inputs="50-tests.md"), "inputs must be a list"),
], ids=["no-open", "no-close", "misspelt", "no-outputs", "empty-outputs",
        "reserved-id", "bad-id", "duplicate-name", "dangling-after", "after-none",
        "unwritten-input", "unknown-capability", "numbered-gate", "bare-list"])
def test_the_parser_refuses_by_name(text, words):
    with pytest.raises(ST.StepSpecError, match=None) as caught:
        ST.parse_step_spec(text)
    assert words in str(caught.value), str(caught.value)


def test_the_parser_ships_unwired():
    """Readiness, not the feature: nothing outside `stage.py` and the tests
    calls the parser or the registration seam."""
    callers = [p.relative_to(ROOT).as_posix()
               for p in (ROOT / "tools").rglob("*.py")
               if "tests" not in p.parts and p.name != "stage.py"
               and ("parse_step_spec" in (text := p.read_text(encoding="utf-8"))
                    or "stage.register(" in text or "stages.register(" in text)]
    assert callers == [], callers


# ── the synthetic step, end to end ───────────────────────────────────────────


@pytest.fixture
def registered(monkeypatch):
    """Register the synthetic step, with every registry name restored after."""
    for mod, name in ((ST, "STAGES"), (ST, "NAMES"), (ST, "PHASES"), (ST, "GATES"),
                      (L, "PHASES"), (A, "GATE_PHASE"), (A, "GATES")):
        monkeypatch.setattr(mod, name, getattr(mod, name))
    step = ST.parse_step_spec(LINT)
    ST.register(step, ST.spec_after(LINT))
    return step


@pytest.fixture
def repo(tmp_path, registered) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "gate_profile: solo\nphases: [intake, requirements, design, implementation, "
        "test, lint-report, review, triage, closeout]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(r)], check=True, capture_output=True)
    return r


def test_the_step_takes_its_place_and_its_gate_is_hard_under_solo(repo, registered):
    pipe = ST.pipeline(repo)
    names = [s.name for s in pipe.steps]
    assert names[names.index("test") + 1] == "lint-report"
    gate = next(g for g in pipe.gates if g.id == "G-lint-report")
    assert (gate.after_phase, gate.origin) == ("lint-report", "spec")
    assert "G-lint-report" in ST.resolved_hard_gates(repo), \
        "a spec's `gate: hard` is hard in every regime, not dialled like a builtin"


def test_the_run_halts_at_the_step_and_status_names_its_gate(repo, registered):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    for phase in ("P1", "P3", "P4", "P5"):
        R.park(repo, rid, phase, status="running")
    R.park(repo, rid, "lint-report")
    held = ST.halted_at(repo, rid, "lint-report")
    assert held and "G-lint-report" in held[0], held
    with pytest.raises(ST.StageError, match="G-lint-report"):
        ST.advance(repo, rid, "P6")
    standing, _ = STATUS.standing_gates(repo)
    assert STATUS.waiting_on(L.load(repo, rid), standing, repo, rid) == "G-lint-report"


def test_approving_its_gate_opens_the_way_and_merge_ready_demands_it(repo, registered):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    for phase in ("P1", "P3"):
        R.park(repo, rid, phase, status="running")
    A.record(repo, rid, "G2", "approve", WHO)
    for phase in ("P4", "P5", "lint-report", "P6"):
        R.park(repo, rid, phase, status="running")
    R.park(repo, rid, "P7")
    A.record(repo, rid, "G4", "approve", WHO)
    problems = V.merge_ready(repo, rid)
    assert any("G-lint-report" in p for p in problems), problems
    A.record(repo, rid, "G-lint-report", "approve", WHO)
    assert V.merge_ready(repo, rid) == []


def test_registration_is_refused_twice_and_after_an_unknown_step(registered):
    with pytest.raises(ST.StepSpecError, match="already registered"):
        ST.register(registered, "test")
    other = registered._replace(phase="other", name="other")
    with pytest.raises(ST.StepSpecError, match="not a step"):
        ST.register(other, "nowhere")
