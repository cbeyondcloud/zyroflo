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
from pathlib import Path, PurePath, PureWindowsPath

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
#: **Ten, MEASURED 2026-09-16 at "connect: ten refusals where the constant
#: claimed four, and a path in prose"** by spawning fifteen command lines:
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
#: **Row 1's conflation: T230 fixed the absent-`--target` arm of it.** An
#: undeclared source, a `--target` that does not exist, and a connector path that
#: is a DIRECTORY all reached `path.is_file()` being false and got this same
#: "declares no connector" sentence, and two of the three were not "you declared
#: no connector" at all. T230 now checks the target FIRST and names it, so this
#: row (source absent, target PRESENT) is the legitimate case and says what it
#: means - see test_an_absent_target_names_the_target_not_the_connector. A
#: connector PATH that is a directory is still conflated; filed for the leader.
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
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; {what} got {proc.returncode} saying {said!r}"
    assert said_words in said, f"{what} did not say {said_words!r}: {said!r}"
    assert list(ledger.list_runs(repo)) == [], f"{what} wrote to the ledger"


def test_an_absent_target_names_the_target_not_the_connector(tmp_path):
    """**SPAWNED. The absent-`--target` arm of row 1's conflation (T230).** A
    `--target` that does not exist used to reach `mapping_for` and be reported as
    "declares no connector" - sending an adopter to write a mapping into a
    repository that is not there - while the sibling doors answered an absent
    target as a measured zero. connect now checks the target FIRST: it names the
    absent target and does NOT blame the connector, and exits could-not-look (3).

    A VALID payload is supplied on purpose: without the check, connect reads it
    and then reaches the old connector sentence, which is the red this closes -
    not a payload refusal that would pass whether or not the target was seen."""
    payload = tmp_path / "item.json"
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    absent = tmp_path / "no-such-repo"                   # never created
    proc, said = spawn_door(absent, "--source", "alert", "--payload", str(payload))
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert absent.name in said, f"the door did not name the absent target: {said!r}"
    assert "declares no connector" not in said, \
        f"an absent target was blamed on the connector: {said!r}"


#: Anything that looks like a filesystem path, so a SENTENCE can be compared
#: without the path it quotes. A drive letter, a UNC prefix, a POSIX absolute
#: path, or any run containing a separator.
PATHLIKE = re.compile(r"'[^']*'|\"[^\"]*\"|\S*[\\/]\S*|[A-Za-z]:\S*")


def sentence_of(said: str, *roots: PurePath) -> str:
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
    assert absent_proc.returncode == 3 and null_proc.returncode == 3
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
        assert proc.returncode == 3, f"{what}: exit {proc.returncode}: {said!r}"
        out.append((what, sentence_of(said, repo, root)))
    return out


