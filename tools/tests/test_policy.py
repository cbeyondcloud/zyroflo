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

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as S  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

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


#: A name somebody could actually push to. `StrictStr` alone accepts "", and a
#: blank remote is not a permissive setting - it is one nothing satisfies.
Named = Annotated[StrictStr, Field(min_length=1)]


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
    coverage_enforcement_floor: Literal["advisory", "hard"] = "advisory"
    self_approval: Literal["allow", "forbid"] = "allow"
    worktrees_root: StrictStr = ".sdlc-worktrees"
    protected_paths: list[StrictStr] = []
    # Item 37 widened this: text for one remote, a list for an estate whose
    # components publish to different places. A non-empty list of non-empty
    # names, because an empty set is a rule nothing can satisfy.
    push_remote: Named | Annotated[list[Named], Field(min_length=1)] = "origin"
    push_branch_prefix: StrictStr = Field(default="sdlc/", min_length=1)
    deny_git: list[StrictStr] = []
    #: Each rule must at least name a program; a rule that is only whitespace
    #: is a wall that matches nothing while looking like one. `min_length` alone
    #: does NOT say this - it accepts "  " - and the two validators disagreeing
    #: about that fixture is what this pair exists to surface.
    deny_commands: list[Annotated[StrictStr, Field(pattern=r"\S")]] = []
    max_files_per_batch: StrictInt = Field(default=40, ge=0)
    max_lines_per_batch: StrictInt = Field(default=1500, ge=0)
    budget_exempt: list[StrictStr] = []
    # Item 105: the org's runner floor. Empty = any declared runner; named =
    # a profile routes only within the list. Names must be non-blank for the
    # deny_commands reason: a floor entry of whitespace is a pin on nothing.
    allowed_runners: list[Named] = []
    require_conformance: StrictBool = False


#: Documents both validators must ACCEPT.
ACCEPTED = [
    {},
    {"schema_version": 1},
    {"gate_profile_floor": "regulated", "signing_floor": "dsse"},
    {"block_on_floor": ["critical"]},
    {"block_on_floor": ["critical", "high", "medium", "low", "info"]},
    {"self_approval": "forbid"},
    {"coverage_enforcement_floor": "hard"},
    {"max_files_per_batch": 0, "max_lines_per_batch": 0},
    {"protected_paths": [], "deny_git": [], "deny_commands": [],
     "budget_exempt": []},
    {"deny_commands": ["rm -r|-R", "mkfs"]},
    {"push_remote": "upstream", "push_branch_prefix": "run/"},
    # An estate: components publish to a fork here and a mirror there, and
    # each may only CHOOSE from this set (item 37).
    {"push_remote": ["origin", "fork", "internal-mirror"]},
]

