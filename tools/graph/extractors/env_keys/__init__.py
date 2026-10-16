"""Configuration keys read from the environment by code, and what they resolve to.

The analogue of the properties extractor for a workspace that configures itself through the
environment rather than through files in the component. Like that one, its product is a
**resolved view** in `ctx.cache` rather than its own edges — resolving a key once is not an
optimisation, because two extractors resolving it independently is two chances to resolve it
differently and the graph would hold both answers with no way to tell which is wrong.

## Two sources, and the difference between them is the whole layering

An inline default (`os.getenv("Q", "events-v2")`) is a value this component declares about
itself, and it lands in this view. A key read with no default (`os.environ["Q"]`) is a value
this component *requires* and does not have; that value lives in the deployment layer, which
the compose extractor publishes separately. Consumers read both through `resolve()` below —
**deployment first, inline default second** — so the layering rule is stated once rather
than re-derived per consumer.

That sentence used to say "check both, in that order" meaning inline-default first, on the
reading that the two layers are disjoint. They are disjoint per read *site* and not per
*key*, because relaxed binding collapses several spellings into one — see `resolve()` for
what that cost.

This is why the extractor `REQUIRES` the deployment reader: a key with no default is only
genuinely unresolved once the deployment layer has been consulted and come up empty. Without
that ordering every such key would be reported missing, and a report where the real holes
are buried in false ones is a report nobody reads twice.

## The distinction that keeps E7 honest

"Nothing sets this key" and "the deployment layer was not read in this build" produce the
same emptiness and mean completely different things. `--only` can exclude the compose
extractor entirely, so the unresolved row says WHICH it was. Reporting the first when the
second is true would send someone hunting for a hole that is not there.

## Parsed with `ast`

Python ships its own parser — the same one the interpreter uses. See the http_routes module
for why that is preferred here over the tree-sitter wrapper Java needs.

## Deliberately not here

No `service → key` edge. "Which services read this key" is a real blast-radius question and
it needs an edge type that does not exist yet, whose only consumer is the impact engine at
M2. Inventing the type now means designing it against an imagined query — the same reason
the compose extractor does not emit `depends_on`.

`from os import environ` and `os.environ.setdefault` are not read. Both are real, neither
appears here, and building against an imagined input is how a fixture stops being a gate.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import ExtractResult, Node, unresolved_row
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import compose

#: Where the resolved view lands. Separate from the deployment layer's, deliberately: which
#: of the two wins is the layering question, and merging them here would answer it by
#: accident and in whichever order the extractors happened to run.
CACHE_KEY = "envkeys"


def view_for(cache: dict, alias: str) -> dict | None:
    """This component's resolved environment reads, or None when nothing published one."""
    return cache.get(CACHE_KEY, {}).get(alias)


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_environ(node: ast.AST) -> bool:
    """`os.environ`, spelled as an attribute of a name called `os`."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def env_reads(tree: ast.Module) -> list[dict]:
    """Every environment read in the module: key, line, and its inline default if any.

    `default` is None when the read has none — distinct from an empty-string default, which
    is a real value somebody chose. Collapsing the two would turn "this must be configured"
    into "this defaults to nothing", and nothing downstream could tell.
    """
    out: list[dict] = []
    for node in ast.walk(tree):
        # os.environ["KEY"]
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            key = _literal(node.slice)
            if key:
                out.append({"key": key, "default": None, "line": node.lineno})
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # os.getenv("KEY", "default") / os.environ.get("KEY", "default")
        is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv"
                     and isinstance(func.value, ast.Name) and func.value.id == "os")
        is_environ_get = (isinstance(func, ast.Attribute) and func.attr == "get"
                          and _is_environ(func.value))
        if not (is_getenv or is_environ_get):
            continue
        key = _literal(node.args[0]) if node.args else None
        if not key:
            continue
        default = _literal(node.args[1]) if len(node.args) > 1 else None
        out.append({"key": key, "default": default, "line": node.lineno})
    return out


def binding_sites(tree: ast.Module) -> dict[str, dict]:
    """Module-level names bound to an environment read, with the line the read is on.

    Exported because a later extractor meeting `producer.produce(QUEUE, ...)` has to get
    from that name back to the key. It reads the same AST it already parsed, so this is
    shared code rather than a shared cache entry — one implementation of "what does this
    name hold", without a second contract to keep in step.

    The line matters as much as the key. A consumer citing only where a value was *set* and
    not where this module *reads* it hands a reader the far end of the chain and leaves them
    to find the near end themselves — and the near end is usually the one they were going to
    edit.
    """
    out: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        reads = env_reads(ast.Module(body=[node], type_ignores=[]))
        if len(reads) != 1:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = {"key": reads[0]["key"], "line": reads[0]["line"]}
    return out


def bindings(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound to an environment read: `{"QUEUE": "RESTOCK_QUEUE"}`.

    The name-to-key view of `binding_sites`, for consumers that do not need the line.
    """
    return {name: site["key"] for name, site in binding_sites(tree).items()}