def test_the_key_removes_a_doubled_backslash_root_outside_quotes():
    """**The case the doubled spelling exists for, which the door never produces.**

    `sentence_of` removes each root as written AND as a repr doubles its
    backslashes. Every doubled path this door prints today is an `OSError`
    filename inside single quotes, which the pattern removes whole anyway - so
    no spawned refusal can fail without the doubled spelling, and it was
    recorded EQUIVALENT on that argument. **An argument about text a parser
    consumes is not an equivalence until somebody has tried to break it**: the
    next refusal added to `connect.py` may print a doubled path unquoted, and
    nobody adding it will know this key depends on the quotes.

    So the case is built here directly: a doubled-backslash root OUTSIDE any
    quotes, whose middle directory has spaces - both hazards in one string. The
    root is a `PureWindowsPath`, so it carries backslashes on every platform
    and the case is real on CI's Linux as well; every backslash is built from
    its code point and no part of it is a drive, a share or a home directory."""
    one = chr(92)
    two = one * 2
    words = " ".join(("quite", "spaced", "folder"))
    root = PureWindowsPath("top", words, "repo")
    sentence = ("declares no title - a run with no title is one nobody can find "
                "again, so this is refused rather than filed under a generated name")
    tail = two.join(("", ".sdlc", "connectors", "n.json"))
    doubled = f"!! ConnectError: {two.join(root.parts)}{tail} {sentence}\n"
    raw = f"!! ConnectError: {one.join(root.parts)}{tail.replace(two, one)} {sentence}\n"

    assert "'" not in doubled and '"' not in doubled, \
        "the synthetic case must carry its path OUTSIDE quotes, or it tests nothing"
    assert two.join(root.parts) in doubled and str(root) not in doubled, \
        "the synthetic case must carry ONLY the doubled spelling"

    assert sentence_of(doubled, root) == sentence, \
        f"a doubled root outside quotes leaked into the key: {sentence_of(doubled, root)!r}"
    # The control: the same refusal quoting the root as WRITTEN keys the same.
    assert sentence_of(raw, root) == sentence, sentence_of(raw, root)


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
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; {what} got {proc.returncode} saying {said!r}"
    assert said_words in said, f"{what} did not say {said_words!r}: {said!r}"
    assert "Traceback" not in said, f"{what} handed over a traceback: {said!r}"
    assert list(ledger.list_runs(repo)) == [], f"{what} wrote to the ledger"


def test_a_usage_error_is_refused_by_argparse_rather_than_by_the_door(repo):
    """**The documented code nothing asserted.** `cli.door` states that
    `SystemExit` passes straight through "so argparse keeps its own exit 2 for a
    usage error" - and for this door nothing had ever spawned one, so the claim
    was prose.

    **The collision is gone: T231 moved this door's `failed` off argparse's 2.**
    `connect.py` used to hand `door` a `failed` of 2 - the very code argparse
    spends on a usage error - so a bad command line and a payload nobody could
    read were indistinguishable, and asserting `== 2` here would have welded that
    collision in place and made fixing it look like a regression. connect now
    hands `door` a 3, its siblings' could-not-look code, so the two are separable
    and this test pins the separation: argparse answered, the door did not, it is
    not a success, AND the code is not the door's own could-not-look 3.

    **The mutant this now kills.** While `failed` was 2 the assertion had an
    EQUIVALENT MUTANT: a `door` that CAUGHT `SystemExit` and re-emitted its code
    was byte-for-byte indistinguishable here from one that let it pass through -
    both produced `usage:` on stderr and the same non-zero code. With `failed` at
    3 a swallowed-and-re-emitted usage exit would return the door's 3 while a
    passed-through one still returns argparse's 2, so `!= 3` below separates them:
    the survivor T231's predecessor recorded is now killable, and killed here."""
    proc, said = spawn_door(repo, "--source", "alert", "--payload", "-",
                            "--no-such-flag")
    assert proc.returncode != 0, f"a usage error succeeded: {said!r}"
    assert proc.returncode != 3, (
        f"a usage error exits {proc.returncode}, the door's OWN could-not-look "
        f"code - T231 moved connect off argparse's 2 precisely so a caller can "
        f"tell a bad command line from a payload it could not read: {said!r}")
    assert "usage:" in said, f"argparse did not answer: {said!r}"
    assert "did not finish" not in said, \
        f"the door swallowed a usage error into could-not-look: {said!r}"


