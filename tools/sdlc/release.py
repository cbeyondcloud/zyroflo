"""P8 — publish the branch, open the pull request, watch the REAL CI. Item 47.

The lifecycle's own table says it plainly: *"Pushes branch, opens PR with
artifacts, watches real CI… **Clicks merge — always**"*, where the human is the
one clicking. This module is everything up to that click and nothing past it.

**The merge click is not automated, and that is enforced rather than promised.**
`gh pr merge` is never called, and a test asserts the string does not appear in
this file. A prose rule in an SOP is a rule an agent can decide to be pragmatic
about; a gate the code cannot reach is not.

**Three answers are not green, and each of them has been the shape of a real
defect in this project.**

* **red** — a check failed. The phase routes back to P4 **with the log**,
  because "CI is red" is not something P4 can act on and a failing job's output
  is.
* **pending** — a check has not finished. A run that read pending as passing
  would merge on a promise; this is the `not-run` vs `ok` distinction the review
  gates already make.
* **none** — the pull request has NO checks at all. This is the one worth the
  most care: item 40's SCA gate scanned nothing and reported nothing for
  months, and *"nothing ran"* and *"nothing failed"* produce identical output
  unless something insists they are different sentences.

**Publishing goes through the wall, not around it.** `guard.check` decides
whether this remote and this branch may be pushed to, using `policy.push_target`
— the same predicate the constitution is written in. A component whose role is
`fork` is refused there, and this module does not second-guess it.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
#: The phase this module judges. Derived from the stage contract, not spelled.
PHASE = stages.by_name("release").phase

#: The CLI this phase watches CI through. Pinned in the manifest at
#: `needed_by: M5` with this item named in its purpose.
TOOL = "gh"

#: What a pull request's checks add up to. **Only one of these is a pass**, and
#: the other four are kept apart because folding any pair of them together is
#: how a gate starts reporting a green it did not earn.
GREEN, RED, PENDING, NONE, UNKNOWN = "green", "red", "pending", "none", "unknown"

#: `gh pr checks --json ... .bucket` vocabulary, mapped to ours. `skipping` is
#: deliberately NOT a pass: a skipped job ran nothing, and whether that is fine
#: is a question for a person.
BUCKETS = {"pass": GREEN, "fail": RED, "pending": PENDING,
           "skipping": PENDING, "cancel": RED}

#: REST `check-runs[].conclusion` → the `gh pr checks` bucket words above. **A
#: translation at the boundary, deliberately not a second reader table.**
#: `verdict` read `bucket` through `BUCKETS` alone while `rest_rows` filled it
#: from `.conclusion`, so every REST word — `success` included — mapped to
#: nothing and the fallback that exists so a healthy CI is not called `none`
#: called all of it `unknown`. Worse: a genuine `failure` never reached
#: `found["failed"]`, so neither `--logs` nor the route back to P4 could fire.
#: A second table keyed on our own vocabulary would have been the drift class
#: item 94 exists to end; converting where the REST word is PRODUCED keeps one
#: vocabulary reaching one reader, and keeps a gh row spelled `success` — a
#: word `gh pr checks` does not emit — reportable as the producer change it is.
#:
#: Only conclusions whose meaning is unambiguous are translated. `stale` and
#: anything GitHub adds later pass through UNCHANGED, so `verdict` still names
#: them under `unknown` rather than this module guessing a colour for them.
#: `neutral` rides with `skipped`: it ran and declined to judge, which is a
#: question for a person by the same rule that keeps `skipping` off green.
REST_CONCLUSIONS = {"success": "pass", "failure": "fail", "timed_out": "fail",
                    "action_required": "fail", "startup_failure": "fail",
                    "cancelled": "cancel", "skipped": "skipping",
                    "neutral": "skipping"}

#: REST `.status` values that mean the run has not produced a conclusion yet.
#: A null `conclusion` on one of these is not a vocabulary this phase fails to
#: know; it is a check still running, and `.status` is what says so.
REST_RUNNING = frozenset({"queued", "in_progress", "waiting", "requested",
                          "pending"})


class ReleaseError(RuntimeError):
    """The publish path could not be walked, which is never a green CI."""


def binary() -> tuple[Path | None, str]:
    from tools.review import prepass

    return prepass.binary(TOOL)


def _gh(args: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str, str]:
    exe, why = binary()
    if exe is None:
        raise ReleaseError(f"{TOOL} is not available: {why} - CI cannot be "
                           f"watched, which is not the same as CI being green")
    try:
        proc = subprocess.run([str(exe), *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"{TOOL} did not run: {exc}") from None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def repo_of(target: Path, remote: str = "origin") -> str:
    """`owner/name` for a remote, read from git rather than guessed.

    **This is a safety fix, not a convenience.** `gh` resolves "which
    repository" from the remotes when nothing says otherwise, and a FORK has
    two: `origin` (yours) and `upstream` (theirs). Measured on a real fork with
    no default set — the branch was pushed to `origin`, and `gh pr create`
    resolved to the UPSTREAM and refused with *"No commits between master and
    sdlc/<run-id>"*. The refusal was luck. The failure mode it was one step away
    from is opening a pull request against somebody else's repository, which is
    the one thing this phase must never do.

    So every call that names a repository is given one, taken from the remote
    the push wall approved.
    """
    code, out, err, note = cli.run_bounded(
        ["git", "-C", str(target), "remote", "get-url", remote], timeout=120,
        timed_out="git did not answer within {timeout}s")
    # A clock that ran out is COULD NOT LOOK, and it gets its own sentence.
    # Folded into the branch below it would have been announced as "no remote
    # named 'origin'" - this module telling an operator a remote is absent on
    # the one evidence that cannot distinguish absent from unreadable.
    if note:
        # BOTH streams, never `err or out` - this module's own rule, written
        # down at `artifact_version` below and at `suite.py`'s step runner.
        # It bites HARDEST here: the thing this call went to fetch, the URL,
        # is printed on STDOUT, so keeping stderr alone throws away the
        # partial answer and reports only git's complaint about it. `out`
        # goes LAST because the slice is a TAIL, and the tail is the half
        # worth keeping.
        printed = "\n".join(p for p in ((err or "").strip(),
                                        (out or "").strip()) if p)
        raise ReleaseError(f"the remote {remote!r} could not be read: "
                           f"{cli.noted(note, printed[-160:])}")
    if code != 0:
        raise ReleaseError(f"no remote named {remote!r}: "
                           f"{(err or '').strip()[:160]}")
    url = (out or "").strip()
    if url.endswith(".git"):
        url = url[:-4]
    # A GITHUB remote, and nothing else. The first cut took the last two path
    # segments of anything, so a local path like `/some/local/path` became the
    # repository `local/path` - which gh would then fail on with a confusing
    # error rather than this phase refusing with a clear one. `gh` is a GitHub
    # client; a remote it cannot address is a remote this phase cannot publish
    # to, and saying so is the whole job.
    if "://" in url:                       # https://github.com/owner/name
        host, _, rest = url.split("://", 1)[1].partition("/")
    elif "@" in url and ":" in url:        # git@github.com:owner/name
        host, _, rest = url.split("@", 1)[1].partition(":")
    else:
        host, rest = "", url
    host = host.split("@")[-1]             # a userinfo prefix is not the host
    if not host.endswith("github.com"):
        raise ReleaseError(f"remote {remote!r} is {url!r}, which is not a GitHub "
                           f"remote - this phase publishes through `gh` and will "
                           f"not guess where else to send a branch")
    parts = [p for p in rest.split("/") if p]
    if len(parts) != 2:
        raise ReleaseError(f"remote {remote!r} is {url!r}, which is not owner/name "
                           f"- this phase will not guess which repository to "
                           f"publish to")
    return "/".join(parts)


# ── publishing, through the wall ────────────────────────────────────────────


def branch_of(target: Path, run_id: str, component: str = "") -> str:
    """This run's branch, from the record the worktree model already keeps."""
    from tools.sdlc import worktree as trees

    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
    except (trees.WorktreeError, OSError):
        return ""
    entry = homes.get(component) if component else None
    if entry is None and len(homes) == 1:
        entry = next(iter(homes.values()))
    return str((entry or {}).get("branch") or "")


