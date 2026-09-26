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
import os
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


# The probe the cross-process test spawns. It imports the module from the
# worktree root passed as argv[1] - so the interpreter under test is this tree's,
# not an installed copy - and prints, per candidate, BOTH keys
# `intake.duplicates` matches a redelivered signal on: `intake.py` matches either
# a `spec_sha256` OR a case-folded title, so the probe emits the
# `intake.spec_hash` identity AND the title as its own field, plus the full spec
# body - a change in candidates() order, in the spec text, or in the hash all
# show. Its first stdout line is the seed the child actually ran under.
_DETERMINISM_PROBE = '''\
import json
import os
import sys
from pathlib import Path

root, target = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))

from tools.sdlc import debt as D
from tools.sdlc import intake as entry

rows = []
for item in D.candidates(target):
    body = D.spec_for(item)
    rows.append({
        "key": item["key"],
        "component": item["component"],
        "title": item["title"],
        "spec": body,
        "identity": entry.spec_hash(item["title"], body),
    })
print(os.environ.get("PYTHONHASHSEED", ""))
sys.stdout.write(json.dumps(rows, sort_keys=True, ensure_ascii=True))
'''


def test_the_specification_is_deterministic_across_processes(repo, tmp_path):
    """**SPAWNED, because the sibling above cannot see it (T327).** That check
    calls `spec_for` twice in ONE interpreter, so an identity keyed on anything
    constant within a process but varying between them - a hash seed re-salting
    `hash(str)` and set iteration, a process id, a wall-clock read - passes it
    unseen. W211 measured exactly that: `spec_for` made to carry the process id
    left both of the sibling's assertions green.

    So each interpreter is started with a DIFFERENT `PYTHONHASHSEED`, serializes
    every candidate (title, spec body and `intake.spec_hash` identity), and the
    two serializations are compared byte-for-byte. Each child echoes back the
    seed it actually ran under and the test asserts those two differ, so the
    comparison is known to have exercised two hash seeds rather than agreeing by
    construction."""
    fails = ["red.A#one", "red.B#two", "red.C#three",
             "red.D#four", "red.E#five", "red.F#six"]
    vulns = ["PyPI aaa 1.0 CVE-1", "PyPI bbb 2.0 CVE-2"]
    declare(repo, "known_failures", fails)
    declare(repo, SCA.QUARANTINE_KEY, vulns)

    probe = tmp_path / "determinism_probe.py"
    probe.write_text(_DETERMINISM_PROBE, encoding="utf-8")

    seeds = ("0", "1")

    def serialized(seed: str) -> tuple[str, str]:
        proc = subprocess.run(
            [sys.executable, str(probe), str(ROOT), str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, env={**os.environ, "PYTHONHASHSEED": seed})
        assert proc.returncode == 0, \
            f"the probe under PYTHONHASHSEED={seed} exited {proc.returncode}: " \
            f"{proc.stderr!r}"
        ran, _, spec_json = proc.stdout.partition("\n")
        return ran, spec_json

    ran_a, spec_a = serialized(seeds[0])
    ran_b, spec_b = serialized(seeds[1])

    # The control, read back from the children rather than asserted of the
    # literals above: each child echoes the PYTHONHASHSEED it actually started
    # under. If the seed never reached a child, or both ran the same, the compare
    # below would collapse to "x == x" - so this is what keeps it honest.
    assert (ran_a, ran_b) == seeds, \
        f"a child did not run under the intended seed: got {(ran_a, ran_b)!r}, " \
        f"passed {seeds!r}"
    assert ran_a != ran_b, \
        f"both children ran under the same hash seed ({ran_a!r}); the spec " \
        f"compare below would then be blind to hash-seed variance"

    rows = json.loads(spec_a)
    assert len(rows) == len(fails) + len(vulns), \
        f"nothing meaningful to compare across processes: {rows}"
    assert {r["key"] for r in rows} == set(fails) | set(vulns), \
        f"the probe did not enumerate the declared debt: {rows}"
    assert spec_a == spec_b, \
        f"the serialized debt spec differed between PYTHONHASHSEED {seeds[0]} " \
        f"and {seeds[1]}: a redelivered signal would open a second run"


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


def test_filing_that_opened_nothing_is_not_a_success(repo):
    """**SPAWNED. T504.** Intake refuses a dirty tree, so every item was refused
    and `0 run(s) opened` exited 0 - the same code as filing every one. Debt
    still owed after `--file` is `1`, as it is before it."""
    declare(repo, "known_failures", ["test_red"])
    (repo / "scratch.py").write_text("y = 2\n", encoding="utf-8")

    proc, said = spawn_debt(repo, "--file")
    assert "0 run(s) opened" in said and "refused: test_red" in said, said
    assert proc.returncode == 1, \
        f"debt still owed after filing is exit 1; got {proc.returncode}: {said!r}"


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


def test_an_absent_target_is_could_not_look_not_a_measured_zero(tmp_path):
    """**SPAWNED. Absent is not zero (T230).** A `--target` that does not exist
    read through `policy._profile_source` as "no profile yet", so the door
    answered `0 debt item(s) declared` at exit 0 - the measured-zero this project
    keeps finding, on the input easiest to get wrong. It is could-not-look at the
    whole target, so the door NAMES it and exits 3, exactly as it does for an
    UNREADABLE profile. Both doors are checked: the writing one and the read-only
    JSON one, because a door that writes must not act on a target it never saw."""
    absent = tmp_path / "no-such-dir"                    # never created
    for extra in (["--file", "--allow-dirty"], ["--json"]):
        proc, said = spawn_debt(absent, *extra)
        assert proc.returncode == 3, \
            f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
        assert "does not exist" in said and absent.name in said, \
            f"the door did not name the absent target: {said!r}"
        assert "0 debt item" not in said, \
            f"an absent target was answered as a measured zero: {said!r}"


# ── one declaration is one item; the key follows where it was MADE ──────────

WORKSPACE = ("stack: python\ngate_profile: solo\nscope: workspace\n"
             "phases: [intake, requirements, design, implementation, test, review, "
             "triage, closeout]\n"
             'test_command: "pytest -q"\n')


def workspace(tmp_path: Path, top: str, comps: list[tuple]) -> Path:
    """A three-component workspace. **A single-component fixture cannot express
    this case** - the whole question is what a value read by three components
    files as - so the shape of the fixture is part of the test.

    **Every component's PATH differs from its NAME**, and that is part of the
    shape too. This helper used to write each name as the path, so an identity
    keyed on PATH passed every test here - measured, 33 of 33 - while the row's
    whole claim is that the key follows where a declaration was made. A fixture
    that cannot tell the two ways of naming a component apart cannot prove
    which one the key uses. An entry may name its path as a third element, which
    is how two components come to share one."""
    r = tmp_path / "ws"
    (r / ".sdlc").mkdir(parents=True, exist_ok=True)
    text = WORKSPACE + top + "components:\n"
    for n, (name, own, *where) in enumerate(comps):
        path = where[0] if where else f"src/part{n}"
        text += f"  - name: {name}\n    path: {path}\n" + own
    (r / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")
    return r


def three_plain() -> list[tuple[str, str]]:
    return [("web", ""), ("api", ""), ("worker", "")]


def test_a_top_level_declaration_files_once_for_the_whole_repository(tmp_path):
    """**The count this section exists for. MEASURED at the base: 6 candidates
    from 2 declarations on three components**, three copies of each carrying one
    specification hash between them and differing only in a field nothing read.
    On a 26-component workspace that is 26 tracker rows for one decision, and
    item 66 exists so debt becomes a run somebody SCHEDULES.

    **The rule is the reader's own** (`suite.quarantined`): a value a component
    declared for itself wins, and the top level is the FALLBACK. A value taken
    from the fallback was declared once, at the repository, so it files once."""
    root = workspace(tmp_path, 'known_failures: ["ws.Red#one"]\n'
                     + f'{SCA.QUARANTINE_KEY}: ["PyPI pkg 1.0 CVE-1"]\n',
                     three_plain())
    got = D.candidates(root)
    assert [(i["source"], i["key"], i["component"]) for i in got] == [
        ("quarantined-test", "ws.Red#one", ""),
        ("accepted-vulnerability", "PyPI pkg 1.0 CVE-1", ""),
    ], f"two declarations filed as {len(got)} items: {got}"


def test_a_component_declaration_files_once_per_declaring_component(tmp_path):
    """The other half of the same rule. A component that declared a quarantine
    for ITSELF owns that debt, so each declaring component gets its own item -
    and a component that declared nothing is not carrying anybody's."""
    root = workspace(tmp_path, "", [
        ("web", '    known_failures:\n      - "web.Own#red"\n'),
        ("api", '    known_failures:\n      - "api.Own#red"\n'),
        ("worker", ""),
    ])
    got = D.candidates(root)
    assert [(i["key"], i["component"]) for i in got] == [
        ("api.Own#red", "api"), ("web.Own#red", "web")], got


def test_the_repository_and_a_component_declaring_one_name_are_two_items(tmp_path):
    """**The collision this fix would otherwise create one layer on.**

    `web` declares `both.Red#one` for itself and the repository declares it for
    everyone: two facts, made in two places, that must stay two items. They
    carry the same `key`, so if the specification ignores the component they
    hash the SAME - and `intake.duplicates` would then refuse to file the second
    as a duplicate of the first. The component field is READ, here, for exactly
    this: two items that must be distinct must not be identical to the finder.
    """
    root = workspace(tmp_path, 'known_failures: ["both.Red#one"]\n', [
        ("web", '    known_failures:\n      - "both.Red#one"\n'),
        ("api", ""), ("worker", ""),
    ])
    got = D.candidates(root)
    assert sorted((i["key"], i["component"]) for i in got) == [
        ("both.Red#one", ""), ("both.Red#one", "web")], got
    first, second = sorted(got, key=lambda i: i["component"])
    assert entry.spec_hash(first["title"], D.spec_for(first)) != \
        entry.spec_hash(second["title"], D.spec_for(second)), \
        "the repository's item and the component's item hash the same, so the " \
        "duplicate finder would file only one of two real decisions"


@pytest.mark.parametrize("blank_name", ["", '"   "'],
                         ids=["missing", "quoted-whitespace"])
def test_two_components_with_no_name_do_not_become_one_item(tmp_path,
                                                             blank_name):
    """**T351.** A component with no usable name still made its OWN
    declaration, and the scoping helper must not hand it the repository's own
    marker for that fact - the empty marker the FALLBACK case earns honestly
    by never having declared anything of its own.

    MEASURED at the pre-fix source: `_scoped` computed
    `mine = comp.get("name", "")` whenever the component declared its own
    value - so a component that DID declare for itself, but whose `name` is
    blank, got the exact empty-string marker that means "this came from the
    repository fallback". Two components with no name, each independently
    declaring the SAME test for itself, then hash to the identical
    `(mine, value)` pair and the second is silently dropped as though it were
    a duplicate of the first - one component's declaration eaten by
    another's, both mistaken for the repository's. A nameless component
    cannot be scoped, so it is refused rather than guessed at.

    **Parametrised over TWO ways to have no usable name, per cell 7's own
    "empty OR MISSING": a bare `- name:` with nothing after it, and a name
    that UNQUOTES to pure whitespace.** `_text` (`_unquote` before T494) takes a
    surrounding quote pair off and strips nothing inside it, so `- name: "   "`
    yields the string `"   "` -
    non-empty, hence TRUTHY - and only blank once it is itself `.strip()`ped.
    A guard that checked the raw name rather than the stripped one would
    raise for "missing" and silently re-scope for "quoted-whitespace", which
    is this row's exact defect reopened by testing the wrong value - so the
    "missing" case alone cannot tell the two checks apart and both are
    needed."""
    root = workspace(tmp_path, "", [
        (blank_name, '    known_failures:\n      - "shared.Red#one"\n', "src/one"),
        (blank_name, '    known_failures:\n      - "shared.Red#one"\n', "src/two"),
        ("api", ""),
    ])
    with pytest.raises(D.DebtError) as exc:
        D.candidates(root)
    assert "name" in str(exc.value).lower(), exc.value


def test_an_accepted_vulnerability_follows_the_same_rule_as_a_quarantined_test(
        tmp_path):
    """**Both quarantine kinds go through ONE reader** - `suite.quarantined`,
    differing only in the key - so a fix defined as agreeing with that reader
    has to agree for both, or the module has invented a third rule for one of
    them."""
    root = workspace(tmp_path, f'{SCA.QUARANTINE_KEY}: ["PyPI pkg 1.0 CVE-1"]\n', [
        ("web", f'    {SCA.QUARANTINE_KEY}:\n      - "PyPI own 2.0 CVE-2"\n'),
        ("api", ""), ("worker", ""),
    ])
    got = D.candidates(root)
    assert sorted((i["key"], i["component"]) for i in got) == [
        ("PyPI own 2.0 CVE-2", "web"), ("PyPI pkg 1.0 CVE-1", "")], got


def test_a_components_item_names_the_component_a_human_has_to_go_to(tmp_path):
    """A field with zero reads is this repository's dead-declaration class, and
    `component` had three writes and no reads. It is read rather than dropped
    because the identity above needs it - and once it is in the identity it
    belongs in the text a human is asked to act on, or the tracker carries two
    rows with one title."""
    root = workspace(tmp_path, "", [
        ("web", '    known_failures:\n      - "one.Red#test"\n'), ("api", "")])
    item = D.candidates(root)[0]
    assert "web" in item["title"], item["title"]
    assert "web" in D.spec_for(item), D.spec_for(item)


def test_a_single_repository_install_still_files_one_item(repo):
    """The degenerate case of the same model. `policy.components` synthesises
    ONE component named for the directory, which declares no quarantine of its
    own - so the value comes from the fallback and is a repository fact."""
    declare(repo, "known_failures", ["test_red"])
    got = D.candidates(repo)
    assert [(i["key"], i["component"]) for i in got] == [("test_red", "")], got


# ── the identity is TITLE-OR-HASH, so the scope must survive a forged key ───


def titles(items: list[dict]) -> list[str]:
    return [i["title"] for i in items]


def test_a_key_cannot_forge_the_scope_in_the_title(tmp_path):
    """**The half of the identity the first pass did not measure.**
    `intake.duplicates` matches a case-folded TITLE as an alternative to the
    specification hash, so a title is half the match key - and a scope written
    into free text is a scope the key can spell for itself.

    Here the repository declares a test whose own name embeds the component
    scope. If the scope is appended as prose, the repository item's title and
    `web`'s item's title are byte-identical, the duplicate finder calls them one
    run, and two differently-scoped decisions collapse - the original bug, re-
    entered through the door the fix opened.

    The scope therefore sits at a FIXED position ahead of the key, where nothing
    the key contains can reach it. **The forging key spells whatever marker the
    module emits**: it is built from `_where` rather than typed, because a typed
    delimiter is a third copy of the fact and a moved delimiter walks straight
    past it - measured, the two POSITION mutants lived through the typed
    version and die against the derived one. Deriving the key does nothing for
    the third survivor, the delimiter doubling: no key-side forge can see that,
    and it is killed from the component-name end below."""
    forge = "flaky.Test#one" + D._where("web")
    root = workspace(tmp_path, f'known_failures: ["{forge}"]\n', [
        ("web", f'    known_failures:\n      - "flaky.Test#one"\n'),
        ("api", ""), ("worker", "")])
    got = D.candidates(root)
    assert sorted((i["key"], i["component"]) for i in got) == [
        ("flaky.Test#one", "web"), (forge, "")], got
    assert len({t.strip().lower() for t in titles(got)}) == 2, \
        f"a key forged the scope; the duplicate finder sees one run: {titles(got)}"


def test_a_key_cannot_forge_the_scope_on_the_other_quarantine_kind(tmp_path):
    """**Both kinds, because both build a title the same way.** A fix welded
    into one title string and left out of the other is this repository's
    dominant defect shape, and the sibling test above cannot see it."""
    forge = "PyPI pkg 1.0 CVE-1" + D._where("web")
    root = workspace(tmp_path, f'{SCA.QUARANTINE_KEY}: ["{forge}"]\n', [
        ("web", f'    {SCA.QUARANTINE_KEY}:\n      - "PyPI pkg 1.0 CVE-1"\n'),
        ("api", ""), ("worker", "")])
    got = D.candidates(root)
    assert len({t.strip().lower() for t in titles(got)}) == 2, \
        f"a key forged the scope; the duplicate finder sees one run: {titles(got)}"


def joint(tmp_path: Path, key: str) -> str:
    """The template text between the scope marker and the declared value, READ
    OFF A REAL TITLE rather than typed - a typed copy is a third copy of the
    fact, and this repository has already watched three mutants live through
    one."""
    root = workspace(tmp_path / "joint", "",
                     [("c0", f'    {key}:\n      - "k0"\n')])
    title, marker = D.candidates(root)[0]["title"], D._where("c0")
    return title[title.index(marker) + len(marker): title.index("k0")]


def forged_pair(tmp_path: Path, key: str) -> Path:
    """Two components whose titles collide unless the marker's delimiter is
    doubled. **The keys DIFFER**, and they have to: the text after the marker is
    the template PLUS THE KEY, and the key is free text that can absorb whatever
    a name pushed past the closing bracket. With one shared key the two titles
    differ whatever the marker does, and a test built that way passes with the
    doubling removed - measured, which is how it was found."""
    j = joint(tmp_path, key)
    return workspace(tmp_path, "", [
        ("web]" + j + "X", f'    {key}:\n      - "Y"\n'),
        ("web", f'    {key}:\n      - "X]{j}Y"\n')])


def test_a_component_name_cannot_forge_another_components_scope(tmp_path):
    """**The forge from the other end, and the doubling is the whole defence.**
    A name carrying `]` plus the template text closes the marker early and
    spells `web`'s scope; a second component's KEY supplies the rest. Without
    `]` doubled in the name the two titles are byte-identical. My earlier
    record called that doubling an EQUIVALENT mutant - injective at a fixed
    position before a constant template - and it was false: the template is
    only constant up to where the key begins."""
    got = D.candidates(forged_pair(tmp_path, "known_failures"))
    assert len(got) == 2 and len({i["key"] for i in got}) == 2, got
    assert len({t.strip().lower() for t in titles(got)}) == 2, \
        f"a component name forged another component's scope: {titles(got)}"


@pytest.mark.parametrize("key", ["known_failures", "sca_known_findings"])
def test_the_forged_name_does_not_match_either_way_in_the_duplicate_finder(
        tmp_path, repo, key):
    """**Composed with the real matcher, in BOTH directions, on BOTH kinds.** A
    title-set assertion is a string comparison; the defect is what
    `intake.duplicates` does with them. Each forged item gets a real run, and
    the matcher is asked about the other."""
    # Welded: a renamed quarantine key fails here rather than parametrising
    # over a key nothing reads.
    assert key in ("known_failures", SCA.QUARANTINE_KEY), key
    got = D.candidates(forged_pair(tmp_path, key))
    assert len(got) == 2, got
    for filed, asked in (got, got[::-1]):
        rec = entry.open_run(repo, D.spec_for(filed), kind=D.KIND,
                             src_kind="debt", src_name=filed["source"],
                             allow_dirty=True)
        found = entry.duplicates(repo, entry.spec_hash(asked["title"],
                                                       D.spec_for(asked)),
                                 asked["title"])
        assert [f for f in found if f["run_id"] == rec["run_id"]] == [], (
            f"{asked['component']!r}'s item matched {filed['component']!r}'s run: "
            f"{found} - {filed['title']!r} vs {asked['title']!r}")


def test_two_components_sharing_one_path_are_still_two_items(tmp_path):
    """**The key is the component's NAME, not where it lives on disk.** Two
    components may be rooted at one directory, and each that declares a value
    for itself owns that decision. An identity keyed on path would make them one
    item - and the component that pays it down would close the other's."""
    root = workspace(tmp_path, "", [
        ("web", '    known_failures:\n      - "s.Red#one"\n', "shared"),
        ("api", '    known_failures:\n      - "s.Red#one"\n', "shared")])
    got = D.candidates(root)
    assert [(i["key"], i["component"]) for i in got] == [
        ("s.Red#one", "api"), ("s.Red#one", "web")], got


def test_the_scope_reaches_the_duplicate_finder_that_matches_on_it(tmp_path, repo):
    """**PRODUCER AND CONSUMER COMPOSED**, which is the only reason the tests
    above are worth anything. They assert two title STRINGS differ; this opens a
    real run for the component item and asks the real `intake.duplicates` about
    the repository item. The matcher's key is the specification hash OR the
    case-folded title, so a title this module believes is distinct and the
    matcher folds into one is the defect - and no string comparison here can
    see that."""
    forge = "flaky.Test#one" + D._where("web")
    root = workspace(tmp_path, f'known_failures: ["{forge}"]\n',
                     [("web", '    known_failures:\n      - "flaky.Test#one"\n'),
                      ("api", "")])
    scoped = [i for i in D.candidates(root) if i["component"]][0]
    repository = [i for i in D.candidates(root) if not i["component"]][0]
    rec = entry.open_run(repo, D.spec_for(scoped), kind=D.KIND,
                         src_kind="debt", src_name=scoped["source"])
    found = entry.duplicates(repo, entry.spec_hash(repository["title"],
                                                   D.spec_for(repository)),
                             repository["title"])
    assert found == [], (
        f"the repository item matched the component's run {rec['run_id']}: "
        f"{found} - scoped title {scoped['title']!r} vs repository title "
        f"{repository['title']!r}")


# ── unanimity is a THIRD valid basis, and it must not be adopted ────────────


def test_every_component_declaring_one_value_is_still_one_item_each(tmp_path):
    """**Rule 28's third valid-but-wrong basis, which had no test.** A value
    every component declares is ARGUABLY a repository decision - which is
    exactly what makes it dangerous: a keying that collapsed unanimity would
    look reasonable and no test would see it.

    The ruling says the key follows where the declaration was MADE. Three
    components each declared this for themselves, so it is three facts and three
    components have to act - and the one that pays it down first must not close
    the other two's item."""
    same = "shared.Red#one"
    root = workspace(tmp_path, "", [
        ("web", f'    known_failures:\n      - "{same}"\n'),
        ("api", f'    known_failures:\n      - "{same}"\n'),
        ("worker", f'    known_failures:\n      - "{same}"\n')])
    got = D.candidates(root)
    assert [(i["key"], i["component"]) for i in got] == [
        (same, "api"), (same, "web"), (same, "worker")], got
    assert len({entry.spec_hash(i["title"], D.spec_for(i)) for i in got}) == 3, \
        f"three components' items share an identity: {titles(got)}"