def _spawn_debt_could_not_look(target: Path):
    """A SIBLING door's could-not-look exit code, taken from the process.

    `debt.py` over an unreadable profile is the sibling's could-not-look branch:
    `candidates` -> `policy.components` raises `ProfileError`, the door catches it
    and spends its own could-not-look code. Spawned, because a CLI's contract is
    a process; the NBSP indent is `policy`'s own refusal, spelled as an escape
    (rule 1.18) rather than typed as the character."""
    (target / ".sdlc").mkdir(parents=True)
    (target / ".sdlc" / "profile.yml").write_text(
        "components:\n\u00a0- name: a\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "debt.py"),
         "--target", str(target), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_could_not_look_shares_the_sibling_code_and_is_not_argparses(repo, tmp_path):
    """**SPAWNED, and held against a sibling by MEASUREMENT, not a typed 3 (T231).**

    `connect.py` handed `cli.door` a `failed` of 2 - the code argparse already
    spends on a usage error - while its siblings (`debt.py`, `learning.py`,
    `metrics.py`) spend 3, so a caller could not tell a bad command line from a
    payload it could not read. This takes all three codes FROM the processes: a
    connector could-not-look, a sibling could-not-look, and argparse's own. It
    asserts the two could-not-looks AGREE and that neither is argparse's, so the
    fix is pinned to what the doors actually do rather than to a literal that
    would drift the day a sibling's code moved."""
    argparse_proc, _ = spawn_door(repo, "--source", "alert", "--payload", "-",
                                  "--no-such-flag")
    connect_proc, connect_said = spawn_door(
        repo, "--source", "alert", "--payload", "-", stdin_text="not json here")
    connect_cnl = connect_proc.returncode
    sibling_cnl, sibling_said = _spawn_debt_could_not_look(tmp_path / "sibling")

    assert connect_cnl == sibling_cnl, (
        f"connect could-not-look exits {connect_cnl}; the sibling door exits "
        f"{sibling_cnl} - the connector must answer could-not-look with the same "
        f"code as its siblings. connect said {connect_said!r}; "
        f"sibling said {sibling_said!r}")
    assert connect_cnl != argparse_proc.returncode, (
        f"connect could-not-look exits {connect_cnl}, the same code argparse "
        f"spends on a usage error ({argparse_proc.returncode}) - a caller cannot "
        f"tell a typo from a payload it could not read: {connect_said!r}")


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
        assert proc.returncode == 3, \
            f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
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
    assert proc.returncode == 3, f"got {proc.returncode} saying {said!r}"
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

    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
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
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert rec["run_id"] in said, said
    assert run_dirs(repo) == before, "a second run was opened for the same item"


# ── a specification that parses to the WRONG SHAPE is damage, not a mapping ──
#
# The fourth arm of the rows above: not readable-and-matching, not unreadable,
# not absent - valid JSON, and not what the reader thinks. `duplicates` called
# `.get` on whatever `00-intake.json` parsed to. Measured before these were
# written: a list beside an UNRELATED item made P0 traceback at exit 1 and the
# connector answer "did not finish" at exit 2 for every item, and a mapping
# without the two fields compared, beside an unreadable state, filed a SECOND
# run for the item it held. Item 152 left exactly this residual; item 117's
# rule is the answer - refused, never reinterpreted - and `state.read_state`
# already refuses the same shape in the file beside it.


def plant_specification(repo: Path, run_id: str, text: str) -> Path:
    """Overwrite one real run's `00-intake.json` with `text`."""
    doc = ledger.run_dir(repo, run_id) / entry.INTAKE_JSON
    assert doc.is_file(), "the fixture did not produce a real specification"
    doc.write_text(text, encoding="utf-8")
    return doc


#: Valid JSON every one, and none a specification record; the last is the
#: unparsable document, which the healthy-state arm now scopes the same way.
@pytest.mark.parametrize("text", ["[]", '"a string"', "7", "null", "{}", "{not json"])
def test_an_unrelated_item_files_beside_a_damaged_specification(repo, tmp_path, text):
    """**SPAWNED. The property T329 fought for, with the state HEALTHY.**

    The damaged run's `state.json` still carries its title, and that title is
    not this item's - so it demonstrably holds something else, and one bad
    document must not refuse, or answer "already open" for, every other item."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, first["run_id"], text)
    before = run_dirs(repo)

    payload = tmp_path / "other.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(ALERT_B), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    assert "did not finish" not in said, f"the door was not answered: {said!r}"
    assert proc.returncode == 0, \
        f"a damaged specification refused an UNRELATED item: exit {proc.returncode}: {said!r}"
    assert "filed" in said and "already open" not in said, said
    assert len(run_dirs(repo)) == len(before) + 1, "the unrelated item opened no run"


@pytest.mark.parametrize("text", ["[]", "{}"])
def test_the_item_a_wrong_shape_specification_might_hold_is_refused(repo, tmp_path, text):
    """**SPAWNED. The quiet half.** State unreadable AND a specification that is
    no record: nothing says which item this run holds. `{}` used to be read as a
    DIFFERENT specification and file a second run for the same item; `[]` used
    to leave through the door as an AttributeError."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, first["run_id"], text)
    corrupt_state(repo, first["run_id"])
    before = run_dirs(repo)

    payload = tmp_path / "same.json"           # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    #: Not "did not finish": `cli.door` prints every `ConnectError` refusal under
    #: that prefix, the right one included. What leaked was the TYPE.
    assert "AttributeError" not in said, f"a traceback, not a refusal: {said!r}"
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert first["run_id"] in said and "could not be read" in said, said
    assert run_dirs(repo) == before, "a second run was opened for the same item"


@pytest.mark.parametrize("text", ["[]", '"a string"', "7", "null", "{}"])
def test_a_wrong_shape_specification_is_compared_on_the_title_its_state_carries(
        repo, text):
    """The scope, in one call over one ledger: the damaged run IS a candidate for
    its own title - named, with the document that could not be read - and is
    NOT one for a different item. Reading the title off the damaged document
    instead finds nothing; keeping every damaged run finds the unrelated one."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title, spec = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, rec["run_id"], text)

    same = entry.duplicates(repo, entry.spec_hash(title, spec), title)
    assert [r["run_id"] for r in same] == [rec["run_id"]], same
    assert "same title" in same[0]["why"] and entry.INTAKE_JSON in same[0]["why"], \
        f"the candidate does not name the document it could not read: {same[0]}"

    other_title, other_spec = C.spec_for("alert", mapping, ALERT_B)
    assert entry.duplicates(repo, entry.spec_hash(other_title, other_spec),
                            other_title) == [], \
        "a run whose state carries a DIFFERENT title is still a candidate"


def test_a_wrong_shape_specification_with_no_state_either_is_refused(repo, tmp_path):
    """**SPAWNED. Nothing carries the title.** With no `state.json`, `list_runs`
    gives the row a placeholder title rather than the run's own, so comparing on
    it would call the run a different item and file this one a second time. The
    run stays a candidate, and the door refuses it by name."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, first["run_id"], "[]")
    corrupt_state(repo, first["run_id"], absent=True)
    before = run_dirs(repo)

    payload = tmp_path / "same.json"           # a webhook payload, not a repo file
    payload.write_text(json.dumps(PAYLOADS["alert"]), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    assert "AttributeError" not in said, f"a traceback, not a refusal: {said!r}"
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert first["run_id"] in said and "could not be read" in said, said
    assert run_dirs(repo) == before, "a second run was opened for the same item"


# ── one rule, per field: usable only as a non-empty string ──────────────────
#
# Review of the two commits above measured two regressions against efd7376, by
# spawning: a state that PARSES with no usable title had a stand-in compared as
# its title and filed a second run for the same item; and a document with one
# damaged field lost the good one too, so an unrelated item beside an unreadable
# state was refused - T329's outage, back for those shapes. Each field is now
# decided alone, and a run is ruled OUT only on a usable title.

#: Removes the key, rather than writing a value into it.
ABSENT_KEY = object()


def edit_json(path: Path, key: str, value) -> None:
    """Set or remove one top-level key of a real run file, keeping the rest."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if value is ABSENT_KEY:
        body.pop(key, None)
    else:
        body[key] = value
    path.write_text(json.dumps(body), encoding="utf-8")


def door_in(repo: Path, tmp_path: Path, item: dict):
    payload = tmp_path / "item.json"           # a webhook payload, not a repo file
    payload.write_text(json.dumps(item), encoding="utf-8")
    return file_via_door(repo, payload, "--file", "--allow-dirty")


@pytest.mark.parametrize("state_title, text", [
    (ABSENT_KEY, "[]"), ("", "{not json"), ("   ", "[]"), (None, "[]"), (5, "[]")],
    ids=["no-title-list", "empty-title-unparsable", "blank-title-list",
         "null-title-list", "number-title-list"])
def test_a_state_with_no_usable_title_does_not_file_its_own_item_again(
        repo, tmp_path, state_title, text):
    """**SPAWNED. R1.** The state parses and its title is not a non-empty string,
    and the specification is damaged: nothing can rule this run out, so it stays
    a candidate. `list_runs`' stand-in title used to be compared as the run's
    own and filed a second run; a null or a number was an AttributeError."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    edit_json(ledger.run_dir(repo, first["run_id"]) / ledger.STATE_NAME, "title",
              state_title)
    plant_specification(repo, first["run_id"], text)
    before = run_dirs(repo)

    proc, said = door_in(repo, tmp_path, PAYLOADS["alert"])

    assert "AttributeError" not in said, f"a traceback, not an answer: {said!r}"
    assert run_dirs(repo) == before, f"a second run was opened for the same item: {said!r}"
    assert proc.returncode == 0 and "already open" in said and first["run_id"] in said, \
        f"exit {proc.returncode}: {said!r}"


@pytest.mark.parametrize("item, files", [("unrelated", True), ("same", False)])
def test_a_null_state_title_falls_back_to_the_specification_title(
        repo, tmp_path, item, files):
    """**SPAWNED.** A null state title beside an INTACT specification crashed at
    every sha. The specification's title is usable, so it decides: an unrelated
    item files and the run's own item is already open."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    edit_json(ledger.run_dir(repo, first["run_id"]) / ledger.STATE_NAME, "title", None)
    before = run_dirs(repo)

    proc, said = door_in(repo, tmp_path, ALERT_B if item == "unrelated" else PAYLOADS["alert"])

    assert "AttributeError" not in said, f"a traceback, not an answer: {said!r}"
    assert proc.returncode == 0, f"exit {proc.returncode}: {said!r}"
    assert len(run_dirs(repo)) == len(before) + (1 if files else 0), said
    assert ("filed" if files else "already open") in said, said


@pytest.mark.parametrize("digest", [ABSENT_KEY, 5, ""], ids=["no-digest", "number-digest",
                                                          "empty-digest"])
def test_a_usable_title_survives_a_damaged_digest_beside_an_unreadable_state(
        repo, tmp_path, digest):
    """**SPAWNED. R2, and T329's property with the state UNREADABLE.** The title
    is still usable, so it rules this run out for a different item - which must
    file - and keeps it for its own, which is refused by name."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    edit_json(ledger.run_dir(repo, first["run_id"]) / entry.INTAKE_JSON, "spec_sha256",
              digest)
    corrupt_state(repo, first["run_id"])
    before = run_dirs(repo)

    proc, said = door_in(repo, tmp_path, ALERT_B)
    assert proc.returncode == 0 and "filed" in said, \
        f"one damaged field refused an UNRELATED item: exit {proc.returncode}: {said!r}"
    assert len(run_dirs(repo)) == len(before) + 1, said

    before = run_dirs(repo)
    proc, said = door_in(repo, tmp_path, PAYLOADS["alert"])
    assert proc.returncode == 3, f"could-not-look is exit 3; got {proc.returncode}: {said!r}"
    assert first["run_id"] in said and "could not be read" in said, said
    assert run_dirs(repo) == before, "a second run was opened for the same item"


