"""External sources become runs, through the one door. Items 56 and 49.

Two items on one seam, because they were one question asked twice: a tracker issue
and a firing alert are both *something outside the repository says work is needed*,
and building them separately would have produced two answers to what a connector is.

**The claim that carries both, and the test that holds it:** the harness names no
vendor. A connector is a mapping an adopter writes, so a tracker nobody here has
heard of is exactly as supported as the three with stubs — asserted by sweeping the
module for the stub names rather than by trusting the design.

**The other half is a duplication that had two copies and now has one.** `debt.py`
and `verify.py` each carried the open-run policy — *a run that CLOSED does not hold
this filed* — differing only in how they built a title. Adding a third caller is
where that becomes a pattern nobody maintains, so the tests here assert both callers
reach the shared one and that neither kept a private copy.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import connect as C  # noqa: E402
from tools.sdlc import debt as D  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import verify as V  # noqa: E402

STUBS = ROOT / "sdlc_templates" / "connectors"
PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')

#: One payload per shipped stub, shaped the way that source really posts.
PAYLOADS = {
    "jira": {"key": "AB-7", "self": "https://example.invalid/AB-7",
             "fields": {"summary": "the export drops the last row",
                        "description": "Reported by a user; reproduced twice."}},
    "github-issue": {"number": 41, "title": "timeout is not configurable",
                     "body": "It is hard-coded to 30s.",
                     "html_url": "https://example.invalid/i/41"},
    "alert": {"fingerprint": "9f1", "generatorURL": "https://example.invalid/g",
              "labels": {"alertname": "QueueDepthHigh"},
              "annotations": {"description": "Depth above threshold for 10m."}},
}


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed repository, because intake refuses a dirty tree."""
    return build_repo(tmp_path / "repo")


def build_repo(r: Path) -> Path:
    """The fixture's body, callable, so a test can build a SECOND repository
    under a root it chooses - one with spaces in it - from the same one source
    rather than a copy that drifts."""
    (r / ".sdlc").mkdir(parents=True)
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    return r


def declare(repo: Path, source: str, mapping: dict) -> None:
    """Write a mapping AND COMMIT IT.

    A connector mapping is repository configuration, like the profile beside it, so
    an uncommitted one refuses the run - correctly, and this fixture found that by
    not committing. `intake` refuses a dirty tree because P5's baseline would
    otherwise describe a tree nobody can reconstruct.
    """
    folder = repo / C.CONNECTORS
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{source}.json").write_text(json.dumps(mapping), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "map")


def undeclared_source(mod) -> str:
    """The source text with docstrings removed.

    Prose describing the rule is not the rule being broken - item 48 hit exactly
    this when a module's own docstring named `subprocess` while explaining that it
    imports nothing that can execute a command.
    """
    import ast

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree).lower()


def stub(name: str) -> dict:
    return json.loads((STUBS / f"{name}.json").read_text(encoding="utf-8"))


# ── the harness names no vendor ─────────────────────────────────────────────


#: Frozen deliberately, and NOT derived from the stub directory: a sweep that
#: read its own list from what happens to be shipped would shrink to nothing the
#: moment somebody removed a stub, which is the mirror-not-a-test shape item 67
#: found. Names beyond the three shipped are here so the rule outlives them.
VENDORS = ("jira", "github", "alert", "sentry", "pagerduty", "servicenow")


def test_the_vendor_sweep_can_actually_fail(tmp_path):
    """**The guard's own guard.** An empty name list, or a reader that returns
    nothing, makes the assertion below vacuous while it stays green - which is
    exactly how a roster goes stale unnoticed."""
    assert len(VENDORS) >= 3
    fake = tmp_path / "pretend.py"
    fake.write_text('"""A docstring naming jira, which is prose."""\nSOURCE = "jira"\n',
                    encoding="utf-8")

    class Mod:
        __file__ = str(fake)

    text = undeclared_source(Mod)
    assert "jira" in text, "the sweep cannot see a vendor named in code"
    assert "prose" not in text, "the sweep reads docstrings, so prose would trip it"


def test_no_shipped_source_is_named_anywhere_in_the_module():
    """**The property that makes an unlisted tracker as supported as a listed one.**

    Three stubs exist and the module must not know that. If a vendor is ever
    special-cased in code, the three stop being three declarations of one mechanism
    and become three half-integrations — which is what building 56 and 49 apart
    would have produced.
    """
    text = undeclared_source(C)
    for name in VENDORS:
        assert name not in text, f"{name!r} is named in connect.py"


def test_every_shipped_stub_is_a_usable_mapping(tmp_path, repo):
    """A stub that does not parse, or that declares no title, is a file an adopter
    copies and then debugs. Each is exercised against a payload of its own shape."""
    found = sorted(p.stem for p in STUBS.glob("*.json"))
    assert found == sorted(PAYLOADS), f"stubs and payloads disagree: {found}"
    for name, payload in PAYLOADS.items():
        declare(repo, name, stub(name))
        title, spec = C.spec_for(name, C.mapping_for(repo, name), payload)
        assert title and title in spec
        assert name in spec, "the specification does not say where it came from"


def test_the_three_stubs_travel_the_same_code_path(repo):
    """One mechanism, three declarations — the whole argument for one seam.

    Every stub opens a run through the same call, and each run's kind is the one
    ITS mapping declared, which is the only thing that differs between them.
    """
    opened = {}
    for name, payload in PAYLOADS.items():
        declare(repo, name, stub(name))
        rec = C.file_one(repo, name, payload)
        opened[name] = ledger.load(repo, rec["run_id"])
    assert len(opened) == 3
    assert {s["kind"] for s in opened.values()} == {"requirement", "defect"}
    for name, state in opened.items():
        assert state["kind"] == stub(name)["kind"]


# ── one home for the open-run policy ────────────────────────────────────────


