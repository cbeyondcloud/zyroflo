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
import builtins
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
from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import stage as STAGE  # noqa: E402
from tools.sdlc import state as S  # noqa: E402
from tools.sdlc import suite as SUITE  # noqa: E402
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


@pytest.mark.parametrize("command", [
    "find . -name '*.py' | xargs rm -rf",
    "xargs -a list.txt rm -rf",
    "( rm -rf build )",
    "(rm -rf build)",
    "echo $(rm -rf build)",
    "timeout 60 rm -rf build",
])
def test_the_six_spellings_that_walked_past_the_wall_are_refused(command):
    """Measured over the SHIPPED constitution before this existed: five positive
    controls blocked (`rm -rf build`, an `&&` chain, `sudo`, `cmd /c rmdir /s`,
    `find -exec rm -rf`) and these six were allowed.

    Three holes, one per shape. `xargs` carries a command the way `find -exec`
    does and was not a carrier; `timeout` runs the command after it the way
    `sudo` does and was not a prefix; and `(` was read as the program's name, so
    a parenthesis was a hat that hid `rm` from a wall that could see through
    `sudo`.
    """
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "xargs rm -rf build", "xargs -0 rm -rf build", "xargs -t -r rm -rf build",
    "xargs -n 5 rm -rf build", "xargs -I {} rm -rf {}",
    "xargs -a list.txt -n 1 rm -rf build", "xargs --null rm -r build",
    "find . -print0 | xargs -0 rm -rf",
])
def test_xargs_options_are_skipped_to_the_command_xargs_runs(command):
    """`xargs`'s own options stand between it and the command it runs, and a
    carrier that stopped at the first of them would judge `-0` as the program.
    The recognised options are named at `_XARGS_VALUE_FLAGS`; a value-taking one
    whose argument was NOT skipped is the same miss wearing the other face,
    because then `5` is the program."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "timeout 60 rm -rf build", "timeout 5s rm -r build",
    "timeout -s KILL 60 rm -rf build", "timeout --signal=KILL 60 rm -rf build",
    "timeout -k 5 60 rm -rf build",
    "stdbuf -o0 rm -rf build", "stdbuf -oL -eL rm -rf build",
    "timeout 60 sudo rm -rf /srv", "nohup timeout 60 rm -rf build",
])
def test_a_wrapper_that_takes_arguments_is_skipped_to_its_command(command):
    """`timeout 60 rm -rf /` is `rm -rf /` with a hat on, exactly as `sudo` is —
    but a wrapper with an ARGUMENT is not skipped by dropping one word, and
    dropping one word too many judges `-rf` instead. Both directions are a
    silent miss, so both spellings of the duration are pinned here."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "( rm -rf build )", "(rm -rf build)", "( cd build && rm -rf . )",
    "echo $(rm -rf build)", "echo $( rm -rf build )",
    "echo `rm -rf build`", "true && (rm -r build)",
])
def test_grouping_and_substitution_are_judged_by_what_runs_inside(command):
    """A parenthesis is not a program. Measured: `( rm -rf build )` tokenised to
    `['(', 'rm', '-rf', 'build', ')']`, whose program is `(`, and
    `echo $(rm -rf build)` to `['echo', '$(rm', ...]`, whose program is `echo` —
    in both the wall saw a name no rule is about while `rm` ran. So the wrappers
    come off before the line is cut into commands."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    'echo "rm -rf x"', "echo 'rm -r y'", 'grep -F "$(cat list)"',
    'python -c "print(sorted(paths))"', "xargs --help", "timeout --help",
])
def test_a_quoted_string_is_data_and_a_wrapper_is_not_an_accusation(command):
    """The false-positive side of the three shapes above. GREEN BY DESIGN — each
    of these is allowed before this change and must still be allowed after, and
    that is the whole point of writing them down.

    Measured first, because the design turns on it: this tokeniser does not
    respect quoting at all. `echo "rm -rf x"` becomes `['echo', 'rm', '-rf',
    'x']` — the quotes come off each word and the words stay in ONE command
    whose program is `echo`. It is allowed because of the PROGRAM, not because
    the quotes were honoured. So stripping a wrapper must never promote quoted
    text into a command of its own: do that and `echo "rm -rf x"` is refused for
    printing a sentence. `_GROUPING` therefore names `(`, `)`, `$(` and a
    backtick, and deliberately not a quote.
    """
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    # Brace command grouping. A `{ …; }` group is a hat like a subshell `( … )`,
    # and only the paren was coming off. Measured allowed before this change.
    "{ rm -rf build; }", "{ rm -rf build }", "true && { rm -rf build; }",
    # A prefix flag that takes a SEPARATE value: the value was eaten as the
    # program. `sudo -u root rm -rf /` ran `root` as the command's name to a wall
    # that could already see through a bare `sudo`.
    "sudo -u root rm -rf /", "sudo -H -u root rm -rf /srv",
    "env -u FOO rm -rf build", "doas -u root rm -rf /srv",
    # Wrappers that run the command after them, exactly as `sudo` and `timeout`
    # do, but were not named as prefixes.
    "nice -n 10 rm -rf build", "nice rm -r build",
    "ionice -c3 rm -rf build", "ionice -c 3 rm -rf build",
    "setsid rm -rf build", "parallel rm -rf ::: a b",
    # `eval` runs its argument string as a command.
    'eval "rm -rf x"', "eval rm -r build",
    # A shell reached through its own variable is still a shell.
    "${SHELL} -c 'rm -rf x'", "$SHELL -c \"rm -r build\"",
    # A backslash line-continuation is one command, not two: the `-rf` landed on
    # the second segment and the first was a bare `rm`.
    "rm \\\n-rf build", "rm \\\n  -rf  build",
])
def test_the_second_family_of_wall_escapes_is_refused(command):
    """The escapes W31 found still open after item 157 hardened the first family
    (`xargs`, `timeout`, subshell `( … )`). The nineteen spellings below (W31's
    fourteen still-allowed families, several spelled more than one way) walked
    past the shipped constitution; each was MEASURED allowed before this change
    and is refused after.

    Same idiom as item 157: declared shapes, never a grammar. Brace grouping and
    a backslash continuation come off before the line is cut into commands; the
    value-taking flags of `sudo`/`env`/`doas`/`nice`/`ionice` are named so their
    VALUE is not read as the program; `nice`/`ionice`/`setsid`/`parallel`/`eval`
    are prefixes; and `$SHELL`/`${SHELL}` normalises to the shell interpreter."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    # A shell VARIABLE reference is not command grouping: `${…}` and `{a,b}`
    # brace expansion are adjacent to non-space and must survive untouched.
    "echo ${HOME}/build", "mkdir -p build/{a,b,c}", "cp file.{txt,bak} /tmp",
    # The same wrappers doing legitimate work — allowed because the PROGRAM they
    # run is not destructive, not because the wrapper was ignored.
    "sudo -u deploy systemctl restart web", "env -u TERM printenv",
    "nice -n 10 make -j4", "ionice -c3 tar czf backup.tgz data/",
    "setsid mydaemon --start", "parallel gzip ::: logs/one.log logs/two.log",
    "eval echo hello", "${EDITOR} notes.txt",
    # A backslash-continued line whose joined command is ordinary.
    "echo one \\\n two",
    # A brace stuck to a quote is not a group: the PROGRAM is echo, exactly as
    # `echo "rm -rf x"` is data. This is the non-vacuous side — a matcher that
    # treated every brace as grouping would refuse it (the greedy-brace mutant
    # does), the way item 157's quoted-string control fires if a quote is added
    # to `_GROUPING`.
    'echo "{ rm -rf x; }"',
    # `env -S` with an ordinary command: the split falls through to `echo`, so a
    # legitimate `-S` is not refused (only a destructive one is, below).
    'env -S "echo hi"',
])
def test_the_second_family_leaves_legitimate_work_alone(command):
    """The false-positive side, and the point of writing it down. Item 128
    measured that over-blocking is this wall's failure mode, so widening what it
    catches must not refuse a legitimate command. Each of these uses one of the
    newly-handled shapes and must stay allowed both before and after — GREEN BY
    DESIGN. A `${VAR}` or `{a,b}` is not a group; a wrapper whose command is
    `make`/`tar`/`gzip` is not an accusation."""
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    'env -S "rm -rf /"', "#!/usr/bin/env -S rm -rf /",
])
def test_env_split_string_does_not_smuggle_a_command_past_the_wall(command):
    """A correction to this change's own first cut. GNU `env -S` RE-SPLITS its
    value into a whole sub-command, so listing `-S` among env's value-consuming
    flags (like `-u NAME`) made the wall eat `rm -rf /` as an opaque value and
    ALLOW it — a wall escape the second-family fix introduced, reproduced by the
    reviewer in both the inline and shebang (`#!/usr/bin/env -S …`) forms.

    The tokeniser strips quotes before it sees a word, so `env -S "rm -rf /"` and
    `env -S rm -rf /` are one token stream and `-S`'s value cannot be isolated as
    a single opaque command anyway. So `-S` is treated as a lone flag: the
    command it splits into falls through and is judged directly. Both forms were
    allowed at the tip that added `-S`; both are refused now. `env -u FOO rm -rf`
    stays blocked and `env -S "echo hi"` stays allowed (both pinned above)."""
    assert P.destructive(dict(P.DEFAULTS), command), command


# ── a value STUCK to its flag ────────────────────────────────────────────────
#
# The gap item 157's correction left behind, and it said so in its own comment:
# "the tokeniser strips quotes before it sees a word, so `env -S "rm -rf /"` and
# `env -S rm -rf /` are one token stream". True of the SEPARATE-word spelling,
# and only of that one. Written with the value attached — `--split-string=rm -rf
# /`, `-S"rm -rf /"`, `-Srm -rf /` — the flag and the first word of the command
# it carries are ONE token, which equals no rule and names no program.
#
# Measured over the shipped constitution before this change, by running each
# spelling through `P.destructive`: every one of the env spellings below was
# ALLOWED, and so was `cmd /c=rmdir /s /q x`. Measured the other way too, on the
# machine rather than from the manual page: GNU coreutils 8.32 runs
# `env --split-string='echo hi'`, `env -S'echo hi'`, `env -Secho hi` and
# `env --split-string=echo\ MARKER`; `cmd.exe /c=rmdir /s /q DIR` deleted a
# real directory at exit 0; and `truncate --size=0 FILE` really truncated one.


