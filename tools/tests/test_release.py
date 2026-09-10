"""P8 — publish, open the PR, watch the real CI. Item 47.

The lifecycle's own table says the human *"clicks merge — always"*. This module
is everything up to that click and nothing past it.

The properties worth more than the rest of this file:

**The merge click is enforced, not promised.** A prose rule in an SOP is one an
agent can decide to be pragmatic about. `gh pr merge` does not appear in the
module and a test says so.

**Four of the five verdicts are not green**, and every one of them has been a
real defect shape here. `none` matters most: item 40's SCA gate scanned nothing
and reported nothing for months, because *"nothing ran"* and *"nothing failed"*
produce identical output unless something insists they differ.

**Publishing goes through the constitution's own wall.** `policy.push_target`
decides, not a second opinion written here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import release as R  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, release, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)
    return r


def rows(*specs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "bucket": b, "state": "COMPLETED", "link": ""}
            for n, b in specs]


# ── only one of five answers is a pass ──────────────────────────────────────


def test_all_passing_is_green(repo):
    assert R.verdict(rows(("build", "pass"), ("scan", "pass")))["state"] == R.GREEN


def test_a_failing_check_is_red_and_names_it(repo):
    found = R.verdict(rows(("build", "pass"), ("scan", "fail")))
    assert found["state"] == R.RED and found["failed"] == ["scan"]


def test_a_pending_check_is_not_a_pass(repo):
    """A run that read pending as passing would merge on a promise."""
    found = R.verdict(rows(("build", "pass"), ("slow", "pending")))
    assert found["state"] == R.PENDING and found["pending"] == ["slow"]


def test_no_checks_at_all_is_not_a_pass(repo):
    """**The one this project has paid for twice.** Item 40's SCA gate scanned
    nothing and reported nothing for months. A pull request with no checks is
    not a passing pull request; it is one nobody is checking."""
    found = R.verdict([])
    assert found["state"] == R.NONE
    assert "nothing ran" in found["why"] and "nothing failed" in found["why"]


def test_a_skipped_check_is_not_a_pass(repo):
    """A skipped job ran nothing. Whether that is fine is a question for a
    person, not an answer this phase may give itself."""
    assert R.verdict(rows(("build", "skipping")))["state"] == R.PENDING


def test_a_cancelled_check_is_red(repo):
    assert R.verdict(rows(("build", "cancel")))["state"] == R.RED


def test_a_bucket_this_phase_has_never_seen_is_not_green(repo):
    """The vocabulary gap that let a leaked credential out of item 36's review
    loop: a value no rule anticipated must not fall through to a pass."""
    found = R.verdict(rows(("build", "pass"), ("odd", "some-new-bucket")))
    assert found["state"] == R.UNKNOWN
    assert any("odd" in u for u in found["unknown"])


def test_red_outranks_pending(repo):
    """A failure is actionable now; waiting for the rest would delay the loop
    back to P4 for no gain."""
    assert R.verdict(rows(("a", "fail"), ("b", "pending")))["state"] == R.RED


def test_every_verdict_has_the_same_shape():
    """`found["unknown"]` must not raise on exactly the no-checks answer.

    Every other branch returns `unknown`; `verdict([])` did not, so the record
    written to CI_RESULT had two shapes and a reader indexing it crashed on the
    one this phase exists to make loud.
    """
    keys = {"state", "checks", "failed", "pending", "unknown", "why"}
    for produced in (R.verdict([]), R.verdict(rows(("b", "pass"))),
                     R.verdict(rows(("b", "fail"))),
                     R.verdict(rows(("b", "pending"))),
                     R.verdict(rows(("b", "wat")))):
        assert keys <= set(produced), produced


# ── the REST fallback speaks the reader's vocabulary ────────────────────────
#
# `rest_rows` fills `bucket` from the API's `.conclusion`, and `verdict` reads
# it through `BUCKETS`, which held only the `gh pr checks` words. A REAL API
# payload is the producer in every test below - the shape `gh api --jq` emits,
# one JSON object per line - because a fixture that spells the reader's
# expectation is exactly what hid this for as long as it was hidden.


def _rest_line(name: str, conclusion, status: str = "completed") -> str:
    """One line of what `gh api ... --jq` prints for a check run."""
    return json.dumps({"name": name, "state": status, "bucket": conclusion,
                       "link": f"https://example.invalid/{name}/runs/1"})


def _through_rest(monkeypatch, *lines: str) -> list[dict]:
    """The real `rest_rows` over a real REST payload, gh stubbed at the door.

    `repo_of` is stubbed with the other two. It is not decoration: `rest_rows`
    calls it, and `repo_of` shells out to `git remote get-url` in the target -
    which is `Path(".")` here, i.e. WHEREVER pytest happens to have been
    started. Left real, these tests passed from inside a checkout and failed
    with `no remote named 'origin'` from anywhere else, so their result was a
    fact about the ambient working directory rather than about `rest_rows`.
    """
    monkeypatch.setattr(R, "repo_of", lambda *a, **k: "owner/repo")
    monkeypatch.setattr(R, "_head_sha", lambda *a, **k: "0" * 40)
    monkeypatch.setattr(R, "_gh", lambda *a, **k: (0, "\n".join(lines) + "\n", ""))
    return R.rest_rows(Path("."), 7)


def test_a_failing_check_read_through_the_rest_fallback_is_red(monkeypatch):
    """**The defect this file is here for.** `failure` reached no bucket, so a
    genuine RED came back UNKNOWN with `failed` empty - and an empty `failed`
    is why neither `--logs` nor the route back to P4 could ever fire."""
    found = R.verdict(_through_rest(monkeypatch, _rest_line("build", "failure"),
                                    _rest_line("scan", "success")))
    assert found["state"] == R.RED, found
    assert found["failed"] == ["build"], found
    assert found["unknown"] == [], found


def test_a_healthy_ci_read_through_the_rest_fallback_is_green(monkeypatch):
    """The fallback exists so a healthy CI is not called `none`. It called it
    `unknown`, which is not green either - the same wrong answer one word on."""
    found = R.verdict(_through_rest(monkeypatch, _rest_line("build", "success"),
                                    _rest_line("scan", "success")))
    assert found["state"] == R.GREEN, found


def test_a_queued_check_run_is_pending_not_unknown(monkeypatch):
    """A run that has not finished carries `conclusion: null`. That is a check
    still running, and `.status` says so - not a word this phase cannot read."""
    found = R.verdict(_through_rest(monkeypatch,
                                    _rest_line("slow", None, "in_progress"),
                                    _rest_line("build", "success")))
    assert found["state"] == R.PENDING and found["pending"] == ["slow"], found


def test_rest_conclusions_that_are_not_a_pass_are_not_green(monkeypatch):
    """`cancelled` and `skipped` are the REST spellings of two words this
    module already refuses to call green. Reaching no bucket made them
    `unknown`, which is not-green by accident rather than by rule."""
    assert R.verdict(_through_rest(
        monkeypatch, _rest_line("build", "cancelled")))["state"] == R.RED
    skipped = R.verdict(_through_rest(monkeypatch,
                                      _rest_line("build", "skipped")))
    assert skipped["state"] == R.PENDING, skipped


def test_a_conclusion_this_phase_has_never_seen_is_still_named(monkeypatch):
    """The translation must not become a blanket pass. A word the map does not
    hold goes through UNCHANGED and is named, which is the property `unknown`
    was carrying by accident and now carries on purpose."""
    found = R.verdict(_through_rest(monkeypatch,
                                    _rest_line("odd", "some_new_conclusion")))
    assert found["state"] == R.UNKNOWN
    assert any("some_new_conclusion" in u for u in found["unknown"]), found


@pytest.mark.parametrize("conclusion", ["timed_out", "action_required",
                                        "startup_failure"])
def test_a_rest_run_that_did_not_pass_is_red_and_is_named(monkeypatch,
                                                          conclusion):
    """The three REST spellings of "this did not pass" that nothing exercised.

    A run the clock killed, one waiting on a human, and one whose runner never
    started are all failures the API reports with their own word. Reaching no
    bucket put every one of them in `unknown` with `failed` empty - and an
    empty `failed` is why neither `--logs` nor the route back to P4 can fire,
    which is the same wrong answer `failure` was giving, three words on."""
    found = R.verdict(_through_rest(monkeypatch,
                                    _rest_line("build", conclusion),
                                    _rest_line("scan", "success")))
    assert found["state"] == R.RED, found
    assert found["failed"] == ["build"], found
    assert found["unknown"] == [], found


def test_a_neutral_rest_conclusion_is_not_green(monkeypatch):
    """`neutral` is a check that RAN and declined to judge. It is not a pass,
    by the same rule that keeps `skipping` off green: whether a job that
    judged nothing is fine is a question for a person, not for this phase."""
    found = R.verdict(_through_rest(monkeypatch, _rest_line("build", "neutral")))
    assert found["state"] == R.PENDING, found
    assert found["state"] != R.GREEN, found


def test_the_translation_is_at_the_boundary_not_in_the_reader():
    """One vocabulary reaches the reader. `BUCKETS` must NOT learn REST words:
    a second table keyed on our own vocabulary is the drift item 94 ends, and
    it would silently accept a `gh pr checks` row spelled `success` - a word
    that subcommand does not emit, so one appearing is a producer change worth
    hearing about."""
    assert not set(R.BUCKETS) & set(R.REST_CONCLUSIONS), R.BUCKETS
    assert set(R.REST_CONCLUSIONS.values()) <= set(R.BUCKETS)
    # **The two above are shape, and shape alone is a mirror.** Both hold for
    # ANY self-consistent pair of tables - including one where `success` maps
    # to `fail` - because each derives its expectation from the tables it is
    # checking. So the colour is named here as a LITERAL: exactly one REST
    # conclusion may reach green, and it is spelled `success`. Anything else
    # arriving there is this phase about to report a green it did not earn.
    reach_green = sorted(word for word, bucket in R.REST_CONCLUSIONS.items()
                         if R.BUCKETS[bucket] == R.GREEN)
    assert reach_green == ["success"], reach_green


# ── a git call the clock kills says so, and keeps what it printed ───────────
#
# A REAL `subprocess.TimeoutExpired` is raised inside `cli.run_bounded`, so
# the helper, `cli.noted` and the module's own handling all run for real; only
# the hang is simulated, because there is no portable way to make git hang.


def _hangs(monkeypatch, printed: str = "", timeout: int = 600,
           on_stdout: str = ""):
    """A real `TimeoutExpired` carrying whatever the child had printed.

    `on_stdout` is separate from `printed` because `TimeoutExpired` carries
    BOTH halves and a site that reads one of them loses the other - which is
    the whole of the `err or out` class this module bans by name.
    """
    from tools import cli

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "git",
                                        timeout=k.get("timeout", timeout),
                                        output=on_stdout, stderr=printed)

    monkeypatch.setattr(cli.subprocess, "run", raise_timeout)


def test_a_hung_push_says_the_clock_ended_it_before_anything_else(monkeypatch,
                                                                  repo):
    """**The push wall.** The reason must lead: every reader of this message
    takes a LEADING slice, so a note appended after 400 characters of git's
    output is the part that gets cut - and a push the CLOCK ended becomes
    indistinguishable from one that answered and failed."""
    monkeypatch.setattr(R, "push_problems", lambda *a, **k: [])
    _hangs(monkeypatch, printed="Enumerating objects: 41, done.\n" * 40)
    with pytest.raises(R.ReleaseError) as caught:
        R.push(repo, "run-1", "sdlc/run-1")
    said = str(caught.value)
    assert "did not finish within 600s" in said[:100], said[:200]
    assert "Enumerating objects" in said, said      # what it printed is kept
    assert "is unknown" in said                     # and the operator is told


def test_a_hung_push_does_not_take_the_phase_down_with_it(monkeypatch, repo):
    """It raised `TimeoutExpired` out of P8 entirely, so the phase's own
    refusal prose - the thing that tells an operator what to do - never ran."""
    monkeypatch.setattr(R, "push_problems", lambda *a, **k: [])
    _hangs(monkeypatch)
    with pytest.raises(R.ReleaseError):
        R.push(repo, "run-1", "sdlc/run-1")


def test_a_hung_remote_lookup_is_could_not_look_not_a_missing_remote(
        monkeypatch, repo):
    """ABSENT is not COULD-NOT-LOOK. Folded into the returncode branch, a
    timeout would have been announced as "no remote named 'origin'" - this
    phase telling an operator a remote is absent on the one evidence that
    cannot tell absent from unreadable."""
    _hangs(monkeypatch, printed="", timeout=120)
    with pytest.raises(R.ReleaseError) as caught:
        R.repo_of(repo, "origin")
    said = str(caught.value)
    assert "could not be read" in said and "did not answer within 120s" in said
    assert "no remote named" not in said, said


def test_a_remote_that_really_is_missing_still_says_so(repo):
    """The control that keeps the test above from being satisfied by a module
    that answers "could not look" to everything."""
    with pytest.raises(R.ReleaseError) as caught:
        R.repo_of(repo, "nosuchremote")
    assert "no remote named 'nosuchremote'" in str(caught.value)


def test_a_hung_remote_lookup_keeps_the_half_of_the_url_it_got(monkeypatch,
                                                               repo):
    """**BOTH streams, never one.** `git remote get-url` prints its ANSWER on
    STDOUT, so a site that folds only stderr into the note discards the very
    thing the call went to fetch - and what it keeps instead is git's
    complaint. That is the `err or out` defect spelled as an omission, and it
    lands on the one call where stdout is the whole point."""
    _hangs(monkeypatch, printed="warning: redirecting to a slow mirror",
           timeout=120, on_stdout="https://github.invalid/owner/re")
    with pytest.raises(R.ReleaseError) as caught:
        R.repo_of(repo, "origin")
    said = str(caught.value)
    assert "did not answer within 120s" in said, said        # reason still leads
    assert "owner/re" in said, said                          # the partial ANSWER
    assert "slow mirror" in said, said                       # and the warning


def test_a_hung_push_keeps_what_reached_stdout_as_well(monkeypatch, repo):
    """`git push` writes progress to stderr, but a hook, a credential helper or
    a `push.default` warning writes to STDOUT - and on a push the clock killed,
    every line saying how far it got is the whole evidence there is."""
    monkeypatch.setattr(R, "push_problems", lambda *a, **k: [])
    _hangs(monkeypatch, printed="Enumerating objects: 41, done.",
           on_stdout="remote: hook: publishing to the index")
    with pytest.raises(R.ReleaseError) as caught:
        R.push(repo, "run-1", "sdlc/run-1")
    said = str(caught.value)
    assert "did not finish within 600s" in said[:100], said[:200]
    assert "Enumerating objects" in said, said
    assert "publishing to the index" in said, said


# ── the merge click is a human's, and that is enforced ──────────────────────


def test_the_module_cannot_merge():
    """**Enforced rather than promised.** A prose rule in an SOP is one an agent
    can decide to be pragmatic about; a call the code does not contain is not."""
    import ast

    src = (ROOT / "tools" / "sdlc" / "release.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # An AST sweep, not a word search - item 94's idiom. The first cut banned
    # the WORD and failed on a section header reading "never its merge", which
    # is prose doing its job. What must not exist is an ARGUMENT: a merge is
    # `_gh(["pr", "merge", ...])`, so a string literal "merge" is the thing to
    # refuse, wherever it appears outside a docstring.
    # Docstring nodes are found STRUCTURALLY - the first statement of a module,
    # function or class. Comparing against `ast.get_docstring` does not work:
    # it returns the CLEANED text, which never equals the raw Constant, so the
    # module docstring stayed in the sweep and its own sentence about `gh pr
    # merge` failed the assertion it was describing.
    skip = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            continue
        first = (node.body or [None])[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            skip.add(id(first.value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in skip]
    assert "merge" not in literals, "a `merge` argument is reachable from this module"
    assert not any("pr merge" in s for s in literals)
    # and the sweep must be looking at something, or it passes vacuously
    assert "checks" in literals and "create" in literals


def test_nothing_green_leaves_the_phase(repo):
    for state in (R.RED, R.PENDING, R.NONE, R.UNKNOWN):
        owed = R.problems(repo, "r1", {"state": state, "why": "because"})
        assert len(owed) == 1 and state in owed[0]
        assert "merge click is a human" in owed[0]


def test_green_owes_nothing(repo):
    assert R.problems(repo, "r1", {"state": R.GREEN, "why": "all passed"}) == []


def test_no_result_yet_owes_nothing_here(repo):
    """`stage.verify` says P8 owes its artifact by name. Saying it twice in
    different words teaches people to skim both."""
    assert R.problems(repo, "r1", None) == []


# ── publishing goes through the constitution's wall ─────────────────────────


def test_a_branch_that_is_not_a_run_branch_is_refused(repo):
    owed = R.push_problems(repo, "r1", "my-feature", "origin")
    assert owed and "agents push only to" in owed[0]


def test_a_remote_the_constitution_does_not_name_is_refused(repo):
    owed = R.push_problems(repo, "r1", "sdlc/r1", "somewhere-else")
    assert owed and "somewhere-else" in owed[0]


def test_a_run_branch_to_the_allowed_remote_is_permitted(repo):
    assert R.push_problems(repo, "r1", "sdlc/r1", "origin") == []


def test_a_branch_belonging_to_another_run_is_refused(repo):
    owed = R.push_problems(repo, "r2", "sdlc/r1", "origin")
    assert owed and "another run" in owed[0]


def test_the_branch_comes_from_the_run_record_not_a_caller(repo):
    """The same seam item 37 built and `6a00bfc` made run-sourced: a path a
    caller must get right is one that will eventually be got wrong."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    W.create(repo, run_id)
    assert R.branch_of(repo, run_id) == f"sdlc/{run_id}"


