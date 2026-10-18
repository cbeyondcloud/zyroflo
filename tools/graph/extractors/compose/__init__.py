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

`ctx.cache["composeenv"][<view>]` has **exactly the shape the properties extractor
publishes**, so `properties.lookup()` reads it unchanged and there is one resolution rule
rather than two that can disagree. A view is a component's name - the one its build places a
declaration on, or the service's name, which the component named like it reads (decision 55) -
or, for a claim an `[[apart]]` block names that no build places, the claim's own node key,
`<service>@<declaring component>`, which no component reads (T621, A'):

    {"values": {"<relaxed key>": {"value", "file", "line", "raw", "spellings"}},
     "unresolved": {"<relaxed key>": {"raw", "missing", "file", "line"}}}

It is a **separate cache key** rather than a merge into the properties view. Whether a
deployment's value overrides what a component declares for itself is the layering question,
which merging the two here would answer by accident and in whichever order the extractors
happened to run. Compose's own values are ranked across components by one decision - the
workspace's block over the compose packages claiming the service, else the last component by
name (`_canonical.env_winner`, batch 24) - never by read order, and the rows saying which
value lost are a link pass's (`env-ranking`), since a component's rows are written before the
later components are read.

Two namespaces beside the view, compose's alone (`CACHE_KEYS`): `composeenv.candidates` -
per view, per relaxed key, per component, that component's own winner, the value the ranking
takes - and `composeenv.claims` - per view, per normalised service, per package, the claims
compose made on the service, a package setting no key included: the set the block decides
over.

## `extends:` and `include:` (T620, the owner's decision 40)

A service with no build context of its own takes the first one up its chain of `extends:`
bases - a name in the same file, or `service` with a `file` - resolved from the directory of
the file declaring it, as compose rebases a base's relative paths, and is placed by it; its
edge records where (`build_from`), and a row about that build names the base and its file,
where the path is written (review B21-3). An `include:`d compose file this component reads is
read on its own, as every compose file is. Both are followed only into the compose files this
reader is handed in the component - a shard's stamp digests those alone, and a read outside
them would let a stale shard look current - so an include or base anywhere else (another
name, another component, a declared child, a path compose interpolates or fetches, a file
whose services could not be read) is a row naming why. A chain is followed whole, whatever
places the service - its own `build:` included - since compose resolves every base: a base
the graph cannot reach is a row (the owner's decision 40, and batch 21's review), and the
claim stays the component's unless a build places it. The env view follows the placement
(decision 55), and a service takes the environment its chain brings (decision 57): each base's
`environment:`, nearest first, where nothing nearer writes the key - cited where the base writes
it, published as the extending service's own `DG_CONFIGURES` edge with `inherited_from`, and
ranked in the extending service's package; a chain that breaks brings what it reached.

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
from pathlib import Path, PurePosixPath
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

#: Batch 24 (T590): per view, per relaxed key, per component, the component's own winner - its
#: candidate - which `_canonical.env_winner` ranks across components (design pass 24, section 2).
CANDIDATES_KEY = "composeenv.candidates"

#: Batch 24 (T590): per view, per normalised service, per package (its workspace key), the claims
#: compose made on the service - a package setting no key included (D24-32, D24-44) - each a
#: `_canonical.PlainClaim`: the decision set `env_winner` takes the block over.
CLAIMS_KEY = "composeenv.claims"


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
    view: str               # the env-view key its environment is published under (env_view)
    join: bool = False      # a deployment-only claim, for `join-by-name` to decide
    unplaced: str = ""      # why its own `build:` could not be placed - a row
    ambiguous: tuple[str, ...] | list[str] = ()   # the components a layer could mean - a row
    within: str = ""        # the place a build context points, when not a component's own
                            # directory (D534-25, D534-37) - the `built_within` prop
    inferred: bool = False  # strictly inside ANOTHER component: mapped, as an inference
    local: bool = False     # a building component's service it does not build (ruling 4)
    build_from: str = ""    # `<file>:<line>` of the base whose build placed it (T620)
    unfollowed: str = ""    # why its `extends:` chain could not be followed - a row (T620)
    extends: str = ""       # the base that could not be followed, as written ("" for none)
    image: str = ""         # the image its service runs with, as compose merges it (batch 23)
    image_from: str = ""    # `<file>:<line>` writing that image, where its own declaration does not
    #: Why its `extends:` chain broke at a base the graph does not read - `reach`'s reason - so
    #: the base may declare a build of any image; "" for a chain compose itself refuses, which
    #: hides no build (batch 23, D23-119, D23-132).
    extends_unread: str = ""


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
    set (D534-71).
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


def _explicit_context(service: Any) -> tuple[bool, bool, str | None]:
    """(declares a build, writes its context itself, that context as a path or None) - for
    `extends:` (T620): `_context_of` reads a mapping with no `context:` as the project
    directory, but through a base the context is the base's when the service writes none,
    as compose merges a `build:` mapping. A null `context:` is not written; a context that is
    not a path is written, and `built_by` names it."""
    if not isinstance(service, dict) or "build" not in service:
        return False, False, None
    build = service["build"]
    if isinstance(build, dict):
        if build.get("context") is None:
            return True, False, None
        context = build["context"]
    else:
        context = build
    if not isinstance(context, str) or not context.strip():
        return True, True, None
    return True, True, context.strip()


#: The files this reader reads, by name - an `include:` or `extends:` naming another is not
#: followed (T620). The class's own globs, spelled once, here, so the rows can name them.
COMPOSE_GLOBS = _canonical.COMPOSE_FILES


def reach(file_dir: str, written: Any, *, base: str, layout: dict[str, str], own: str,
          listed: bool, inputs: set[str], lost: set[str]) -> tuple[str, str]:
    """(the component-relative compose file an `include:` or an `extends:` `file` names, "")
    where this reader reads it in this component, else ("", why) - T620. Path arithmetic on
    the workspace's layout and the files the reader was handed, never the filesystem, so a
    tree gives the same answer on every machine; `file_dir` is the naming file's directory,
    workspace-relative, and `base` the component's path."""
    if not isinstance(written, str) or not written.strip():
        return "", "names no file"
    text = written.strip()
    if _INTERPOLATED.search(text):
        return "", (f"names `{text}`, which compose interpolates when it runs, so the file it "
                    f"means is not written here")
    if _REMOTE.match(text):
        return "", f"names `{text}`, which is not a file in this workspace"
    spelled = text.replace("\\", "/")
    if spelled.startswith("/") or _DRIVE.match(spelled):
        return "", (f"names `{text}`, an absolute path, which cannot be placed in a workspace "
                    f"that may be checked out anywhere")
    target = posixpath.normpath(posixpath.join(file_dir, spelled))
    rel = (target if base == "." else target[len(base) + 1:]
           if target.startswith(base + "/") else None)
    if rel is None or rel == ".." or rel.startswith("../"):
        holders = [(name, path) for name, path in layout.items()
                   if name != own and listed and _holds(path, target)
                   and not (target == ".." or target.startswith("../"))]
        if holders:
            holder = max(holders, key=lambda h: _depth(h[1]))[0]
            return "", (f"names `{text}`, a file of component `{holder}`, which reads it as "
                        f"its own; this component's reading does not")
        return "", f"names `{text}`, which lies outside this component as written"
    child = _canonical.child_holding(rel, _canonical.declared_children(own, layout))
    if child is not None:
        return "", (f"names `{text}`, a file of component `{child}`, declared inside this one, "
                    f"which reads it")
    if not any(repo_walk.glob_match(rel, posixpath.basename(rel), g) for g in COMPOSE_GLOBS):
        return "", (f"names `{text}`, which is not named as a compose file ("
                    f"{', '.join(f'`{g}`' for g in COMPOSE_GLOBS)}), so the graph does not "
                    f"read it")
    if rel not in inputs:
        return "", (f"names `{text}`, which this component's compose reading was not handed - "
                    f"it is missing, or the walk leaves it out")
    if rel in lost:
        return "", f"names `{text}`, whose services could not be read (see its row)"
    return rel, ""


#: The ways on for a context no component holds as written (D534-78, D534-84, D534-88). A
#: respelling of a declared component comes first: a junction, a symlink, a trailing dot or
#: an 8.3 short name can reach a declared component's directory on the filesystem, and
#: declaring it again would put two components over one directory. Declaring is offered
#: only for a directory that exists, since the workspace parser refuses one that does not.
#: The row never cites what the filesystem resolves the context to: rows are in the shard
#: digest, and that answer is one machine's.
def _ways_on(where: str) -> str:
    return (f"if it means a declared component, write that component's directory as "
            f"declared, relative to {where}; otherwise, if that directory exists, declare it "
            f"as a component to map it, and if it does not, the context is wrong")


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
             listed: bool, *, where: str = "this file") -> tuple[str, str, str]:
    """(the component holding a build context, how it holds it, why none does). `where` is
    the file the context is written in, as the reason names it: a base's, where an
    `extends:` brought the context (review B21-3).

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
                        f"the directory it names is not written in {where}")
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
                                  f"write that relative to {where}" if single else
                                  f"if it means a directory in {here}, write it relative to "
                                  f"{where}"))
    if _REMOTE.match(context):
        return "", "", f"builds `{context}`, which is not a directory in {here}"
    written = context.replace("\\", "/")
    if written.startswith("/") or _DRIVE.match(written):
        return "", "", (f"builds `{context}`, an absolute path, which cannot be placed in a "
                        f"workspace that may be checked out anywhere; if it names a directory "
                        f"in {here}, write it relative to {where}")
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
                        f"relative to {where}")
    if single:
        # One component, as in a `--target` build: no declaring clause, since a workspace
        # `--target` infers has no file to declare into (D534-93).
        return "", "", outside
    if best is not None:             # only the workspace root's component holds it
        return "", "", (f"builds `{context}`, which lies in no declared component but the "
                        f"workspace root's, as written. Paths are matched exactly, so "
                        f"{_ways_on(where)}")
    return "", "", (f"builds `{context}`, which no declared component holds as written. "
                    f"Paths are matched exactly, so {_ways_on(where)}")


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
    #: Batch 24 (decision 57): the bases its `extends:` chain reaches, nearest first - each
    #: `(file, name, node)` - whose `environment:` it takes where nothing nearer writes the key.
    bases: tuple = ()


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


def apart_view(claim: Claim, name: str, declaring: str, apart: list[dict]) -> str:
    """The view a declaration's settings keep where an `[[apart]]` block names its pair (T621,
    A'; design pass 24, section 5), the rules in order: a claim of its component's OWN namesake
    service - `_claim`'s namesake test, never `own == edge.dst`, which every compose claim meets
    (D24-71, D24-77, D24-89) - has no join to refuse, and keeps its view; a claim a build places -
    a `-f` layer keyed with its component's building files among them, a workspace contradicting
    itself (D24-43) - keeps it, since a block refuses no build (D24-7); any other is filed under
    its own claim's node key, `<service>@<declaring component>`, a view no component's code
    reads: it feeds neither the component named like the service nor the declaring component's
    own code (D24-8, D24-16)."""
    if ids.service_id(name, declaring) == ids.component_service_id(declaring):
        return claim.view
    if claim.mapped_by == "build" or not any(
            _canonical.apart_pair(block, declaring, name) for block in apart):
        return claim.view
    return ids.service_id(name, declaring).removeprefix("svc:")


def env_view(name: str, rel: str, declares: bool, comp: str, how: str,
             placed_in: dict[str, list[tuple[str, str]]], declaring: set[str]) -> str:
    """The env view a declaration's values are published under (decision 55, design pass 24 -
    ruling 10 revised): the component its OWN build places its claim on, where that build's
    context is a component's own directory (`exact` - path arithmetic, `built_by`'s answer,
    never the filesystem, so a value keys alike on every machine; the build a T620 chain brings
    counts as the service's own). A context inside a component - the declaring one's (`own`)
    or another's (`within`, an inference, which never carries settings to code) - and a build
    nothing could place key by the name rule, as on `main` (D24-137: keyed to the declaring
    component, a sidecar's settings configured its component's whole code with no row).

    A declaration with no `build:` of its own is a `-f` layer where its component builds the
    name in OTHER files: it keys with those builds where every one places it in one component,
    exactly, and by the name rule where they do not (D24-20, D24-47, D24-57, D24-68). A
    declaration whose own file builds the name is no layer - it keys by the name rule, whatever
    other files build (D24-142, D24-150). The name rule is the service's own name: the view the
    component named like it reads (D24-6: `main`'s filesystem branch, gone, decided little).

    `placed_in` holds, per file of the component, the placements of this name's builds there;
    `declaring` the files declaring a build of it, placed or not."""
    if declares:
        return comp if how == "exact" else name
    if rel in declaring:
        return name
    others = [(c, h) for r, placed in placed_in.items() if r != rel for c, h in placed]
    if others and all(h == "exact" for _, h in others) and len({c for c, _ in others}) == 1:
        return others[0][0]
    return name


#: compose's own default file discovery (compose-go `cli/options.go` at 6ec8efb,
#: `DefaultFileNames` and `DefaultOverrideFileNames`, each in order of preference): in one
#: directory, the first base present and the first override present are what
#: `docker compose up` merges with no `-f` (T588).
DEFAULT_BASES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
DEFAULT_OVERRIDES = ("compose.override.yml", "compose.override.yaml",
                     "docker-compose.override.yml", "docker-compose.override.yaml")


def read_order(rels: list[str], layers: dict[str, str]) -> list[str]:
    """A component's compose files in the order their values are read (design pass 24, section
    2): their component-relative POSIX paths' SEGMENT order - compared segment by segment,
    case-sensitively, so `docker/compose.yml` comes before `docker-compose.yml` as on `main`, and
    a case-distinct pair reads alike on every platform, where the platform's `Path` folded case
    on Windows (D534-74, D24-14, D24-136) - with each T588 override right after its base, since
    compose merges the override over it and segment order alone would read it first (D24-11).
    `layers` maps each override to its base (`projects`)."""
    order = sorted(rels, key=lambda rel: PurePosixPath(rel).parts)
    out: list[str] = []
    for rel in order:
        if rel in layers:
            continue
        out.append(rel)
        out.extend(other for other in order if layers.get(other) == rel)
    return out


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


class Chain(NamedTuple):
    """A service's `extends:` chain as `_through_extends` reads it (T620)."""

    declares: bool          # a `build:` up to the context that places it, its own included
    context: str | None     # that context, as written, or None
    context_dir: str        # the directory it resolves from: the file writing it
    build_from: str         # `<file>:<line>` of the base writing it, "" for the service's own
    base: str               # that base's name, "" for the service's own
    extends: str            # the base that could not be followed, as written ("" for none)
    why: str                # why not - a row - or ""
    #: `<file>:<line>` of the first base declaring a `build:` with no context, where none is
    #: written up the chain and the service declares no `build:` of its own: that build
    #: places it (review B21-18). For the edge and the row only - never for T588's layering,
    #: since a context-less build leaves its base file's context standing (review B21-25).
    declared_at: str = ""
    #: The first `image:` a base up the chain writes - its value node - and the `<file>:<line>`
    #: writing it, the chain walked whole, past the base whose context places the build (batch
    #: 23, D23-86): compose merges a base's `image:` into a service that writes none.
    image: Any = None
    image_from: str = ""
    #: Where it broke at a `file:` naming a file this reader does not read, `reach`'s reason -
    #: every reason but "names no file", which compose refuses (batch 23, D23-119).
    extends_unread: str = ""
    #: Every base the chain reached, nearest first - `(file, name, node)` - up to where it ends
    #: or breaks: compose merges each base's `environment:` into the service, the nearer
    #: winning, and a broken chain brings what it reached (decision 57, D24-24).
    bases: tuple = ()