@pytest.mark.parametrize("command", [
    # `env`: the value of `--split-string=`/`-S` is not a value, it is a whole
    # sub-command that GNU env re-splits and runs.
    'env --split-string="rm -rf /"',
    "env --split-string=rm -rf /",
    "env --split-string=rm\\ -rf\\ /",
    "env --split-string='rm -r build'",
    "#!/usr/bin/env --split-string=rm -rf /",
    '#!/usr/bin/env --split-string="rm -rf /"',
    'env -S"rm -rf /"',
    "env -S'rm -r build'",
    "env -Srm -rf /",
    'env --split-string="Remove-Item -Recurse -Force ."',
    'env --split-string="rmdir /s /q build"',
    # …and the same hat under another hat, because a wrapper stacks.
    'sudo env --split-string="rm -rf /"',
    'nohup env --split-string="rm -rf /"',
    # (`env --split-string="sudo rm -rf /srv"` is NOT here: it was already
    # blocked, by accident. `sudo` is not a `-flag`, so the flag-skipping loop
    # stopped on it and left `rm` as the program — a pass that documents the
    # behaviour instead of defending it.)
    # `cmd`: `=` is an argument delimiter to cmd.exe, so `/c=rmdir` IS `/c`
    # followed by a command — measured by deleting a directory with it.
    "cmd /c=rmdir /s /q x",
    "cmd.exe /c=rd /s .",
    "cmd /k=del /s /q build",
    'cmd /c="rmdir /s /q x"',
    # The attached spelling on the other carriers. MEASURED not to run at all
    # (`powershell -Command=…`, `powershell -Command:…`, `bash -c=…`, `sh -c=…`
    # each fail, and `find` answers `unknown predicate '-exec='`), so these cost
    # nothing to refuse and the carrier list stays one rule rather than four.
    'powershell -Command="Remove-Item -Recurse -Force ."',
    'bash -c="rm -rf /"',
    "find . -exec=rm -rf {} +",
    # The rule table's own missing long spelling. `truncate -s` knew one of the
    # two ways GNU truncate spells its size, so BOTH `--size 0` and `--size=0`
    # emptied a file with the wall watching.
    "truncate --size=0 important.log",
    "truncate --size 0 important.log",
])
def test_a_value_stuck_to_its_flag_is_still_the_command_it_carries(command):
    """Item 157 isolates a flag's value only when the value is a SEPARATE word,
    so no attached `--flag=value` spelling was ever split — a pre-existing
    limitation of the matcher, not something item 157 introduced.

    Two shapes reach data through it, and one rule-table gap sits beside them.
    A flag whose value is a COMMAND (`env --split-string=`, `-S`) hides the
    command inside one token; a CARRIER flag with its command stuck on by an
    `=` (`cmd /c=rmdir`) hides it the same way one layer out. Both are declared
    per flag — `_COMMAND_VALUE_FLAGS` and the `=` spelling of `_RUN_FLAGS`/
    `_EXEC` — rather than by splitting every `=` in the line, which item 128's
    finding forbids and which the test below measures the cost of.
    """
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    # Item 157's load-bearing property, restated because this change is the one
    # that could break it: the tokeniser does NOT honour quotes, and these are
    # allowed because the PROGRAM is `echo`, not because the quotes were read.
    'echo "rm -rf x"', "echo 'rm -r y'", 'echo "{ rm -rf x; }"',
    "confirm -rf plan.txt", "echo charm -rf", "grep -rf patterns.txt src/",
    # A legitimate `--flag=value` whose value is destructive-looking TEXT. The
    # flag is only a carrier when the PROGRAM is one, which is the same sentence
    # as the `echo` controls above, one layer down.
    'git commit --message="rm -rf the old build step"',
    'git commit --message="remove-item -recurse notes"',
    "grep --include=*.py -rn 'rm -rf' src/",
    "echo --split-string=rm -rf /",
    "mvn -q test -Dmaven.test.skip=false",
    "docker run --entrypoint=rm alpine -rf /data",
    # The newly-split flags doing legitimate work: what `--split-string=` splits
    # into is judged, and `make` is not an accusation.
    'env --split-string="echo hi"', 'env -S"echo hi"',
    "env --split-string=make\\ -j4", "env --unset=TERM printenv",
    "cmd /c=npm run build", 'cmd /c="npm run build"',
    # The attached forms of every other wrapper, which were ALREADY blocked or
    # allowed correctly before this change and must not move.
    "timeout --signal=KILL 60 make -j4", "sudo --user=deploy systemctl restart web",
    "nice --adjustment=10 make -j4", "xargs --arg-file=list.txt gzip",
    "truncate --help",
    # A backslash-escaped space is removed before the words are matched, so
    # ordinary work that contains one must still be read by its program.
    "cp my\\ file.txt /tmp", "echo one\\ two",
    # T361 review-W350's benign sweep: real-world safe commands through the
    # same carriers this round touched, staying allowed before and after.
    "env --split-string='ls -la'", "find . -name '*.py' -exec grep -n TODO {} ;",
    "timeout 5 python script.py", "sudo -u root systemctl status nginx",
])
def test_an_attached_value_does_not_turn_ordinary_work_into_an_accusation(command):
    """The false-positive side, and the reason the split is DECLARED. Item 128
    measured that over-blocking is this wall's failure mode, so the widening
    above has to leave every one of these alone — GREEN BY DESIGN, before and
    after."""
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    "xargs --replace={} rm -rf {}", "xargs --max-lines=5 rm -rf build",
    "xargs --eof=STOP rm -rf build", "xargs --process-slot-var=N rm -rf build",
    "xargs --max-args=5 rm -rf build", "xargs --arg-file=list.txt rm -rf",
])
def test_an_attached_value_is_split_only_where_it_is_declared(command):
    """What a blanket `=` split would have cost, measured rather than assumed.

    `_XARGS_VALUE_FLAGS` names the options whose argument is a SEPARATE word and
    deliberately leaves out `--replace`, `--eof` and `--max-lines`, because GNU
    xargs attaches theirs. Every spelling here is BLOCKED today for exactly that
    reason: `--replace={}` is one unrecognised flag word, so the command begins
    at `rm`. Split every `=` in the line and `{}` becomes a word of its own, the
    carrier stops there, and `xargs --replace={} rm -rf {}` goes from refused to
    allowed — a wall escape introduced by widening the wall.

    So the split is declared per flag, and this is the control that says so:
    these six must stay blocked, before and after.
    """
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command, denied", [
    # A quote typed into the MIDDLE of the token leaves the flag carrying only
    # the quote, and the command in the words after it - exactly where it was
    # before. `env -S" rm -rf /"` is what a shell hands GNU env as `-S rm -rf /`.
    ('env -S" rm -rf /"', True),
    ('env --split-string=" rm -rf /"', True),
    ("env -S\"\" rm -rf /", True),
    # The opening quote lands INSIDE the token, so the value begins with it and
    # the per-word quote strip above never reaches it. MEASURED (GNU coreutils
    # 8.32): `env --split-string='FOO=1 echo MARKER'` and `env -SBAR=2 echo
    # MARKER` both run, so an assignment inside the split string is real - and
    # an assignment still wearing its quote (`"FOO=1`) is not an assignment to
    # the matcher, so it is not dropped and `rm` never becomes the program.
    ('env --split-string="FOO=1 rm -rf /"', True),
    ('env --split-string="RUNNER=1 sudo rm -rf /srv"', True),
    ('env --split-string="FOO=1 echo hi"', False),
    # A flag carrying NOTHING carries no command, and a wrapper with no command
    # is not a command spelled `-S`.
    ("env -S", False),
    ("env --split-string=", False),
])
def test_a_flag_carrying_nothing_is_a_flag_and_not_a_command(command, denied):
    """Found by the mutation pass, not by writing the tests.

    The quotes have to come off the VALUE as well as off the word. Every word
    is unquoted before it is read, but that strips the ENDS of a word, and an
    attached value puts the opening quote in the middle - `--split-string="rm`
    strips to itself. Leave the value quoted and `"FOO=1` stops being an
    assignment the tokeniser drops, so the command behind it never becomes the
    program and the wall goes quiet on the spelling it had just learned to see.
    """
    assert bool(P.destructive(dict(P.DEFAULTS), command)) is denied, command


def test_a_wrapper_may_not_eat_the_command_a_flag_of_its_own_carried(monkeypatch):
    """The invariant that makes `_COMMAND_VALUE_FLAGS` safe to extend.

    Only `env` declares a command-carrying flag today, and `env` eats no
    positional words - so the line that zeroes them cannot be reached through
    the shipped table, and a mutation pass run only against that table would
    report it as dead code rather than as the guard it is. Point the table at a
    wrapper that DOES eat a positional and the difference is a wall escape:
    `timeout` swallows `rm` into its duration slot and judges `-rf` as the
    program.
    """
    monkeypatch.setitem(P._COMMAND_VALUE_FLAGS, "timeout", ("--run",))
    assert P.destructive(dict(P.DEFAULTS), "timeout --run=rm -rf build")


# ── round 2, finding 1: an env option carried ALONGSIDE the real command ────
#
# Round 1's `_attached_value` isolates the flag's value and hands it back as
# the program - but stopped there. When that value's own first word is one
# of env's OWN options (`-v`, `-i`/`--ignore-environment`, `-u NAME`/
# `--unset=NAME`), THAT option was taken as the program and the real command
# right behind it was judged as its arguments. MEASURED: `env -S'-v rm -rf
# victim'` printed env's own `executing: rm` trace and deleted the
# directory; the door agreed (`ALLOWED []`). `-u FOO` is the odd one out: it
# was already allowed at the PRE-T361 tip too, by an unrelated accident (the
# whole attached word was popped as an unrecognised flag and `FOO` was left
# to be misread as the program) - one fix, two different prior failures.


@pytest.mark.parametrize("command", [
    "env -S'-v rm -rf victim'", "env -S-v rm -rf victim",
    "env --split-string='-v rm -rf victim'",
    "env -S'-i rm -rf victim'", "env -S-i rm -rf victim",
    "env --split-string='--ignore-environment rm -rf victim'",
    "env -S'--unset=FOO rm -rf victim'",
    "env --split-string='--unset=FOO rm -rf victim'",
    "env -S'-u FOO rm -rf victim'", "env --split-string='-u FOO rm -rf victim'",
])
def test_an_env_option_carried_with_the_split_string_does_not_hide_the_command(command):
    """FAILS against the round-1 fix: the carried option replaces `words[0]`
    and the loop stops, so the option itself - not `rm` - is judged as the
    program and nothing names it. Passes once the reconstructed words are fed
    back through the SAME option-skipping loop that already lets the
    separate-word spelling `env -u FOO rm -rf build` fall through correctly -
    converging the two spellings rather than adding a third path."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "env -S'echo hi'", "env --split-string='-u TERM printenv'",
    "env -S'-i printenv'", "env --split-string='-v make -j4'",
])
def test_an_env_option_carried_with_ordinary_work_is_not_an_accusation(command):
    """The false-positive side. `-u TERM printenv` and `-i printenv` run
    something that is not destructive, and must stay allowed exactly as the
    separate-word spelling `env -u TERM printenv` already does - GREEN BY
    DESIGN, before and after."""
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


# ── round 2, finding 2: a stray -exec-family token does not disable xargs ───
#
# `_carried_command(words, _EXEC)` scanned every word of every segment, so a
# decoy `-exec=`/`-ok=`/`-execdir=`/`-okdir=` token anywhere in an xargs argv
# hijacked the nested-command lookup before the xargs-specific one ever ran.
# MEASURED: `echo x | xargs rm -rf v2 -- -exec=foo` deleted v2; the door
# agreed (`ALLOWED []`).


@pytest.mark.parametrize("command", [
    "xargs -exec=foo rm -rf build", "xargs -ok=foo rm -rf build",
    "xargs -execdir=foo rm -rf build", "xargs -okdir=foo rm -rf build",
    "xargs rm -exec=foo -rf build",
    "xargs rm -rf -- -exec=foo", "xargs rm -rf build -- -ok=foo",
    "find . -print0 | xargs -0 rm -rf -- -exec=x",
    "xargs -0 rm -rf -- -exec=x",
])
def test_a_stray_exec_family_token_does_not_disable_the_xargs_lookup(command):
    """FAILS against the round-1 fix: unifying find's `-exec=` carrier with
    `_RUN_FLAGS` under one shared helper made the scan `=`-partition ANY
    word of ANY segment, and that scan runs before the xargs-specific lookup
    and pre-empts it whenever it matches - `xargs` never even reaches
    `_xargs_command_at`. `-exec=` protects nothing real (find refuses it as
    an unknown predicate, per `_EXEC`) so gating the scan to find's own
    segment costs nothing and this passes after."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "xargs --exec=foo echo hi", "xargs -0 echo hi -- -exec=foo",
])
def test_a_stray_exec_family_token_on_ordinary_xargs_work_is_not_an_accusation(command):
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    "grep -exec rm -rf pattern.txt", "echo -exec rm -rf note.txt",
])
def test_an_exec_family_word_is_only_a_carrier_when_the_program_is_find(command):
    """The gate's OWN false-positive control, not just its true-positive
    one. Neither `grep` nor `echo` is find or xargs, so an `-exec` sitting in
    their own arguments is exactly that - an argument, not a carrier -
    and `rm -rf` behind it never becomes anyone's program. Kills a
    mutant the other controls in this file do not: `exec_ok` hardcoded to
    always `True` passes every OTHER test here, because the xargs check now
    runs unconditionally ahead of it and every find-carrier control already
    has `find` as its program - this is the one that needs a program which
    is neither."""
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


# ── round 2: closing both findings must not open a third ────────────────────
#
# Gating the exec scan to `find` by asking `_program(words[0]) == "find"`
# AFRESH at every recursion level breaks on two exec-family tokens in a row:
# the first extraction leaves a nested list whose own first word is the
# SECOND token, not literally `find`, so a gate re-derived from that word
# stops right there - the same shape of hole as finding 2, one level in.
# MEASURED against this fix's own first draft, by the adversarial corpus
# this round generated: `find . -exec -exec rm -rf x ;` is blocked at BOTH
# the pre-branch source and the round-1 fix (neither version gates the scan
# at all, so an
# unbroken chain of exec-family tokens is peeled all the way through by
# accident) and flipped to allowed under a find-only gate that does not
# survive its own recursion.


@pytest.mark.parametrize("command", [
    "find . -exec -exec rm -rf x ;", "find . -exec -ok rm -rf x ;",
    "find . -ok -exec rm -rf x ;", "find . -exec -execdir rm -rf x ;",
    "find . -execdir -okdir rm -rf x ;",
    "find . -exec=foo -exec rm -rf x ;",
    "find . -ok=foo -execdir rm -rf x ;",
])
def test_a_chain_of_exec_family_tokens_still_reaches_the_real_program(command):
    """A decoy does not have to be the only extra token on the line. This
    composition must stay blocked, as it was before T361 ever touched this
    file and after round 1 - the fix's own find-only gate has to be
    INHERITED down an exec-extraction chain, not re-derived from a word
    that is no longer `find`, or this same round would trade finding 2 for a
    narrower version of it."""
    assert P.destructive(dict(P.DEFAULTS), command), command


