"""Item 35 — the guardrail policy layer: rules as data, walls in the CLIs.

Three things are under test, and they are not equally important.

**The tighten-only floor** is the item. `test_a_profile_cannot_switch_the_review_gate_off`
is its centre: `review.block_on: []` in a profile is how a control disappears without
anyone deciding to remove it, and the floor makes that cost a change to `policy.yml`.

**The reader** has to be right on a machine with no packages, so the refusals matter more
than the accepts — a policy misread is worse than one missing, because it looks enforced.
Every accepted fixture is also parsed by PyYAML and the two are required to agree
(`test_pyyaml_agrees_with_the_standard_library_reader`); every rejected one is rejected by
BOTH this validator and a pydantic model defined here, which is what the task line's
"pydantic-validated" buys without making an adopter install pydantic to be governed.

**The predicates** are the individual walls. Each has a passing case as well as a failing
one, because a wall that refuses everything is as useless as one that refuses nothing.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as S  # noqa: E402

TEMPLATE = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_policy", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()

Severity = Literal["info", "low", "medium", "high", "critical"]


class PolicyModel(BaseModel):
    """The task line's `pydantic-validated`, kept HERE rather than in `tools/`.

    The validator that ships has to run where the harness runs, and that is a machine
    with no packages; a model that only validates on a maintainer's laptop validates
    nothing. So the model lives in the suite and its job is to hold `policy.validate()`
    honest — two independent statements of the same schema, required to agree.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    gate_profile_floor: Literal["solo", "team", "regulated"] = "solo"
    block_on_floor: list[Severity] = Field(default=["critical", "high"], min_length=1)
    signing_floor: Literal["none", "dsse"] = "none"
    self_approval: Literal["allow", "forbid"] = "allow"
    worktrees_root: StrictStr = ".sdlc-worktrees"
    protected_paths: list[StrictStr] = []
    push_remote: StrictStr = "origin"
    push_branch_prefix: StrictStr = Field(default="sdlc/", min_length=1)
    deny_git: list[StrictStr] = []
    max_files_per_batch: StrictInt = Field(default=40, ge=0)
    max_lines_per_batch: StrictInt = Field(default=1500, ge=0)
    budget_exempt: list[StrictStr] = []


#: Documents both validators must ACCEPT.
ACCEPTED = [
    {},
    {"schema_version": 1},
    {"gate_profile_floor": "regulated", "signing_floor": "dsse"},
    {"block_on_floor": ["critical"]},
    {"block_on_floor": ["critical", "high", "medium", "low", "info"]},
    {"self_approval": "forbid"},
    {"max_files_per_batch": 0, "max_lines_per_batch": 0},
    {"protected_paths": [], "deny_git": [], "budget_exempt": []},
    {"push_remote": "upstream", "push_branch_prefix": "run/"},
]

