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
    """Delete `design` from phases[] and its step AND its gate leave the resolved
    pipeline. This is the behaviour the whole registry exists to make one fact:
    today `policy.hard_gates` still calls G2 hard (the orphan T441 reconciles),
    but the RESOLVER already agrees the gate is not standing anywhere."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "profile.yml").write_text(
        "gate_profile: solo\n"
        "phases: [intake, requirements, implementation, test, review, triage, closeout]\n",
        encoding="utf-8")
    p = stage.pipeline(tmp_path)
    assert "P3" not in [s.phase for s in p.steps]
    assert "G2" not in [g.id for g in p.gates]
    assert [g.id for g in p.gates] == ["G3", "G4"]


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
    assert status.PHASE_GATE == {p: g for g, p in approve.GATE_PHASE.items()}
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
