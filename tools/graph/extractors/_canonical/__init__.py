"""Which file a workspace's `[[canonical]]` declaration covers.

Underscore-prefixed so the registry skips it, same as `_java/` and `_yaml/`. One rule, read
by every deployment reader, because two readers deciding "is this the owner's file" their own
way is two chances to decide it differently — and the answer changes which values the graph
believes.

## Why the extract layer consults this at all

The canonical link pass resolves the contested service *node*, and that is not enough. A
resolved-value view is built during **extraction**, before any link pass runs, so a value read
from a contested service comes from whichever file the reader happened to merge last. The
second fixture made that concrete: a compose file whose own header says it is superseded sorted
after the live one, won the merge, and a whole component was recorded as publishing to a topic
one version number off. Everything about the resulting graph looked correct.

`ExtractContext.config` already carries the `[[canonical]]` blocks, so consulting them here
needs no core change — the seam existed before anything used it.

## `path` covers what it NAMES, and a package is asked about as a package

Compose names the contested file directly, so an exact match is all it ever needed. Helm
cannot work that way: a chart tree declares its service in `values.yaml` while the host map it
publishes lives in `templates/*.yaml`, so a claim naming the values file has to reach the
chart's templates or it reaches nothing at all.

That reach used to live in `covers`, as "the file it names and everything under that file's
DIRECTORY" - which T529 found covering far more than a chart: a superseded deployment kept
in a subdirectory of the live one (`deploy/legacy/` beside `deploy/docker-compose.yml`) was
canonical too, and a directory claim (`charts/orderflow`) covered every chart beside it. So
the two questions are separate now. `covers` answers for a file: the path the claim names,
or anything under it when it names a directory. `covers_package` answers for a file of a
package - a chart - by the package roots the reader hands it: the claim names one of them or
a directory above it, or names a FILE of one of them (a chart's `values.yaml`, its overlay
`values-prod.yaml`, one of its templates). A package nested below another is its own
package, and a claim on one is not a claim on the other - unless the reader says the outer
one deploys it: a Helm subchart, vendored under its umbrella's `charts/`, ships in the
umbrella's release, so the reader lists the umbrella's root among the subchart's. Which
roots a file has, and which package a claimed file belongs to, is the reader's vocabulary;
this module only holds the rule. `claimed_path` is public for a reader that names a claim
in a row, as helm's `off-case-claim` does (T576).

## One reading per reader, for the link pass too (batch 15b)

A block acts through three readers: the link pass's decision among the packages claiming its
service, compose's env ranking and helm's host-map ranking. The link pass used to read every
block by `covers` alone, so a block naming a chart's template, its values overlay or its
`Chart.yaml` ranked the host map and decided no contest (D15b-10). `designates` and
`names_exactly` give the link pass each reader's own rule, dispatched on the facts the
reader published on its node - `kind` and `ships_in` - never on the config, so nothing is
baked into a shard. The chart rule (`chart_root`, `claimed_chart`) lives here, and the helm
reader imports it back: both layers apply one function.
"""
from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from tools.graph.core.lib import ids


def workspace_layout(config: dict) -> dict[str, str]:
    """Each component's workspace-relative POSIX path, by name, from `config["components"]`.

    Normalised as T575 normalises a claim's path - a separator, a leading `./`, a doubled or
    trailing `/` spell the same path - so `./platform` and `platform` are one place (D534-42).
    The ONE reading of the component list that placing a build context, keying a package and
    deciding which component reads a file (`build.activate`, T591) all share; two readings of
    one config are how two of them come to disagree while each looks right.
    """
    out: dict[str, str] = {}
    for component in (config or {}).get("components") or []:
        text = str(component.get("path") or ".").replace("\\", "/").strip()
        path = posixpath.normpath(text).strip("/") if text else "."
        out[str(component["name"])] = path or "."
    return out


