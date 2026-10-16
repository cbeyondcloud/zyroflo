"""Which endpoints a user interface calls, and how confident that reading is.

The frontend is where a workspace's coupling is least visible from the backend: nothing on
the server side records that a screen depends on an endpoint, so a change to a path breaks a
user interface with no compile error anywhere. These edges are the only warning.

## A confidence ladder, not a boolean

Where a path is written says a great deal about how reliable the reading is, and collapsing
that into "found / not found" throws the useful half away:

  * a **central URL module** — one file every screen is supposed to import from — is the
    highest-confidence source there is, because its whole purpose is to be the single place
    a path appears;
  * a path written **inline** in a screen is still real and still a dependency, but it is
    also where typos, stale copies and half-finished migrations live.

Both are recorded. The difference lands in `confidence` and in `via`, so a reader ranking a
blast radius can tell "the app's URL table says so" from "one component has this string in
it" without having to open either file.

## Identity, not text

A path is normalised before it becomes a node, so `/orders/v1/detail/{orderId}` written in
the URL table and `/orders/v1/detail/{id}` written inline are ONE endpoint. They are the same
endpoint declared by two people with different taste in variable names, and splitting them
splits every impact query that touches either — while looking, from the outside, like a
component calling two endpoints.

## What is not read, and it is said rather than hidden

Only literals count, from a parse tree. A path assembled at runtime, or one held in a typed
source this has no grammar for, is not found — and where a grammar is missing that is
reported per component rather than passed over, because a frontend that silently produces no
edges is indistinguishable from one that calls nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk, srcfacts
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _lang

#: Sources this can read, and the grammar each needs. A source with no grammar still
#: activates — so that its absence is reported rather than being silently zero edges.
#: The map moved to `_lang` once a second reader needed it and got it wrong: two copies of
#: one question are two chances to answer it differently, which is what `_yaml` and
#: `_canonical` already exist to prevent. `INPUT_GLOBS` still decides which files this
#: reader claims; `_lang` only says what would parse them.
LANGUAGES = _lang.GRAMMARS

#: Filename stems whose whole job is to hold paths, and directories that mean the same.
CENTRAL_STEMS = {"urls", "url", "endpoints", "endpoint", "routes", "api", "paths"}
CENTRAL_DIRS = {"config", "constants"}

#: Node types holding a string, per grammar. Template literals carry interpolation, so a
#: path built from one is partial by construction and is deliberately not mined.
_LITERAL_TYPES = ("string", "string_literal")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def is_central(rel: str) -> bool:
    """Whether this file is the component's URL table rather than one of its screens."""
    parts = rel.lower().split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    return stem in CENTRAL_STEMS or any(p in CENTRAL_DIRS for p in parts[:-1])


def package_name(repo: RepoRef, files: list[Path]) -> str:
    """The published name of this user interface, or the component alias when unnamed."""
    for path in files:
        if path.name != "package.json":
            continue
        try:
            name = json.loads(repo_walk.read_text(path)).get("name")
        except (ValueError, AttributeError):
            continue
        if isinstance(name, str) and name.strip():
            return name.strip()
    return repo.alias



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
#: The parsers this module's readers can lack (batch 16, the owner's ruling 30): a shard
#: records each one's version when it was built, or that it was missing (batch 22).
PARSERS = ("javascript", "typescript", "tsx")

UNRESOLVED_FIELDS = {
    "grammar-unavailable": ('language',),
    "unparsable": ('error',),
}

class UrlsExtractor:
    """Endpoints a user interface calls, ranked by where the path was written."""

    name = "frontend-urls"
    INPUT_GLOBS = ("*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx", "package.json")

    def parser_of(self, path: Path, rel: str, text: str | None = None) -> str | None:
        """The grammar this reader needs for a file it is handed (batch 16): its folded
        suffix's, unless the file sits on a test path, which it skips. `extract` asks it
        for its gate, so the stamp and the reader never disagree."""
        language = LANGUAGES.get(path.suffix.lower())
        if language is None or repo_walk.looks_like_test(rel):
            return None
        return language

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            files = ctx.inputs(repo)
            ui = ids.ui_id(package_name(repo, files), repo.alias)
            emitted = False
            missing: set[str] = set()

            for path in files:
                rel = _rel(repo, path)
                language = self.parser_of(path, rel)
                if language is None:
                    continue
                if not srcfacts.available(language):
                    missing.add(language)
                    continue
                emitted |= self._file(res, repo, rel, path, ui, language)

            if emitted:
                res.nodes.append(Node(id=ui, type="ui", props={
                    "package": package_name(repo, files), "component": repo.alias}))
            for language in sorted(missing):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "grammar-unavailable", "language": language,
                    "source_file": "", "line": 0,
                    "why": (f"no parser is installed for {language}, so the paths those "
                            f"sources name are missing. A user interface that silently "
                            f"produces no edges is indistinguishable from one that calls "
                            f"nothing, which is why this is recorded rather than skipped"),
                })
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, rel: str, path: Path,
              ui: str, language: str) -> bool:
        try:
            parsed = srcfacts.parse(language, repo_walk.read_text(path))
        except Exception as exc:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": str(exc),
                "why": "the parser could not read this source, so what it calls is unknown",
            })
            return False

        central = is_central(rel)
        found = False
        for cap in parsed.string_literals(*_LITERAL_TYPES):
            if not ids.looks_like_path(cap.text):
                continue
            ep = ids.endpoint_id(cap.text)
            res.nodes.append(Node(id=ep, type="ep"))
            res.edges.append(Edge(
                src=ui, type="DG_CALLS", dst=ep, source_file=rel, line=cap.line,
                extractor=self.name,
                # The ladder. A path in the URL table is what the app is supposed to use; a
                # path inline is equally real and likelier to be stale, and a reader ranking
                # a blast radius needs to be able to tell them apart.
                confidence=0.95 if central else 0.8,
                provenance=Provenance.EXTRACTED if central else Provenance.AMBIGUOUS,
                props={"via": "urls-central" if central else "url-inline",
                       "literal": cap.text}))
            found = True
        return found


EXTRACTORS = (UrlsExtractor(),)
