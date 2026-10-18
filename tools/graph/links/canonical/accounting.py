"""What the graph READ of a component, and how an accounting row says it (batch 15b, T573).

`canonical-unmatched` and `apart-unmatched` are judgments of ABSENCE - "the graph read no
deployment of `web` in `a`" - so each is made only where it can be true:

* **the read gate.** In a run that saw the whole workspace through its deployment readers
  (not `narrowed`), where a deployment reader was handed one of the component's files or
  missed one by case (D15b-43, D15b-122, D15b-132), and where every node the judgment
  evaluates carries its reader's facts - a claim-bearing deployment node its `kind`, a
  helm one and every host-map file its `ships_in` (D15b-23, D15b-54). An in-process caller
  that hands no run scope judges nothing.
* **what was read.** The component's claims; its deployment files the graph could not read
  (its `unread` nodes, named first, most specific remedy first, D15b-47, D15b-102); the
  claim files it was handed and read that declare no deployment - a library chart, a chart
  with no templates of its own, a compose file with no `services:` (D15b-122) - each neither
  unread nor claim-bearing, and never a chart's `Chart.yaml` beside a values file the graph
  has a node for, read, unread or lost to its case: that values file speaks for its chart,
  as the helm reader reads the pair (D15b-136). Nothing else speaks for a file beside it -
  one directory holds several compose packages (T588; review B15b-1); its host-map
  files; and the directories its walk could enter but not list (`config["unlisted"]`, T645):
  a block whose `path` lies in one is unknown, not absent - never accused - and they are named
  first, as its unread files are, wherever `prefix` opens a sentence and in each of
  `none_read`'s branches (the branch that names a block's own unread file, `named_unread`,
  names that file alone).

Both rows build their sentences here and are appended by their own passes, at their own
emission sites, through `model.unresolved_row` (D15b-97). Every "none read" spelling ends
naming what the graph does not read (D15b-106). The tool vocabulary in the sentences is
the rows' own; which file is a chart's metadata is `_canonical`'s to answer.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from tools.graph.core.lib.rows import capped
from tools.graph.core.model import Edge, Node
from tools.graph.extractors import _canonical

CLAIM_TYPE = "DG_DEPLOYS"

#: What the graph does not read, said at the end of every "none read" spelling (D15b-106):
#: compose's limits and helm's alike, since either may hold the deployment. Since T620 an
#: `include:` or `extends:` is followed into the compose files a component reads, and one
#: that cannot be followed is its own row.
NOT_READ = ("(the graph does not read a compose file under another name - an `include:` or "
            "`extends:` naming one is its own row - nor a chart's values overlays)")

#: The unread kinds whose remedy is to fix the file.
_BROKEN = ("unreadable-file", "compose-unparsed", "unparsable")
#: A file read whole that claims nothing because of its chart (T624): not a file that could
#: not be read, so never in that ending.
_REFUSED = "chart-refused"
#: The unread kinds a file lost to its case carries - T531's row, or the deployment readers'.
_CASE = ("off-case-input", "deployment-off-case")
#: How a host-map clause is read: naming a root promotes its host maps (D15b-69 iv).
_HOST_MAPS_KEPT = "(a path under a root makes that root's host maps the ones the ranking keeps)"


@dataclass
class Reading:
    """What the graph read of one component, for the accounting rows."""

    claims: list[Edge] = field(default_factory=list)
    unread: list[Node] = field(default_factory=list)
    nothing: list[str] = field(default_factory=list)
    host_maps: list[Node] = field(default_factory=list)
    #: The files its compose files' `include:` entries name that compose does not read (batch
    #: 23, D23-131) - each a file the graph could not read, named first, in `prefix`'s place.
    include_unread: list[str] = field(default_factory=list)
    #: The directories the component's walk could enter but not list (`config["unlisted"]`):
    #: what they hold is unknown, not absent, so every judgment of absence names them first
    #: (T645, batch 27).
    unlisted: list[str] = field(default_factory=list)


def _component_of(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    node = nodes_by_id.get(edge.src)
    return str(node.props.get("component", "")) if node is not None else ""


def _chart_only(node: Node) -> bool:
    """An `unnamed-deployment` node whose file is a chart's `Chart.yaml`: ruling 20's
    chart with no values file, not a values file naming no service."""
    return (node.props.get("unread") == "unnamed-deployment"
            and _canonical.names_chart_metadata(str(node.props.get("path", ""))))


def _specificity(node: Node) -> tuple[int, str]:
    """Most specific remedy first (D15b-102): rename, then add a values file, then name a
    service, then fix the file."""
    kind = node.props.get("unread")
    rank = (4 if node.props.get("claims_nothing") else 0 if kind in _CASE
            else 1 if _chart_only(node) else 2 if kind == "unnamed-deployment" else 3)
    return rank, str(node.props.get("path", ""))


def read_gate(config: dict, component: str, nodes: list[Node], edges: list[Edge]) -> bool:
    """Whether the accounting rows may judge a block naming `component` (see the module)."""
    if config.get("narrowed") is not False:
        return False
    if not ((config.get("deployment_inputs") or {}).get(component)
            or (config.get("deployment_off_case") or {}).get(component)):
        return False
    bearing = {e.src for e in edges if e.type == CLAIM_TYPE}
    for node in nodes:
        if node.props.get("component") != component:
            continue
        if node.type == "deploy" and node.id in bearing:
            kind = node.props.get("kind")
            if not kind or (kind == "helm" and not _roots(node)):
                return False
        if node.type == "cfg" and node.props.get("kind") == "configmap" and not _roots(node):
            return False
    return True


def _roots(node: Node) -> list[str]:
    roots = node.props.get("ships_in")
    return roots if isinstance(roots, list) and roots else []


def reading(config: dict, component: str, nodes: list[Node], edges: list[Edge],
            nodes_by_id: dict[str, Node]) -> Reading:
    out = Reading()
    out.claims = [e for e in edges if e.type == CLAIM_TYPE
                  and _component_of(e, nodes_by_id) == component]
    bearing = {e.src for e in out.claims}
    mine = [n for n in nodes if n.type == "deploy" and n.props.get("component") == component]
    out.unread = sorted({n.id: n for n in mine if n.props.get("unread")}.values(),
                        key=_specificity)
    spoken = {str(n.props.get("path", "")) for n in mine
              if n.props.get("unread") or n.id in bearing}
    charted = {posixpath.dirname(path) for path in spoken if _canonical.names_values_file(path)}
    handed = (config.get("deployment_claims") or {}).get(component) or []
    out.nothing = sorted(rel for rel in handed
                         if rel not in spoken and not (_canonical.names_chart_metadata(rel)
                                                       and posixpath.dirname(rel) in charted))
    out.host_maps = sorted({n.id: n for n in nodes
                            if n.type == "cfg" and n.props.get("component") == component
                            and n.props.get("kind") == "configmap"}.values(),
                           key=lambda n: n.id)
    out.include_unread = sorted({str(entry.get("file", "")) for n in mine
                                 for entry in _entries(n.props.get("include_unread"))})
    out.unlisted = sorted(str(d) for d in (config.get("unlisted") or {}).get(component) or [])
    return out


def unlisted_spelling(directory: str) -> str:
    """One directory the walk could not list, as every judgment of absence names it (T645)."""
    return (f"`{directory}` could not be listed, so what it declares is unknown - allow "
            f"listing it, then rebuild")


def unlisted_holding(r: Reading, path: str) -> str:
    """The unlisted directory `path` lies in, or "" - a component-relative path, and the
    component's own root (`.`) holding every path (T645)."""
    clean = path.strip("/")
    for directory in r.unlisted:
        d = directory.strip("/")
        if d in ("", ".") or clean == d or clean.startswith(d + "/"):
            return directory
    return ""


