"""ADR triggers — item 64: the decision record fires on evidence, not on taste.

`LIFECYCLE.md` and the P3 SOP both say an ADR is owed when a change adds a
dependency, crosses a service boundary or alters a data model. Item 36 put the
artifact in the stage contract as `conditional` — and then pinned, in a test
named for it, that it *is asked for and never required*. Which is the shape
this plan set keeps finding: the obligation existed, the wording was right, and
the only thing standing behind it was the agent's own opinion of whether a
trigger had fired. An agent that does not want to write an ADR does not have to
notice one.

So the trigger is a lookup here. The design artifact names the files it intends
to change; the graph already knows what facts each of those files DECLARES,
with `source_file:line` provenance behind every one. A file that declares a
dependency, a contract or a table is a file whose change moves something other
components can see, and that is the trigger — read off the same edges the blast
radius is computed from, so it cannot disagree with them.

**Three things this refuses to do quietly.**

*Judge without looking.* Without a built graph there is no evidence and the
detector says so, loudly, and blocks nothing — a checker that cannot run must
not report clean. And with a graph, a design that names **no file this
workspace contains** is itself the finding: "no triggers fired" out of a
document that named nothing is a clean bill of health from a check that looked
at nothing, which is the false green this harness has now found four times.

*Rule by omission.* Every edge type the traversal table knows carries a ruling
here — a trigger kind, or the empty string meaning *considered, not a trigger*.
A type nobody ruled on is reported as unruled rather than silently passing, and
a test holds this map against `rules.DEFAULT_TABLE` so a new coupling type
cannot enter the graph without someone deciding whether it is worth a record.

**What it will NOT do, ruled rather than left open (item 124, observation O1).**
A trigger is FILE-granular: any change to a file that declares a boundary fires
a boundary trigger, so a controller touched to fix a log line fires the same
trigger as one whose contract moved, and dismissal becomes routine — which is
how a real check gets switched off, `prepass.py`'s own recorded rule.

It cannot be finer HERE, and the reason is structural rather than an omission:
**P3 produces `30-design.md` and the change first exists at P4** as
`40-impl/change.patch`. At the phase where the trigger must fire there is no
diff, so *a file the design names* is the finest resolution available. Moving
the trigger to P4 would make it precise and useless: G2 stands before
implementation, which is the whole point of asking at P3.

So the cost is real and is paid on purpose. What would change the ruling is a
design that named the DECLARATIONS it intends to move rather than the files —
which is a change to what P3 writes, not to how this reads it.

*Force filler.* The SOP's warning is real: demanding an ADR per trigger teaches
people to write empty ones, and an ADR corpus with filler in it is worse than a
thin one. A trigger is therefore ANSWERED, not counted — one record may answer
several, and a trigger may be DISMISSED outright with a stated reason and a
name against it. What cannot happen is silence: an unanswered trigger is
something P3 owes, so `stage.verify` reports it and G2 has nothing to stand on
until it is either recorded or refused.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.graph import impact  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import prose  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

#: The phase that owes the records. DERIVED from the stage contract so this
#: module and `stage.py` cannot drift apart — which is what the previous comment
#: here claimed while a hardcoded "P3" sat under it.
PHASE = stages.by_name("design").phase

#: Where a dismissal is written, inside the run folder. A jsonl append rather
#: than a field in `state.json`, for the reason item 32 gives about approvals:
#: a decision that overrides a control is a record with a name and a time on
#: it, not a flag someone set.
DISMISSED = "30-adr-dismissed.jsonl"

#: The line an ADR states its triggers on, and the one it states its rejected
#: alternatives on. Both are STATED in the SOP — a requirement nobody states
#: cannot be met, which item 33 paid for twice.
ANSWERS_MARK = "**Answers**"
REJECTED_MARK = "**Rejected**"

#: A ruling for every edge type the graph mints. The value is the trigger kind,
#: or "" for *considered, and not a trigger*. The distinction is the point:
#: `DG_TESTS` not firing is a decision, and a type absent from this map is
#: nobody's decision at all.
EDGE_RULING: dict[str, str] = {
    "DG_DEPENDS_ON": "dependency",
    "DG_BUILT_FROM": "dependency",
    "DG_CALLS": "boundary",
    "DG_CONSUMES": "boundary",
    "DG_EXPOSES": "boundary",
    "DG_PRODUCES": "boundary",
    "DG_SPECIFIES": "boundary",
    "DG_OWNS_TABLE": "data-model",
    "DG_WRITES_TABLE": "data-model",
    # Ruled, and not triggers. Configuring a service, testing it, deploying it
    # or living in a repository are all facts a change can move without moving
    # anything another component compiles, calls or reads.
    "DG_CONFIGURES": "",
    "DG_TESTS": "",
    "DG_DEPLOYS": "",
    "DG_IN_REPO": "",
    "DG_ALIAS_OF": "",
}

KINDS = ("dependency", "boundary", "data-model")

WHY = {
    "dependency": "changes what this component builds against, or what it "
                  "publishes as, so every dependent inherits the decision",
    "boundary": "changes a contract another component reaches across — the "
                "shape of a call, a topic or a published spec",
    "data-model": "changes the shape or ownership of stored data, which "
                  "outlives every service that reads it",
}

#: How many provenance lines one trigger shows before it says how many it kept
#: back. Elision is counted out loud, the rule `pack.py` already follows.
EVIDENCE_SHOWN = 5

#: A backticked token is a path if it has a separator or a suffix and no
#: whitespace. Deliberately generous — `locate` resolves against the real tree,
#: so a false candidate costs a `is_file()` call and a true one is never lost
#: to a pattern that tried to be clever about what a filename looks like.
_TICKED = re.compile(r"`([^`\n]{2,240})`")

#: A line that DECLARES a change set — item 123, and item 100's rule in a second
#: module. `defect.py` carries the same shape for regression tests, and the
#: vocabulary here is the P3 SOP's own: it asks for *a change set per component*,
#: so "change set" is the one spelling the reader knows and the one the SOP
#: teaches. A test holds the two together, because a reader enforcing a word the
#: procedure never states is item 33's class.
_DECLARES_CHANGES = re.compile(
    r"^(?:#{1,6}\s+.*\bchange set\b"
    r"|\s*(?:[-*]\s*)?(?:\*\*)?change set(?:\*\*)?\s*[:—-])",
    re.I)

#: A markdown list entry. What a declared change set is MADE of — prose inside
#: the section is prose, which is the whole discrimination this item adds.
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

#: Markdown's deepest heading. A label-declared change set sits at this level,
#: so every heading closes it.
_MAX_HEADING = 6

#: Any heading. Ends a change set when it is at the declaring level or shallower.
_HEADING = re.compile(r"^#{1,6}\s+")


class AdrError(RuntimeError):
    """A trigger record that cannot be written, or a dismissal that cannot
    stand."""


class Trigger(NamedTuple):
    id: str
    kind: str
    component: str
    path: str
    evidence: tuple[str, ...]
    #: Provenance lines beyond `EVIDENCE_SHOWN`, kept out of the rendering.
    elided: int = 0

    @property
    def why(self) -> str:
        return WHY[self.kind]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the graph, or an honest absence ──────────────────────────────────────────


def graph_dir(target: Path) -> Path:
    return Path(target) / ".sdlc" / "graph"


#: One parsed graph per build. `verify` runs on every gate check and every
#: phase move, and an estate's graph is tens of thousands of rows — re-reading
#: it per call is the shape item 23 measured out of `pack.py`. The key carries
#: the file's stamp as well as its path, so a rebuild is picked up without
#: anyone having to remember to clear anything.
_PARSED: dict[tuple[str, int, int], tuple[list[dict], list[dict]]] = {}


def load(target: Path) -> tuple[list[dict], list[dict]]:
    """The built graph as (nodes, edges), parsed once per build.

    The absence check is HERE rather than in each caller. `obligations` and
    `report` both asked `graph_ready` first, so this was only ever reached
    with a graph present — and then `dismiss` reached it without one and died
    as a `FileNotFoundError` traceback, which the CLI did not catch because an
    `OSError` is not a `RuntimeError`. A precondition every caller has to
    remember is one the next caller will forget.
    """
    path = graph_dir(target) / "graph.jsonl"
    if not graph_ready(target):
        raise AdrError(
            f"there is no built graph at {graph_dir(target)}, so no fact can be "
            f"looked up and no trigger can fire - build one, or accept that this "
            f"workspace's ADR triggers are not evaluated")
    stamp = path.stat()
    key = (str(path.resolve()), stamp.st_mtime_ns, stamp.st_size)
    if key not in _PARSED:
        _PARSED.clear()
        try:
            _PARSED[key] = impact.load_graph(graph_dir(target))
        except impact.GraphError as exc:
            # The docstring above says the absence check is here so no caller
            # has to remember it. A DAMAGED graph was not covered by that -
            # it went past every `AdrError` handler as a `JSONDecodeError`,
            # and this CLI's exit 1 means "there are obligations", so a
            # truncated build reported ADR obligations that do not exist.
            raise AdrError(str(exc)) from exc
    return _PARSED[key]


def graph_ready(target: Path) -> bool:
    """Whether there is a BUILT graph, not merely the directory a build would
    write into. `stage.capable` asks the second question because scoping
    activates on intent; this one is about evidence, and an empty directory
    supplies none."""
    path = graph_dir(target) / "graph.jsonl"
    return path.is_file() and path.stat().st_size > 0


# ── what the design says it will change ──────────────────────────────────────


def change_set_lines(text: str) -> list[str]:
    """The lines a design DECLARES its change set on.

    **Item 123 — item 100's finding in a second module.** This used to be the
    whole document, so every backticked token anywhere was read as a file the
    change would touch, and a design citing its own sibling artifacts in the
    ordinary house style named `20-scope.md`, `40-repro.md` and
    `10-requirements.md` as files. `defect.py` had exactly this reader, and item
    100 recorded what it cost there: *the only way past was to strip backticks
    off every legitimate cross-reference, so the check made documents worse*.

    **A change set is a section, not a line**, which is why this cannot copy
    `defect.py` outright — that bounds a declaration at ONE following line,
    right for a short list of test names and wrong for a per-component change
    set. So: from a declaring line to the next heading, and inside that span
    only LIST entries count. Prose in the section is prose.

    **Prose is SKIPPED rather than treated as the end.** Erring toward reading
    more is the safe direction for this particular check, because it REPORTS
    and does not oblige — item 64 made the ADR trigger a lookup and an
    unresolved name is a finding, never an obligation. Under-reading would be
    the silent direction, and silence here is an ADR nobody was asked for.

    A document that declares nothing yields nothing, which is already a stated
    finding: *a clean bill of health from a check that looked at nothing*.
    """
    # Item 149: a fenced block is what the document SHOWS, not what it says.
    # A `diff` sample's `-`/`+` lines are list items, and the P3 SOP teaches
    # this whole shape inside a fence.
    lines = prose.unfenced(text)
    out: list[str] = []
    depth: int | None = None          # None = not inside a change set
    for line in lines:
        heading = _HEADING.match(line)
        if _DECLARES_CHANGES.match(line):
            # A heading declares at ITS level. A label declares at the DEEPEST
            # level there is, so any heading at all closes it - `**Change
            # set:** ...` owns the lines under it and nothing past the next
            # section.
            depth = len(heading.group(0).strip()) if heading else _MAX_HEADING
            out.append(line)          # `**Change set:** `a`, `b`` is one line
            continue
        if depth is None:
            continue
        if heading:
            # **A DEEPER heading is part of the change set, not the end of it.**
            # `## Change set` followed by `### orders-api` is the per-component
            # shape the SOP asks for, and ending at any heading cut every
            # component's list off - found by the cross-repo build test, which
            # had been writing exactly that shape since before this item.
            if len(heading.group(0).strip()) <= depth:
                depth = None
            continue
        if _LIST_ITEM.match(line):
            out.append(line)
    return out


def named_paths(text: str) -> list[str]:
    """Every backticked token the design DECLARES as a path, in order.

    Backticks are the requirement and the SOP states it; since item 123 the
    declaration is too. Mining prose for bare filenames finds `pom.xml` inside
    a sentence about Maven and misses `src/main/java/Order.java` at the end of
    one; a convention the writer is told about beats a heuristic that guesses
    at their punctuation.
    """
    out: list[str] = []
    for raw in _TICKED.findall("\n".join(change_set_lines(text))):
        token = raw.strip().rstrip(",;:")
        if not token or any(c.isspace() for c in token):
            continue
        token = token.replace("\\", "/")
        while token.startswith("./"):
            token = token[2:]
        if not token or token.startswith("-"):
            continue
        if token not in out:
            out.append(token)
    return out


def path_shaped(token: str) -> bool:
    """Whether an UNRESOLVED token is worth reporting as a file this workspace
    does not have. Resolution is the real test of a path - this only judges
    what already failed it, and keeps `pytest -q`'s neighbours out.

    **Item 141: a path SEPARATOR, because a dotted identifier is not a file.**
    A dot alone made `Pattern.CASE_INSENSITIVE`, `Math.max`, `Matcher.find`
    and `com.example.Order` path-shaped, and a change set names members that
    way constantly - in Java, Python, C# or JS - so an ordinary design was
    reported as naming a file this workspace does not have, about a constant.
    Seen on the first P3 ever walked, and the runner did not correct the tool:
    it spent a paragraph explaining the token away INSIDE the artifact a gate
    pins. That is item 100's recorded cost arriving in a new shape - there the
    only way past was to strip backticks off legitimate cross-references,
    making documents worse; here a report nobody believes gets an apology
    written around it, which is `prepass.py`'s own rule about unusable checks.

    **A bare `Word.word` is undecidable by shape and that is why the rule is
    the separator rather than a cleverer dot.** `Order.java` and `Math.max`
    are the same token to anything that does not know a table of source
    extensions - and this module names no ecosystem, for item 40's reason. So
    the rule is the one the P3 SOP already teaches: name every file
    WORKSPACE-RELATIVE, and its own worked example carries the separator.

    It also makes the rule CONSISTENT with what it already dropped. Measured:
    `Dockerfile` and `Makefile` carry neither a dot nor a slash and were never
    reported, while a bare `Order.java` was - so the loss below is not
    introduced here, it is made uniform.

    **The limit, stated rather than hidden**: a design naming a brand-new
    ROOT-LEVEL file by bare name loses its "named but not in the workspace"
    line. Nothing that EXISTS is affected - a file resolves however it is
    spelled, and item 64's finding that a design naming no file this workspace
    contains is itself a finding is carried by `resolved` being empty.

    A Windows spelling is already normalised to `/` by `named_paths`, so one
    separator is the whole rule rather than two.
    """
    return "/" in token


def locate(target: Path, token: str) -> list[tuple[str, str]]:
    """Every `(component, component-relative path)` a named token resolves to.

    Two spellings are accepted because both are natural: a path qualified by
    its component (`orders-api/pom.xml`) and one relative to it (`pom.xml`).
    A qualified hit wins outright — it says which component it means. An
    unqualified token that hits several is returned as several, and the caller
    reports the ambiguity rather than picking, because attributing a change to
    the wrong component produces evidence that is confidently about the wrong
    file.
    """
    root = Path(target)
    qualified: list[tuple[str, str]] = []
    bare: list[tuple[str, str]] = []
    for comp in guardrails.components(root):
        name = comp["name"]
        rel = (comp.get("path") or ".").replace("\\", "/").strip("/")
        base = root if rel in ("", ".") else root / rel
        prefix = "" if rel in ("", ".") else rel + "/"
        if prefix and token.startswith(prefix):
            inner = token[len(prefix):]
            if inner and (base / inner).is_file():
                qualified.append((name, inner))
            continue
        if (base / token).is_file():
            (qualified if not prefix else bare).append((name, token))
    return qualified or bare


class ChangeSet(NamedTuple):
    #: `(component, path)` pairs the design named and this workspace contains.
    resolved: tuple[tuple[str, str], ...]
    #: Tokens that resolve under more than one component, with their hits.
    ambiguous: tuple[tuple[str, tuple[str, ...]], ...]
    #: Backticked path-shaped tokens this workspace does not contain. Not a
    #: finding on their own — a design names files it is about to CREATE, and
    #: a file that does not exist yet declares nothing either way.
    unknown: tuple[str, ...]


def change_set(target: Path, run_id: str) -> ChangeSet:
    """The design's named files, resolved against the workspace."""
    design = stages.by_phase(PHASE).produces[0]
    path = ledger.run_dir(target, run_id) / design
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    resolved: list[tuple[str, str]] = []
    ambiguous: list[tuple[str, tuple[str, ...]]] = []
    unknown: list[str] = []
    for token in named_paths(text):
        hits = locate(target, token)
        if not hits:
            if path_shaped(token):
                unknown.append(token)
        elif len(hits) == 1:
            if hits[0] not in resolved:
                resolved.append(hits[0])
        else:
            ambiguous.append((token, tuple(sorted(c for c, _ in hits))))
    return ChangeSet(tuple(resolved), tuple(ambiguous), tuple(unknown))