def test_a_run_with_no_worktree_record_names_no_branch(repo):
    """Absent, not guessed. Inventing `sdlc/<id>` here would push a branch that
    may not exist."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    assert R.branch_of(repo, run_id) == ""


# ── could-not-look is never a green CI ──────────────────────────────────────


def test_an_absent_gh_is_refused_not_read_as_green(repo, monkeypatch):
    # A remote, because `repo_of` now runs FIRST - you cannot ask gh about a
    # repository without naming one. Without it this refuses for a different
    # (also correct) reason and stops testing what it is named for.
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/someone/thing.git"], check=True,
                   capture_output=True, timeout=120)
    monkeypatch.setattr(R, "binary", lambda: (None, "not pinned on this platform"))
    with pytest.raises(R.ReleaseError) as exc:
        R.find_pr(repo, "sdlc/r1")
    assert "not the same as CI being green" in str(exc.value)


def test_a_remote_that_is_not_owner_slash_name_is_refused(repo):
    """This phase will not guess which repository to publish to."""
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "/some/local/path"], check=True, capture_output=True,
                   timeout=120)
    with pytest.raises(R.ReleaseError) as exc:
        R.repo_of(repo, "origin")
    assert "will not guess" in str(exc.value)


def test_the_repository_comes_from_the_remote_the_wall_approved(repo):
    """**The field finding.** A fork has two remotes and gh picks one when
    nothing says otherwise - it picked the UPSTREAM."""
    for name, url in (("origin", "https://github.com/mine/thing.git"),
                      ("upstream", "git@github.com:theirs/thing.git")):
        subprocess.run(["git", "-C", str(repo), "remote", "add", name, url],
                       check=True, capture_output=True, timeout=120)
    assert R.repo_of(repo, "origin") == "mine/thing"
    assert R.repo_of(repo, "upstream") == "theirs/thing"


def test_the_cli_refuses_checks_without_a_pr(repo, capsys):
    assert R.main(["--target", str(repo), "--checks"]) == 2
    assert "needs --pr" in capsys.readouterr().err


def test_the_cli_reports_a_refused_push(repo, capsys):
    assert R.main(["--target", str(repo), "--branch", "not-a-run-branch"]) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_the_cli_reports_an_allowed_push_without_making_one(repo, capsys):
    """Reporting is not publishing. The CLI's default path answers the question
    and pushes nothing."""
    before = subprocess.run(["git", "-C", str(repo), "branch", "-a"],
                            capture_output=True, text=True, timeout=120).stdout
    assert R.main(["--target", str(repo), "--branch", "sdlc/r1"]) == 0
    after = subprocess.run(["git", "-C", str(repo), "branch", "-a"],
                           capture_output=True, text=True, timeout=120).stdout
    assert before == after
    assert "allowed" in capsys.readouterr().out


# ── wired into the phase contract, not merely available ────────────────────


def test_p8_owes_its_verdict_through_the_conditional_seam(repo):
    """A CLI nobody runs is a CLI nobody runs. `stage.verify` is what a gate
    stands on, and it must carry this."""
    from tools.sdlc import stage as ST

    assert ST.CI_RESULT in ST.by_phase("P8").conditional
    assert ST.CONDITIONAL_JUDGE["P8"][0] == "tools.sdlc.release"
    assert ST.CI_RESULT == R.RESULT      # one name, asked for rather than spelled


def test_a_run_at_p8_with_no_verdict_owes_one(repo):
    """A branch nobody watched is not a branch whose checks passed."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and R.RESULT in owed[0]
    assert "nobody watched" in owed[0]


