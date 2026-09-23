"""T440 — the single pipeline registry.

The lifecycle's phase order, its gate→phase bindings, and its merge gate used to
be spelled in six-plus places that a human kept in lockstep. This pins the
collapse: one ordered list (`state.PHASES`), one gate→phase binding
(`stage.STAGES[].gate`, surfaced as `stage.GATES`), one merge-gate name
(`policy.MERGE_GATE`), and one resolver (`stage.pipeline`). Everything else
DERIVES, and `test_no_shipped_module_restates_a_phase_or_gate_literal` is the
guard that keeps a seventh spelling from re-appearing.

`stage.pipeline` is behaviour-additive here: it is the seam T441's user-driven
pipeline and T450's diagram move onto; T440 changes no existing path.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tools.sdlc import approve, policy, slice as sl, stage, state, status, validate
from tools.sdlc import adr, defect, deploy, release, suite, verify

SDLC = Path(stage.__file__).resolve().parent


# ── the resolver ─────────────────────────────────────────────────────────────


def test_pipeline_default_resolution(tmp_path):
    """The golden: a bare workspace takes the solo default, runs eight steps, and
    stands three gates. The measured shape today, pinned so a later change to the
    resolver has to declare itself here."""
    p = stage.pipeline(tmp_path)
    assert [s.phase for s in p.steps] == \
        ["P0", "P1", "P3", "P4", "P5", "P6", "P7", "P11"]
    assert [(g.id, g.after_phase, g.merge) for g in p.gates] == \
        [("G2", "P3", False), ("G3", "P4", False), ("G4", "P7", True)]
    # scoping/release/deploy/verify are absent because the default omits them,
    # NOT because of a missing capability — the two reasons stay distinct.
    assert [(s.phase, why) for s, why in p.skipped] == [
        ("P2", "not listed in phases[]"),
        ("P8", "not listed in phases[]"),
        ("P9", "not listed in phases[]"),
        ("P10", "not listed in phases[]"),
    ]


def test_pipeline_reflects_a_deleted_step(tmp_path):
    """Delete `design` from phases[] and its STEP leaves the pipeline, but its
    gate (G2) does NOT vanish — T441c RE-ANCHORS it to the nearest preceding
    active step (requirements/P1), because a gate approves the work up to a
    point and re-anchoring preserves that.

    Red-first against the pre-T441c source, where a deleted step's gate was
    SILENTLY dropped (`gates = [g for g in GATES if g.after_phase in live]`) so
    G2 was absent from `p.gates` entirely."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "profile.yml").write_text(
        "gate_profile: solo\n"
        "phases: [intake, requirements, implementation, test, review, triage, closeout]\n",
        encoding="utf-8")
    p = stage.pipeline(tmp_path)
    assert "P3" not in [s.phase for s in p.steps]
    assert "G2" in [g.id for g in p.gates], "design's gate must be re-anchored, not dropped"
    g2 = {g.id: g for g in p.gates}["G2"]
    assert g2.after_phase == "P1", "re-anchored onto the nearest preceding active step"
    assert [g.id for g in p.gates] == ["G2", "G3", "G4"]


def test_pipeline_backs_the_derived_views(tmp_path):
    """`active`/`skipped`/`next_phase` are reimplemented on the resolver, so they
    cannot disagree with it about what runs."""
    p = stage.pipeline(tmp_path)
    assert stage.active(tmp_path) == list(p.steps)
    assert stage.skipped(tmp_path) == list(p.skipped)
    assert stage.next_phase(tmp_path, "P1") == "P3"   # P2 skipped, so not P2


# ── one ordered list ─────────────────────────────────────────────────────────


def test_phase_order_is_single_sourced():
    """`state.PHASES` is THE order; `stage.PHASES` is the same object, and the
    stage table is in that order. Three spellings that used to be able to drift
    (there was no guard tying `state.PHASES` to `stage.PHASES`) are now one."""
    assert stage.PHASES is state.PHASES
    assert tuple(s.phase for s in stage.STAGES) == state.PHASES
    assert state.PHASES[0] == "P0"