def _through_extends(rel: str, name: str, service: dict, index: dict[str, dict],
                     dir_of, follow) -> Chain:
    """A service's chain of `extends:` bases (T620): the first context up it - the service's
    own first - from the directory of the file writing it; with none on the chain, a `build:`
    any of them declares builds the project directory, as `_context_of` reads a mapping with
    no context. The chain is followed WHOLE, past the context that places the service, since
    compose resolves every base: one the graph cannot reach is a row, named through the bases
    it was reached by (batch 21's review). Where it breaks before any context, the service
    keeps its own reading. `index` maps a file to its services `(body, line)`;
    `follow(file_dir, written)` is `reach` for this component. The first `image:` a base
    writes is read off the base's own node, which `index` keeps beside its body (D23-95)."""
    current, cur_name, cur_rel = service, name, rel
    seen = {(rel, name)}
    img: tuple[Any, str] = (None, "")
    reached: list[tuple[str, str, Any]] = []

    def done(chain: Chain, unread: str = "") -> Chain:
        return chain._replace(image=img[0], image_from=img[1], extends_unread=unread,
                              bases=tuple(reached))

    declares = False
    found: tuple[bool, str | None, str, str, str] | None = None
    # The first base declaring a `build:` with no context: with none written up the chain, its
    # build is the one that places the service, from the project directory (review B21-18) -
    # never where the service declares a `build:` of its own, which places it (review B21-26).
    declared_at = ""
    own_build = _explicit_context(service)[0]
    while True:
        did, explicit, context = _explicit_context(current)
        mine = (cur_rel, cur_name) == (rel, name)
        if not mine and img[0] is None:
            written_image = _yaml.mapping_get(index[cur_rel][cur_name][2], "image")
            if written_image is not None:
                img = (written_image, f"{cur_rel}:{_yaml.line_of(written_image)}")
        if found is None:
            declares = declares or did
            if did and not mine and not declared_at and not own_build:
                declared_at = f"{cur_rel}:{index[cur_rel][cur_name][1]}"
            if explicit:
                found = (declares, context, dir_of(cur_rel),
                         "" if mine else f"{cur_rel}:{index[cur_rel][cur_name][1]}",
                         "" if mine else cur_name)
        # Present, even written as null, is an `extends:` to follow or row (review B21-19).
        if not (isinstance(current, dict) and "extends" in current):
            if found is not None:
                return done(Chain(*found, "", ""))
            return done(Chain(declares, "." if declares else None, dir_of(rel), "", "", "", "",
                              declared_at))
        ext = current["extends"]
        base_name, base_file = ((ext, None) if isinstance(ext, str) else
                                (ext.get("service"), ext.get("file")) if isinstance(ext, dict)
                                else (None, None))
        # The base as written, never a Python `None` (review B21-7): "" where none is named.
        named = base_name.strip() if isinstance(base_name, str) else ""
        written = (f"{named} in {base_file.strip()}"
                   if named and isinstance(base_file, str) and base_file.strip() else named)
        via = ("" if (cur_rel, cur_name) == (rel, name) else
               f"reaches `{cur_name}` in `{cur_rel}` through `extends:`, which ")
        why, base_rel, entry, unread = "", cur_rel, None, ""
        if not named:
            why = "names no base service to extend"
        elif base_file is not None:
            base_rel, reason = follow(dir_of(cur_rel), base_file)
            if reason:
                why = f"extends `{named}`, but its `file:` {reason}"
                # A base this reader does not read may declare a build of any image; a `file:`
                # naming no file is compose's own refusal and hides none (D23-119, D23-132).
                unread = "" if reason == "names no file" else reason
        if not why and (base_rel, named) in seen:
            why = f"extends itself through `{named}` in `{base_rel}`, which compose refuses"
        if not why:
            entry = index.get(base_rel, {}).get(named)
            if entry is None or entry[0] is None:
                why = f"extends `{named}`, which `{base_rel}` does not declare as a service"
        if why:
            if found is not None:
                return done(Chain(*found, written, via + why), unread)
            # What the chain declared up to the break stands, as where it ends (review B21-18).
            return done(Chain(declares, "." if declares else None, dir_of(rel), "", "", written,
                              via + why, declared_at), unread)
        seen.add((base_rel, named))
        reached.append((base_rel, named, entry[2]))
        current, cur_name, cur_rel = entry[0], named, base_rel