def declared_children(name: str, layout: dict[str, str]) -> list[tuple[str, str]]:
    """(child name, its path relative to `name`'s) for every component declared strictly
    inside `name`, the longest path first so the most specific child matches first. The
    root component (`.`) holds every other; two components over one directory hold neither
    (T615's shape).

    With `child_holding`, the ONE declared-child rule (T616): which component reads a file
    (`build.activate`, T591) and whether a deployment node's declared `package` names a file
    of a component declared inside the declaring one (`links/canonical`, review B15a-11)
    both ask it - two implementations agreed on every shape measured, and an edit to one
    would have gone unnoticed by the other's tests."""
    mine = layout.get(name)
    if mine is None:
        return []
    out = []
    for other, path in layout.items():
        if other == name or path == mine:
            continue
        if mine == ".":
            out.append((other, path))
        elif path.startswith(mine + "/"):
            out.append((other, path[len(mine) + 1:]))
    return sorted(out, key=lambda child: (-child[1].count("/"), child[1], child[0]))


def child_holding(rel: str, children: list[tuple[str, str]]) -> str | None:
    """The most specific declared child holding the component-relative path `rel`, by whole
    path segments - `svc-a` holds `svc-a/x`, never `svc-a-legacy/x` (T530's trap)."""
    for child, path in children:
        if rel == path or rel.startswith(path + "/"):
            return child
    return None


def claims_for(config: dict, alias: str) -> list[dict]:
    """Every `[[canonical]]` block that names a path and applies to this component.

    A claim with no `owner` applies everywhere: `owner` narrows, it does not qualify. A claim
    with no `path` decides nothing about files and is skipped here — it still decides the
    service node, which is the link pass's business rather than this one's.
    """
    out = []
    for claim in config.get("canonical") or []:
        if not isinstance(claim, dict) or not str(claim.get("path", "")).strip():
            continue
        owner = str(claim.get("owner", ""))
        if not owner or owner == alias:
            out.append(claim)
    return out


def claimed_path(claim: dict, alias: str) -> str | None:
    """The claim's path in the graph's own spelling; None when the claim is another
    component's. A Windows separator, a leading `./` and a doubled or trailing `/` spell the
    same path, not another one that matches nothing (T575), and `.` names the component's
    root, as `/` does."""
    owner = str(claim.get("owner", ""))
    if owner and owner != alias:
        return None
    path = str(claim.get("path", "")).strip().replace("\\", "/")
    path = posixpath.normpath(path).strip("/") if path else ""
    return "" if path == "." else path


def covers(claim: dict, alias: str, rel: str) -> bool:
    """Whether this claim designates `rel` (a component-relative path) as the owner's: the
    path it names, or anything under it when it names a directory. Matched on whole path
    segments, so `charts/orders` is not a prefix of `charts/orders-legacy` (T530)."""
    path = claimed_path(claim, alias)
    if path is None:
        return False
    if not path:
        return True
    rel = rel.strip("/")
    return rel == path or rel.startswith(path + "/")


def covers_package(claim: dict, alias: str, rel: str, roots: Sequence[str],
                   root_of: Callable[[str], str | None]) -> bool:
    """Whether this claim designates `rel`, a file of a package. `roots` are the root
    directories of the packages `rel` ships in, its own first ("" is the component's root);
    `root_of(path)` is the root of the package a claimed path is a file of, None when it
    names no package's file. The claim designates `rel` when it covers `rel` itself, names
    one of `roots` or a directory above one, or names a file of one of them - a chart
    claimed by its `values.yaml`, whose host map lives in `templates/` below (T529)."""
    path = claimed_path(claim, alias)
    if path is None:
        return False
    if not path or covers(claim, alias, rel):
        return True
    if any(covers(claim, alias, root) for root in roots if root):
        return True
    return root_of(path) in roots


def owns_package(config: dict, alias: str, rel: str, roots: Sequence[str],
                 root_of: Callable[[str], str | None]) -> bool:
    """Whether `rel` is canonical: designated by a claim (see `covers_package`), or
    uncontested because none applies.

    "No claim names a path in this component" means the same as it always did — nothing is
    contested, so nothing is ranked and the reader's existing merge order stands. A workspace
    only fills in `[[canonical]]` for collisions the build actually reports.
    """
    claims = claims_for(config, alias)
    return not claims or any(covers_package(c, alias, rel, roots, root_of) for c in claims)