def test_phase_order_is_not_a_range_count():
    """The ids are explicit data, so nothing derives a phase from a length. Red
    against the pre-T440 source, where `state.PHASES` was
    `tuple(f"P{i}" for i in range(12))` — a Call, not a Tuple of constants."""
    tree = ast.parse((SDLC / "state.py").read_text(encoding="utf-8"))
    assign = next(n for n in tree.body if "PHASES" in _targets(n))
    assert isinstance(assign.value, ast.Tuple), \
        "state.PHASES is not an explicit tuple of ids"
    assert all(isinstance(e, ast.Constant) for e in assign.value.elts)
    assert [e.value for e in assign.value.elts] == list(state.PHASES)


# ── one gate→phase binding, one merge gate ───────────────────────────────────


def test_gate_records_derive_from_stages_and_policy():
    """`stage.GATES` is built from `Stage.gate` and `policy.MERGE_GATE`; exactly
    one gate is the merge gate, and it is policy's."""
    assert [g.id for g in stage.GATES] == [s.gate for s in stage.STAGES if s.gate]
    for g in stage.GATES:
        assert g.after_phase == stage.by_phase(g.after_phase).phase
        assert stage.by_phase(g.after_phase).gate == g.id
    merge = [g for g in stage.GATES if g.merge]
    assert [g.id for g in merge] == [policy.MERGE_GATE]


def test_every_derived_view_equals_the_registry():
    """The six former restatements now all read the one registry."""
    assert approve.GATE_PHASE == {s.gate: s.phase for s in stage.STAGES if s.gate}
    # T450b: status keeps NO static gate view — it reads the resolved pipeline
    # (`status.standing_gates`), because the static one could not see an
    # extra_gates stop or a re-anchored builtin.
    assert not hasattr(status, "PHASE_GATE")
    assert validate.MERGE_GATE == policy.MERGE_GATE
    assert sl.LAST_GATE == policy.MERGE_GATE
    assert (adr.PHASE, defect.PHASE, suite.PHASE,
            release.PHASE, deploy.PHASE, verify.PHASE) == \
        tuple(stage.by_name(n).phase for n in
              ("design", "implementation", "test", "release", "deploy", "verify"))


# ── the guard: no seventh spelling ───────────────────────────────────────────

#: A phase or gate token. `P<n>` for any n (so a future custom-numbered phase is
#: still caught) and `G1`..`G5`.
_TOKEN = re.compile(r"^(P\d+|G[1-5])$")