def test_neither_caller_kept_a_private_copy_of_the_policy():
    """**Two copies is one too many and three is a pattern nobody maintains.**

    Asserted on the mechanism rather than on a comment: the policy is the loop that
    filters prior runs on `ledger.TERMINAL`, and neither module may still run one.
    """
    for mod in (D, V):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "TERMINAL" not in text, \
            f"{Path(mod.__file__).name} still carries the policy itself"
        assert "connect.open_runs_for" in text, \
            f"{Path(mod.__file__).name} does not reach the shared policy"


def test_both_callers_answer_through_the_shared_policy(repo, monkeypatch):
    """Composed, not merely present. If either caller stopped calling it, this
    fails — which is the check the two copies could never have."""
    seen = []
    real = C.open_runs_for

    def spy(target, title, spec):
        seen.append(title)
        return real(target, title, spec)

    monkeypatch.setattr(C, "open_runs_for", spy)
    D.open_runs_for(repo, {"title": "a thing", "why": "w", "source": "s"})
    V.open_runs_for(repo, "https://example.invalid/health")
    assert len(seen) == 2, "a caller answered without the shared policy"
    assert any("health" in t for t in seen)


def test_a_closed_run_does_not_hold_the_item_filed(repo):
    """**The sharp half of the policy.** If the thing is still true after somebody's
    attempt went away, it outlived the attempt — a tracker that treats a closed run
    as payment is one that quietly forgets."""
    declare(repo, "jira", stub("jira"))
    first = C.file_one(repo, "jira", PAYLOADS["jira"])
    assert C.file_one(repo, "jira", PAYLOADS["jira"]) is None
    ledger.advance(repo, first["run_id"], status="closed", note="closed unfixed")
    again = C.file_one(repo, "jira", PAYLOADS["jira"])
    assert again is not None and again["run_id"] != first["run_id"]


def test_the_same_item_seen_twice_is_one_run(repo):
    """Deterministic by construction: nothing in the specification reads a clock or
    a run id, so a webhook that redelivers does not open a second run."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    # Two DISTINCT objects carrying the same item, which is what a redelivered
    # webhook actually hands over - the same dict twice cannot discriminate a
    # deterministic specification from one keyed on the payload object itself.
    one = C.spec_for("alert", mapping, json.loads(json.dumps(PAYLOADS["alert"])))
    two = C.spec_for("alert", mapping, json.loads(json.dumps(PAYLOADS["alert"])))
    assert one == two
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])
    assert C.open_runs_for(repo, *one) == [rec["run_id"]]


# ── it refuses rather than filing something nobody can find ─────────────────


def test_an_undeclared_source_is_refused_by_name(repo):
    with pytest.raises(C.ConnectError) as exc:
        C.mapping_for(repo, "nothing-here")
    assert "nothing-here" in str(exc.value)
    assert "a mapping you write" in str(exc.value)


def test_a_mapping_with_no_title_is_refused(repo):
    declare(repo, "x", {"body": "b"})
    with pytest.raises(C.ConnectError) as exc:
        C.mapping_for(repo, "x")
    assert "title" in str(exc.value)


def test_a_mapping_that_is_not_a_mapping_is_refused(repo):
    folder = repo / C.CONNECTORS
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(C.ConnectError) as exc:
        C.mapping_for(repo, "x")
    assert "not a mapping" in str(exc.value)


def test_a_payload_the_mapping_does_not_describe_is_refused(repo):
    """Either the mapping is wrong or this is not the payload it describes, and
    both are somebody's to fix — filing under a generated title would bury it."""
    declare(repo, "jira", stub("jira"))
    with pytest.raises(C.ConnectError) as exc:
        C.spec_for("jira", C.mapping_for(repo, "jira"), {"nothing": "here"})
    assert "fields.summary" in str(exc.value)


def test_a_list_of_items_is_refused_rather_than_filed(tmp_path):
    """Filing a list would open runs nobody counted."""
    p = tmp_path / "many.json"
    p.write_text('[{"title": "a"}, {"title": "b"}]', encoding="utf-8")
    with pytest.raises(C.ConnectError) as exc:
        C.read_payload(str(p))
    assert "one at a time" in str(exc.value)