#: Documents both validators must REJECT, each for the reason in its name.
REJECTED = {
    "unknown key": {"gate_profile_flor": "solo"},
    "wrong schema version": {"schema_version": 2},
    "unknown gate profile": {"gate_profile_floor": "casual"},
    "unknown signing mode": {"signing_floor": "pgp"},
    "unknown coverage enforcement": {"coverage_enforcement_floor": "strict"},
    "unknown self approval": {"self_approval": "maybe"},
    "empty block_on floor": {"block_on_floor": []},
    "unknown severity": {"block_on_floor": ["critical", "catastrophic"]},
    "negative budget": {"max_files_per_batch": -3},
    "boolean budget": {"max_files_per_batch": True},
    "list where text belongs": {"worktrees_root": [".sdlc-worktrees"]},
    "an empty set of remotes": {"push_remote": []},
    "a blank remote": {"push_remote": ""},
    "a remote that is not text": {"push_remote": ["origin", 7]},
    "a blank remote in the set": {"push_remote": ["origin", ""]},
    "text where list belongs": {"deny_git": "--force"},
    "commands where list belongs": {"deny_commands": "rm -r"},
    "a rule that names no program": {"deny_commands": ["rm -r", "  "]},
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


@pytest.mark.parametrize("doc, what", [(dict(P.DEFAULTS), "the built-in floor"),
                                       (P.parse(TEMPLATE), "the shipped template")])
def test_the_second_validator_knows_every_field_the_first_one_ships(doc, what):
    """The pydantic model is only an independent statement of the schema while
    it knows all of it, and nothing was checking that.

    Found adding `deny_commands`: the model has `extra="forbid"` and a
    hand-written field list, so a new key reached `policy.validate` and the
    template with the model none the wiser — a second opinion that had quietly
    stopped covering the document. The fixtures below cannot notice, because
    they are also hand-written. This can: it feeds the model the two real
    documents, so the next field added fails here until the model learns it.
    """
    PolicyModel(**doc)


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


# ── destruction git never sees ───────────────────────────────────────────────
#
# The carried follow-up from item 43: `deny_git` is git operations and says so
# in its name, so nothing in the harness refused `rm -rf` for any runner but
# Claude Code, whose wall was a permission file rather than this one. Measured
# before the fix: 22 of 24 destructive shell spellings were allowed.


@pytest.mark.parametrize("command", [
    "rm -rf /", "rm -fr build", "rm -r -f build", "rm -Rf build",
    "rm --recursive --force build", "rm -r build",
    "sudo rm -rf /srv", "/bin/rm -rf /srv", "RUNNER=1 sudo /bin/rm -Rf /srv",
    "del /f /s /q build", "rmdir /s /q build", "rd /s /q .",
    "Remove-Item -Recurse -Force .", "Remove-Item -Force -Recurse .",
    "ri -r -fo .", "erase /s /q .",
    "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1", "shred -u secrets.env",
    "truncate -s 0 important.log", "find . -delete",
])
def test_every_spelling_of_a_destroying_command_is_refused(command):
    """One operation, many spellings. The point of tokenising rather than
    substring-matching: `-rf`, `-fr`, `-Rf` and `-r -f` are the same act, and a
    matcher that cannot see that is one an agent defeats by swapping two
    characters."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    # The word-boundary cases a substring rule spelled "rm -rf" would have
    # refused: each of these CONTAINS it.
    "confirm -rf plan.txt", "echo charm -rf", "grep -rf patterns.txt src/",
    # And ordinary work, which a wall people turn off protects nobody from.
    "npm ci && npm run build", "mvn -q test", "pytest -q", "git status",
    "find . -exec grep foo {} ;", "cmd /c npm run build",
    "powershell -Command \"Get-ChildItem\"", "bash -c \"npm ci && npm test\"",
    "rm build/one.tmp", "truncate --help",
])
def test_ordinary_work_is_not_refused(command):
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    "git status && rm -rf /", "echo hi | rm -rf /", "true; rm -rf /",
    "false || rm -rf /", "git log\nrm -rf /",
])
def test_a_second_command_on_the_same_line_is_judged_too(command):
    """`deny_git` finds its token anywhere in the line because it searches the
    whole string. A tokenised matcher that read only the first program would be
    the WEAKER of the two walls, which is why the line is split first."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "cmd.exe /c rmdir /s /q x", "cmd /k rd /s .",
    "powershell -Command \"Remove-Item -Recurse -Force .\"",
    "pwsh -c \"ri -r .\"", "bash -c \"rm -rf /srv\"", "sh -c 'rm -r build'",
    "find . -exec rm -rf {} ;",
])
def test_a_command_handed_to_another_program_is_still_judged(command):
    """The Rev-5 incident's own shape, one layer down: `powershell -Command`
    beside the rules it was meant to reinforce. Without this the program is
    `cmd` and the wall sees nothing."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    'bash -c "rm -r"', "sh -c 'rm -r'", 'powershell -Command "ri -r"',
    'cmd /c "rmdir /s"',
])
def test_a_closing_quote_stuck_to_the_last_flag_does_not_hide_it(command):
    """Found by the mutation pass, not by writing the tests.

    Every quoted case above ended on an argument, so the closing quote landed on
    a path and the flags were clean - and neutering the quote strip reddened
    NOTHING. A nested command that ends on its flag puts the quote on the flag
    instead (`-r"`), which stops being a short-flag cluster, and the wall goes
    quiet. One character, and only a mutation would have said so.
    """
    assert P.destructive(dict(P.DEFAULTS), command), command


def test_a_refusal_names_the_rule_that_refused_and_which_list_it_is_from():
    git = P.destructive(dict(P.DEFAULTS), "git push --force")
    shell = P.destructive(dict(P.DEFAULTS), "rm -rf /")
    assert "denied git operation" in git[0]
    assert "denied operation" in shell[0] and "destroys data" in shell[0]


def test_one_command_matching_one_rule_is_reported_once():
    """`cmd /c rm -rf x` is the same rule reached twice - by the nested command
    and by nothing else. A wall that counted it twice would make the log read
    like two attacks."""
    assert len(P.destructive(dict(P.DEFAULTS), "bash -c \"rm -rf /\"")) == 1


def test_recursion_alone_is_the_trigger_and_that_is_deliberate():
    """`-f` only silences the prompts. Requiring it would be a silent miss on
    the plainer spelling, which is the direction this project does not choose."""
    assert P.destructive(dict(P.DEFAULTS), "rm -r tree")
    assert P.destructive(dict(P.DEFAULTS), "rm -f one.txt") == [], \
        "a single forced delete is not the walled operation"


@pytest.mark.parametrize("key", ["deny_git", "deny_commands", "protected_paths",
                                 "budget_exempt"])
def test_the_shipped_template_and_the_built_in_floor_are_the_same_list(key):
    """Two copies of one constitution. An adopter who installs the template must
    not get a different floor from one who does not, and nothing but this test
    would notice them drifting."""
    assert P.parse(TEMPLATE)[key] == P.DEFAULTS[key]


def test_a_rule_with_no_program_is_refused_by_the_validator():
    """It would sit in the constitution looking like a wall and match nothing."""
    problems = P.validate({**dict(P.DEFAULTS), "deny_commands": ["rm -r", "  "]})
    assert any("denies nothing" in p for p in problems)


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
    R.park(repo, run_id, "P7")


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


# ── the review's fixes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("record", [
    ".sdlc/metrics/provider-smoke.jsonl",
    ".sdlc/metrics/reviews.jsonl",
    ".sdlc/metrics/guardrail.jsonl",
    ".sdlc/metrics/a-record-added-tomorrow.jsonl",
])
def test_every_metrics_record_is_a_path_only_the_clis_write(record):
    """`protected_paths` named files, so each new record had to be REMEMBERED
    onto it — and the next one was not. `provider-smoke.jsonl` became a gate
    the CLI answers 0/1/3 from while staying writable by any agent: a
    hand-written row claiming `runner_tier: agent, verdict: green` made the
    recurring gate report satisfied for a smoke that never ran. One glob
    covers the directory, including the record nobody has added yet."""
    assert P.matches(record, P.DEFAULTS["protected_paths"])


def test_ordinary_work_is_still_writable():
    """The negative control: the glob must not protect the repository."""
    for ordinary in ("src/app.py", "docs/metrics.md", "metrics.py"):
        assert not P.matches(ordinary,
                              P.DEFAULTS["protected_paths"]), ordinary


def test_the_shipped_constitution_protects_them_too():
    """The template an adopter installs and the floor the code enforces are
    two statements of one rule; item 63 recorded what happens when they drift."""
    assert '"**/metrics/*.jsonl"' in TEMPLATE


# ── an inline list splits on commas that are not inside quotes ──────────────
#
# `inner.split(",")` is the obvious way and it is wrong for any value that
# contains one. Nothing the installer WRITES contains a comma, which is why it
# never fired - and why it was waiting for the first person to hand-edit the
# file. Found by a test for `known_failures`, one layer up.


def test_a_quoted_comma_does_not_split_an_inline_list():
    assert P.parse('known_failures: ["test_thing[1,2]"]', "p.yml") == {
        "known_failures": ["test_thing[1,2]"]}
    assert P.parse('x: ["a,b", "c"]', "p.yml") == {"x": ["a,b", "c"]}


def test_the_lists_this_harness_actually_writes_are_unchanged():
    """The negative control. A splitter that also broke the ordinary case
    would be worse than the defect it fixes."""
    assert P.parse("block_on: [critical, high]", "p.yml") == {
        "block_on": ["critical", "high"]}
    assert P.parse("phases: [intake, design, test]", "p.yml") == {
        "phases": ["intake", "design", "test"]}
    assert P.parse("x: [1, 2, 3]", "p.yml") == {"x": [1, 2, 3]}
    assert P.parse("x: []", "p.yml") == {"x": []}


def test_an_unterminated_quote_means_what_it_meant_before():
    """This reader refuses what it cannot read, and a half-quoted list is not
    that: `_scalar` takes an unquoted word as a string, which is the answer the
    old splitter gave too. A malformed list must not change meaning quietly."""
    assert P.parse('x: ["unterminated, two]', "p.yml") == {
        "x": ['"unterminated, two']}


def test_the_stdlib_reader_agrees_with_pyyaml_on_the_case_it_got_wrong():
    """The crosscheck is the safety net for a change to this reader, so the
    case that motivated the change is put to it explicitly."""
    yaml = pytest.importorskip("yaml")
    for text in ('known_failures: ["test_thing[1,2]"]', 'x: ["a,b", "c"]',
                 "block_on: [critical, high]"):
        mine = P.parse(text, "p.yml")
        assert mine == yaml.safe_load(text), text
        assert P.crosscheck(text, mine, "p.yml") == "", text



# ── what the installer WRITES, this reader must READ ─────────────────────────


def _installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_keys",
                                                  ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_per_component_command_the_installer_writes_is_one_this_reader_keeps():
    """**The producer and the consumer of `components[]`, composed.**

    `sdlc-init.py` renders a per-component command block into every estate
    profile; `policy.components` is the only thing anywhere that reads that
    block back. Nothing held the two lists together, so `deploy_command` was
    emitted by the installer and dropped by the reader for as long as P9 has
    existed - a component's own deploy silently became the top-level one, or
    nothing at all.

    Item 38 found this shape and fixed the fields it was looking at. This is
    the rule instead of the fix: a command added to the writer fails here until
    the reader learns it.
    """
    emitted = set(_installer()._COMPONENT_COMMANDS)
    assert emitted, "the installer's per-component command list is empty"
    missing = sorted(emitted - set(P.COMPONENT_KEYS))
    assert not missing, (
        f"sdlc-init.py writes {', '.join(missing)} per component and "
        f"policy.COMPONENT_KEYS drops {'them' if len(missing) > 1 else 'it'}, "
        f"so every component's value is silently discarded")


def test_the_workspace_template_accepts_what_the_installer_emits():
    """The third spelling of the same list. A command the installer renders but
    the workspace schema rejects is one an adopter cannot declare in the file
    they are told to fill in before INIT."""
    mod = _installer()
    assert set(mod._COMPONENT_COMMANDS) <= mod._WS_COMPONENT_KEYS


def test_a_component_deploy_command_survives_the_round_trip(tmp_path):
    """Read back through the real reader, because a key in a tuple is not
    evidence that a value reaches a caller."""
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ngate_profile: team\n"
        "components:\n"
        '  - name: "api"\n    path: "api"\n    role: authoritative\n'
        '    deploy_command: "helm upgrade api"\n'
        '  - name: "web"\n    path: "web"\n    role: authoritative\n',
        encoding="utf-8")
    found = {c["name"]: c for c in P.components(root)}
    assert found["api"]["deploy_command"] == "helm upgrade api"
    assert "deploy_command" not in found["web"], "absent must stay absent"


# ── item 116: one profile block reader, not seven ────────────────────────────
#
# The seven hand-rolled block scanners disagreed about ONE thing — what counts
# as indentation — and every consequence of that disagreement was measured on a
# single file differing by a single INVISIBLE character. A non-breaking space is
# what a profile pasted out of a web page, a wiki or a chat client carries, and
# nothing anywhere refuses such a file: PyYAML reads it as junk without raising,
# so these hand readers are the only readers there are.

PROFILE_FILE = "profile.yml"
READER_SOURCE = Path(P.__file__).read_text(encoding="utf-8", errors="replace")

#: The one profile whose indentation the tests vary. It exercises every block a
#: reader asks for, so a single file answers the whole roster.
BLOCK_PROFILE = (
    "scope: estate\n"
    "gate_profile: solo\n"
    "components:\n"
    "{i}- name: alpha\n"
    '{i}  test_command: "COMPONENT suite"\n'
    "{i}  vcs:\n"
    "{i}    deny_raw_git: true\n"
    "\n"                            # a blank line INSIDE a block does not end it
    "{i}- name: beta\n"
    "vcs:\n"
    "{i}push_remote: origin\n"
    "gates:\n"
    "{i}G4: hard\n"
    "runners:\n"
    "{i}- name: hermes\n"
    "{i}  capabilities: tools\n"
    "phase_runners:\n"
    "{i}implementation: hermes\n")

SPACE_INDENT = "  "
NBSP_INDENT = chr(0xA0) + " "   # spelled, never typed: an invisible literal in source is one an editor can normalise away, and a check comparing spaces to spaces asserts nothing

#: Every profile block reader, with the call that drives it. **The roster is
#: DERIVED** (`profile_readers` below), so an eighth reader fails until it is
#: written down here; the CALLS are frozen, because item 67's lesson is that an
#: expectation read out of the implementation under test is a mirror, not a
#: test. Four of the seven answered identically either way even before the fix
#: and are kept as NEGATIVE CONTROLS — a check that only ever fires on the
#: broken three would not show that AGREEMENT is the property being asserted.
PROFILE_READERS = {
    "component_count": lambda t: P.component_count(t),
    "components": lambda t: [c["name"] for c in P.components(t)],
    "top_vcs": lambda t: P.top_vcs(t),
    "gate_overrides": lambda t: P.gate_overrides(t),
    "runners_declared": lambda t: P.runners_declared(t),
    "phase_runner_map": lambda t: P.phase_runner_map(t),
    "profile_find": lambda t: P.profile_find(t, "test_command"),
}


def indented_profile(tmp_path: Path, indent: str, name: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        BLOCK_PROFILE.replace("{i}", indent), encoding="utf-8")
    return root


def profile_readers(source: str | None = None) -> set[str]:
    """Every function that reads a profile BLOCK, found by AST.

    Derived rather than listed for item 115's reason: a roster goes stale the
    first time somebody adds a reader, and an eighth reader quietly missing from
    the agreement check is exactly the failure this item exists to end.
    """
    tree = ast.parse(READER_SOURCE if source is None else source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in ("_profile_block", "_profile_sections"):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in ("_profile_block", "_profile_sections")):
                found.add(node.name)
    return found


def own_profile_reads(source: str | None = None) -> set[str]:
    """Every function that names the profile FILE for itself, docstrings exempt.

    This is the divergence's SOURCE rather than its symptom: the two readers
    that opened the file for themselves are two of the three that disagreed with
    everyone else about what indentation is.

    **There is deliberately no docstring exemption**, which is a correction the
    mutation pass forced. Item 94's rule — docstrings exempt — is a rule about
    SUBSTRING sweeps, where a module's own prose naming the file is the false
    positive. This match is EXACT, so no sentence anybody writes can trip it and
    a docstring would have to consist of the bare filename to be seen at all.
    Item 113 struck the same eight lines for the same reason: a branch guarding
    a case nobody can write by accident is a branch no test can reach.
    """
    tree = ast.parse(READER_SOURCE if source is None else source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for lit in ast.walk(node):
            if isinstance(lit, ast.Constant) and lit.value == PROFILE_FILE:
                found.add(node.name)
    return found


def indent_disagreement(call, tmp_path: Path, name: str) -> str:
    """The comparison, extracted so a positive control can watch it COMPLAIN.

    Item 115's lesson, and the mutation pass made it again here: with the
    comparison inline in its own test, a mutation pointing both halves at the
    same profile — or spelling the invisible indent as two ordinary spaces —
    left the check green while it asserted nothing at all.
    """
    spaces = call(indented_profile(tmp_path, SPACE_INDENT, name + "-spaces"))
    invisible = call(indented_profile(tmp_path, NBSP_INDENT, name + "-invisible"))
    if spaces == invisible:
        return ""
    return (f"an invisible-space indent reads differently from a plain one: "
            f"{spaces!r} vs {invisible!r}. One reader means one answer.")


def old_rule_reader(target: Path) -> dict:
    """A block reader written the way three of the seven were.

    Kept as the positive control for `indent_disagreement`: this one MUST be
    caught, or the check that clears the seven real readers has cleared nothing.
    """
    out, inside = {}, False
    for line in P._profile_lines(target):
        if not line.strip():
            continue
        if line[:1] not in (" ", chr(9)):
            inside = line.strip().startswith("gates:")
            continue
        if inside and ":" in line.strip():
            gate, _, state = line.strip().partition(":")
            out[gate.strip()] = state.strip()
    return out


def test_the_indentation_check_can_fail(tmp_path):
    """The invisible indent must actually be invisible, and the comparison must
    actually complain — neither is worth assuming."""
    assert NBSP_INDENT != SPACE_INDENT and NBSP_INDENT.isspace()
    assert indent_disagreement(old_rule_reader, tmp_path, "control")


@pytest.mark.parametrize("reader", sorted(PROFILE_READERS))
def test_every_profile_block_reader_agrees_about_what_indentation_is(
        tmp_path, reader):
    """One file, one invisible character, and the readers must not split.

    Three of them did. `component_count` saw 0 components where `components`
    saw 2; `gate_overrides` dropped a `G4: hard` override; and `profile_find`
    answered a workspace question with a component's value — the very defect its
    own docstring records having been written to fix.
    """
    problem = indent_disagreement(PROFILE_READERS[reader], tmp_path, reader)
    assert not problem, f"{reader}: {problem}"


def test_an_invisible_indent_cannot_hide_a_component_from_the_solo_check(tmp_path):
    """The consequence that makes this a defect rather than a tidiness item.

    `component_count` feeding 0 to `tighten_only` makes `solo` selectable on a
    multi-component workspace — item 63's STRUCTURAL check, the one its own text
    calls unselectable from any policy.
    """
    root = indented_profile(tmp_path, NBSP_INDENT, "estate")
    problems = P.tighten_only(P.DEFAULTS, P.profile_dials(root),
                              P.component_count(root))
    assert any("solo" in p for p in problems), (
        "solo was accepted on a two-component workspace because an invisible "
        f"indent hid the components: {problems!r}")


@pytest.mark.parametrize("indent", (SPACE_INDENT, NBSP_INDENT))
def test_a_workspace_question_is_never_answered_with_a_components_value(
        tmp_path, indent):
    """Agreement alone cannot see a change that moves BOTH answers, and a
    mutation proved it: `profile_find` reading the components block made the
    space and invisible cases agree on the WRONG value.

    Nothing anywhere else held this property, though `profile_find`'s own
    docstring records it as the defect the function was written to fix —
    `stage.resolve` builds P0's and P5's exact-value blocks out of it, so an
    estate's prompt stated one component's suite as the workspace's.
    """
    root = indented_profile(tmp_path, indent, "estate" + str(len(indent)))
    assert P.profile_find(root, "test_command") is None
    assert P.component_count(root) == 2


def test_a_block_reader_honours_the_lines_it_is_handed(tmp_path):
    """The injection seam `runner_for` and `components` rely on to read the file
    ONCE — untested until a mutation ignoring it survived, because every fixture
    handed over lines identical to the ones on disk."""
    root = indented_profile(tmp_path, SPACE_INDENT, "injected")
    handed = ["gates:", "  G1: hard"]
    assert P.gate_overrides(root, handed) == {"G1": "hard"}
    assert P.gate_overrides(root) == {"G4": "hard"}


def test_an_invisible_indent_cannot_drop_a_gate_hardening(tmp_path):
    """A dropped override is a HARDENING lost, which is the wrong direction for
    a reader to be wrong in — and the reason `_indented` keeps a block OPEN when
    the indentation is strange rather than closing it early."""
    root = indented_profile(tmp_path, NBSP_INDENT, "hardened")
    assert P.gate_overrides(root) == {"G4": "hard"}


def test_the_profile_is_read_in_exactly_one_place():
    """`component_count` and `gate_overrides` opened the file for themselves,
    and they are two of the three readers that disagreed with everyone else."""
    assert own_profile_reads() == {"_profile_lines"}, (
        "more than one function names the profile file; a second read is where "
        "a second answer to 'what is indentation' comes from")


def test_the_one_read_guard_can_fail():
    """A guard nobody can watch fail is a guard nobody has checked — item 115's
    lesson, applied when the guard is WRITTEN rather than after a mutation pass
    has to find it."""
    source = (
        "def a():\n    p = x / 'profile.yml'\n"
        "def b():\n    q = y / 'profile.yml'\n"
        'def c():\n    """names the profile in prose only."""\n    return 1\n')
    assert own_profile_reads(source) == {"a", "b"}


def test_the_reader_roster_is_derived_so_an_eighth_reader_cannot_hide():
    """The agreement check is only as good as the list it runs over, and a
    hand-written list is stale the moment somebody adds a reader."""
    assert profile_readers() == set(PROFILE_READERS)


def test_the_roster_sweep_can_fail():
    source = ("def eighth(t):\n    return _profile_block(t, 'gates')\n"
              "def unrelated(t):\n    return 1\n")
    assert profile_readers(source) == {"eighth"}