#: The modules ALLOWED to spell a phase/gate token in a module-level constant —
#: the registry's owners — and which names may hold one. Every other shipped
#: module must DERIVE. `stage.py` is the registry home and is exempt entirely.
_OWNERS = {
    "state.py": {"PHASES"},
    "policy.py": {"GATES", "MERGE_GATE", "PRESETS"},
}
_EXEMPT_MODULES = {"stage.py"}


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _phase_gate_tokens(value: ast.AST | None) -> list[str]:
    if value is None:
        return []
    return [n.value for n in ast.walk(value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _TOKEN.match(n.value)]


def _shipped_modules() -> list[Path]:
    return sorted(p for p in SDLC.glob("*.py") if p.name != "__init__.py")


def test_the_registry_guard_sees_the_owners():
    """A positive control: the scan must actually FIND the tokens the owners
    legitimately spell, or the guard above is vacuous. `state.PHASES` and
    `policy.PRESETS` are the two that carry them."""
    phases = next(n for n in ast.parse((SDLC / "state.py").read_text(encoding="utf-8")).body
                  if "PHASES" in _targets(n))
    assert {"P0", "P11"} <= set(_phase_gate_tokens(phases.value))
    presets = next(n for n in ast.parse((SDLC / "policy.py").read_text(encoding="utf-8")).body
                   if "PRESETS" in _targets(n))
    assert "G4" in _phase_gate_tokens(presets.value)


@pytest.mark.parametrize("path", _shipped_modules(), ids=lambda p: p.name)
def test_no_shipped_module_restates_a_phase_or_gate_literal(path: Path):
    """One registry, or none. A module-level constant that spells a `P<n>`/`G<n>`
    token — outside the registry's declared owners — is a seventh place the
    lifecycle is defined, and the drift T440 exists to end.

    Red-first: against the pre-T440 source this fires on `approve.GATE_PHASE`,
    `validate.MERGE_GATE`, `slice.LAST_GATE`, and the six per-module `PHASE`
    constants — nine offenders.
    """
    if path.name in _EXEMPT_MODULES:
        return
    allowed = _OWNERS.get(path.name, set())
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in tree.body:                       # module top level only
        names = _targets(node)
        if not names:
            continue
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        toks = _phase_gate_tokens(value)
        for name in names:
            if toks and name not in allowed:
                offenders.append(
                    f"{path.name}:{node.lineno} {name} spells {sorted(set(toks))} "
                    f"- derive it from the registry (stage.pipeline / stage.GATES "
                    f"/ policy.MERGE_GATE) instead of restating it")
    assert not offenders, "\n".join(offenders)


# ── T441a: locked steps ──────────────────────────────────────────────────────
#
# A profile may delete steps (that is T440's `phases[]`), but not the spine.
# `stage.declared` refuses a `phases[]` that omits a locked step, so the deletion
# cannot happen silently. Red against the pre-T441 source, where any subset was
# accepted.


def _profile(tmp_path: Path, body: str) -> Path:
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir(exist_ok=True)
    (sdlc / "profile.yml").write_text(body, encoding="utf-8")
    return tmp_path


def test_locked_phases_are_real_step_names():
    """A locked step that is not a step name would lock nothing."""
    assert set(stage.LOCKED_PHASES) <= set(stage.NAMES)
    assert stage.LOCKED_PHASES == ("intake", "implementation", "review")


def test_the_solo_default_includes_every_locked_step():
    """The shipped default must itself be a legal pipeline, or the harness refuses
    its own out-of-the-box profile."""
    assert set(stage.LOCKED_PHASES) <= set(stage.SOLO_DEFAULT)


@pytest.mark.parametrize("locked", stage.LOCKED_PHASES)
def test_declared_refuses_dropping_a_locked_step(tmp_path, locked):
    """Delete one locked step from a full phases[] and the profile is refused by
    name. Red-first: pre-T441 `declared` accepted any subset."""
    kept = [p for p in stage.SOLO_DEFAULT if p != locked]
    target = _profile(tmp_path, f"phases: [{', '.join(kept)}]\n")
    with pytest.raises(stage.StageError) as ei:
        stage.declared(target)
    assert locked in str(ei.value)
    assert "cannot be removed" in str(ei.value)


def test_declared_accepts_dropping_a_deletable_step(tmp_path):
    """`design` is not locked, so a profile may drop it and still resolve."""
    kept = [p for p in stage.SOLO_DEFAULT if p != "design"]
    target = _profile(tmp_path, f"phases: [{', '.join(kept)}]\n")
    names, _ = stage.declared(target)
    assert "design" not in names
    assert set(stage.LOCKED_PHASES) <= set(names)


def test_the_solo_default_is_accepted_when_no_phases_are_declared(tmp_path):
    """A profile with no phases[] takes the solo default, which keeps every locked
    step — so the fallback is never a way around the lock."""
    target = _profile(tmp_path, "gate_profile: solo\n")
    names, source = stage.declared(target)
    assert set(stage.LOCKED_PHASES) <= set(names)
    assert "solo default" in source


# ── T441b: extra_gates ───────────────────────────────────────────────────────
#
# A profile ADDS a hard human stop at the end of a named step with
# `extra_gates: [step]`, id `G-<step>`. It stands in the resolved pipeline, halts
# the run until approved, and `approve` records it — none of which the pre-T441b
# source did (extra_gates was read by nothing; a `G-<step>` id was refused).

import subprocess  # noqa: E402

from tools.sdlc import state as ledger  # noqa: E402
from tools.tests import _runs  # noqa: E402


def test_extra_gate_stands_at_its_step(tmp_path):
    target = _profile(
        tmp_path,
        "phases: [intake, requirements, implementation, test, review, triage, closeout]\n"
        "extra_gates: [test]\n")
    p = stage.pipeline(target)
    gate = {g.id: g for g in p.gates}["G-test"]
    assert (gate.after_phase, gate.merge, gate.origin) == ("P5", False, "profile")
    # It stands between G3 (implementation, P4) and G4 (triage, P7). This profile
    # omits `design`, so under T441c its gate G2 is re-anchored to requirements
    # (P1) rather than dropped — the extra gate's own position is unchanged.
    assert [g.id for g in p.gates] == ["G2", "G3", "G-test", "G4"]


def test_extra_gate_on_a_step_not_run_is_refused(tmp_path):
    target = _profile(
        tmp_path,
        "phases: [intake, requirements, implementation, test, review, triage, closeout]\n"
        "extra_gates: [scoping]\n")   # scoping is not in phases[]
    with pytest.raises(stage.StageError, match="does not run"):
        stage.pipeline(target)


def test_extra_gate_on_a_non_step_is_refused(tmp_path):
    target = _profile(tmp_path, "extra_gates: [nonsense]\n")
    with pytest.raises(stage.StageError, match="not a step"):
        stage.pipeline(target)


def test_gate_phase_of_resolves_builtin_and_extra(tmp_path):
    target = _profile(
        tmp_path,
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\nextra_gates: [test]\n")
    assert approve.gate_phase_of(target, "G2") == "P3"      # builtin, from the map
    assert approve.gate_phase_of(target, "G-test") == "P5"  # extra, from the pipeline
    with pytest.raises(ValueError, match="unknown gate"):
        approve.gate_phase_of(target, "G-nope")


def test_an_extra_gate_halts_the_run_until_approved(tmp_path):
    """The behaviour, end to end. Red-first: pre-T441b, `test` has no builtin
    gate so `halted_at` returned [], and `record('G-test', …)` raised
    'unknown gate'."""
    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    (target / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\nextra_gates: [test]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True,
                   capture_output=True)
    rid = ledger.create(target, "requirement", "gated by an added gate")["run_id"]
    _runs.park(target, rid, "P5")

    held = stage.halted_at(target, rid, "P5")
    assert any("G-test" in p for p in held), held

    approve.record(target, rid, "G-test", "approve", "who@x.invalid", force=True)
    assert stage.halted_at(target, rid, "P5") == []


# ── T441c: enforcement + governance ──────────────────────────────────────────
#
# The resolver reconciles the orphans the pre-T441c source left: a gated step the
# USER deleted has its gate RE-ANCHORED to the nearest preceding active step; a
# builtin gate whose phase needs an ABSENT CAPABILITY is DROPPED with a visible
# diagnostic (re-anchoring a scope gate would demand artifacts the workspace
# cannot produce). `stage.resolved_hard_gates` and a re-anchor-aware
# `approve.gate_phase_of` are the accessors that keep the rest of the harness
# consistent with the resolved pipeline. All red-first against the pre-T441c source.


def test_a_deleted_gated_step_reanchors_its_gate_to_the_last_active_step(tmp_path):
    """Delete `triage` — which carries G4, the merge gate, and is NOT locked —
    and G4 must NOT vanish: it re-anchors to the last active step before it
    (review/P6). Red-first: the pre-T441c resolver dropped G4 outright, leaving a
    workspace that could never reach a merge decision."""
    target = _profile(
        tmp_path,
        "phases: [intake, requirements, design, implementation, test, review, closeout]\n")
    p = stage.pipeline(target)
    gates = {g.id: g for g in p.gates}
    assert "G4" in gates, "the merge gate must survive triage being deleted"
    assert gates["G4"].after_phase == "P6", "re-anchored to the last active step (review)"
    assert gates["G4"].merge is True
    assert [g.id for g in p.gates] == ["G2", "G3", "G4"]


def test_a_capability_gate_is_dropped_with_a_visible_diagnostic(tmp_path):
    """A builtin gate whose phase needs a capability the workspace lacks is
    DROPPED, not re-anchored, and the drop is DIAGNOSED rather than silent. The
    default install has neither `graph` (G1@scoping) nor `deploy` (G5@deploy).
    Red-first: the pre-T441c `Pipeline` had no `dropped_gates` channel at all."""
    p = stage.pipeline(tmp_path)
    dropped = {g.id: why for g, why in p.dropped_gates}
    assert "G1" in dropped and "graph" in dropped["G1"]
    assert "G5" in dropped and "deploy" in dropped["G5"]
    assert "G1" not in [g.id for g in p.gates]
    assert "G5" not in [g.id for g in p.gates]


def test_two_reanchored_builtins_merge_keeping_the_merge_gate(tmp_path):
    """A workspace with a deploy capability but neither `deploy` nor `triage` in
    phases[]: G4 (triage) and G5 (deploy) both re-anchor onto review (P6, the
    last active step before either). Two builtins on one step MERGE into one, and
    the merge gate (G4) is kept over the later-in-lifecycle G5. Red-first: the
    pre-T441c resolver dropped both."""
    target = _profile(
        tmp_path,
        "deploy_command: echo deploy\n"
        "phases: [intake, requirements, design, implementation, test, review, closeout]\n")
    p = stage.pipeline(target)
    gates = {g.id: g for g in p.gates}
    assert [g.id for g in p.gates] == ["G2", "G3", "G4"]
    assert gates["G4"].after_phase == "P6" and gates["G4"].merge is True
    assert "G5" not in gates, "G5 merged into the merge gate at review"
    merged = {g.id: why for g, why in p.dropped_gates}
    assert "G5" in merged and "G4" in merged["G5"], merged


def test_resolved_hard_gates_excludes_an_orphan(tmp_path):
    """`stage.resolved_hard_gates` is the reconciled hard-gate set: consistent
    with the pipeline, never naming a gate that does not stand. Under `team` the
    regime calls all five hard, but with no graph/deploy G1 and G5 do not stand.
    Red-first: `stage.resolved_hard_gates` did not exist."""
    target = _profile(tmp_path, "gate_profile: team\n")
    rules = policy.load(target)
    dials = policy.profile_dials(target)
    assert set(policy.hard_gates(rules, dials)) == {"G1", "G2", "G3", "G4", "G5"}
    assert stage.resolved_hard_gates(target) == ["G2", "G3", "G4"]


def test_policy_hard_gates_filters_orphans_when_given_the_standing_set(tmp_path):
    """The floor-side reconciliation: `policy.hard_gates` drops a regime-hard
    gate that is not standing when handed the standing set. Red-first: the
    pre-T441c signature took no `standing` argument."""
    rules = policy.load(tmp_path)
    dials = {"gate_profile": "team"}
    assert set(policy.hard_gates(rules, dials)) == {"G1", "G2", "G3", "G4", "G5"}
    assert policy.hard_gates(rules, dials, standing={"G2", "G3", "G4"}) == \
        ["G2", "G3", "G4"]


def test_gate_phase_of_follows_a_reanchored_gate(tmp_path):
    """Approving a re-anchored gate must resolve to where it STANDS, not its
    static home — otherwise G4 re-anchored to review could never be approved
    (the run never reaches triage/P7). Red-first: the pre-T441c `gate_phase_of`
    returned the static `GATE_PHASE['G4']` == 'P7' for the builtin."""
    target = _profile(
        tmp_path,
        "phases: [intake, requirements, design, implementation, test, review, closeout]\n")
    assert approve.gate_phase_of(target, "G4") == "P6"