# ── what those files declare ─────────────────────────────────────────────────


def declared_facts(target: Path,
                   files: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], list[dict]]:
    """The edges each named file declared, keyed by `(component, path)`.

    A provenance lookup over the same `source_file` field the impact prototype
    seeds from, using the same owner resolution — one implementation of "which
    component declared this", so a trigger and a blast radius can never
    disagree about whose file it is.
    """
    nodes, edges = load(target)
    nodes_by_id = {n["id"]: n for n in nodes}
    known = impact.known_components(nodes)
    wanted = set(files)
    out: dict[tuple[str, str], list[dict]] = {}
    for edge in edges:
        key = (impact.edge_owner(edge, nodes_by_id, known),
               str(edge.get("source_file", "")).replace("\\", "/"))
        if key in wanted:
            out.setdefault(key, []).append(edge)
    return out


def _fact(edge: dict) -> tuple[int, str, str, str]:
    """One declaration, as a sortable tuple. Sorting the RENDERED string put
    line 12 above line 2, which reads as a provenance bug to anyone checking
    the citation against the file."""
    return (int(edge.get("line", 0) or 0), str(edge.get("source_file", "")),
            str(edge["type"]), str(edge["dst"]))


def _evidence(fact: tuple[int, str, str, str]) -> str:
    line, source_file, edge_type, dst = fact
    return f"{source_file}:{line} declares {edge_type} → {dst}"


