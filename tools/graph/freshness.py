"""One judgment of a shard (batch 16, T627-T630; the owner's rulings 27-30).

Whether each component's committed shard is current, stale and why, refused and why, or
missing: one reading of its stamps, bytes and rows (`snapshot.read_shard`) and one verdict
(`judge`), shared by `--check-fresh`, `--from-snapshot` and `--incremental` - three paths
that used to judge a shard on their own, and disagreed (batch 15b's code-review rounds 5-8
each found one disagreeing with another).

A shard is stamped with the parser state it was built under (ruling 30): every parser its
component's own readers needed for the files they read, and every parser the readers they
depend on needed for the files those read anywhere, each with its version, or `false` where
it was missing (batch 22, T631) - `parser_stamp`, the one producer the publish and the
verdict both call. It is stale whenever that stamp differs from what a publish would write
now - in either direction, whatever the reader (rulings 27, 28) - a version that cannot be
read comparing presence alone (D22-68).

It names no reader and no estate: which parser a file needs comes from the readers' own
declarations, handed in by `build.py` as `parser_of` (reader, path, rel) and `probe`.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The producer named on a row the pipeline itself emits, from the activation walk, and the
#: stamp entry holding that walk's off-case record (T531). Owned here since batch 16, like the
#: two helpers below; `build.py` re-exports them, and the entries after this one.
ACTIVATION = "activation"
#: The stamp entry holding the deployment readers' case record (batch 15b): its own, so
#: `--check-fresh` names the record that moved, never both (D15b-123).
ACTIVATION_DEPLOY = "activation-deploy"
#: The stamp entry holding the directories the activation walk could not list (T611): its
#: own, and poisoned, as an unreadable file's digest is - so it never matches, and a
#: component holding one is stale until the directory can be listed.
ACTIVATION_UNLISTED = "activation-unlisted"
#: How `--check-fresh` says that entry: not "changed" - nobody can know - and what lifts it.
UNLISTED_WORDS = ("a directory could not be listed, so its files cannot be called unchanged "
                  "(allow listing it, then rebuild)")

ParserOf = Callable[[str, Path, str], "str | None"]
#: Each parser's version now (batch 22, T631): the version of what served it, or `INSTALLED`
#: where that cannot be read. Asked only of a parser the probe finds installed.
Version = Callable[[str], str]
#: A version that cannot be read: presence alone, `main`'s stamp. Where either side of a
#: comparison reads it, claims 8 and 10 compare presence alone, so an environment that
#: cannot read a version never stales another's shard on it (D22-68).
INSTALLED = "installed"


def stamp_matches(current: str, stamped: str) -> bool:
    """Whether an input digest still matches its stamp. Unreadable never matches - on either
    side, in either build: content nobody could read is content nobody may claim is
    unchanged."""
    if not current or not stamped:
        return False
    if current.startswith("unreadable-") or stamped.startswith("unreadable-"):
        return False
    return current == stamped


def handed_path(path: Path, base: Path) -> str:
    """The path a reader is handed a file by, relative to its component `base`, as every
    stamp spells it: a file above the component - one a reader declaring `READS_ANCESTORS` is
    handed (T614) - as `../x`, never by the absolute path a moved tree changes, which would
    make every committed shard of such a component stale on any other machine."""
    if path.is_relative_to(base):
        return path.relative_to(base).as_posix()
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:                     # another drive: no relative spelling exists
        return path.as_posix()


_rel = handed_path


def parser_halves(component: str, act: Any, closure: dict[str, frozenset[str]],
                  parser_of: ParserOf, root: Path, components: list[dict],
                  memo: dict | None = None
                  ) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Each parser the shard's readers need, with the readers needing it, in registry order.

    The OWN half comes from the files this component handed its own active readers; the
    CLOSURE half from the files every other component handed the readers in their
    cross-component closure - `closure` is `build.cross_closure`: the transitive `REQUIRES`
    closure minus its component-scoped members, whose cache entries reach only their own
    component (batch 22, T633) - as claim 9 counts those readers' inputs (a closure reader's
    files in this component are the own half: only an active reader is handed any). Exact per
    file, since each reader answers by its own gate (`parser_of`). `memo` keeps one answer per
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


def stamp_value(parser: str, probe: Callable[[str], bool], version: Version) -> str | bool:
    """What a stamp records for one parser now: its version where it is installed, `False`
    where it is not - the one computation the publish writes and the verdict compares."""
    return version(parser) if probe(parser) else False


def parser_stamp(component: str, act: Any, closure: dict[str, frozenset[str]],
                 probe: Callable[[str], bool], parser_of: ParserOf, root: Path,
                 components: list[dict], memo: dict | None = None, *,
                 version: Version) -> dict[str, str | bool]:
    """The `parsers` a publish writes into this component's manifest, and what the verdict
    compares the stamp against: every parser either half reaches, with the version it has
    now, or `False` where it is not installed (batch 22, T631). The ONE producer of a stamp
    (design round 3, D16-31)."""
    own, near = parser_halves(component, act, closure, parser_of, root, components, memo)
    return {parser: stamp_value(parser, probe, version) for parser in sorted({*own, *near})}


def closure_record(component: str, act: Any, cross: dict[str, frozenset[str]],
                   digests: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """What a shard records of the inputs it read from OTHER components (batch 22, T632): for
    each extractor in its cross-component closure - its own readers' `cross` closures, unioned
    - and each other component where that extractor is active, the digest `input_digests`
    computes there, one per (component, extractor), never one per file. The ONE producer of
    the record (as `parser_stamp` is of the stamp): the publish writes it, and claim 9
    compares it against what it would be now."""
    depended: set[str] = set()
    for reader in act.by_component.get(component) or []:
        depended |= cross.get(reader, frozenset())
    return {comp: {ext: per[ext] for ext in sorted(depended) if ext in per}
            for comp, per in digests.items()
            if comp != component and depended & set(per)}


def _poisoned(digest: str) -> bool:
    return digest.startswith("unreadable-")


def _record_words(recorded: dict[str, dict[str, str]], now: dict[str, dict[str, str]],
                  places: dict[str, int], rank: dict[str, int]) -> str:
    """Claim 9 from the record (batch 22, T632): the first reason in order - 9a, a recorded
    pair with a digest now that no longer matches it; 9b, a recorded pair with no digest now;
    9c, a pair with a digest now and no record - components and extractors in registry order.
    9a asks only of a pair with a digest now (`stamp_matches` is False where a side is empty,
    so unbounded it would take 9b's case, D22-31), and a poisoned digest is never a match, so
    it words what it is: unreadable now, the way on in the words, or unreadable at the
    publish (D22-73b, D22-91)."""
    def ordered(record: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
        return sorted(((comp, ext) for comp, per in record.items() for ext in per),
                      key=lambda pair: (places.get(pair[0], len(places)), pair[0],
                                        rank.get(pair[1], len(rank)), pair[1]))
    for comp, ext in ordered(recorded):
        then, digest = recorded[comp][ext], now.get(comp, {}).get(ext)
        if digest is not None and not stamp_matches(digest, then):
            if _poisoned(digest):
                return f"depends on {ext}, whose inputs in {comp} {UNREADABLE_NOW}"
            if _poisoned(then):
                return (f"depends on {ext}, whose inputs in {comp} could not be read when its "
                        f"shard was published")
            return (f"depends on {ext}, whose inputs in {comp} changed since its shard was "
                    f"published")
    for comp, ext in ordered(recorded):
        if ext not in now.get(comp, {}):
            return f"depends on {ext}, which is no longer active in {comp}"
    for comp, ext in ordered(now):
        if ext not in recorded.get(comp, {}):
            return f"depends on {ext}, now active in {comp}, which its shard did not read"
    return ""


def _compared(stamped: str | bool, now: str | bool) -> str | None:
    """How one parser's stamp differs from now, or None where it matches (batch 22): a legacy
    `False` compares as any `False` (ruling 28); a legacy `True` records no version; a side
    reading `INSTALLED` compares presence alone."""
    if now is False:
        return None if stamped is False else "removed"
    if stamped is False:
        return "installed"
    if stamped is True:
        return "unversioned"
    if INSTALLED in (stamped, now) or stamped == now:
        return None
    return "version"


#: The kept words (ruling 29): today's reasons stay word for word where they still hold.
MISSING = "no freshness stamps — no committed shard, or one published before stamps existed"
#: Claim 4: the readers or their code (batch 22, T617).
EXTRACTORS_CHANGED = "the extractors or their code changed since its shard was published"
#: Claim 4 where the extractor version is UNKNOWN - a file of the extraction code could not be
#: read, or is missing (batch 22, T617): its rendering opens with these words and carries its
#: own way on in parentheses, "`<path>` (<cause>; <way on>)".
UNKNOWN_VERSION = "a file of the extraction code could not be read: "


@dataclass(frozen=True)
class UnknownVersion:
    """The extractor version now, where it cannot be computed: `words` is claim 4's rendering
    for every shard that reaches claim 4 (`build.unknown_words`)."""

    words: str


def carries_way(words: str) -> bool:
    """Whether a stale verdict's words carry their own way on, so a load offers no other for
    its group - one channel per line (batch 22, D22-79): the unknown version's, and claim 9a's
    where a sibling's inputs cannot be read now."""
    return words.startswith(UNKNOWN_VERSION) or words.endswith(UNREADABLE_NOW)
#: `--check-fresh`'s own reason for a configuration change, kept word for word (D15b-96);
#: `build.py` re-exports it for the load's group words.
STALE_CONFIG = "the workspace configuration changed since its shard was published"
#: Claim 6 as `--incremental` words it.
OWN_FILES = "its own files changed"
NO_PARSER_STAMP = "its shard was published before parser stamps existed"
#: Claim 9 where a shard records nothing of what it read from other components but would hold
#: a record now (batch 22, T632): published before the record existed.
NO_RECORD = "its shard was published before it recorded the inputs it read from other components"
#: Claim 9a where the inputs cannot be read now: its way on in its words (D22-73b), so a load
#: offers no other for its group.
UNREADABLE_NOW = "cannot be read now (make them readable)"


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
    and each of the activation walk's records in its own words (D15b-123; T611)."""
    readers = _in_order([e for e in moved
                         if e not in (ACTIVATION, ACTIVATION_DEPLOY, ACTIVATION_UNLISTED)], rank)
    return "; ".join(
        ([f"files read by {', '.join(readers)} changed"] if readers else [])
        + (["files no extractor reads because of their case changed"]
           if ACTIVATION in moved else [])
        + (["a file a deployment reader misses only by case changed"]
           if ACTIVATION_DEPLOY in moved else [])
        + ([UNLISTED_WORDS] if ACTIVATION_UNLISTED in moved else []))


#: A legacy `true` stamp's words, in either claim (batch 22, D22-35).
def _unversioned(parser: str) -> str:
    return f"its shard's parser stamp records no version for `{parser}`"


def _own_words(own: dict[str, tuple[str, ...]], stamped: dict[str, str | bool],
               probe: Callable[[str], bool], version: Version, rank: dict[str, int]) -> str:
    """Claim 8: the own half of the stamp against now - installed since, removed since, read
    with another version (batch 22, T631), stamped with no version, and each name the stamp
    lacks - or "" where it matches."""
    installed: set[str] = set()
    removed: set[str] = set()
    moved: list[str] = []
    unversioned: list[str] = []
    lacked: list[str] = []
    for parser, readers in own.items():
        if parser not in stamped:
            lacked.append(f"its shard's parser stamp does not record `{parser}`, which "
                          f"{_ticked(list(readers))} now use")
            continue
        now = stamp_value(parser, probe, version)
        kind = _compared(stamped[parser], now)
        if kind == "installed":
            installed.update(readers)
        elif kind == "removed":
            removed.update(readers)
        elif kind == "version":
            moved.append(f"{_ticked(list(readers))} read with `{parser}` {stamped[parser]}; "
                         f"it is {now} now")
        elif kind == "unversioned":
            unversioned.append(_unversioned(parser))
    clauses = []
    if installed:
        clauses.append(f"its shard was published while {_ticked(_in_order(installed, rank))} "
                       f"had no parser installed")
    if removed:
        clauses.append(f"its shard was published while {_ticked(_in_order(removed, rank))} "
                       f"had a parser that is no longer installed")
    return "; ".join(clauses + moved + unversioned + lacked)


def _closure_words(own: dict[str, tuple[str, ...]], near: dict[str, tuple[str, ...]],
                   stamped: dict[str, str | bool], probe: Callable[[str], bool],
                   version: Version, rank: dict[str, int]) -> str:
    """Claim 10: the closure half of the stamp against now, and each stamped name neither half
    reaches any more (D16-52), or "" where it matches. A parser the own half reaches is claim
    8's: the stamp holds one merged map."""
    installed: set[str] = set()
    removed: set[str] = set()
    moved: list[str] = []
    unversioned: list[str] = []
    lacked: list[str] = []
    for parser, readers in near.items():
        if parser in own:
            continue
        if parser not in stamped:
            lacked.append(f"its shard's parser stamp does not record `{parser}`, which "
                          f"{', '.join(readers)} it depends on now use")
            continue
        now = stamp_value(parser, probe, version)
        kind = _compared(stamped[parser], now)
        if kind == "installed":
            installed.update(readers)
        elif kind == "removed":
            removed.update(readers)
        elif kind == "version":
            moved.append(f"depends on {', '.join(readers)}, whose parser `{parser}` was "
                         f"{stamped[parser]} and is {now} now")
        elif kind == "unversioned":
            unversioned.append(_unversioned(parser))
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
    return "; ".join(clauses + moved + unversioned + lacked + gone)


def judge(readings: dict[str, Any], components: list[dict], act: Any,
          closure: dict[str, frozenset[str]], digests: dict[str, dict[str, str]],
          version: str | UnknownVersion, config_shas: dict[str, str],
          probe: Callable[[str], bool], parser_of: ParserOf, root: Path, *,
          parser_version: Version) -> dict[str, Verdict]:
    """Every component's verdict, by the design note's claims in order; the first that fails
    is the verdict (section 4). `readings` are `snapshot.read_shard`'s, one per component;
    `digests` the input digests now (`input_digests`), `version` and `config_shas` the
    extractor version and each component's configuration stamp now - `version` an
    `UnknownVersion` where a file of the extraction code cannot be read, which fails claim 4
    for every shard that reaches it (batch 22) - and `probe` and `parser_version` whether
    each parser is installed now and its version.

    Claim 9 judges what the dependent itself read from other components (batch 22, T632): its
    shard's record (`closure_record`) against what the record would be now. A sibling's own
    state - its stamps, its shard, its configuration half - is never read: the dependent's
    facts depend on the sibling's FILES, which the record compares directly. `closure` is the
    cross-component closure (`build.cross_closure`, T633)."""
    rank = {name: i for i, name in enumerate(act.by_extractor)}
    places = {comp["name"]: i for i, comp in enumerate(components)}
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
        elif isinstance(version, UnknownVersion):
            out[name] = Verdict("stale", version.words)
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
            out[name] = _parser_verdict(name, act, closure, stamps, digests, probe,
                                        parser_version, parser_of, root, components, memo,
                                        rank, places)
    return out


def _parser_verdict(name: str, act: Any, closure: dict[str, frozenset[str]], stamps: dict,
                    digests: dict[str, dict[str, str]], probe: Callable[[str], bool],
                    version: Version, parser_of: ParserOf, root: Path,
                    components: list[dict], memo: dict, rank: dict[str, int],
                    places: dict[str, int]) -> Verdict:
    """Claims 8 to 11, for a shard whose own stamps all match."""
    stamped = stamps["parsers"]
    own, near = parser_halves(name, act, closure, parser_of, root, components, memo)
    if words := _own_words(own, stamped, probe, version, rank):
        return Verdict("stale", words)
    now = closure_record(name, act, closure, digests)
    recorded = stamps.get("closure_inputs")
    if recorded is None:
        if now:   # belt-and-braces: claim 4 stales every shard published before it (D22-12)
            return Verdict("stale", NO_RECORD)
    elif words := _record_words(recorded, now, places, rank):
        return Verdict("stale", words)
    if words := _closure_words(own, near, stamped, probe, version, rank):
        return Verdict("stale", words)
    return Verdict("current")
