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

## Which component a service belongs to (T534, T579)

`build:` names a directory, and a directory is a component or it is not — a **declared**
link, so it is taken at full confidence: the claim lands on `service_id(name, component)`,
one node per service name and the component it builds, so two processes built from one
component stay two nodes (D534-5) and a service named after its component is that
component's own node. The context is resolved by path arithmetic on the workspace's own
component list (`config["components"]`, the whole workspace even when a run extracts fewer),
never against the filesystem, so a tree gives the same ids on every machine.

A component whose compose files declare any `build:` is **building**: every other service
it declares is its own infrastructure, repo-scoped, and never joins anything (D9-1). A
component that declares none is **deployment-only**: its claims are repo-scoped here and
marked, and the `join-by-name` link pass moves one onto the estate's node for that name
where the estate already has one — a chart claims it, or a component carries the name.
That join is recorded as INFERRED rather than presented as the same kind of fact, because
a name matching is a coincidence that usually holds.

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

import posixpath
import re
from pathlib import Path
from typing import Any, NamedTuple

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance, unresolved_row
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _canonical, _yaml

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


#: Beside the view, never in it: which component and file each held value came from, and
#: whether a building component set it for a service it does not build (ruling 4). The view
#: is a shape other publishers share; a field only this one understands would be the
#: divergence its contract exists to stop.
ORIGIN_KEY = "composeenv.origin"


def origin_for(cache: dict, alias: str, relaxed: str) -> dict | None:
    """Where the value `alias`'s view holds for `relaxed` came from, or None."""
    return cache.get(ORIGIN_KEY, {}).get(alias, {}).get(relaxed)


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


#: Where `key` is declared as a MAPPING KEY, rather than wherever its name happens to appear.
#: Lived here until a second YAML reader needed the same thing; moved to `_yaml/` so there is
#: one implementation instead of two that can drift apart while both look right. Re-exported
#: under its original name for the test that pins one line finder for every YAML reader;
#: compose itself takes a service's line from its key node since T593.
key_line = _yaml.key_line


def environment_entries(node: Any) -> list[dict]:
    """The `environment:` block as entries - `key`, `value`, `line`, `readings` - in either
    spelling compose allows, read from its YAML NODE.

    A map (`KEY: value`) and a list (`- KEY=value`) mean the same thing, and a reader that
    handles only the map form silently produces a service with no environment at all — which
    is indistinguishable from a service that genuinely has none.

    A bare `- KEY` in list form means "inherit from the host" and carries no value here. It
    yields an empty string rather than being dropped: the key is referenced, and a key this
    workspace names is a key the graph should know about. A map value YAML reads as null
    (`KEY:`) says the same.

    Read from the node, not from what `safe_load` typed (T536): a value is the text it was
    written as - `1.10`, not `1.1`; `off`, not `False` - and `readings` names what a YAML
    reader may deliver it as instead. A key's line is its own node's (T539), so a key merged
    in through an anchor (`<<: *env`) is cited where it is declared, not at the next
    service's key of the same name.
    """
    y = props.yaml
    out: list[dict] = []
    if isinstance(node, y.MappingNode):
        for key, value in _yaml.mapping_pairs(node):
            if isinstance(value, y.ScalarNode):
                text, readings = _yaml.scalar_text(value), _yaml.typed_readings(value)
            else:
                text, readings = str(_yaml.constructed(value)), ()
            out.append({"key": str(key.value), "value": text,
                        "line": _yaml.line_of(key), "readings": readings})
    elif isinstance(node, y.SequenceNode):
        for item in node.value:
            # A list item is `KEY=value` text; one YAML reads as anything but text (a null,
            # a number) names no variable, as before.
            if not isinstance(item, y.ScalarNode) or item.tag != _yaml.STR_TAG:
                continue
            key, sep, value = item.value.partition("=")
            out.append({"key": key.strip(), "value": value.strip() if sep else "",
                        "line": _yaml.line_of(item), "readings": ()})
    return out


#: Marks a claim that names its service by name alone, for the `join-by-name` link pass to
#: decide (T534 v4). Only a deployment-only component's claims carry it. The pass strips it
#: from every claim it sees, so it stays in a graph only when the pass did not run - a
#: claim nothing has yet decided about, which is what it says (D534-23).
JOIN_PROP = "join"

#: A context compose interpolates when it runs, one that is not a directory at all, and a
#: Windows drive - none of them can be placed in the workspace from the file alone.
_INTERPOLATED = re.compile(r"\$")
_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@)", re.IGNORECASE)
_DRIVE = re.compile(r"^[A-Za-z]:")