def triggers(target: Path, run_id: str) -> tuple[list[Trigger], list[str]]:
    """Every ADR trigger the design's change set fires, and every edge type it
    declared that nobody has ruled on."""
    changes = change_set(target, run_id)
    facts = declared_facts(target, changes.resolved)
    found: dict[str, list[tuple[int, str, str, str]]] = {}
    seen: dict[str, Trigger] = {}
    unruled: set[str] = set()
    for (component, path) in changes.resolved:
        for edge in facts.get((component, path), []):
            kind = EDGE_RULING.get(edge["type"])
            if kind is None:
                unruled.add(edge["type"])
                continue
            if not kind:
                continue
            key = f"{kind}:{component}/{path}"
            found.setdefault(key, []).append(_fact(edge))
            seen[key] = Trigger(key, kind, component, path, ())
    out: list[Trigger] = []
    for key in sorted(found):
        facts = sorted(set(found[key]))
        base = seen[key]
        out.append(base._replace(
            evidence=tuple(_evidence(f) for f in facts[:EVIDENCE_SHOWN]),
            elided=max(0, len(facts) - EVIDENCE_SHOWN)))
    return out, sorted(unruled)


# ── what answers them ────────────────────────────────────────────────────────


def adr_files(target: Path, run_id: str) -> list[Path]:
    pattern = stages.by_phase(PHASE).conditional[0]
    folder = ledger.run_dir(target, run_id)
    return sorted(p for p in folder.glob(pattern) if p.is_file())