def test_a_payload_that_is_not_json_is_refused(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("not json at all", encoding="utf-8")
    with pytest.raises(C.ConnectError) as exc:
        C.read_payload(str(p))
    assert "not JSON" in str(exc.value)


# ── the dotted path reader ──────────────────────────────────────────────────


#: A payload carrying a key that is PRESENT AND NULL beside one that is absent.
#: The null was missing from this table, which is why the fold below survived.
DIG_PAYLOAD = {"a": {"b": [10, 11], "nothing": None}}


@pytest.mark.parametrize("path,want", [
    ("a", {"b": [10, 11], "nothing": None}),
    ("a.b", [10, 11]),
    ("a.b.1", 11),
    ("a.b.9", None),
    ("a.nothing", None),          # present and null — the row that was absent
    ("a.missing", None),
    ("missing.entirely", None),
])
def test_a_dotted_path_reaches_what_is_there_and_none_otherwise(path, want):
    """With no `missing` given, `None` is still the answer for both — the default
    is unchanged, so every existing caller reads exactly what it read before."""
    assert C.dig(DIG_PAYLOAD, path) == want


#: The same paths again, asked with a sentinel. **`want` is the WORD, not the
#: value**, so a reader can see at a glance which rows are meant to differ.
@pytest.mark.parametrize("path,want", [
    ("a.b", "present"),
    ("a.b.1", "present"),
    ("a.nothing", "present"),     # present and null
    ("a.b.9", "absent"),
    ("a.missing", "absent"),
    ("missing.entirely", "absent"),
])
def test_a_dotted_path_tells_absent_from_present_but_null(path, want):
    """**The distinction `dig`'s own docstring promised and did not deliver.**

    *None means the path was not there, which the caller must not confuse with a
    field that was present and empty* — and `None` was also the answer for a field
    that WAS there and was null, so the two were one answer and the docstring was
    a claim about a distinction the function could not make."""
    found = C.dig(DIG_PAYLOAD, path, missing=C.ABSENT)
    assert (found is not C.ABSENT) == (want == "present"), \
        f"{path!r} read as {found!r}, which is not {want}"


def test_a_present_but_empty_title_is_refused_like_an_absent_one(repo):
    """Present-and-blank and absent are different facts and the SAME repair here:
    nobody can find a run with no title, so neither files."""
    declare(repo, "x", {"title": "t"})
    with pytest.raises(C.ConnectError):
        C.spec_for("x", C.mapping_for(repo, "x"), {"t": "   "})


# ── absent and present-but-null are different answers ───────────────────────
#
# The refusal for an absent title says *either the mapping is wrong or this is
# not the payload it describes*. For a field the source sent as null both halves
# of that sentence are FALSE: the mapping is right, the payload is the one it
# describes, and the source had no value. An adopter told to check a mapping that
# is already correct looks in the one place the answer is not.


def test_a_null_title_is_not_reported_as_a_wrong_mapping(repo):
    """**The misdiagnosis, with its control beside it.** The same connector over
    two payloads: one with the declared path absent, one with it present and null.
    Both refuse - a run needs a title either way - and they must not refuse with
    the SAME sentence, because only one of them is the adopter's to fix."""
    declare(repo, "nt", {"title": "a.title"})
    mapping = C.mapping_for(repo, "nt")

    with pytest.raises(C.ConnectError) as absent:
        C.spec_for("nt", mapping, {"a": {}})
    with pytest.raises(C.ConnectError) as null:
        C.spec_for("nt", mapping, {"a": {"title": None}})

    assert str(absent.value) != str(null.value), \
        f"absent and present-but-null gave one sentence: {absent.value}"
    assert "null" in str(null.value), \
        f"the null case does not say the field was there: {null.value}"
    assert "the mapping is wrong" not in str(null.value), \
        f"a correct mapping was blamed: {null.value}"
    # The control: a VALID payload through the same call still files a title, so
    # this cannot pass by refusing everything.
    title, _spec = C.spec_for("nt", mapping, {"a": {"title": "a real title"}})
    assert title == "a real title"


def test_a_null_in_the_mapping_is_not_reported_as_an_undeclared_field(repo):
    """The same fold one layer up, at the identical site in `mapping_for`. Fixing
    the payload reader and leaving this one is the shape this repository keeps
    finding: a rule hardened where somebody happened to be looking.

    **ONE connector name, re-declared.** Written with two names first, and that
    version passed at the unfixed source - the two messages differ by the FILE
    PATH they quote, so `!=` was satisfied without either sentence saying anything
    about null. A control that can only pass is the defect this row exists to fix.
    """
    declare(repo, "n", {})
    with pytest.raises(C.ConnectError) as absent:
        C.mapping_for(repo, "n")
    declare(repo, "n", {"title": None})
    with pytest.raises(C.ConnectError) as null:
        C.mapping_for(repo, "n")
    assert str(absent.value) != str(null.value), \
        f"absent and null declared the same way: {absent.value}"
    assert "null" in str(null.value), null.value
    # The control: the same name, declared properly, is accepted.
    declare(repo, "n", {"title": "labels.alertname"})
    assert C.mapping_for(repo, "n")["title"] == "labels.alertname"


def test_the_null_refusal_names_every_required_key_it_found(repo, monkeypatch):
    """**The weld, without which the sentence's tail is unkillable.**

    The refusal's head is built from `REQUIRED`; its tail used to say the word
    `title` in prose. With one required key those two are indistinguishable, so
    no test at today's `REQUIRED` could tell a derived tail from a typed one -
    the hard-coded name was an EQUIVALENT MUTANT and would have stayed one.

    `REQUIRED` is widened here to make them differ. A tail that names one key
    while the head names two is a sentence telling an adopter to fix half of
    what is wrong, which is the second-statement-of-one-fact shape this file is
    otherwise full of."""
    monkeypatch.setattr(C, "REQUIRED", ("title", "kind"))
    declare(repo, "n", {"title": None, "kind": None})
    with pytest.raises(C.ConnectError) as exc:
        C.mapping_for(repo, "n")
    said = str(exc.value)
    for key in C.REQUIRED:
        assert said.count(key) >= 2, \
            (f"{key!r} is named {said.count(key)} time(s), so head and tail "
             f"cannot both be derived from REQUIRED: {said}")


# ── no null reaches a consumer as the four-character string `None` ──────────


def test_a_null_kind_never_reaches_the_run_opener_as_a_string(repo, tmp_path):
    """**SPAWNED. Item 150's shape at a second reader.**

    `kind` has a defensible default, so a mapping that names it as null means
    *unset* — but `str(mapping.get("kind", "requirement"))` only defaults when the
    key is ABSENT, so a null one arrived at `intake.open_run` as the literal
    `'None'`. MEASURED at `45fdcaa` by spawning this exact command: exit 2,
    `IntakeRefused: unknown kind 'None' - one of requirement, defect,
    deploy-change`. A blank became a name nothing can ever match."""
    declare(repo, "k", {"title": "labels.alertname", "kind": None})
    payload = tmp_path / "alert.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    proc, said = spawn_door(repo, "--source", "k", "--payload", str(payload),
                            "--file", "--allow-dirty", "--json")
    assert "'None'" not in said, f"a null reached a consumer as a string: {said!r}"
    assert proc.returncode == 0, f"a null kind refused the item: {said!r}"

    runs = list(ledger.list_runs(repo))
    assert len(runs) == 1, f"the item did not file: {said!r}"
    assert ledger.load(repo, runs[0]["run_id"])["kind"] == "requirement"


