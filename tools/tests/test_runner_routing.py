"""Pluggable per-phase agents: the binding layer. Item 105.

The socket (RUNNER_CONTRACT), the conformance evidence (`provider_smoke`) and
the judgment (gates) all existed; this file holds the BINDING that connects
them — who runs a phase, declared in the profile, floored in the constitution,
refused by name at phase entry, and recorded in the run's history so an audit
answers *which agent did P4 on run X* from the run folder alone.

The canonical example is the owner's own: a hermes agent, plugged in for
development only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import provider_smoke as smoke  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

BASE_PROFILE = (
    "stack: python\ngate_profile: solo\n"
    "phases: [intake, requirements, design, implementation, test, review, "
    "triage, closeout]\n"
    'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')

ROUTED = BASE_PROFILE + (
    "runners:\n"
    "  - name: hermes\n"
    '    command: "hermes exec --headless"\n'
    "    capabilities: tools\n"
    "    phases: [implementation]\n"
    '    note: "internal build agent"\n'
    "  - name: design-llm\n"
    '    command: "python http_runner.py"\n'
    "    capabilities: chat\n"
    "phase_runners:\n"
    "  implementation: hermes\n"
    "  design: design-llm\n")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


def repo_with(tmp_path: Path, profile: str, policy_text: str = "") -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    (r / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    if policy_text:
        (r / ".sdlc" / "policy.yml").write_text(policy_text, encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    return r


@pytest.fixture
def routed(tmp_path) -> Path:
    return repo_with(tmp_path, ROUTED)


# ── the declaration parses the way components: taught adopters to write ─────


def test_runners_declared_reads_the_block(routed):
    found = {r["name"]: r for r in P.runners_declared(routed)}
    assert set(found) == {"hermes", "design-llm"}
    assert found["hermes"]["command"] == "hermes exec --headless"
    assert found["hermes"]["capabilities"] == "tools"
    assert found["hermes"]["phases"] == ["implementation"]
    assert found["design-llm"]["capabilities"] == "chat"
    assert "phases" not in found["design-llm"], "an absent self-scope stays absent"


def test_phase_runner_map_reads_the_section(routed):
    assert P.phase_runner_map(routed) == {"implementation": "hermes",
                                          "design": "design-llm"}


def test_a_profile_with_no_routing_routes_nothing(tmp_path):
    r = repo_with(tmp_path, BASE_PROFILE)
    assert P.runners_declared(r) == []
    assert P.phase_runner_map(r) == {}
    for phase in ("implementation", "design", "review"):
        assert P.runner_for(r, phase) is None, \
            "an unmapped phase must keep today's behaviour"


def test_a_declaration_with_no_name_is_skipped_not_crashed(tmp_path):
    r = repo_with(tmp_path, BASE_PROFILE +
                  "runners:\n  - name:\n    capabilities: tools\n")
    assert P.runners_declared(r) == []


# ── the hermes case, verbatim ───────────────────────────────────────────────


def test_hermes_runs_implementation_and_only_implementation(routed):
    """The owner's example is the canonical one: a custom agent, plugged in
    for development only."""
    got = P.runner_for(routed, "implementation")
    assert got["name"] == "hermes" and got["capabilities"] == "tools"


def test_the_self_scope_is_a_bound_not_a_hint(tmp_path):
    text = ROUTED + "  review: hermes\n"        # route hermes outside its scope
    r = repo_with(tmp_path, text)
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, "review")
    assert "implementation" in str(exc.value) and "self-scope" in str(exc.value)


# ── refusals, each by name, each at resolve ─────────────────────────────────


def test_an_undeclared_name_is_refused_naming_both_sides(tmp_path):
    r = repo_with(tmp_path, BASE_PROFILE + "phase_runners:\n  design: ghost\n")
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, "design")
    assert "ghost" in str(exc.value)
    assert "none at all" in str(exc.value)


def test_a_capability_nobody_declared_is_refused_not_inferred(tmp_path):
    r = repo_with(tmp_path, BASE_PROFILE +
                  "runners:\n  - name: x\n"
                  '    command: "x run"\n'
                  "phase_runners:\n  design: x\n")
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, "design")
    assert "declared, never inferred" in str(exc.value)


@pytest.mark.parametrize("phase", sorted(P.ACT_PHASES))
def test_a_chat_runner_on_an_acting_phase_is_refused(tmp_path, phase):
    """A chat endpoint writes; it cannot act. Routing one to a phase whose
    artifacts cannot be produced by prose alone is refused BEFORE the phase
    starts, not discovered as a half-done phase nobody owns."""
    r = repo_with(tmp_path, BASE_PROFILE +
                  "runners:\n  - name: llm\n"
                  '    command: "python http_runner.py"\n'
                  "    capabilities: chat\n"
                  f"phase_runners:\n  {phase}: llm\n")
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, phase)
    assert "cannot act" in str(exc.value)


def test_a_chat_runner_on_a_document_phase_is_fine(routed):
    got = P.runner_for(routed, "design")
    assert got["name"] == "design-llm"


def test_act_phases_is_the_deliberate_set():
    """Frozen and pinned: widened with a reason, never by accident — and the
    document phases stay routable to an endpoint, which is the enterprise case
    the item exists for."""
    assert P.ACT_PHASES == ("implementation", "test", "review",
                            "release", "deploy", "verify")
    words = {s.name for s in S.STAGES}
    assert set(P.ACT_PHASES) <= words, "an ACT phase that is not a phase"


def test_the_org_floor_pins_the_set(tmp_path):
    r = repo_with(tmp_path, ROUTED,
                  policy_text='allowed_runners: ["claude"]\n')
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, "implementation")
    assert "allowed_runners" in str(exc.value) and "claude" in str(exc.value)


def test_an_empty_floor_means_any_declared_runner(tmp_path):
    r = repo_with(tmp_path, ROUTED, policy_text="allowed_runners: []\n")
    assert P.runner_for(r, "implementation")["name"] == "hermes"


def test_a_floor_naming_the_runner_lets_it_through(tmp_path):
    r = repo_with(tmp_path, ROUTED,
                  policy_text='allowed_runners: ["hermes", "design-llm"]\n')
    assert P.runner_for(r, "implementation")["name"] == "hermes"


# ── conformance: the smoke record gains its consumer ────────────────────────


def test_require_conformance_refuses_a_runner_with_no_green_row(tmp_path):
    r = repo_with(tmp_path, ROUTED, policy_text="require_conformance: true\n")
    with pytest.raises(P.RunnerError) as exc:
        P.runner_for(r, "implementation")
    assert "provider-smoke" in str(exc.value)


def test_a_green_smoke_row_satisfies_the_conformance_floor(tmp_path):
    """The evidence is the REAL record written by the real writer — not a
    hand-shaped row — so the producer and this consumer stay composed."""
    r = repo_with(tmp_path, ROUTED, policy_text="require_conformance: true\n")
    smoke.record(r, "hermes exec --headless", "green", [],
                 out_dir=tmp_path / "smoke", milestone="test", tier="agent")
    assert P.runner_for(r, "implementation")["name"] == "hermes"


def test_a_red_row_does_not_satisfy_it(tmp_path):
    r = repo_with(tmp_path, ROUTED, policy_text="require_conformance: true\n")
    smoke.record(r, "hermes exec --headless", "red", ["it broke"],
                 out_dir=tmp_path / "smoke", milestone="test", tier="agent")
    with pytest.raises(P.RunnerError):
        P.runner_for(r, "implementation")


def test_the_conformance_dial_defaults_off(routed):
    """Deliberate: gates judge whatever a runner leaves, so an unproven
    runner's output cannot pass a gate on its own — conformance saves a wasted
    run, it does not guard correctness."""
    assert P.DEFAULTS["require_conformance"] is False
    assert P.runner_for(routed, "implementation") is not None


# ── the audit: entering a phase records who runs it ─────────────────────────


def open_run(target: Path) -> str:
    from tools.sdlc import intake
    rec = intake.open_run(target, "title: a change\n\nkind: requirement\n\n"
                          "## What\n\nA small change.\n")
    return rec["run_id"]


def test_entering_a_routed_phase_records_the_runner_name(routed):
    run_id = open_run(routed)
    S.advance(routed, run_id, "P1", force=True)
    S.advance(routed, run_id, "P3", force=True)   # design -> design-llm
    st = ledger.load(routed, run_id)
    notes = [h.get("note", "") for h in st["history"]]
    assert any("runner: design-llm" in n for n in notes), notes


def test_entering_a_misrouted_phase_is_refused_by_name(tmp_path):
    text = ROUTED + "  review: hermes\n"          # outside hermes's self-scope
    r = repo_with(tmp_path, text)
    run_id = open_run(r)
    with pytest.raises(S.StageError) as exc:
        S.advance(r, run_id, "P6", force=False)
    assert "runner routing" in str(exc.value)
    assert "--force records the override" in str(exc.value)


def test_forcing_past_a_misroute_records_the_override(tmp_path):
    text = ROUTED + "  review: hermes\n"
    r = repo_with(tmp_path, text)
    run_id = open_run(r)
    S.advance(r, run_id, "P6", force=True)
    st = ledger.load(r, run_id)
    notes = " | ".join(h.get("note", "") for h in st["history"])
    assert "routing into P6" in notes


def test_an_unmapped_phase_advances_exactly_as_before(tmp_path):
    r = repo_with(tmp_path, BASE_PROFILE)
    run_id = open_run(r)
    S.advance(r, run_id, "P1", force=True)
    st = ledger.load(r, run_id)
    assert st["phase"] == "P1"
    assert not any("runner:" in h.get("note", "") for h in st["history"])


# ── the CLI question ────────────────────────────────────────────────────────


def test_the_cli_answers_who_runs_a_phase(routed, capsys):
    assert P.main(["--target", str(routed), "--who", "implementation"]) == 0
    out = capsys.readouterr().out
    assert "hermes" in out and "tools" in out


def test_the_cli_says_unmapped_rather_than_inventing(routed, capsys):
    assert P.main(["--target", str(routed), "--who", "closeout"]) == 0
    assert "unmapped" in capsys.readouterr().out


def test_the_cli_exits_one_on_a_refusal(tmp_path, capsys):
    r = repo_with(tmp_path, BASE_PROFILE + "phase_runners:\n  design: ghost\n")
    assert P.main(["--target", str(r), "--who", "design"]) == 1
    assert "ghost" in capsys.readouterr().out


# ── the templates teach it ──────────────────────────────────────────────────


def test_the_policy_template_carries_both_floors():
    body = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    assert "allowed_runners: []" in body
    assert "require_conformance: false" in body


def test_the_profile_template_documents_the_hermes_shape():
    """The example an adopter copies is the owner's own case."""
    body = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    assert "# runners:" in body and "# phase_runners:" in body
    assert "hermes" in body and "capabilities: tools" in body
    assert "http_runner.py" in body, "the endpoint runner is the chat example"