# ── round 2: exec_ok inherited into an xargs segment must not outrank it ────
#
# Found by the MUTATION pass, not by the corpus this round generated first -
# the corpus never composed "find's -exec hands off to xargs, and that xargs
# invocation carries a decoy of its own". `exec_ok=True`, inherited from
# find's own peel, made the (gated, but still ungated-by-PROGRAM) exec scan
# run before `_xargs_command_at` on the xargs segment underneath, so a
# trailing `-exec=` decoy on THAT xargs invocation hijacked it exactly as
# finding 2 did at the top level. MEASURED: blocked at the pre-branch source,
# allowed at the round-1 fix (expected - finding 2's own shape) but ALSO
# allowed by this
# round's first fix, before the xargs check was moved ahead of the
# exec_ok-gated one.


@pytest.mark.parametrize("command", [
    "find . -exec xargs rm -rf {} -- -exec=foo ;",
    "find . -exec xargs -0 rm -rf {} -exec=foo ;",
    "find . -ok xargs rm -rf {} -- -ok=foo ;",
])
def test_exec_ok_inherited_into_an_xargs_segment_does_not_outrank_it(command):
    """The xargs-specific lookup must resolve a segment whose OWN program is
    xargs before an inherited `exec_ok` gets a chance to misread that
    segment's own decoy as another find-style carrier - the same ordering
    `_INTERPRETERS` already had (checked unconditionally, before anything
    gated). FAILS against this round's own first draft (find-only gate,
    two-pass `_exec_carried_command`, and sticky `exec_ok` - everything
    except the check ORDER); passes once the xargs check runs first."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "find . -exec xargs echo hi -- -exec=foo ;",
    "find . -exec xargs -0 printenv -exec=foo ;",
])
def test_exec_ok_inherited_into_ordinary_xargs_work_is_not_an_accusation(command):
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


# ── round 3 (T361 review-W350, finding 1): an `=`-decoy run flag still
# outraces the BARE one for an interpreter ──────────────────────────────────
#
# `_exec_carried_command` was given a bare-first pass so an `=`-decoy could
# not pre-empt a real bare `-exec` behind it - but the interpreter branch of
# `_commands_in` kept calling the shared `_carried_command` directly, which
# matches bare-or-`=` at each word left to right. So an `=`-spelled decoy
# placed BEFORE the real run flag still wins the race there, and the real
# carried command is never read. MEASURED (review-W350.md, finding 1): all
# twelve spellings below are blocked at the pre-branch source and allowed by
# the round-2 fix.


@pytest.mark.parametrize("command", [
    "cmd /k=x /c rmdir /s /q victim",
    "cmd /c=x /c rmdir /s /q victim",
    "bash -c=x -c 'rm -rf victim'",
    'sh -c=x -c "rm -rf victim"',
    "zsh -c=x -c 'rm -rf victim'",
    'powershell -com=x -Command "Remove-Item -Recurse victim"',
    "sh -c=x -c 'shred victim'",
    "find . -exec sh -c=x -c 'rm -rf victim' ;",
    "sudo sh -c=x -c 'rm -rf victim'",
    "timeout 5 sh -c=x -c 'rm -rf victim'",
    "cmd /k=x /k rmdir /s /q victim",
    "bash --login -c=x -c 'rm -rf victim'",
])
def test_a_run_flag_eq_decoy_does_not_outrace_the_bare_one_for_an_interpreter(command):
    """FAILS against the round-2 fix: the interpreter branch calls
    `_carried_command`
    directly, which returns the FIRST match - bare or `=` - left to right, so
    an `=`-decoy standing before the real bare flag wins and the real carried
    command is never read. Passes once the interpreter branch is given the
    same bare-first-then-`=`-fallback discipline `_exec_carried_command`
    already has."""
    assert P.destructive(dict(P.DEFAULTS), command), command


@pytest.mark.parametrize("command", [
    "sh -c=x echo hi", "bash -c 'git status'", "cmd /c echo hello",
    'powershell -Command "Get-ChildItem"',
])
def test_a_run_flag_eq_decoy_alone_is_still_read_as_the_carried_command(command):
    """The false-positive side: with no bare run flag racing it, the `=`
    spelling must still fall through to its `=`-fallback and carry the real
    command exactly as before - GREEN BY DESIGN, before and after."""
    assert P.destructive(dict(P.DEFAULTS), command) == [], command


@pytest.mark.parametrize("command", [
    "cmd /k=x /c rmdir /s /q victim",
    "cmd /c=x /c rmdir /s /q victim",
    "bash -c=x -c 'rm -rf victim'",
    'sh -c=x -c "rm -rf victim"',
    "zsh -c=x -c 'rm -rf victim'",
    'powershell -com=x -Command "Remove-Item -Recurse victim"',
    "sh -c=x -c 'shred victim'",
    "find . -exec sh -c=x -c 'rm -rf victim' ;",
    "sudo sh -c=x -c 'rm -rf victim'",
    "timeout 5 sh -c=x -c 'rm -rf victim'",
    "cmd /k=x /k rmdir /s /q victim",
    "bash --login -c=x -c 'rm -rf victim'",
])
def test_a_run_flag_eq_decoy_is_refused_by_the_process_not_only_by_the_function(
        repo, command):
    """A CLI's contract is a process, not a function (CLAUDE.md): an
    in-process call cannot tell a refusal from a traceback. Spawned with
    `sys.executable` against policy.py's own `--git` door - the one every
    runner asks, not only Claude Code's."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "policy.py"),
         "--target", str(repo), "--git", command],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert done.returncode == 1, (command, done.returncode, done.stdout, done.stderr)
    assert "REFUSED" in done.stdout, done.stdout


# ── round 3 (T361 review-W350, finding 2): the docstring's "equivalent"
# mutant is not equivalent ──────────────────────────────────────────────────
#
# `_exec_carried_command`'s docstring argued that collapsing it to
# `_carried_command(words, _EXEC)` (bare-and-`=` combined, no priority) is
# EQUIVALENT, because `_commands_in`'s inherited `exec_ok` gate rescues it
# one recursion later for every reachable case. MEASURED false (review-
# W350.md, finding 2): the rescue assumes the recursive call reaches the
# exec scan, and it does not when the decoy's own extracted value names an
# INTERPRETER - that recursive call takes the interpreter branch instead,
# a trailing `-c`/`-Command` inside the decoy's own payload satisfies THAT
# branch's carried-command lookup, `nested is None` is false, and the real
# `-exec rm -rf victim` behind the decoy is never read.


@pytest.mark.parametrize("command", [
    "find . -exec=sh -exec rm -rf victim -c echo hi ;",
    "find . -exec=bash -exec rm -rf victim -c ls ;",
    "find . -ok=sh -exec rm -rf victim -c echo hi ;",
    "find . -exec=cmd -exec rm -rf victim /c echo hi ;",
    "find . -exec=sh -execdir rm -rf victim -c echo hi ;",
    "find . -execdir=sh -exec rm -rf victim -c echo hi ;",
    "find . -exec=powershell -exec rm -rf victim -Command echo ;",
    "find . -exec=sh -exec shred victim -c echo hi ;",
    "find . -exec=sh -exec rm -rf victim -c 'echo hi' ;",
    "find . -okdir=sh -exec rm -rf victim -c echo ;",
    "find . -exec=zsh -ok rm -rf victim -c echo ;",
])
def test_the_bare_first_pass_is_not_equivalent_to_the_combined_scan(command):
    """Already green at the round-2 fix - the bare-first pass is what keeps
    it so.
    Turned red by mutating `_exec_carried_command` down to
    `_carried_command(words, _EXEC)`, exactly as the docstring used to argue
    was equivalent: the mutant's combined scan matches the FIRST `-exec…`
    word (bare or `=`) it sees, which for these commands is the `=`-decoy,
    and hands its value to the interpreter it names instead of continuing to
    the real `-exec` behind it. This is the test the corrected docstring
    points at."""
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
    # T382: without a stored digest the status is "unverified" (cannot tell who
    # changed the file), not "preserved" (which requires a digest to compare).
    # OLD: assert op["status"] == "preserved" and op["rel"].endswith(".new")
    assert op["status"] in ("preserved", "unverified") and op["rel"].endswith(".new")
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
    # Item 129. `list_from_lines` is `profile_list`'s body, factored out so
    # the WRITER of this document can ask exactly what its readers ask.
    # Driven through the one door here, because a parser that never meets
    # `_profile_lines` sits outside the refusal this table exists to prove.
    "list_from_lines": lambda t: P.list_from_lines(P._profile_lines(t),
                                                   "phases"),
    # Item 117's own two, and they joined this table because the DERIVED sweep
    # below failed until they did — the guard doing its job on the very commit
    # that adds readers, which is the only proof that it will do it on the next.
    "profile_blocks": lambda t: P.profile_blocks(P._profile_lines(t)),
    "profile_top_value": lambda t: P.profile_top_value(t, "gate_profile"),
    # `runner_for` reads the file once and hands the lines to the two block
    # readers below it. Widening the sweep to `_profile_lines` is what surfaced
    # it — it had been reading the profile since item 105 and no agreement check
    # had ever driven it.
    "runner_for": lambda t: P.runner_for(t, "implementation"),
    # Item 118's. It read the file only through `profile_find` until it learned
    # the block spelling, and now reads the lines itself to hand ONE walk to
    # both spellings — so the agreement check drives it for the first time.
    "profile_list": lambda t: P.profile_list(t, "known_failures"),
    # Item 222's. The crosscheck's value half asked `scalar_from_lines`, a
    # TOP-LEVEL-only reader, while every consumer asks `profile_find`, which
    # reads at any depth — so it now reaches `_profile_entry` and the DERIVED
    # sweep below caught it the moment it did, which is the guard doing its job
    # on the very commit that adds a reader.
    "_value_disagreement": lambda t: P._value_disagreement(
        "profile.yml", P._profile_lines(t), {"gate_profile": "solo"}),
}


def indented_profile(tmp_path: Path, indent: str, name: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        BLOCK_PROFILE.replace("{i}", indent), encoding="utf-8")
    return root


#: The three functions that ARE the reader. Anything else calling one of them is
#: a reader of the profile and belongs in the agreement roster. `_profile_lines`
#: joined this list with item 117: the eighth copy read top-level keys and no
#: block at all, so a sweep that only knew about blocks was the reason it could
#: hide in plain sight.
#: `_profile_entry` joined by item 118. It is the same shape as the other three
#: — it takes `lines` and CANNOT open the file — so it is an internal whose
#: callers are readers, not a reader itself.
READER_INTERNALS = ("_profile_block", "_profile_sections", "_profile_lines",
                    "_profile_entry")


def profile_readers(source: str | None = None) -> set[str]:
    """Every function that reads the profile, found by AST.

    Derived rather than listed for item 115's reason: a roster goes stale the
    first time somebody adds a reader, and a reader quietly missing from the
    agreement check is exactly the failure these two items exist to end.
    """
    tree = ast.parse(READER_SOURCE if source is None else source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in READER_INTERNALS + ("_profile_source",):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in READER_INTERNALS):
                found.add(node.name)
    return found