def test_a_null_kind_reads_the_same_in_the_specification_and_in_the_run(repo):
    """**The producer/consumer weld.** The specification text is what `spec_hash`
    fingerprints, so if these two disagree the run's recorded kind is not the one
    its own identity was computed from, and the same item seen twice is two runs.
    Both sites are asked here rather than trusting that one call covers both."""
    declare(repo, "k", {"title": "labels.alertname", "kind": None})
    mapping = C.mapping_for(repo, "k")
    _title, spec = C.spec_for("k", mapping, PAYLOADS["alert"])
    said = [ln for ln in spec.splitlines() if ln.startswith("kind:")]
    assert said == ["kind: requirement"], said

    rec = C.file_one(repo, "k", PAYLOADS["alert"], allow_dirty=True)
    assert ledger.load(repo, rec["run_id"])["kind"] == spec.splitlines()[2].split()[1]


# ── filing is an explicit act ───────────────────────────────────────────────


def test_without_file_nothing_is_written(repo, tmp_path, capsys):
    """Item 17's rule: a tool that filed on being run would open a backlog item
    every time somebody looked at a tracker."""
    declare(repo, "jira", stub("jira"))
    payload = tmp_path / "issue.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["jira"]), encoding="utf-8")
    assert C.main(["--target", str(repo), "--source", "jira",
                   "--payload", str(payload)]) == 0
    assert "nothing was written" in capsys.readouterr().out
    assert list(ledger.list_runs(repo)) == []


def test_the_cli_files_and_then_reports_the_duplicate(repo, tmp_path, capsys):
    declare(repo, "jira", stub("jira"))
    payload = tmp_path / "issue.json"
    payload.write_text(json.dumps(PAYLOADS["jira"]), encoding="utf-8")
    args = ["--target", str(repo), "--source", "jira",
            "--payload", str(payload), "--file"]
    assert C.main(args) == 0
    assert "filed" in capsys.readouterr().out
    assert C.main(args) == 0
    assert "already open" in capsys.readouterr().out
    assert len(list(ledger.list_runs(repo))) == 1


#: The payload argument that must be a path this repository cannot supply as a
#: literal: a machine path may not enter a test id, so the row carries a marker
#: and the test builds the path under `tmp_path`.
ABSENT_FILE = "<a path built under tmp_path>"


def spawn_door(repo: Path, *args: str, stdin_text: str = ""):
    """Spawn the real door with stdin under THIS test's control.

    **The whole of why this exists.** The in-process test that stood here read
    `--payload -` under pytest, where `sys.stdin.read()` RAISES, so its `== 2`
    was satisfied by the framework and the branch its arguments name was never
    reached. Captured verbatim at `45fdcaa`:

        !! the connector did not finish: ConnectError: the payload could not be
        read: pytest: reading from stdin while output is captured!

    A spawned process supplies its own stdin, so each refusal is reached on
    purpose rather than pre-empted by the test runner.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "connect.py"),
         "--target", str(repo), *args],
        input=stdin_text, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


#: **SOME of the ways this door refuses before it reaches the ledger** - not all
#: of them, and the first draft of this comment said otherwise.
#:
#: **Ten, MEASURED 2026-09-16 at `ff7065a`** by spawning fifteen command lines:
#: thirteen distinct texts, which collapse to ten distinct SENTENCES once the
#: quoted path is set aside. **Nothing re-derives that figure** - it is evidence
#: about the day and the commit it was taken at, not a threshold, and a change to
#: any refusal in `connect.py` makes it stale. The population as measured, so the
#: next person starts from a list rather than a guess:
#:
#:  1. `does not exist - ... declares no connector`  **covers three inputs**
#:  2. `<connector> could not be read: <json error>`      (malformed mapping)
#:  3. `<connector> holds list, not a mapping`
#:  4. `<connector> declares no title`
#:  5. `<connector> declares title as null`              **new in this commit**
#:  6. `the payload could not be read: [Errno N]`   covers absent AND a directory
#:  7. `the payload is not JSON`                    covers empty AND malformed
#:  8. `the payload is a list, not one item`
#:  9. `the payload has nothing at X`               covers absent AND blank
#: 10. `the payload carries X and it is null`           **new in this commit**
#:
#: Rows 5 and 10 are spawned by their own tests below rather than from this table,
#: because each needs a connector this fixture must write first. Rows 2, 3, 4 and
#: 9 are pre-existing and remain unspawned - named here so that is a known gap
#: rather than an unknown one.
#:
#: **Row 1 is a PRE-EXISTING CONFLATION and is deliberately not fixed here**: an
#: undeclared source, a `--target` that does not exist, and a connector path that
#: is a DIRECTORY all reach `path.is_file()` being false and all get the same
#: sentence. Two of those three are not "you declared no connector" at all, and a
#: person told to write a mapping they already wrote looks in the wrong place.
#: Filed for the leader; fixing it is not this row's ruling.
#:
#: The three rows `--source absent --payload -` can reach are all in here:
#: MEASURED, that one command line answers "declares no connector" with a JSON
#: object on stdin and "the payload is not JSON" with an empty one, while
#: in-process it answered neither.
DOOR_REFUSALS = [
    ("an undeclared source", ["--source", "absent", "--payload", "-"],
     '{"labels": {"alertname": "QueueDepthHigh"}}', "declares no connector"),
    ("a payload file that is not there", ["--source", "alert",
                                          "--payload", ABSENT_FILE],
     "", "the payload could not be read"),
    ("empty input", ["--source", "alert", "--payload", "-"],
     "", "the payload is not JSON"),
    ("a list rather than one item", ["--source", "alert", "--payload", "-"],
     '[{"a": 1}, {"a": 2}]', "one at a time"),
]


@pytest.mark.parametrize("what,args,stdin_text,said_words", DOOR_REFUSALS)
def test_each_door_refusal_is_a_spawned_exit_code(repo, tmp_path, what, args,
                                                  stdin_text, said_words):
    """**SPAWNED. A CLI's contract is a process, and this one was green on pytest.**

    Each refusal names itself and exits could-not-look. An in-process call cannot
    tell a refusal from a traceback, and worse here: it could not tell one refusal
    from ANOTHER, because the framework's own stdin refusal answered first."""
    declare(repo, "alert", stub("alert"))
    here = str(tmp_path / "not-here.json")
    args = [here if a == ABSENT_FILE else a for a in args]
    proc, said = spawn_door(repo, *args, stdin_text=stdin_text)
    assert proc.returncode == 2, \
        f"could-not-look is exit 2; {what} got {proc.returncode} saying {said!r}"
    assert said_words in said, f"{what} did not say {said_words!r}: {said!r}"
    assert list(ledger.list_runs(repo)) == [], f"{what} wrote to the ledger"