def test_an_earlier_phase_owes_nothing_here(repo):
    """A run that has not reached release has no CI to watch, and saying it
    owes one would fire this judge on every run in the pipeline."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    assert R.obligations(repo, run_id) == []


def test_a_recorded_green_verdict_owes_nothing(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text(
        json.dumps({"state": R.GREEN, "why": "all passed"}), encoding="utf-8")
    assert R.obligations(repo, run_id) == []


def test_a_recorded_red_verdict_holds_the_phase(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text(
        json.dumps({"state": R.RED, "why": "1 check failed: build"}),
        encoding="utf-8")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and "red" in owed[0]


def test_an_unreadable_verdict_is_refused_not_read_as_green(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text("{not json",
                                                         encoding="utf-8")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


def test_verify_surfaces_the_owed_verdict(repo):
    """The obligation has to reach the phase machinery, or it is a CLI nobody
    runs."""
    from tools.sdlc import stage as ST

    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / "80-release.md").write_text("released\n",
                                                                encoding="utf-8")
    ledger.record_artifact(repo, run_id, "80-release.md")
    problems = ST.verify(repo, run_id, "P8")
    assert any(R.RESULT in p for p in problems), problems


def test_the_lazy_import_is_declared_in_the_bundle_map(repo):
    """`stage.py` is embedded in the emitted ledger check, so a sibling it
    names must be declared or `render_check` refuses to emit."""
    from tools.sdlc import validate as V

    assert "release" in V.LAZY_OUTSIDE_THE_BUNDLE


def test_push_asks_the_wall_before_it_touches_git(repo):
    """**Found by a surviving mutation.** `push_problems` was tested and `push`
    was tested and nothing composed them, so dropping the wall out of `push`
    reddened nothing - the producer/consumer-never-composed class this project
    keeps meeting.

    The refusal must come from the constitution, not from git failing on a
    missing remote: those are different sentences, and only one of them means
    "you may not publish that".
    """
    with pytest.raises(R.ReleaseError) as exc:
        R.push(repo, "r1", "not-a-run-branch", "origin")
    assert "agents push only to" in str(exc.value)
    assert "git push" not in str(exc.value)
    # and nothing was published: the repository has no remote at all, so a
    # push that reached git would have failed with a different message.
    remotes = subprocess.run(["git", "-C", str(repo), "remote"],
                             capture_output=True, text=True, timeout=120).stdout
    assert remotes.strip() == ""


def test_push_refuses_a_remote_outside_the_constitution(repo):
    with pytest.raises(R.ReleaseError) as exc:
        R.push(repo, "r1", "sdlc/r1", "somewhere-else")
    assert "somewhere-else" in str(exc.value)


# ── every gh call names the repository it means ────────────────────────────


def test_every_gh_call_names_its_repository():
    """**A safety property, found in the field and then found again.**

    `gh` resolves "which repository" from the remotes when nothing says
    otherwise, and a FORK has two: `origin` (yours) and `upstream` (theirs).
    On a real fork with no default set, `gh pr create` resolved to the UPSTREAM
    and refused - one step from opening a pull request against somebody else's
    repository, which is the one thing this phase must never do.

    Five call sites were fixed and `logs_for` was missed, so `gh run view` hit
    the upstream and returned `HTTP 404`, which was handed back AS THE LOG. A
    plausible string that is not the log is worse than an error, because P4
    would act on it.

    So the rule is swept rather than remembered. A call is compliant when it
    passes `--repo`, or when it interpolates `repo_of(...)` into its argument -
    the `gh api` path names the repository inside the URL.
    """
    import ast

    src = (ROOT / "tools" / "sdlc" / "release.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls, offenders = 0, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_gh"):
            continue
        calls += 1
        # The whole call, as source: covers a literal list, a list built into a
        # variable, and an f-string that interpolates the repository.
        text = ast.get_source_segment(src, node) or ""
        target = node.args[0] if node.args else None
        if isinstance(target, ast.Name):        # args built above the call
            text += _assigned_source(src, tree, target.id, node.lineno)
        if "--repo" in text or "repo_of(" in text:
            continue
        # `gh --version` and friends name no repository and need none.
        lits = [e.value for e in getattr(target, "elts", [])
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if lits and lits[0].startswith("-"):
            continue
        offenders.append(f"line {node.lineno}: {lits[:3]}")
    assert calls >= 5, f"the sweep found only {calls} gh call(s) - it is not looking"
    assert offenders == [], (
        "a gh call does not name its repository, so it resolves from the "
        "remotes - and a fork has two: " + "; ".join(offenders))


def _assigned_source(src: str, tree, name: str, before: int) -> str:
    """Source of the last assignment to `name` above `before`."""
    out = ""
    for node in ast.walk(tree) if (ast := __import__("ast")) else []:
        if isinstance(node, (ast.Assign, ast.AugAssign)) and node.lineno < before:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(x, "id", "") == name for x in targets):
                out += ast.get_source_segment(src, node) or ""
    return out