def _listing(r: Reading) -> str:
    """The unlisted directories, said first, for a sentence `prefix` does not open."""
    return ("; ".join(unlisted_spelling(d) for d in r.unlisted) + ", then: "
            if r.unlisted else "")


def _entries(value: object) -> list[dict]:
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


@dataclass(frozen=True)
class Unread:
    """A file or directory compose did not read - its component, its path, why, and where it
    is cited (batch 23): an unread file or directory at itself, line 0, as `main`'s rows cite
    one; a file an `include:` names at that entry's file and line (D23-156, D23-163)."""

    component: str
    path: str
    why: str
    file: str
    line: int


@dataclass(frozen=True)
class Unreached:
    """A service whose own `extends:` chain names a base the graph cannot reach (batch 23,
    D23-112): its claim's file and line, the base as written and `reach`'s reason."""

    component: str
    service: str
    file: str
    line: int
    extends: str
    reason: str


#: Why each `Unread` was not read, as the image link's rows say it (design pass 23, section 3).
NOT_PARSED, OFF_CASE, NOT_LISTED, INCLUDED = (
    "could not be read or parsed", "named off its case", "could not be listed",
    "named by an `include:` it does not read")


def compose_unread(config: dict, nodes: list[Node],
                   edges: list[Edge]) -> tuple[list[Unread], list[Unreached]]:
    """What COMPOSE did not read in this run (batch 23, design pass 23, section 3), keyed on
    the facts, never on one row kind: a compose node marked `unread`; a compose file lost to
    its case (compose's own names over each component's case record, D23-31); a directory the
    walk could not list (`config["unlisted"]`, D23-32); a file a compose file's `include:` names
    that compose does not read (`include_unread`, D23-131); and each service whose own chain
    names a base the graph cannot reach (`extends_unread` beside its own `unfollowed` - an
    override taking its base's is not named again, D23-134). Ordered by component, then path,
    then service. A chart the run did not read is none of these: it builds nothing (D23-56)."""
    unread: list[Unread] = []
    for node in nodes:
        if node.type != "deploy" or node.props.get("kind") != "compose":
            continue
        component, path = str(node.props.get("component", "")), str(node.props.get("path", ""))
        if node.props.get("unread"):
            unread.append(Unread(component, path, NOT_PARSED, path, 0))
        for entry in _entries(node.props.get("include_unread")):
            unread.append(Unread(component, str(entry.get("file", "")), INCLUDED, path,
                                 int(entry.get("line") or 0)))
    for component, lost in sorted((config.get("deployment_off_case") or {}).items()):
        unread += [Unread(component, rel, OFF_CASE, rel, 0) for rel in lost
                   if _canonical.names_compose_file(rel, folded=True)]
    for component, directories in sorted((config.get("unlisted") or {}).items()):
        unread += [Unread(component, d, NOT_LISTED, d, 0) for d in directories]
    nodes_by_id = {n.id: n for n in nodes}
    unreached = [Unreached(_component_of(e, nodes_by_id), str(e.props.get("service", "")),
                           e.source_file, e.line, str(e.props.get("extends", "")),
                           str(e.props["extends_unread"]))
                 for e in edges if e.type == CLAIM_TYPE and e.props.get("extends_unread")
                 and e.props.get("unfollowed")]
    return (sorted(set(unread), key=lambda u: (u.component, u.path, u.file, u.line)),
            sorted(set(unreached), key=lambda u: (u.component, u.file, u.service, u.line)))


