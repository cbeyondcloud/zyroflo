"""Calls a component makes, assembled from a configured host and a path literal.

The mirror of the routes extractor, and the half that makes it worth anything: an endpoint
nobody calls and a call nobody serves are the same graph, and only the join tells you which
you are looking at.

## Neither half is a call

The address lives in the environment (`API_BASE = os.environ["PANTRY_API_URL"]`) and the
path is a literal in the source (`ITEMS_PATH = "/pantry/v1/items"`). Read alone, the first is
a string with no destination and the second is an endpoint with no caller. **Pairing them is
the extraction**, and the host's *value* comes from a layer outside this component entirely —
the deployment file — which is why this cannot be done by reading one component's own files.

## Pairing by co-location, and what that is worth

The two halves are joined because they sit in one module, not because anything links them.
That is an inference and it is scored as one: `confidence 0.9`, `provenance INFERRED`, the
same class as the configuration-key pairing on the other stack, which joins on a shared key
prefix. Both are "these two facts are adjacent, therefore related", and adjacency is usually
right and never certain.

**More than one resolvable host in a module and this emits nothing**, recording an ambiguity
instead. Which path is fetched from which host is a dataflow question, and answering it by
picking the first would produce edges that look exactly like the correct ones. Refusing to
guess is the same rule the canonical link pass follows when two components claim one service.

## What is cited, and why the path rather than the host

The edge carries the **path literal's** line; the host's file and line ride along in props.
The path is where a human changes which endpoint is called, and provenance that points
somewhere plausible-but-wrong is wrong in the way nothing notices.

## Deliberately not here

A **hardcoded** host (`BASE = "http://inventory:8000"`, no environment involved) is a real
pattern and no committed fixture contains one, so it is not read. Building it now would mean
asserting behaviour against an imagined input.

A module-level path literal in a file with **no** configured address produces nothing and is
not reported. Path-shaped constants are ordinary — filesystem paths, URL fragments, format
strings — and a report with one row per occurrence is a report that teaches its reader to
skip it. The asymmetry is deliberate: an unresolvable host in a module that *does* declare
paths IS reported, because there the module has said it intends to call something.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import env_keys

#: An address this extractor is willing to call an HTTP service.
_HTTP_URL = re.compile(r"^https?://", re.I)

#: A bare `host` or `host:port`, for deployments that configure an authority without a
#: scheme. Anchored on both ends so a sentence containing a hostname does not qualify.
_AUTHORITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?::\d+)?$")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def looks_like_host(value: str) -> bool:
    """Whether a resolved configuration value addresses an HTTP service.

    Decided from the VALUE, never from the key's name. A suffix rule (`*_URL`, `*_ENDPOINT`)
    reads intent out of a naming convention, which is the guess-wearing-a-fact's-clothes this
    workspace refuses everywhere else — and it fails in both directions: a database DSN is
    routinely called `*_URL`, and plenty of real service addresses are not.

    A non-HTTP scheme is rejected rather than accepted-with-doubt. `postgresql://db:5432/x`
    is an address, it is not an address this graph's `DG_CALLS` means, and admitting it would
    put a database on the receiving end of an HTTP call edge.
    """
    v = (value or "").strip()
    if not v:
        return False
    if _HTTP_URL.match(v):
        return True
    if "://" in v:
        return False
    return bool(_AUTHORITY.match(v))


def path_literals(tree: ast.Module) -> list[dict]:
    """Module-level names bound to a string literal that is plausibly a URL path.

    Module level only. A path built inside a function body is reachable in principle and
    needs dataflow to attribute, and a half-followed dataflow produces confident wrong
    answers rather than missing ones.
    """
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        value = node.value.value
        if not isinstance(value, str) or not ids.looks_like_path(value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out.append({"name": target.id, "path": value, "line": node.lineno})
    return out


class HttpCallsExtractor:
    """Outbound calls, paired from a configured host and a path literal in one module."""

    name = "http-calls"
    INPUT_GLOBS = ("*.py",)
    #: The deployment layer supplies the host's value and the inline-default layer may
    #: supply it instead, so both must have published before a host can be called
    #: unresolved. Without this ordering every deployment-configured caller reports a hole.
    REQUIRES = ("compose-services", "env-keys")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in sorted(ctx.inputs(repo)):
                rel = _rel(repo, path)
                # A path named in a test fixture is not a call this component makes. A
                # phantom caller, once created, arrives in every blast radius forever.
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, ctx, repo, path, rel, svc)
        return res

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, path: Path,
              rel: str, svc: str) -> None:
        text = repo_walk.read_text(path)
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            res.unresolved.append({
                "extractor": self.name, "kind": "python-unparsed", "component": repo.alias,
                "source_file": rel, "line": exc.lineno or 0,
                "why": f"could not be parsed as Python: {exc.msg}"})
            return

        paths = path_literals(tree)
        if not paths:
            return
        bound = env_keys.binding_sites(tree)
        if not bound:
            return

        hosts = []
        for name, site in sorted(bound.items()):
            entry = env_keys.resolve(ctx.cache, repo.alias, site["key"])
            if entry and looks_like_host(entry["value"]):
                hosts.append((name, site, entry))

        if not hosts:
            res.unresolved.append({
                "extractor": self.name, "kind": "call-host-unresolved",
                "component": repo.alias, "source_file": rel, "line": paths[0]["line"],
                "paths": [p["path"] for p in paths],
                "keys": sorted(s["key"] for s in bound.values()),
                "why": ("this module declares request paths and reads configuration, but no "
                        "key it reads resolves to an HTTP address — so the calls it makes "
                        "cannot be attributed to an endpoint")})
            return
        if len(hosts) > 1:
            res.unresolved.append({
                "extractor": self.name, "kind": "ambiguous-call-host",
                "component": repo.alias, "source_file": rel, "line": paths[0]["line"],
                "hosts": [s["key"] for _, s, _ in hosts],
                "why": ("more than one configured HTTP address is in scope here, and which "
                        "path is fetched from which is a dataflow question this does not "
                        "answer — picking one would produce edges indistinguishable from "
                        "correct ones")})
            return

        _, site, host = hosts[0]
        res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
        for p in paths:
            ep = ids.endpoint_id(p["path"])
            res.nodes.append(Node(id=ep, type="ep", props={"path": ids.norm_path(p["path"])}))
            res.edges.append(Edge(
                src=svc, type="DG_CALLS", dst=ep, source_file=rel, line=p["line"],
                extractor=self.name, confidence=0.9, provenance=Provenance.INFERRED,
                # Three coordinates, because the chain has three links and a reader landing
                # on any one of them needs the other two: where the path is written, where
                # this module reads the address, and where the address is set.
                props={"via": "env-host-pair", "host": host["value"],
                       "host_key": ids.relaxed_key(site["key"]),
                       "key_line": site["line"], "host_file": host["file"],
                       "host_line": host["line"], "declared_as": p["path"],
                       "binding": p["name"]}))


EXTRACTORS = (HttpCallsExtractor(),)
