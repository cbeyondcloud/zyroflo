"""Compose files: what services exist, and what environment each one actually runs with.

Like the properties extractor, this emits some edges of its own but its real product is a
**resolved environment view** left in `ctx.cache` for later extractors to read.

## Why this layer matters more here than a chart layer does

In a workspace whose components carry their own `application.properties`, configuration and
the code reading it live in one directory, so an extractor confined to a component finds
both halves. Compose inverts that: a service's environment is declared in the *deployment*
component, and the code reading it lives somewhere else entirely. An extractor that only
reads a component's own files sees `os.environ["QUEUE"]`, finds no value anywhere near it,
and stops one hop short of wherever that queue goes.

That is not a Python fact or a compose fact. It is what any workspace looks like once
deployment is a separate concern, and it is the reason this view is published rather than
resolved privately.

## Which component a service belongs to

`build:` names a directory, and a directory is a component or it is not — a **declared**
link, so it is taken at full confidence. Matching by name instead (service `pantry-api` must
mean component `pantry-api`) is a coincidence that usually holds, so it is the fallback and
it is recorded as INFERRED rather than presented as the same kind of fact. A service that
maps to neither — a stock database image, say — still publishes its environment under its
own name, because a service this workspace does not build is still a service it runs.

## The cache contract

`ctx.cache["composeenv"][<component alias or service name>]` has **exactly the shape the
properties extractor publishes**, so `properties.lookup()` reads it unchanged and there is
one resolution rule rather than two that can disagree:

    {"values": {"<relaxed key>": {"value", "file", "line", "raw", "spellings"}},
     "unresolved": {"<relaxed key>": {"raw", "missing", "file", "line"}}}

It is a **separate cache key** rather than a merge into the properties view. Whether a
deployment's value overrides what a component declares for itself is the layering question,
and layering across components is a link pass; silently merging here would answer it by
accident and in whichever order the extractors happened to run.

## `${VAR:-default}`

Compose writes `:-` where a properties file writes `:`. Both already parse: `props.PLACEHOLDER`
covers the two spellings, which is worth stating because it means this extractor adds no
interpolation of its own. A second implementation is how one workspace ends up resolving a
placeholder two ways.

## Deliberately not here

`depends_on` is a real coupling and is not emitted as `DG_DEPENDS_ON`. That edge type
currently carries *build*-time module dependencies with versions, and putting a runtime
start-order edge under the same name would make a version-carrying query return rows that
have no version. It needs its own type, which needs a consumer, and neither exists yet.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef

#: Where the resolved environment view lands for later extractors to read.
CACHE_KEY = "composeenv"

#: `cfg` node kind for this format, from the generic kind vocabulary.
CONFIG_KIND = "compose"


def view_for(cache: dict, alias: str) -> dict | None:
    """This component's resolved compose environment, or None when nothing published one.

    None is a real answer: a workspace with no compose files has no entry, and `--only` can
    exclude this extractor entirely. A consumer that gets None records an unresolved row
    naming what it could not look up — never a crash, and never a guess.
    """
    return cache.get(CACHE_KEY, {}).get(alias)


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def key_line(text: str, key: str, start: int = 0) -> tuple[int, int]:
    """Where `key` is declared as a MAPPING KEY: (1-based line, offset just past it).

    **Not `props.find_line`, and the difference is a real defect rather than a refinement.**
    That helper is a substring search, which is exactly right for a properties file, where a
    key is at the start of its line by construction. YAML has no such guarantee: this
    fixture's compose file declares a service `db` on line 22 and contains
    `postgresql://db:5432/pantry` in a value on line 11, so the substring search reports the
    service as declared eleven lines before it is.

    That is wrong in the way nothing notices. The number is plausible, the edge is otherwise
    correct, and only someone checking the line against the file would ever find it — which
    is why the answer key asserts line numbers at all.

    Anchored after indentation and an optional list dash, which is what makes it a key. The
    returned offset lets a caller keep searching *forward* from a service's own line, so two
    services declaring the same variable do not both resolve to the first one's.
    """
    m = re.compile(rf"^[ \t]*-?[ \t]*{re.escape(key)}[ \t]*:", re.M).search(text, start)
    if m is None:
        return 0, start
    return text.count("\n", 0, m.start()) + 1, m.end()


def environment_pairs(block: Any) -> list[tuple[str, str]]:
    """The `environment:` block as (key, value) pairs, in either spelling compose allows.

    A map (`KEY: value`) and a list (`- KEY=value`) mean the same thing, and a reader that
    handles only the map form silently produces a service with no environment at all — which
    is indistinguishable from a service that genuinely has none.

    A bare `- KEY` in list form means "inherit from the host" and carries no value here. It
    yields an empty string rather than being dropped: the key is referenced, and a key this
    workspace names is a key the graph should know about.
    """
    if isinstance(block, dict):
        return [(str(k), "" if v is None else str(v)) for k, v in block.items()]
    if isinstance(block, list):
        out = []
        for item in block:
            if not isinstance(item, str):
                continue
            key, sep, value = item.partition("=")
            out.append((key.strip(), value.strip() if sep else ""))
        return out
    return []


def target_component(compose_file: Path, service: dict, repos: list[RepoRef],
                     name: str) -> tuple[str, str]:
    """Which component this service is, and how that was decided.

    Returns (alias, how). `how` is `"build"` for a declared build context, `"name"` for the
    name coincidence, and `""` when neither reaches a component — kept distinct so a
    consumer can rank a declaration above a guess instead of treating them alike.
    """
    build = service.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    if isinstance(context, str) and context.strip():
        try:
            resolved = (compose_file.parent / context.strip()).resolve()
        except OSError:                       # pragma: no cover - platform-dependent
            resolved = None
        if resolved is not None:
            for repo in repos:
                if repo.path.resolve() == resolved:
                    return repo.alias, "build"
    for repo in repos:
        if repo.alias == name:
            return repo.alias, "name"
    return "", ""


class ComposeExtractor:
    """Services a compose file declares, and the environment it gives each one."""

    name = "compose-services"
    INPUT_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml",
                   "compose*.yml", "compose*.yaml")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        views: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        for repo in ctx.repos:
            for path in sorted(ctx.inputs(repo)):
                self._file(res, ctx, repo, path, views)
        return res

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, path: Path,
              views: dict[str, dict]) -> None:
        rel = _rel(repo, path)
        doc, error = props.load_yaml_reporting(path)
        if error:
            # E6. Recorded, and the build continues: one unreadable file must not abort a
            # workspace scan, and must not vanish from it either.
            res.unresolved.append({
                "kind": "compose-unparsed", "component": repo.alias, "source_file": rel,
                "line": 0, "why": f"could not be parsed as YAML: {error}"})
            return
        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict):
            return

        text = props.read_text(path)
        deploy = ids.deployment_id(repo.alias, rel)
        res.nodes.append(Node(id=deploy, type="deploy", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))
        cfg = ids.config_id(repo.alias, rel)
        res.nodes.append(Node(id=cfg, type="cfg", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))

        # Scanned forward, service by service, so a variable declared under two services
        # does not resolve to the first one's line for both of them.
        cursor = 0
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            line, cursor = key_line(text, str(name), cursor)
            self._service(res, ctx, repo, rel, text, deploy, cfg, str(name), service,
                          views, line, cursor)

    def _service(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
                 text: str, deploy: str, cfg: str, name: str, service: dict,
                 views: dict[str, dict], line: int, cursor: int) -> None:
        alias, how = target_component(repo.path / rel, service, ctx.repos, name)

        # The claim on a service, in exactly the shape `mark-canonical` groups on. Two files
        # naming one service produce two of these, identically, and the workspace's own
        # [[canonical]] block decides — never this extractor, which cannot see intent.
        target = ids.service_id(name, repo.alias)
        res.nodes.append(Node(id=target, type="svc", props={"component": repo.alias}))
        res.edges.append(Edge(
            src=deploy, type="DG_DEPLOYS", dst=target, source_file=rel, line=line,
            extractor=self.name, confidence=1.0,
            props={"service": name, "component": alias, "mapped_by": how}))

        pairs = environment_pairs(service.get("environment"))
        if not pairs:
            return
        declarations = [{"key": k, "value": v, "file": rel,
                         "line": key_line(text, k, cursor)[0] or line}
                        for k, v in pairs]
        view = _resolve(declarations)
        views.setdefault(alias or name, {"values": {}, "unresolved": {}})
        _merge(views[alias or name], view)

        for entry in declarations:
            node = ids.env_key_id(entry["key"])
            res.nodes.append(Node(id=node, type="envkey",
                                  props={"key": ids.relaxed_key(entry["key"])}))
            resolved = view["values"].get(ids.relaxed_key(entry["key"]))
            res.edges.append(Edge(
                src=cfg, type="DG_CONFIGURES", dst=node, source_file=rel,
                line=entry["line"], extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED,
                props={"declared_as": entry["key"], "service": name,
                       "value": resolved["value"] if resolved else "",
                       "raw": entry["value"]}))
        for key, miss in view["unresolved"].items():
            res.unresolved.append({
                "kind": "compose-env-unresolved", "component": repo.alias, "key": key,
                "source_file": rel, "line": miss["line"],
                "why": (f"`{miss['raw']}` names {', '.join(miss['missing'])}, which nothing "
                        f"in this file sets and which carries no default")})


def _resolve(declarations: list[dict]) -> dict:
    """Interpolate a service's environment against itself.

    Two passes, because compose lets one variable reference another and the declaration
    order in a file says nothing about dependency order. A placeholder that still cannot be
    resolved is kept OUT of the values map — absence is the safe failure, where returning the
    literal would hand every consumer a plausible string and name an entity after it.
    """
    literal = {ids.relaxed_key(d["key"]): d["value"] for d in declarations
               if "${" not in d["value"]}
    values: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    for d in declarations:
        expanded, missing = props.interpolate(d["value"], literal)
        key = ids.relaxed_key(d["key"])
        if missing:
            unresolved[key] = {"raw": d["value"], "missing": missing,
                               "file": d["file"], "line": d["line"]}
            continue
        values[key] = {"value": expanded, "file": d["file"], "line": d["line"],
                       "raw": d["value"],
                       "spellings": {d["key"]: {"file": d["file"], "line": d["line"],
                                                "raw": d["value"]}}}
    return {"values": values, "unresolved": unresolved}


def _merge(into: dict, view: dict) -> None:
    """Fold one service's view into a component's, keeping every spelling seen.

    Several compose files can configure one component. Later files win the value — the same
    deterministic last-wins rule the properties extractor uses within a component — but no
    spelling is lost, because provenance must point at the declaration the consumer asked
    for rather than at whichever one happened to win.
    """
    for key, entry in view["values"].items():
        existing = into["values"].get(key)
        if existing is None:
            into["values"][key] = dict(entry)
            continue
        spellings = {**existing.get("spellings", {}), **entry.get("spellings", {})}
        into["values"][key] = {**entry, "spellings": spellings}
    for key, miss in view["unresolved"].items():
        into["unresolved"].setdefault(key, miss)


EXTRACTORS = (ComposeExtractor(),)
