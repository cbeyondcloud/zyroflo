"""Web endpoints a service exposes, and the services it calls through configuration.

Two extractors, both about one framework's configuration model — that a path is assembled
from several places, and that a client's target is a pair of properties rather than a URL
written in code.

## Why an endpoint is not the string in the annotation

The declared path is a fragment. The real path is `context-path` + class mapping + method
mapping, and the first of those lives in a properties file, not in the source. An extractor
that reads only the annotation produces `/v1/list` where the service actually serves
`/orders/v1/list` — a plausible, wrong endpoint that will never match the caller's URL, so
the graph shows a service exposing endpoints nobody calls and a UI calling endpoints nobody
exposes. Both halves look like real findings.

## Where the pairing convention belongs

`rest_calls` reads a host key and a path key that share a prefix. That is a *convention*,
not a framework feature — nothing enforces it — and it is here rather than in a layer of its
own only because the files it reads are this framework's. If a workspace without the
framework needs the same pairing, lift it out; nothing in it depends on the framework.
"""
from __future__ import annotations

from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _java
from tools.graph.extractors.properties import ambiguity_of, lookup, view_for

#: Class-level annotations that contribute a path prefix.
CLASS_MAPPINGS = {"RequestMapping"}

#: Method-level mapping annotations and the verb each implies. A bare `RequestMapping`
#: declares its verb in an argument, or none at all.
METHOD_MAPPINGS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "",
}

#: Annotations that mark a class as serving HTTP.
CONTROLLER_MARKERS = {"RestController", "Controller"}

#: Keys that declare the prefix every mapping in the component sits under.
CONTEXT_PATH_KEYS = ("server.servlet.context-path", "server.context-path")

#: Suffixes that make a key look like the ADDRESS of something being called.
HOST_SUFFIXES = (".host", ".url", ".uri", ".endpoint", ".base.url", ".baseurl")

#: Suffixes that make a key look like a PATH on such a host.
PATH_SUFFIXES = (".path", ".endpoint.path", ".uri.path")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _mapping_value(ann: _java.Annotation) -> str:
    """The path a mapping annotation declares, whether written bare or as `value = ...`."""
    if ann.value is not None:
        return ann.value
    return ann.args.get("value") or ann.args.get("path") or ""


def context_path(view: dict | None, source_file: str = "") -> dict | None:
    """The path prefix that applies to THIS file, with the line that declared it.

    Per file rather than per component, because a component holding several applications
    holds several prefixes — on the measured estate every such component declared between
    4 and 26 of them, and the old component-wide answer was whichever file sorted last,
    applied to every controller. The asking file's own scope answers; a file whose scope
    declares none gets None rather than a prefix borrowed from another application.
    """
    for key in CONTEXT_PATH_KEYS:
        hit = lookup(view, key, source_file) if view is not None else None
        if hit is not None:
            return hit
    return None



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "grammar-unavailable": ('language',),
    "missing-context-path": (),
    "no-config-view": (),
    "unpaired-host": ('key', 'value'),
    "unparsable": ('error',),
}

class ControllersExtractor:
    """Endpoints a component serves, assembled from all three places that name them."""

    name = "spring-controllers"
    INPUT_GLOBS = ("*.java",)
    #: The path prefix lives in a properties file, so the resolved view must exist first.
    REQUIRES = ("properties",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not _java.available():
            for repo in ctx.repos:
                res.unresolved.append(_no_grammar(self.name, repo, len(ctx.inputs(repo))))
            return res

        for repo in ctx.repos:
            view = view_for(ctx.cache, repo.alias)
            svc = ids.component_service_id(repo.alias)

            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                # Test sources are the `tests` extractor's. A mapping found in one is a test
                # fixture, and turning it into a served endpoint invents an entity that
                # nothing deploys.
                if repo_walk.looks_like_test(rel):
                    continue
                # Per file: a component holding several applications holds several
                # prefixes, and the one that applies is the one this file's scope declares.
                prefix = context_path(view, rel)
                found = self._file(res, repo, rel, path, svc, prefix)
                if found and prefix is None:
                    contested = next((ambiguity_of(view, k) for k in CONTEXT_PATH_KEYS
                                      if ambiguity_of(view, k) is not None), None)
                    if contested is not None:
                        why = (f"{found} mapping(s) here, and {CONTEXT_PATH_KEYS[0]} is "
                               f"declared with {len(contested['values'])} differing values "
                               f"across this component's configuration scopes, none in this "
                               f"file's own scope — the endpoint paths are recorded WITHOUT "
                               f"a prefix rather than borrowing another application's")
                    else:
                        why = (f"{found} mapping(s) here, and no {CONTEXT_PATH_KEYS[0]} is "
                               f"declared in this component — the endpoint paths are "
                               f"recorded WITHOUT a prefix and may be shorter than what the "
                               f"service actually serves")
                    res.unresolved.append({
                        "extractor": self.name, "component": repo.alias,
                        "kind": "missing-context-path", "source_file": rel, "line": 0,
                        "why": why,
                    })
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, rel: str, path: Path,
              svc: str, prefix: dict | None) -> int:
        text = repo_walk.read_text(path)
        try:
            parsed = _java.parse(text)
        except Exception as exc:                # a file this grammar cannot parse
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": str(exc),
                "why": "the parser could not read this file, so any mapping in it is missing",
            })
            return 0

        count = 0
        for cls in parsed.classes:
            class_ann = next((a for a in cls.annotations if a.name in CLASS_MAPPINGS), None)
            mapped = [(m, a) for m in cls.methods
                      for a in m.annotations if a.name in METHOD_MAPPINGS]
            # The mapping annotations ARE the evidence; a controller marker is recorded but
            # not required, because it is routinely inherited from a base class and demanding
            # it would drop real endpoints without a word.
            if not mapped:
                continue
            marked = any(a.name in CONTROLLER_MARKERS for a in cls.annotations)

            class_path = _mapping_value(class_ann) if class_ann else ""
            for method, ann in mapped:
                endpoint = ids.join_path(prefix["value"] if prefix else "",
                                         class_path, _mapping_value(ann))
                res.nodes.append(Node(id=ids.endpoint_id(endpoint), type="ep"))
                res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
                res.edges.append(Edge(
                    src=svc, type="DG_EXPOSES", dst=ids.endpoint_id(endpoint),
                    source_file=rel, line=ann.line, extractor=self.name,
                    confidence=1.0 if prefix else 0.7,
                    props={
                        "http_method": METHOD_MAPPINGS[ann.name] or ann.args.get("method", ""),
                        "declared_class": cls.name, "declared_method": method.name,
                        "marked_controller": marked,
                        "class_mapping_line": class_ann.line if class_ann else 0,
                        "context_path": prefix["value"] if prefix else "",
                        "context_path_source": (f"{prefix['file']}:{prefix['line']}"
                                                if prefix else ""),
                    }))
                count += 1
        return count


