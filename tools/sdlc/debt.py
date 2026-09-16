"""Tech debt, from close-out back into intake — item 66.

`P11-closeout.md` step 5 has said this since item 36: *"What the codebase got
wrong becomes a tech-debt intake, so it is a run somebody schedules rather than
a paragraph somebody remembers."* **Nothing did it.** It was an instruction to a
runner, and an instruction to write something down by hand is the paragraph it
was trying to replace.

**The debt is already declared; nothing was reading it back.** Three mechanisms
this harness built for other reasons are, between them, a debt register that
nobody consulted:

* `known_failures` — a test in the codebase is red and somebody named it so the
  suite stayed judgeable. That is debt with a name on it.
* `sca_known_findings` — a dependency is vulnerable and somebody accepted it for
  now. Debt with a CVE on it.
* a review finding dispositioned **deferred** — real, and not fixed here.

Each was built so a gate could stay honest about something it was letting past.
Letting something past is exactly what debt is, so the register was written
three times before anything asked it a question.

**Codebase debt, NOT harness learning, and the line is drawn where it is
checkable.** Item 50 already carries what the HARNESS got wrong back through its
own gate. `review_suppress` therefore does not appear here however tempting it
looks: a suppressed finding means the RULE was noisy, which is a fact about the
harness, and filing it as debt in the adopter's tracker would put the harness's
problems in somebody else's backlog. A rejected finding is absent for the same
reason from the other end — somebody decided it was not real, and re-filing it
would be overruling them by machine.

**Deduped against OPEN runs, so a recurring smell files once.** The identity is
`intake.spec_hash` over a deterministic spec, and the duplicate finder is
`intake.duplicates` — the one this package already uses, not a second one. What
this module adds is the policy: a debt item with a run still open is not filed
again, and one whose run CLOSED without the declaration going away is, because
the debt outlived the attempt and saying otherwise would be a tracker that
quietly forgets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import feedback as record  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402

SCHEMA_VERSION = 1

#: Debt is work somebody has to schedule, not a defect with a symptom to
#: reproduce. Filing it as a defect would make item 45's check demand a
#: reproduction for a test that is already, knowingly, red.
KIND = "requirement"

#: Where a debt item can come from. Each is a DECLARATION somebody made so a
#: gate could stay honest; none is inferred from a smell.
SOURCES = ("quarantined-test", "accepted-vulnerability", "deferred-finding")


class DebtError(RuntimeError):
    """The declarations cannot be read, which is not the same as no debt."""


def _components(target: Path) -> list[dict]:
    try:
        return guardrails.components(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        raise DebtError(f"the profile could not be read: {exc}") from None


def _scoped(target: Path, key: str) -> list[tuple[str, str]]:
    """Every declared value under `key`, as `(component, value)` pairs.

    **THE KEY FOLLOWS WHERE THE DECLARATION WAS MADE, NOT WHERE IT WAS READ**,
    and that is the reader's own rule rather than a third one. `suite.quarantined`
    answers per component and falls back to the top level when the component
    declared nothing - so a value that came back through the FALLBACK was
    declared once, at the repository, and is one fact. A value the component
    declared for itself is that component's fact, once per declaring component.

    **Measured on a three-component workspace: two top-level declarations
    produced SIX candidates**, three copies of each carrying one specification
    hash between them and differing only in a field this module never read. On a
    26-component workspace that is 26 tracker rows for one decision, and item 66
    exists so debt becomes a run somebody SCHEDULES.

    The condition is `suite.quarantined`'s own, spelled the same way: a
    per-component value wins when it is present and not blank. Written twice it
    would drift, which is why both quarantine sources call this.
    """
    from tools.sdlc import suite as engine

    seen, pairs = set(), []
    for comp in _components(target):
        own = comp.get(key)
        mine = comp.get("name", "") if not (
            own is None or (isinstance(own, str) and not own.strip())) else ""
        for value in engine.quarantined(Path(target), comp, key):
            if (mine, value) not in seen:
                seen.add((mine, value))
                pairs.append((mine, value))
    return pairs


def _where(component: str) -> str:
    """An item's scope, as a title marker. Empty is the repository, which is the
    fallback's scope and says nothing extra.

    **THE TITLE IS HALF THE IDENTITY, so this marker has to be unforgeable.**
    `intake.duplicates` matches a case-folded TITLE as an alternative to the
    specification hash, and a scope written as prose beside the key is a scope
    the KEY CAN SPELL FOR ITSELF: measured, a repository-level `known_failures`
    entry named `flaky.Test#one in web` produced a title byte-identical to
    `web`'s item for `flaky.Test#one`, and the real matcher answered
    `same title` - two differently-scoped decisions collapsed into one run,
    which is the bug this module was fixed to stop, re-entered through the door
    the fix opened.

    Two properties, and the key's text can reach neither. The marker sits at a
    FIXED POSITION AHEAD OF THE KEY - the key is substituted after the colon, so
    nothing it contains can put a bracket before one. And `]` is DOUBLED where
    the component name goes in, so a name carrying the delimiter cannot close
    the marker early and spell a different component's scope.
    """
    return f" [{component.replace(']', ']]')}]" if component else ""


def quarantined_tests(target: Path) -> list[dict]:
    """Every declared `known_failures` entry, as debt."""
    from tools.sdlc import suite as engine

    out = []
    for component, name in _scoped(target, engine.KNOWN_FAILURES):
        out.append({
            "source": "quarantined-test", "key": name,
            "component": component,
            "title": (f"tech debt{_where(component)}: the quarantined test "
                      f"{name} is still red"),
            "why": (f"`{name}` is declared in `{engine.KNOWN_FAILURES}`, which "
                    f"keeps the suite judgeable and does not make the test "
                    f"pass. A quarantine that outlives its failure is a "
                    f"standing exception nobody re-examines."),
        })
    return out


def accepted_vulnerabilities(target: Path) -> list[dict]:
    """Every declared `sca_known_findings` entry, as debt."""
    from tools.review import sca

    out = []
    for component, entry_text in _scoped(target, sca.QUARANTINE_KEY):
        out.append({
            "source": "accepted-vulnerability", "key": entry_text,
            "component": component,
            "title": (f"tech debt{_where(component)}: accepted vulnerability "
                      f"{entry_text}"),
            "why": (f"`{entry_text}` is declared in `{sca.QUARANTINE_KEY}`, so "
                    f"the supply-chain gate reports it and does not owe it. "
                    f"The declaration names one advisory at one version: "
                    f"bumping the package invalidates it, which is the "
                    f"moment this debt is either paid or renewed."),
        })
    return out


def deferred_findings(target: Path) -> list[dict]:
    """Review findings a human deferred: real, and not fixed here.

    **Deferred only.** A REJECTED finding is one somebody decided was not real,
    and re-filing it as debt would be a machine overruling a person. That
    distinction is the whole reason `hitl.py` records a disposition rather than
    a count.
    """
    out = []
    for group in record.group_corrections(Path(target), floor=1):
        if "deferred" not in (group.get("dispositions") or []):
            continue
        name = group["finding"]
        runs = ", ".join(group.get("runs") or [])
        out.append({
            "source": "deferred-finding", "key": name, "component": "",
            "title": f"tech debt: the deferred review finding {name}",
            "why": (f"`{name}` was DEFERRED by a reviewer in {group['run_count']} "
                    f"run(s) ({runs}) - judged real and not fixed there. A "
                    f"deferral with no run behind it is a decision that expires "
                    f"quietly."),
        })
    return out


def candidates(target: Path) -> list[dict]:
    """Every debt item this repository currently declares, in a stable order."""
    found = (quarantined_tests(target) + accepted_vulnerabilities(target)
             + deferred_findings(target))
    found.sort(key=lambda d: (SOURCES.index(d["source"]), d["key"],
                              d["component"]))
    return found


def spec_for(item: dict) -> str:
    """The intake specification for one debt item.

    **Deterministic**, because `intake.spec_hash` over this text is the
    identity `intake.duplicates` matches on. Anything varying per invocation - a
    timestamp, a run id - would make the same smell a new item every time it was
    looked at, which is the opposite of filing once.

    **The component reaches the identity through the TITLE, and one copy is the
    whole point.** A component may declare a quarantine the repository also
    declares: two facts made in two places, which `_scoped` keeps as two items.
    They carry the same `key`, so an identity blind to the component would hash
    them the SAME and `intake.duplicates` would file one of two real decisions.
    `intake.spec_hash` takes the title AND the body, and `_where` puts the
    component in the title - so a second `component:` line here would be a
    second copy of one fact that changes no answer. **Measured: with that line
    present, deleting it killed nothing**, which is what a redundant copy looks
    like before it drifts. A repository item names no component, so its identity
    is unchanged and an item already filed stays filed.
    """
    return (f"title: {item['title']}\n\n"
            f"kind: {KIND}\n\n"
            f"## What is owed\n\n{item['why']}\n\n"
            f"## Where it came from\n\n"
            f"Filed from close-out by `debt.py`, not written by hand. The source "
            f"is a declaration this repository already carries "
            f"(`{item['source']}`), so this run exists to pay it down or to "
            f"decide out loud that it stays.\n")


def open_runs_for(target: Path, item: dict) -> list[str]:
    """Runs already open for this debt item.

    `intake.duplicates` finds prior runs with the same specification - the one
    duplicate finder this package has - and the policy added here is the only
    new part: a run that CLOSED does not hold the item filed. If the
    declaration is still there after a run went away, the debt outlived the
    attempt.
    """
    from tools.sdlc import connect

    # ONE home for this rule. `verify.py` carried the same eighteen lines, and a
    # third copy is where a policy stops being maintained.
    try:
        return connect.open_runs_for(Path(target), item["title"], spec_for(item))
    except connect.ConnectError as exc:
        raise DebtError(str(exc)) from None


def triage(target: Path) -> tuple[list[tuple[dict, list[str]]], list[dict],
                                  list[tuple[dict, str]]]:
    """Every declared item, split three ways - open, owed, and COULD-NOT-LOOK.

    **Three answers, because `outstanding` can only give two and the third was
    taking the other two down with it.** `open_runs_for` refuses when a
    candidate run's state cannot be read, which is right - a run nobody can read
    does not say the item is unfiled. But `outstanding` sweeps EVERY item, so
    that refusal escaped before the first one was reported, and one damaged run
    belonging to item X answered for items Y and Z as well. Measured: with one
    item's run damaged and a second, unrelated item declared, the door exited 3
    and opened nothing, where the per-item handler in `_main` would have refused
    X and filed Y. The handler was not wrong; it was unreachable.

    That is the same unscoped blast `intake.duplicates` was rebuilt to stop, one
    layer up: a rule hardened where somebody was looking. The scoping in
    `duplicates` decides which RUN could not be read; this decides which ITEM
    that stops us answering about, and without it the first was spent.

    `outstanding` keeps raising, deliberately. `metrics.py` catches it and
    records could-not-look for the whole figure, which is honest for a COUNT -
    a number with one unknown in it is not a number. A door that lists items one
    per line can do better, so it uses this.
    """
    live, owed, unknown = [], [], []
    for item in candidates(target):
        try:
            found = open_runs_for(target, item)
        except DebtError as exc:
            unknown.append((item, str(exc)))
            continue
        if found:
            live.append((item, found))
        else:
            owed.append(item)
    return live, owed, unknown


def outstanding(target: Path) -> list[dict]:
    """Debt with no open run behind it - what close-out should file.

    Raises `DebtError` if any candidate run's state cannot be read: a list of
    what to file is not a place to bury an item nobody could answer about. Use
    `triage` where the answer can be given per item.
    """
    live, owed, unknown = triage(target)
    if unknown:
        raise DebtError(unknown[0][1])
    return owed


def file_one(target: Path, item: dict, allow_dirty: bool = False) -> dict:
    """Open a run for one debt item, through the ordinary front door.

    `intake.open_run`, not a private path: a debt item is a change like any
    other and takes the same gates, and a second way to open a run would be a
    second answer to what a run IS.
    """
    if live := open_runs_for(Path(target), item):
        raise DebtError(f"{item['key']!r} already has an open run "
                        f"({', '.join(live)}) - filing it again would make one "
                        f"smell two backlog items")
    return entry.open_run(Path(target), spec_for(item), kind=KIND,
                          src_kind="debt", src_name=item["source"],
                          allow_dirty=allow_dirty)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--file", action="store_true",
                    help="open a run for each outstanding item; without it "
                         "nothing is written")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    every = candidates(target)
    # `triage`, not `outstanding`: one item nobody can answer about must not
    # answer for the rest. `3` is could-not-look and outranks `1` (debt owed),
    # because a door that exited 1 with an unknown in the list would be saying
    # it had looked.
    already, owed, unknown = triage(target)
    worst = 3 if unknown else (1 if owed else 0)

    if args.json and not args.file:
        print(json.dumps({"schema_version": SCHEMA_VERSION,
                          "candidates": every, "outstanding": owed,
                          "unknown": [{"key": i["key"], "source": i["source"],
                                       "why": why} for i, why in unknown]},
                         indent=2, sort_keys=True))
        return worst

    print(f"{len(every)} debt item(s) declared, {len(owed)} with no open run")
    for item, live in already:
        print(f"  ok  [{item['source']}] {item['key']}  "
              f"(open: {', '.join(live)})")
    for item, why in unknown:
        print(f"  ??  [{item['source']}] {item['key']}: {why}", file=sys.stderr)
    for item in owed:
        print(f"  !!  [{item['source']}] {item['key']}")
    if not args.file:
        if owed:
            print("  --file opens a run for each; nothing was written")
        return worst

    opened = []
    for item in owed:
        try:
            rec = file_one(target, item, allow_dirty=args.allow_dirty)
        except (DebtError, entry.IntakeRefused) as exc:
            print(f"  refused: {item['key']}: {exc}", file=sys.stderr)
            continue
        opened.append(rec["run_id"])
        print(f"  filed {rec['run_id']}  [{item['source']}] {item['key']}")
    print(f"{len(opened)} run(s) opened")
    return 3 if unknown else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look: declarations that cannot be read are
    not an absence of debt."""
    cli.use_utf8()
    return cli.door(_main, argv, "the tech-debt intake", 3)


if __name__ == "__main__":
    raise SystemExit(main())