def unpublished_run(target: Path, run_id: str, branch: str,
                    where: Path | None = None) -> list[str]:
    """Whether the branch about to be pushed carries this run's own folder.

    **The requirement is the harness's oldest stated one and no procedure ever
    passed it on.** `state.py`'s module docstring says a run is *"a folder under
    `<target>/.sdlc/runs/<run-id>/`, committed with the code it describes"*, and
    `validate.merge_ready` refuses a merge without it. Nothing between the two
    ever told a runner to commit it - swept across all twelve SOPs, which
    mention committing code and publishing a branch and never the run folder.

    So the gate fired at the most expensive moment available: after a branch was
    published and a pull request opened on a public fork, as a red CI check.
    Asking the same question HERE moves the refusal to before anything leaves
    the machine.

    **It cannot reuse `validate.merge_ready`**, which asks whether the folder is
    on DISK. That is the right question in CI, where the checkout is the branch,
    and no question at all locally, where an uncommitted folder sits in the
    working tree looking exactly like a committed one. This asks git what the
    BRANCH holds.

    Silent when it cannot tell rather than refusing on a guess: a repository
    that is not a git checkout, or a branch git does not know, is somebody
    else's error and blocking a push on it would be this check inventing one.
    """
    rel = ledger.RUNS_REL.as_posix() + "/" + run_id
    cwd = Path(where or target)
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "ls-tree", "--name-only", branch, "--", rel],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    if (proc.stdout or "").strip():
        return []
    return [f"{branch} does not carry {rel}. A run is committed with the code "
            f"it describes, and the merge gate reads the ledger out of the "
            f"branch - so this push would open a pull request CI must refuse. "
            f"Commit the run folder onto this branch first"]