@pytest.mark.parametrize("fields", [{"title": "", "spec_sha256": ""},
                                    {"title": ABSENT_KEY}],
                         ids=["empty-strings", "no-title"])
def test_a_specification_with_no_usable_title_is_refused_beside_an_unreadable_state(
        repo, tmp_path, fields):
    """**SPAWNED.** Empty strings are strings and compare as nothing, so they
    read as a DIFFERENT item and filed a second run. And a usable digest cannot
    rule a run out alone - one title carries many bodies - so the item that
    shares only the TITLE is the one built here: at efd7376 it filed."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    doc = ledger.run_dir(repo, first["run_id"]) / entry.INTAKE_JSON
    for key, value in fields.items():
        edit_json(doc, key, value)
    corrupt_state(repo, first["run_id"])
    before = run_dirs(repo)

    same_title = {**PAYLOADS["alert"],
                  "annotations": {"description": "The same alert name, another body."}}
    mapping = C.mapping_for(repo, "alert")
    assert C.spec_for("alert", mapping, same_title)[0] == \
        C.spec_for("alert", mapping, PAYLOADS["alert"])[0], "not the same title"
    assert C.spec_for("alert", mapping, same_title)[1] != \
        C.spec_for("alert", mapping, PAYLOADS["alert"])[1], "not a different body"

    proc, said = door_in(repo, tmp_path, same_title)
    assert proc.returncode == 3, f"could-not-look is exit 3; got {proc.returncode}: {said!r}"
    assert first["run_id"] in said, said
    assert run_dirs(repo) == before, "a second run was opened for an item it may be"


# ── T357: a run's own two records of its identity must agree ────────────────
#
# `duplicates` matched on `spec_sha256 == digest` without ever asking whether
# `state_title` agreed - so a run's own `00-intake.json` could be overwritten
# with ANOTHER item's complete, valid record (right shape, a genuinely
# matching digest, nothing `specification_fields` calls damaged) and the door
# would trust the document over the state even when the state was perfectly
# healthy and said something else the whole time.


def plant_someone_elses_record(repo: Path, run_id: str, title: str, digest: str) -> None:
    """Overwrite one real run's `00-intake.json` with ANOTHER item's own
    title and spec_sha256 - right shape, nothing damaged, just not this run's."""
    doc = ledger.run_dir(repo, run_id) / entry.INTAKE_JSON
    body = json.loads(doc.read_text(encoding="utf-8"))
    body["title"] = title
    body["spec_sha256"] = digest
    doc.write_text(json.dumps(body), encoding="utf-8")