#: The modules the one-read guard sweeps. **Widened from `policy.py` by item
#: 117**, and the reason is measured rather than tidy: `intake.profile_value`
#: was a whole OTHER module's profile reader with its own rule, disagreeing with
#: `profile_find` on the same file, and item 116's guard could not see it
#: because that sweep was scoped to one file. A guard that can only find the
#: copies in the module it already fixed is a guard that finds nothing new.
#: **Item 129 replaced the roster with a SWEEP, and the comment above is why.**
#: It said in as many words that "a guard that can only find the copies in the
#: module it already fixed is a guard that finds nothing new" - and then named
#: four modules by hand. `learning.py` was not among them, and `learning.render`
#: is the only code in the harness that WRITES this document: it read the inline
#: spelling alone, so a block-form declaration read as absent and the writer
#: deleted every entry somebody had put there. A hand-written roster is how it
#: sat outside a guard whose whole subject it was.
def shipped_sources() -> dict[str, str]:
    """Every shipped module, by import path, read off the filesystem.

    The filesystem is the authority on what ships, the way `cli_sources` and
    `test_dependencies` already derive the same population - a list maintained
    beside it is a second answer that goes stale on its own.
    """
    root = Path(P.__file__).resolve().parents[2]
    return {q.relative_to(root).as_posix():
            q.read_text(encoding="utf-8", errors="replace")
            for q in sorted((root / "tools").rglob("*.py"))
            if "tests" not in q.parts and "__pycache__" not in q.parts}


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

    **Item 117 replaced agreement with REFUSAL, which is the stronger form of
    the same property.** Item 116 asked every reader to give the SAME answer
    about an invisible-space profile, and one answer is worth having only if it
    is the right one — three of the seven were permissively wrong and the other
    four were guessing. There is one door now, so the question is no longer
    whether the readers agree about a file they cannot read: it is that none of
    them reads it at all, while every one of them still reads an ordinary
    profile.
    """
    try:
        plain = call(indented_profile(tmp_path, SPACE_INDENT, name + "-spaces"))
    except P.ProfileError as exc:
        return f"an ordinary space-indented profile was refused: {exc}"
    try:
        read = call(indented_profile(tmp_path, NBSP_INDENT, name + "-invisible"))
    except P.ProfileError:
        return ""                    # refused by line: the one right answer
    return (f"an invisible-space indent was READ, as {read!r}, rather than "
            f"refused (the plain file reads as {plain!r}). A reader that "
            f"interprets what it cannot read is what item 117 exists to end.")


def old_rule_reader(target: Path) -> dict:
    """A block reader written the way three of the seven were, reading the file
    for ITSELF.

    Kept as the positive control for `indent_disagreement`: this one MUST be
    caught, or the check that clears the real readers has cleared nothing. It
    deliberately does NOT go through `_profile_lines` — that is the door item
    117 put the refusal behind, so a control that used it would be refused for
    free and would prove only that the door exists.
    """
    out, inside = {}, False
    text = (Path(target) / ".sdlc" / "profile.yml").read_text(
        encoding="utf-8", errors="replace")
    for line in text.splitlines():
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

    Item 116 closed it by making every reader count the components the same way;
    item 117 closes it one level earlier, by refusing the file. **Both are worth
    holding, and the refusal is the one that generalises** — the reader agreeing
    with itself protects the checks somebody has already thought of, and the
    refusal protects the ones nobody has written yet.
    """
    root = indented_profile(tmp_path, NBSP_INDENT, "estate")
    with pytest.raises(P.ProfileError) as caught:
        P.tighten_only(P.DEFAULTS, P.profile_dials(root), P.component_count(root))
    assert "U+00A0" in str(caught.value) and ":" in str(caught.value), (
        f"the refusal must name the character and the line: {caught.value}")