def resolve(cache: dict, alias: str, key: str) -> dict | None:
    """What a component sees for one configuration key, or None when nothing sets it.

    The read side of the two views, and deliberately the ONLY one. A second consumer
    resolving keys its own way is a second chance to resolve them differently, and the graph
    would then hold both answers with nothing to say which is wrong — the same reasoning
    that put the resolved views in the cache instead of inside each extractor.

    Returns the view entry (`value`, `file`, `line`, `raw`, `spellings`) rather than the bare
    value, so a consumer can cite where a value came from and not only what it is.

    **The deployment layer wins, and that order was established the expensive way.** This
    first shipped the other way round — inline default first — on the reading that the two
    layers are disjoint, since a key read WITH a default never needs the deployment and a
    key read WITHOUT one has no default to offer. That is true per read site and false per
    *key*: relaxed binding collapses `RESTOCK_QUEUE`, `restock.queue` and `restock-queue`
    into one key, so a default written beside one spelling silently answered for all three.
    In the second fixture that default is the empty string, and the topic a whole component
    publishes to resolved to `""` — a node named nothing, produced by a chain that looked
    entirely healthy at every step.

    The order now matches what a running process does: an environment variable set by the
    deployment overrides the default written in code. It is pinned by its own test, because
    the only reason the old order survived review is that nothing had ever exercised it.
    """
    relaxed = ids.relaxed_key(key)
    for view in (compose.view_for(cache, alias), view_for(cache, alias)):
        entry = (view or {}).get("values", {}).get(relaxed)
        if entry is not None:
            return entry
    return None



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "env-key-unset": ('key',),
    "env-key-from-building-repo": ('key', 'origin'),
    "python-unparsed": ('error',),
    "superseded-declaration": ('key', 'value'),
    "unreadable-file": (),
}