def complete(config: dict, nodes: list[Node], edges: list[Edge]) -> bool:
    """Whether this run may judge an image (batch 23, design pass 23, section 3): not narrowed
    (`config["narrowed"]`, which every narrowing reason sets; an in-process caller handing no
    run scope judges nothing), and holding nothing compose did not read (`compose_unread`) and
    no claim whose chain - its own or the base's an override inherits - names a base the graph
    cannot reach. The ONE reading of COMPLETE, called by `join-by-name`'s declaration pass and
    image rule and `mark-canonical`'s `apart-held` alike (D23-133, D23-135)."""
    if config.get("narrowed") is not False:
        return False
    unread, _ = compose_unread(config, nodes, edges)
    return not unread and not any(e.type == CLAIM_TYPE and e.props.get("extends_unread")
                                  for e in edges)


def capped_ticked(names: list[str]) -> str:
    """Names in backticks, the first five, "and <n> more" past them (`capped`)."""
    return capped([f"`{name}`" for name in names])


def _spelled(directory: str) -> str:
    """A directory of a component as a block's `path` spells it: `.` for the component's
    own root, which `claimed_path` reads as the root - never an empty name (review B15b-3)."""
    return directory or "."


def unread_spelling(node: Node) -> str:
    """One unread deployment file, with its reason and its remedy, in the spelling its kind
    needs (D15b-47) - never a fixed "fix it first", false for a chart that was read but names
    nothing and for a file lost to its case (D15b-102)."""
    path, kind = str(node.props.get("path", "")), node.props.get("unread")
    if node.props.get("claims_nothing"):
        # T625: read or not, it claims nothing - no remedy here would change that.
        return f"`{path}` would claim nothing even read: {node.props['claims_nothing']}"
    if kind == _REFUSED:
        return (f"`{path}` is in a chart helm refuses to load, so it deploys nothing until "
                f"the chart loads; fix what its `chart-refused` row names")
    if kind in _CASE:
        return (f"`{path}` is not read because of its case (see its row), so what it declares "
                f"is unknown; rename it as the patterns spell it")
    if _chart_only(node):
        return (f"`{_spelled(posixpath.dirname(path))}` has no `values.yaml`, so nothing the "
                f"graph reads names the service its templates deploy; add one "
                f"(`fullnameOverride: <name>`)")
    if kind == "unnamed-deployment":
        return f"`{path}` names no service, so what it deploys is unknown; name one in it"
    return (f"`{path}` could not be read (see its row), so what it declares is unknown; "
            f"fix it")