def test_a_workspace_question_is_never_answered_with_a_components_value(tmp_path):
    """Agreement alone cannot see a change that moves BOTH answers, and a
    mutation proved it: `profile_find` reading the components block made the
    space and invisible cases agree on the WRONG value.

    Nothing anywhere else held this property, though `profile_find`'s own
    docstring records it as the defect the function was written to fix —
    `stage.resolve` builds P0's and P5's exact-value blocks out of it, so an
    estate's prompt stated one component's suite as the workspace's.

    The invisible-indent half of this became `indent_disagreement`'s job when
    item 117 made that file a refusal; what stays here is the property that a
    perfectly ORDINARY estate profile still does not leak a component's value
    upward, which no refusal can protect.
    """
    root = indented_profile(tmp_path, SPACE_INDENT, "estate-plain")
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
    a reader to be wrong in.

    Item 116 answered it by keeping the block OPEN when the indentation is
    strange, and the max-effort review of that commit measured the cost of that
    choice going the other way: an extra report under `gates:` is a
    `tighten_only` HALT, so `\\xa0signing: none` refused the run naming a
    loosening nobody wrote. Refusing the file answers both directions at once,
    which is why item 117 is a fix rather than a second guess.
    """
    root = indented_profile(tmp_path, NBSP_INDENT, "hardened")
    with pytest.raises(P.ProfileError):
        P.gate_overrides(root)
    assert P.gate_overrides(indented_profile(
        tmp_path, SPACE_INDENT, "hardened-plain")) == {"G4": "hard"}


def test_the_profile_is_read_in_exactly_one_place():
    """`component_count` and `gate_overrides` opened the file for themselves,
    and they are two of the three readers that disagreed with everyone else.

    **Swept across the module SET now, not one file.** `intake.profile_value`
    and `intake.tree_state` each opened it too — the eighth and ninth readers —
    and the module-scoped version of this guard could not have seen either.
    """
    sources = shipped_sources()
    assert len(sources) >= 25, f"only {len(sources)} shipped module(s) swept"
    offenders = {rel: sorted(found) for rel, source in sources.items()
                 if (found := own_profile_reads(source))}
    assert offenders == {}, (
        f"{sorted(offenders)} name the profile file inside a function; the name "
        f"has one home, `policy.PROFILE_NAME`, and a second spelling is where a "
        f"second answer to 'what is indentation' comes from")


def test_the_profile_filename_has_exactly_one_home():
    """**Item 94's rule for the governing document rather than an artifact.**
    `policy.yml` has had `POLICY_NAME` since item 35; the profile - the file an
    adopter actually fills in - was spelled inline in three modules, and the
    module that WRITES it was one of them."""
    assert P.PROFILE_NAME == "profile.yml"
    spelled = [rel for rel, source in shipped_sources().items()
               if f'"{P.PROFILE_NAME}"' in source]
    assert spelled == ["tools/sdlc/policy.py"], (
        f"the profile's name is written out in {spelled}; it belongs in "
        f"policy.PROFILE_NAME and nowhere else")


def test_the_sweep_would_catch_a_new_reader():
    """The positive control. A guard nobody can watch fail is a guard nobody
    has checked - and this one was demonstrably unwatchable, because the module
    it missed was destroying data while every roster entry passed."""
    planted = ('def brand_new_reader(target):\n'
               '    return (target / ".sdlc" / "profile.yml").read_text()\n')
    assert own_profile_reads(planted) == {"brand_new_reader"}


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


# ── item 117: the profile refuses what it cannot read ────────────────────────
#
# Item 116 gave the seven hand readers ONE rule and stopped there, and a
# max-effort review of that commit measured the cost: the rule REGRESSED four
# of them, because a rule that keeps a block open reports LESS where a reader
# skips a block and MORE where one collects. Both directions lose something,
# which is the argument for not choosing between them — `policy.yml` has been
# REFUSED by line since item 35 and the one document an adopter actually fills
# in was the one still being silently reinterpreted.
#
# Two channels, and their independence is the property worth proving: the
# character rule is pure stdlib and always runs, the PyYAML crosscheck is a
# second opinion where PyYAML is installed. Either alone catches the invisible
# indent, and two positive controls below say so.

TAB = chr(9)


def profile_at(tmp_path: Path, name: str, text: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")
    return root


def test_a_tab_indented_profile_is_refused_naming_the_line(tmp_path):
    """`parse` has refused a tab for `policy.yml` since item 35 and PyYAML
    raises on one, so agreeing here costs nothing anybody writes on purpose —
    measured: the shipped template and every rendered profile carry zero."""
    root = profile_at(tmp_path, "tabbed",
                      "scope: repo\ncomponents:\n" + TAB + "- name: a\n")
    with pytest.raises(P.ProfileError) as caught:
        P.component_count(root)
    assert "profile.yml:3:" in str(caught.value), (
        f"a refusal that cannot name a line is one nobody can act on: "
        f"{caught.value}")
    # **The SENTENCE, not just the refusal**, and a mutation is why: turning the
    # tab branch off left the suite green, because the per-character rule below
    # catches a tab too and says `U+0009 (CHARACTER TABULATION), which looks
    # like a space and is not` — true, and the wrong thing to tell a person
    # about the most ordinary way to get this wrong. The branch earns its place
    # by what it says, so what it says is asserted.
    assert str(caught.value) == P.tab_refusal("profile.yml", 3)


def test_both_governing_documents_refuse_a_tab_in_the_same_words():
    """One rule, one spelling — item 112's subject, and item 115's: two copies
    of a sentence drift, and the drift is silent because both still read like
    refusals. `parse` has refused a tab for `policy.yml` since item 35 and item
    117 gave the profile the same refusal, which is exactly when a restatement
    starts."""
    source = READER_SOURCE
    assert source.count('"{name}:{line_no}: tabs are not indentation in YAML"') \
        + source.count("tabs are not indentation in YAML") == 2, (
        "the tab sentence is written more than once; one rule, one spelling")
    with pytest.raises(P.PolicyError) as caught:
        P.parse("gates:\n" + chr(9) + "G1: hard\n", "policy.yml")
    assert str(caught.value) == P.tab_refusal("policy.yml", 2)


def test_an_invisible_indent_is_refused_naming_its_codepoint(tmp_path):
    """'Your indentation is wrong' about a character nobody can SEE is a
    refusal nobody can act on, which is why the codepoint is in the sentence."""
    root = profile_at(tmp_path, "invisible",
                      "gates:\n" + chr(0xA0) + "  G4: hard\n")
    with pytest.raises(P.ProfileError) as caught:
        P.gate_overrides(root)
    said = str(caught.value)
    assert "profile.yml:2:" in said and "U+00A0" in said and "NO-BREAK" in said, said


def test_an_ordinary_comment_is_indented_however_it_likes(tmp_path):
    """A `#` note indented with SPACES is never a finding, at any depth: a
    comment's indentation means nothing to any reader, and refusing one would
    make the check useless in exactly the file people annotate."""
    root = profile_at(tmp_path, "annotated",
                      "scope: repo\n      # a deeply indented note\n"
                      "gates:\n  # about the gates\n  G4: hard\n")
    assert P.gate_overrides(root) == {"G4": "hard"}


def test_an_invisible_space_before_a_comment_is_still_refused(tmp_path):
    """**Written the other way round first, and MEASUREMENT overruled it.** The
    obvious rule is that a comment's indentation cannot be wrong, so an invisible
    space before a `#` should pass. PyYAML disagrees: it reads the line as a
    scalar beginning with a non-breaking space and raises `ScannerError`, because
    `\\xa0#` is not a comment to YAML at all.

    So refusing is AGREEING with the reference reader, and passing it would have
    been this reader inventing a third opinion about the one character the whole
    item is about. `_strip_comment` carries the same rule from the other side —
    it only strips a `#` preceded by a space or a tab — which is why the line
    survives to be judged.
    """
    root = profile_at(tmp_path, "pasted",
                      "scope: repo\n" + chr(0xA0) + "# pasted from the wiki\n")
    with pytest.raises(P.ProfileError) as caught:
        P.gate_overrides(root)
    assert "U+00A0" in str(caught.value)
    assert yaml_refuses("scope: repo\n" + chr(0xA0) + "# pasted from the wiki\n"), (
        "the refusal is only justified while PyYAML refuses it too")


def yaml_refuses(text: str) -> bool:
    """Whether PyYAML rejects this document — the reference the rule above
    defers to, asked rather than remembered."""
    try:
        yaml.safe_load(text)
    except Exception:
        return True
    return False


def test_an_absent_profile_is_not_an_unreadable_one(tmp_path):
    """The owner's ruling, and the distinction this harness keeps needing: a
    repository nobody has installed into is not one with a broken profile."""
    bare = tmp_path / "bare"
    (bare / ".sdlc").mkdir(parents=True)
    assert P._profile_lines(bare) == []
    assert P.component_count(bare) == 0
    assert P.profile_limits(bare) == []


def test_a_top_level_line_with_no_colon_names_no_block(tmp_path):
    """The colon requirement all seven copies enforced and item 116 dropped.
    Without it a typo silently becomes a section."""
    root = profile_at(tmp_path, "typo", "gates\n  G4: hard\n")
    assert P.gate_overrides(root) == {}


def test_a_document_marker_closes_the_block_above(tmp_path):
    """`---` is legal YAML and named a block that then owned every indented
    line beneath it."""
    root = profile_at(tmp_path, "marker",
                      "gates:\n---\n  G4: hard\nvcs:\n  push_remote: origin\n")
    assert P.gate_overrides(root) == {}
    assert P.top_vcs(root) == {"push_remote": "origin"}


def test_a_top_level_sequence_entry_names_no_block(tmp_path):
    """`parse` refuses one outright — *a list at the top level has no key* — and
    this reader agrees with it rather than inventing a block called `- name`.

    Asserted on `profile_blocks` because that is where it is OBSERVABLE: the
    downstream readers answer identically either way, so a test that only
    checked `gate_overrides` would have passed with the clause deleted. What
    would be wrong is the claim — a document PyYAML reads as a LIST does not
    have a top-level key called `- name`, and the crosscheck's key set is
    exactly that claim.
    """
    lines = P._profile_lines(profile_at(
        tmp_path, "seq", "- name: a\n  G4: hard\ngates:\n  G1: hard\n"))
    assert P.profile_blocks(lines) == ["gates"]
    assert P.gate_overrides(profile_at(
        tmp_path, "seq2", "- name: a\n  G4: hard\ngates:\n  G1: hard\n")
    ) == {"G1": "hard"}


def test_a_space_before_the_colon_still_names_the_key(tmp_path):
    """PyYAML reads `components :` as the key `components`, and a reader that
    disagreed with PyYAML about a space would be inventing a second grammar —
    which is what the ninth reader did: no workspace, while the count said two."""
    root = profile_at(tmp_path, "spaced",
                      "components :\n  - name: a\n  - name: b\n")
    assert P.component_count(root) == 2
    assert I.tree_state(root)["workspace"] is True


def test_a_component_key_after_a_vcs_block_is_not_dropped(tmp_path):
    """Item 116's fourth regression, on an ORDINARY space-indented file: a
    component key that follows a `vcs:` sub-block, on DEDENT back to the
    component's own indent, was matched against VCS_KEYS, missed, and
    DISAPPEARED. Read-correctness, not refusal — one well-formed `components:`
    block is genuinely un-refusable.

    **Item 224 rewrote this fixture from two top-level `components:` blocks to
    one.** The old spelling relied on the reader MERGING the two blocks, which is
    itself the hand-vs-PyYAML disagreement item 224 refuses (PyYAML keeps the
    LAST block and drops the first), so the duplicate spelling is now a refusal;
    nobody hand-writes two `components:` and the installer writes one. The single
    block tests the SAME property — the key after the sub-block, on dedent."""
    root = profile_at(tmp_path, "vcsreset",
                      "components:\n  - name: a\n    vcs:\n"
                      "      deny_raw_git: true\n"
                      "    test_command: KEPT\n")
    found = P.components(root)
    assert found[0]["test_command"] == "KEPT", (
        f"a component key after a vcs: sub-block was silently dropped: {found}")
    assert found[0]["vcs"] == {"deny_raw_git": True}


def test_the_block_set_is_every_top_level_key_not_only_those_with_children(
        tmp_path):
    """The landmine inside the crosscheck, measured before it was built: 26 of
    the shipped template's top-level keys and only THREE carry children, so a
    'blocks with content' reading would report a disagreement about 23 keys for
    every ordinary profile — a gate that fires on everything is a gate somebody
    turns off in a week."""
    root = profile_at(tmp_path, "flat",
                      "scope: repo\ngate_profile: solo\nvcs:\n  push_remote: origin\n")
    assert P.profile_blocks(P._profile_lines(root)) == [
        "scope", "gate_profile", "vcs"]


def test_the_reader_and_pyyaml_agree_about_a_real_rendered_profile(tmp_path):
    """The installer is SPAWNED (item 96's rule) rather than imported, because
    what has to agree is the file an adopter actually receives."""
    repo = tmp_path / "rendered"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(repo), "--no-graph"],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    text = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    lines = [P._strip_comment(line).rstrip() for line in text.splitlines()]
    assert P.profile_crosscheck(text, lines, "profile.yml") == ("", ""), (
        "the profile the installer writes must cross-check clean, or every "
        "install refuses itself")
    assert P.profile_limits(repo) == []


def test_a_key_pyyaml_sees_and_the_reader_does_not_is_refused():
    """The one FATAL branch. PyYAML PARSES an invisible-space profile — it reads
    the prefixed key as a top-level key of its own where the reader thinks the
    line sits inside `components` — so the crosscheck catches the whole class
    even where the character rule has been removed."""
    text = "components:\n  - name: a\n" + chr(0xA0) + "test_command: X\n"
    lines = [P._strip_comment(line).rstrip() for line in text.splitlines()]
    refusal, limit = P.profile_crosscheck(text, lines, "profile.yml")
    assert not limit
    assert "PyYAML sees" in refusal and "disagree about the top-level keys" in refusal


def test_the_crosscheck_can_fail():
    """A guard nobody can watch fail is a guard nobody has checked — item 115's
    lesson, applied when the guard is written rather than after a mutation pass
    has to find it."""
    agreeing = "scope: repo\nvcs:\n  push_remote: origin\n"
    lines = [P._strip_comment(line).rstrip() for line in agreeing.splitlines()]
    assert P.profile_crosscheck(agreeing, lines, "profile.yml") == ("", "")
    assert P.profile_crosscheck(agreeing, lines[:1], "profile.yml")[0], (
        "hand the crosscheck a reader that sees fewer keys and it must complain")


def test_the_two_channels_are_independent(tmp_path, monkeypatch):
    """**The proof that matters more than the ratio.** Remove the character rule
    and the crosscheck must still refuse the invisible indent on its own — two
    statements of one property, not one written twice. Where PyYAML is absent
    the character rule is the whole of it, which the other control covers.
    """
    monkeypatch.setattr(P, "_indent_problem", lambda lines, name: "")
    root = profile_at(tmp_path, "onechannel",
                      "components:\n  - name: a\n" + chr(0xA0) + "test_command: X\n")
    with pytest.raises(P.ProfileError) as caught:
        P.component_count(root)
    assert "PyYAML" in str(caught.value), (
        f"with the character rule gone the crosscheck must catch it alone: "
        f"{caught.value}")


def test_a_profile_pyyaml_cannot_read_is_a_limit_not_a_refusal(tmp_path):
    """**The owner's ruling of 2026-09-01, and the measurement behind it.**
    `policy.crosscheck` refuses a file PyYAML cannot read and that precedent does
    NOT transfer: `sdlc_templates/policy.yml` carries no placeholders and parses,
    while the profile template carries eighteen placeholders and PyYAML raises on
    it. Refusing here would refuse the harness's own shipped template, and would
    make the hard stop environment-dependent — firing only where PyYAML is
    installed, which is the inverse of what the stdlib reader exists for.
    """
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    root = profile_at(tmp_path, "unrendered", template)
    assert P.component_count(root) == 0          # read, not refused
    said = P.profile_limits(root)
    assert len(said) == 1 and "PyYAML cannot read this file" in said[0], said
    assert "\n" not in said[0], "a stated limit is one line, not a YAML traceback"


def test_the_limit_is_silent_when_pyyaml_parses(tmp_path):
    """Silent when the crosscheck really ran, because then a disagreement is a
    REFUSAL and a note beside it would say the opposite of what happened."""
    root = profile_at(tmp_path, "clean", "scope: repo\ngate_profile: solo\n")
    assert P.profile_limits(root) == []


def test_the_limit_speaks_when_pyyaml_is_absent(tmp_path, monkeypatch):
    """The zero-dependency configuration this harness promises to work in.
    Saying nothing would let a reader that read the file alone report the same
    confidence as one that had a second opinion."""
    real_import = builtins.__import__

    def refuse_yaml(name, *rest, **kw):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real_import(name, *rest, **kw)

    monkeypatch.setattr(builtins, "__import__", refuse_yaml)
    root = profile_at(tmp_path, "nodeps", "scope: repo\n")
    said = P.profile_limits(root)
    assert len(said) == 1 and "PyYAML is not installed" in said[0], said
    assert P.component_count(root) == 0, "and the reader still reads"


def test_intake_and_policy_answer_a_top_level_key_identically(tmp_path):
    """The EIGHTH reader, folded in. It had its own rule and measurement caught
    it disagreeing: an invisible-space-prefixed `scope:` read as None in intake
    and as its value in policy — and item 116's guard was scoped to `policy.py`,
    so it could not have seen it."""
    root = profile_at(tmp_path, "eighth",
                      'scope: regulated\ntest_command: "pytest # not a comment"\n'
                      "components:\n  - name: a\n    scope: nested\n")
    assert I.profile_value(root, "scope") == P.profile_top_value(root, "scope")
    assert I.profile_value(root, "scope") == "regulated"
    assert I.profile_value(root, "test_command") == "pytest # not a comment", (
        "the quote walk moved with the body: a quoted scalar ends at its "
        "CLOSING quote, or P5 is handed a command no shell can run")
    assert P.profile_top_value(root, "path") is None, "a nested key is not top-level"


def test_policy_no_longer_imports_intake():
    """The bundle half. Deleting the import relaxes an ORDERING constraint in the
    emitted `.sdlc/ledger_check.py` — `intake` stays in the closure regardless,
    imported at column 0 by both `approve.py` and `stage.py`, which is measured
    here rather than assumed."""
    tree = ast.parse(READER_SOURCE)
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.col_offset == 0
                for alias in node.names}
    assert "intake" not in imported, (
        "policy imported intake for ONE call to the eighth profile reader; the "
        "reader moved, so the claim the import makes is no longer true")
    for sibling in ("approve", "stage"):
        source = Path(
            (ROOT / "tools" / "sdlc" / f"{sibling}.py")).read_text(encoding="utf-8")
        assert "\nfrom tools.sdlc import intake" in source, (
            f"{sibling}.py is what keeps intake in the emitted bundle; if this "
            f"moved, the ordering claim above needs re-measuring")


def test_tree_state_and_component_count_agree_about_a_workspace(tmp_path):
    """The NINTH reader. `startswith('components:')` over the raw file is a
    different question from *is any component declared*, and only the second is
    the one `tree_state` has."""
    cases = (("components:\n  - name: a\n", True),
             ("components :\n  - name: a\n", True),
             ("scope: repo\n", False),
             ("components:\n", False))
    for n, (text, expected) in enumerate(cases):
        root = profile_at(tmp_path, f"ninth{n}", text)
        assert I.tree_state(root)["workspace"] is expected, text
        assert (P.component_count(root) > 0) is expected, text


def test_the_crosscheck_memo_cannot_make_two_profiles_share_an_answer(tmp_path):
    """The memo exists for a measured reason — a PyYAML parse costs 6.7 ms
    against the reader's 0.28 ms and `suite.declared` reads the profile seven
    times per component — and a memo that answered for the wrong file would be a
    far worse defect than the cost it removes."""
    good = profile_at(tmp_path, "memo-good", "scope: repo\n")
    bad = profile_at(tmp_path, "memo-bad",
                     "components:\n  - name: a\n" + chr(0xA0) + "test_command: X\n")
    for _ in range(3):
        assert P.component_count(good) == 0
        with pytest.raises(P.ProfileError):
            P.component_count(bad)


def test_the_memo_is_keyed_on_content_so_an_edit_is_seen(tmp_path):
    """**The property a path-keyed memo would fail**, and the reason this one is
    keyed on the text: a profile edited in place between two reads — which is
    what a person fixing the refusal actually does — must be judged again."""
    root = profile_at(tmp_path, "memo-edit",
                      "components:\n  - name: a\n" + chr(0xA0) + "test_command: X\n")
    with pytest.raises(P.ProfileError):
        P.component_count(root)
    (root / ".sdlc" / "profile.yml").write_text(
        "components:\n  - name: a\n    test_command: X\n", encoding="utf-8")
    assert P.component_count(root) == 1, (
        "the fix was ignored because the memo answered about the old bytes")


#: Every shipped CLI that reads the profile and takes a `--target`, with the
#: argv that makes it read one. **The refusal reaches all sixty-seven call sites
#: for free — what these check is that it ARRIVES AS A SENTENCE**, because
#: `ProfileError` subclasses `PolicyError` (a `RuntimeError`) and every one of
#: these already had a door for that. A fresh exception type would have walked
#: past every handler and landed as a traceback in ten CLIs at once, which is a
#: worse answer than the silence item 117 replaces.
PROFILE_CLIS = (
    ("tools/sdlc/policy.py", ["--validate"]),
    ("tools/sdlc/stage.py", ["--list"]),
    ("tools/sdlc/suite.py", ["--plan", "--run", "no-such-run"]),
)


@pytest.mark.parametrize("script,extra", PROFILE_CLIS,
                         ids=[Path(s).stem for s, _ in PROFILE_CLIS])
def test_an_unreadable_profile_reaches_a_cli_as_a_sentence(tmp_path, script, extra):
    """SPAWNED, not called — item 96's rule, and it earned itself here: calling
    `main` in-process found nothing, and spawning found `policy.py --validate`
    reading the profile OUTSIDE its own error door, so the module whose job is
    refusing an unreadable governing document left as a traceback.
    """
    repo = profile_at(tmp_path, "cli" + Path(script).stem,
                      "scope: repo\ngates:\n" + chr(0xA0) + " G4: hard\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / script), "--target", str(repo), *extra],
        capture_output=True, text=True, cwd=str(ROOT))
    said = done.stdout + done.stderr
    assert "Traceback" not in said, (
        f"{script} handed a person a traceback instead of the reason:\n{said}")
    assert done.returncode != 0, f"{script} carried on as if it had read it"
    assert "U+00A0" in said and "profile.yml:3" in said, (
        f"{script} refused without naming the line or the character:\n{said}")


def test_every_module_that_reads_the_profile_has_a_door():
    """The DERIVED half: a module reading the profile without a handler for the
    refusal is the next traceback, and a hand-written CLI list above cannot see
    one that is added later. `PolicyError` is a `RuntimeError`, so a door that
    catches either counts — what does not count is no door at all.
    """
    readers, doorless = [], []
    for path in sorted((ROOT / "tools").rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("_"):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if "def main(" not in source:
            continue
        if not any(call in source for call in
                   ("profile_scalar", "profile_list", "profile_find",
                    "component_count", "gate_overrides", "profile_dials",
                    "top_vcs", "profile_value", "profile_top_value")):
            continue
        readers.append(path.name)
        if not any(door in source for door in
                   ("cli.door", "PolicyError", "RuntimeError", "Exception")):
            doorless.append(path.name)
    assert readers, "the sweep found no profile-reading CLI at all"
    assert not doorless, (
        f"these read the profile and would hand a person a traceback: {doorless}")


def test_an_advisory_says_could_not_look_rather_than_all_clear(tmp_path):
    """**The one consumer that must NOT refuse, and must not reassure either.**

    `worktree.overlap` is advisory by design — there is no lock, and the ruling
    on file is optimistic concurrency with rebase-before-merge as the
    enforcement — so it swallows the refusal rather than halting a run over a
    question it was only asked for convenience. What it may not do is answer
    *no other live run shares a component*, which is what it did: until item 117
    `policy.components` synthesised a component for an unreadable profile rather
    than raising, so the `unknown` basis had no live route to it and the branch
    that handles it was, in its own comment's words, a guarded path.

    Item 100 found this same function answering *no overlap* through the whole
    window it exists for, because it read an absent record as an empty set. This
    is that finding from the other side, and one word of the summary line with
    it: a could-not-look row is a FINDING, never an overlap.
    """
    repo = profile_at(tmp_path, "advisory",
                      "scope: repo\ngates:\n" + chr(0xA0) + " G4: hard\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/sdlc/worktree.py"), "--target",
         str(repo), "--overlap", "--run", "no-such-run"],
        capture_output=True, text=True, cwd=str(ROOT))
    said = done.stdout + done.stderr
    assert "Traceback" not in said, said
    assert "could not be determined" in said and "could-not-look" in said, said
    assert "no other live run shares a component" not in said, (
        f"the advisory reported a clear run for a profile it could not read:\n"
        f"{said}")
    assert "overlap(s)" not in said, (
        f"a could-not-look row was counted as an overlap:\n{said}")
    assert done.returncode == 1, "advisory findings are exit 1, not a refusal"


def test_the_advisory_is_silent_when_the_profile_reads(tmp_path):
    """The control: this must not turn every clean repository into a finding."""
    repo = profile_at(tmp_path, "advisory-clean", "scope: repo\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/sdlc/worktree.py"), "--target",
         str(repo), "--overlap", "--run", "no-such-run"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert "no other live run shares a component" in done.stdout, done.stdout
    assert done.returncode == 0


# ── item 118: a list is a list in either YAML spelling ──────────────────────
#
# `profile_list` read INLINE only, and three of the four documents that talk
# about a quarantine spell it as a block. So an adopter who followed the
# template's own instructions declared NOTHING and the gate kept blocking with
# no explanation — a reader answering None is indistinguishable from a profile
# that named nothing.
#
# **PyYAML decides the grammar here, not this reader.** Item 117 settled that
# argument for `components :`; the same argument about `- ` gets the same
# answer, which is why every case below is asserted against the reference
# reader rather than against a number somebody typed.

#: Every block spelling that is legal YAML, measured rather than assumed — a
#: sequence may sit at OR below its key's indentation, so the zero-indent form
#: is legal and was the case a `>` test would have silently dropped.
BLOCK_SPELLINGS = {
    "two-space": 'known_failures:\n  - "a"\n  - "b"\n',
    "four-space": 'known_failures:\n    - "a"\n    - "b"\n',
    "zero-indent": 'known_failures:\n- "a"\n- "b"\n',
    "trailing-key": 'known_failures:\n  - "a"\n  - "b"\nscope: repo\n',
    "unquoted": 'known_failures:\n  - a\n  - b\n',
    "commented": 'known_failures:\n  - "a"    # why this one\n  - "b"\n',
    "blank-line-inside": 'known_failures:\n  - "a"\n\n  - "b"\n',
}


@pytest.mark.parametrize("spelling", sorted(BLOCK_SPELLINGS))
def test_a_block_list_reads_as_the_list_pyyaml_says_it_is(tmp_path, spelling):
    """The item, one spelling at a time, judged by the reference reader."""
    text = BLOCK_SPELLINGS[spelling]
    root = profile_at(tmp_path, f"block-{spelling}", text)
    got = P.profile_list(root, "known_failures")
    assert got == ["a", "b"], f"{spelling}: {got!r}"

    yaml = pytest.importorskip("yaml")
    assert got == yaml.safe_load(text)["known_failures"], (
        f"{spelling}: the reader and PyYAML disagree about one document, which "
        f"is the whole failure class items 116, 117 and 118 exist to end")


def test_the_inline_spelling_is_unchanged(tmp_path):
    """The control. Widening a reader must not move the case that worked."""
    root = profile_at(tmp_path, "inline", 'known_failures: ["a", "b"]\n')
    assert P.profile_list(root, "known_failures") == ["a", "b"]


def test_an_empty_block_is_none_and_not_an_empty_list(tmp_path):
    """`key:` with nothing under it is `None` in YAML, and the difference is
    load-bearing: for `phases`, `[]` means *run no phases* and None means
    *nobody said*. Agreeing with the reference reader is why this case is
    called out rather than left to fall out of the loop."""
    text = "known_failures:\nscope: repo\n"
    root = profile_at(tmp_path, "empty-block", text)
    assert P.profile_list(root, "known_failures") is None
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(text)["known_failures"] is None


def test_an_explicit_empty_inline_list_stays_an_empty_list(tmp_path):
    """The other half of the distinction above, and the one the template ships."""
    root = profile_at(tmp_path, "empty-inline", "known_failures: []\n")
    assert P.profile_list(root, "known_failures") == []


def test_a_scalar_is_still_not_a_list(tmp_path):
    """A widened reader must not start reading `key: value` as a one-item list."""
    root = profile_at(tmp_path, "scalar", "known_failures: nope\n")
    assert P.profile_list(root, "known_failures") is None


def test_an_unclosed_inline_list_is_refused_not_read_as_absent(tmp_path):
    """**Item 130 (W30 A4-5), the reader half.** An inline list opened with `[`
    and never closed — `phases: [intake, implementation, release` — was read as
    None (ABSENT) by `profile_list`, so `stage.py --list` took the solo default
    and reported `release` as *not listed* though the file lists it. INVALID is
    not ABSENT (item 117's altitude): this is the harness's OWN reader, so it
    refuses a list it cannot close rather than reading the key as unset. PyYAML
    raises on the same bytes, so refusing AGREES with it."""
    text = "scope: repo\nphases: [intake, implementation, release\n"
    root = profile_at(tmp_path, "unclosed", text)
    with pytest.raises(P.ProfileError) as caught:
        P.profile_list(root, "phases")
    msg = str(caught.value)
    assert "profile.yml:2" in msg and "]" in msg, msg
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)


def test_valid_inline_and_block_lists_are_not_newly_refused(tmp_path):
    """**The false-positive control for item 130 (over-refusal, item 117/128).**
    The reader's own notion of a closed inline list is `startswith('[')` AND
    `endswith(']')`, so the refusal is its exact complement: a closed inline
    list, an empty inline list, a block list, and a quoted scalar that merely
    CONTAINS a bracket must all still read."""
    closed = profile_at(tmp_path, "closed", "phases: [intake, test]\n")
    assert P.profile_list(closed, "phases") == ["intake", "test"]
    empty = profile_at(tmp_path, "emptylist", "known_failures: []\n")
    assert P.profile_list(empty, "known_failures") == []
    block = profile_at(tmp_path, "blocklist", "phases:\n  - intake\n  - test\n")
    assert P.profile_list(block, "phases") == ["intake", "test"]
    scalar = profile_at(tmp_path, "brackety", 'test_command: "pytest [x]"\n')
    assert P.profile_scalar(scalar, "test_command") == "pytest [x]"


def test_a_nested_block_list_is_read_too(tmp_path):
    """`review.block_on` is the nested case, and it was unreadable in BOTH
    block spellings — `profile_find` has always found nested keys, so a list
    reader that could not was the odd one out."""
    text = "review:\n  block_on:\n    - critical\n    - high\n"
    root = profile_at(tmp_path, "nested", text)
    assert P.profile_list(root, "block_on") == ["critical", "high"]
    yaml = pytest.importorskip("yaml")
    assert (P.profile_list(root, "block_on")
            == yaml.safe_load(text)["review"]["block_on"])


def test_a_block_list_does_not_swallow_the_key_after_it(tmp_path):
    """The sequence ends at the first line that is not one of its items. Without
    that, one block list would eat the rest of the profile."""
    root = profile_at(tmp_path, "bounded",
                      'known_failures:\n  - "a"\nscope: repo\nphases: [test]\n')
    assert P.profile_list(root, "known_failures") == ["a"]
    assert P.profile_scalar(root, "scope") == "repo"
    assert P.profile_list(root, "phases") == ["test"]


def test_the_components_block_is_still_skipped(tmp_path):
    """`profile_find` skips `components:` so a workspace question is never
    answered with the first component's answer. The list reader shares that
    walk, so it inherits the skip rather than re-deciding it."""
    root = profile_at(
        tmp_path, "comp-skip",
        'components:\n  - name: a\n    known_failures:\n      - "theirs"\n'
        'known_failures:\n  - "ours"\n')
    assert P.profile_list(root, "known_failures") == ["ours"]


def test_a_block_list_block_on_is_now_judged_by_tighten_only(tmp_path):
    """**The blast radius, stated as a test rather than as a paragraph.**

    `profile_list` serves seven call sites, and `review.block_on` is one of
    them — a dial `tighten_only` polices. A block-list `block_on` was invisible
    before item 118, so a profile that dropped a severity ran happily with the
    constitution's floor quietly applied instead. It is READ now, which means a
    loosening written in valid YAML HALTS the run naming the file to change.

    That is the fix working rather than a new refusal: the adopter did write
    the loosening, and a control that cannot be evaluated has not been
    satisfied. It is called out because it turns a silent run into a refusal.
    """
    base = "scope: repo\ngate_profile: team\nsigning: none\n"
    loosened = profile_at(tmp_path, "loose-block",
                          base + "review:\n  block_on:\n    - critical\n")
    problems = P.tighten_only(P.load(loosened), P.profile_dials(loosened), 1,
                              P.gate_overrides(loosened))
    assert any("block_on" in p for p in problems), problems

    inline = profile_at(tmp_path, "loose-inline",
                        base + "review:\n  block_on: [critical]\n")
    assert P.tighten_only(P.load(inline), P.profile_dials(inline), 1,
                          P.gate_overrides(inline)) == problems, (
        "the two spellings of one loosening must produce one refusal")


def test_a_block_list_that_loosens_nothing_still_runs(tmp_path):
    """The control. Reading the dial must not turn every block-list profile
    into a refusal — only the ones that actually loosen something."""
    root = profile_at(
        tmp_path, "tight-block",
        "scope: repo\ngate_profile: team\nsigning: none\n"
        "review:\n  block_on:\n    - critical\n    - high\n")
    assert P.tighten_only(P.load(root), P.profile_dials(root), 1,
                          P.gate_overrides(root)) == []


def test_an_empty_sequence_item_is_none_in_both_spellings(tmp_path):
    """A bare `-` is a sequence item whose value is null, and PyYAML reads it
    that way. The inline spelling already agreed — `[null, "a"]` goes through
    `_scalar` and answers `[None, "a"]` — so the block reader agreeing is not a
    new opinion, it is the two spellings staying one list.

    Found by the mutation pass: dropping the bare-`-` branch killed nothing,
    because no test named the case at all.
    """
    yaml = pytest.importorskip("yaml")
    block = 'known_failures:\n  -\n  - "a"\n'
    inline = 'known_failures: [null, "a"]\n'
    got_block = P.profile_list(profile_at(tmp_path, "bare-dash", block),
                               "known_failures")
    got_inline = P.profile_list(profile_at(tmp_path, "explicit-null", inline),
                                "known_failures")
    assert got_block == [None, "a"], got_block
    assert got_block == yaml.safe_load(block)["known_failures"]
    assert got_block == got_inline, (
        f"one list, two spellings, two answers: {got_block!r} vs {got_inline!r}")


# ── item 130: four more things the reader and PyYAML disagreed about ────────
#
# All four came out of the max-effort review of items 117-128, and all four are
# the same shape: item 117 settled that this reader must agree with PyYAML about
# a document's SHAPE, and four places had not been brought along.

NL130 = chr(10)


def _profile_at(tmp_path: Path, text: str, name: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")
    return root


def test_a_space_before_the_colon_does_not_hide_a_dial(tmp_path):
    """**Part 1.** `_profile_sections` was changed to `partition(":")` in item
    117 with the reason recorded - *"`components :` is still the key
    `components`, because PyYAML says it is"* - and the two key-LOCATING
    functions kept `startswith(key + ":")`. So both readers saw the key and only
    one could read its value, which `tighten_only` treats as an absent dial."""
    yaml = pytest.importorskip("yaml")
    text = "scope: repo" + NL130 + "gate_profile : solo" + NL130
    root = _profile_at(tmp_path, text, "spaced")
    assert P.profile_top_value(root, "gate_profile") == "solo"
    assert yaml.safe_load(text)["gate_profile"] == "solo"


def test_the_value_is_sliced_by_the_same_partition_that_matched_the_key(tmp_path):
    """The other half, and it would have been a fresh defect: the old body
    sliced by `len(key) + 1`, which is right only when the colon sits flush
    against the name. Widening the MATCH without widening the SLICE would have
    handed back `": solo"`."""
    root = _profile_at(tmp_path, "gate_profile  :  solo" + NL130, "sliced")
    assert P.profile_top_value(root, "gate_profile") == "solo"


def test_the_floor_refuses_a_spaced_dial_the_way_it_refuses_a_plain_one(tmp_path):
    """**A CLI's contract is a process** (CLAUDE.md), and this is the refusal a
    person sees. Before item 130 the spaced spelling exited 0 - `OK - within
    policy` - for a profile the plain spelling refuses by name."""
    root = _profile_at(tmp_path, "scope: repo" + NL130 + "gate_profile : solo"
                       + NL130, "floor")
    (root / ".sdlc" / "policy.yml").write_text(
        "schema_version: 1" + NL130 + "gate_profile_floor: regulated" + NL130,
        encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "policy.py"), "--validate",
         "--target", str(root)], capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 1, done.stdout
    assert "looser than the floor" in done.stdout


def test_a_component_key_after_its_vcs_block_is_not_dropped(tmp_path):
    """**Part 2, and the silent-drop direction this project calls its most
    expensive.** `in_vcs` was cleared by a top-level line or the next `- name:`,
    and a component key written after that component's own `vcs:` block is still
    indented - so it was matched against VCS_KEYS, missed, and vanished.
    `suite.declared` then reported no test command and path `.`, so P5 ran the
    suite at the WORKSPACE ROOT."""
    yaml = pytest.importorskip("yaml")
    text = ("scope: workspace" + NL130 + "components:" + NL130
            + "  - name: orders-api" + NL130 + "    path: orders-api" + NL130
            + "    vcs:" + NL130 + "      push_remote: upstream" + NL130
            + '    test_command: "mvn -q test"' + NL130
            + "    deploy_command: \"helm upgrade\"" + NL130)
    got = P.components(_profile_at(tmp_path, text, "aftervcs"))[0]
    ref = yaml.safe_load(text)["components"][0]
    assert got["test_command"] == ref["test_command"]
    assert got["deploy_command"] == ref["deploy_command"]
    assert got["vcs"] == ref["vcs"], "the vcs block itself must still be read"


def test_a_components_block_form_list_is_a_list(tmp_path):
    """**Part 3.** These rows carry no colon, so they fell through the `if not
    sep` guard and were dropped while the key stored `""` - which
    `suite.quarantined` reads as *nobody declared one*, substituting the
    WORKSPACE list. The component lost its own declaration and inherited one
    nobody accepted for it, in one step. This is the spelling
    `sca.py --propose-quarantine` drafts and the template teaches."""
    yaml = pytest.importorskip("yaml")
    text = ("scope: workspace" + NL130 + "components:" + NL130
            + "  - name: web" + NL130 + "    path: web" + NL130
            + "    known_failures:" + NL130 + '      - "web.Already#red"' + NL130
            + '      - "web.Also#red"' + NL130)
    root = _profile_at(tmp_path, text, "blocklist")
    got = P.components(root)[0]
    assert got["known_failures"] == yaml.safe_load(text)["components"][0][
        "known_failures"]
    assert SUITE.quarantined(root, got, "known_failures") == [
        "web.Already#red", "web.Also#red"]


def test_a_bare_component_list_key_is_still_nobody_declared_one(tmp_path):
    """**The control, and an existing test caught me getting it wrong.** A
    `key:` with nothing under it is `None` in YAML, not `[]` - `profile_list`
    settled that one level up - so pre-creating the empty list turned *nobody
    declared one* into *declared and empty* and killed the fall-back to the
    workspace list."""
    text = ("scope: workspace" + NL130 + 'known_failures: ["ws.Red#one"]' + NL130
            + "components:" + NL130 + "  - name: web" + NL130
            + "    path: web" + NL130 + "    known_failures:" + NL130)
    root = _profile_at(tmp_path, text, "bare")
    got = P.components(root)[0]
    assert "known_failures" not in got or got["known_failures"] in ("", None)
    assert SUITE.quarantined(root, got, "known_failures") == ["ws.Red#one"]


def test_a_runners_self_scope_is_not_the_workspaces_phase_list(tmp_path):
    """**Part 4.** `_profile_entry` skipped `components:` alone, and `runners:`
    is the profile's other sequence-of-maps block - so a runner's `phases:`
    self-scope was returned as the workspace's `phases[]` and `stage.active()`
    answered `['P4']`, dropping every gate, the review loop and closeout while
    reporting it had read the profile's own list."""
    yaml = pytest.importorskip("yaml")
    text = ("scope: repo" + NL130 + "runners:" + NL130 + "  - name: hermes" + NL130
            + "    capabilities: tools" + NL130 + "    phases: [implementation]"
            + NL130 + "phases: [intake, requirements, test]" + NL130)
    root = _profile_at(tmp_path, text, "runnerscope")
    assert P.profile_list(root, "phases") == yaml.safe_load(text)["phases"]


def test_every_sequence_of_maps_block_is_named(tmp_path):
    """Derived rather than remembered: the skip list must cover every top-level
    block whose entries are maps, or the next one repeats part 4."""
    assert set(P.SEQUENCE_BLOCKS) == {"components", "runners"}
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(
        encoding="utf-8")
    for block in P.SEQUENCE_BLOCKS:
        assert block + ":" in template, f"{block} is not in the shipped template"


def test_an_invisible_indent_before_a_comment_agrees_with_pyyaml(tmp_path):
    """**Part 5, and the review was WRONG about this one.** It reported the
    refusal as a defect on the strength of a docstring that promised the
    opposite. PyYAML RAISES on the same bytes, so refusing is agreeing with the
    reference reader - which is exactly what item 117 measured and ruled. The
    prose was the thing that was stale, and 'fixing' the behaviour would have
    made this reader invent a third opinion."""
    yaml = pytest.importorskip("yaml")
    text = ("scope: repo" + NL130 + "  # a note pasted from a web page"
            + NL130 + "gate_profile: solo" + NL130)
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)
    with pytest.raises(P.ProfileError):
        P.profile_top_value(_profile_at(tmp_path, text, "nbspnote"),
                            "gate_profile")

    source = Path(P.__file__).read_text(encoding="utf-8")
    assert "indented with ORDINARY whitespace" in source
    assert "PyYAML raises on the same bytes" in source, (
        "the docstring must state the ruling, or the next reader 'fixes' it")


@pytest.mark.parametrize("lines,reference,complains", [
    (["scope: repo", "gate_profile: solo"],
     {"scope": "repo", "gate_profile": "regulated"}, True),
    (["phases: [intake, test]"], {"phases": ["intake", "design", "test"]}, True),
    (["coverage_threshold: 85"], {"coverage_threshold": 85}, False),
    (["deny_raw_git: true"], {"deny_raw_git": True}, False),
    (["scope: repo"], {"scope": "repo"}, False),
])
def test_the_crosscheck_compares_values_not_only_keys(lines, reference, complains):
    """**The derived guard.** The crosscheck compared `set(profile_blocks)`
    against `set(reference)` and stopped, so two readers could agree about which
    keys EXIST and disagree about what they SAY - which is exactly how part 1
    escaped: both saw `gate_profile`, one could read its value and the other
    could not.

    Extracted so a positive control can watch it complain - item 115's lesson,
    where four of seven mutations survived because nothing proved the comparison
    could fail. The scalar cases pin that PyYAML's TYPES are not a disagreement:
    it reads `85` and this reader hands back the text."""
    said = P._value_disagreement("profile.yml", lines, reference)
    assert bool(said) is complains, said
    if complains:
        assert "disagree about what a key SAYS" in said


def test_a_value_disagreement_reaches_the_reader_as_a_refusal(tmp_path, monkeypatch):
    """**The composition, and without it the wiring is untested.**

    The parametrised cases above drive `_value_disagreement` directly; this one
    proves `_profile_lines` actually ASKS it and that what it says becomes a
    refusal a person sees. Producer and consumer each tested and never composed
    is the gap this project has found more often than any other class - and it
    cannot be provoked with a real profile any more, because item 130 fixed
    every disagreement it knew about.
    """
    root = _profile_at(tmp_path, "scope: repo" + NL130 + "gate_profile: solo"
                       + NL130, "composed")
    monkeypatch.setattr(P, "_CROSSCHECK_MEMO", None, raising=False)
    monkeypatch.setattr(
        P, "_value_disagreement",
        lambda name, lines, reference: f"{name}: the two readers disagree")
    with pytest.raises(P.ProfileError) as caught:
        P._profile_lines(root)
    assert "the two readers disagree" in str(caught.value)


def test_only_a_list_key_opens_a_sequence(tmp_path):
    """**A mutation survivor, and a real gap.** `COMPONENT_LIST_KEYS` is the
    discriminator: a bare SCALAR key still records the empty string it always
    did, and does not open a sequence that would swallow the next `- ` row.
    Widening the condition to every empty value made a bare `test_command:`
    VANISH rather than record blank, which `suite.declared` reads differently."""
    text = ("scope: workspace" + NL130 + "components:" + NL130
            + "  - name: web" + NL130 + "    path: web" + NL130
            + "    test_command:" + NL130)
    got = P.components(_profile_at(tmp_path, text, "barescalar"))[0]
    assert got.get("test_command") == "", (
        "a bare scalar key must record blank, not disappear")


# ── items 221/222: a block is a DEPTH, not an indentation ────────────────────
#
# A `components:` / `runners:` sequence may be written with its `- ` rows at
# column 0. That is legal YAML, and `_profile_entry_at`'s own docstring says
# the spelling "was measured, not assumed". The block walk named a line's block
# only from NON-INDENTED lines and a non-indented `- ` CLEARED it, so those rows
# named no block at all: every dial inside them was promoted to the workspace
# and the component itself was lost in the same step. The crosscheck could not
# see the scalar half of it, because it asked `scalar_from_lines` (top level
# only) while every consumer asks `profile_find` (any depth).

#: Every list key a component or a runner may carry that `profile_list` also
#: serves at the workspace level - the six a zero-indent sequence promoted.
PROMOTABLE_LIST_KEYS = ("known_failures", "sca_known_findings", "probes",
                        "phases", "block_on", "review_suppress")

_DIALS_221 = (("known_failures", "alpha-flake"),
              ("sca_known_findings", "CVE-A"),
              ("probes", "http://a/health"),
              ("phases", "implementation"),
              ("block_on", "security"),
              ("review_suppress", "alpha-rule"))


def _one_document_221(indent: str) -> str:
    """The SAME profile in one of two spellings: `indent` is what sits in front
    of each `- ` row of the `components:` block. YAML says a block sequence may
    sit at or below its key's own indentation, so `""` and `"  "` are the same
    document - which is the property under test."""
    rows = ["scope: workspace", "components:", indent + "- name: alpha",
            indent + "  path: alpha"]
    for key, value in _DIALS_221:
        rows.append(indent + "  " + key + ":")
        rows.append(indent + "  - " + value)
    return NL130.join(rows) + NL130


@pytest.mark.parametrize("key", PROMOTABLE_LIST_KEYS)
def test_a_zero_indent_sequence_does_not_promote_a_component_dial(tmp_path, key):
    """**Item 221, and both spellings are held against PyYAML rather than
    against each other.** A dial written inside a component is the COMPONENT's;
    at zero indent every one of these six became the WORKSPACE's, which for
    `block_on` is item 35's tighten-only floor defeated from inside the
    document and for `phases` is item 130's recorded consequence verbatim."""
    for name, indent in (("deep221", "  "), ("flat221", "")):
        text = _one_document_221(indent)
        assert key not in yaml.safe_load(text), (
            "the fixture must keep every dial out of the top level")
        assert P.profile_list(_profile_at(tmp_path, text, name), key) is None, (
            "a component's " + key + " is not the workspace's, at any indent")


def test_a_zero_indent_sequence_is_still_a_component(tmp_path):
    """The other half of the same step: the rows were not merely promoted, the
    component carrying them was LOST - `components()` fell through to the
    synthesised single-repository entry while its dials had already become the
    run's. Both spellings must answer with the declared component."""
    answers = [P.components(_profile_at(tmp_path, _one_document_221(indent), name))
               for name, indent in (("deep221c", "  "), ("flat221c", ""))]
    assert answers[0] == answers[1], "two spellings of one document, one answer"
    assert [c["name"] for c in answers[1]] == ["alpha"]
    assert "synthesised" not in answers[1][0], (
        "a declared component must not fall through to the synthesised one")
    assert P.component_count(
        _profile_at(tmp_path, _one_document_221(""), "flat221n")) == 1


def test_a_zero_indent_runner_keeps_its_phases_to_itself(tmp_path):
    """`runners` is the profile's other sequence-of-maps block, and item 130
    added it to `SEQUENCE_BLOCKS` for exactly this reason: a runner's `phases:`
    is the phases IT may run, not the phases the workspace runs."""
    text = (NL130.join(["scope: repo", "runners:", "- name: r1",
                        "  command: echo", "  capabilities: [chat]",
                        "  phases:", "  - implementation"]) + NL130)
    root = _profile_at(tmp_path, text, "flat221r")
    assert "phases" not in yaml.safe_load(text)
    assert P.profile_list(root, "phases") is None
    assert [r["name"] for r in P.runners_declared(root)] == ["r1"]


def test_the_crosscheck_asks_the_reader_the_consumers_ask(tmp_path):
    """**Item 222.** `_value_disagreement` asked `scalar_from_lines`, which
    skips indented lines and is therefore TOP-LEVEL ONLY, while every consumer
    reaches a value through `profile_find`, which reads at ANY depth. So a
    nested twin of a top-level key is what the harness enforces and what the
    guard cannot see - and item 130 widened this very check from key SETS to
    VALUES precisely so the two readers could not agree about which keys exist
    while disagreeing about what they say."""
    text = (NL130.join(["scope: repo", "review:", "  gate_profile: solo",
                        "gate_profile: regulated"]) + NL130)
    root = _profile_at(tmp_path, text, "twin222")
    assert yaml.safe_load(text)["gate_profile"] == "regulated"
    assert P._profile_entry(text.splitlines(), "gate_profile")[0] == "solo", (
        "the consumers' reader takes the first match at ANY depth")
    assert P.scalar_from_lines(text.splitlines(), "gate_profile") == "regulated", (
        "the reader the guard used to ask sees only the top level")
    with pytest.raises(P.ProfileError) as caught:
        P.profile_dials(root)
    assert "gate_profile" in str(caught.value)
    assert "regulated" in str(caught.value)


def test_the_crosscheck_still_reads_a_quoted_scalar_as_its_value(tmp_path):
    """The guard that must NOT fire: asking the consumers' reader hands back
    the RAW text, quotes and all, so a comparison that skipped the scalar
    coercion would report every quoted command in every profile as a
    disagreement - a gate that fires on everything is a gate somebody turns off
    in a week."""
    text = (NL130.join(['scope: repo', 'test_command: "mvn test"',
                        'coverage_threshold: 85']) + NL130)
    root = _profile_at(tmp_path, text, "quoted222")
    assert P.profile_limits(root) == []
    assert P.profile_scalar(root, "test_command") == "mvn test"
    assert P.profile_scalar(root, "coverage_threshold") == 85


def test_a_nested_yaml_boolean_is_crosschecked_like_a_top_level_one(tmp_path):
    """**Item 223 (A4c-3).** A YAML 1.1 boolean at the TOP LEVEL already refuses
    by disagreeing — `block_on: [on]` reads `['on']` here and `[True]` to PyYAML,
    so the two readers already agree-by-refusing. The SAME list one level down,
    `review.block_on: [on]`, was invisible: `_value_disagreement` compared only
    TOP-LEVEL keys, so a nested twin of the very shape it exists to catch walked
    straight past. The consumer reads it (`profile_dials` → `profile_list`), so a
    gate acts on `['on']` while PyYAML would have enforced `[True]`.
    """
    text = "scope: repo\nreview:\n  block_on: [on]\n"
    lines = [P._strip_comment(l).rstrip() for l in text.splitlines()]
    assert P.list_from_lines(lines, "block_on") == ["on"], "the consumer's reader"
    assert yaml.safe_load(text)["review"]["block_on"] == [True], "YAML 1.1"
    refusal, limit = P.profile_crosscheck(text, lines, "profile.yml")
    assert not limit, limit
    assert "block_on" in refusal and "disagree about what a key SAYS" in refusal, refusal
    # and it reaches a person through the one door every consumer passes
    root = profile_at(tmp_path, "nested-bool", text)
    with pytest.raises(P.ProfileError) as caught:
        P.profile_dials(root)
    assert "block_on" in str(caught.value), caught.value


def test_a_valid_nested_list_is_not_newly_refused(tmp_path):
    """**The false-positive control for item 223 (over-refusal, item 117/128).**
    The nested crosscheck must fire ONLY on a real disagreement: a valid
    `review.block_on: [critical, high]` reads the same in both readers and must
    stay accepted, or every profile the installer writes would refuse itself."""
    root = profile_at(tmp_path, "nested-ok",
                      "scope: repo\nreview:\n  block_on: [critical, high]\n")
    assert P.profile_limits(root) == []
    assert P.component_count(root) == 0, "reads clean, no refusal"
    assert P.profile_dials(root)["block_on"] == ["critical", "high"]


def test_a_nested_block_on_under_two_sections_is_scoped(tmp_path):
    """**The scoping control for item 223 (a mutation gap, closed).** A key that
    appears BOTH at the top level and nested under a section, each AGREEING with
    its own PyYAML value but differing from the other, must NOT be reported as a
    disagreement. The nested comparison reads the SECTION's lines, so it is about
    `review.block_on` and not the top-level `block_on` the first-match reader
    would otherwise hand back — read the whole file for a nested key and you
    invent a disagreement that is not there."""
    text = "block_on: [critical]\nreview:\n  block_on: [high]\n"
    lines = [P._strip_comment(l).rstrip() for l in text.splitlines()]
    ref = yaml.safe_load(text)
    assert ref["block_on"] == ["critical"] and ref["review"]["block_on"] == ["high"]
    assert P._value_disagreement("profile.yml", lines, ref) == "", (
        "each key agrees with its OWN PyYAML value; scoping keeps them apart")
    root = profile_at(tmp_path, "twoscope", text)
    assert P.component_count(root) == 0, "reads clean, no invented refusal"


def test_a_duplicate_top_level_key_is_refused_without_pyyaml(tmp_path, monkeypatch):
    """**Item 224 (A4c-4), the wider finding.** `parse` refuses a duplicate key
    by name (`policy.py:357`) and the PROFILE reader did not: the hand reader
    takes the FIRST and PyYAML the LAST, so `gate_profile: solo` above
    `gate_profile: regulated` read `solo` here and `regulated` to PyYAML — a
    LOOSENING the value crosscheck caught only when PyYAML was installed AND the
    values differed. The stdlib reader exists to govern a machine with NO
    packages, so the refusal must hold with PyYAML gone."""
    real_import = builtins.__import__

    def refuse_yaml(name, *rest, **kw):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real_import(name, *rest, **kw)

    monkeypatch.setattr(builtins, "__import__", refuse_yaml)
    monkeypatch.setattr(P, "_CROSSCHECK_MEMO", None, raising=False)
    root = profile_at(tmp_path, "dup-nopyyaml",
                      "scope: repo\ngate_profile: solo\ngate_profile: regulated\n")
    with pytest.raises(P.ProfileError) as caught:
        P.profile_dials(root)
    msg = str(caught.value)
    assert "gate_profile" in msg and "set twice" in msg, msg
    assert "profile.yml:3" in msg, ("the refusal names the second occurrence", msg)


def test_a_duplicate_components_block_is_refused(tmp_path):
    """**Item 224, and NO sequence-block exemption.** Two top-level `components:`
    blocks are the same disagreement as two scalars: the profile reader MERGED
    them while PyYAML keeps the LAST, so exempting the sequence blocks would leave
    exactly the hand-vs-PyYAML split this refusal closes. Refused by name, like
    every other duplicate top-level key."""
    root = profile_at(tmp_path, "dupcomp",
                      "scope: workspace\ncomponents:\n  - name: a\n"
                      "components:\n  - name: b\n")
    with pytest.raises(P.ProfileError) as caught:
        P.component_count(root)
    msg = str(caught.value)
    assert "components" in msg and "set twice" in msg, msg
    assert "profile.yml:4" in msg, msg


def test_repeated_nested_keys_across_components_are_not_duplicates(tmp_path):
    """**The false-positive control for item 224 (over-refusal, item 117/128).**
    Every component carries its own `name`/`path`/`test_command`, so a dup-key
    check that looked BELOW the top level would refuse every multi-component
    workspace. Only TOP-LEVEL duplicates are refused, and a profile with each
    top-level key once must still read."""
    root = profile_at(tmp_path, "multi",
                      "scope: workspace\ncomponents:\n"
                      "  - name: web\n    path: web\n    test_command: pytest\n"
                      "  - name: api\n    path: api\n    test_command: pytest\n")
    assert P.component_count(root) == 2
    assert P.profile_limits(root) == []
    single = profile_at(tmp_path, "single",
                        "scope: repo\ngate_profile: solo\nsigning: none\n")
    assert P.profile_dials(single)["gate_profile"] == "solo"


def test_both_readers_refuse_a_duplicate_key_the_same_way(tmp_path):
    """**The weld (item 115).** `parse` (policy.yml) and the profile reader each
    refuse a duplicate top-level key, and the SENTENCE is single-sourced in
    `duplicate_key_refusal` so the two cannot drift. Both are driven here and
    both refusals must be that ONE spelling — a restatement would pass this only
    until somebody edited one copy. `parse` builds a dict and catches the second
    key against it; the profile reader walks lines and holds no dict, which is
    why the detection is not shared but the saying is."""
    assert "is set twice - which one is the policy?" in P.duplicate_key_refusal(
        "policy.yml", 2, "gate_profile")
    with pytest.raises(P.PolicyError) as pol:
        P.parse("gate_profile: solo\ngate_profile: team\n", "policy.yml")
    assert str(pol.value) == P.duplicate_key_refusal("policy.yml", 2, "gate_profile")
    root = profile_at(tmp_path, "weld",
                      "scope: repo\ngate_profile: solo\ngate_profile: team\n")
    with pytest.raises(P.ProfileError) as prof:
        P.component_count(root)
    assert str(prof.value) == P.duplicate_key_refusal("profile.yml", 3, "gate_profile")


def test_profile_blocks_names_a_zero_indent_block_once(tmp_path):
    """**A mutation survivor, and a real gap.** `profile_blocks` says it returns
    every top-level key *in the order it sees them*, and it excluded a block's
    body by asking whether the line was INDENTED. Once a column-0 `- ` row sits
    inside the block above it that row is un-indented too, so the old test named
    `components` once per item — a public reader answering a list nobody wrote,
    invisible to the crosscheck because it takes a `set`."""
    for name, indent in (("deep221b", "  "), ("flat221b", "")):
        lines = P._profile_lines(_profile_at(tmp_path,
                                             _one_document_221(indent), name))
        assert P.profile_blocks(lines) == ["scope", "components"], (
            "one document, one answer, at either indent")


def test_a_key_that_already_has_a_value_opens_no_block(tmp_path):
    """The other half of item 221's depth rule, and the half nothing drove.
    `opened` is the top-level key whose own line carried NO value; a key that
    has already SAID something has no body for a column-0 `- ` row to join, so
    that row still names nothing. A reader that let ANY key open a block would
    hand the row to `scope`, and `_profile_block` would then report a line
    nobody wrote inside `scope` as `scope`'s content.

    **No wrong answer is being pointed at here, and the test does not pretend
    otherwise.** PyYAML REFUSES this document, so there is no second reader to
    be caught disagreeing: the crosscheck states a LIMIT and `_profile_lines`
    reads on, which is `profile_limits`' documented contract rather than a
    defect. What is pinned is what THIS reader returns, because the consumers
    enforce that answer whether or not PyYAML could look."""
    text = NL130.join(["scope: repo", "- stray"]) + NL130
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)
    root = _profile_at(tmp_path, text, "valued221")
    assert P.profile_limits(root) != [], (
        "a file PyYAML cannot read is a stated limit, never a refusal")
    assert list(P._profile_sections(text.splitlines())) == [
        ("scope", "scope: repo"), ("", "- stray")], (
        "`scope` said its value on its own line, so it opened nothing")
    assert P._profile_block(root, "scope") == [], (
        "and a key that opened nothing has nothing inside it")
