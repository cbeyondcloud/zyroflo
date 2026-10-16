"""OpenAPI/Swagger documents: what an API *promises*, joined to what the code serves.

A spec is a claim about intent. The graph records it as `DG_SPECIFIES` edges from the
spec document to endpoint nodes — the SAME endpoint ids the source extractors emit, so
"is this endpoint spec-matched?" is a join, not a heuristic: an `ep` node carrying both
`DG_EXPOSES` and `DG_SPECIFIES` is served and documented, one carrying only the second is
specified and served nowhere, and neither situation is invented by proximity. That join
is the unit of the M1 coverage target ("endpoints spec-matched"), which had no producer
until this extractor.

## Recognition is by content, never by filename

Real estates name spec files after their services (`invoicing.yaml`, `gatekeeper.yaml`)
and keep them in a dedicated repository or beside the code, so a filename glob cannot
find them without claiming everything. Every YAML/JSON file is glob-claimed and then
**sniffed on raw text** for a top-level `openapi:`/`swagger:` key before any parse:

* a file that does not sniff is silently not a spec — an ordinary values file or a
  message bundle is not a loss, and rowing it would double-report files other
  extractors already own;
* a file that sniffs and then fails to parse IS a loss and is rowed with the parser's
  message — it claimed to be a spec and cannot be read;
* the sniff needs no YAML library, so under the zero-dependency gate the JSON half
  still works in full and only sniffed YAML files degrade, one row per component.

## Path identity

`/orders/v1/{orderId}/history` and `/orders/v1/{id}/history` are one endpoint declared
with two names for the same variable; `ids.norm_path` erases both to `{}` — the same
rule the source extractors apply, which is exactly why the join works.
"""
from __future__ import annotations

import json as json_lib
import re
from pathlib import Path

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef

#: `cfg` node kind for this format, from the generic kind vocabulary.
CONFIG_KIND = "openapi"

#: A top-level `openapi:`/`swagger:` mapping key (YAML) or property (JSON), at the start
#: of a line. Column 0 for YAML on purpose: an indented `openapi:` is somebody's field,
#: not a document version declaration.
_SNIFF = re.compile(r'^(?:openapi|swagger)[ \t]*:|"(?:openapi|swagger)"[ \t]*:', re.M)


def looks_like_spec(text: str) -> bool:
    """Whether raw text declares itself an OpenAPI/Swagger document."""
    return _SNIFF.search(text) is not None


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
#: The parsers this module's readers can lack (batch 16, the owner's ruling 30): a shard
#: records each one's version when it was built, or that it was missing (batch 22).
PARSERS = ("yaml",)

UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "grammar-unavailable": ('language',),
    "spec-without-paths": (),
    "unparsable": ('error',),
}


def _no_yaml(extractor: str, repo: RepoRef, n_files: int) -> dict:
    """One row per component when PyYAML is absent — only the sniffed YAML half is lost;
    JSON specs need no library and are read in full either way."""
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files, "language": "yaml",
        "why": (f"no parser is installed for yaml (PyYAML is absent), so {n_files} "
                f"file(s) that declare themselves OpenAPI documents were not read. Every "
                f"endpoint they specify is missing from the graph, and this row is the "
                f"only record of that"),
    }