# ── the chart rule, shared by the helm reader and the link pass (batch 15b) ─────

#: A chart's own files in its root that a `[[canonical]]` claim may name it by: its values
#: files - the default one and any overlay beside it (`values-prod.yaml`) - and `Chart.yaml`.
CHART_FILE = re.compile(r"(?:values[^/]*|Chart)\.ya?ml")


def chart_root(rel: str) -> str:
    """The chart a template belongs to: the directory holding its last `templates/`
    segment ("" when that is the component's own root). T529: the canonical question is
    asked of the chart, never of the template file."""
    parts = rel.strip("/").split("/")
    for i in range(len(parts) - 2, -1, -1):       # directories only, the last one first
        if parts[i] == "templates":
            return "/".join(parts[:i])
    return posixpath.dirname(rel.strip("/"))


#: A chart's metadata file and its values file, by name: the helm reader's globs for them
#: ARE these (review B15b-15), so its matching, its directory listing and the accounting
#: rows read one fact.
CHART_FILES = ("Chart.yaml", "Chart.yml")
VALUES_FILES = ("values.yaml", "values.yml")


#: The compose reader's own file names - its globs ARE these, spelled once here so the link
#: passes read the deployment readers' case record by compose's rule without naming a reader
#: (batch 23): a compose file lost to its case hides what compose would have read.
COMPOSE_FILES = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")


def names_compose_file(path: str, *, folded: bool = False) -> bool:
    """Whether a path's base name is one compose reads - exactly, as the reader's globs match,
    or, `folded`, once case is ignored: a file another reader recorded as lost to its case is
    compose's where its folded name matches (batch 23, D23-31)."""
    name = posixpath.basename(path.strip("/"))
    if folded:
        name = name.casefold()
    return any(fnmatch.fnmatchcase(name, glob) for glob in COMPOSE_FILES)


def names_chart_metadata(path: str) -> bool:
    """Whether a path is a chart's `Chart.yaml` - its metadata, not a values file: what the
    accounting rows need to word ruling 20's chart with no values file (batch 15b)."""
    return posixpath.basename(path.strip("/")) in CHART_FILES


def names_values_file(path: str) -> bool:
    """Whether a path - or a bare file name - is a chart's values file IN ANY CASE, its name
    folded (D15b-126): the file a chart's `Chart.yaml` is metadata beside (ruling 20). One
    rule for the helm reader, deciding a chart directory holds one, and the accounting rows,
    deciding which node speaks for a `Chart.yaml` (batch 15b, review B15b-1)."""
    return posixpath.basename(path.strip("/")).casefold() in VALUES_FILES


def claimed_chart(path: str) -> str | None:
    """The chart a claimed path names by one of that chart's own files: a template (anything
    under its `templates/`), or a values file or `Chart.yaml` in its root. None for any
    other path: a directory above a chart is covered as a directory, and any other file
    names no chart at all - a claim naming a compose file beside a chart is not a claim on
    the chart."""
    parts = path.strip("/").split("/")
    if "templates" in parts[:-1]:
        return chart_root(path)
    if CHART_FILE.fullmatch(parts[-1]):
        return posixpath.dirname(path.strip("/"))
    return None


# ── how a block reaches a claim, by its reader's rule (batch 15b, T573) ─────────

#: Node kinds whose reader publishes `ships_in` - the chart roots a file ships in, its
#: own first - so the host-map rule applies to them.
_CHART_KINDS = ("helm", "configmap")


def _ships_in(node: Mapping[str, Any]) -> list[str] | None:
    """The chart roots a helm node's file ships in, where its reader published them - a
    non-empty list of strings - else None. An absent list is never passed on: round 5
    measured `None` raising inside `mark-canonical` and `[]` answering a false "designates
    nothing" (D15b-54)."""
    roots = node.get("ships_in")
    if (node.get("kind") in _CHART_KINDS and isinstance(roots, list) and roots
            and all(isinstance(r, str) for r in roots) and isinstance(node.get("path"), str)):
        return roots
    return None