def test_a_planted_record_for_a_different_item_no_longer_answers_already_open(
        repo, tmp_path):
    """**SPAWNED. T357, the healthy-state arm.** Run A is genuinely item A - a
    healthy, untouched `state.json` says so throughout. Its `00-intake.json` is
    overwritten with item B's OWN complete, valid record. Before this fix,
    `spec.get("spec_sha256") == digest` fired on that alone, so item B's own
    first-ever submission answered `already open` against a run whose state
    never once agreed with the document doing the answering."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    title_a, _ = C.spec_for("alert", mapping, PAYLOADS["alert"])
    rec_a = C.file_one(repo, "alert", PAYLOADS["alert"])
    title_b, spec_b = C.spec_for("alert", mapping, ALERT_B)
    digest_b = entry.spec_hash(title_b, spec_b)

    plant_someone_elses_record(repo, rec_a["run_id"], title_b, digest_b)
    # the state is untouched and healthy: it names item A throughout
    assert ledger.load(repo, rec_a["run_id"])["title"] == title_a

    before = run_dirs(repo)
    payload = tmp_path / "item_b.json"          # a webhook payload, not a repo file
    payload.write_text(json.dumps(ALERT_B), encoding="utf-8")
    proc, said = file_via_door(repo, payload, "--file", "--allow-dirty")

    assert proc.returncode == 0, f"exit {proc.returncode}: {said!r}"
    assert "filed" in said and "already open" not in said, (
        f"item B's own first submission was matched against run A, whose "
        f"state names {title_a!r} the whole time: {said!r}")
    assert len(run_dirs(repo)) == len(before) + 1, \
        f"no run was opened for item B: {before} -> {run_dirs(repo)}"


def test_the_disagreement_is_named_through_intakes_own_door(repo, tmp_path):
    """**SPAWNED.** The same planted run, met through `intake.py` directly (the
    door `connect.py` and `debt.py` both call through, via `open_run`): a NOTE
    names the disagreement instead of staying silent about a run whose own two
    records no longer agree - and the run is not offered as a "possible
    duplicate" of the new, unrelated specification either."""
    declare(repo, "alert", stub("alert"))
    mapping = C.mapping_for(repo, "alert")
    rec_a = C.file_one(repo, "alert", PAYLOADS["alert"])
    title_b, spec_b = C.spec_for("alert", mapping, ALERT_B)
    digest_b = entry.spec_hash(title_b, spec_b)
    plant_someone_elses_record(repo, rec_a["run_id"], title_b, digest_b)

    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Add a health endpoint\n\nThe service should expose "
                         "/health returning 200 so the load balancer can probe "
                         "it.\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
         "--target", str(repo), "--spec", str(spec_file), "--allow-dirty"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    said = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"exit {proc.returncode}: {said!r}"
    assert rec_a["run_id"] in said and "disagrees with itself" in said, \
        f"no NOTE named the disagreement: {said!r}"
    assert "possible duplicate" not in said, \
        f"a self-contradictory run must not be trusted as a match either: {said!r}"