class OpenApiExtractor:
    """Endpoints an API document specifies, cited at the path key's own line."""

    name = "openapi-specs"
    INPUT_GLOBS = ("*.yaml", "*.yml", "*.json")

    def parser_of(self, path: Path, rel: str, text: str | None = None) -> str | None:
        """The parser this reader needs for a file it is handed (batch 16): YAML for a
        `.yaml`/`.yml` file that declares itself a spec; none for a `.json` spec, which
        `json` reads in full without PyYAML, for a file that does not sniff, and for one that
        cannot be read (its row is `unreadable-file`, whatever the parser). `extract` asks it
        for the YAML decision with the text it holds; the stamp hands none, and it reads."""
        if path.suffix.lower() not in (".yaml", ".yml"):
            return None
        if text is None:
            text, read_err = repo_walk.read_text_reporting(path)
            if read_err:
                return None
        return "yaml" if looks_like_spec(text) else None

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            yaml_blocked = 0
            for path in sorted(ctx.inputs(repo)):
                text, read_err = repo_walk.read_text_reporting(path)
                if read_err:
                    res.unresolved.append({
                        "extractor": self.name, "component": repo.alias,
                        "kind": "unreadable-file", "source_file": _rel(repo, path), "line": 0,
                        "why": ("this file could not be read, so whether it is an API "
                                "specification is unknown; if it was, every endpoint it "
                                "specifies is missing from the graph rather than absent")})
                    continue
                if not looks_like_spec(text):
                    continue
                rel = _rel(repo, path)
                if path.suffix.lower() == ".json":
                    self._file(res, repo, rel, text, json_load=True)
                elif self.parser_of(path, rel, text) == "yaml" and not props.yaml_available():
                    yaml_blocked += 1
                else:
                    self._file(res, repo, rel, text, json_load=False)
            if yaml_blocked:
                res.unresolved.append(_no_yaml(self.name, repo, yaml_blocked))
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, rel: str, text: str,
              *, json_load: bool) -> None:
        if json_load:
            try:
                doc = json_lib.loads(text)
                error = ""
            except ValueError as exc:
                doc, error = None, " ".join(str(exc).split())
        else:
            doc, error = _load_yaml_text(text)
        if not isinstance(doc, dict):
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0,
                "error": error or f"parsed to {type(doc).__name__}, not a mapping",
                "why": ("this file declares itself an API document and could not be read "
                        "as one, so every endpoint it specifies is missing from the graph"),
            })
            return

        node_id = ids.config_id(repo.alias, rel)
        res.nodes.append(Node(id=node_id, type="cfg",
                              props={"kind": CONFIG_KIND, "path": rel,
                                     "component": repo.alias,
                                     "title": str((doc.get("info") or {}).get("title", ""))}))

        paths = doc.get("paths")
        if not isinstance(paths, dict) or not paths:
            # A spec with no paths specifies nothing — real (a components-only fragment,
            # a stub) and worth a row, because a reader comparing spec coverage would
            # otherwise count this document as covering something.
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias,
                "kind": "spec-without-paths", "source_file": rel, "line": 0,
                "why": ("this API document declares no paths, so it specifies no "
                        "endpoint; if it is a shared-components fragment this is its "
                        "normal state, and if it is a service's spec the service is "
                        "undocumented"),
            })
            return

        offset = 0
        for declared in paths:
            if not isinstance(declared, str) or not declared.startswith("/"):
                continue        # x-extensions and $ref siblings are not paths
            line, offset = _key_line(text, declared, offset)
            methods = sorted(m for m in (paths[declared] or {})
                             if isinstance(m, str) and m.lower() in _HTTP_METHODS)
            res.edges.append(Edge(
                src=node_id, type="DG_SPECIFIES", dst=ids.endpoint_id(declared),
                source_file=rel, line=line, extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED,
                props={"declared_path": declared, "methods": methods}))
            res.nodes.append(Node(id=ids.endpoint_id(declared), type="ep"))


_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _key_line(text: str, key: str, start: int) -> tuple[int, int]:
    """The path key's own line. YAML keys via the shared scanner; JSON keys are quoted,
    so the same anchored scan runs against the quoted spelling."""
    from tools.graph.extractors import _yaml
    line, offset = _yaml.key_line(text, key, start)
    if line:
        return line, offset
    return _yaml.key_line(text, f'"{key}"', start)


def _load_yaml_text(text: str):
    """YAML from text already in hand — `load_yaml_reporting` reads a path, and this
    file was read once for the sniff; reading it twice would be a second chance for the
    filesystem to disagree with itself. Reached only behind `props.yaml_available()`,
    and it uses props' own guarded module handle so there is exactly one import site
    for the optional library."""
    try:
        return props.yaml.safe_load(text), ""
    except props.yaml.YAMLError as exc:
        return None, " ".join(str(exc).split())


EXTRACTORS = (OpenApiExtractor(),)
