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
    (reader, file) across components, so a sniffing reader reads a file once per run."""
    memo = {} if memo is None else memo
    rank = {name: i for i, name in enumerate(act.by_extractor)}
    bases = {c["name"]: root / c["path"] for c in components}

    def needs(reader: str, comp: str) -> set[str]:
        out: set[str] = set()
        for path in act.files.get((reader, comp), ()):
            key = (reader, path)
            if key not in memo:
                memo[key] = parser_of(reader, path, _rel(path, bases[comp]))
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