def test_the_template_policy_still_validates_with_the_new_keys():
    body = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    doc = P.parse(body)
    assert P.validate(doc) == []
    assert doc["allowed_runners"] == []
    assert doc["require_conformance"] in (False, "false")


# ── item 112: the forced-advance FIELD, on the routing path too ─────────────


def test_forcing_past_a_misroute_sets_the_forced_field(tmp_path):
    """`stage.advance` has TWO override paths — the artifact/gate check and the
    routing check — and a mutation removing the flag from this one survived
    until this test existed. One concept, one spelling, both places."""
    from tools.sdlc import metrics as M

    text = ROUTED + "  review: hermes\n"          # outside hermes's self-scope
    r = repo_with(tmp_path, text)
    run_id = open_run(r)
    S.advance(r, run_id, "P6", force=True)
    entry = ledger.load(r, run_id)["history"][-1]
    assert entry.get("forced") is True
    assert S.FORCED_NOTE in entry["note"], "the prose says WHAT was overridden"
    assert M.compute(r)["gates"]["forced_advances"]["value"] >= 1


def test_an_ordinary_advance_is_not_marked_forced(tmp_path):
    """The other half. Marking every advance forced would make the override
    count meaningless — and `metrics.forced_advances` is what an auditor reads
    to tell a clean record from one that was pushed through."""
    r = repo_with(tmp_path, BASE_PROFILE)
    run_id = open_run(r)
    S.advance(r, run_id, "P1")                    # nothing owed, no force
    entry = ledger.load(r, run_id)["history"][-1]
    assert "forced" not in entry, entry
