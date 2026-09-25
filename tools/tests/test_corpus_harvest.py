"""Corpus harvest v1: mechanical selection from real history, adopter-resident by rule.

Item 17. The classifier is pinned sentence by sentence against a synthetic repository
whose history is BUILT here, commit by commit — so every rule ("a docs-only fix is not
a defect case") fails its own test rather than a reading of the code. The probe default
writes nothing; `--write` lands cases in the repository they came from, never anywhere
else.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import corpus_harvest as H  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """Four commits, one per rule: a wide change, a fix, a docs-only fix, and a merge."""
    r = tmp_path_factory.mktemp("hist") / "svc"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")

    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")

    (r / "api").mkdir(); (r / "worker").mkdir()
    (r / "api" / "a.py").write_text("a\n"); (r / "worker" / "b.py").write_text("b\n")
    git(r, "add", "."); git(r, "commit", "-qm", "add api and worker halves")

    (r / "api" / "a.py").write_text("a2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix the null response bug")

    (r / "README.md").write_text("docs\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix typo in readme")

    git(r, "checkout", "-qb", "feature")
    (r / "worker" / "b.py").write_text("b2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "feature work")
    git(r, "checkout", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-m", "Merge branch feature", "feature")
    return r


def cases_of(repo: Path) -> list[dict]:
    """CORRECTED (T389): this used to also assert ``dropped == []``, which
    was true only because the ``repo`` fixture's root commit ("seed") and
    turn-down commit ("fix typo in readme") both vanished from ``dropped``
    silently. Now that both are recorded (see the T389 section below),
    that assumption is false for this very fixture, so it is dropped from
    this generic helper; what ``dropped`` now contains is pinned
    explicitly by
    test_the_shared_fixtures_root_and_turn_down_commits_are_both_recorded.
    """
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == ""
    return cases


# ── the classifier, one rule per test ─────────────────────────────────────────


def test_a_change_spreading_across_directories_is_a_scoping_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    assert "scoping" in by_title["add api and worker halves"]["classes"]


def test_a_fix_touching_code_is_a_defect_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    case = by_title["fix the null response bug"]
    assert case["classes"] == ["defect"], "one file, one dir — a defect and nothing else"
    assert case["files"] == ["api/a.py"]


def test_a_docs_only_fix_is_not_a_defect_case(repo):
    titles = {c["title"] for c in cases_of(repo)}
    assert "fix typo in readme" not in titles, \
        "a typo fixed in a README is a fix, not a case to replay a localizer against"


def test_a_merge_with_changes_is_a_review_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    assert "review" in by_title["Merge branch feature"]["classes"]


def test_every_case_is_stamped_real_and_carries_its_provenance(repo):
    for c in cases_of(repo):
        assert c["synthetic"] is False
        assert c["sha"] and c["parent"] and c["date"] and c["files"]


def test_the_history_window_is_a_recorded_bound(repo):
    newest_only, err, _ = H.harvest_repo(repo, "svc", limit=1)
    assert err == ""
    assert {c["title"] for c in newest_only} == {"Merge branch feature"}


def test_a_directory_that_is_not_a_repository_degrades_to_a_row(tmp_path):
    cases, err, _ = H.harvest_repo(tmp_path, "plain", limit=10)
    assert cases == [] and err, "stated, never skipped silently"


# ── probe vs write: the adopter-resident line ─────────────────────────────────


def test_probe_is_the_default_and_writes_nothing(repo, tmp_path, capsys):
    # ROUND-TRIPPED (T389). ORIGINAL: `== 0`. Round 1 changed this to
    # `== 3`, reasoning that the shared `repo` fixture's root and
    # turn-down commits, once recorded, should flip the exit code (T389
    # round 1's "any drop means could-not-look" rule, extending T318).
    # Round 2 corrected the RULE itself: an examined-and-excluded commit
    # is a measurement, not a could-not-look, so this fixture's drops no
    # longer change the exit code. Back to `== 0` - the ORIGINAL literal
    # value, restored for the corrected reason, not merely reverted.
    out = tmp_path / "probe.json"
    assert H.main(["--repo", str(repo), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "probe only" in text and "nothing written" in text
    assert not (repo / ".sdlc").exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["classes"]["defect"] == 1
    assert report["floor_met"]["defect"] is False, "1 < the M5 floor of 10, and v1 " \
                                                   "reports rather than enforces"


def test_write_lands_cases_in_the_repository_they_came_from(repo, capsys):
    # ROUND-TRIPPED (T389), same history and reason as
    # test_probe_is_the_default_and_writes_nothing above: ORIGINAL `== 0`
    # -> round 1 `== 3` -> round 2 back to `== 0`. `--write` still writes
    # every real case regardless of the exit code, which only reports
    # whether a GENUINE read failure occurred.
    assert H.main(["--repo", str(repo), "--write"]) == 0
    files = sorted((repo / ".sdlc" / "corpus").glob("*.json"))
    assert files, "the adopter's act puts the cases in the adopter's tree"
    case = json.loads(files[0].read_text(encoding="utf-8"))
    assert case["schema_version"] == H.SCHEMA_VERSION and case["synthetic"] is False


def test_exactly_one_source_is_required(capsys):
    assert H.main([]) == 2
    assert "exactly one" in capsys.readouterr().out


# ── could-not-look is not measured-zero (A3c-14 / W156) ──────────────────────


ROOT = Path(__file__).resolve().parents[2]


def spawn_harvest(*extra: str):
    """The real door, spawned - a CLI's contract is a process, and this one had
    never been spawned by a test (W156 A3c-14, class 7)."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "corpus_harvest.py"), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def _one_commit_repo(root: Path) -> Path:
    """A real git repo with only a root commit - READABLE, but no case qualifies
    (a root commit has no 'before'), so it is a genuine MEASURED zero."""
    r = root / "root_only"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")
    return r