def cited(path: Path) -> list[str]:
    """The trigger ids one ADR says it answers."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith(ANSWERS_MARK):
            out += [t.strip() for t in _TICKED.findall(line) if t.strip()]
    return out


def dismissals(target: Path, run_id: str) -> dict[str, dict]:
    """Trigger id → the dismissal record that refused it. The LAST record for
    an id wins, so a reason can be corrected by stating a better one rather
    than by editing the file."""
    path = ledger.run_dir(target, run_id) / DISMISSED
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    # errors="replace": this ledger holds reasons a HUMAN typed, and a byte
    # from a cp1252 editor used to raise UnicodeDecodeError from the `for`
    # statement itself - outside the guard below, and a ValueError that
    # this CLI's handler does not catch, so it exited 1, which here means
    # "there are obligations". The bad bytes now reach the JSON parse and
    # come back as the line number that carries them.
    for n, line in enumerate(path.read_text(encoding="utf-8",
                                            errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AdrError(f"{path}:{n} is not readable JSON: {exc}") from None
        if isinstance(row, dict) and row.get("trigger"):
            out[str(row["trigger"])] = row
    return out


def dismiss(target: Path, run_id: str, trigger_id: str, because: str,
            by: str = "") -> dict:
    """Record that a fired trigger is deliberately not getting an ADR.

    The escape hatch, and it is one on purpose — the alternative to a stated
    refusal is not rigour, it is a filler record written to clear a check. It
    is refused for an id that did not fire (a dismissal that answers nothing
    is a typo wearing a decision's clothes), for a blank reason, and for
    nobody: "a control that cannot be evaluated has not been satisfied" is
    item 35's rule and an unattributed override cannot be evaluated.
    """
    from tools.sdlc import approve as approvals

    reason = " ".join(str(because or "").split())
    if not reason:
        raise AdrError(
            f"dismissing {trigger_id} needs a reason - the record IS the reason, "
            f"and a dismissal with none is indistinguishable from forgetting")
    fired, _ = triggers(target, run_id)
    if trigger_id not in {t.id for t in fired}:
        raise AdrError(
            f"{trigger_id!r} is not a trigger this design fired - "
            + (f"the ones it did: {', '.join(t.id for t in fired)}" if fired
               else "it fired none, so there is nothing to dismiss"))
    who = approvals.who(Path(target), by or None)
    if not who:
        raise AdrError(
            f"dismissing {trigger_id} needs a name against it: pass --by, or set "
            f"git config user.email in the target - an override attributed to "
            f"nobody cannot be evaluated, and an unevaluable control has not "
            f"been satisfied")
    row = {"schema": SCHEMA_VERSION, "trigger": trigger_id, "because": reason,
           "by": who, "at": _now(), "run": run_id}
    path = ledger.run_dir(target, run_id) / DISMISSED
    ledger.append_record(path, json.dumps(row, sort_keys=True) + "\n")
    # Pinned like any other artifact of the run: item 32 hashes what a gate is
    # about, and an override that can be rewritten after the yes is an override
    # nobody approved.
    ledger.record_artifact(Path(target), run_id, DISMISSED)
    return row


# ── the obligation ───────────────────────────────────────────────────────────


def naming_problem(target: Path) -> str:
    """Why no lookup here can hit, when the profile and the graph disagree
    about what the components are called.

    The failure this catches is silence. Resolution finds the file on disk
    through the profile's component PATHS; the fact lookup then asks the graph
    for it under the profile's component NAME. Nothing ever required the two
    spellings to agree, and where they do not every lookup misses and the
    answer is zero triggers — correct-looking, and wrong. Item 37's defect
    exactly: `validate.py` held a hardcoded branch prefix while a repository
    renamed its run branches, so the installed gate stayed green and never
    asked the question it was installed to ask.
    """
    graph_names = {n for n in impact.known_components(load(target)[0]) if n}
    declared = {c["name"] for c in guardrails.components(target)}
    if not graph_names or not declared or (graph_names & declared):
        return ""
    return (f"the profile names its components {', '.join(sorted(declared))} and "
            f"the graph names them {', '.join(sorted(graph_names))} - not one name "
            f"is shared, so every fact lookup misses and 'no trigger fired' would "
            f"be the answer whatever the design said. Rebuild the graph from this "
            f"workspace, or align the names")


def obligations(target: Path, run_id: str) -> list[str]:
    """What P3 still owes on account of its triggers, as sentences.

    Empty when there is no graph — and that is not the same as clean. `report`
    carries `evaluated`, and the CLI exits 3 rather than 0, because a checker
    that could not look must not be read as one that looked and found nothing.
    """
    target = Path(target)
    if not graph_ready(target):
        return []
    design = stages.by_phase(PHASE).produces[0]
    if not (ledger.run_dir(target, run_id) / design).is_file():
        return []          # `verify` already says the phase owes it, by name.
    changes = change_set(target, run_id)
    problems: list[str] = []
    if naming := naming_problem(target):
        problems.append(naming)
    for token, hits in changes.ambiguous:
        problems.append(
            f"{design} names `{token}`, which exists in {len(hits)} components "
            f"({', '.join(hits)}) - name it component-relative, because a fact "
            f"read off the wrong one is evidence about the wrong file")
    if not changes.resolved:
        return problems + [
            f"{design} names no file this workspace contains, so its ADR "
            f"triggers were not evaluated - 'no decision worth recording' out "
            f"of a document that named nothing is a clean bill of health from "
            f"a check that looked at nothing. Name each changed file in "
            f"backticks, workspace-relative"]
    fired, _ = triggers(target, run_id)
    answered: dict[str, Path] = {}
    known = {t.id for t in fired}
    for path in adr_files(target, run_id):
        text = path.read_text(encoding="utf-8", errors="replace")
        ids = cited(path)
        if not text.strip():
            problems.append(f"{path.name} is empty - an ADR nobody wrote reads "
                            f"exactly like one nobody needed")
            continue
        if not ids:
            problems.append(
                f"{path.name} states no `{ANSWERS_MARK}` line, so nothing "
                f"connects it to a trigger and no trigger is closed by it")
        if REJECTED_MARK not in text:
            problems.append(
                f"{path.name} states no `{REJECTED_MARK}` line - a decision "
                f"with no rejected alternative is a description of what was "
                f"done, and the alternatives are the half that is worth "
                f"reading in six months")
        for one in ids:
            if one in known:
                answered[one] = path
            else:
                problems.append(
                    f"{path.name} answers `{one}`, which this design did not "
                    f"fire - a citation that matches nothing leaves the real "
                    f"trigger open while looking closed")
    refused = dismissals(target, run_id)
    for trigger in fired:
        if trigger.id in answered or trigger.id in refused:
            continue
        problems.append(
            f"{trigger.id} fired and nothing answers it "
            f"({trigger.evidence[0] if trigger.evidence else 'no evidence line'}) "
            f"- answer it in a {stages.by_phase(PHASE).conditional[0]}, or dismiss "
            f"it with a reason; tools/sdlc/adr.py --check says why it fired")
    return problems


def report(target: Path, run_id: str) -> dict:
    """Everything the check knows, as data — for the CLI and for a caller that
    wants the triggers without the sentences."""
    target = Path(target)
    if not graph_ready(target):
        return {"schema": SCHEMA_VERSION, "run": run_id, "evaluated": False,
                "why": f"no built graph at {graph_dir(target)} - the triggers are "
                       f"read off declared facts, and this workspace has none",
                "triggers": [], "unruled": [], "answered": {}, "dismissed": {},
                "obligations": [], "change_set": {}}
    changes = change_set(target, run_id)
    fired, unruled = triggers(target, run_id)
    answered: dict[str, str] = {}
    for path in adr_files(target, run_id):
        for one in cited(path):
            answered.setdefault(one, path.name)
    return {
        "schema": SCHEMA_VERSION, "run": run_id, "evaluated": True, "why": "",
        "triggers": [t._asdict() for t in fired],
        "unruled": unruled,
        "answered": answered,
        "dismissed": {k: v.get("because", "") for k, v in
                      dismissals(target, run_id).items()},
        "obligations": obligations(target, run_id),
        "change_set": {"resolved": [list(p) for p in changes.resolved],
                       "ambiguous": [[t, list(h)] for t, h in changes.ambiguous],
                       "unknown": list(changes.unknown)},
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _render(data: dict) -> str:
    lines: list[str] = []
    if not data["evaluated"]:
        return f"ADR triggers: NOT EVALUATED — {data['why']}"
    changes = data["change_set"]
    lines.append(f"change set: {len(changes['resolved'])} file(s) named and found"
                 + (f", {len(changes['ambiguous'])} ambiguous" if changes["ambiguous"] else "")
                 + (f", {len(changes['unknown'])} named but not in the workspace"
                    if changes["unknown"] else ""))
    if not data["triggers"]:
        lines.append("triggers: none — no named file declares a dependency, a "
                     "contract or a table")
    for row in data["triggers"]:
        mark = ("answered by " + data["answered"][row["id"]]) if row["id"] in data["answered"] \
            else ("dismissed: " + data["dismissed"][row["id"]]) if row["id"] in data["dismissed"] \
            else "UNANSWERED"
        lines.append(f"  [{row['kind']}] {row['id']} — {mark}")
        lines.append(f"      why: {WHY[row['kind']]}")
        for line in row["evidence"]:
            lines.append(f"      {line}")
        if row["elided"]:
            lines.append(f"      … {row['elided']} further declaration(s) not shown")
    if data["unruled"]:
        lines.append("edge types nobody has ruled on, seen on a changed file: "
                     + ", ".join(data["unruled"])
                     + " — they trigger nothing until someone decides they should")
    for problem in data["obligations"]:
        lines.append(f"OWED: {problem}")
    if data["obligations"]:
        lines += [
            "",
            f"Answer a trigger with a {stages.by_phase(PHASE).conditional[0]} "
            f"carrying an `{ANSWERS_MARK} \u0060<trigger-id>\u0060` line and a "
            f"`{REJECTED_MARK}` line. Refuse one with:",
            "    tools/sdlc/adr.py --target <repo> --run <id> --dismiss <trigger-id> "
            "--because \"<reason>\"",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR",
                    help="the repository or workspace the run lives in")
    ap.add_argument("--run", required=True, metavar="ID", help="the run id")
    ap.add_argument("--check", action="store_true",
                    help="which triggers fired, what answers them, what is owed")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument("--dismiss", metavar="ID",
                    help="record that a fired trigger deliberately gets no ADR")
    ap.add_argument("--because", default="", metavar="TEXT",
                    help="with --dismiss, the reason - required, and recorded")
    ap.add_argument("--by", default="", metavar="WHO",
                    help="with --dismiss, who decided (default: the target's git identity)")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    try:
        if args.dismiss:
            row = dismiss(target, args.run, args.dismiss, args.because, args.by)
            print(f"dismissed {row['trigger']} — {row['because']} ({row['by']})")
            return 0
        data = report(target, args.run)
        print(json.dumps(data, indent=2, sort_keys=True) if args.json else _render(data))
        if not data["evaluated"]:
            return 3
        return 1 if data["obligations"] else 0
    except (AdrError, stages.StageError, guardrails.PolicyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