#: Anything that looks like a filesystem path, so a SENTENCE can be compared
#: without the path it quotes. A drive letter, a UNC prefix, a POSIX absolute
#: path, or any run containing a separator.
PATHLIKE = re.compile(r"'[^']*'|\"[^\"]*\"|\S*[\\/]\S*|[A-Za-z]:\S*")


def sentence_of(said: str, *roots: Path) -> str:
    """One refusal reduced to the WORDS it says, with every path removed.

    **`roots` are the directories the TEST built, and they are removed FIRST,
    as whole strings, before any pattern runs.** A pattern cannot know where a
    path ends: `\\S*` stops at a space, so a root containing a directory with
    spaces left that directory's WORDS in the key - measured by review, three
    of eight refusals keyed differently from a spaced root than from a plain
    one. The fixture knows the root exactly, so the root is never recovered by
    guessing. Each is removed in both spellings a message can carry it in -
    as written, and as a repr doubles its backslashes (`OSError` quotes a
    filename that way) - longest first, so a root nested inside another is
    removed whole rather than leaving its tail behind.

    **The key this replaces could capture a machine path, and did.** It was
    `said.split(":")[-1].strip()[:40]` - the tail after the last colon. On Windows
    a message quoting an absolute path has its last colon in the DRIVE LETTER, so
    forty characters of path became the key, and two refusals were "distinct"
    only because their paths differed. The set still had four members, so the
    assertion passed, and on a POSIX-shaped run it passes for a different reason
    again - green both ways, for neither of the right ones.

    That is the same vacuity this file fixes two hundred lines above: an
    expectation satisfied by something other than the behaviour it names. A key
    built from the sentence cannot be satisfied by a path.
    """
    body = said.split("ConnectError: ", 1)[-1]
    spellings = {form for root in roots
                 for form in (str(root), str(root).replace("\\", "\\\\"))}
    for form in sorted(spellings, key=len, reverse=True):
        body = body.replace(form, " ")
    return " ".join(PATHLIKE.sub(" ", body).split())


def test_the_door_refusals_are_all_different_sentences(repo, tmp_path):
    """**The control that stops the rows above being one assertion again.**

    Rule of this repository: a guard proved by making the input invalid proves
    only that valid is required. Every row here is a VALID command line reaching
    a DIFFERENT branch, so the set of sentences must have as many members as the
    table - if the door ever collapses two of them, a person is told the wrong
    thing about a repository they can fix."""
    declare(repo, "alert", stub("alert"))
    seen = set()
    for what, args, stdin_text, _words in DOOR_REFUSALS:
        args = [str(tmp_path / "not-here.json") if a == ABSENT_FILE else a
                for a in args]
        _proc, said = spawn_door(repo, *args, stdin_text=stdin_text)
        seen.add(sentence_of(said, repo, tmp_path))
    assert len(seen) == len(DOOR_REFUSALS), \
        f"{len(DOOR_REFUSALS)} refusals collapsed to {len(seen)}: {seen}"


def test_the_distinctness_key_survives_two_refusals_quoting_one_path(repo):
    """**The proof that the new key reads the sentence and not the path.**

    ONE connector name, re-declared between the two spawns, so the two refusals
    quote a path that is character-for-character IDENTICAL. Under the old
    tail-slice key both keys were that path and the two collapsed to one. Under
    `sentence_of` the path is gone and only the words remain, which is the only
    thing that can tell these two apart.

    It is also the pair the rest of this section turns on: *declares no title*
    and *declares title as null* are the same file saying two different things.
    """
    declare(repo, "n", {})
    absent_proc, absent_said = spawn_door(repo, "--source", "n",
                                          "--payload", "-", stdin_text="{}")
    declare(repo, "n", {"title": None})
    null_proc, null_said = spawn_door(repo, "--source", "n",
                                      "--payload", "-", stdin_text="{}")

    # The path is TAKEN FROM THE FIXTURE, never recovered from the output by a
    # pattern. The pattern that stood here could not cross a space, so under any
    # base temp directory containing one this proof failed on the machine it
    # ran on - a false red, which is how a gate gets switched off.
    quoted = str(repo / C.CONNECTORS / "n.json")
    assert quoted in absent_said and quoted in null_said, \
        f"the fixture did not produce ONE path in both: {absent_said!r}"
    assert absent_proc.returncode == 2 and null_proc.returncode == 2
    absent_key, null_key = sentence_of(absent_said, repo), sentence_of(null_said, repo)
    assert absent_key != null_key, \
        f"two refusals quoting one path collapsed: {absent_key!r}"
    assert "null" in null_key
    # And the key really did drop the path, rather than keeping a piece of it.
    assert quoted not in null_key, f"the key still carries a path: {null_key!r}"


