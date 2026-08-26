"""The walls, actually called — item 43: blocked, and logged.

Item 35 shipped five predicates that say whether an agent may write here, edit
that, push there, run this, or land a diff this large. Item 43 asked the only
question that matters about them — *is the attack blocked?* — and found that
**every one of them was called by nothing but its own tests.** The tests proved
the predicates were correct. Nothing proved they ran. A guardrail nobody calls
is documentation with a test suite.

So this module is the one door. It applies the walls, refuses, and **records the
refusal**, because "blocked" and "logged" are different properties and the
second is the one an incident review needs. A wall that stops an attack and says
nothing leaves no evidence that anything was ever tried.

**The log must not become the leak.** A blocked secret-exfiltration attempt is
recorded as *what was attempted and which wall stopped it*, never as the bytes
that were being moved: paths are kept, values never are, and the one place a
value could reach the record — a command line — is scrubbed of anything shaped
like a secret before it is written. A guardrail whose audit trail publishes what
it just blocked has moved the leak rather than stopped it.

**Refusals are per-run where a run exists, and repository-wide otherwise.** An
attack during a run belongs to that run's evidence; one outside any run still
belongs to the repository's. The run id is an ARGUMENT, though — this module
reads no run state and imports no ledger. Every refusal is decided from
`policy.yml` and the action being judged, and nothing else. It is worth saying
because the paragraph above reads as though a lookup happens, and because an
unused `from tools.sdlc import state` sat here for exactly as long as nobody
asked: an import is a claim about what a module depends on, and a reader will
believe it whether or not anything exercises it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
LOG_REL = Path(".sdlc") / "metrics" / "guardrail.jsonl"

#: The walls, named. Each is one predicate from item 35 and one question an
#: agent's next action asks.
WALLS = ("worktree", "protected", "budget", "destructive", "push")

#: Anything shaped like a credential is replaced before a command reaches the
#: record. Deliberately greedy: over-redacting a command line costs legibility,
#: under-redacting it costs the secret the wall just refused to move.
_SECRETISH = re.compile(
    r"""(?ix)
    ( (?:--?[a-z0-9_-]*(?:token|key|secret|password|passwd|pwd|auth|cred)[a-z0-9_-]*)
      [=\s]+ ) \S+
    | ( (?:[A-Za-z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|AUTH|CRED)[A-Za-z0-9_]*) = ) \S+
    | \b(gh[pousr]_[A-Za-z0-9]{16,})
    | \b(xox[abps]-[A-Za-z0-9-]{10,})
    | \b(sk-[A-Za-z0-9]{16,})
    | \b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})
    """)


class GuardError(RuntimeError):
    """The question could not be judged, which is never the same as allowed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(text: str) -> str:
    """A command line with anything credential-shaped removed.

    `provider_smoke` learned this at item 29 and the lesson is the same one:
    a value the record never needed is a value the record must not hold.
    """
    def _mask(m: re.Match) -> str:
        flag = m.group(1) or m.group(2)
        return f"{flag}<redacted>" if flag else "<redacted>"
    return _SECRETISH.sub(_mask, text)


# ── the log ──────────────────────────────────────────────────────────────────


def log_path(target: Path) -> Path:
    return Path(target) / LOG_REL


def record(target: Path, row: dict) -> dict:
    """Append one guardrail decision. Refusals always; allowances never — a log
    of everything that was permitted is a log nobody reads."""
    path = log_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return row


def read_log(target: Path) -> list[dict]:
    """Every refusal recorded, in file order, damage marked in place.

    This was the one reader in the tree that never asked whether a parsed row
    was an OBJECT, so a line holding a bare JSON string came back as a `str`
    and every caller's `.get` raised: `--show` died on `row.get('at')` and the
    red-team suite's own filter died on `r.get('allowed', True)`. A refusal
    log that cannot be displayed is a wall whose evidence is unreadable, which
    is most of what a wall is for.
    """
    return [line.row if line.row is not None else {"_unreadable": line.number}
            for line in cli.jsonl_rows(log_path(target))]


# ── the walls ────────────────────────────────────────────────────────────────


