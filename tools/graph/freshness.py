"""One judgment of a shard (batch 16, T627-T630; the owner's rulings 27-30).

Whether each component's committed shard is current, stale and why, refused and why, or
missing: one reading of its stamps, bytes and rows (`snapshot.read_shard`) and one verdict
(`judge`), shared by `--check-fresh`, `--from-snapshot` and `--incremental` - three paths
that used to judge a shard on their own, and disagreed (batch 15b's code-review rounds 5-8
each found one disagreeing with another).

A shard is stamped with the parser state it was built under (ruling 30): every parser its
component's own readers needed for the files they read, and every parser the readers they
depend on needed for the files those read anywhere (`parser_stamp`, the one producer the
publish and the verdict both call). It is stale whenever that stamp differs from what a
publish would write now - in either direction, whatever the reader (rulings 27, 28).

It names no reader and no estate: which parser a file needs comes from the readers' own
declarations, handed in by `build.py` as `parser_of` (reader, path, rel) and `probe`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The producer named on a row the pipeline itself emits, from the activation walk, and the
#: stamp entry holding that walk's off-case record (T531). Owned here since batch 16, like the
#: two helpers below; `build.py` re-exports all four.
ACTIVATION = "activation"
#: The stamp entry holding the deployment readers' case record (batch 15b): its own, so
#: `--check-fresh` names the record that moved, never both (D15b-123).
ACTIVATION_DEPLOY = "activation-deploy"

ParserOf = Callable[[str, Path, str], "str | None"]


def _shared_of(config_sha: str) -> str:
    """The shared half of a `config_digest` stamp - a stamp from before batch 15b has no
    halves, so it is all shared, and differs from every current one."""
    return config_sha.split(":", 1)[0]


def stamp_matches(current: str, stamped: str) -> bool:
    """Whether an input digest still matches its stamp. Unreadable never matches - on either
    side, in either build: content nobody could read is content nobody may claim is
    unchanged."""
    if not current or not stamped:
        return False
    if current.startswith("unreadable-") or stamped.startswith("unreadable-"):
        return False
    return current == stamped


def _rel(path: Path, base: Path) -> str:
    """The path a reader is handed a file by, as `input_digests` spells it."""
    return path.relative_to(base).as_posix() if path.is_relative_to(base) else path.as_posix()


def parser_halves(component: str, act: Any, closure: dict[str, frozenset[str]],
                  parser_of: ParserOf, root: Path, components: list[dict],
                  memo: dict | None = None
                  ) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Each parser the shard's readers need, with the readers needing it, in registry order.

    The OWN half comes from the files this component handed its own active readers; the
    CLOSURE half from the files every other component handed the readers in their transitive
    `REQUIRES` closure, as claim 9 counts those readers' inputs (a closure reader's files in
    this component are the own half: only an active reader is handed any). Exact per file,
    since each reader answers by its own gate (`parser_of`); conservative across components
    where a closure reader's cache is keyed per component (T633). `memo` keeps one answer per
    (reader, file, path handed) across components, so a sniffing reader reads a file once
    per run, and a rule that answers by that path is asked it for each."""
    memo = {} if memo is None else memo
    rank = {name: i for i, name in enumerate(act.by_extractor)}
    bases = {c["name"]: root / c["path"] for c in components}

    def needs(reader: str, comp: str) -> set[str]:
        out: set[str] = set()
        for path in act.files.get((reader, comp), ()):
            # Keyed by the path the reader is handed too: one file reaches a reader that
            # reaches into a declared child under two such paths, and its rule may answer
            # each differently (review B16-4).
            rel = _rel(path, bases[comp])
            key = (reader, path, rel)
            if key not in memo:
                memo[key] = parser_of(reader, path, rel)
            if memo[key]:
                out.add(memo[key])
        return out

    own_readers = list(act.by_component.get(component) or [])
    own: dict[str, set[str]] = {}
    for reader in own_readers:
        for parser in needs(reader, component):
            own.setdefault(parser, set()).add(reader)
    depended: set[str] = set()
    for reader in own_readers:
        depended |= closure.get(reader, frozenset())
    near: dict[str, set[str]] = {}
    for reader in depended:
        for comp in bases:
            if comp != component:
                for parser in needs(reader, comp):
                    near.setdefault(parser, set()).add(reader)

    def ordered(found: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
        return {parser: tuple(sorted(readers, key=lambda r: (rank.get(r, len(rank)), r)))
                for parser, readers in sorted(found.items())}
    return ordered(own), ordered(near)


def parser_stamp(component: str, act: Any, closure: dict[str, frozenset[str]],
                 probe: Callable[[str], bool], parser_of: ParserOf, root: Path,
                 components: list[dict], memo: dict | None = None) -> dict[str, bool]:
    """The `parsers` a publish writes into this component's manifest, and what the verdict
    compares the stamp against: every parser either half reaches, with whether it is
    installed now. The ONE producer of a stamp (design round 3, D16-31)."""
    own, near = parser_halves(component, act, closure, parser_of, root, components, memo)
    return {parser: bool(probe(parser)) for parser in sorted({*own, *near})}


#: The kept words (ruling 29): today's reasons stay word for word where they still hold.
MISSING = "no freshness stamps — no committed shard, or one published before stamps existed"
EXTRACTORS_CHANGED = "the extractor set changed since its shard was published"
#: `--check-fresh`'s own reason for a configuration change, kept word for word (D15b-96);
#: `build.py` re-exports it for the load's group words.
STALE_CONFIG = "the workspace configuration changed since its shard was published"
#: Claim 6 as `--incremental` words it.
OWN_FILES = "its own files changed"
NO_PARSER_STAMP = "its shard was published before parser stamps existed"
#: Claim 9b's causes, in the order the first one a dependent meets is its reason (T630).
_UNCOMPARABLE = ("that has no committed shard",
                 "whose shard was published before stamps existed",
                 "whose committed snapshot.json could not be read")


@dataclass(frozen=True)
class Verdict:
    """One component's judgment - `current`, `stale`, `refused` or `missing` - in
    `--check-fresh`'s words. A cause the paths word differently carries the load's and the
    plan's renderings too (claim 6, and claim 2's digest class); `remedy` is a refusal's."""

    state: str
    words: str = ""
    load_words: str | None = None
    plan_words: str | None = None
    remedy: str | None = None

    @property
    def load(self) -> str:
        """The rendering a `--from-snapshot` load prints."""
        return self.load_words or self.words

    @property
    def plan(self) -> str:
        """The rendering `--incremental` records."""
        return self.plan_words or self.words


def _in_order(readers: set[str] | list[str], rank: dict[str, int]) -> list[str]:
    """Reader names in registry order: every list a verdict prints is in it."""
    return sorted(set(readers), key=lambda r: (rank.get(r, len(rank)), r))


def _ticked(readers: list[str]) -> str:
    return ", ".join(f"`{r}`" for r in readers)


def _moved_words(moved: list[str], rank: dict[str, int]) -> str:
    """Claim 6 in `--check-fresh`'s rendering: each stamp entry that moved, the readers first
    and each case record in its own words (D15b-123)."""
    readers = _in_order([e for e in moved if e not in (ACTIVATION, ACTIVATION_DEPLOY)], rank)
    return "; ".join(
        ([f"files read by {', '.join(readers)} changed"] if readers else [])
        + (["files no extractor reads because of their case changed"]
           if ACTIVATION in moved else [])
        + (["a file a deployment reader misses only by case changed"]
           if ACTIVATION_DEPLOY in moved else []))


def _own_words(own: dict[str, tuple[str, ...]], stamped: dict[str, bool],
               probe: Callable[[str], bool], rank: dict[str, int]) -> str:
    """Claim 8: the own half of the stamp against now - installed since, removed since, and
    each name the stamp lacks - or "" where it matches."""
    installed: set[str] = set()
    removed: set[str] = set()
    lacked: list[str] = []
    for parser, readers in own.items():
        if parser not in stamped:
            lacked.append(f"its shard's parser stamp does not record `{parser}`, which "
                          f"{_ticked(list(readers))} now use")
        elif stamped[parser] != bool(probe(parser)):
            (removed if stamped[parser] else installed).update(readers)
    clauses = []
    if installed:
        clauses.append(f"its shard was published while {_ticked(_in_order(installed, rank))} "
                       f"had no parser installed")
    if removed:
        clauses.append(f"its shard was published while {_ticked(_in_order(removed, rank))} "
                       f"had a parser that is no longer installed")
    return "; ".join(clauses + lacked)


def _closure_words(own: dict[str, tuple[str, ...]], near: dict[str, tuple[str, ...]],
                   stamped: dict[str, bool], probe: Callable[[str], bool],
                   rank: dict[str, int]) -> str:
    """Claim 10: the closure half of the stamp against now, and each stamped name neither half
    reaches any more (D16-52), or "" where it matches. A parser the own half reaches is claim
    8's: the stamp holds one merged map."""
    installed: set[str] = set()
    removed: set[str] = set()
    lacked: list[str] = []
    for parser, readers in near.items():
        if parser in own:
            continue
        if parser not in stamped:
            lacked.append(f"its shard's parser stamp does not record `{parser}`, which "
                          f"{', '.join(readers)} it depends on now use")
        elif stamped[parser] != bool(probe(parser)):
            (removed if stamped[parser] else installed).update(readers)
    clauses = []
    if installed:
        clauses.append(f"depends on {', '.join(_in_order(installed, rank))}, which had no "
                       f"parser installed when its shard was published")
    if removed:
        clauses.append(f"depends on {', '.join(_in_order(removed, rank))}, whose parser was "
                       f"installed when its shard was published and is not now")
    gone = [f"its shard's parser stamp records `{parser}`, which nothing it reads uses now: "
            f"what it depended on changed or left the tree"
            for parser in sorted(stamped) if parser not in own and parser not in near]
    return "; ".join(clauses + lacked + gone)


def judge(readings: dict[str, Any], components: list[dict], act: Any,
          closure: dict[str, frozenset[str]], digests: dict[str, dict[str, str]],
          version: str, config_shas: dict[str, str], probe: Callable[[str], bool],
          parser_of: ParserOf, root: Path) -> dict[str, Verdict]:
    """Every component's verdict, by the design note's claims in order; the first that fails
    is the verdict (section 4). `readings` are `snapshot.read_shard`'s, one per component;
    `digests` the input digests now (`input_digests`), `version` and `config_shas` the
    extractor version and each component's configuration stamp now.

    What marks a dependency's inputs as changed for every dependent (claim 9) is `main`'s
    three contributors that survive: a sibling's per-extractor stamp against its digest, its
    shared configuration half, and a sibling with nothing to compare against (no shard, no
    stamps, a manifest that cannot be read). Its extractor version, its rows and its bytes
    failing their digest are not, measured or argued safe in design rounds 3-5: the parser
    stamp, not the rows, decides currency for every reader."""
    rank = {name: i for i, name in enumerate(act.by_extractor)}
    changed: set[str] = set()
    unseen: dict[str, int] = {}
    for comp in components:
        name, reading = comp["name"], readings[comp["name"]]
        current, stamps = digests.get(name, {}), reading.stamps
        if stamps is None:
            cause = 0 if reading.absent == "no shard" else 1 if reading.absent else 2
            for ext in current:
                unseen[ext] = min(cause, unseen.get(ext, cause))
            continue
        if _shared_of(stamps["config_sha"]) != _shared_of(config_shas[name]):
            changed |= set(current)
            continue
        changed |= {ext for ext in set(current) | set(stamps["inputs"])
                    if not stamp_matches(current.get(ext, ""), stamps["inputs"].get(ext, ""))}

    memo: dict = {}
    out: dict[str, Verdict] = {}
    for comp in components:
        name, reading = comp["name"], readings[comp["name"]]
        current, stamps = digests.get(name, {}), reading.stamps
        if reading.absent == "no shard":
            out[name] = Verdict("missing", MISSING)
        elif reading.refused:
            out[name] = Verdict("refused", reading.refused, load_words=reading.refused_load,
                                remedy=reading.remedy)
        elif stamps is None:
            out[name] = Verdict("stale", MISSING)
        elif stamps["extractor_version"] != version:
            out[name] = Verdict("stale", EXTRACTORS_CHANGED)
        elif stamps["config_sha"] != config_shas[name]:
            out[name] = Verdict("stale", STALE_CONFIG)
        elif moved := sorted(e for e in set(current) | set(stamps["inputs"])
                             if not stamp_matches(current.get(e, ""),
                                                  stamps["inputs"].get(e, ""))):
            out[name] = Verdict("stale", _moved_words(moved, rank), plan_words=OWN_FILES)
        elif "parsers" not in stamps:
            out[name] = Verdict("stale", NO_PARSER_STAMP)
        else:
            out[name] = _parser_verdict(name, act, closure, stamps["parsers"], changed, unseen,
                                        probe, parser_of, root, components, memo, rank)
    return out


def _parser_verdict(name: str, act: Any, closure: dict[str, frozenset[str]],
                    stamped: dict[str, bool], changed: set[str], unseen: dict[str, int],
                    probe: Callable[[str], bool], parser_of: ParserOf, root: Path,
                    components: list[dict], memo: dict, rank: dict[str, int]) -> Verdict:
    """Claims 8 to 11, for a shard whose own stamps all match."""
    own, near = parser_halves(name, act, closure, parser_of, root, components, memo)
    if words := _own_words(own, stamped, probe, rank):
        return Verdict("stale", words)
    deps: set[str] = set()
    for reader in act.by_component.get(name) or []:
        deps |= closure.get(reader, frozenset())
    if dirty := _in_order(deps & changed, rank):
        return Verdict("stale", f"depends on {', '.join(dirty)}, whose inputs changed in "
                                f"another component")
    if blind := deps & set(unseen):
        first = min(unseen[e] for e in blind)
        exts = _in_order({e for e in blind if unseen[e] == first}, rank)
        return Verdict("stale", f"depends on {', '.join(exts)}, active in another component "
                                f"{_UNCOMPARABLE[first]}")
    if words := _closure_words(own, near, stamped, probe, rank):
        return Verdict("stale", words)
    return Verdict("current")