class EnvKeysExtractor:
    """Environment keys code reads, and the values it declares inline for them."""

    name = "env-keys"
    #: Every entry it writes is keyed by the component it ran for; what it reads of other
    #: components comes through the producer it REQUIRES (batch 22, T633).
    CACHE_SCOPE = "component"
    CACHE_KEYS = (CACHE_KEY,)
    INPUT_GLOBS = ("*.py",)
    REQUIRES = ("compose-services",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        views: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        # None means the deployment reader never ran. An empty dict means it ran and found
        # nothing. Those are different answers and the unresolved rows say which.
        deployed = ctx.cache.get(compose.CACHE_KEY)
        origins = ctx.cache.get(compose.ORIGIN_KEY) or {}
        for repo in ctx.repos:
            view = views.setdefault(repo.alias, {"values": {}, "unresolved": {}})
            for path in sorted(ctx.inputs(repo)):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, repo, path, rel, view, deployed, origins)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str, view: dict,
              deployed: dict | None, origins: dict) -> None:
        text, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "unreadable-file", rel, 0,
                "this file could not be read, so every key it declares is missing from the "
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

        for read in env_reads(tree):
            key, relaxed = read["key"], ids.relaxed_key(read["key"])
            res.nodes.append(Node(id=ids.env_key_id(key), type="envkey",
                                  props={"key": relaxed}))
            self._from_building_repo(res, repo, rel, read, key, relaxed, deployed, origins)
            if read["default"] is not None:
                entry = {"value": read["default"], "file": rel, "line": read["line"],
                         "raw": read["default"],
                         "spellings": {key: {"file": rel, "line": read["line"],
                                             "raw": read["default"]}}}
                existing = view["values"].get(relaxed)
                if existing is None:
                    view["values"][relaxed] = entry
                else:
                    if existing["value"] != entry["value"]:
                        # A second inline default disagrees with the first. Keep the first
                        # value and REPORT the loser, the way compose and helm guard the
                        # identical site — a value kept beside provenance that now names a
                        # different value is exactly the silent disagreement this row exists
                        # to surface.
                        res.unresolved.append({
                            "extractor": self.name, "kind": "superseded-declaration",
                            "component": repo.alias, "key": relaxed, "source_file": rel,
                            "line": read["line"], "value": entry["value"],
                            "why": (f"`{key}` defaults to {entry['value']!r} here and to "
                                    f"{existing['value']!r} in {existing['file']}, which is "
                                    f"read first. The first value is what the graph uses; this "
                                    f"one is reported because a second inline default nobody "
                                    f"reconciled is a finding, not noise")})
                    # setdefault, not update: the kept value's own spelling provenance must
                    # not be overwritten by the superseded declaration's.
                    for spelling, prov in entry["spellings"].items():
                        existing["spellings"].setdefault(spelling, prov)
                continue
            if self._deployment_sets(deployed, repo.alias, relaxed):
                continue
            view["unresolved"].setdefault(relaxed, {
                "raw": key, "missing": [key], "file": rel, "line": read["line"]})
            res.unresolved.append({
                "extractor": self.name,
                "kind": "env-key-unset", "component": repo.alias, "key": relaxed,
                "source_file": rel, "line": read["line"],
                "why": (f"`{key}` is read with no default and nothing sets it"
                        if deployed is not None else
                        f"`{key}` is read with no default, and the deployment layer was "
                        f"not read in this build so its value could not be checked")})

    def _from_building_repo(self, res: ExtractResult, repo: RepoRef, rel: str, read: dict,
                            key: str, relaxed: str, deployed: dict | None,
                            origins: dict) -> None:
        """Ruling 4 (T534): the value this read resolves to was set by ANOTHER repo's compose
        file, one that also builds code, for a service it does not build. That shape is an
        orchestration repo running this component's prebuilt image - the value is right -
        or a dev file running a stock container that shares this component's name - the
        value is not this component's. The file cannot say which, so the value is used, as
        it always was, and this row says where it came from.

        The row carries the key and the origin FILE only - no value, no line (ruling 5). It
        lives in this component's shard, so anything of the other repo's it carried would
        drift this shard on an edit there; a file name changes only when the setting's
        source moves, which is the coupling `env-key-unset` already has."""
        entry = ((deployed or {}).get(repo.alias) or {}).get("values", {}).get(relaxed)
        origin = origins.get(repo.alias, {}).get(relaxed)
        if entry is None or not origin or not origin.get("local") \
                or origin.get("component") == repo.alias:
            return
        where = f"{origin['component']}/{origin['file']}"
        service = origin.get("service", "")
        what = (f"`{service}`, whose build that file does not place" if origin.get("unplaced")
                else f"`{service}`, a container it does not build")
        res.unresolved.append(unresolved_row(
            self.name, repo.alias, "env-key-from-building-repo", rel, read["line"],
            f"`{key}` resolves from {where}, a compose file that also builds code and sets it "
            f"for {what}. If that is this component's deployment the value is right; if it is "
            f"that repo's own local container, the value is not this component's - read it "
            f"there",
            key=relaxed, origin=where))

    @staticmethod
    def _deployment_sets(deployed: dict | None, alias: str, relaxed: str) -> bool:
        if not deployed:
            return False
        entry = deployed.get(alias)
        return bool(entry and relaxed in entry.get("values", {}))


EXTRACTORS = (EnvKeysExtractor(),)