@pytest.mark.parametrize("text", ["{not json", "[]"])
def test_p0_still_warns_about_a_damaged_specification_it_ruled_out(repo, tmp_path, text):
    """**SPAWNED. T355.** Beside an unparsable specification P0 printed a note
    naming that run, attached to a false duplicate claim. The claim is gone; the
    warning must not go with it."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, first["run_id"], text)

    spec = tmp_path / "spec.md"                # a specification, not a repo file
    spec.write_text("# Add a health endpoint\n\nThe service should expose /health "
                    "returning 200 so the load balancer can probe it.\n",
                    encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
         "--target", str(repo), "--spec", str(spec), "--allow-dirty"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    said = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"exit {proc.returncode}: {said!r}"
    notes = [ln for ln in said.splitlines()
             if "NOTE" in ln and first["run_id"] in ln and entry.INTAKE_JSON in ln]
    assert notes, f"no note names the damaged specification: {said!r}"
    assert not any("possible duplicate" in ln for ln in notes), notes


def test_p0_does_not_traceback_beside_a_list_shaped_specification(repo, tmp_path):
    """**SPAWNED.** The intake door itself: `open_run` sweeps duplicates before
    it creates anything, so this was a Python traceback at exit 1 for a brand
    new, unrelated specification."""
    declare(repo, "alert", stub("alert"))
    first = C.file_one(repo, "alert", PAYLOADS["alert"])
    plant_specification(repo, first["run_id"], "[]")
    before = run_dirs(repo)

    spec = tmp_path / "spec.md"                # a specification, not a repo file
    spec.write_text("# Add a health endpoint\n\nThe service should expose /health "
                    "returning 200 so the load balancer can probe it.\n",
                    encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
         "--target", str(repo), "--spec", str(spec), "--allow-dirty"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    said = (proc.stdout or "") + (proc.stderr or "")
    assert "Traceback" not in said, said
    assert proc.returncode == 0, f"exit {proc.returncode}: {said!r}"
    assert len(run_dirs(repo)) == len(before) + 1, said


def test_the_bootstrap_guide_documents_the_seam():
    """The guide is item 56's other half. A guide that describes a mechanism it
    does not name sends somebody to a command that does not exist."""
    body = (ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8")
    assert "connect.py" in body
    assert "--source" in body and "--payload" in body and "--file" in body
    for name in PAYLOADS:
        assert f"{name}.json" in body, f"the guide does not name the {name} stub"
    assert "--single-component" in body, "the monorepo refusal is not documented"