class Claim(NamedTuple):
    """Where one file's declaration of one service lands, and why (T534 v5)."""

    target: str             # the svc node the claim is on
    scope: str              # the component that id is scoped to - the node's `component`
    component: str          # the edge's `component`: whose code the service is, or ""
    mapped_by: str          # "build", "name", or ""
    view: str               # the env-view key its environment is published under: main's
    join: bool = False      # a deployment-only claim, for `join-by-name` to decide
    unplaced: str = ""      # why its own `build:` could not be placed - a row
    ambiguous: tuple[str, ...] | list[str] = ()   # the components a layer could mean - a row
    within: str = ""        # the place a build context points, when not a component's own
                            # directory (D534-25, D534-37) - the `built_within` prop
    inferred: bool = False  # strictly inside ANOTHER component: mapped, as an inference
    local: bool = False     # a building component's service it does not build (ruling 4)


def _layout(ctx: ExtractContext) -> dict[str, str]:
    """Every component's root-relative POSIX path, by name, from `config["components"]`.

    The WHOLE workspace's list even when a run extracts fewer (T579, ruling 3): a
    component's facts must not depend on which others a run was given. Empty for an
    in-process caller that gave none, and then only the component's own tree resolves.
    Read through `_canonical.workspace_layout`, the one normalising reading the canonical
    pass and the join share (D534-42).
    """
    return _canonical.workspace_layout(ctx.config)


def _context_of(service: Any) -> tuple[bool, str | None]:
    """(declares a build, its context as written - None when it names no path).

    A `build:` mapping with no `context:`, or a null one, builds the project directory: the
    Compose Specification's `build.context` "defaults to project directory (`.`)" when not
    set (D534-71). Its env view is still main's, from `env_view`.
    """
    if not isinstance(service, dict) or "build" not in service:
        return False, None
    build = service["build"]
    if isinstance(build, dict):
        context = build.get("context", ".")
        context = "." if context is None else context
    else:
        context = build
    if not isinstance(context, str) or not context.strip():
        return True, None
    return True, context.strip()


#: The ways on for a context no component holds as written (D534-78, D534-84, D534-88). A
#: respelling of a declared component comes first: a junction, a symlink, a trailing dot or
#: an 8.3 short name can reach a declared component's directory on the filesystem, and
#: declaring it again would put two components over one directory. Declaring is offered
#: only for a directory that exists, since the workspace parser refuses one that does not.
#: The row never cites what the filesystem resolves the context to: rows are in the shard
#: digest, and that answer is one machine's.
_WAYS_ON = ("if it means a declared component, write that component's directory as "
            "declared, relative to this file; otherwise, if that directory exists, declare it "
            "as a component to map it, and if it does not, the context is wrong")


def _holds(path: str, target: str) -> bool:
    return path == "." or target == path or target.startswith(path + "/")


def _depth(path: str) -> int:
    return 0 if path == "." else path.count("/") + 1


def _case_twin(target: str, layout: dict[str, str]) -> tuple[str, str] | None:
    """A non-root component whose path holds `target` once letter case is ignored, when no
    component holds it exactly: paths are matched exactly on every platform (T531's rule),
    so the row names the near miss instead of calling the directory undeclared."""
    folded = target.casefold()
    twins = [(name, path) for name, path in sorted(layout.items())
             if path != "." and _holds(path.casefold(), folded)]
    return max(twins, key=lambda t: _depth(t[1])) if twins else None


