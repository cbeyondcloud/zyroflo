"""Tech debt, from close-out back into intake — item 66.

`P11-closeout.md` step 5 has said since item 36 that *"what the codebase got
wrong becomes a tech-debt intake, so it is a run somebody schedules rather than
a paragraph somebody remembers"*. **Nothing did it** — it was an instruction to
write something down by hand, which is the paragraph it meant to replace.

The properties worth more than the rest of this file:

**The debt was already declared three times over.** `known_failures`,
`sca_known_findings` and a deferred review finding were each built so a gate
could stay honest about something it was letting past. Letting something past is
what debt IS, so the register existed before anything asked it a question.

**Codebase debt, not harness learning, and the line is checkable.**
`review_suppress` means the RULE was noisy — a fact about the harness, item 50's
territory — and filing it here would put the harness's problems in an adopter's
backlog. A REJECTED finding is excluded from the other end: somebody decided it
was not real, and re-filing it would be a machine overruling a person.

**Deduped against OPEN runs.** A run that closed without the declaration going
away does NOT hold the item filed, because the debt outlived the attempt.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl  # noqa: E402
from tools.review import sca as SCA  # noqa: E402
from tools.sdlc import debt as D  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import learning as L  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


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


def declare(repo: Path, key: str, values: list[str]) -> None:
    p = repo / ".sdlc" / "profile.yml"
    rendered = ", ".join(f'"{v}"' for v in values)
    p.write_text(p.read_text(encoding="utf-8") + f"{key}: [{rendered}]\n",
                 encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "declare")


def reviewed(repo: Path, run_id: str, finding: str, disposition: str,
             started: str = "2026-08-29T01:00:00+00:00") -> None:
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": started,
            "findings": [{"id": finding, "disposition": disposition,
                          "severity": "low", "source": "agent",
                          "path": "a/b.py"}]}, sort_keys=True) + "\n")


def keys(items: list[dict]) -> list[str]:
    return [i["key"] for i in items]


# ── the register was already written, three times ───────────────────────────


def test_a_quarantined_test_is_debt(repo):
    declare(repo, "known_failures", ["test_a_thing_that_is_red"])
    got = D.candidates(repo)
    assert keys(got) == ["test_a_thing_that_is_red"]
    assert got[0]["source"] == "quarantined-test"


def test_an_accepted_vulnerability_is_debt(repo):
    entry_text = "PyPI requests 2.19.1 CVE-2018-18074"
    declare(repo, SCA.QUARANTINE_KEY, [entry_text])
    got = D.candidates(repo)
    assert keys(got) == [entry_text]
    assert got[0]["source"] == "accepted-vulnerability"


def test_a_deferred_finding_is_debt(repo):
    run_id = ledger.create(repo, "defect", "a run")["run_id"]
    reviewed(repo, run_id, "slow-query-in-the-loop", "deferred")
    got = D.candidates(repo)
    assert keys(got) == ["slow-query-in-the-loop"]
    assert got[0]["source"] == "deferred-finding"


def test_a_repository_that_declares_nothing_owes_nothing(repo):
    assert D.candidates(repo) == []


# ── the line between codebase debt and harness learning ─────────────────────


def test_a_rejected_finding_is_not_debt(repo):
    """Somebody decided it was not real. Re-filing it would be a machine
    overruling a person, which is why `hitl` records a disposition rather than
    a count."""
    run_id = ledger.create(repo, "defect", "a run")["run_id"]
    reviewed(repo, run_id, "a-finding-nobody-believed", "rejected")
    assert D.candidates(repo) == []


def test_an_accepted_finding_is_not_debt(repo):
    """Accepted means fixed in that run. Filing it would be debt for work
    already done."""
    run_id = ledger.create(repo, "defect", "a run")["run_id"]
    reviewed(repo, run_id, "a-finding-that-was-fixed", "accepted")
    assert D.candidates(repo) == []


def test_a_suppressed_rule_is_harness_learning_and_not_codebase_debt(repo):
    """**The distinction the task line is written about.** A suppression means
    the RULE was noisy - a fact about the harness, carried by item 50 through
    the harness's own gate. Filing it here would put the harness's problems in
    an adopter's backlog."""
    declare(repo, L.SUPPRESS_KEY, ["a-noisy-rule"])
    assert D.candidates(repo) == []


