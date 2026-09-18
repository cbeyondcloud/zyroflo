"""HTTP routes declared by decorators, where the path is assembled rather than written.

The pattern this reads is the one every decorator-routed framework shares: a router object
carries a prefix, and each handler's decorator carries the rest. Neither half is the
endpoint.

## Why assembly, and not the decorator alone

Reading only the decorator yields `/items`. No caller ever requests `/items` — they request
`/pantry/v1/items` — so the graph would hold endpoints nobody calls beside calls nobody
serves, and **both halves look like findings**. That failure is the same one the annotation
concatenation on the Java side exists to prevent, arriving through a completely different
syntax: there the prefix is a class-level annotation, here it is a keyword argument to a
constructor held in a module-level variable.

## Parsed with `ast`, deliberately

`srcfacts` wraps tree-sitter because Java has no parser in the standard library. Python
does, and it is the same parser the interpreter uses — exact by construction, with no
grammar to pin and no version to drift. Using a less precise tool here to match the other
extractor's *mechanism* would be consistency for its own sake, at the cost of the accuracy
that motivated tree-sitter in the first place.

A file that will not parse is recorded as unresolved rather than crashing the build: a
source file written for a different language version is a normal thing to meet in a real
workspace, and a scan that dies on one is worse than a scan that reports it.

## Deliberately not here

`include_router(router, prefix=...)` adds a *second* prefix layer, and this fixture has no
instance of it. Building it now would mean writing against an imagined input and asserting
nothing — the mistake Rev 9 corrected by moving `openapi` to item 9. The limitation is
recorded here so its absence is a decision rather than an oversight.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance, unresolved_row
from tools.graph.core.spi import ExtractContext, RepoRef

#: Decorator attributes that declare a route. `websocket` is included because a websocket
#: endpoint is still an address this service serves and something still calls it.
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options",
                           "trace", "websocket", "route"})

#: Constructors that carry a routing prefix. Named for the shape rather than the framework:
#: an object built with `prefix=` and decorated with `.get(...)` is this pattern whatever
#: the library calls itself.
ROUTER_FACTORIES = frozenset({"APIRouter", "Router", "Blueprint", "FastAPI"})


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def router_prefixes(tree: ast.Module) -> dict[str, dict]:
    """Module-level router variables, each with its prefix and the line it is set on.

    A router built with no prefix maps to the empty string rather than being skipped: it
    still routes, and its handlers' paths are still endpoints.

    The line is carried because the endpoint is ASSEMBLED from two places, and an edge
    citing only the decorator sends a reader to half of the thing they need to change. Same
    reasoning as the configured-host reader beside this one, which records where the address
    is read as well as where the path is written.
    """
    out: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in ROUTER_FACTORIES:
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix":
                prefix = _literal(kw.value) or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = {"prefix": prefix, "line": node.lineno}
    return out


def routes(tree: ast.Module, prefixes: dict[str, str]) -> list[dict]:
    """Every decorated route in the module: assembled path, method, and the decorator's line.

    The line is the DECORATOR's, not the function's. That is where a human edits the path,
    and provenance that points one line off is wrong in a way nothing notices.
    """
    found: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            owner = dec.func.value
            if not isinstance(owner, ast.Name) or owner.id not in prefixes:
                continue
            if dec.func.attr not in ROUTE_METHODS:
                continue
            path = _literal(dec.args[0]) if dec.args else None
            if path is None:
                continue
            found.append({
                "path": ids.join_path(prefixes[owner.id]["prefix"], path),
                "method": dec.func.attr.upper(),
                "router": owner.id,
                "declared_as": path,
                "prefix": prefixes[owner.id]["prefix"],
                "prefix_line": prefixes[owner.id]["line"],
                "line": dec.lineno,
            })
    return found



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "python-unparsed": ('error',),
    "unreadable-file": (),
}

class HttpRoutesExtractor:
    """Endpoints a component serves, assembled from a router prefix and a decorator path."""

    name = "http-routes"
    INPUT_GLOBS = ("*.py",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in sorted(ctx.inputs(repo)):
                rel = _rel(repo, path)
                # Test sources are claimed by the tests extractor. Reading them here would
                # turn a path named in a fixture into an endpoint this service serves.
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, repo, path, rel, svc)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str,
              svc: str) -> None:
        text, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "unreadable-file", rel, 0,
                "this file could not be read, so every route it declares is missing from the "
                "graph rather than absent from the code"))
            return
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "python-unparsed", rel, exc.lineno or 0,
                "could not be parsed as Python, so every key, route or call it declares is "
                "missing from the graph rather than absent from the code",
                error=exc.msg))
            return

        prefixes = router_prefixes(tree)
        if not prefixes:
            return
        found = routes(tree, prefixes)
        if not found:
            return
        res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
        for route in found:
            ep = ids.endpoint_id(route["path"])
            res.nodes.append(Node(id=ep, type="ep", props={"path": route["path"]}))
            res.edges.append(Edge(
                src=svc, type="DG_EXPOSES", dst=ep, source_file=rel, line=route["line"],
                extractor=self.name, confidence=1.0, provenance=Provenance.EXTRACTED,
                props={"method": route["method"], "declared_as": route["declared_as"],
                       "router": route["router"], "prefix": route["prefix"],
                       "prefix_line": route["prefix_line"]}))


EXTRACTORS = (HttpRoutesExtractor(),)