#: Documents both validators must REJECT, each for the reason in its name.
REJECTED = {
    "unknown key": {"gate_profile_flor": "solo"},
    "wrong schema version": {"schema_version": 2},
    "unknown gate profile": {"gate_profile_floor": "casual"},
    "unknown signing mode": {"signing_floor": "pgp"},
    "unknown self approval": {"self_approval": "maybe"},
    "empty block_on floor": {"block_on_floor": []},
    "unknown severity": {"block_on_floor": ["critical", "catastrophic"]},
    "negative budget": {"max_files_per_batch": -3},
    "boolean budget": {"max_files_per_batch": True},
    "list where text belongs": {"push_remote": ["origin"]},
    "text where list belongs": {"deny_git": "--force"},
    "empty push prefix": {"push_branch_prefix": ""},
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(TEMPLATE, encoding="utf-8")
    return root


def profile(root: Path, body: str) -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")


# ── the reader: what it accepts ──────────────────────────────────────────────


def test_the_shipped_template_is_a_valid_policy():
    assert P.validate(P.parse(TEMPLATE)) == []


def test_the_built_in_defaults_are_themselves_a_valid_policy():
    """The floor a repository gets for free has to pass the check a written one
    does, or the default is a shape no author could legally have typed."""
    assert P.validate(dict(P.DEFAULTS)) == []


@pytest.mark.parametrize("text, expected", [
    ("a: 1\n", {"a": 1}),
    ("a: 1.5\n", {"a": 1.5}),
    ("a: true\nb: false\n", {"a": True, "b": False}),
    ("a:\n", {"a": {}}),
    ("a: null\nb: ~\n", {"a": None, "b": None}),
    ('a: "x y"\n', {"a": "x y"}),
    ("a: 'x y'\n", {"a": "x y"}),
    ("a: bare words\n", {"a": "bare words"}),
    ("a: [x, y]\n", {"a": ["x", "y"]}),
    ("a: []\n", {"a": []}),
    ("a:\n  - x\n  - y\n", {"a": ["x", "y"]}),
    ("a:\n  b: 1\n  c: two\n", {"a": {"b": 1, "c": "two"}}),
    ("# lead\na: 1  # trailing\n", {"a": 1}),
    ('a: "has # inside"\n', {"a": "has # inside"}),
    ("\n\na: 1\n\n", {"a": 1}),
])
def test_the_subset_it_reads(text, expected):
    assert P.parse(text) == expected


@pytest.mark.parametrize("text", [t for t, _ in [
    ("a: 1\n", None), ("a: [x, y]\n", None), ("a:\n  - x\n", None),
    ("a:\n  b: 1\n", None), ('a: "x y"\n', None), ("a: true\n", None),
    ("a: null\n", None), ("# c\na: 1 # c\n", None),
]] + [TEMPLATE])
def test_pyyaml_agrees_with_the_standard_library_reader(text):
    """The weld. The stdlib reader is the one that runs everywhere, so where a
    real parser IS available the two must mean the same thing — otherwise the
    policy says one thing on a maintainer's machine and another in the field."""
    assert P.parse(text) == yaml.safe_load(text)
    assert P.crosscheck(text, P.parse(text)) == ""


# ── the reader: what it refuses rather than guesses at ───────────────────────


@pytest.mark.parametrize("label, text", [
    ("two levels of nesting", "a:\n  b:\n    c: 1\n"),
    ("a sub-key opening a block", "a:\n  b:\n"),
    ("ragged indentation", "a:\n  - x\n    - y\n"),
    ("tabs", "a:\n\t- x\n"),
    ("the same key twice", "a: 1\na: 2\n"),
    ("a list at the top level", "- x\n"),
    ("an indented line before any key", "  a: 1\n"),
    ("a top-level line with no colon", "just words\n"),
    ("a mapping under a list", "a:\n  - x\n  b: 1\n"),
    ("a list item under a mapping", "a:\n  b: 1\n  - x\n"),
])
def test_what_it_will_not_guess_at(label, text):
    with pytest.raises(P.PolicyError):
        P.parse(text)


def test_a_refusal_names_the_line_it_stopped_on():
    with pytest.raises(P.PolicyError, match=r"policy\.yml:3"):
        P.parse("a: 1\nb:\n  c:\n    d: 2\n")


def test_the_deep_nest_is_refused_by_the_reader_not_only_by_the_crosscheck():
    """Found by running it: the reader FLATTENED a two-level document into the
    level above and only PyYAML noticed. The machines this has to be right on
    are the ones without PyYAML."""
    text = "schema_version: 1\nprotected_paths:\n  nested:\n    too: deep\n"
    assert P.crosscheck(text, {}) != "", "the fixture must really disagree"
    with pytest.raises(P.PolicyError, match="nesting"):
        P.parse(text)


def test_the_tab_refusal_names_tabs_and_not_something_else():
    """Counting only spaces made a tab-indented line look top-level, so the
    refusal was right and its reason was wrong — its own defect in a file whose
    whole job is to be unambiguous."""
    with pytest.raises(P.PolicyError, match="tabs are not indentation"):
        P.parse("a:\n\t- x\n")


def test_the_crosscheck_names_the_key_that_differs():
    message = P.crosscheck("a:\n  b:\n    c: 1\n", {"a": {"b": None, "c": 1}})
    assert "a" in message and "misread policy looks" in message


def test_the_crosscheck_is_silent_where_pyyaml_is_absent(monkeypatch):
    """An adopter without PyYAML gets the stdlib reader alone — that is the
    supported configuration, not a degraded one."""
    real = __import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", no_yaml)
    assert P.crosscheck("a: 1\n", {"a": 1}) == ""


def test_a_file_pyyaml_itself_rejects_is_not_a_policy():
    assert "PyYAML cannot read" in P.crosscheck("a: [1, 2\n", {"a": "[1, 2"})


# ── validation, and the pydantic model that holds it honest ──────────────────


@pytest.mark.parametrize("doc", ACCEPTED, ids=[str(i) for i in range(len(ACCEPTED))])
def test_both_validators_accept_the_same_documents(doc):
    assert P.validate(doc) == []
    PolicyModel(**doc)


@pytest.mark.parametrize("label", sorted(REJECTED))
def test_both_validators_reject_the_same_documents(label):
    doc = REJECTED[label]
    assert P.validate(doc), f"the shipped validator accepted {label}"
    with pytest.raises(ValidationError):
        PolicyModel(**doc)


def test_validation_reports_every_reason_at_once():
    """An operator fixing a policy file should need one round trip, not five —
    the rule intake already follows."""
    problems = P.validate({"self_approval": "maybe", "block_on_floor": [],
                           "max_files_per_batch": -3, "nonsense": 1})
    assert len(problems) == 4


# ── the tighten-only floor: the item ─────────────────────────────────────────


def test_a_profile_cannot_switch_the_review_gate_off():
    """The centre. An empty `block_on` is how a control disappears without
    anyone deciding to remove it."""
    problems = P.tighten_only(dict(P.DEFAULTS), {"block_on": []})
    assert len(problems) == 1
    assert "critical, high" in problems[0] and "policy.yml" in problems[0]


@pytest.mark.parametrize("dials, loosens", [
    ({"block_on": ["critical", "high"]}, False),
    ({"block_on": ["critical", "high", "medium"]}, False),
    ({"block_on": ["critical"]}, True),
    ({"block_on": []}, True),
    ({"gate_profile": "solo"}, False),
    ({"gate_profile": "regulated"}, False),
    ({"signing": "dsse"}, False),
    ({}, False),
])
def test_a_run_may_tighten_and_may_never_loosen(dials, loosens):
    assert bool(P.tighten_only(dict(P.DEFAULTS), dials)) is loosens


@pytest.mark.parametrize("floor, chosen, loosens", [
    ("solo", "solo", False), ("team", "solo", True), ("team", "team", False),
    ("regulated", "team", True), ("solo", "regulated", False),
])
def test_the_gate_profile_floor(floor, chosen, loosens):
    policy = {**P.DEFAULTS, "gate_profile_floor": floor}
    assert bool(P.tighten_only(policy, {"gate_profile": chosen})) is loosens


def test_a_dsse_floor_refuses_an_unsigned_run():
    policy = {**P.DEFAULTS, "signing_floor": "dsse"}
    assert P.tighten_only(policy, {"signing": "none"})
    assert P.tighten_only(policy, {"signing": "dsse"}) == []


def test_the_effective_regime_unions_the_floor_in_severity_order():
    got = P.effective(dict(P.DEFAULTS), {"block_on": ["medium"]})
    assert got["block_on"] == ["critical", "high", "medium"]


def test_a_loosening_is_refused_rather_than_quietly_clamped():
    """Raising someone's setting for them would hide that they asked for
    something the policy forbids."""
    dials = {"block_on": ["critical"]}
    assert P.tighten_only(dict(P.DEFAULTS), dials), "it must refuse first"
    assert P.effective(dict(P.DEFAULTS), dials)["block_on"] == ["critical", "high"]


def test_the_profiles_dials_are_read_with_the_stdlib(repo):
    profile(repo, "stack: python\ngate_profile: team\nsigning: none\n"
                  "review:\n  block_on: [critical, high, medium]  # tightened\n")
    assert P.profile_dials(repo) == {"gate_profile": "team", "signing": "none",
                                     "block_on": ["critical", "high", "medium"]}


# ── the walls ────────────────────────────────────────────────────────────────


def test_a_write_inside_the_runs_own_worktree_is_allowed(repo):
    assert P.confinement(dict(P.DEFAULTS), repo, "r1",
                         [".sdlc-worktrees/r1/src/a.py"]) == []


@pytest.mark.parametrize("path, reason", [
    ("src/b.py", "outside this run's worktree"),
    (".sdlc-worktrees/r2/src/a.py", "outside this run's worktree"),
    ("../../.ssh/authorized_keys", "outside the repository altogether"),
])
def test_a_write_outside_it_is_named_and_the_escape_is_named_differently(repo, path,
                                                                         reason):
    problems = P.confinement(dict(P.DEFAULTS), repo, "r1", [path])
    assert len(problems) == 1 and reason in problems[0]


@pytest.mark.parametrize("path, blocked", [
    (".env", True),
    ("services/api/.env", True),
    (".sdlc/runs/r1/approvals.jsonl", True),
    (".sdlc/runs/r1/state.json", True),
    (".sdlc/policy.yml", True),
    ("src/app.py", False),
    ("docs/env.md", False),
])
def test_protected_paths_match_at_the_root_and_at_depth(repo, path, blocked):
    assert bool(P.protected(dict(P.DEFAULTS), repo, [path])) is blocked


@pytest.mark.parametrize("remote, branch, run_id, problems", [
    ("origin", "sdlc/r1", "r1", 0),
    ("origin", "main", "r1", 1),
    ("origin", "sdlc/r2", "r1", 1),
    ("upstream", "sdlc/r2", "r1", 2),
    ("origin", "sdlc/r1", "", 0),
])
def test_agents_publish_to_exactly_one_place(remote, branch, run_id, problems):
    assert len(P.push_target(dict(P.DEFAULTS), remote, branch, run_id)) == problems


@pytest.mark.parametrize("command, denied", [
    ("git push --force origin main", True),
    ("git push --force-with-lease", True),
    ("git  reset   --hard  HEAD~5", True),
    ("git branch -D main", True),
    ("git clean -fdx", True),
    ("git push origin sdlc/r1", False),
    ("git status", False),
    ("git commit -m 'work'", False),
])
def test_destructive_git_is_caught_through_extra_whitespace(command, denied):
    assert bool(P.destructive(dict(P.DEFAULTS), command)) is denied


def _diff(files: int, lines_each: int = 1, name: str = "src/f{}.py") -> str:
    out = []
    for i in range(files):
        out += [f"diff --git a/{name.format(i)} b/{name.format(i)}",
                f"--- a/{name.format(i)}", f"+++ b/{name.format(i)}", "@@ -0,0 +1 @@"]
        out += [f"+line {j}" for j in range(lines_each)]
    return "\n".join(out) + "\n"


def test_a_batch_within_budget_passes():
    assert P.budget(dict(P.DEFAULTS), _diff(5, 10)) == []


def test_too_many_files_is_named_with_the_budget():
    problems = P.budget(dict(P.DEFAULTS), _diff(60))
    assert len(problems) == 1 and "60 files" in problems[0] and "budget 40" in problems[0]


def test_too_many_lines_is_named_separately():
    problems = P.budget(dict(P.DEFAULTS), _diff(3, 800))
    assert len(problems) == 1 and "2400 changed lines" in problems[0]


def test_a_lockfile_does_not_push_a_real_change_over_the_limit():
    """Nobody reads a lockfile line by line, and counting it would make the
    budget fire on changes that are perfectly reviewable."""
    text = _diff(5, 10) + _diff(1, 9000, "uv.lock")
    problems = P.budget(dict(P.DEFAULTS), text)
    assert problems == []
    over = P.budget(dict(P.DEFAULTS), _diff(60) + _diff(1, 10, "uv.lock"))
    assert "1 exempt file(s) not counted" in over[0], "and it says what it excluded"


def test_a_budget_of_zero_disables_that_limit():
    policy = {**P.DEFAULTS, "max_files_per_batch": 0}
    assert P.budget(policy, _diff(500)) == []


@pytest.mark.parametrize("mode, approver, author, refused", [
    ("allow", "a@x.invalid", "a@x.invalid", False),
    ("allow", "a@x.invalid", "", False),
    ("forbid", "a@x.invalid", "b@x.invalid", False),
    ("forbid", "a@x.invalid", "a@x.invalid", True),
    ("forbid", "A@X.invalid", "a@x.invalid", True),
    ("forbid", "a@x.invalid", "", True),
])
def test_self_approval_and_the_unknown_author_that_must_not_pass(mode, approver, author,
                                                                 refused):
    policy = {**P.DEFAULTS, "self_approval": mode}
    assert bool(P.self_approval(policy, approver, author)) is refused


def test_an_unevaluable_self_approval_says_which_input_is_missing():
    problems = P.self_approval({**P.DEFAULTS, "self_approval": "forbid"}, "a@x", "")
    assert "no recorded author" in problems[0] and "--author" in problems[0]


# ── loading and resolution ───────────────────────────────────────────────────


def test_a_repository_with_no_policy_gets_the_defaults(tmp_path):
    assert P.load(tmp_path) == dict(P.DEFAULTS)
    assert P.locate(tmp_path) is None


def test_the_plans_spelling_wins_over_the_installers(tmp_path):
    for rel in P.POLICY_LOCATIONS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(TEMPLATE, encoding="utf-8")
    assert P.locate(tmp_path) == tmp_path / P.POLICY_LOCATIONS[0]


def test_an_unreadable_policy_raises_rather_than_falling_back_to_defaults(repo):
    """Every caller's next act is to enforce it. Returning a default here would
    mean an unparseable constitution reads as the permissive one."""
    (repo / ".sdlc" / "policy.yml").write_text("a:\n  b:\n    c: 1\n", encoding="utf-8")
    with pytest.raises(P.PolicyError):
        P.load(repo)


def test_an_invalid_policy_raises_with_every_reason(repo):
    (repo / ".sdlc" / "policy.yml").write_text(
        "self_approval: maybe\nblock_on_floor: []\n", encoding="utf-8")
    with pytest.raises(P.PolicyError, match="maybe.*block_on_floor|block_on_floor.*maybe"):
        P.load(repo)


# ── approve.py enforces it, because a runner that skips the check is the case ─


def _run(repo: Path, run_id: str = "r1") -> None:
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True,
                   timeout=120)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=dev@x.invalid", "-c",
                    "user.name=Dev", "commit", "-qm", "base"], check=True, timeout=120)
    S.create(repo, "requirement", "pagination", run_id=run_id)
    S.advance(repo, run_id, phase="P7")


