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
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import connect as C  # noqa: E402
from tools.sdlc import debt as D  # noqa: E402
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
    r = tmp_path / "repo"
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


@pytest.mark.parametrize("path,want", [
    ("a", {"b": [10, 11]}),
    ("a.b", [10, 11]),
    ("a.b.1", 11),
    ("a.b.9", None),
    ("a.missing", None),
    ("missing.entirely", None),
])
def test_a_dotted_path_reaches_what_is_there_and_none_otherwise(path, want):
    """**None means the path was not there**, which a caller must not confuse with a
    field that was present and empty — a distinction this project has had to make in
    four other readers."""
    assert C.dig({"a": {"b": [10, 11]}}, path) == want


def test_a_present_but_empty_title_is_refused_like_an_absent_one(repo):
    """Present-and-blank and absent are different facts and the SAME repair here:
    nobody can find a run with no title, so neither files."""
    declare(repo, "x", {"title": "t"})
    with pytest.raises(C.ConnectError):
        C.spec_for("x", C.mapping_for(repo, "x"), {"t": "   "})


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


def test_the_door_reports_a_refusal_rather_than_a_traceback(repo, capsys):
    """Exit 2 is could-not-look, matching the other intake-side CLIs."""
    assert C.main(["--target", str(repo), "--source", "absent",
                   "--payload", "-"]) == 2


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


def test_the_bootstrap_guide_documents_the_seam():
    """The guide is item 56's other half. A guide that describes a mechanism it
    does not name sends somebody to a command that does not exist."""
    body = (ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8")
    assert "connect.py" in body
    assert "--source" in body and "--payload" in body and "--file" in body
    for name in PAYLOADS:
        assert f"{name}.json" in body, f"the guide does not name the {name} stub"
    assert "--single-component" in body, "the monorepo refusal is not documented"