class RestCallsExtractor:
    """Outbound calls declared as a host key paired with a path key."""

    name = "spring-rest-calls"
    INPUT_GLOBS = ("*.properties",)
    #: Reads the resolved view rather than the files, so it must run after the resolver.
    REQUIRES = ("properties",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            view = view_for(ctx.cache, repo.alias)
            if view is None:
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "no-config-view",
                    "source_file": "", "line": 0,
                    "why": ("no resolved configuration was published for this component, so "
                            "no host/path pair could be read"),
                })
                continue
            self._component(res, repo, view)
        return res

    def _component(self, res: ExtractResult, repo: RepoRef, view: dict) -> None:
        # A host and a path only configure ONE call when the same scope declares both. The
        # flat component-wide view paired keys across applications that share nothing but a
        # repository — on the measured estate, 46 of its pairs existed in no single scope
        # (a database URL paired with a migration directory, two services' keys glued into
        # a call neither makes) and every pair a scope really declares survives this.
        scopes = view.get("scopes") or {("", ""): view}
        for fam in sorted(scopes):
            self._scope(res, repo, scopes[fam]["values"])

    def _scope(self, res: ExtractResult, repo: RepoRef, values: dict) -> None:
        svc = ids.component_service_id(repo.alias)
        for key, host in sorted(values.items()):
            if not key.endswith(HOST_SUFFIXES):
                continue
            prefix = key.rsplit(".", 1)[0]
            paths = [(k, v) for k, v in sorted(values.items())
                     if k.startswith(f"{prefix}.") and k.endswith(PATH_SUFFIXES)]
            if not paths:
                # A declared address nobody calls a path on. Reported rather than dropped:
                # it is either a dangling reference or a call this extractor cannot see, and
                # both are findings. The reverse — a path key with no host — is NOT reported,
                # because context paths and filesystem paths dominate that shape and the
                # noise would train a reader to ignore the report.
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "unpaired-host",
                    "key": key, "value": host["value"], "source_file": host["file"],
                    "line": host["line"],
                    "why": ("a host is declared here and no path key shares its prefix, so "
                            "the call it configures cannot be resolved to an endpoint"),
                })
                continue
            for path_key, path in paths:
                ep = ids.endpoint_id(path["value"])
                res.nodes.append(Node(id=ep, type="ep"))
                res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
                res.edges.append(Edge(
                    src=svc, type="DG_CALLS", dst=ep,
                    source_file=path["file"], line=path["line"], extractor=self.name,
                    confidence=0.9, provenance=Provenance.INFERRED,
                    props={"via": "config-pair", "host": host["value"], "host_key": key,
                           "host_line": host["line"], "path_key": path_key},
                ))


def _no_grammar(extractor: str, repo: RepoRef, n_files: int) -> dict:
    """One row per component, not per file — enough to see the loss, not enough to bury it."""
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files,
        # Promised by this module's UNRESOLVED_FIELDS and absent until item 86. The row
        # carried `files` — how much was lost — while omitting what was lost, and its prose
        # said "this language" without naming it. This branch only runs where the java
        # grammar is missing, which is neither this machine nor CI, so nothing ever ran it.
        "language": "java",
        "why": (f"no parser is installed for java, so {n_files} file(s) were not "
                f"read. Any edge they declare is missing from the graph, and its absence "
                f"here is the only record of that"),
    }


EXTRACTORS = (ControllersExtractor(), RestCallsExtractor())