def built_by(file_dir: str, context: str | None, layout: dict[str, str], own: str,
             listed: bool) -> tuple[str, str, str]:
    """(the component holding a build context, how it holds it, why none does).

    `how` is `"exact"` for a component's own directory and `"own"` for a directory inside
    the file's own component - both declarations - or `"within"` for one strictly inside
    ANOTHER component, which is mapped but as an inference (D534-25): `../platform/svc-ab`
    lands in `platform` whether it means `svc-a` or a typo of it. A context only the
    workspace root's component holds, from a file of another component, is not placed at
    all (D534-21): a root component holds every path, so it would turn any typo into a
    declared build.

    Path arithmetic on root-relative POSIX paths, never the filesystem (D534-9): the same
    tree gives the same answer on every platform and machine, and a component's facts
    depend only on its own files and the workspace config - exactly what its freshness
    stamp covers. Existence is deliberately not checked (D534-6).
    """
    if context is None:
        return "", "", ("declares a `build:` context that is not a path, so what it builds "
                        "cannot be read")
    if _INTERPOLATED.search(context):
        return "", "", (f"builds `{context}`, which compose interpolates when it runs, so "
                        f"the directory it names is not written in this file")
    # Worded by layout: one component, as in a `--target` build, is "this repository"
    # (D534-87, D534-92). With no component list the fallback layout has one entry too.
    single = len(layout) == 1
    here = "this repository" if single else "this workspace"
    # "As written": a context that leaves the root by name and comes back in, such as
    # `../../ws/orders`, or one that respells the repository's own directory, reaches a
    # component's directory on the filesystem, which path arithmetic - root-relative, blind
    # to the root's own name - cannot see (D534-85, D534-90). One text per layout for both
    # causes; with one component it names the repository's own directory, the commonest
    # respelling there (C534-5).
    outside = (f"builds `{context}`, which lies outside {here} as written. Paths are matched "
               f"exactly, so " + ("if it means this repository's own directory or one in it, "
                                  "write that relative to this file" if single else
                                  f"if it means a directory in {here}, write it relative to "
                                  f"this file"))
    if _REMOTE.match(context):
        return "", "", f"builds `{context}`, which is not a directory in {here}"
    written = context.replace("\\", "/")
    if written.startswith("/") or _DRIVE.match(written):
        return "", "", (f"builds `{context}`, an absolute path, which cannot be placed in a "
                        f"workspace that may be checked out anywhere; if it names a directory "
                        f"in {here}, write it relative to this file")
    target = posixpath.normpath(posixpath.join(file_dir, written))
    if target == ".." or target.startswith("../"):
        if not listed:
            return "", "", (f"builds `{context}`, which lies outside this component, and the "
                            f"workspace's component list was not given, so no other "
                            f"component can be named")
        # No declaring clause here: a component declared outside the root is accepted, but
        # this branch comes before the holders, so declaring would leave the row standing
        # (D534-106). A directory inside the workspace, rewritten, meets the rows below.
        return "", "", outside
    holders = sorted((name, path) for name, path in layout.items() if _holds(path, target))
    exact = [name for name, path in holders if path == target]
    if exact:
        return exact[0], "exact", ""
    best = max(holders, key=lambda h: _depth(h[1])) if holders else None
    if best is not None and best[0] == own:
        return own, "own", ""
    if best is not None and best[1] != ".":
        return best[0], "within", ""
    twin = _case_twin(target, layout)
    if twin is not None:
        return "", "", (f"builds `{context}`, which differs only in letter case from "
                        f"component `{twin[0]}` at `{twin[1]}`. Paths are matched exactly on "
                        f"every platform, so write that component's directory as declared, "
                        f"relative to this file")
    if single:
        # One component, as in a `--target` build: no declaring clause, since a workspace
        # `--target` infers has no file to declare into (D534-93).
        return "", "", outside
    if best is not None:             # only the workspace root's component holds it
        return "", "", (f"builds `{context}`, which lies in no declared component but the "
                        f"workspace root's, as written. Paths are matched exactly, so "
                        f"{_WAYS_ON}")
    return "", "", (f"builds `{context}`, which no declared component holds as written. "
                    f"Paths are matched exactly, so {_WAYS_ON}")


class Decl(NamedTuple):
    """One service declaration of one file, in the file's order, with its claim.

    A declaration is its own entry, so two a file writes alike but YAML types apart - `on:`
    and `"on":`, which `mapping_pairs` keeps as two (B10-14) - keep their own claims
    (D534-76). The body is constructed once, in the pre-pass, and handed on (D534-75)."""
    name: str
    node: Any
    service: dict | None     # None: neither a mapping nor an empty body, so it is not read
    claim: Claim
    #: The 1-based line its name is declared on, from the key node's own mark (T593): a
    #: quoted name (`"my api":`) was looked for in the text as written bare, never found,
    #: and every edge and row about the service cited line 0.
    line: int


def _body(node: Any) -> dict | None:
    """A service's body: a mapping, constructed; `{}` for an empty body (`worker:` with
    nothing under it is a declared service with no config, and dropping it loses a real svc
    node and its DG_DEPLOYS edge); None for anything else."""
    y = props.yaml
    if isinstance(node, y.MappingNode):
        return _yaml.constructed(node)
    if isinstance(node, y.ScalarNode) and _yaml.scalar_text(node) == "" and node.style is None:
        return {}
    return None