def _own_file(node: Mapping[str, Any], source_file: str) -> str:
    """The file a claim came from, as its reader published it - or, for a node that
    carries none of its reader's facts, the claim's source file (`main`'s reading)."""
    if (node.get("kind") == "compose" and isinstance(node.get("path"), str)) \
            or _ships_in(node) is not None:
        return node["path"].strip("/")
    return source_file.strip("/")


def designates(block: dict, alias: str, node: Mapping[str, Any], source_file: str) -> bool:
    """Whether `block` reaches the file a claim (or a host map) came from, by the rule its
    reader ranks by: a compose file by `covers`, as compose's env ranking reads it; a chart's
    file by `covers_package` over the chart roots its reader published, as the host-map
    ranking reads it; any other node - one whose reader published neither fact - by `covers`
    on the claim's source file, `main`'s rule, so a shard published before batch 15b keeps
    `main`'s decisions."""
    if node.get("kind") == "compose" and isinstance(node.get("path"), str):
        return covers(block, alias, node["path"])
    roots = _ships_in(node)
    if roots is not None:
        return covers_package(block, alias, node["path"], roots, claimed_chart)
    return covers(block, alias, source_file)


def names_exactly(block: dict, alias: str, node: Mapping[str, Any], source_file: str) -> int:
    """How exactly `block` names this claim's own package: 2 where its path is the file's
    own, 1 where it names (by one of the chart's own files) the file's own chart -
    `ships_in[0]` - and 0 otherwise. Two ranks, so where a path designates several packages
    the most specific wins by an ORDERED test, never a disjunction (ruling 14, D15b-119). A
    path naming a chart's root DIRECTORY names neither: `claimed_chart` reads chart files,
    so an umbrella claimed by its own directory decides nothing, as on `main` (D15b-74)."""
    path = claimed_path(block, alias)
    if not path:
        return 0
    if path == _own_file(node, source_file):
        return 2
    roots = _ships_in(node)
    if roots is not None and claimed_chart(path) == roots[0]:
        return 1
    return 0


def block_designates(block: dict, alias: str, node: Mapping[str, Any],
                     source_file: str) -> bool:
    """Whether a block designates one claim, for which block decides its node (T623): by its
    `path`, through the reader's own rule (`designates`), or - with no path, or one naming
    its owner's root - by its `owner` being the claim's component."""
    if str(block.get("path") or "").strip() and not is_root_path(block):
        return designates(block, alias, node, source_file)
    owner = str(block.get("owner") or "").strip()
    return bool(owner) and owner == alias


def deciding_block(blocks: Sequence[dict], claims: Iterable[tuple[str, Mapping[str, Any], str]]
                   ) -> tuple[dict | None, list[dict]]:
    """(the block deciding a node, the later blocks designating one of its claims too): the
    first block, in file order, that designates one of the node's claims - each `(component,
    node props, source file)` - as every reader of the blocks takes the first; None where none
    does.

    Several blocks may name one service (T623, the owner's decision 41): two nodes can share
    a name, every block needs an `owner`, and an owner's block designates only that
    component's files, so one block could never decide both. This is the ONE rule
    `mark-canonical`, the name join and compose's env ranking take a node's block by - each
    over the claims it can see."""
    claims = list(claims)
    reaching = [b for b in blocks if any(block_designates(b, a, n, f) for a, n, f in claims)]
    return (reaching[0], reaching[1:]) if reaching else (None, [])


def is_root_path(block: dict) -> bool:
    """Whether a block's `path` reads as its owner's root - `.`, `./`, `/`, `//` - which
    designates every package of the owner: the owner branch decides it, and it is recorded
    `owner=` (on `main` `.` and `./` were recorded `path=.`, D15b-9, D15b-30). A block with no
    `path` is not one - it IS the owner branch."""
    if not str(block.get("path") or "").strip():
        return False
    return claimed_path(block, str(block.get("owner", ""))) == ""