def test_a_repo_that_cannot_be_read_is_could_not_look_not_measured_zero(tmp_path):
    """**SPAWNED. A3c-14 (W156).** A `--repo` that is not a git repository and a
    real repo that genuinely yields no case both printed the floors 'below' at
    exit 0, so a caller reading only the exit code could not tell could-not-look
    from measured-zero - this project's most-repeated distinction. Could-not-look
    is exit 3, the code `corpus_replay` already uses at its door."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    proc, said = spawn_harvest("--repo", str(not_a_repo))
    assert proc.returncode == 3, \
        f"a component that could not be read is could-not-look (3); got " \
        f"{proc.returncode}: {said!r}"
    assert "!!" in said, said


def test_a_real_repo_with_no_qualifying_case_is_measured_zero_exit_0(tmp_path):
    """**SPAWNED.** The other half: a repo that WAS read and holds no case is a
    measured zero and stays exit 0. Without it, exit 3 would only prove 3 means
    'anything', not 'could not look'.

    ROUND-TRIPPED (T389). Round 1 of this row briefly moved this test (it
    made `_one_commit_repo`'s root commit flip the exit code to 3 once
    recorded, reasoning that ANY drop should demote the run). Round 2
    corrected that: an examined-and-excluded commit (this one - git
    shows it fine, it is a root commit with no "before") is a
    MEASUREMENT, not a could-not-look, and collapsing the two inverts
    A3c-14, this project's own most-repeated distinction. Restored here,
    STRENGTHENED over the original: it now also asserts the root commit
    is VISIBLE as a recorded drop (zero silent drops), proving that
    recording it and demoting the exit code are independent - T389
    fixes the first without needing the second.
    """
    r = _one_commit_repo(tmp_path)
    proc, said = spawn_harvest("--repo", str(r))
    assert proc.returncode == 0, \
        f"an examined-and-excluded commit is measured-zero (exit 0), not " \
        f"could-not-look; got {proc.returncode}: {said!r}"
    assert "!!" in said, \
        f"the root commit must still be VISIBLE as a recorded drop (zero " \
        f"silent drops), even though it does not change the exit code; " \
        f"got:\n{said}"


def test_floor_met_is_none_when_no_component_could_be_read(tmp_path):
    """**A3c-14, the report half.** `floor_met: false` is a MEASURED claim - we
    counted and it was below the floor. On a run where nothing was readable the
    count never happened, so the honest answer is None, not False."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    out = tmp_path / "probe.json"
    code = H.main(["--repo", str(not_a_repo), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["unharvestable"], "the premise: the component was unreadable"
    assert report["floor_met"]["scoping"] is None, \
        "a floor nobody could measure is could-not-look (None), not below (False)"
    assert code == 3, "and the process says could-not-look too"


# ── a dropped commit is counted, not skipped silently (A3c-15 / W156) ────────


def _show_always_fails(monkeypatch):
    real = H._git

    def fake(r, *args):
        if args and args[0] == "show":
            return "", "fatal: bad object"
        return real(r, *args)

    monkeypatch.setattr(H, "_git", fake)


def test_a_commit_whose_show_fails_is_counted_not_dropped(repo, monkeypatch):
    """**A3c-15 (W156).** `harvest_repo` did `if err: continue` on a commit whose
    `git show` failed - no counter, no record - so a dropped commit lowered the
    counts the M5 floors are judged against and the lowered count was reported as
    measured. The module docstring keeps this rule at the COMPONENT level
    ('stated, never skipped silently') and broke it at the commit level."""
    _show_always_fails(monkeypatch)
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == "", "the component itself was readable"
    assert cases == [], "every commit's show failed, so no case survives"
    assert dropped, "a dropped commit must be recorded, not skipped silently"
    assert all(d.get("sha") and d.get("why") for d in dropped), \
        "each drop carries which commit and why - provenance, not a bare count"
    # Pin the IDENTITY of the show-failure reason: it must come from git, not
    # be a static string the code could silently swap.  A monkeypatched show
    # returns "fatal: bad object", so that text must appear in `why`.
    assert any("fatal" in d["why"] for d in dropped), \
        f"a show-failure reason must carry git's own error; got " \
        f"{[d['why'] for d in dropped]!r}"
    # T389 round 2: a show failure is the one shape that genuinely could
    # not be examined, so it (and only it) must carry kind="could_not_look".
    assert any(d.get("kind") == "could_not_look" for d in dropped), \
        f"a git-show failure must be tagged kind='could_not_look'; got " \
        f"{[(d['sha'][:12], d.get('kind')) for d in dropped]!r}"


def test_dropped_commits_are_reported_and_make_the_run_could_not_look(
        repo, tmp_path, monkeypatch, capsys):
    """The report and the exit code, composed. A drop that only lowered a count
    is exactly could-not-look wearing measured-zero's clothes, so the report
    records it the way it records an unharvestable component and the process
    stops reading as a clean measured zero."""
    _show_always_fails(monkeypatch)
    out = tmp_path / "probe.json"
    code = H.main(["--repo", str(repo), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report.get("dropped_commits"), \
        "commits git could not show must be recorded in the report"
    said = capsys.readouterr().out
    assert "!!" in said, "the drop is printed, not silent"
    assert code == 3, \
        "a run that could not read some of the history it was asked to is " \
        "could-not-look, not a clean measured zero"


# ── a harness-only commit is recorded, not silently dropped (T318 / W391) ────


def _repo_with_harness_only_commit(root: Path) -> Path:
    """A real git repo whose newest non-root commit touches ONLY harness files.

    The commit qualifies structurally (has a parent, git show succeeds) but
    after subject_files() filters the harness bookkeeping, nothing remains.
    Previously this was a silent ``continue`` with no record — the commit
    vanished from the case list AND from the dropped list, lowering the
    total the M5 floors rest on with nobody the wiser.
    """
    r = root / "harness_only"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    # Commit 1 (root): creates a real file.
    (r / "src").mkdir()
    (r / "src" / "app.py").write_text("# app\n")
    git(r, "add", "."); git(r, "commit", "-qm", "initial")
    # Commit 2: touches a real file in two dirs (scoping-eligible).
    (r / "lib").mkdir()
    (r / "lib" / "util.py").write_text("# util\n")
    (r / "src" / "app.py").write_text("# app v2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "add lib and update app")
    # Commit 3: touches ONLY harness bookkeeping.
    (r / ".sdlc").mkdir()
    (r / ".sdlc" / "run.json").write_text("{}\n")
    git(r, "add", "."); git(r, "commit", "-qm", "record harness run")
    return r


def test_harness_only_commit_is_recorded_not_silently_dropped(tmp_path):
    """T318.  ``harvest_repo`` lists commits from ``git log``, then calls
    ``git show`` on each.  When show succeeds but every changed file is
    harness bookkeeping, ``subject_files()`` returns ``[]`` and the loop
    used to ``continue`` with no record.  The commit vanished from BOTH
    the case list AND the dropped list — zero silent drops is a
    load-bearing convention, and this broke it.

    CORRECTED (T389): the filter below used to be ``if d["sha"]`` (true
    of every entry), which only ever isolated the harness-only drop
    because this fixture's FIRST commit is a root commit that, at T318
    time, still vanished silently. Now the root commit ("initial") is
    ALSO a recorded drop (T389), so the filter is narrowed to the
    harness-specific reason to keep testing what it says it tests.
    """
    r = _repo_with_harness_only_commit(tmp_path)
    cases, err, dropped = H.harvest_repo(r, "harness_only", limit=50)
    assert err == "", "the component itself was readable"
    # The harness-only commit must appear in `dropped`, not vanish.
    harness_drops = [d for d in dropped if "harness" in d.get("why", "").lower()]
    assert harness_drops, \
        "a commit whose files are all harness bookkeeping must be " \
        "recorded in dropped, not silently skipped"
    # The reason must name WHY — specifically, that it was harness bookkeeping.
    for d in harness_drops:
        assert d.get("why"), \
            f"dropped commit {d['sha'][:12]} carries no reason"
        assert "harness" in d["why"].lower(), \
            f"the reason must name harness bookkeeping, not a generic " \
            f"message; got: {d['why']!r}"


def test_harness_only_drop_is_visible_in_cli_output(tmp_path):
    """T318, spawned.  A person running the probe must SEE that commits
    were examined and excluded — absent is not zero, and a count that
    silently omits entries it tried and failed to process is worse than
    one that admits the gap.

    CORRECTED (T389 round 2). OLD: asserted `proc.returncode == 3`,
    reasoning "a run with unscored commits is could-not-look, not a
    clean zero". MEASURED false at scale: a harness-only commit is
    EXAMINED (git show succeeds) and excluded by rule, exactly like a
    root commit or a turn-down - it is not a read failure, so it must
    not carry could-not-look's exit code either (T389 round 2 draws
    this line the same way for all three "excluded" shapes). The CLI
    wording is corrected too: T318's "could not be scored" was already
    false for this exact drop (the commit WAS scored - examined and
    judged, just not claimed), corrected to "did not produce a case".
    """
    r = _repo_with_harness_only_commit(tmp_path)
    proc, said = spawn_harvest("--repo", str(r))
    # The CLI must mention the dropped commit(s).
    assert "!!" in said, \
        f"the harness-only drop must be visible in the output; got:\n{said}"
    # Pin the corrected wording positively, not just the old one's absence.
    assert "git could not show" not in said, \
        "the CLI message must not claim show-failure when the drop was " \
        "harness bookkeeping"
    assert "did not produce a case" in said, \
        f"the CLI must use the true wording (examined, not claimed), not " \
        f"claim the commit could not be scored; got:\n{said}"
    assert proc.returncode == 0, \
        f"an examined-and-excluded commit (harness bookkeeping) is " \
        f"measured-zero (exit 0), not could-not-look; got {proc.returncode}"


def test_harvest_repo_docstring_covers_all_drop_reasons():
    """T318 round 4 (W396's round-3 BLOCK, part 2).  Round 3 collected only
    ``ast.Constant`` string values under a ``"why"`` key, so the SAME
    reason written as an f-string (``ast.JoinedStr`` -- Python parses even
    a brace-free f-string this way, never simplified to a plain constant)
    was invisible: adding one as a new drop reason left the test at
    ``1 passed, 18 deselected`` with nothing checked.  Widened per the
    reviewer's minimum: an f-string's literal (non-interpolated) parts are
    now collected too.  Proven with the reviewer's own f-string probe
    (worker-W400.md round 4).

    STILL NOT COVERED, named rather than implied, because a widened catch
    is not a promise of exhaustiveness: a reason built with ``+``
    concatenation (``ast.BinOp``), one read off a module-level or other
    ``Name`` constant, the ``dict(sha=..., why=...)`` call form (as
    opposed to a ``{...}`` display), and any reason appended from a helper
    this walk does not descend into (it parses only ``harvest_repo``'s own
    source).  A reason written in one of those shapes would pass here
    whether or not the docstring mentions it.  Widen further if one of
    those shapes is ever actually used.
    """
    import ast, inspect, re, textwrap
    src = textwrap.dedent(inspect.getsource(H.harvest_repo))
    func_def = ast.parse(src).body[0]
    assert isinstance(func_def, ast.FunctionDef) and func_def.name == "harvest_repo"

    def literal_text(value_node):
        """A string value's own fixed text: a plain constant, or an
        f-string's non-interpolated parts joined -- its ``{...}`` parts
        are dynamic and carry no fixed text to hold the docstring to."""
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            return value_node.value
        if isinstance(value_node, ast.JoinedStr):
            return "".join(part.value for part in value_node.values
                           if isinstance(part, ast.Constant)
                           and isinstance(part.value, str))
        return None

    # Every "why" value in a dict literal anywhere in the function body that
    # carries fixed text -- a plain string or an f-string's literal parts.
    reason_literals = [
        text
        for node in ast.walk(func_def) if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "why"
        and (text := literal_text(value))
    ]
    assert reason_literals, \
        "expected at least one literal 'why' reason inside harvest_repo -- " \
        "if this is empty the DERIVATION broke, not necessarily the docstring"

    doc = H.harvest_repo.__doc__
    assert doc is not None, "harvest_repo must have a docstring"
    doc_words = set(re.findall(r"[a-z]+", doc.lower()))
    stopwords = {"a", "an", "the", "is", "are", "was", "were", "all", "of", "to",
                "for", "in", "on", "and", "or", "not", "no", "that", "this",
                "with", "its", "it", "be", "as", "at", "each"}
    for literal in reason_literals:
        content_words = {w for w in re.findall(r"[a-z]+", literal.lower())
                         if w not in stopwords}
        missing = content_words - doc_words
        assert not missing, \
            f"the docstring must cover the recorded reason {literal!r} " \
            f"(missing word(s) {sorted(missing)!r}); doc={doc!r}"


# ── a root or turn-down commit is recorded, not silently dropped (T389) ──────


def _titles_by_sha(repo: Path) -> dict[str, str]:
    """sha -> subject, read directly via git - independent of
    ``harvest_repo``'s own bookkeeping, so a test correlating a drop's sha
    back to a commit title is not just checking the tool against itself."""
    out, err = H._git(repo, "log", "--max-count=50", "--format=%H%x01%s")
    assert err == ""
    titles: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, title = line.partition("\x01")
        titles[sha] = title
    return titles


def test_the_shared_fixtures_root_and_turn_down_commits_are_both_recorded(repo):
    """T389.  The shared ``repo`` fixture has a root commit ("seed") and a
    turn-down commit ("fix typo in readme", docs-only). Both used to
    vanish from ``cases`` AND ``dropped`` silently. Corrected: both are
    now recorded in ``dropped`` with a reason naming which shape it was.

    Round 2 also pins ``kind``: both are examined commits (git shows the
    turn-down fine; a root commit is never even asked), not read
    failures, so both must carry ``kind="excluded"`` - the one field the
    exit-code door now reads to tell a measurement from a could-not-look.
    """
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == ""
    titles = _titles_by_sha(repo)
    dropped_titles = {titles[d["sha"]]: d["why"] for d in dropped}
    dropped_kinds = {titles[d["sha"]]: d.get("kind") for d in dropped}
    assert "seed" in dropped_titles, \
        f"the root commit must be recorded in dropped; got {dropped_titles!r}"
    assert "parent" in dropped_titles["seed"].lower(), \
        f"the root commit's reason must say why (no parent); got " \
        f"{dropped_titles['seed']!r}"
    assert dropped_kinds["seed"] == "excluded", \
        f"a root commit is examined-and-turned-down, not a read failure; " \
        f"got kind={dropped_kinds['seed']!r}"
    assert "fix typo in readme" in dropped_titles, \
        f"the turn-down commit must be recorded in dropped; got {dropped_titles!r}"
    assert "classify" in dropped_titles["fix typo in readme"].lower(), \
        f"the turn-down commit's reason must say why (no class claimed " \
        f"it); got {dropped_titles['fix typo in readme']!r}"
    assert dropped_kinds["fix typo in readme"] == "excluded", \
        f"a turn-down is examined-and-turned-down, not a read failure; " \
        f"got kind={dropped_kinds['fix typo in readme']!r}"
    # And no commit from this fixture is unaccounted for any more.
    case_shas = {c["sha"] for c in cases}
    dropped_shas = {d["sha"] for d in dropped}
    log_out, log_err = H._git(repo, "log", "--max-count=50", "--format=%H")
    assert log_err == ""
    all_shas = {sha for sha in log_out.splitlines() if sha}
    assert not (all_shas - case_shas - dropped_shas), \
        "every commit git log listed must now be a case or a recorded drop"


def _repo_with_one_good_commit(root: Path) -> Path:
    """A root commit, plus one real scoping-shaped commit - so a
    ``--limit`` that excludes the root from the window leaves a genuinely
    fully-accounted history: everything considered became a case."""
    r = root / "one_good"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")
    (r / "api").mkdir(); (r / "worker").mkdir()
    (r / "api" / "a.py").write_text("a\n"); (r / "worker" / "b.py").write_text("b\n")
    git(r, "add", "."); git(r, "commit", "-qm", "add api and worker halves")
    return r


# test_a_root_only_repo_is_now_could_not_look_not_a_clean_zero used to
# live here (round 1). CORRECTED and FOLDED BACK (T389 round 2): round 1
# ruled that recording a root commit should ALSO flip the exit code to 3.
# A reviewer measured the consequence on an ORDINARY readable repository
# (root + scoping + defect + docs commit): could-not-look became the
# routine outcome rather than the exception, because nearly every real
# repository has both a root commit and a documentation commit. Round 2
# reserves could-not-look for genuine read failures and gives this
# fixture back its ORIGINAL exit-0 contract, now STRENGTHENED to also
# check the drop is visible - see
# test_a_real_repo_with_no_qualifying_case_is_measured_zero_exit_0 near
# the top of this file, which is this test's restored, corrected home.
# OLD (quoted in full for the record):
#     def test_a_root_only_repo_is_now_could_not_look_not_a_clean_zero(tmp_path):
#         """**SPAWNED. T389 RULING.** ``_one_commit_repo`` is a single ROOT
#         commit: git show would succeed, but by rule a root commit never
#         becomes a case (no "before" to diff against). Before T389 this
#         vanished from both ``cases`` and ``dropped`` silently and the CLI
#         exited 0 -- indistinguishable from a repo genuinely, completely empty
#         of any case. That exit-0 was an ARTIFACT of the silent drop, not a
#         considered contract: once the root commit is recorded like any other
#         drop, it falls under the same rule T318 already established for a
#         harness-only commit (`test_harness_only_drop_is_visible_in_cli_output`
#         below) -- any recorded drop, of any reason, means the floor counts
#         rest on a partial accounting, so the run is could-not-look (3), not a
#         clean measured zero. This REPLACES
#         test_a_real_repo_with_no_qualifying_case_is_measured_zero_exit_0,
#         which asserted exit 0 for this exact fixture; both versions are
#         quoted in the commit body."""
#         r = _one_commit_repo(tmp_path)
#         proc, said = spawn_harvest("--repo", str(r))
#         assert proc.returncode == 3, \
#             f"a root commit is now a recorded drop, so this is could-not-look " \
#             f"(3), not a clean zero; got {proc.returncode}: {said!r}"
#         assert "!!" in said, said


def test_a_real_repo_with_a_fully_accounted_window_is_measured_zero_exit_0(tmp_path):
    """**SPAWNED. T389 RULING, the other half.** The could-not-look /
    measured-zero distinction (A3c-14, "this project's most-repeated
    distinction") survives this row: it is demonstrated by a window
    where EVERY commit ``git log`` listed became a case (here, ``--limit
    1`` excludes the root commit, leaving only the one scoping-shaped
    commit) -- read in full, nothing needed excluding, so nothing is
    could-not-look, even though the counts are still below the M5
    floors."""
    r = _repo_with_one_good_commit(tmp_path)
    proc, said = spawn_harvest("--repo", str(r), "--limit", "1")
    assert proc.returncode == 0, \
        f"a fully-accounted, below-floor window is measured-zero (exit " \
        f"0); got {proc.returncode}: {said!r}"


def test_dropped_now_accounts_for_every_commit(repo):
    """**CORRECTED (T389) from test_dropped_is_not_the_complement_of_cases.**
    OLD (T318 round 3, a deliberate tripwire): asserted ``unaccounted`` was
    NON-EMPTY, because a root commit and a turn-down commit both escaped
    ``cases`` and ``dropped``. T389 closes exactly that gap -- both shapes
    are now recorded in ``dropped`` (see
    test_the_shared_fixtures_root_and_turn_down_commits_are_both_recorded
    above) -- so the tripwire fires the moment this row lands, by design,
    and is corrected here to assert the opposite: ``unaccounted`` is now
    EMPTY, and the docstring must say so rather than still claiming a gap.
    """
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == "", "the component itself is readable"
    case_shas = {c["sha"] for c in cases}
    dropped_shas = {d["sha"] for d in dropped}
    log_out, log_err = H._git(repo, "log", "--max-count=50", "--format=%H")
    assert log_err == "", "git log itself must succeed against this fixture"
    all_shas = {sha for sha in log_out.splitlines() if sha}
    unaccounted = all_shas - case_shas - dropped_shas
    assert not unaccounted, \
        "every commit git log listed must now be a case or a recorded " \
        f"drop; still unaccounted: {sorted(unaccounted)!r}"
    # A set difference alone is blind to a commit counted TWICE - it is
    # still not "unaccounted" if it is in both `cases` and `dropped`, but
    # the docstring claims `len(cases) + len(dropped)` IS the number of
    # commits considered, which is only true if the two never overlap.
    # (T389 round 2, a reviewer-found survivor.)
    double_counted = case_shas & dropped_shas
    assert not double_counted, \
        "a commit must be a case OR a recorded drop, never both (or " \
        f"len(cases)+len(dropped) over-counts); got {sorted(double_counted)!r}"
    # CORRECTED (T389 round 3): the intersection check above only catches
    # a sha double-counted ACROSS the two lists. It is BLIND to the same
    # sha appended to `dropped` TWICE (e.g. a missing `continue` lets one
    # commit fall through two drop branches) - `dropped_shas` is a SET,
    # so a within-list duplicate collapses to one element and neither
    # check above ever sees it. My round-2 report claimed this control
    # proved the assertion was what killed a counted-twice mutant; a
    # reviewer measured that claim FALSE against exactly this shape (21
    # passed at my tip). The line the first round already proposed - a
    # straight count against list LENGTH, not set membership - catches
    # both shapes, because a within-list duplicate inflates
    # `len(dropped)` itself.
    assert len(cases) + len(dropped) == len(all_shas), \
        "every commit git log listed must be counted exactly once " \
        f"across cases and dropped; len(cases)={len(cases)} + " \
        f"len(dropped)={len(dropped)} != len(all_shas)={len(all_shas)}"
    doc = H.harvest_repo.__doc__
    assert "every commit" in doc.lower(), \
        "the docstring must say cases and dropped now account for every " \
        "commit, not still hedge that dropped is not their complement"


def test_the_printed_example_prefers_a_could_not_look_drop_over_an_excluded_one(
        tmp_path, monkeypatch, capsys):
    """T389 round 3.  The printed example is ``drops[0]``, ordered by
    recency (newest commit first) - so when the NEWEST drop in a
    component is a benign "excluded" one (a turn-down) and an OLDER
    commit is the one git genuinely could not read, the printed line
    showed the benign example next to a bare exit 3: nothing PRINTED
    said a commit could not be read, even though the exit code and the
    JSON both correctly carried it. Not silent - the code fires - but
    the summary line and the exit code disagreed about what happened.
    Fixed: the example prefers a could_not_look entry when the
    component has one, regardless of its position."""
    r = tmp_path / "old_fail_new_turndown"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    (r / "api").mkdir()
    (r / "api" / "a.py").write_text("a\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed with code")
    (r / "api" / "a.py").write_text("a2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix the null response bug")
    old_out, old_err = H._git(r, "rev-parse", "HEAD")
    assert old_err == ""
    old_sha = old_out.strip()
    (r / "README.md").write_text("docs\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix typo in readme")  # newest

    real = H._git

    def fake(repo, *args):
        if args and args[0] == "show" and old_sha in args:
            return "", "fatal: unable to read tree (corrupted, simulated)"
        return real(repo, *args)

    monkeypatch.setattr(H, "_git", fake)

    code = H.main(["--repo", str(r)])
    said = capsys.readouterr().out
    assert code == 3, "a genuine read failure is could-not-look"
    assert "fatal" in said, \
        f"the printed example must name the read failure, not a benign, " \
        f"newer, merely-excluded drop; got:\n{said}"


def test_the_harvested_cases_are_written_in_lf_on_every_platform(tmp_path):
    """T543. The corpus is committed into the adopter's tree, and it was written
    with the platform newline, so a harvest on Windows wrote CRLF and the next
    one on Linux rewrote every line. `sca.py` already wrote LF; these now match."""
    written = H.write_cases(tmp_path, [{"sha": "0123456789abcdef", "title": "t",
                                        "files": ["a.py", "b.py"]}])
    assert written == 1
    (case,) = (tmp_path / ".sdlc" / "corpus").iterdir()
    assert b"\r" not in case.read_bytes()