def test_the_three_sources_are_all_read_together(repo):
    run_id = ledger.create(repo, "defect", "a run")["run_id"]
    reviewed(repo, run_id, "deferred-thing", "deferred")
    declare(repo, "known_failures", ["test_red"])
    declare(repo, SCA.QUARANTINE_KEY, ["PyPI x 1.0 CVE-1"])
    got = D.candidates(repo)
    assert [i["source"] for i in got] == list(D.SOURCES)


# ── deduped against OPEN runs ───────────────────────────────────────────────


def test_filing_opens_a_real_run_through_intake(repo):
    """Not a private path: a debt item is a change like any other and takes the
    same gates. A second way to open a run would be a second answer to what a
    run IS."""
    declare(repo, "known_failures", ["test_red"])
    item = D.candidates(repo)[0]
    rec = D.file_one(repo, item)
    assert rec["kind"] == D.KIND
    state = ledger.load(repo, rec["run_id"])
    assert state["status"] not in ledger.TERMINAL
    assert (ledger.run_dir(repo, rec["run_id"]) / entry.INTAKE_JSON).is_file()


def test_a_recurring_smell_files_once(repo):
    """The task line, in one test."""
    declare(repo, "known_failures", ["test_red"])
    item = D.candidates(repo)[0]
    D.file_one(repo, item)
    assert D.outstanding(repo) == []
    with pytest.raises(D.DebtError) as exc:
        D.file_one(repo, item)
    assert "already has an open run" in str(exc.value)


def test_a_closed_run_does_not_hold_the_debt_filed(repo):
    """**The half that keeps the register honest.** If the declaration is still
    there after the run went away, the debt outlived the attempt - and a
    tracker that treats a closed run as payment is one that quietly forgets."""
    declare(repo, "known_failures", ["test_red"])
    item = D.candidates(repo)[0]
    rec = D.file_one(repo, item)
    assert D.outstanding(repo) == []
    ledger.advance(repo, rec["run_id"], status="closed", note="closed unfixed")
    assert keys(D.outstanding(repo)) == ["test_red"]


def test_paying_the_debt_removes_it_from_the_register(repo):
    """The other direction, and the reason the source is a DECLARATION rather
    than a note: remove the quarantine and the item is simply gone."""
    declare(repo, "known_failures", ["test_red"])
    assert D.candidates(repo)
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        'known_failures: ["test_red"]', "known_failures: []"), encoding="utf-8")
    assert D.candidates(repo) == []


def test_the_specification_is_deterministic(repo):
    """`intake.spec_hash` over this text is the identity the duplicate finder
    matches on. Anything varying per invocation - a timestamp, a run id - would
    make the same smell a new item every time it was looked at."""
    declare(repo, "known_failures", ["test_red"])
    item = D.candidates(repo)[0]
    assert D.spec_for(item) == D.spec_for(item)
    assert entry.spec_hash(item["title"], D.spec_for(item)) == \
        entry.spec_hash(item["title"], D.spec_for(item))


def test_two_different_debts_are_two_items(repo):
    declare(repo, "known_failures", ["test_one", "test_two"])
    assert sorted(keys(D.candidates(repo))) == ["test_one", "test_two"]
    first, second = D.candidates(repo)
    assert (entry.spec_hash(first["title"], D.spec_for(first))
            != entry.spec_hash(second["title"], D.spec_for(second)))


# ── could-not-look is not "no debt" ─────────────────────────────────────────


def test_an_unreadable_profile_refuses_rather_than_reporting_none(repo,
                                                                  monkeypatch):
    """A register that cannot be read is not an empty register. Reporting no
    debt would be the cheerful answer that hides the question."""
    def boom(_target):
        raise OSError("the profile could not be read")

    monkeypatch.setattr(D.guardrails, "components", boom)
    with pytest.raises(D.DebtError) as exc:
        D.candidates(repo)
    assert "could not be read" in str(exc.value)


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_writes_nothing_without_the_flag(repo, capsys):
    """Filing opens runs in somebody's repository. That is an act, and it takes
    a flag - the same rule `corpus_harvest --write` follows."""
    declare(repo, "known_failures", ["test_red"])
    before = len(ledger.list_runs(repo))
    assert D.main(["--target", str(repo)]) == 1
    assert len(ledger.list_runs(repo)) == before
    assert "nothing was written" in capsys.readouterr().out


