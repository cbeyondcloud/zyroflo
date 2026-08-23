"""Item 63 — gate_profile presets: the regime stops being a label and starts meaning
something.

Before this, `gate_profile` was a word in a config file that nothing read. A profile
could say `regulated` and behave exactly like `solo`, and every ledger entry recorded
the word rather than the regime. Three things are under test:

**The presets are the master flow's own words as data** — which gates halt, how finely
agent changes are reviewed, which severities block, whether signing is required. G4 is
hard in every one of them, because the halt rule is not a dial: the regime sets the
GRANULARITY of review, never whether review happens.

**`solo` above one component is structural, not a floor.** A floor can be raised past;
this cannot be selected at all, from any policy. One reviewer signing off changes that
span repositories is the regime failing to describe what is happening.

**A regime you declare is a regime you are held to.** `gate_profile: regulated` beside
`signing: none` was the same hole one level up — declaring a regime and then opting out
of what it means. The effective floor is now the stricter of the constitution and the
preset, and the ledger records the RESOLVED regime beside the declared one.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as S  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

TEMPLATE = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
PROFILE_TEMPLATE = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_gates",
                                                  ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_gates"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()
FLOOR = dict(P.DEFAULTS)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(TEMPLATE, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True,
                   timeout=120)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, timeout=120)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=dev@x.invalid", "-c",
                    "user.name=Dev", "commit", "-qm", "base"], check=True, timeout=120)
    S.create(root, "requirement", "pagination", run_id="r1")
    R.park(root, "r1", "P7")
    return root


def profile(root: Path, body: str) -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")


# ── the presets are the flow's own words, as data ────────────────────────────


@pytest.mark.parametrize("name, hard, granularity, block, signing", [
    ("solo", ["G2", "G4"], "branch", ["critical", "high"], "none"),
    ("team", ["G1", "G2", "G3", "G4", "G5"], "diff", ["critical", "high"], "none"),
    ("regulated", ["G1", "G2", "G3", "G4", "G5"], "diff",
     ["critical", "high", "medium"], "dsse"),
])
def test_each_regime_resolves_to_what_the_master_flow_says_it_means(
        name, hard, granularity, block, signing):
    got = P.regime(FLOOR, {"gate_profile": name})
    assert got["hard_gates"] == hard
    assert got["g3_granularity"] == granularity
    assert got["block_on"] == block
    assert got["signing"] == signing


def test_there_is_a_preset_for_every_regime_and_no_others():
    assert set(P.PRESETS) == set(P.GATE_PROFILES)


@pytest.mark.parametrize("name", sorted(P.PRESETS))
def test_the_merge_gate_is_hard_in_every_regime(name):
    """The halt rule is not a dial. Under `solo`, G3 does not halt and the whole
    branch diff is reviewed at G4 instead — so G4 is the one that can never go."""
    assert P.MERGE_GATE in P.PRESETS[name]["hard_gates"]


def test_solo_does_not_halt_per_diff_but_still_reviews_before_the_branch_leaves():
    solo = P.regime(FLOOR, {"gate_profile": "solo"})
    assert "G3" not in solo["hard_gates"], "solo does not halt on every agent diff"
    assert solo["g3_granularity"] == "branch"
    assert P.MERGE_GATE in solo["hard_gates"], "and the branch is still reviewed"


def test_the_regimes_are_monotone_so_moving_up_never_relaxes_anything():
    order = [P.PRESETS[n] for n in P.GATE_PROFILES]
    for looser, tighter in zip(order, order[1:]):
        assert set(looser["hard_gates"]) <= set(tighter["hard_gates"])
        assert set(looser["block_on"]) <= set(tighter["block_on"])
        assert (P.SIGNING_MODES.index(looser["signing"])
                <= P.SIGNING_MODES.index(tighter["signing"]))


def test_an_unknown_regime_falls_back_to_the_constitutions_floor():
    """Not to the loosest preset. A typo must not be a downgrade."""
    policy = {**FLOOR, "gate_profile_floor": "team"}
    assert P.regime(policy, {"gate_profile": "casual"})["name"] == "team"
    assert P.regime(policy, {})["name"] == "team"


# ── solo above one component: structural, not a floor ────────────────────────


@pytest.mark.parametrize("components, refused", [(0, False), (1, False), (2, True),
                                                 (26, True)])
def test_solo_is_unselectable_once_there_is_more_than_one_component(components,
                                                                    refused):
    problems = P.tighten_only(FLOOR, {"gate_profile": "solo"}, components)
    assert bool([p for p in problems if "unselectable" in p]) is refused


@pytest.mark.parametrize("name", ["team", "regulated"])
def test_the_other_regimes_are_fine_across_many_components(name):
    assert P.tighten_only(FLOOR, {"gate_profile": name}, 26) == []


@pytest.mark.parametrize("floor", sorted(P.GATE_PROFILES))
def test_no_constitution_can_permit_solo_across_components(floor):
    """A floor is something you raise past. This is not one — which is what makes
    the guard worth more than a dial."""
    policy = {**FLOOR, "gate_profile_floor": floor}
    assert any("unselectable" in p
               for p in P.tighten_only(policy, {"gate_profile": "solo"}, 3))


def _components(n: int) -> list[dict]:
    return [{"name": f"svc-{i}", "path": f"svc-{i}", "stack": "python"}
            for i in range(n)]


@pytest.mark.parametrize("n", [0, 1, 3, 12])
def test_the_component_count_reads_the_installers_own_output(tmp_path, n):
    """A weld, not a parser: the count is taken from the shape `render_components`
    writes, so the generator owns the contract on both sides."""
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    rendered = si.render_profile(si.workspace_profile(_components(n)) if n
                                 else {"stack": "python", "test_command": "",
                                       "coverage_command": "", "lint_command": "",
                                       "build_command": "", "deploy_command": "",
                                       "coverage_threshold": 90, "vcs": {},
                                       "components": []}, ["claude"])
    (root / ".sdlc" / "profile.yml").write_text(rendered, encoding="utf-8")
    assert P.component_count(root) == n


def test_the_installer_never_emits_solo_for_a_workspace_it_would_refuse(tmp_path):
    rendered = si.render_profile(si.workspace_profile(_components(3)), ["claude"])
    assert "gate_profile: team" in rendered
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(rendered, encoding="utf-8")
    assert P.tighten_only(FLOOR, P.profile_dials(root), P.component_count(root)) == []


def test_hand_editing_the_regime_back_to_solo_is_caught_at_run_time(tmp_path):
    """The installer emitting `team` is an install-time guard only. Someone who
    opens the file afterwards is the case this item is about."""
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    rendered = si.render_profile(si.workspace_profile(_components(3)), ["claude"])
    (root / ".sdlc" / "profile.yml").write_text(
        rendered.replace("gate_profile: team", "gate_profile: solo"), encoding="utf-8")
    assert any("unselectable" in p for p in
               P.tighten_only(FLOOR, P.profile_dials(root), P.component_count(root)))


def test_a_commented_out_component_is_not_a_component(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    profile(root, 'components:\n  - name: "a"\n#  - name: "b"\n    path: "a"\n')
    assert P.component_count(root) == 1


# ── per-gate overrides, tighten-only ─────────────────────────────────────────


def test_a_gate_may_be_made_harder_than_the_regime_makes_it():
    assert P.tighten_only(FLOOR, {"gate_profile": "solo"}, 1, {"G1": "hard"}) == []
    assert P.hard_gates(FLOOR, {"gate_profile": "solo"}, {"G1": "hard"}) == \
        ["G1", "G2", "G4"]


@pytest.mark.parametrize("name", sorted(P.PRESETS))
def test_the_merge_gate_can_never_be_made_advisory(name):
    problems = P.tighten_only(FLOOR, {"gate_profile": name}, 1, {"G4": "advisory"})
    assert problems and "G4" in problems[0]


def test_dropping_a_gate_the_regime_requires_is_refused_by_name():
    problems = P.tighten_only(FLOOR, {"gate_profile": "team"}, 1, {"G3": "advisory"})
    assert len(problems) == 1
    assert "team makes it hard" in problems[0] and "never remove one" in problems[0]


def test_softening_a_gate_the_regime_never_required_is_not_a_loosening():
    """`solo` does not halt at G1, so saying so out loud changes nothing."""
    assert P.tighten_only(FLOOR, {"gate_profile": "solo"}, 1, {"G1": "advisory"}) == []


@pytest.mark.parametrize("overrides, fragment", [
    ({"G9": "hard"}, "unknown gate"),
    ({"G1": "optional"}, "one of hard, advisory"),
])
def test_an_override_that_means_nothing_is_refused_rather_than_ignored(overrides,
                                                                       fragment):
    problems = P.tighten_only(FLOOR, {"gate_profile": "solo"}, 1, overrides)
    assert problems and fragment in problems[0]


def test_the_override_block_is_read_from_the_profile(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    profile(root, "gate_profile: solo\ngates:\n  G1: hard\n  G5: hard  # ours\n"
                  "review:\n  block_on: [critical, high]\n")
    assert P.gate_overrides(root) == {"G1": "hard", "G5": "hard"}


def test_keys_from_another_block_are_not_mistaken_for_gate_overrides(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    profile(root, "gates:\n  G1: hard\nreview:\n  block_on: [critical]\n"
                  "  max_iterations: 5\n")
    assert P.gate_overrides(root) == {"G1": "hard"}


# ── a regime you declare is a regime you are held to ─────────────────────────


def test_a_regulated_profile_cannot_also_ask_for_no_signing():
    """The same hole as `block_on: []`, one level up: declare a regime, then opt
    out of what the regime means."""
    problems = P.tighten_only(FLOOR, {"gate_profile": "regulated",
                                      "signing": "none"}, 1)
    assert len(problems) == 1
    assert "gate_profile: regulated" in problems[0] and "'dsse'" in problems[0]


def test_a_regulated_profile_cannot_drop_the_severity_the_regime_adds():
    problems = P.tighten_only(FLOOR, {"gate_profile": "regulated",
                                      "block_on": ["critical", "high"]}, 1)
    assert len(problems) == 1 and "medium" in problems[0]
    assert "regulated" in problems[0], "the regime is named, not just policy.yml"


def test_the_constitution_is_still_named_when_it_is_the_binding_one():
    policy = {**FLOOR, "signing_floor": "dsse"}
    problems = P.tighten_only(policy, {"gate_profile": "team", "signing": "none"}, 1)
    assert "policy.yml" in problems[0]


@pytest.mark.parametrize("declared, preset_name, expected", [
    ("none", "solo", "none"),
    ("none", "regulated", "dsse"),
    ("dsse", "solo", "dsse"),
    (None, "regulated", "dsse"),
])
def test_the_effective_signing_is_the_strictest_of_the_three(declared, preset_name,
                                                             expected):
    dials = {"gate_profile": preset_name}
    if declared is not None:
        dials["signing"] = declared
    assert P.effective(FLOOR, dials)["signing"] == expected


def test_the_effective_regime_carries_the_gates_a_runner_has_to_honour():
    got = P.effective(FLOOR, {"gate_profile": "team"}, {"G1": "hard"})
    assert got["hard_gates"] == list(P.GATES)
    assert got["g3_granularity"] == "diff"


# ── the ledger records the regime that governed, not only the word written ───


def test_an_entry_records_the_declared_word_and_the_resolved_regime(repo):
    profile(repo, "stack: python\n")  # says nothing about gates
    entry = A.record(repo, "r1", "G4", "approve", "lead@x.invalid")
    assert entry["gate_profile"] == "unset", "what the profile said"
    assert entry["gate_regime"] == "solo", "what actually governed"
    assert entry["hard_gates"] == ["G2", "G4"]


def test_the_two_agree_when_the_profile_states_a_regime(repo):
    profile(repo, "gate_profile: team\n")
    entry = A.record(repo, "r1", "G4", "approve", "lead@x.invalid")
    assert entry["gate_profile"] == entry["gate_regime"] == "team"
    assert entry["hard_gates"] == list(P.GATES)


def test_an_override_reaches_the_entry_so_an_auditor_sees_the_real_set(repo):
    profile(repo, "gate_profile: solo\ngates:\n  G1: hard\n")
    entry = A.record(repo, "r1", "G4", "approve", "lead@x.invalid")
    assert entry["hard_gates"] == ["G1", "G2", "G4"]


def test_a_regulated_run_cannot_approve_until_signing_exists_and_says_which(repo,
                                                                            monkeypatch):
    """Choosing `regulated` means choosing dsse. Recording an unsigned entry under
    it would be the downgrade item 32 refused, arrived at from the other side."""
    monkeypatch.delenv(A.SIGNING_KEY_ENV, raising=False)
    # No `signing:` line at all — the profile never asks for it, the REGIME does.
    # That is the case worth testing: a run that would otherwise have recorded an
    # unsigned entry under a regime whose whole point is attribution.
    profile(repo, "gate_profile: regulated\n")
    with pytest.raises(RuntimeError) as exc:
        A.record(repo, "r1", "G4", "approve", "lead@x.invalid")
    said = str(exc.value)
    assert "gate_profile: regulated requires signing: dsse" in said
    assert "not implemented yet" in said, "and item 32's blockers are still named"
    assert not A.approvals_path(repo, "r1").exists()


def test_a_profile_that_asks_for_signing_itself_is_told_so_in_its_own_words(repo,
                                                                            monkeypatch):
    """The message names the source that bound it. Blaming the regime for a
    setting the author typed would send them to the wrong file."""
    monkeypatch.delenv(A.SIGNING_KEY_ENV, raising=False)
    profile(repo, "gate_profile: team\nsigning: dsse\n")
    with pytest.raises(RuntimeError, match="profile asks for signing: dsse"):
        A.record(repo, "r1", "G4", "approve", "lead@x.invalid")


def test_approve_refuses_solo_across_components(repo):
    profile(repo, "gate_profile: solo\ncomponents:\n"
                  '  - name: "a"\n    path: "a"\n  - name: "b"\n    path: "b"\n')
    with pytest.raises(RuntimeError, match="unselectable"):
        A.record(repo, "r1", "G4", "approve", "lead@x.invalid")


# ── the template teaches what the code does ──────────────────────────────────


@pytest.mark.parametrize("name", sorted(P.PRESETS))
def test_the_profile_template_documents_every_regime_that_exists(name):
    assert f"  {name} " in PROFILE_TEMPLATE or f"#   {name}" in PROFILE_TEMPLATE


def test_the_template_shows_the_override_block_and_says_it_only_tightens():
    assert "gates:" in PROFILE_TEMPLATE and "hard | advisory" in PROFILE_TEMPLATE
    assert "never softer" in PROFILE_TEMPLATE
    assert "STRUCTURALLY unselectable" in PROFILE_TEMPLATE


def test_the_template_states_the_rule_the_presets_must_not_break():
    assert "HALT RULE IS NOT A DIAL" in PROFILE_TEMPLATE