def refusals_under(root: Path) -> list[tuple[str, str]]:
    """EIGHT refusals spawned against a repository built under `root`, as
    `(what, key)`. Four quote a path - raw, or repr-doubled - and four quote
    none, so a key that leaks any part of a root shows up in half the rows.

    Every connector here is written by `declare` into the repository under
    `root`; the absent payload is a name under `root`. Nothing is shared with
    the other root, so the two sides differ in their paths and in nothing else.
    """
    repo = build_repo(root / "repo")
    declare(repo, "alert", stub("alert"))
    declare(repo, "n0", {})
    declare(repo, "n1", {"title": None})
    declare(repo, "nt", {"title": "a.title"})
    rows = [
        ("undeclared source", ["--source", "absent", "--payload", "-"], "{}"),
        ("payload file not there",
         ["--source", "alert", "--payload", str(root / "not-here.json")], ""),
        ("empty input", ["--source", "alert", "--payload", "-"], ""),
        ("a list", ["--source", "alert", "--payload", "-"], "[1, 2]"),
        ("mapping declares no title", ["--source", "n0", "--payload", "-"], "{}"),
        ("mapping declares title null", ["--source", "n1", "--payload", "-"], "{}"),
        ("payload path absent", ["--source", "nt", "--payload", "-"], '{"a": {}}'),
        ("payload path null", ["--source", "nt", "--payload", "-"],
         '{"a": {"title": null}}'),
    ]
    out = []
    for what, args, stdin_text in rows:
        proc, said = spawn_door(repo, *args, stdin_text=stdin_text)
        assert proc.returncode == 2, f"{what}: exit {proc.returncode}: {said!r}"
        out.append((what, sentence_of(said, repo, root)))
    return out


def test_the_sentence_key_is_the_same_from_a_root_with_spaces(tmp_path):
    """**BOTH DIRECTIONS, and the second is the one that was missing.**

    Direction one - different refusals give different keys - was already
    proven. Direction two - the SAME refusal gives the SAME key wherever the
    repository lives - was not, and it failed: a root containing a directory
    with spaces put that directory's words into three of eight keys. CLAUDE.md
    names directories with spaces as a Windows hazard that has bitten this
    project twice; a key that fails there is a key that fails on somebody's
    machine and not on CI's.

    The spaced directory is assembled at RUNTIME under this test's own temp
    directory, from words, so no path of any machine is written here."""
    plain_root = tmp_path / "plain"
    spaced_root = tmp_path / " ".join(("a", "dir", "with", "spaces"))
    plain = refusals_under(plain_root)
    spaced = refusals_under(spaced_root)

    # Direction one, from each root: eight refusals, eight different keys.
    for side in (plain, spaced):
        keys = [key for _what, key in side]
        assert len(set(keys)) == len(keys), f"refusals collapsed: {side}"

    # Direction two: the same refusal, the same key, from either root.
    for (what, plain_key), (_same, spaced_key) in zip(plain, spaced):
        assert plain_key == spaced_key, \
            f"{what} keyed differently from a spaced root: {plain_key!r} vs {spaced_key!r}"
    # And none of the spaced directory's words survived into any key.
    for what, key in spaced:
        assert "spaces" not in key.split(), f"{what} leaked the root: {key!r}"


# ── this commit's OWN two new refusals, SPAWNED ─────────────────────────────
#
# They were asserted in process only. A row whose entire subject is *a CLI's
# contract is a process* cannot land with its own new refusals proven by a
# function call - that is the defect it exists to remove, committed again one
# section later.


@pytest.mark.parametrize("what,mapping,stdin_text,said_words", [
    ("a mapping declaring title as null", {"title": None}, "{}",
     "declares title as null"),
    ("a payload carrying the declared path as null", {"title": "a.title"},
     '{"a": {"title": null}}', "and it is null"),
])
def test_the_new_null_refusals_are_spawned_exit_codes(repo, what, mapping,
                                                      stdin_text, said_words):
    """**SPAWNED.** Each new refusal reaches a person as an exit code and a
    sentence, and neither writes to the ledger on its way out."""
    declare(repo, "n", mapping)
    proc, said = spawn_door(repo, "--source", "n", "--payload", "-",
                            stdin_text=stdin_text)
    assert proc.returncode == 2, \
        f"could-not-look is exit 2; {what} got {proc.returncode} saying {said!r}"
    assert said_words in said, f"{what} did not say {said_words!r}: {said!r}"
    assert "Traceback" not in said, f"{what} handed over a traceback: {said!r}"
    assert list(ledger.list_runs(repo)) == [], f"{what} wrote to the ledger"


def test_a_usage_error_is_refused_by_argparse_rather_than_by_the_door(repo):
    """**The documented code nothing asserted.** `cli.door` states that
    `SystemExit` passes straight through "so argparse keeps its own exit 2 for a
    usage error" - and for this door nothing had ever spawned one, so the claim
    was prose.

    **The VALUE is deliberately not pinned here.** `connect.py` hands `door` a
    `failed` of 2, so a usage error and a payload nobody could read are currently
    indistinguishable - which is a separately tracked row, not this one. Asserting
    `== 2` would weld that collision in place and make fixing it look like a
    regression. What is asserted is what `cli.door`'s docstring actually claims:
    argparse answered, the door did not, and it is not a success.

    **THE BOUND, written down rather than chased.** While this door's `failed` is
    2, the assertion has an EQUIVALENT MUTANT: a `door` that caught `SystemExit`
    and re-emitted its code is byte-for-byte indistinguishable here from one that
    lets it pass through, because both produce `usage:` on stderr and the same
    non-zero code. No test at this door can separate them, and no cleverness in
    this file will change that - the two behaviours have collapsed onto one
    observation, not onto one assertion.

    **What closes it: T231.** The moment that row moves this door's `failed` off
    2, a swallowed-and-re-emitted `SystemExit` returns the NEW code while a
    passed-through one still returns argparse's 2, and this test becomes killable
    without a line of it changing. Whoever lands T231 should add `!= <the new
    failed code>` here and watch that mutant die; until then the survivor is
    recorded, not hidden."""
    proc, said = spawn_door(repo, "--source", "alert", "--payload", "-",
                            "--no-such-flag")
    assert proc.returncode != 0, f"a usage error succeeded: {said!r}"
    assert "usage:" in said, f"argparse did not answer: {said!r}"
    assert "did not finish" not in said, \
        f"the door swallowed a usage error into could-not-look: {said!r}"