def _claims(ctx: ExtractContext, repo: RepoRef, parsed: list[tuple[str, Any]],
            layers: dict[str, str], inputs: set[str] | None = None,
            lost: set[str] | None = None) -> dict[str, list[Decl]]:
    """Each file's declarations, in order, each with its claim, from ALL of a component's
    compose files (T534 v5).

    One pass decides whether the component is **building** - any file declares `build:`,
    whether or not it can be placed, since a typo is still a statement that the file builds
    (ruling 1, D534-1) - and which components each service name is built from. Then each
    declaration is placed on its own terms (D534-7): its own `build:` first; a layer that
    declares none takes the component-wide answer when it is one component, and a row when
    it is several.

    Before that rule, a service an override writes no build context for - no `build:`, or a
    `build:` mapping whose `context:` is absent or null (`build: {}` included), which compose
    merges over the base's (T658) - takes its base's claim for the same name (T588; `layers`
    maps each override to its base): nine of `Claim`'s seventeen fields - `target`, `scope`,
    `component`, `mapped_by`, `join`, `within`,
    `inferred`, `local` and `build_from` - and its `extends_unread` where it carries none of
    its own, a fact no row is emitted from (D23-118) - and `view` (decision 55: compose merges
    the pair itself); `unplaced` and `ambiguous` are the reasons for the base's own rows,
    which stay the base's (design review D15a-15); `unfollowed` and `extends` are its own
    chain's, whose row is its own (D23-134); and `image` and `image_from` are its package's,
    computed once and set on both claims (D23-158).
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
    #: Per service name, per file, how each of its builds there is placed - (component, how) -
    #: and the files declaring a build of it, placed or not: what a declaration's env view is
    #: keyed by (decision 55, `env_view`).
    placed_in: dict[str, dict[str, list[tuple[str, str]]]] = {}
    declaring: dict[str, set[str]] = {}
    pending: list[tuple[str, str, Any, dict | None, tuple[bool, str, str, str, bool], str,
                       int, tuple[str, str, str, bool, bool, str, tuple],
                       tuple[Any, str]]] = []
    building = False

    def dir_of(rel: str) -> str:
        return posixpath.normpath(posixpath.join(base, posixpath.dirname(rel) or "."))

    # Each declaration with its body, constructed once here and handed on (D534-75); and,
    # for a base an `extends:` names (T620), each file's services by name - the first
    # declaration of a name, where two keys YAML types apart spell one (B10-14).
    declared = {rel: [(k, v, _body(v)) for k, v in _yaml.mapping_pairs(services_node)]
                for rel, services_node in parsed}
    # Each base's value node is kept beside its body, so a chain reads the `image:` it writes
    # and the line writing it, which the constructed body cannot give (D23-95).
    index: dict[str, dict[str, tuple[Any, int, Any]]] = {}
    for rel, items in declared.items():
        for key_node, value_node, body in items:
            index.setdefault(rel, {}).setdefault(str(key_node.value),
                                                 (body, _yaml.line_of(key_node), value_node))

    def follow(file_dir: str, written: Any) -> tuple[str, str]:
        return reach(file_dir, written, base=base, layout=layout, own=repo.alias,
                     listed=listed, inputs=inputs if inputs is not None else set(index),
                     lost=lost or set())

    for rel, _services_node in parsed:
        file_dir = dir_of(rel)
        for key_node, node, service in declared[rel]:
            name = str(key_node.value)
            declares, context = _context_of(service)
            context_dir, followed = file_dir, ("", "", "", False, False, "", ())
            where, extended_from = "this file", ""
            # The image it runs with (batch 23): an `image:` it writes decides, as compose
            # merges a service's own key over a base's; else its chain's, below.
            own_image = _yaml.mapping_get(node, "image")
            imaged: tuple[Any, str] = ((own_image, f"{rel}:{_yaml.line_of(own_image)}")
                                       if own_image is not None else (None, ""))
            if isinstance(service, dict) and "extends" in service:
                chain = _through_extends(rel, name, service, index, dir_of, follow)
                own = bool(_explicit_context(service)[1])
                if not own:
                    # Placed by what its chain brings; its own context, written, places it as
                    # on `main`, and its chain is still followed for the row.
                    declares, context, context_dir = (chain.declares, chain.context,
                                                      chain.context_dir)
                    if chain.build_from:
                        where = f"`{chain.build_from.rsplit(':', 1)[0]}`"
                        extended_from = chain.base
                # The base whose build places it, for the edge and the row; and whether a base
                # WROTE that build's context, which alone stops T588's layering (B21-25).
                followed = ((chain.build_from or chain.declared_at) if not own else "",
                            chain.why, chain.extends, own, bool(chain.build_from) and not own,
                            chain.extends_unread, chain.bases)
                if own_image is None:
                    imaged = (chain.image, chain.image_from)
            comp, how, why = (built_by(context_dir, context, layout, repo.alias, listed,
                                       where=where)
                              if declares else ("", "", ""))
            if why and extended_from:
                # Review B21-3: said where the path is written - the base's file.
                why = (f"takes its build through `extends:` from `{extended_from}` at "
                       f"`{followed[0]}`: it {why}")
            building = building or declares
            # Recorded for every match that is not a component's own directory, so `./svc-a`
            # and a typo like `./nope` read differently even where both map (D534-37).
            within = (posixpath.normpath(posixpath.join(context_dir,
                                                        context.replace("\\", "/")))
                      if how in ("within", "own") and context else "")
            # The env view is keyed after every file's builds are known (decision 55): a layer
            # keys with the builds its component's other files place.
            pending.append((rel, name, node, service,
                            (declares, comp, why, within, how == "within"), how,
                            _yaml.line_of(key_node), followed, imaged))
            if comp:
                built.setdefault(name, {}).setdefault(comp, set()).add(
                    (within, how == "within"))
                placed_in.setdefault(name, {}).setdefault(rel, []).append((comp, how))
            if declares:
                declaring.setdefault(name, set()).add(rel)
    decls: dict[str, list[Decl]] = {}
    #: The declarations a context a base wrote places - `(file, its index in that file's
    #: declarations)`, never its line, which flow style shares between services (review
    #: B21-33) - which no override layers over its base (T588); a base's context-less build
    #: leaves that context standing.
    brought: set[tuple[str, int]] = set()
    #: Per declaration - `(file, its index in that file's declarations)` - the image it runs
    #: with (its value node, the `<file>:<line>` writing it), and where its OWN `image:` is
    #: written ("" where it writes none).
    imaged_of: dict[tuple[str, int], tuple[Any, str]] = {}
    own_place: dict[tuple[str, int], str] = {}
    #: T621 under A' (decision 56): the `[[apart]]` blocks, read for keying alone.
    apart = [b for b in (ctx.config or {}).get("apart") or [] if isinstance(b, dict)]
    for rel, name, node, service, decl, how, line, followed, imaged in pending:
        frm, unfollowed, written, _own, context_brought, unread, bases = followed
        view = env_view(name, rel, decl[0], decl[1], how, placed_in.get(name, {}),
                        declaring.get(name, set()))
        claim = _claim(name, *decl, own=repo.alias, building=building,
                       built=built.get(name, {}), view=view)
        claim = claim._replace(view=apart_view(claim, name, repo.alias, apart))
        if frm and claim.mapped_by == "build":
            claim = claim._replace(build_from=frm)
        if unfollowed:
            claim = claim._replace(unfollowed=unfollowed, extends=written, extends_unread=unread)
        items = decls.setdefault(rel, [])
        if context_brought:
            brought.add((rel, len(items)))
        imaged_of[(rel, len(items))] = imaged
        own_written = _yaml.mapping_get(node, "image")
        own_place[(rel, len(items))] = (f"{rel}:{_yaml.line_of(own_written)}"
                                        if own_written is not None else "")
        items.append(Decl(name, node, service, claim, line, bases))
    for override, base_rel in layers.items():
        if override not in decls:
            continue
        inherited: dict[str, Claim] = {}
        for decl in decls.get(base_rel, []):
            inherited.setdefault(decl.name, decl.claim)
        decls[override] = [
            decl._replace(claim=inherited[decl.name]._replace(
                unplaced="", ambiguous=(), unfollowed=decl.claim.unfollowed,
                extends=decl.claim.extends,
                extends_unread=decl.claim.extends_unread or inherited[decl.name].extends_unread))
            # T658 (batch 27): an override writing no context of its own - no `build:`, or a
            # `build:` mapping of `dockerfile`/`args` alone - builds from its base's, as compose
            # merges a `build:` mapping; one writing its own context keeps it (B21-25).
            if decl.name in inherited and not _explicit_context(decl.service)[1]
            and (override, k) not in brought else decl
            for k, decl in enumerate(decls[override])]
    # The image each claim's service runs with (batch 23), set after the layering so an
    # override's inherited claim never carries its base's superseded image: a T588 package
    # runs the override's image - its own `image:`, else its chain's - else its base
    # declaration's, computed ONCE, and BOTH its claims carry it (D23-153, D23-158, D23-166);
    # `image_from` cites where it is written wherever a claim's own declaration does not.
    runs = dict(imaged_of)
    for override, base_rel in layers.items():
        first: dict[str, int] = {}
        for kb, decl in enumerate(decls.get(base_rel, [])):
            first.setdefault(decl.name, kb)
        for k, decl in enumerate(decls.get(override, [])):
            if decl.name in first:
                o_key, b_key = (override, k), (base_rel, first[decl.name])
                runs[o_key] = runs[b_key] = (imaged_of[o_key] if imaged_of[o_key][0] is not None
                                             else imaged_of[b_key])
    for rel, items in decls.items():
        for k, decl in enumerate(items):
            image_node, place = runs.get((rel, k), (None, ""))
            text = (_yaml.scalar_text(image_node).strip()
                    if isinstance(image_node, props.yaml.ScalarNode) else "")
            if text:
                items[k] = decl._replace(claim=decl.claim._replace(
                    image=text, image_from="" if place == own_place[(rel, k)] else place))
    # Worded from the claim each ends with - an override's is its base's - since the claim
    # stays the component's only where no build places it (batch 21's review).
    return {rel: [decl._replace(claim=_unfollowed_worded(decl)) if decl.claim.unfollowed
                  else decl for decl in items]
            for rel, items in decls.items()}


def _unfollowed_worded(decl: "Decl") -> Claim:
    """A claim's unfollowed reason with what places the service, in the order it is asked: the
    base whose `build:` placed it (`build_from` - its context, or, where the service declares
    no build, its context-less build), its own `build:`, the build its component's other
    compose files declare (a layer's), or nothing - the claim then stays the component's."""
    claim = decl.claim
    said = claim.unfollowed
    if claim.mapped_by != "build":
        # Unknown only where nothing else says what it builds: an unplaced build has its row.
        said += "" if claim.unplaced else ", so the build it would take is unknown"
        tail = "The claim stays this component's"
    elif claim.build_from:
        tail = f"It is placed by the `build:` at `{claim.build_from}`"
    elif _explicit_context(decl.service)[0]:
        # Its own `build:`, a context written or not (review B21-17).
        tail = "It is placed by its own `build:`"
    else:
        tail = "It is placed by the build its component's other compose files declare"
    return claim._replace(unfollowed=f"{said}. {tail}")


def _claim(name: str, declares: bool, comp: str, why: str, within: str, inferred: bool,
           *, own: str, building: bool,
           built: dict[str, set[tuple[str, bool]]], view: str) -> Claim:
    """Where one declaration lands. A node's `component` is the component its id is scoped
    to (D534-13): the built component, or the declaring one.

    Environment is filed under `view` (`env_view`, decision 55), and a service a building
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
#: The parsers this module's readers can lack (batch 16, the owner's ruling 30): a shard
#: records each one's version when it was built, or that it was missing (batch 22).
PARSERS = ("yaml",)

UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "compose-env-unresolved": ('key',),
    "compose-unparsed": ('error',),
    "grammar-unavailable": ('language',),
    "superseded-declaration": ('key', 'value'),
    "yaml-typed-value": ('key', 'value', 'readings'),
    "compose-build-unresolved": ('service',),
    "compose-build-ambiguous": ('service', 'components'),
    "compose-extends-unresolved": ('service', 'extends'),
    "compose-include-unresolved": ('include',),
    "compose-image-unresolved": ('service',),
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

def _deployment_only(ctx: ExtractContext, repo: RepoRef, decls: dict[str, list["Decl"]],
                     lost: set[str], include_unread: dict[str, list[dict]]) -> bool | None:
    """Whether the component deploys only images it does not build, as far as compose can say
    (batch 23, D23-6, D23-61, D23-101, D23-116): False wherever a compose file it read declares
    `build:` - a file it did not read cannot make a building component deployment-only; True
    only where it read every compose file of the component - none unread, none whose
    `include:` names a file it does not read, none lost to its case, no directory of the
    component unlisted, and no claim of the component, in any file, whose chain names a base
    it does not read, which may declare a build - and none declares `build:`; else None, not
    known, so not published. Per component, so a sibling file's gap reaches every node."""
    items = [decl for file_decls in decls.values() for decl in file_decls]
    if any(_context_of(decl.service)[0] for decl in items):
        return False
    config = ctx.config or {}
    # Compose's own names over the case record, which holds every deployment reader's losses,
    # as helm applies its own (D23-31).
    case_lost = [rel for rel in (config.get("deployment_off_case") or {}).get(repo.alias, [])
                 if _canonical.names_compose_file(rel, folded=True)]
    unknown = (lost or any(include_unread.values()) or case_lost
               or (config.get("unlisted") or {}).get(repo.alias)
               or any(decl.claim.extends_unread for decl in items))
    return None if unknown else True


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
    #: The namespaces it writes (batch 22): shared - it writes views under the names of the
    #: services and components its files declare, other components' included.
    CACHE_KEYS = (CACHE_KEY, ORIGIN_KEY, CANDIDATES_KEY, CLAIMS_KEY)
    #: The one spelling, which `reach` refuses a file by too (review B21-10).
    INPUT_GLOBS = COMPOSE_GLOBS
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
        for repo in ctx.repos:
            first_node = len(res.nodes)
            #: The component's own state, as on `main` within it (design pass 24, section 2):
            #: its views - `_merge` folds its files' values there, never into another
            #: component's - the keys its canonical source has claimed (`owned`), what each
            #: value it holds may be read as (`held`, T536, review B10-4), where each came from,
            #: and its DG_CONFIGURES edges, marked `wins` once its files are all read.
            env = {"views": {}, "owned": {}, "held": {}, "origins": {}, "configures": [],
                   "claimed": set()}
            owned, held = env["owned"], env["held"]
            # In segment order, each override right after its base (`read_order`). Nothing
            # placed depends on it, since each file places its own `build:` (D534-7), but which
            # env value is read last does. Each file is read and parsed once (remainder E).
            includes: dict[str, Any] = {}
            lost: set[str] = set()
            by_rel = {_rel(repo, path): path for path in ctx.inputs(repo)}
            layers = projects(list(by_rel))
            parsed = [item for rel in read_order(list(by_rel), layers)
                      if (item := self._read(res, repo, by_rel[rel], includes, lost)) is not None]
            inputs = set(by_rel)
            decls = _claims(ctx, repo, parsed, layers, inputs, lost)
            # T623: per node, this component's files declaring a service on it - the files
            # a block must designate to rank their values.
            on_node: dict[str, list[str]] = {}
            for rel, declared in decls.items():
                for decl in declared:
                    if decl.service is not None:
                        on_node.setdefault(decl.claim.target, []).append(rel)
            # Batch 23: what each file's `include:` names that this reader does not read goes
            # on the file's node (D23-131), so the include is read before the files are (the
            # rows' order is the writer's, which sorts them).
            include_unread = {rel: self._includes(res, ctx, repo, rel, includes[rel], inputs,
                                                  lost)
                              for rel in sorted(includes)}
            for rel, _services_node in parsed:
                self._file(res, ctx, repo, rel, decls.get(rel, []), env["views"], owned,
                           held, layers.get(rel, ""), on_node, include_unread.get(rel, []),
                           env=env)
            self._publish(res, ctx, repo, env, views)
            # A file declaring no service of its own whose `include:` names a file this reader
            # does not read: its node, carrying no claim, says so - else nothing in the graph
            # would, as an unreadable file's `unread` node says its own gap.
            claiming = {rel for rel, _ in parsed}
            for rel, unread in include_unread.items():
                if unread and rel not in claiming and rel not in lost:
                    res.nodes.append(Node(id=ids.deployment_id(repo.alias, rel), type="deploy",
                                          props={"kind": CONFIG_KIND, "path": rel,
                                                 "component": repo.alias,
                                                 "include_unread": unread}))
            only = _deployment_only(ctx, repo, decls, lost, include_unread)
            if only is not None:
                for node in res.nodes[first_node:]:
                    if node.type == "deploy" and node.props.get("kind") == CONFIG_KIND:
                        node.props["deployment_only"] = only
        return res

    def _publish(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, env: dict,
                 views: dict[str, dict]) -> None:
        """Once a component's files are all read (design pass 24, section 2): its own winner for
        each (view, key) is its candidate - the value, raw text and spellings its entry
        carries, its service, file, line, readings, package, and `local` and `unplaced` for
        `env-key-from-building-repo` (D24-66, D24-73) - and every key of each view it set or
        claimed in is decided again over the candidates the run holds, by
        `_canonical.env_winner`: the shared view holds the winner's entry - its own spellings,
        never a loser's (D24-26b) - and the origin table its origin (D24-13). Its
        DG_CONFIGURES edges are marked `wins` where they are its own winner."""
        candidates = ctx.cache.setdefault(CANDIDATES_KEY, {})
        claims = ctx.cache.setdefault(CLAIMS_KEY, {})
        origins = ctx.cache.setdefault(ORIGIN_KEY, {})
        for view_key, mine in env["views"].items():
            for key, entry in mine["values"].items():
                origin = env["origins"].get(view_key, {}).get(key, {})
                candidates.setdefault(view_key, {}).setdefault(key, {})[repo.alias] = {
                    **entry, "component": repo.alias, "service": origin.get("service", ""),
                    "readings": list(env["held"].get(view_key, {}).get(key, ())),
                    "package": origin.get("package", ""), "local": origin.get("local", False),
                    "unplaced": origin.get("unplaced", False)}
            shared = views.setdefault(view_key, {"values": {}, "unresolved": {}})
            for key, miss in mine["unresolved"].items():
                shared["unresolved"].setdefault(key, miss)
        for view_key in sorted(set(env["views"]) | env["claimed"]):
            for key, cands in sorted(candidates.get(view_key, {}).items()):
                decision = _canonical.env_winner(cands, claims.get(view_key, {}),
                                                 ctx.config or {})
                win = cands[decision.winner]
                shared = views.setdefault(view_key, {"values": {}, "unresolved": {}})
                shared["values"][key] = {field: win[field] for field in
                                         ("value", "file", "line", "raw", "spellings")}
                origins.setdefault(view_key, {})[key] = {
                    "component": win["component"], "file": win["file"],
                    "service": win["service"], "local": win["local"],
                    "unplaced": win["unplaced"]}
        for index, view_key, key, rel, line, name in env["configures"]:
            entry = env["views"].get(view_key, {}).get("values", {}).get(key)
            origin = env["origins"].get(view_key, {}).get(key, {})
            res.edges[index].props["wins"] = bool(
                entry and entry["file"] == rel and entry["line"] == line
                and origin.get("service") == name)

    def _includes(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
                  node: Any, inputs: set[str], lost: set[str]) -> list[dict]:
        """T620: each `include:` entry this reader cannot follow is a row on its line - its
        services are missing from the graph, not absent. One it can follow is a compose file
        this component reads, on its own and from its own directory, so an entry moving that
        directory (`project_directory`) is said too.

        Batch 23: an `include:` that is no list is read as a list of what it spells - a scalar's
        text, a single mapping's `path` or each file of its `path` list - beside its "is not a
        list" row, and one spelling no file hides nothing, that row alone (D23-141, D23-148,
        D23-149); a value that is not a string - a number - names no file, listed or not
        (D23-160). Returns the files it names that this reader does not read - `reach`'s every
        reason but "names no file", which compose refuses - each `{"file", "line"}`, for the
        compose node's `include_unread` (D23-131, D23-161)."""
        layout = _layout(ctx)
        listed = repo.alias in layout
        if not listed:
            layout = {repo.alias: "."}
        base = layout[repo.alias]
        file_dir = posixpath.normpath(posixpath.join(base, posixpath.dirname(rel) or "."))
        y = props.yaml

        def row(written: str, line: int, why: str) -> None:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-include-unresolved", rel, line,
                f"`include:` {why}. The services it declares are missing from the graph, not "
                f"absent", include=written))

        unread: list[dict] = []
        listed_entries = isinstance(node, y.SequenceNode)
        if not listed_entries:
            row("", _yaml.line_of(node), "is not a list of files")
        for item in node.value if listed_entries else [node]:
            line = _yaml.line_of(item)
            project_dir: Any = None
            if isinstance(item, y.ScalarNode):
                # The value as YAML constructs it: a number is no file name (D23-160); one
                # spelling nothing, written alone, hides nothing beyond its row (D23-149).
                value = _yaml.constructed(item)
                paths: Any = ([value] if isinstance(value, str)
                              and (listed_entries or value.strip()) else None)
            elif isinstance(item, y.MappingNode):
                body = _yaml.constructed(item)
                paths = body.get("path")
                paths = [paths] if isinstance(paths, str) else paths
                project_dir = body.get("project_directory")
            else:
                paths = None
            if not isinstance(paths, list) or not paths:
                if listed_entries:
                    row("", line, "has an entry that names no file")
                continue
            for written in paths:
                target, why = reach(file_dir, written, base=base, layout=layout,
                                    own=repo.alias, listed=listed, inputs=inputs, lost=lost)
                shown = written if isinstance(written, str) else ""
                if why:
                    row(shown, line, why)
                    if why != "names no file":
                        unread.append({"file": shown, "line": line})
                    continue
                if isinstance(project_dir, str) and project_dir.strip():
                    moved = posixpath.normpath(posixpath.join(file_dir, project_dir.strip()))
                    own_dir = posixpath.normpath(posixpath.join(
                        base, posixpath.dirname(target) or "."))
                    if moved != own_dir:
                        row(shown, line,
                            f"names `{shown}` with `project_directory: {project_dir.strip()}`, "
                            f"which compose resolves its relative paths from; the graph reads "
                            f"that file on its own, from its own directory, so a relative "
                            f"`build:` in it is placed from there, not from the project "
                            f"directory - and what it builds may be misplaced")
        return unread

    def _read(self, res: ExtractResult, repo: RepoRef, path: Path,
              includes: dict[str, Any] | None = None,
              lost: set[str] | None = None) -> tuple[str, Any] | None:
        """(component-relative path, the `services:` node), or None with the row that
        says why the file declares nothing this extractor can read. A file's top-level
        `include:` node goes to `includes`, whether or not it declares services, and a file
        that cannot be read or parsed to `lost` (T620)."""
        rel = _rel(repo, path)
        lost = set() if lost is None else lost
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            lost.add(rel)
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
            lost.add(rel)
            # E6. Recorded, and the build continues: one unreadable file must not abort a
            # workspace scan, and must not vanish from it either.
            res.unresolved.append({
                "extractor": self.name, "kind": "compose-unparsed",
                "component": repo.alias, "source_file": rel, "line": 0, "error": error,
                "why": "could not be parsed as YAML, so every service it declares and "
                       "every address they publish are missing from the graph"})
            res.nodes.append(_unread(repo, rel, "compose-unparsed"))
            return None
        if includes is not None and isinstance(doc, dict) and "include" in doc:
            includes[rel] = _yaml.mapping_get(
                _yaml.document_nodes(props.read_text(path))[0], "include")
        services = doc.get("services") if isinstance(doc, dict) else None
        if services is None:
            return None
        if not isinstance(services, dict):
            # `services:` written as a LIST (or any non-mapping) is valid YAML, so no parse
            # error fires and a bare `return` reported an INVALID file as one that simply
            # declared no services. Record it: invalid must not read as absent - and lost, so
            # an `include:` or `extends:` naming it says so (review B21-15).
            lost.add(rel)
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
              held: dict[str, dict[str, tuple[str, ...]]], package: str,
              on_node: dict[str, list[str]] | None = None,
              include_unread: list[dict] | None = None, *, env: dict) -> None:
        deploy = ids.deployment_id(repo.alias, rel)
        # Batch 24: the file's package by its WORKSPACE key - `mark-canonical`'s `package_of`
        # rule (`_canonical.package_key`), so the env decision and the node decision read one
        # package set (D24-31).
        declared = _canonical.declared_package({"package": package})
        pkg = _canonical.package_key(repo.alias, _layout(ctx), rel, declared, deploy)
        # An override names the base whose package it layers (T588): `package` is the link
        # pass's word - any reader may say which file's package one of its files belongs
        # to - and it names a file this same component reads (D15a-22).
        res.nodes.append(Node(id=deploy, type="deploy", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias,
            **({"package": package} if package else {}),
            # Batch 23: the files its `include:` names that this reader does not read (D23-131).
            **({"include_unread": include_unread} if include_unread else {})}))
        cfg = ids.config_id(repo.alias, rel)
        res.nodes.append(Node(id=cfg, type="cfg", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))

        for decl in decls:
            if decl.service is None:
                continue
            self._service(res, ctx, repo, rel, deploy, cfg, decl.name, decl.node, views,
                          owned, held, decl.line, placed=decl.claim,
                          files=(on_node or {}).get(decl.claim.target, [rel]), env=env,
                          package=pkg, declared=declared, bases=decl.bases)

    def _service(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
                 deploy: str, cfg: str, name: str, node: Any,
                 views: dict[str, dict], owned: dict[str, set[str]],
                 held: dict[str, dict[str, tuple[str, ...]]], line: int, *,
                 placed: Claim, env: dict, files: list[str] | None = None,
                 package: str = "", declared: str = "", bases: tuple = ()) -> None:
        # The claim on a service, in exactly the shape `mark-canonical` groups on. Two files
        # naming one service produce two of these, identically, and the workspace's own
        # [[canonical]] block decides — never this extractor, which cannot see intent.
        res.nodes.append(Node(id=placed.target, type="svc",
                              props={"component": placed.scope}))
        edge_props = {"service": name, "component": placed.component,
                      "mapped_by": placed.mapped_by,
                      # Batch 24: the view its settings are filed under, so `env-ranking` keys
                      # the decision set per (view, service) as extraction does (D24-58).
                      "view": placed.view}
        # The claim, in the decision set of its (view, service) - a package setting no key
        # among them (D24-32, D24-44).
        ctx.cache.setdefault(CLAIMS_KEY, {}).setdefault(placed.view, {}).setdefault(
            ids.norm_id(name), {}).setdefault(package or deploy, []).append(
            (repo.alias, {"kind": CONFIG_KIND, "path": rel,
                          **({"package": declared} if declared else {})}, rel, line))
        env["claimed"].add(placed.view)
        if placed.join:
            edge_props[JOIN_PROP] = "name"
        if placed.within:
            # Not a component's own directory: the place the build pointed at, so a
            # reader can tell `./svc-a` from a typo (D534-37). Strictly inside ANOTHER
            # component it is also an inference (D534-25) - see the provenance below.
            edge_props["built_within"] = placed.within
        if placed.build_from:
            # T620: placed by the build a base declares - where, so the edge cites it.
            edge_props["build_from"] = placed.build_from
        # Batch 23: the reasons a claim carries, as they are - the image link reads a claim
        # whose build could not be placed, whose chain could not be followed, and whose chain
        # broke at a base the graph does not read, which may build any image (D23-43, D23-119).
        for reason in ("unplaced", "unfollowed", "extends", "extends_unread"):
            if getattr(placed, reason):
                edge_props[reason] = getattr(placed, reason)
        if placed.image:
            # Batch 23: the image it runs with, as compose merges it, and where that image is
            # written wherever its own declaration does not write it.
            edge_props["image"] = placed.image
            if placed.image_from:
                edge_props["image_from"] = placed.image_from
        res.edges.append(Edge(
            src=deploy, type="DG_DEPLOYS", dst=placed.target, source_file=rel, line=line,
            extractor=self.name, confidence=1.0, props=edge_props,
            provenance=Provenance.INFERRED if placed.inferred else Provenance.EXTRACTED))
        if placed.unplaced:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-build-unresolved", rel, line,
                f"`{name}` {placed.unplaced}. The claim stays this component's", service=name))
        if placed.unfollowed:
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-extends-unresolved", rel, line,
                f"`{name}` {placed.unfollowed}", service=name, extends=placed.extends))
        written_image = _yaml.mapping_get(node, "image")
        if isinstance(written_image, (props.yaml.MappingNode, props.yaml.SequenceNode)):
            shape = "a mapping" if isinstance(written_image, props.yaml.MappingNode) else "a list"
            res.unresolved.append(unresolved_row(
                self.name, repo.alias, "compose-image-unresolved", rel, line,
                f"`{name}`'s `image:` is {shape}, not one image reference, so it takes part in "
                f"no image proposal; write it as one string", service=name))
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
        # Decision 57: what its `extends:` chain brings, cited where its base writes it
        # (design pass 24, section 4).
        inherited = brought_by(bases, {ids.relaxed_key(e["key"]) for e in entries})
        if not entries and not inherited:
            return
        declarations = [{"key": e["key"], "value": e["value"], "file": rel,
                         "line": e["line"]} for e in entries] + [
            {"key": e["key"], "value": e["value"], "file": e["file"], "line": e["line"],
             "inherited": True} for e in inherited]
        brought = {ids.relaxed_key(e["key"]): e["base"] for e in inherited}
        view = _resolve(declarations)
        view_key = placed.view
        views.setdefault(view_key, {"values": {}, "unresolved": {}})
        claim = canonical_claim(ctx.config, name, repo.alias, files or [rel])
        written: set[str] = set()
        readings_of = {ids.relaxed_key(e["key"]): e["readings"] for e in entries + inherited}
        superseded = _merge(views[view_key], view,
                            owned.setdefault(view_key, set()),
                            contested=claim is not None,
                            canonical=is_canonical_source(claim, repo.alias, rel),
                            held=held.setdefault(view_key, {}),
                            readings=readings_of,
                            written=written)
        # Ruling 4: whose each value the component's view now holds is, beside it - its
        # service, `local` and `unplaced` for `env-key-from-building-repo`, and the package its
        # candidate is ranked in. `_publish` writes the shared origin table from the winner:
        # its component, and the file its entry cites - the base's for an inherited value
        # (D24-13, D24-37). Recorded for the keys the merge says it wrote from this file,
        # never inferred by comparing entries.
        origins = env["origins"].setdefault(view_key, {})
        # Whose each superseded value was BEFORE this file's values land - another of this
        # component's declarations, the views being the component's own (batch 24).
        prior = {row["key"]: origins.get(row["key"]) for row in superseded}
        for relaxed in sorted(written):
            origins[relaxed] = {"service": name, "local": placed.local,
                                # A build this file declares and nothing could place is
                                # not "a container it does not build" (D534-35).
                                "unplaced": bool(placed.unplaced or placed.ambiguous),
                                "package": package or deploy}
        for row in superseded:
            # The row names the DROPPED declaration where it is written. This file's value
            # was dropped exactly when the merge did not write it; otherwise the dropped one
            # was held from an earlier file, which the origin table recorded before this
            # file's values landed.
            earlier = prior.get(row["key"]) or {}
            here_dropped = row["key"] not in written
            # Whose each declaration is: this file's service, or the earlier one's. An origin
            # the table does not hold falls to the one-service wording; the branches of
            # `_merge` never reach it - every write records an origin - so it is a guard, not
            # a shape (D534-69).
            kept_service = str(earlier.get("service", "")) if here_dropped else name
            dropped_service = name if here_dropped else str(earlier.get("service", ""))
            if kept_service and dropped_service and kept_service != dropped_service:
                # Two services filed under one view (D534-54): facts only - where the other
                # declaration is, by file and line, whichever way it lies; never which value
                # the component keeps, which a later file may decide, nor a block, which ranks
                # one service's declarations (D24-80, D24-93, D24-114 to D24-116, D24-128).
                res.unresolved.append({
                    "extractor": self.name, "kind": "superseded-declaration",
                    "component": repo.alias, "key": row["key"],
                    "source_file": row["dropped"]["file"], "line": row["dropped"]["line"],
                    "value": row["dropped"]["value"],
                    "why": (f"`{row['key']}` is filed under `{view_key}` as "
                            f"`{row['dropped']['value']}` here for `{dropped_service}` and as "
                            f"`{row['kept']['value']}` at `{row['kept']['file']}:"
                            f"{row['kept']['line']}` for `{kept_service}`; one declaration of "
                            f"`{row['key']}` per view reaches the code, and this one is not it. "
                            f"Both containers still run with their own value; to keep this one "
                            f"in the graph, make it the only declaration of `{row['key']}` "
                            f"filed under `{view_key}`")})
                continue
            # One component, one service in two files - the merge reads the component's own
            # views since batch 24, so both declarations are this component's: facts only -
            # which declaration is not kept, and the owner as why; never the value the graph
            # keeps, which another service of the view or another component can decide
            # (D24-56, D24-123). Across components the rows are `env-ranking`'s.
            said = (f"`{row['key']}` is declared as {row['dropped']['value']!r} here and "
                    f"as {row['kept']['value']!r} in {row['kept']['file']}, which the "
                    f"workspace names as the owner of service `{name}`, so this "
                    f"declaration is not kept; it is reported because ")
            res.unresolved.append({
                "extractor": self.name,
                "kind": "superseded-declaration", "component": repo.alias,
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
            relaxed = ids.relaxed_key(entry["key"])
            resolved = view["values"].get(relaxed)
            # Batch 24: the facts `env-ranking` ranks by (design pass 24, section 3) - the view
            # and package it is filed under, whether it resolved (an unresolved value was never
            # a candidate), what it may be read as, and, once the component's files are all
            # read, whether it is the component's own winner (`wins`, set in `_publish`).
            # `wins` is judged where the value is written: an inherited one, the base's place.
            env["configures"].append((len(res.edges), view_key, relaxed, entry["file"],
                                      entry["line"], name))
            # An inherited key is the extending service's own edge (D24-79): from its file's
            # `cfg` node, at its declaration line, citing where the base writes it.
            here = line if entry.get("inherited") else entry["line"]
            res.edges.append(Edge(
                src=cfg, type="DG_CONFIGURES", dst=envkey, source_file=rel,
                line=here, extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED,
                props={"declared_as": entry["key"], "service": name,
                       "value": resolved["value"] if resolved else "",
                       "raw": entry["value"], "view": view_key,
                       "resolved": resolved is not None,
                       "readings": list(readings_of.get(relaxed, ())),
                       "package": package or deploy, "wins": False,
                       **({"inherited_from": f"{entry['file']}:{entry['line']}"}
                          if entry.get("inherited") else {})}))
        # Its immediate base's own declaration resolves the whole chain beyond it - read once.
        base_unresolved = (_resolve(base_declarations(bases, 0))["unresolved"] if brought
                           else {})
        for key, miss in view["unresolved"].items():
            if key in brought:
                # That base's declaration rows - or defers - by this same rule where its chain
                # leaves the value unresolved; where only this service's own environment does,
                # overriding what the value names, it is rowed here, where its base writes it
                # (review B24-3).
                if key in base_unresolved:
                    continue
                res.unresolved.append({
                    "extractor": self.name,
                    "kind": "compose-env-unresolved", "component": repo.alias, "key": key,
                    "source_file": miss["file"], "line": miss["line"],
                    "why": (f"`{miss['raw']}`, which `{name}` takes from "
                            f"`{bases[brought[key]][1]}` through `extends:`, names "
                            f"{', '.join(miss['missing'])}, which `{name}`'s own environment "
                            f"leaves without a value and which carries no default")})
                continue
            res.unresolved.append({
                "extractor": self.name,
                "kind": "compose-env-unresolved", "component": repo.alias, "key": key,
                "source_file": rel, "line": miss["line"],
                "why": (f"`{miss['raw']}` names {', '.join(miss['missing'])}, which nothing "
                        f"in this file sets and which carries no default")})


def brought_by(bases: tuple, known: set[str]) -> list[dict]:
    """What an `extends:` chain brings (decision 57; design pass 24, section 4): each base's
    `environment:`, nearest first, where neither the service - `known`, its own keys, relaxed -
    nor a nearer base writes the key, as compose merges a base's `environment:` under the
    service's own. Each entry is cited where its base writes it (`file`), with the index of the
    base that brings it (`base`)."""
    seen = set(known)
    out: list[dict] = []
    for index, (base_rel, _name, base_node) in enumerate(bases):
        for e in environment_entries(_yaml.mapping_get(base_node, "environment")):
            relaxed = ids.relaxed_key(e["key"])
            if relaxed not in seen:
                seen.add(relaxed)
                out.append({**e, "file": base_rel, "base": index})
    return out


def base_declarations(bases: tuple, index: int) -> list[dict]:
    """The declarations base `index` of a chain runs with on its own - its own `environment:`, then
    what the chain beyond it brings - which its own declaration's `_service` resolves: whether a
    value it brings is unresolved there decides which declaration rows it (review B24-3)."""
    base_rel, _name, node = bases[index]
    own = [{"key": e["key"], "value": e["value"], "file": base_rel, "line": e["line"]}
           for e in environment_entries(_yaml.mapping_get(node, "environment"))]
    rest = brought_by(bases[index + 1:], {ids.relaxed_key(d["key"]) for d in own})
    return own + [{"key": e["key"], "value": e["value"], "file": e["file"], "line": e["line"]}
                  for e in rest]


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


def canonical_claim(config: dict, service: str, alias: str = "",
                    files: list[str] | tuple[str, ...] = ()) -> dict | None:
    """The workspace's decision about a contested service, or None if it made none.

    Several blocks may name one service (T623, decision 41): the block deciding this
    component's `files` declaring it on one node is the first that designates one of them -
    `_canonical.deciding_block`, the rule `mark-canonical` decides the node by, over the files
    this component reads: it ranks the component's own files' values, its winner the
    component's candidate, which `_canonical.env_winner` ranks across components over every
    compose package claiming the service (batch 24, design pass 24, section 2). Where none
    does, or no files are given, the first block naming the service, as on `main`: the files
    are then contested, and not the owner's.

    Read from `ctx.config`, which the pipeline already populates from the workspace's
    `[[canonical]]` blocks — so the decision reaches the extract layer through a seam the
    SPI already had, and no core change is needed to consult it.

    Matched on the NORMALISED name, as `mark-canonical` matches it (D534-24): a service
    written `my api` is `svc:my-api@...`, and a block the proposal drafts as `my-api` must
    decide the environment as well as the edges.
    """
    wanted = ids.norm_id(service)
    blocks = [claim for claim in config.get("canonical") or []
              if isinstance(claim, dict) and ids.norm_id(str(claim.get("service", ""))) == wanted]
    if files:
        decided, _ = _canonical.deciding_block(
            blocks, [(alias, {"kind": CONFIG_KIND, "path": rel}, rel) for rel in files])
        if decided is not None:
            return decided
    return blocks[0] if blocks else None


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
