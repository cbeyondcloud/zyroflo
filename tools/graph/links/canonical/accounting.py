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
  one directory holds several compose packages (T588; review B15b-1); and its host-map
  files.

Both rows build their sentences here and are appended by their own passes, at their own
emission sites, through `model.unresolved_row` (D15b-97). Every "none read" spelling ends
naming what the graph does not read (D15b-106). The tool vocabulary in the sentences is
the rows' own; which file is a chart's metadata is `_canonical`'s to answer.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

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
    return out


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
        return (f"`{path}` is in a chart helm refuses to load (see its row), so it deploys "
                f"nothing until the chart loads; fix the chart's `Chart.yaml`")
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
        return (f"`{path}` names `{owner}`'s file in a chart helm refuses to load (see its "
                f"row), so it deploys nothing until the chart loads; fix the chart's "
                f"`Chart.yaml`")
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


def prefix(r: Reading) -> str:
    """The component's unread files, named before anything else (D15b-47)."""
    # T625: a file that would claim nothing even read is still named - nothing is dropped -
    # but has nothing to fix first, so it is said on its own, before the files that do.
    inert = [n for n in r.unread if n.props.get("claims_nothing")]
    fix = [n for n in r.unread if not n.props.get("claims_nothing")]
    said = "".join(unread_spelling(n) + ". " for n in inert)
    if fix:
        said += "; ".join(unread_spelling(n) for n in fix) + ", then: "
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


def none_read(r: Reading, component: str, head: str, *, refusing: bool) -> str:
    """Row (a)'s sentence after its head, by what the graph DID read (D15b-52, D15b-58,
    D15b-122): the other deployments it read; every deployment file unread; files read that
    declare no deployment; or no deployment file at all. `refusing` words it for
    `apart-unmatched` (a block refuses; there is no host-map clause)."""
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
        why = (head + f" None of `{component}`'s deployment files {said}: "
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
        why = (head + f" The graph read no deployment file of `{component}`; its host maps are "
               f"designated by a path under: {_files(_host_roots(r))} - set `path` to one of "
               f"them, or remove the block {_HOST_MAPS_KEPT}")
    else:
        why = (head + f" The graph read no deployment file of `{component}`, so this block "
               f"{can} nothing it read; remove the block")
    return f"{why}. {NOT_READ}"