def push_problems(target: Path, run_id: str, branch: str,
                  remote: str = "origin", where: Path | None = None) -> list[str]:
    """Why this push is refused, in the constitution's own words.

    **Through `guard.check`, the ONE door that RECORDS.** This module's own
    docstring says publishing goes through `guard.check` (item 43) - but the
    call went straight to `policy.push_target`, so a refused push left no line
    in `guardrail.jsonl`, and *blocked* without *logged* is half a wall. Routing
    it through the door also injects the component's `role` into the vcs it
    judges, which is what makes the `role: fork` refusal fire at all - the
    direct call passed a vcs block with no role in it.

    **The component the branch belongs to, in a MULTI-component workspace too.**
    `comp = every[0] if len == 1 else None` sent None for every workspace with
    more than one component, so the fork refusal and the component's own branch
    pattern were skipped on exactly the estates that have forks. The branch
    names its component through that component's pattern, so that is how it is
    found; a branch no component's pattern claims falls back to the top-level
    wall rather than guessing one.
    """
    from tools.sdlc import guard

    try:
        rules = guardrails.load(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        return [f"the policy could not be read: {exc}"]
    comp_name = ""
    try:
        every = guardrails.components(Path(target))
        if len(every) == 1:
            comp_name = str(every[0].get("name") or "")
        else:
            for c in every:
                pattern = guardrails.branch_pattern(rules, c.get("vcs"))
                if guardrails.run_of_branch(pattern, branch):
                    comp_name = str(c.get("name") or "")
                    break
    except Exception:
        comp_name = ""
    verdict = guard.check(Path(target), run_id or "", remote=remote,
                          branch=branch, component=comp_name)
    said = [b["why"] for b in verdict.get("blocked", [])
            if b.get("wall") == "push"]
    # Item 121. A SECOND question rather than a second opinion: the wall above
    # decides WHERE a run may publish, and this decides whether what is being
    # published can be merged at all. Both refuse before the push, which is the
    # only moment either is cheap.
    #
    # **Asked of the tree the push will RUN IN, not the workspace root.** `push`
    # runs `git push` from `where` (the run's worktree); asking `unpublished_run`
    # of `target` meant that in a multi-repo estate git ran in the wrong checkout,
    # exited non-zero, and the wall vanished (item 46's could-not-look shape). The
    # tree is resolved from the run's own worktree record when a caller does not
    # name one, so the question lands in the same place the push will.
    tree = where if where is not None else Path(worktree_for(Path(target), run_id))
    return said + unpublished_run(Path(target), run_id, branch, where=tree)


def push(target: Path, run_id: str, branch: str, remote: str = "origin",
         where: Path | None = None) -> dict:
    """Publish the run branch. Refuses first, pushes second."""
    cwd = Path(where or target)
    # The wall is asked of the SAME tree the push runs in, so its
    # `unpublished_run` check reads the checkout git is about to publish rather
    # than the workspace root - the multi-repo defect T126(c) names.
    if blocked := push_problems(Path(target), run_id, branch, remote, where=cwd):
        raise ReleaseError("; ".join(blocked))
    # git, not gh: publishing a branch needs neither the CLI nor a token, and
    # requiring one here would refuse a push on a machine where only CI
    # WATCHING is unavailable.
    code, out, err, note = cli.run_bounded(
        ["git", "-C", str(cwd), "push", "-u", remote, branch], timeout=600,
        timed_out="git push did not finish within {timeout}s")
    # **The push wall itself.** A hung push is the one case where whether
    # anything reached the remote is unknown, so the reason leads and what git
    # managed to print follows it - `cli.noted`'s order, because every reader
    # of this message takes a LEADING slice and the tail is what gets cut.
    if note:
        # BOTH streams here too. `git push` writes its progress to stderr, but
        # a hook, a credential helper or a `push.default` warning writes to
        # STDOUT - and on a push the clock killed, the one line saying how far
        # it got is the whole evidence. Dropping half of it is the same defect
        # `err or out` is banned for, spelled as an omission instead.
        printed = "\n".join(p for p in ((err or "").strip(),
                                        (out or "").strip()) if p)
        raise ReleaseError(f"the push could not be completed: "
                           f"{cli.noted(note, printed[-400:])} - "
                           f"whether {branch!r} reached {remote!r} is unknown; "
                           f"check the remote before pushing again")
    if code != 0:
        raise ReleaseError(f"git push failed: "
                           f"{(err or '').strip().splitlines()[-1:]}")
    return {"remote": remote, "branch": branch, "pushed": True}


# ── the pull request, and never its merge ───────────────────────────────────


def find_pr(target: Path, branch: str, remote: str = "origin") -> dict | None:
    """The open pull request for this branch, or None."""
    code, out, err = _gh(["pr", "list", "--repo", repo_of(Path(target), remote),
                          "--head", branch, "--state", "open",
                          "--json", "number,url,headRefName"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} pr list failed: {err.strip()[:200]}")
    try:
        rows = json.loads(out or "[]")
    except ValueError as exc:
        raise ReleaseError(f"{TOOL} pr list returned unreadable JSON: {exc}") from None
    return rows[0] if rows else None


def open_pr(target: Path, branch: str, title: str, body: str,
            base: str = "", remote: str = "origin") -> dict:
    """Open the pull request. **This is the last outward-facing act of P8** —
    the merge belongs to a human and is not reachable from this module."""
    args = ["pr", "create", "--repo", repo_of(Path(target), remote),
            "--head", branch, "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    code, out, err = _gh(args, Path(target), timeout=600)
    if code != 0:
        raise ReleaseError(f"{TOOL} pr create failed: {err.strip()[:300]}")
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return {"url": lines[-1] if lines else ""}


# ── what CI actually says ───────────────────────────────────────────────────


def check_rows(target: Path, pr: int | str,
               remote: str = "origin") -> list[dict]:
    """Every check on this pull request.

    `gh pr checks` first; **the REST fallback is not decoration** — the
    subcommand's JSON fields have moved between releases, and a phase that
    could only read one shape would report `none` on a repository whose CI is
    running perfectly.
    """
    code, out, err = _gh(["pr", "checks", str(pr), "--repo",
                          repo_of(Path(target), remote), "--json",
                          "name,state,bucket,link"], Path(target))
    if code in (0, 8) and out.strip():          # 8 = checks exist and some failed
        try:
            return [r for r in json.loads(out) if isinstance(r, dict)]
        except ValueError:
            pass
    return rest_rows(Path(target), pr, remote)


def rest_rows(target: Path, pr: int | str,
              remote: str = "origin") -> list[dict]:
    """The same question through the API, for when the subcommand cannot answer."""
    code, out, err = _gh(["api", f"repos/{repo_of(Path(target), remote)}/commits/"
                          f"{_head_sha(target, pr, remote)}/check-runs",
                          "--jq", ".check_runs[] | {name: .name, "
                                  "state: .status, bucket: .conclusion, "
                                  "link: .html_url}"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} api check-runs failed: {err.strip()[:200]}")
    rows = []
    # One check per line as `gh --jq` writes it, its name raw: on the newline, never
    # `splitlines()`, which cut a name holding U+2028 in two and dropped the check (T635).
    for line in (out or "").split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            row["bucket"] = _rest_bucket(row)
        rows.append(row)
    return rows


def _rest_bucket(row: dict) -> str:
    """One REST check-run row's conclusion, said in the reader's vocabulary.

    The whole translation happens HERE, at the boundary where this module
    knows it is holding a REST word, so that exactly one vocabulary ever
    reaches `verdict` and its one `BUCKETS` table.
    """
    conclusion = str(row.get("bucket") or "").strip().lower()
    if not conclusion:
        # `conclusion` is null until the run completes. Reading that as an
        # unknown word would report a CI that is merely still running as one
        # this phase cannot understand; `.status` is what distinguishes them.
        # A COMPLETED run with no conclusion is neither, and is left blank so
        # `verdict` names it rather than this module inventing an answer.
        status = str(row.get("state") or "").strip().lower()
        return "pending" if status in REST_RUNNING else conclusion
    return REST_CONCLUSIONS.get(conclusion, conclusion)


def _head_sha(target: Path, pr: int | str, remote: str = "origin") -> str:
    code, out, err = _gh(["pr", "view", str(pr), "--repo",
                          repo_of(Path(target), remote), "--json", "headRefOid",
                          "--jq", ".headRefOid"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} pr view failed: {err.strip()[:200]}")
    return (out or "").strip()


def verdict(rows: list[dict]) -> dict:
    """What the checks add up to, and **four of the five answers are not green.**

    `none` is the one this project has paid for twice: a gate that scanned
    nothing reported nothing, and *"nothing ran"* read exactly like *"nothing
    failed"*. A pull request with no checks is not a passing pull request; it is
    a pull request nobody is checking.
    """
    if not rows:
        # `unknown` is present here too. Every other path returns it, and a
        # record whose shape depends on which branch produced it is one a
        # reader has to special-case - `found["unknown"]` raising KeyError on
        # exactly the no-checks answer this phase exists to make loud.
        return {"state": NONE, "checks": 0, "failed": [], "pending": [],
                "unknown": [],
                "why": "this pull request has no checks at all - nothing ran, "
                       "which is not the same as nothing failed"}
    failed, pending, unknown = [], [], []
    for row in rows:
        bucket = str(row.get("bucket") or "").strip().lower()
        name = str(row.get("name") or "?")
        mapped = BUCKETS.get(bucket)
        if mapped == RED:
            failed.append(name)
        elif mapped == PENDING:
            pending.append(name)
        elif mapped is None:
            # A bucket this module has never seen. Not green, and named - a
            # vocabulary gap is how item 36's rdjson severities let a leaked
            # credential out of the review loop.
            unknown.append(f"{name} ({bucket or 'blank'})")
    if failed:
        state, why = RED, f"{len(failed)} check(s) failed: {', '.join(failed)}"
    elif unknown:
        state, why = UNKNOWN, (f"check(s) reported a state this phase does not "
                               f"know: {', '.join(unknown)} - not read as a pass")
    elif pending:
        state, why = PENDING, f"{len(pending)} check(s) still running"
    else:
        state, why = GREEN, f"all {len(rows)} check(s) passed"
    return {"state": state, "checks": len(rows), "failed": failed,
            "pending": pending, "unknown": unknown, "why": why}


def logs_for(target: Path, pr: int | str, names: list[str],
             remote: str = "origin") -> dict[str, str]:
    """The failing jobs' output, because *"CI is red"* is not actionable and a
    log is. This is what P4 is handed when the phase routes back.

    **`--repo` here was missed when the other five call sites got it**, and the
    field run caught it one command later: `gh run view` resolved to the
    UPSTREAM and returned `HTTP 404` — which this function then handed back AS
    THE LOG. A plausible-looking string that is not the log is worse than an
    error, because P4 would act on it. Every sweep hardens the line it is
    looking at and leaves its neighbours; this was that class, committed by the
    change that was fixing that class.
    """
    out: dict[str, str] = {}
    for row in check_rows(Path(target), pr, remote):
        if str(row.get("name")) not in names:
            continue
        link = str(row.get("link") or "")
        run_id = link.rstrip("/").split("/runs/")[-1].split("/")[0] if "/runs/" in link else ""
        if not run_id:
            out[str(row.get("name"))] = f"no log link recorded ({link or 'none'})"
            continue
        code, text, err = _gh(["run", "view", run_id, "--repo",
                               repo_of(Path(target), remote), "--log-failed"],
                              Path(target), timeout=600)
        out[str(row.get("name"))] = (text or err or "").strip()[:20000]
    return out


#: Where the CI verdict lands, so `stage.verify` has something to read. A phase
#: whose evidence is only ever printed is a phase nobody can check afterwards -
#: the SCA gate writes `50-sca.json` for the same reason.
#:
#: ASKED FOR, never spelled again. The first cut declared the literal here while
#: `stage.py` declared it too - item 94's exact violation, two spellings of one
#: name - and the equality test I wrote passed because the strings matched,
#: which is what a second spelling looks like right up until one of them moves.
#: The dead-import guard is what caught it: `stages` was imported and unused
#: precisely BECAUSE the name had been duplicated instead of asked for.
RESULT = stages.CI_RESULT


def recorded(target: Path, run_id: str) -> dict | None:
    """The CI verdict this run recorded, or None if it never asked."""
    path = ledger.run_dir(Path(target), run_id) / RESULT
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"{path} could not be read: {exc}") from None
    return found if isinstance(found, dict) else None


def obligations(target: Path, run_id: str) -> list[str]:
    """What P8 owes, for `stage.verify` through the CONDITIONAL_JUDGE seam.

    **Owed once the run is actually AT P8.** A run sitting there with no
    recorded verdict has not watched CI - which differs from watching it and
    finding no checks, and both differ from green. Earlier phases owe nothing
    here, because a run that has not reached release has nothing to watch.
    """
    try:
        found = recorded(Path(target), run_id)
    except ReleaseError as exc:
        return [str(exc)]
    if found is None:
        try:
            at = ledger.phase_of(Path(target), run_id)
        except Exception as exc:
            return [f"{PHASE} could not tell whether this run owes {RESULT}: "
                    f"{exc} - a phase nobody could read is not a run that has "
                    f"not reached {PHASE}"]
        if at != PHASE:
            return []
        return [f"{PHASE} has recorded no CI verdict ({RESULT}) - a branch "
                f"nobody watched is not a branch whose checks passed "
                f"(tools/sdlc/release.py --checks --pr <n> --out <run folder>)"
                ] + cut_problems(Path(target), run_id)
    # BOTH, because P8 owes two different answers: what somebody else's
    # machine said about the branch, and what this phase itself produced.
    return (problems(Path(target), run_id, found)
            + cut_problems(Path(target), run_id))


def problems(target: Path, run_id: str, result: dict | None = None) -> list[str]:
    """What a recorded verdict makes P8 owe. Anything but green holds it."""
    if result is None:
        return []
    state = result.get("state")
    if state == GREEN:
        return []
    return [f"CI is {state}: {result.get('why', 'no reason recorded')} - "
            f"a change does not leave {PHASE} on anything but a green check, "
            f"and the merge click is a human's in every case"]


# ── the release cut: version, changelog, notes, tag ─────────────────────────
#
# **`release.version_command`, `changelog_command`, `tag_command` and
# `notes_from_run` have been in the shipped profile template since item 60, and
# until item 65 NOTHING READ ANY OF THEM.** The P8 SOP told a runner to draft
# them, which is an instruction to do it by hand - the class this project has
# now found five times (`phases[]` before item 36, `gate_profile` before 63, a
# run's `kind` before 45, `deploy_command`'s per-component value before 46).

CUT_RESULT = stages.CUT_RESULT

#: In order, and the order is the point: a changelog is written FOR a version
#: and a tag names one, so a cut that ran them in any other sequence would be
#: describing a release that does not exist yet.
CUT_STEPS = ("version", "changelog", "tag")

#: The command that asks the BUILT ARTIFACT what version it carries.
#: Deliberately NOT a `CUT_STEP`: the steps mint a name and this checks
#: one, so folding it in would make a verification look like another way
#: of producing the thing it verifies. Named `artifact_version_command`
#: and not `verify_command` because P10 already owns "verify" - asking a
#: RUNNING SERVICE - and a second meaning of one word is item 36's
#: vocabulary gap, which cost a leaked credential once.
ARTIFACT_VERSION_DIAL = "artifact_version_command"

#: What a step's command being blank MEANS, per key, because the template does
#: not say the same thing about all three. `version_command` and
#: `changelog_command` blank = the step is SKIPPED. `tag_command` blank = use
#: the plain git default, which the template states in its own comment. Reading
#: one rule onto all three would silently stop tagging.
#:
#: **T501: the default runs WITHOUT a shell, as these words.** It was the string
#: `git tag -a {version} -m "{title}"` run with `shell=True`, and the title is
#: the specification's own text, so a quote in it was read as shell syntax. As
#: an argument list the title is one argument, whatever it holds.
TAG_DEFAULT = ("git", "tag", "-a", "{version}", "-m", "{title}")

#: What bash (as `/bin/sh`) or cmd.exe - the two shells `shell=True` runs a
#: DECLARED command through - read as syntax in SOME quoting context (T501). A
#: template may put `{title}` bare, inside double quotes or inside single ones,
#: and nothing here knows which, so a value is written in only when it holds
#: none of these. Every other character is text to both, including all of
#: them outside ASCII: an accented letter or an em dash is never refused. (The
#: first version allowed only ASCII letters and a little punctuation, so it
#: refused `café` for "a character a shell reads as more than text", which was
#: not true - review F2.)
#:
#: NOT in scope: a SPACE. A bare `{version}` placeholder that becomes `1.0 --x`
#: word-splits into two arguments, which is argument injection, not command
#: injection (review F8). A space is common and legitimate in a title, the value
#: is the run's own, and refusing it would refuse most titles; the shell-syntax
#: set above is what stops a value BREAKING OUT of the command into another one.
_SHELL_SYNTAX = frozenset("\"'`$\\;&|<>()[]{}*?#~!%^")


def _shell_syntax_in(value: str) -> str:
    """The first character of `value` that bash or cmd reads as syntax (a
    control character counts), or "" when every character is text to both."""
    for ch in value:
        if ch in _SHELL_SYNTAX or ord(ch) < 32 or ord(ch) == 127:
            return ch
    return ""


def release_dial(target: Path, key: str) -> str:
    """One `release:` command from the profile, or blank."""
    return str(guardrails.profile_scalar(Path(target), key) or "").strip()


def notes_for(target: Path, run_id: str) -> str:
    """Release notes drafted from the RUN'S OWN ARTIFACTS, which is the whole
    point of `notes_from_run`.

    Everything here is something the run already recorded. Notes assembled from
    anything else would be a summary of what somebody remembers doing, and the
    run folder is the only account of it that was written down at the time.
    """
    try:
        st = ledger.load(Path(target), run_id)
    except Exception as exc:
        raise ReleaseError(f"the run {run_id} could not be read: {exc}") from None
    title = str(st.get("title") or "(untitled)")
    kind = str(st.get("kind") or "change")
    artifacts = sorted((st.get("artifacts") or {}).keys())
    lines = [f"# {title}", "",
             f"One {kind} run, `{run_id}`.", "",
             "## What the run recorded", ""]
    lines += [f"- `{name}`" for name in artifacts] or ["- (nothing recorded)"]
    gates = [h for h in (st.get("history") or [])
             if "approved" in str(h.get("note", "")).lower()]
    lines += ["", "## Gates taken", ""]
    lines += [f"- {h.get('note')} ({h.get('at')})" for h in gates] or \
             ["- (none recorded)"]
    lines += ["",
              "*Drafted from the run's own artifacts. A human approves it by "
              "merging; nothing here was published.*", ""]
    return "\n".join(lines)


def cut(target: Path, run_id: str, level: str = "patch", version: str = "",
        timeout: int = 900) -> dict:
    """Run the declared release steps and record what ran, in order.

    **A step whose command is blank is SKIPPED, and skipped is RECORDED.**
    *Nothing ran* and *nothing failed* are identical output unless something
    insists they differ - item 40's shape, and this phase already met it once in
    item 47's `none` verdict.

    **Nothing here publishes.** A tag may be created locally; pushing it is the
    same act as the merge click, which stays a human's - and `cut` reaches no
    push path at all, which a test asserts by walking this function.
    """
    target = Path(target)
    where = Path(worktree_for(target, run_id))
    steps: list[dict] = []
    found = str(version or "").strip()
    title = ""
    try:
        title = str(ledger.load(target, run_id).get("title") or "")
    except Exception:
        title = ""

    for name in CUT_STEPS:
        declared = release_dial(target, f"{name}_command")
        source = "declared"
        if not declared and name == "tag":
            declared, source = " ".join(TAG_DEFAULT), "default"
        if not declared:
            steps.append({"step": name, "command": "", "source": "skipped",
                          "exit_code": None, "why": "no command is declared, "
                          "so this step did not run"})
            continue
        if name != "version" and not found:
            steps.append({"step": name, "command": declared, "source": source,
                          "exit_code": None,
                          "why": "no version could be determined, so there is "
                                 "nothing to name - the step did not run"})
            continue
        values = {"{level}": level, "{version}": found, "{title}": title or run_id}
        if source == "default":
            argv = [values.get(word, word) for word in TAG_DEFAULT]
            run, shell, command = argv, False, subprocess.list2cmdline(argv)
        else:
            unsafe = [(k, ch) for k, v in values.items()
                      if k in declared and (ch := _shell_syntax_in(v))]
            if unsafe:
                key, ch = unsafe[0]
                steps.append({"step": name, "command": declared, "source": source,
                              "exit_code": None,
                              "why": (f"{key} is {values[key]!r}, and {ch!r} in it is "
                                      f"syntax to bash or cmd, which this declared "
                                      f"command runs through - so it was not filled "
                                      f"in and the step did not run")})
                continue
            command = declared
            for key, value in values.items():
                command = command.replace(key, value)
            run, shell = command, True
        t0 = time.perf_counter()
        code, out, err, note = cli.run_bounded(
            run, shell=shell, cwd=str(where), timeout=timeout,
            timed_out="did not finish within {timeout}s",
            could_not_start="could not be started: {error}")
        steps.append({"step": name, "command": command, "source": source,
                      "exit_code": code, "seconds": round(time.perf_counter() - t0, 1),
                      "stdout": out[-4000:],
                      "stderr": cli.noted(note, err[-4000:])})
        if name == "version" and code == 0 and not found:
            found = _version_from(out)

    notes = ""
    if str(guardrails.profile_scalar(target, "notes_from_run") or "").strip().lower() \
            in ("true", "yes", "1"):
        notes = notes_for(target, run_id)

    # Asked AFTER the steps, because the version is what the steps produce -
    # and asked even when they were all skipped, so a repository that tags by
    # hand still learns whether its artifact agrees.
    artifact = artifact_version(target, run_id, found, timeout=timeout)

    ran = [s for s in steps if s["source"] != "skipped"]
    # RELATIVE to the run root in the committed record (item 120), so 80-cut.json
    # carries no machine path; `where` above stays absolute for the step `cwd`.
    # A tree not under `target` is left absolute - target/<absolute> still
    # resolves it, so could-not-relativise is not a wrong path.
    try:
        where_rel = Path(where).resolve().relative_to(Path(target).resolve()).as_posix()
    except (ValueError, OSError):
        where_rel = str(where)
    row = {
        "schema_version": SCHEMA_VERSION, "run": run_id, "at": _now(),
        "where": where_rel, "level": level, "version": found,
        "steps": steps, "notes": notes, "artifact_version": artifact,
        "ran": len(ran), "skipped": len(steps) - len(ran),
        # **Every step blank plus no notes is not a release that was cut.** The
        # phase would otherwise report a clean cut for having done nothing.
        "cut": bool((ran or notes)
                    and all(s["exit_code"] == 0 for s in ran)),
    }
    path = ledger.run_dir(target, run_id) / CUT_RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return row


def artifact_version(target: Path, run_id: str, named: str,
                     timeout: int = 300) -> dict:
    """What the BUILT ARTIFACT says its version is, against what the cut named.

    **Item 124 — a release cut named a version nothing verified the artifact
    carried.** On item 114's walk the tag and the changelog said `2.9.4` and the
    deployed jar was `2.9.3-SNAPSHOT`: the bump moved the manifest and the
    artifact in hand predated it, so P8 published a true-looking claim about
    something that did not exist. Item 46 made the same argument one phase on -
    P9 asks the deployment tool, P10 asks the service - and this is it at the
    phase that mints the name.

    **The command is DECLARED and this module names no ecosystem** (item 40's
    rule): the adopter says how to ask their own artifact, because a harness
    that knew a jar's manifest from a wheel's metadata would be a harness with
    a list of ecosystems in it.

    **Blank is not silence.** A repository that declares nothing gets
    `checked: False` and a reason, so the cut records the version as UNVERIFIED
    rather than asserting it - which is item 65's own *a skipped step is
    RECORDED, not absent* and item 108's third channel. That is what keeps this
    from being the eighth dead dial: its ABSENCE is reported every run.

    A command that cannot be run, or whose output names no version, is
    `could not look` - never agreement. The distinction this package keeps
    having to make.
    """
    declared = release_dial(target, ARTIFACT_VERSION_DIAL)
    if not declared:
        return {"checked": False, "command": "", "found": "", "agrees": None,
                "why": (f"no `release.{ARTIFACT_VERSION_DIAL}` is declared, so "
                        f"nothing asked the built artifact what version it "
                        f"carries - {named or 'the version'} is the cut's own "
                        f"claim and is UNVERIFIED")}
    if not named:
        return {"checked": False, "command": declared, "found": "",
                "agrees": None,
                "why": "no version was determined, so there is nothing to "
                       "check the artifact against"}
    if "{version}" in declared and (ch := _shell_syntax_in(named)):
        # T501's rule for every declared command: a value is written into a
        # shell command only when the shell reads it as text.
        return {"checked": False, "command": declared, "found": "",
                "agrees": None,
                "why": (f"the version {named!r} holds {ch!r}, which is syntax to "
                        f"bash or cmd, so it was not written into this command - "
                        f"could not look, which is not agreement")}
    where = Path(worktree_for(Path(target), run_id))
    command = declared.replace("{version}", named)
    code, out, err, note = cli.run_bounded(
        command, shell=True, cwd=str(where), timeout=timeout,
        timed_out="did not finish within {timeout}s",
        could_not_start="could not be started: {error}")
    if code != 0:
        # BOTH streams, never `err or out`, and the note in front of them.
        # `err or out` drops stdout whenever anything reached stderr - and a
        # command asked to print a version prints it to STDOUT while stderr
        # carries a warning, so the discarded half was the answer. The 200
        # characters are a LEADING slice, so the reason has to lead.
        printed = "\n".join(p for p in (err.strip(), out.strip()) if p)
        return {"checked": False, "command": command, "found": "",
                "agrees": None, "exit_code": code,
                "why": (f"the command did not answer (exit {code}): "
                        f"{cli.noted(note, printed)[:200]} - could not look, "
                        f"which is not agreement")}
    found = _version_from(out) or _version_from(err)
    if not found:
        return {"checked": False, "command": command, "found": "",
                "agrees": None, "exit_code": code,
                "why": "the command answered and named no version, so the "
                       "artifact was not asked anything this can compare"}
    return {"checked": True, "command": command, "found": found,
            "agrees": found.lstrip("v") == named.lstrip("v"), "exit_code": code,
            "why": ("the artifact carries the version the cut named"
                    if found.lstrip("v") == named.lstrip("v") else
                    f"the cut named {named} and the artifact carries {found} - "
                    f"a tag, a changelog and release notes are about to claim a "
                    f"version nothing was built at")}


def _version_from(out: str) -> str:
    """The version a bump command printed, or blank.

    Deliberately dumb and deliberately narrow: the LAST line that looks like a
    version. Guessing harder would mean knowing each ecosystem's output, and
    this module names no ecosystem. When it cannot tell, it says blank and the
    later steps record that they had nothing to name.
    """
    for line in reversed([ln.strip() for ln in (out or "").splitlines() if ln.strip()]):
        if re.fullmatch(r"v?\d+\.\d+(\.\d+)?([-+][0-9A-Za-z.-]+)?", line):
            return line
    return ""


def worktree_for(target: Path, run_id: str) -> str:
    """The tree this run's change lives in, or the root when it has none.

    Item 46's defect, not repeated: a release cut run from the repository root
    would version and tag the BASE.
    """
    from tools.sdlc import worktree as trees

    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
    except (trees.WorktreeError, OSError, ValueError):
        return str(target)
    for entry in homes.values():
        if isinstance(entry, dict) and str(entry.get("home") or "").strip():
            return str(entry["home"])
    return str(target)


def cut_recorded(target: Path, run_id: str) -> dict | None:
    """What this run recorded about its release cut, or None."""
    path = ledger.run_dir(Path(target), run_id) / CUT_RESULT
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"{path} could not be read: {exc}") from None
    return found if isinstance(found, dict) else None


def cut_problems(target: Path, run_id: str) -> list[str]:
    """What the release cut makes P8 owe."""
    declared = [n for n in CUT_STEPS if release_dial(target, f"{n}_command")]
    if not declared:
        # A repository that declares no release commands is not one whose
        # release failed. `blank = step skipped` is the profile's own wording.
        return []
    try:
        found = cut_recorded(Path(target), run_id)
    except ReleaseError as exc:
        return [str(exc)]
    if found is None:
        return [f"{PHASE} has recorded no release cut ({CUT_RESULT}) though "
                f"{len(declared)} release command(s) are declared - a version "
                f"nobody cut is a release nobody can name "
                f"(tools/sdlc/release.py --cut --run <id>)"]
    if not found.get("cut"):
        bad = [s for s in found.get("steps", []) if s.get("source") != "skipped"
               and s.get("exit_code") != 0]
        if bad and bad[0].get("exit_code") is not None:
            why = f"{bad[0]['step']} exited {bad[0]['exit_code']}"
        elif bad:
            # No exit status: the step did not finish, did not start, or was
            # never run. `exited None` renders that as a number (the rule
            # `verify.deployed` already follows), and the step's own reason is
            # the one thing a person can act on (review F2).
            reason = str(bad[0].get("why") or bad[0].get("stderr") or "").strip()
            why = (f"{bad[0]['step']} did not report an exit code - "
                   f"{reason[:240] or 'no reason was recorded'}")
        else:
            why = "no step ran and no notes were drafted"
        return [f"the release cut did not complete: {why} - the pull request "
                f"has no version, changelog or tag behind it"]
    # **A version the artifact does not carry is a FALSE claim, not a warning.**
    # It reaches P8's obligations so `stage.verify` owes it and G4 stands behind
    # it - the same seam item 47 used for the CI verdict. Only a check that
    # actually LOOKED can fail here: `checked: False` is could-not-look, which
    # is recorded in the cut and never mistaken for disagreement.
    said = found.get("artifact_version") or {}
    if said.get("checked") and said.get("agrees") is False:
        return [f"{said.get('why')} (tools/sdlc/release.py --cut re-runs the check once "
                f"the artifact is rebuilt)"]
    return []


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", metavar="ID")
    ap.add_argument("--branch", metavar="NAME")
    ap.add_argument("--remote", metavar="NAME", default="origin")
    ap.add_argument("--pr", metavar="NUMBER")
    ap.add_argument("--checks", action="store_true",
                    help="read what CI says about the pull request")
    ap.add_argument("--logs", action="store_true",
                    help="with --checks: fetch the failing jobs' output")
    ap.add_argument("--out", metavar="DIR",
                    help=f"write {RESULT} here, so the verdict is evidence "
                         f"rather than a line that scrolled past")
    ap.add_argument("--cut", action="store_true",
                    help="run the declared release steps (version, changelog, "
                         "tag) and draft the notes; nothing is published")
    ap.add_argument("--level", default="patch", metavar="LEVEL",
                    help="fills {level} in version_command")
    ap.add_argument("--version", default="", metavar="V",
                    help="use this version instead of reading one from the "
                         "bump command's output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if args.cut:
        if not args.run:
            print("refused: --cut needs --run", file=sys.stderr)
            return 2
        row = cut(target, args.run, args.level, args.version)
        if args.json:
            print(json.dumps(row, indent=2, sort_keys=True))
        else:
            print(f"cut: {'ok' if row['cut'] else 'INCOMPLETE'} - "
                  f"version {row['version'] or '(none determined)'}, "
                  f"{row['ran']} ran, {row['skipped']} skipped")
            for s in row["steps"]:
                mark = "-- " if s["source"] == "skipped" else (
                    "ok " if s.get("exit_code") == 0 else "!! ")
                print(f"  {mark}{s['step']}: "
                      + (s.get("why") or f"exit {s.get('exit_code')}"))
            if row["notes"]:
                print(f"  notes drafted ({len(row['notes'])} chars), "
                      f"nothing published")
        return 0 if row["cut"] else 1

    if args.checks:
        if not args.pr:
            print("refused: --checks needs --pr", file=sys.stderr)
            return 2
        # T506: `--remote` reaches both. Dropped, they asked `origin`, which on a
        # fork is another repository's pull request under the same number.
        rows = check_rows(target, args.pr, args.remote)
        found = verdict(rows)
        if args.logs and found["failed"]:
            found["logs"] = logs_for(target, args.pr, found["failed"], args.remote)
        if args.out:
            where = Path(args.out)
            where.mkdir(parents=True, exist_ok=True)
            (where / RESULT).write_text(
                json.dumps({"schema_version": SCHEMA_VERSION, **found},
                           indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n")
            print(f"wrote {where / RESULT}")
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True))
        else:
            print(f"CI: {found['state']} - {found['why']}")
            for name, text in (found.get("logs") or {}).items():
                print(f"--- {name} ---\n{text}")
        return 0 if found["state"] == GREEN else 1

    if args.run and not args.branch:
        args.branch = branch_of(target, args.run)
    if not args.branch:
        print("refused: no branch - pass --branch, or --run for a run whose "
              "worktree record names one", file=sys.stderr)
        return 2
    blocked = push_problems(target, args.run or "", args.branch, args.remote)
    if args.json:
        print(json.dumps({"branch": args.branch, "remote": args.remote,
                          "refused": blocked}, indent=2, sort_keys=True))
    else:
        print(f"branch {args.branch} -> {args.remote}: "
              + ("allowed" if not blocked else "REFUSED"))
        for line in blocked:
            print(f"  !! {line}")
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which must never read as a green CI."""
    cli.use_utf8()
    return cli.door(_main, argv, "the release phase", 3)


if __name__ == "__main__":
    raise SystemExit(main())