def apart_pair(block: Mapping[str, Any], component: str, service: str) -> bool:
    """Whether an `[[apart]]` block names this pair: its component is the claim's DECLARING one,
    and its service, normalised (`norm_id`, which keeps case), is the claim's (D15b-5). The ONE
    reading of a block: the name join refuses the claims it names by it, and - since batch 24
    (T621, A') - compose and helm read it while extracting, for keying and the host map alone."""
    return (component == str(block.get("component", ""))
            and ids.norm_id(service) == ids.norm_id(str(block.get("service", ""))))


# ── batch 24 (T590): the one decision, over plain claims (design pass 24, section 2) ─────────

#: A claim as the decision reads it - plain data, so extraction and a link pass apply one rule:
#: its declaring component, its deployment node's published facts (`kind`, `path`, `package`),
#: its file and its line.
PlainClaim = tuple[str, Mapping[str, Any], str, int]


def declared_package(node: Mapping[str, Any]) -> str:
    """The file whose package a deployment node says it is part of, "" for none - only a relative
    path that does not climb out of its component (reviews B15a-4, B15a-11): `mark-canonical`'s
    reading, here so the plain decision reads it too."""
    package = node.get("package")
    if not isinstance(package, str) or not package:
        return ""
    normal = posixpath.normpath(package)
    return "" if normal == ".." or normal.startswith(("../", "/")) else normal


def in_a_declared_child(path: str, component: str, layout: Mapping[str, str]) -> bool:
    """Whether the workspace path `path` lies under a component declared inside `component` -
    whose file it then is by T591's rule, not `component`'s (review B15a-11), by whole path
    segments (`child_holding`, T616)."""
    mine = layout[component]
    if mine == ".":
        rel = path
    elif path.startswith(mine + "/"):
        rel = path[len(mine) + 1:]
    else:
        return False
    return child_holding(rel, declared_children(component, dict(layout))) is not None


def package_key(component: str, layout: Mapping[str, str], source_file: str, declared: str,
                fallback: str) -> str:
    """The package a claim belongs to, keyed by its WORKSPACE path: one file is one package unless
    its deployment node names the file whose package it is part of (T588), a file of its own
    component, never one inside a component declared in it - so two components'
    `docker-compose.yml` stay two packages (D24-31). With no layout for the component, the named
    package's deploy node, else `fallback` (its own). `mark-canonical`'s `package_of` and
    compose's env ranking both key by it."""
    if component not in layout:
        return ids.deployment_id(component, declared) if declared else fallback
    if declared:
        key = posixpath.normpath(posixpath.join(layout[component], declared))
        if not in_a_declared_child(key, component, layout):
            return key
    return posixpath.normpath(posixpath.join(layout[component], source_file))


def select_package_by_claims(packages: Mapping[str, Sequence[PlainClaim]],
                             decl: dict | None) -> tuple[str | None, str]:
    """Which package a block selects, and how - `mark-canonical`'s two steps over plain claims,
    `None` where it decides nothing: a `path` selects the one package it designates, of two or
    more the one whose own file it names, then whose own chart it names (ruling 14), else
    nothing; an `owner` selects the one package it reads, and one reading several selects none
    (D534-39). A path reading as the owner's root takes the owner branch (D15b-9, D15b-30).
    Held packages (ruling 19) are the links-level caller's to drop: no compose claim is held."""
    if decl is None:
        return None, ""
    path = (decl.get("path") or "").strip().strip("/")
    if path and not is_root_path(decl):
        def holds(key: str, test: Callable[[PlainClaim], bool]) -> bool:
            return any(test(claim) for claim in packages[key])

        designated = [key for key in packages
                      if holds(key, lambda c: designates(decl, c[0], c[1], c[2]))]
        if len(designated) <= 1:
            return (designated[0], f"path={path}") if designated else (None, "")
        for rank in (2, 1):
            exact = [key for key in designated
                     if holds(key, lambda c, r=rank: names_exactly(decl, c[0], c[1], c[2]) == r)]
            if exact:
                return (exact[0], f"path={path}") if len(exact) == 1 else (None, "")
        return None, ""
    owner = (decl.get("owner") or "").strip()
    if owner:
        matches = [key for key, claims in packages.items()
                   if any(claim[0] == owner for claim in claims)]
        if len(matches) == 1:
            return matches[0], f"owner={owner}"
    return None, ""