def test_approve_refuses_a_gate_taken_under_a_loosened_profile(repo):
    _run(repo)
    profile(repo, "gate_profile: solo\nreview:\n  block_on: []\n")
    with pytest.raises(RuntimeError, match="loosens the repository's policy"):
        A.record(repo, "r1", "G4", "approve", "lead@x.invalid")


def test_approve_refuses_a_self_approval_when_the_policy_forbids_it(repo):
    _run(repo)
    (repo / ".sdlc" / "policy.yml").write_text(
        TEMPLATE.replace("self_approval: allow", "self_approval: forbid"),
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="may not also approve it"):
        A.record(repo, "r1", "G4", "approve", "dev@x.invalid", author="dev@x.invalid")
    entry = A.record(repo, "r1", "G4", "approve", "lead@x.invalid",
                     author="dev@x.invalid")
    assert entry["verdict"] == "approve"


def test_every_entry_records_which_constitution_was_in_force(repo):
    _run(repo)
    entry = A.record(repo, "r1", "G4", "approve", "lead@x.invalid")
    assert entry["policy_source"] == "policy.yml"


def test_a_repository_with_no_policy_still_approves_under_the_defaults(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(root)
    entry = A.record(root, "r1", "G4", "approve", "dev@x.invalid")
    assert entry["policy_source"] == "defaults"


def test_an_unreadable_policy_stops_an_approval_rather_than_permitting_one(repo):
    _run(repo)
    (repo / ".sdlc" / "policy.yml").write_text("a:\n  b:\n    c: 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        A.record(repo, "r1", "G4", "approve", "lead@x.invalid")


# ── the installer ships it, and never clobbers a tightened one ───────────────


def test_the_installer_writes_the_policy_into_the_repository(tmp_path):
    root = tmp_path / "adopter"
    root.mkdir()
    op = si.plan_op(root, ".sdlc/policy.yml", si.load_template("policy.yml"),
                    preserve=True)
    assert op["status"] == "create" and "gate_profile_floor" in op["content"]


def test_a_tightened_policy_survives_a_re_run(tmp_path):
    """A developer who raised a floor has stated something about their repository.
    Silently restoring the shipped one is the exact loosening this item forbids."""
    root = tmp_path / "adopter"
    (root / ".sdlc").mkdir(parents=True)
    tightened = TEMPLATE.replace("self_approval: allow", "self_approval: forbid")
    (root / ".sdlc" / "policy.yml").write_text(tightened, encoding="utf-8")
    op = si.plan_op(root, ".sdlc/policy.yml", si.load_template("policy.yml"),
                    preserve=True)
    assert op["status"] == "preserved" and op["rel"].endswith(".new")
    assert (root / ".sdlc" / "policy.yml").read_text(encoding="utf-8") == tightened


def test_the_template_is_one_the_installer_knows_about():
    assert "policy.yml" in si.TEMPLATE_FILES


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_the_cli_exits_zero_within_policy_and_one_outside_it(repo, capsys):
    assert P.main(["--target", str(repo), "--validate"]) == 0
    assert "OK - within policy." in capsys.readouterr().out
    assert P.main(["--target", str(repo), "--write", ".env"]) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_a_broken_policy_is_exit_two_not_exit_one(repo, capsys):
    """1 means "the action is refused"; 2 means "the check could not be run".
    Conflating them would let a broken policy read as a rejected action."""
    (repo / ".sdlc" / "policy.yml").write_text("self_approval: maybe\n", encoding="utf-8")
    assert P.main(["--target", str(repo), "--validate"]) == 2
    assert "!!" in capsys.readouterr().out


def test_the_cli_refuses_a_target_that_is_not_there(tmp_path, capsys):
    assert P.main(["--target", str(tmp_path / "nope"), "--validate"]) == 2
    assert "no directory at" in capsys.readouterr().out


def test_show_names_where_the_policy_came_from(tmp_path, capsys):
    assert P.main(["--target", str(tmp_path), "--show"]) == 0
    assert "built-in defaults" in capsys.readouterr().out


def test_json_carries_the_policy_the_dials_and_the_findings(repo, capsys):
    profile(repo, "gate_profile: team\n")
    P.main(["--target", str(repo), "--write", ".env", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["profile"] == {"gate_profile": "team"}
    assert doc["findings"]["protected"] and doc["problems"]
    assert doc["effective"]["gate_profile"] == "team"


def test_the_cli_validates_a_file_that_is_not_the_targets_own(repo, capsys):
    assert P.main(["--target", str(repo), "--file",
                   str(ROOT / "sdlc_templates" / "policy.yml"), "--validate"]) == 0