def env_view(compose_file: Path, service: dict, repos: list[RepoRef], name: str) -> str:
    """The env view a declaration's values are published under: main's, by main's own rule
    (ruling 10). That is the component whose directory the build context RESOLVES to on this
    filesystem, else the component named like the service, else the service's name.

    Kept apart from placement on purpose. `built_by` places a claim by path arithmetic, so
    ids are the same on every machine; this reads the filesystem exactly as main did, so
    every value resolves as on main, including the cells where the two answers differ: a
    `build:` mapping with no `context:`, a context differing only in letter case, a symlink
    and an absolute path (D534-71, D534-72). Its platform and filesystem dependence is
    main's, and remainder V-e's row inherits it.
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
                    return repo.alias
    for repo in repos:
        if repo.alias == name:
            return repo.alias
    return name


#: compose's own default file discovery (compose-go `cli/options.go` at 6ec8efb,
#: `DefaultFileNames` and `DefaultOverrideFileNames`, each in order of preference): in one
#: directory, the first base present and the first override present are what
#: `docker compose up` merges with no `-f` (T588).
DEFAULT_BASES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
DEFAULT_OVERRIDES = ("compose.override.yml", "compose.override.yaml",
                     "docker-compose.override.yml", "docker-compose.override.yaml")


def projects(rels: list[str]) -> dict[str, str]:
    """Each compose project's override, mapped to its base: in one directory of one
    component, compose's own default pair is one package, named by its base (T588, the
    owner's ruling A of 2026-10-02).

    Read from the files the component READS, not the ones that parsed - a base that does not
    parse still names its project, since compose would still layer over it - with names
    matched exactly, the graph's one case rule. Every other compose file stays its own
    package: a prod or staging layer (`-f` is not on disk), a second base or override
    compose warns about and ignores, and an override with no base, which compose does not
    load."""
    names: dict[str, set[str]] = {}
    for rel in rels:
        folder, name = posixpath.split(rel)
        names.setdefault(folder, set()).add(name)
    out: dict[str, str] = {}
    for folder, present in names.items():
        base = next((n for n in DEFAULT_BASES if n in present), None)
        override = next((n for n in DEFAULT_OVERRIDES if n in present), None)
        if base and override:
            out[posixpath.join(folder, override)] = posixpath.join(folder, base)
    return out


def _claims(ctx: ExtractContext, repo: RepoRef, parsed: list[tuple[str, Any]],
            layers: dict[str, str]) -> dict[str, list[Decl]]:
    """Each file's declarations, in order, each with its claim, from ALL of a component's
    compose files (T534 v5).

    One pass decides whether the component is **building** - any file declares `build:`,
    whether or not it can be placed, since a typo is still a statement that the file builds
    (ruling 1, D534-1) - and which components each service name is built from. Then each
    declaration is placed on its own terms (D534-7): its own `build:` first; a layer that
    declares none takes the component-wide answer when it is one component, and a row when
    it is several.

    Before that rule, a service an override declares no `build:` of takes its base's claim
    for the same name (T588; `layers` maps each override to its base): eight of `Claim`'s
    eleven fields - `target`, `scope`, `component`, `mapped_by`, `join`, `within`,
    `inferred` and `local` - and none of the other three. `view` stays the override's own,
    main's rule (ruling 10), and `unplaced` and `ambiguous` are the reasons for the base's
    own rows, which stay the base's (design review D15a-15).
    """
    layout = _layout(ctx)
    listed = repo.alias in layout
    if not listed:
        layout = {repo.alias: "."}
    base = layout[repo.alias]
    #: Per service name, per component it is built from, how each building file placed it
    #: - (`built_within`, inferred) - so a layer inherits the placement too (D534-41).
    #: Keyed by name, which is right across files.
    built: dict[str, dict[str, set[tuple[str, bool]]]] = {}
    pending: list[tuple[str, str, Any, dict | None, tuple[bool, str, str, str, bool], str,
                       int]] = []
    building = False
    for rel, services_node in parsed:
        file_dir = posixpath.normpath(posixpath.join(base, posixpath.dirname(rel) or "."))
        for key_node, node in _yaml.mapping_pairs(services_node):
            name = str(key_node.value)
            service = _body(node)
            declares, context = _context_of(service)
            comp, how, why = (built_by(file_dir, context, layout, repo.alias, listed)
                              if declares else ("", "", ""))
            building = building or declares
            # Recorded for every match that is not a component's own directory, so `./svc-a`
            # and a typo like `./nope` read differently even where both map (D534-37).
            within = (posixpath.normpath(posixpath.join(file_dir, context.replace("\\", "/")))
                      if how in ("within", "own") and context else "")
            # The env view is MAIN's, by main's own rule, whatever the placement (ruling
            # 10): T579 moves a built service's node, never which code sees its settings.
            view = env_view(repo.path / rel, service or {}, ctx.repos, name)
            pending.append((rel, name, node, service,
                            (declares, comp, why, within, how == "within"), view,
                            _yaml.line_of(key_node)))
            if comp:
                built.setdefault(name, {}).setdefault(comp, set()).add(
                    (within, how == "within"))
    decls: dict[str, list[Decl]] = {}
    for rel, name, node, service, decl, view, line in pending:
        decls.setdefault(rel, []).append(Decl(name, node, service, _claim(
            name, *decl, own=repo.alias, building=building, built=built.get(name, {}),
            view=view), line))
    for override, base_rel in layers.items():
        if override not in decls:
            continue
        inherited: dict[str, Claim] = {}
        for decl in decls.get(base_rel, []):
            inherited.setdefault(decl.name, decl.claim)
        decls[override] = [
            decl._replace(claim=inherited[decl.name]._replace(
                view=decl.claim.view, unplaced="", ambiguous=()))
            if decl.name in inherited and not _context_of(decl.service)[0] else decl
            for decl in decls[override]]
    return decls


def _claim(name: str, declares: bool, comp: str, why: str, within: str, inferred: bool,
           *, own: str, building: bool,
           built: dict[str, set[tuple[str, bool]]], view: str) -> Claim:
    """Where one declaration lands. A node's `component` is the component its id is scoped
    to (D534-13): the built component, or the declaring one.

    Environment is filed as on main, under `view` (ruling 10), and a service a building
    component does not build is marked `local` so a reader of that name is told where the
    value came from (ruling 4)."""
    if comp:                          # this file builds it: one node per (name, component)
        return Claim(ids.service_id(name, comp), comp, comp, "build", view, within=within,
                     inferred=inferred)
    # A service named after its own component is that component's own node, which main
    # labels by name. It keeps that label where its build cannot be placed or its layer is
    # ambiguous; the row still says why the build is not read (D534-83).
    named = ((own, "name") if ids.service_id(name, own) == ids.component_service_id(own)
             else ("", ""))
    if declares:                      # it builds, from nowhere this can place
        return Claim(ids.service_id(name, own), own, *named, view, unplaced=why, local=True)
    if len(built) == 1:               # a layer over a service its component builds
        (comp, placed), = built.items()
        # The layer takes the placement as well as the component (D534-41): the label and
        # the provenance of the build it layers, when every building file agrees on them;
        # an inference anywhere stays one.
        (within, inferred), = placed if len(placed) == 1 else {("", any(i for _, i in placed))}
        return Claim(ids.service_id(name, comp), comp, comp, "build", view, within=within,
                     inferred=inferred)
    if len(built) > 1:
        return Claim(ids.service_id(name, own), own, *named, view, ambiguous=sorted(built),
                     local=True)
    if named[0]:
        return Claim(ids.service_id(name, own), own, *named, view)  # its own node, by id
    if building:                      # a building component's own infrastructure (D9-1)
        return Claim(ids.service_id(name, own), own, "", "", view, local=True)
    return Claim(ids.service_id(name, own), own, "", "", view, join=True)



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "compose-env-unresolved": ('key',),
    "compose-unparsed": ('error',),
    "grammar-unavailable": ('language',),
    "superseded-declaration": ('key', 'value'),
    "yaml-typed-value": ('key', 'value', 'readings'),
    "compose-build-unresolved": ('service',),
    "compose-build-ambiguous": ('service', 'components'),
}


def _no_yaml(extractor: str, repo: RepoRef, n_files: int) -> dict:
    """One row per component when PyYAML is absent — `spring._no_grammar`'s sibling.

    YAML is a language and its parser is not installed; the row says so in the same
    vocabulary a missing source grammar uses, so item 84's grouping files both losses
    under one cause instead of inventing a second spelling for it.
    """
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files, "language": "yaml",
        "why": (f"no parser is installed for yaml (PyYAML is absent), so {n_files} "
                f"file(s) were not read. Every service they declare and every address "
                f"they publish are missing from the graph, and this row is the only "
                f"record of that"),
    }

def _unread(repo: RepoRef, rel: str, kind: str) -> Node:
    """A deployment file this reader was handed and could not read or parse (batch 15b,
    T573 decision 6): its node, carrying no claim, marked `unread` with the kind of the row
    that explains it - so the graph admits the gap instead of reading as complete, and the
    accounting rows can name the file."""
    return Node(id=ids.deployment_id(repo.alias, rel), type="deploy",
                props={"kind": CONFIG_KIND, "path": rel, "component": repo.alias,
                       "unread": kind})


class ComposeExtractor:
    """Services a compose file declares, and the environment it gives each one."""

    name = "compose-services"
    INPUT_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml",
                   "compose*.yml", "compose*.yaml")
    #: Batch 15b: its facts are deployment facts the accounting rows judge, and every file
    #: it reads, it reads for the services it claims.
    DEPLOYMENT_READER = True
    CLAIM_GLOBS = INPUT_GLOBS

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            # The zero-dependency degradation (item 62), same shape as a missing source
            # grammar: one row per component saying what was not read, because an
            # extractor that simply returned nothing would be indistinguishable from a
            # workspace that runs no services.
            for repo in ctx.repos:
                res.unresolved.append(_no_yaml(self.name, repo, len(ctx.inputs(repo))))
            return res
        views: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        #: Per component, the keys the canonical source has already claimed. Local to this
        #: run rather than stored in the view, which is a shape other publishers share. The
        #: pipeline calls `extract` once per component, so a `[[canonical]]` block ranks env
        #: values within one component only - across components they are not yet ranked
        #: against each other (ruling 9: its own backlog row, with its own design).
        owned: dict[str, set[str]] = {}
        #: Per component, what each value the view now holds may be read as (T536), so two
        #: files spelling one value differently are not reported as disagreeing outright
        #: (review B10-4). Local for the same reason as `owned`.
        held: dict[str, dict[str, tuple[str, ...]]] = {}
        for repo in ctx.repos:
            # In main's order. Nothing placed depends on it, since each file places its own
            # `build:` (D534-7), but which env value is read last does, so it stays main's
            # (ruling 10). Each file is read and parsed once (remainder E).
            parsed = [item for path in sorted(ctx.inputs(repo))
                      if (item := self._read(res, repo, path)) is not None]
            layers = projects([_rel(repo, path) for path in ctx.inputs(repo)])
            decls = _claims(ctx, repo, parsed, layers)
            for rel, _services_node in parsed:
                self._file(res, ctx, repo, rel, decls.get(rel, []), views, owned,
                           held, layers.get(rel, ""))
        return res

    def _read(self, res: ExtractResult, repo: RepoRef,
              path: Path) -> tuple[str, Any] | None:
        """(component-relative path, the `services:` node), or None with the row that
        says why the file declares nothing this extractor can read."""
        rel = _rel(repo, path)
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "kind": "unreadable-file", "component": repo.alias,
                "source_file": rel, "line": 0,
                "why": ("this file could not be read, so whether it defines services is "
                        "unknown; if it did, they are missing from the graph rather than "
                        "absent")})
            res.nodes.append(_unread(repo, rel, "unreadable-file"))
            return None
        doc, error = props.load_yaml_reporting(path)
        if error:
            # E6. Recorded, and the build continues: one unreadable file must not abort a
            # workspace scan, and must not vanish from it either.
            res.unresolved.append({
                "extractor": self.name, "kind": "compose-unparsed",
                "component": repo.alias, "source_file": rel, "line": 0, "error": error,
                "why": "could not be parsed as YAML, so every service it declares and "
                       "every address they publish are missing from the graph"})
            res.nodes.append(_unread(repo, rel, "compose-unparsed"))
            return None
        services = doc.get("services") if isinstance(doc, dict) else None
        if services is None:
            return None
        if not isinstance(services, dict):
            # `services:` written as a LIST (or any non-mapping) is valid YAML, so no parse
            # error fires and a bare `return` reported an INVALID file as one that simply
            # declared no services. Record it: invalid must not read as absent.
            res.unresolved.append({
                "extractor": self.name, "kind": "compose-unparsed",
                "component": repo.alias, "source_file": rel, "line": 0,
                "error": f"`services:` is a {type(services).__name__}, not a mapping",
                "why": "declares `services:` as something other than a mapping, so the "
                       "services it means to define are unreadable rather than absent"})
            res.nodes.append(_unread(repo, rel, "compose-unparsed"))
            return None
        text = props.read_text(path)
        # The services as the NODE tree holds them - each name as written, each body with
        # its values' text and its keys' lines (T536, T539). The loaded dict folds two names
        # YAML reads as equal (`on:` and `true:`) into one, and pairing the two by position
        # or by name then dropped a whole environment without a row (review B10-2).
        return rel, _yaml.mapping_get(_yaml.document_nodes(text)[0], "services")

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
              decls: list[Decl], views: dict[str, dict],
              owned: dict[str, set[str]],
              held: dict[str, dict[str, tuple[str, ...]]], package: str) -> None:
        deploy = ids.deployment_id(repo.alias, rel)
        # An override names the base whose package it layers (T588): `package` is the link
        # pass's word - any reader may say which file's package one of its files belongs
        # to - and it names a file this same component reads (D15a-22).
        res.nodes.append(Node(id=deploy, type="deploy", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias,
            **({"package": package} if package else {})}))
        cfg = ids.config_id(repo.alias, rel)
        res.nodes.append(Node(id=cfg, type="cfg", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))

        for decl in decls:
            if decl.service is None:
                continue
            self._service(res, ctx, repo, rel, deploy, cfg, decl.name, decl.node, views,
                          owned, held, decl.line, placed=decl.claim)

    def _service(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
                 deploy: str, cfg: str, name: str, node: Any,
                 views: dict[str, dict], owned: dict[str, set[str]],
                 held: dict[str, dict[str, tuple[str, ...]]], line: int, *,
                 placed: Claim) -> None:
        # The claim on a service, in exactly the shape `mark-canonical` groups on. Two files
        # naming one service produce two of these, identically, and the workspace's own
        # [[canonical]] block decides — never this extractor, which cannot see intent.
        res.nodes.append(Node(id=placed.target, type="svc",
                              props={"component": placed.scope}))
        edge_props = {"service": name, "component": placed.component,
                      "mapped_by": placed.mapped_by}
        if placed.join:
            edge_props[JOIN_PROP] = "name"
        if placed.within:
            # Not a component's own directory: the place the build pointed at, so a
            # reader can tell `./svc-a` from a typo (D534-37). Strictly inside ANOTHER
            # component it is also an inference (D534-25) - see the provenance below.
            edge_props["built_within"] = placed.within
        res.edges.append(Edge(
            src=deploy, type="DG_DEPLOYS", dst=placed.target, source_file=rel, line=line,
            extractor=self.name, confidence=1.0, props=edge_props,
            provenance=Provenance.INFERRED if placed.inferred else Provenance.EXTRACTED))
        if placed.unplaced:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-build-unresolved", rel, line,
                f"`{name}` {placed.unplaced}. The claim stays this component's", service=name))
        if placed.ambiguous:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-build-ambiguous", rel, line,
                f"`{name}` is built from {' and '.join(placed.ambiguous)} by this component's "
                f"other compose files, and this file declares no build of its own, so which "
                f"one it layers cannot be read. Declaring the `build:` it layers here places "
                f"it; this file and that one then claim one service as two packages, which a "
                f"`[[canonical]]` block decides. The claim stays this component's",
                service=name, components=list(placed.ambiguous)))

        entries = environment_entries(_yaml.mapping_get(node, "environment"))
        if not entries:
            return
        declarations = [{"key": e["key"], "value": e["value"], "file": rel,
                         "line": e["line"]} for e in entries]
        view = _resolve(declarations)
        view_key = placed.view
        views.setdefault(view_key, {"values": {}, "unresolved": {}})
        claim = canonical_claim(ctx.config, name)
        written: set[str] = set()
        superseded = _merge(views[view_key], view,
                            owned.setdefault(view_key, set()),
                            contested=claim is not None,
                            canonical=is_canonical_source(claim, repo.alias, rel),
                            held=held.setdefault(view_key, {}),
                            readings={ids.relaxed_key(e["key"]): e["readings"]
                                      for e in entries},
                            written=written)
        # Ruling 4: where each value the view now holds came from, in a compose-private
        # table beside the shared view, so a component reading a value a building repo set
        # for a service it does not build is told so (`origin_for`). Recorded for the keys
        # the merge says it wrote from this file, never inferred by comparing entries.
        origins = ctx.cache.setdefault(ORIGIN_KEY, {}).setdefault(view_key, {})
        # Where each superseded value was held from BEFORE this file's values land: the
        # declaration a row names may be another component's (ruling 9).
        prior = {row["key"]: origins.get(row["key"]) for row in superseded}
        for relaxed in sorted(written):
            origins[relaxed] = {"component": repo.alias, "file": rel,
                                "service": name, "local": placed.local,
                                # A build this file declares and nothing could place is
                                # not "a container it does not build" (D534-35).
                                "unplaced": bool(placed.unplaced or placed.ambiguous)}
        for row in superseded:
            # The row names the DROPPED declaration where it is written. This file's value
            # was dropped exactly when the merge did not write it; otherwise the dropped one
            # was held from an earlier file, perhaps another component's, which the origin
            # table recorded before this file's values landed (ruling 9).
            earlier = prior.get(row["key"]) or {}
            here_dropped = row["key"] not in written
            dropped_in = repo.alias if here_dropped else str(earlier.get("component", ""))
            kept_in = str(earlier.get("component", "")) if here_dropped else repo.alias
            # An origin the table does not hold (either side empty) falls back to main's
            # wording rather than a half-qualified citation. The branches of `_merge` never
            # reach it - every write records an origin - so it is a guard, not a shape
            # (D534-69).
            if not dropped_in or not kept_in or dropped_in == kept_in:
                # One component: ranked within it, so the claim below holds unless another
                # component sets the key later (remainder V-e) - worded exactly as on main.
                said = (f"`{row['key']}` is declared as {row['dropped']['value']!r} here and "
                        f"as {row['kept']['value']!r} in {row['kept']['file']}, which the "
                        f"workspace names as the owner of service `{name}`. The owner's value "
                        f"is what the graph uses; this one is reported because ")
            else:
                # Two components: the kept file qualified (D9-4), and no claim the graph
                # cannot keep - a component read later can still replace the owner's
                # value, since values from different components are not yet ranked against
                # each other (ruling 9; the unit is the component, D534-66).
                said = (f"`{row['key']}` is declared as {row['dropped']['value']!r} here and "
                        f"as {row['kept']['value']!r} in {kept_in}/{row['kept']['file']}, "
                        f"which the workspace names as the owner of service `{name}` and "
                        f"which replaced this value when it was read. Values from different "
                        f"components are not yet ranked against each other, so one read "
                        f"later can still replace the owner's; this one is reported because ")
            res.unresolved.append({
                "extractor": self.name,
                "kind": "superseded-declaration", "component": dropped_in or repo.alias,
                "key": row["key"], "source_file": row["dropped"]["file"],
                "line": row["dropped"]["line"], "value": row["dropped"]["value"],
                "why": said + ("the two are spelled differently and may be one value or two, "
                               "depending on the tool that reads them"
                               if row["maybe_same"] else
                               "a stale deployment nobody has noticed is a finding, not "
                               "noise")})
        for e in entries:
            if e["readings"]:
                # T536: recorded as written, and said once what else it may arrive as.
                res.unresolved.append({
                    "extractor": self.name, "kind": "yaml-typed-value",
                    "component": repo.alias, "key": e["key"], "value": e["value"],
                    "readings": list(e["readings"]), "source_file": rel, "line": e["line"],
                    "why": _yaml.typed_value_why(e["key"], e["value"], e["readings"])})

        for entry in declarations:
            envkey = ids.env_key_id(entry["key"])
            res.nodes.append(Node(id=envkey, type="envkey",
                                  props={"key": ids.relaxed_key(entry["key"])}))
            resolved = view["values"].get(ids.relaxed_key(entry["key"]))
            res.edges.append(Edge(
                src=cfg, type="DG_CONFIGURES", dst=envkey, source_file=rel,
                line=entry["line"], extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED,
                props={"declared_as": entry["key"], "service": name,
                       "value": resolved["value"] if resolved else "",
                       "raw": entry["value"]}))
        for key, miss in view["unresolved"].items():
            res.unresolved.append({
                "extractor": self.name,
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


def canonical_claim(config: dict, service: str) -> dict | None:
    """The workspace's decision about a contested service, or None if it made none.

    Read from `ctx.config`, which the pipeline already populates from the workspace's
    `[[canonical]]` blocks — so the decision reaches the extract layer through a seam the
    SPI already had, and no core change is needed to consult it.

    Matched on the NORMALISED name, as `mark-canonical` matches it (D534-24): a service
    written `my api` is `svc:my-api@...`, and a block the proposal drafts as `my-api` must
    decide the environment as well as the edges.
    """
    wanted = ids.norm_id(service)
    for claim in config.get("canonical") or []:
        if isinstance(claim, dict) and ids.norm_id(str(claim.get("service", ""))) == wanted:
            return claim
    return None


def is_canonical_source(claim: dict | None, alias: str, rel: str) -> bool:
    """Whether this file is the one the workspace named as the owner of a service.

    Both coordinates are optional and each narrows independently: `owner` alone is enough
    when the variants live in different components, and `path` is what decides it when one
    component holds two of them — which is exactly this fixture's shape.

    Delegates to `_canonical.covers` so that "does this claim designate this file" has ONE
    implementation across every deployment reader. A compose claim names a file, and the
    match is exact: a claim on `deploy/docker-compose.yml` no longer reaches the rest of
    `deploy/`, where a superseded file in `deploy/legacy/` used to count as the owner's too
    (T529). Surrounding slashes are stripped from both sides, so `/docker-compose.yml`
    matches; that is a spelling of the same claim, and refusing it would have been a silent
    no-match rather than a complaint.
    """
    if claim is None:
        return True
    return _canonical.covers(claim, alias, rel)


def _merge(into: dict, view: dict, owned: set[str], *, contested: bool = False,
           canonical: bool = True, held: dict[str, tuple[str, ...]] | None = None,
           readings: dict[str, tuple[str, ...]] | None = None,
           written: set[str] | None = None) -> list[dict]:
    """Fold one service's view into a component's, keeping every spelling seen.

    Several compose files can configure one component. Later files win the value — the same
    deterministic last-wins rule the properties extractor uses within a component — but no
    spelling is lost, because provenance must point at the declaration the consumer asked
    for rather than at whichever one happened to win.

    **Last-wins is correct for layering and catastrophic for a contested service, and those
    are different situations wearing the same shape.** An override file legitimately
    supersedes a base file; a superseded deployment of the same service does not, and the
    workspace's `[[canonical]]` block is the only thing that can tell them apart, because
    neither file contains the intent. So when a service is contested, the owner's value
    outranks every other claim's regardless of filename order — and the losing value is
    RETURNED rather than dropped, because a stale deployment about to be believed is exactly
    what a reader needs to see.

    Without the ranking, this fixture's superseded file sorted last alphabetically and won:
    a whole component published to a topic one version number off, resolved from a file
    whose own header says it is dead. Nothing about the resulting graph looked wrong.

    **`owned` is the extractor's own state and deliberately not part of the view.** Which
    keys the canonical source has claimed is a fact about this extractor's traversal, and
    the view is a SHARED shape that `properties.lookup` reads whoever published it — a field
    only one publisher understands is precisely the divergence that contract exists to stop.
    A test asserting the entry's exact key set caught the first attempt doing it.

    **`held` is the same kind of state:** what each value the view holds may be read as
    (T536), with `readings` the same for this view's values. Values are compared as written,
    so `yes` in one file and `true` in another differ - and a reader of one YAML version
    delivers them alike, another not. The row says which of those it is (`maybe_same`,
    review B10-4) rather than calling every spelling a stale deployment.

    **`written` is told which keys now hold THIS view's value** - so the caller records the
    held value's origin from what the merge did, not by comparing entries, which two
    components' identical `docker-compose.yml:5` holding one value would fool.
    """
    held = {} if held is None else held
    readings = readings or {}
    superseded: list[dict] = []
    for key, entry in view["values"].items():
        existing = into["values"].get(key)
        spellings = {**(existing or {}).get("spellings", {}), **entry.get("spellings", {})}
        maybe_same = existing is not None and _yaml.may_agree(
            existing["value"], held.get(key, ()), entry["value"], readings.get(key, ()))
        if existing is not None and contested and not canonical and key in owned:
            # The owner has already spoken. Keep its value and its provenance; record the
            # loser rather than discarding it.
            if existing["value"] != entry["value"]:
                superseded.append({"key": key, "kept": existing, "dropped": entry,
                                   "maybe_same": maybe_same})
            into["values"][key] = {**existing, "spellings": spellings}
            continue
        if existing is not None and contested and canonical and key not in owned \
                and existing["value"] != entry["value"]:
            superseded.append({"key": key, "kept": entry, "dropped": existing,
                               "maybe_same": maybe_same})
        into["values"][key] = {**entry, "spellings": spellings}
        held[key] = readings.get(key, ())
        if written is not None:
            written.add(key)
        if contested and canonical:
            owned.add(key)
    for key, miss in view["unresolved"].items():
        into["unresolved"].setdefault(key, miss)
    return superseded


EXTRACTORS = (ComposeExtractor(),)