def test_the_cli_files_and_says_what_it_opened(repo, capsys):
    declare(repo, "known_failures", ["test_red"])
    assert D.main(["--target", str(repo), "--file"]) == 0
    out = capsys.readouterr().out
    assert "filed " in out and "1 run(s) opened" in out
    assert D.outstanding(repo) == []


def test_the_cli_exits_zero_when_nothing_is_owed(repo, capsys):
    assert D.main(["--target", str(repo)]) == 0


def test_the_cli_reports_as_data(repo, capsys):
    declare(repo, "known_failures", ["test_red"])
    assert D.main(["--target", str(repo), "--json"]) == 1
    found = json.loads(capsys.readouterr().out)
    assert keys(found["outstanding"]) == ["test_red"]


# ── one item nobody can answer about must not answer for the rest ───────────
#
# `open_runs_for` refuses when a candidate run's state cannot be read. That is
# right, and `outstanding` swept every item, so the refusal escaped `_main`
# before the first item was reported - and the per-item handler three lines
# further down was unreachable for the whole class. One damaged run belonging to
# item X then answered for items Y and Z: exit 3, nothing filed, which is the
# same unscoped blast `intake.duplicates` was rebuilt to stop, one layer up.


def spawn_debt(repo: Path, *extra: str):
    """The real door. `3` is could-not-look and an in-process call cannot tell
    that from a traceback, which is the whole difference being asserted."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "debt.py"),
         "--target", str(repo), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def break_state(repo: Path, run_id: str) -> None:
    (ledger.run_dir(repo, run_id) / ledger.STATE_NAME).write_text(
        "{not json at all", encoding="utf-8")


def test_a_damaged_run_does_not_answer_for_an_unrelated_debt_item(repo):
    """**SPAWNED. The blast radius, which is the whole of this.**

    Item X has a run whose state is unreadable. Item Y is a different
    declaration with nothing to do with it, and must still be filed - with a run
    folder to show for it - or one damaged folder is a standing outage of the
    debt door. The exit code stays `3`, because something in the listing could
    not be looked at and a door that exited 0 would be saying it had."""
    declare(repo, "known_failures", ["test_x"])
    rec = D.file_one(repo, D.candidates(repo)[0])
    break_state(repo, rec["run_id"])
    declare(repo, "sca_known_findings", ["CVE-0000-0000"])
    before = len(ledger.list_runs(repo))

    proc, said = spawn_debt(repo, "--file", "--allow-dirty")
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert "CVE-0000-0000" in said, said
    assert rec["run_id"] in said, \
        f"the door did not say which run it could not read: {said!r}"
    assert len(ledger.list_runs(repo)) == before + 1, \
        f"a damaged run blocked an UNRELATED item: {said!r}"
    assert "1 run(s) opened" in said, said


def test_the_item_whose_own_run_is_damaged_is_still_refused(repo):
    """**SPAWNED, and the control that stops the fix becoming "file anyway".**

    The scoping must narrow the refusal, never remove it: the item whose own run
    cannot be read is exactly the one nobody can answer about, so it is named
    and NOT filed."""
    declare(repo, "known_failures", ["test_x"])
    rec = D.file_one(repo, D.candidates(repo)[0])
    break_state(repo, rec["run_id"])
    before = len(ledger.list_runs(repo))

    proc, said = spawn_debt(repo, "--file", "--allow-dirty")
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert "test_x" in said and rec["run_id"] in said, said
    assert "0 run(s) opened" in said, said
    assert len(ledger.list_runs(repo)) == before, \
        f"a second run was opened over a ledger nobody could read: {said!r}"


def test_the_read_only_report_names_the_unknown_rather_than_calling_it_owed(repo):
    """**SPAWNED. The quieter half of the same lie.**

    Before this, the read-only listing printed the damaged item as `with no open
    run` - which is the same claim the connector door was rebuilt to stop making,
    said by a door that writes nothing. It is carried under its own key instead:
    zero silent drops, and not misfiled as debt either."""
    declare(repo, "known_failures", ["test_x"])
    rec = D.file_one(repo, D.candidates(repo)[0])
    break_state(repo, rec["run_id"])

    proc, said = spawn_debt(repo, "--json")
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    found = json.loads(proc.stdout)
    assert keys(found["outstanding"]) == [], \
        f"an item nobody could answer about was reported as owed: {found}"
    assert [u["key"] for u in found["unknown"]] == ["test_x"], found
    assert rec["run_id"] in found["unknown"][0]["why"], found