def check(target: Path, run_id: str = "", *, paths: list[str] | None = None,
          diff: str | None = None, command: str = "",
          remote: str = "", branch: str = "", component: str = "") -> dict:
    """May an agent do this? Every wall that applies, and every reason it may not.

    One call answers about one intended action. The walls are ANDed: a write
    that is inside the worktree and lands on a protected path is refused, and
    both reasons are given, because an operator who fixes one and retries should
    not discover the second on the next attempt.
    """
    target = Path(target).resolve()
    rules = guardrails.load(target)
    blocked: list[dict] = []

    if paths:
        if not run_id:
            raise GuardError("a write is judged against a run's own worktree, so "
                             "--run is required with --paths")
        for reason in guardrails.confinement(rules, target, run_id, paths):
            blocked.append({"wall": "worktree", "why": reason})
        for reason in guardrails.protected(rules, target, paths):
            blocked.append({"wall": "protected", "why": reason})
    if diff is not None:
        for reason in guardrails.budget(rules, diff):
            blocked.append({"wall": "budget", "why": reason})
    if command:
        for reason in guardrails.destructive(rules, command):
            blocked.append({"wall": "destructive", "why": scrub(reason)})
    if remote or branch:
        vcs = None
        if component:
            vcs = dict(guardrails.component(target, component).get("vcs") or {})
            vcs["role"] = guardrails.component(target, component).get("role", "")
        for reason in guardrails.push_target(rules, remote, branch, run_id, vcs):
            blocked.append({"wall": "push", "why": reason})

    verdict = {
        "schema_version": SCHEMA_VERSION, "at": _now(), "run_id": run_id or "",
        "allowed": not blocked, "blocked": blocked,
        "walls": sorted({b["wall"] for b in blocked}),
        # WHAT was attempted, never the bytes it carried. Paths identify the
        # action; a diff is reduced to its size; a command is scrubbed.
        "attempted": {
            "paths": list(paths or []),
            "diff_lines": None if diff is None else len(diff.splitlines()),
            "command": scrub(command) if command else "",
            "remote": remote, "branch": branch, "component": component,
        },
    }
    if blocked:
        record(target, verdict)
    return verdict


def allowed(verdict: dict) -> bool:
    return bool(verdict["allowed"])


def reasons(verdict: dict) -> list[str]:
    return [f"{b['wall']}: {b['why']}" for b in verdict["blocked"]]


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", default="", metavar="ID",
                    help="the run this action belongs to")
    ap.add_argument("--path", action="append", default=[], metavar="FILE",
                    help="a file the agent intends to write")
    ap.add_argument("--diff", metavar="FILE", help="the change it intends to land")
    ap.add_argument("--command", default="", metavar="CMD",
                    help="a command it intends to run")
    ap.add_argument("--remote", default="", metavar="NAME")
    ap.add_argument("--branch", default="", metavar="NAME")
    ap.add_argument("--component", default="", metavar="NAME")
    ap.add_argument("--show", action="store_true", help="the refusals recorded so far")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()

    try:
        if args.show:
            rows = read_log(target)
            if args.json:
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                damaged = 0
                for row in rows:
                    if "_unreadable" in row:
                        # A line nobody can read is not a refusal, and counting
                        # it as one would overstate what the walls stopped.
                        damaged += 1
                        print(f"  !! line {row['_unreadable']}: not a readable "
                              f"refusal record")
                        continue
                    walls = row.get("walls")
                    print(f"  {row.get('at', '?')}  "
                          f"{row.get('run_id') or '(no run)':<24} "
                          f"{', '.join(walls) if isinstance(walls, list) else '?'}")
                counted = len(rows) - damaged
                print(f"{counted} refusal(s) recorded"
                      + (f", {damaged} line(s) unreadable" if damaged else ""))
            return 0
        diff_text = None
        if args.diff:
            path = Path(args.diff)
            if not path.is_file():
                print(f"!! no diff at {path}")
                return 2
            diff_text = path.read_text(encoding="utf-8", errors="replace")
        if not (args.path or diff_text is not None or args.command
                or args.remote or args.branch):
            print("!! nothing to judge: pass --path, --diff, --command, or "
                  "--remote/--branch")
            return 2
        verdict = check(target, args.run, paths=args.path, diff=diff_text,
                        command=args.command, remote=args.remote,
                        branch=args.branch, component=args.component)
        if args.json:
            print(json.dumps(verdict, indent=2, sort_keys=True))
        else:
            for reason in reasons(verdict):
                print(f"!! {reason}")
            print("allowed" if allowed(verdict)
                  else f"BLOCKED by {', '.join(verdict['walls'])} - recorded in "
                       f"{LOG_REL.as_posix()}")
        return 0 if allowed(verdict) else 1
    except (GuardError, guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