def named_unread(path: str, owner: str, node: Node) -> str:
    """A block whose `path` names one of its owner's unread files, said directly."""
    kind = node.props.get("unread")
    if node.props.get("claims_nothing"):
        return (f"`{path}` names `{owner}`'s file that would claim nothing even read: "
                f"{node.props['claims_nothing']}")
    if kind == _REFUSED:
        return (f"`{path}` names `{owner}`'s file in a chart helm refuses to load, so it "
                f"deploys nothing until the chart loads; fix what its `chart-refused` row "
                f"names")
    if kind in _CASE:
        return (f"`{path}` names `{owner}`'s file that is not read because of its case (see "
                f"its row); rename it as the patterns spell it")
    if _chart_only(node):
        return (f"`{path}` names `{owner}`'s chart that has no `values.yaml`; add one "
                f"(`fullnameOverride: <name>`)")
    if kind == "unnamed-deployment":
        return f"`{path}` names `{owner}`'s file that names no service; name one in it"
    return (f"`{path}` names `{owner}`'s file that could not be read (see its row), so what "
            f"it declares is unknown; fix it")


def read_whole(node: Node) -> bool:
    """Whether an unread deployment node is a file that claims nothing for a reason of its
    chart's - read whole in a chart helm refuses (T624), or one its reader says would claim
    nothing even read (T625) - rather than one the graph could not read."""
    return node.props.get("unread") == _REFUSED or bool(node.props.get("claims_nothing"))


#: The way on for a file a compose file's `include:` names that compose does not read (batch
#: 23, D23-147: `compose-include-unresolved` states none).
INCLUDE_REMEDY = "make the `include:` name a compose file this component's reader reads"


def include_spelling(path: str) -> str:
    """A file an `include:` names that compose does not read, with its remedy (D23-155)."""
    return (f"`{path}` is named by an `include:` the graph does not read, so what it declares "
            f"is unknown; {INCLUDE_REMEDY}")


def prefix(r: Reading, *, joining: bool = False) -> str:
    """The directories the component's walk could not list (T645), then its unread files,
    named before anything else (D15b-47) - and, for a block that joins (batch 23), the files
    its `include:` entries name that compose does not read, in the same place (D23-155,
    D23-159)."""
    # T625: a file that would claim nothing even read is still named - nothing is dropped -
    # but has nothing to fix first, so it is said on its own, before the files that do.
    inert = [n for n in r.unread if n.props.get("claims_nothing")]
    # A directory nobody could list first (T645): it may hold the deployment itself.
    fix = [unlisted_spelling(d) for d in r.unlisted]
    fix += [unread_spelling(n) for n in r.unread if not n.props.get("claims_nothing")]
    if joining:
        fix += [include_spelling(path) for path in r.include_unread]
    said = "".join(unread_spelling(n) + ". " for n in inert)
    if fix:
        said += "; ".join(fix) + ", then: "
    return said


def _host_roots(r: Reading) -> list[str]:
    """The chart roots the component's host maps ship in, each as a path a block can name
    to rank that root's host maps. A root chart alone is `.`; beside another root it is one
    of its own host-map files, since `.` designates every package of the owner and ranks
    none above another, where a file of the root chart designates that chart by the chart
    rule (review B15b-14)."""
    files: dict[str, list[str]] = {}
    for n in r.host_maps:
        if _roots(n):
            files.setdefault(_roots(n)[0], []).append(str(n.props.get("path", "")))
    if set(files) == {""}:
        return ["."]
    return sorted(root or min(paths) for root, paths in files.items())


def host_clause(r: Reading) -> str:
    return (f"its host maps are designated by a path under: "
            f"{', '.join(f'`{root}`' for root in _host_roots(r))} {_HOST_MAPS_KEPT}")


def _files(paths: list[str]) -> str:
    return ", ".join(f"`{p}`" for p in paths)