# ── a run nobody could read is could-not-look, never a clean miss ───────────
#
# `state.list_runs` makes an unreadable run a ROW carrying its reason, and
# `intake.duplicates` forwards it saying "whether this specification repeats it
# is unknown". `open_runs_for` is the consumer of that row, and the fact has to
# survive the last step or the care upstream buys nothing: answering "no open
# run" for a run nobody could read files a SECOND run for an item that may
# already have one, into a ledger that is already damaged.


def corrupt_state(repo: Path, run_id: str, *, absent: bool = False) -> Path:
    """Make one real run's `state.json` unreadable. Returns the path."""
    path = ledger.run_dir(repo, run_id) / ledger.STATE_NAME
    assert path.is_file(), "the fixture did not produce a real run folder"
    if absent:
        path.unlink()
    else:
        path.write_text("{not json at all", encoding="utf-8")
    return path


def test_an_unreadable_run_is_could_not_look_rather_than_no_open_run(repo):
    """**The refusal, with its own control beside it.** The same call over the
    same ledger answers the run id when the state is readable and refuses when it
    is not - so the refusal cannot be mistaken for the policy's normal answer,
    and the test cannot pass by refusing everything."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title, spec = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])

    assert C.open_runs_for(repo, title, spec) == [rec["run_id"]]   # the control

    path = corrupt_state(repo, rec["run_id"])
    with pytest.raises(C.ConnectError) as exc:
        C.open_runs_for(repo, title, spec)
    said = str(exc.value)
    assert rec["run_id"] in said, "the refusal does not name the run it could not read"
    assert "could not be read" in said, \
        "the refusal coins a new word for could-not-look"
    assert "unknown" in said, \
        "the refusal does not say that whether this repeats is UNKNOWN"

    path.write_text(json.dumps({"run_id": rec["run_id"], "status": "running",
                                "phase": "P1", "history": []}), encoding="utf-8")
    assert C.open_runs_for(repo, title, spec) == [rec["run_id"]]


def test_a_run_with_no_state_file_is_the_same_could_not_look(repo):
    """A run folder with an intake and no `state.json` is damage too - and it
    reached `open_runs_for` by the other arm of the same `except`."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title, spec = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])
    corrupt_state(repo, rec["run_id"], absent=True)
    with pytest.raises(C.ConnectError) as exc:
        C.open_runs_for(repo, title, spec)
    assert rec["run_id"] in str(exc.value)
    assert "could not be read" in str(exc.value)


def test_the_door_refuses_an_unreadable_ledger_rather_than_filing_again(repo, tmp_path):
    """**SPAWNED, because a refusal's contract is an exit code a person sees.**
    An in-process call cannot tell a refusal from a traceback, and this is the
    one branch where the difference is a second run in somebody's ledger.

    Both doors are checked: `--file` must not write, and the read-only document
    must not claim `would_file` when whether it would is precisely what nobody
    could establish."""
    declare(repo, "alert", stub("alert"))
    payload = tmp_path / "alert.json"             # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    args = ["--target", str(repo), "--source", "alert", "--payload", str(payload)]

    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    corrupt_state(repo, first["run_id"])
    before = sorted(p.name for p in ledger.runs_dir(repo).iterdir() if p.is_dir())

    for extra in ([], ["--json"]):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "sdlc" / "connect.py"),
             *args, "--file", "--allow-dirty", *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180)
        said = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 2, \
            f"could-not-look is exit 2; got {proc.returncode} saying {said!r}"
        assert first["run_id"] in said, said
        assert "could not be read" in said, said
        assert "filed" not in said, f"it filed over a ledger it could not read: {said!r}"

    after = sorted(p.name for p in ledger.runs_dir(repo).iterdir() if p.is_dir())
    assert after == before, f"a second run was opened: {set(after) - set(before)}"

    # And the read-only door: no document claiming it would file.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "connect.py"), *args, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    said = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 2, f"got {proc.returncode} saying {said!r}"
    assert "would_file" not in said, \
        f"it answered whether it would file over a ledger it could not read: {said!r}"


# ── could-not-look, SCOPED to the item nobody could answer about ────────────
#
# Refusing on an unreadable state was right and UNSCOPED. `intake.duplicates`
# appended such a run to the candidate list before comparing anything, so ONE
# damaged folder refused intake for every unrelated item, from every connector,
# permanently - which is the outage that function's own docstring records item
# 158 removing. Measured before these were written: with one run's `state.json`
# unparsable, an unrelated alert exited 2 and opened no run.
#
# The two files fail independently, which is what makes scoping honest rather
# than merely convenient: `00-intake.json` is written ONCE, at P0, by the single
# site that creates a run, and `state.json` is rewritten on every phase advance,
# so it is the one a partial write leaves broken. Compare the specification
# anyway, and could-not-look fires only where nobody can tell.

#: A different item entirely - different fingerprint, different alert, different
#: specification. Written out rather than derived from PAYLOADS["alert"] so that
#: "unrelated" is visible in the file rather than asserted in a docstring.
ALERT_B = {"fingerprint": "zzz9", "generatorURL": "https://example.invalid/h",
           "labels": {"alertname": "DiskAlmostFull"},
           "annotations": {"description": "A different alert, a different disk."}}


def file_via_door(repo: Path, payload: Path, *extra: str):
    """Spawn the real door. A refusal's contract is an exit code a person sees,
    and an in-process call cannot tell one from a traceback."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "connect.py"),
         "--target", str(repo), "--source", "alert", "--payload", str(payload),
         *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def run_dirs(repo: Path) -> list[str]:
    return sorted(p.name for p in ledger.runs_dir(repo).iterdir() if p.is_dir())


def test_an_unrelated_item_still_files_while_another_run_is_unreadable(repo, tmp_path):
    """**SPAWNED. The blast radius, which is the whole of the scoping question.**

    One run's `state.json` is unparsable. A different item has nothing to do with
    it and must file normally, with a run folder to show for it. Anything else
    makes one damaged folder a permanent outage of every connector - so this is
    the control that stops the refusal beside it from being a refusal of
    everything."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    corrupt_state(repo, first["run_id"])
    before = run_dirs(repo)

    payload = tmp_path / "other.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(ALERT_B), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    assert proc.returncode == 0, \
        f"a damaged run refused an UNRELATED item: exit {proc.returncode}: {said!r}"
    assert "filed" in said, said
    after = run_dirs(repo)
    assert len(after) == len(before) + 1, \
        f"the unrelated item opened no run: {before} -> {after}"