def choices_by_claims(packages: Mapping[str, Sequence[PlainClaim]]) -> dict[str, dict]:
    """For each package, the smallest `[[canonical]]` fields that select exactly it, by
    `select_package_by_claims`' own rule (D534-39, ruling 7): an owner reading no other package -
    the representative's first - else the representative's owner with its path. The
    representative is a package's base claim where it has one, else the first by component,
    file and line (D534-30, D534-42)."""
    reads: dict[str, set[str]] = {}
    for key, claims in packages.items():
        for claim in claims:
            reads.setdefault(claim[0], set()).add(key)
    out: dict[str, dict] = {}
    for key, claims in sorted(packages.items()):
        rep = min(claims, key=lambda c: (bool(declared_package(c[1])), c[0], c[2], c[3]))
        readers = sorted({c[0] for c in claims} - {""}, key=lambda c: (c != rep[0], c))
        owner = next((c for c in readers if reads.get(c) == {key}), "")
        out[key] = {"owner": owner} if owner else {"owner": rep[0], "path": rep[2]}
    return out


class EnvDecision(NamedTuple):
    """How a (view, key) is decided across components (design pass 24, section 2), and how each
    loser's row says so - its form: (1) a block selected another package; (2) no block selected
    one - `block` the one that decides none, if any; (3) the selected package sets no value; (4)
    a group holding two services, decided by no block - `block` one naming a service of it."""

    winner: str                    # the winning component
    form: int
    block: dict | None
    package: str                   # the package the block selects (forms 1 and 3)
    choices: dict[str, dict]       # the decision set's packages and their choices (2 and 3)
    services: tuple[str, ...]      # the services the candidates are for


def env_winner(candidates: Mapping[str, Mapping[str, Any]],
               claims: Mapping[str, Mapping[str, Sequence[PlainClaim]]],
               config: Mapping[str, Any]) -> EnvDecision:
    """The component whose candidate a view holds for one key - ONE function, applied by
    extraction and by `env-ranking` alike (design pass 24, section 2). `candidates` maps each
    component to its own winner for the key (`service` and `package` among its facts); `claims`
    maps each service, normalised, to the compose packages claiming it in the view - a package
    setting no key included (D24-32, D24-44) - each a list of `PlainClaim`s.

    By the workspace's one decision: ONE `deciding_block` call over the decision set, that block
    selecting a package by `select_package_by_claims`; the selected package's candidate wins, and
    where it sets no value the key is decided as with no block (D24-45). Otherwise, and for a
    group holding two services - which no block decides, a block ranking one service's packages
    (D24-46, D24-60, D24-67) - the last component by NAME, never the config's order, which no
    stamp covers (D24-38)."""
    last = max(candidates)
    services = tuple(sorted({ids.norm_id(str(c.get("service", ""))) for c in candidates.values()}))
    blocks = [b for b in config.get("canonical") or [] if isinstance(b, dict)]
    if len(services) > 1:
        named = next((b for b in blocks
                      if ids.norm_id(str(b.get("service", ""))) in services), None)
        return EnvDecision(last, 4, named, "", {}, services)
    (service,) = services
    packages = claims.get(service, {})
    mine = [b for b in blocks if ids.norm_id(str(b.get("service", ""))) == service]
    block, _ = deciding_block(mine, [(c[0], c[1], c[2]) for group in packages.values()
                                     for c in group])
    choices = choices_by_claims(packages)
    if block is None:
        return EnvDecision(last, 2, None, "", choices, services)
    selected, _how = select_package_by_claims(packages, block)
    if selected is None:
        return EnvDecision(last, 2, block, "", choices, services)
    owners = sorted(comp for comp, cand in candidates.items() if cand.get("package") == selected)
    if owners:
        return EnvDecision(owners[0], 1, block, selected, choices, services)
    return EnvDecision(last, 3, block, selected, choices, services)