#: The three blocks an accounting row words its absence for (batch 23: `refusing`, a mode).
DESIGNATING, REFUSING, JOINING = "designating", "refusing", "joining"
#: A joining block's own way on, which REPLACES each branch's trailing one (D23-108).
_JOINING_WAY = "Correct the block's `service` or `component`, or remove it"


def none_read(r: Reading, component: str, head: str, *, mode: str) -> str:
    """Row (a)'s sentence after its head, by what the graph DID read (D15b-52, D15b-58,
    D15b-122): the other deployments it read; every deployment file unread; files read that
    declare no deployment; or no deployment file at all. `mode` words it for the block:
    `designating` (`canonical-unmatched`), `refusing` (`apart-unmatched` - no host-map clause)
    or, batch 23, `joining` (`deploys-unmatched`): the claims named by their own service, the
    files its `include:` names that compose does not read named first in every branch, no
    host-map clause, and its own way on in each branch's place (D23-89, D23-98, D23-107,
    D23-108, D23-155). In every branch the directories the component's walk could not list
    are named first (T645) - by `prefix` where it opens the sentence, else by `_listing`."""
    if mode == JOINING:
        return _joining(r, component, head)
    refusing = mode == REFUSING
    can = "can refuse" if refusing else "designates"
    if r.claims:
        if refusing:
            named = sorted({str(e.props.get("service", "")) for e in r.claims})
        else:
            named = sorted({e.dst.split(":", 1)[-1].split("@", 1)[0] for e in r.claims})
        tail = f" It read: {_files(named)}; name one of them, or remove the block"
        if r.host_maps and not refusing:
            tail += f"; {host_clause(r)}"
        why = prefix(r) + head + tail
    elif r.unread and not r.nothing:
        # Read whole and deploying nothing for its chart (T624) is not "could not be read".
        said = ("could be read" if not any(read_whole(n) for n in r.unread)
                else "declares a service the graph read")
        why = (_listing(r) + head + f" None of `{component}`'s deployment files {said}: "
               + "; ".join(unread_spelling(n) for n in r.unread) + ", or remove the block")
    elif r.nothing:
        tail = (f" The graph read {_files(r.nothing)} of `{component}`, and none declares a "
                f"deployment (a library chart, a chart with no templates of its own, or a "
                f"compose file with no `services:`)")
        if refusing:
            tail += ", so this block can refuse nothing it read; remove the block"
        else:
            tail += (f"; remove the block, or name a service `{component}` deploys in a file "
                     f"the graph reads")
            if r.host_maps:
                tail += f"; {host_clause(r)}"
        why = prefix(r) + head + tail
    elif r.host_maps and not refusing:
        why = (_listing(r) + head + f" The graph read no deployment file of `{component}`; its "
               f"host maps are designated by a path under: {_files(_host_roots(r))} - set "
               f"`path` to one of them, or remove the block {_HOST_MAPS_KEPT}")
    else:
        why = (_listing(r) + head + f" The graph read no deployment file of `{component}`, so "
               f"this block {can} nothing it read; remove the block")
    return f"{why}. {NOT_READ}"


def _joining(r: Reading, component: str, head: str) -> str:
    """`none_read`'s joining framing (batch 23): the files the graph could not read named first
    in every branch, `prefix`'s place - its unread deployment files and the files its `include:`
    entries name - then the branch, then the block's own way on in place of the branch's."""
    first = prefix(r, joining=True)
    if r.claims:
        named = sorted({str(e.props.get("service", "")) for e in r.claims})
        body = f" It read: {_files(named)}. {_JOINING_WAY}"
    elif r.nothing:
        body = (f" The graph read {_files(r.nothing)} of `{component}`, and none declares a "
                f"deployment (a library chart, a chart with no templates of its own, or a "
                f"compose file with no `services:`). {_JOINING_WAY}")
    elif r.unread or r.include_unread:
        # Read whole and deploying nothing for its chart (T624, T625) is not "could not be read":
        # the other two framings' own test (review B23-2).
        said = ("could be read" if not any(read_whole(n) for n in r.unread)
                else "declares a service the graph read")
        body = f" None of `{component}`'s deployment files {said}. {_JOINING_WAY}"
    else:
        body = (f" The graph read no deployment file of `{component}`, so this block can join "
                f"nothing it read. {_JOINING_WAY}")
    return f"{first}{head}{body}. {NOT_READ}"
