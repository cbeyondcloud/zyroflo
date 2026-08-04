"""Service-provider interfaces a profile implements.

A new estate = a new profile directory implementing these two protocols. Core is never
edited to onboard an estate — that claim is checked by the M0 genericity test and proven
by the M1 second-estate micro-profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .model import ExtractResult, Node, Edge


@dataclass
class RepoRef:
    """One repository in the estate, as declared by the profile's registry."""

    alias: str
    path: Path
    tags: list[str] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


@dataclass
class ExtractContext:
    """Everything an extractor is given. Read-only with respect to the estate.

    Extractors receive a context rather than reaching for globals so that a profile can be
    run against a fixture tree in tests exactly as it runs against the real estate.
    """

    repos: list[RepoRef]
    run_id: str
    config: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)

    def repo(self, alias: str) -> RepoRef | None:
        return next((r for r in self.repos if r.alias == alias), None)

    def rel(self, repo: RepoRef, path: Path) -> str:
        """Repo-relative POSIX path — the provenance form used in every edge and pack."""
        try:
            return path.relative_to(repo.path).as_posix()
        except ValueError:
            return path.as_posix()


@runtime_checkable
class Extractor(Protocol):
    """Turns files into nodes and edges. Pure: same input tree -> same output."""

    #: Stable identifier recorded on every edge this extractor emits.
    name: str
    #: Globs (repo-relative) this extractor reads; drives incremental rebuild invalidation.
    INPUT_GLOBS: tuple[str, ...]

    def extract(self, ctx: ExtractContext) -> ExtractResult: ...


@runtime_checkable
class LinkPass(Protocol):
    """Joins already-extracted facts into new edges (or rewrites confidence/canonicality).

    Link passes run after all extractors, in declared order, and may read the full node and
    edge sets — that is where cross-repo identity resolution happens.
    """

    name: str

    def link(
        self, ctx: ExtractContext, nodes: list[Node], edges: list[Edge]
    ) -> ExtractResult: ...


@runtime_checkable
class SymbolLayer(Protocol):
    """Optional intra-service symbol enrichment (e.g. CodeGraphContext).

    Never load-bearing: `NullSymbolLayer` is a complete implementation, and the M0 bake-off
    decides whether a real one ships at all.
    """

    name: str

    def index(self, repo: RepoRef) -> bool: ...
    def callers_of(self, repo: RepoRef, symbol: str) -> list[str]: ...


class NullSymbolLayer:
    """The always-available fallback. Answers nothing, breaks nothing."""

    name = "null"

    def index(self, repo: RepoRef) -> bool:
        return False

    def callers_of(self, repo: RepoRef, symbol: str) -> list[str]:
        return []