def test_a_run_with_no_readable_specification_either_still_refuses(repo, tmp_path):
    """**SPAWNED. The boundary: scoping must not quietly become "never refuse".**

    When `state.json` AND `00-intake.json` are both unreadable, nothing about
    that run is knowable - not even whether it is this item - so refusing is the
    honest answer, and an unrelated item is refused along with it. This is the
    one case where the total blast radius is the correct one."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    corrupt_state(repo, first["run_id"])
    (ledger.run_dir(repo, first["run_id"]) / entry.INTAKE_JSON).write_text(
        "{not json either", encoding="utf-8")
    before = run_dirs(repo)

    payload = tmp_path / "other.json"
    payload.write_text(json.dumps(ALERT_B), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    assert proc.returncode == 2, \
        f"could-not-look is exit 2; got {proc.returncode} saying {said!r}"
    assert first["run_id"] in said, said
    assert "could not be read" in said, said
    assert "filed" not in said, f"it filed over a ledger nobody could read: {said!r}"
    assert run_dirs(repo) == before, "a run was opened over an unreadable ledger"


def test_duplicates_carries_the_unreadable_reason_rather_than_hiding_it(repo):
    """**The fact is CARRIED, not re-derived.**

    `state.list_runs` already decides a run's record is unreadable and records
    why. `duplicates` forwards that on the row under the same key, so
    `open_runs_for` reads it instead of loading the state a second time to
    rediscover the same failure - which is what it was doing, and why the
    unknown could not be scoped where the comparison lives.

    The scope is asserted in the same call, over one ledger: the row survives
    for the specification the damaged run might be, and is absent for one it
    demonstrably is not."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title, spec = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])
    corrupt_state(repo, rec["run_id"])

    same = entry.duplicates(repo, entry.spec_hash(title, spec), title)
    assert [r["run_id"] for r in same] == [rec["run_id"]], same
    assert same[0].get("unreadable"), \
        f"the reason list_runs recorded is not carried on the row: {same[0]}"
    assert "unknown" in same[0]["why"], same[0]
    #: WHICH arm matched, not merely that one did. Both arms of the unreadable
    #: branch end their explanation "is unknown", so that word alone cannot tell
    #: them apart - and a digest read off the ROW instead of the specification
    #: document is always `None`, which falls through to the title arm and
    #: survives every other test in this file. Naming the arm is what kills it.
    assert "same specification" in same[0]["why"], \
        ("the digest arm did not fire, so this run was matched some other way "
         f"and the specification comparison is unpinned: {same[0]}")

    other_title, other_spec = C.spec_for("alert", mapping, ALERT_B)
    assert entry.duplicates(repo, entry.spec_hash(other_title, other_spec),
                            other_title) == [], \
        ("an unreadable run whose specification is demonstrably a DIFFERENT one "
         "is still a candidate, so could-not-look is not scoped")


def test_the_unreadable_run_is_matched_on_its_own_specification_not_its_row(
        repo, tmp_path):
    """**The OTHER scoping key, and a VALID-but-wrong substitution to pin it.**

    `list_runs` puts the REASON in an unreadable row's `title` field, so the
    title compared here has to come from the specification document rather than
    from the row. Both dicts exist, both carry a `title`, both hold a string -
    so reading the wrong one breaks no type, no shape and no spelling, and a
    control that substitutes a DIFFERENT ITEM cannot see it: that item misses on
    the digest and is dropped before the title is consulted at all.

    So this pins the arm the digest cannot reach - same title, a specification
    digest that is valid and is not this one, state unreadable - where reading
    the row's title instead silently stops the run being a candidate and files a
    second run for an item that already has one.
    """
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title, spec = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])

    doc = ledger.run_dir(repo, rec["run_id"]) / entry.INTAKE_JSON
    body = json.loads(doc.read_text(encoding="utf-8"))
    assert body["title"] == title, "the fixture is not this item's own record"
    #: A digest of the EXACT shape `spec_hash` returns - its `sha256:` prefix and
    #: 64 hex characters - which is valid and is not this one, so the digest arm
    #: cannot fire and the title is the only thing left that can match. Built from
    #: the prefix rather than from `len(...)` of the real value on purpose: a run
    #: of zeros the same LENGTH carries no prefix, so it is the wrong SHAPE, and a
    #: control that makes the input invalid proves only that valid is required.
    body["spec_sha256"] = "sha256:" + "0" * 64
    doc.write_text(json.dumps(body), encoding="utf-8")
    corrupt_state(repo, rec["run_id"])

    found = entry.duplicates(repo, entry.spec_hash(title, spec), title)
    assert [r["run_id"] for r in found] == [rec["run_id"]], \
        ("an unreadable run whose SPECIFICATION carries this title is not a "
         f"candidate, so the title was read off the row: {found}")
    assert "same title" in found[0]["why"], found[0]

    payload = tmp_path / "same.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    before = run_dirs(repo)
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")
    assert proc.returncode == 2, \
        f"could-not-look is exit 2; got {proc.returncode} saying {said!r}"
    assert rec["run_id"] in said, said
    assert run_dirs(repo) == before, "a second run was opened for the same item"


def test_the_bootstrap_guide_documents_the_seam():
    """The guide is item 56's other half. A guide that describes a mechanism it
    does not name sends somebody to a command that does not exist."""
    body = (ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8")
    assert "connect.py" in body
    assert "--source" in body and "--payload" in body and "--file" in body
    for name in PAYLOADS:
        assert f"{name}.json" in body, f"the guide does not name the {name} stub"
    assert "--single-component" in body, "the monorepo refusal is not documented"
