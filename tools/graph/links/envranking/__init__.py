"""Env values across components: the rows saying which component's value a view keeps (batch 24,
T590; design pass 24, section 3).

Within one component compose ranks a view's values as it always has, and rows what it drops there.
Across components the view holds ONE decision's winner - `_canonical.env_winner`, which compose
writes the view by: the workspace's block over the compose packages claiming the service, else the
last component by NAME, a group holding two services decided by no block. Compose cannot row the
losers: it writes a component's rows before the later components are read. So it publishes the
facts, and this pass rows them from the graph - which is why no shard holds a row quoting another
component's value (D534-57).

## What it reads

compose's edges alone, by the reader that publishes them (D15b-54): each `DG_CONFIGURES` edge's
`view`, `resolved`, `readings`, `package` and `wins` - true on the declaration that is its
component's own winner for its (view, key), so that winner is read, never computed a second time
(D24-67) - and each `DG_DEPLOYS` claim edge's `view`, the decision set being every compose package
claiming the service in that view, one setting no key included (D24-44, D24-58).

## What it says, and when

One `superseded-declaration` row per losing COMPONENT whose value differs from the winner's
(D534-53, D24-17, D24-69), at that component's winning file and line, in one of four forms - (1) a
block selected another package, (2) no block selected one, (3) the selected package sets no value,
(4) the group holds two services - each naming the view and whether a component's code reads it,
and what compose did not read that could set the key too (D24-5, D24-70, D24-90).

In a WHOLE run alone (`config["narrowed"]` false): a narrowed run's row would state the
workspace's answer over facts the narrowing left out or stale, so a narrowed run judges none, and
the run's line says so (`NARROWED_TAIL`, D24-78 reversed). A compose edge with no `view` comes from
a shard published before this batch: no row is judged on what it could change, and one row names
the components whose shards predate it, with the re-publish (D24-19).
"""
from __future__ import annotations

from typing import Any

from tools.graph.core.lib import ids
from tools.graph.core.model import Edge, ExtractResult, Node, unresolved_row
from tools.graph.extractors import _canonical, _yaml
from tools.graph.links import canonical
from tools.graph.links.canonical import accounting

#: The reader whose facts this pass ranks, and the edges it reads of it.
READER = "compose-services"
CLAIM_TYPE, CONFIG_TYPE = "DG_DEPLOYS", "DG_CONFIGURES"

#: `main`'s two endings, chosen by whether the two values may be read alike (review B10-4).
STALE = "a stale deployment nobody has noticed is a finding, not noise"
SPELLED = ("the two are spelled differently and may be one value or two, depending on the tool "
           "that reads them")

#: What each `kind` this pass emits promises beyond `UNRESOLVED_COMMON` - `superseded-declaration`
#: as compose, `env_keys` and helm declare it (D24-134).
UNRESOLVED_FIELDS = {
    "superseded-declaration": ("key", "value"),
    "env-ranking-unjudged": ("components",),
}


def _ending(kept: dict, dropped: dict) -> str:
    """`main`'s ending, by `maybe_same` over the two DIFFERENT values' readings (D24-17)."""
    same = _yaml.may_agree(kept["value"], tuple(kept["readings"]), dropped["value"],
                           tuple(dropped["readings"]))
    return SPELLED if same else STALE


def _cite(block: dict) -> str:
    """A block where it is written, as the contested row cites one."""
    return (f"`{block.get('file')}:{block.get('line')}`" if block.get("file")
            else "the workspace config")


def _unless(config: dict, nodes: list[Node]) -> str:
    """What compose did not read that could set the key too, said on every row (D24-5, D24-36,
    D24-70, D24-90): a compose file marked `unread`, a file lost to its case that compose's globs
    miss only by case (its node's `readers`), a file an `include:` names that compose does not
    read (batch 23, D23-131) - and a directory the walk could not list (`config["unlisted"]`),
    where a compose file could sit unread."""
    files: set[str] = set()
    for node in nodes:
        if node.type != "deploy":
            continue
        component, path = str(node.props.get("component", "")), str(node.props.get("path", ""))
        compose = node.props.get("kind") == "compose"
        if node.props.get("unread") and (compose or READER in (node.props.get("readers") or ())):
            files.add(f"{component}/{path}")
        if compose:
            for entry in node.props.get("include_unread") or ():
                if isinstance(entry, dict) and entry.get("file"):
                    files.add(f"{component}/{entry['file']}")
    dirs = sorted(f"{component}/{directory}"
                  for component, listed in (config.get("unlisted") or {}).items()
                  for directory in listed)
    said = []
    if files:
        one = len(files) == 1
        said.append(f"{accounting.capped_ticked(sorted(files))}, which "
                    f"{'was' if one else 'were'} not read, set{'s' if one else ''} it too")
    if dirs:
        said.append(f"a compose file in {accounting.capped_ticked(dirs)}, which could not be "
                    f"listed, sets it too")
    return f", unless {', or '.join(said)}" if said else ""


class EnvRankingPass:
    """The env ranking's rows across components (T590, design pass 24, section 3)."""

    name = "env-ranking"
    #: The decision is `mark-canonical`'s, over compose's packages: its rows read after it.
    REQUIRES = ("mark-canonical",)

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        config = ctx.config or {}
        nodes_by_id = {n.id: n for n in nodes}
        mine = [e for e in edges if e.extractor == READER and e.type in (CLAIM_TYPE, CONFIG_TYPE)]
        # D24-19: an edge with no `view` comes from a shard published before this batch - the
        # keys it sets and the services it claims are judged by no row, and one row says why.
        old = [e for e in mine if "view" not in e.props]
        old_keys = {ids.relaxed_key(str(e.props.get("declared_as", "")))
                    for e in old if e.type == CONFIG_TYPE}
        old_services = {ids.norm_id(str(e.props.get("service", ""))) for e in old}
        if old:
            components = sorted({canonical.component_of(e, nodes_by_id) for e in old} - {""})
            one = len(components) == 1
            res.unresolved.append(unresolved_row(
                self.name, "", "env-ranking-unjudged", "", 0,
                f"{accounting.capped_ticked(components)} {'was' if one else 'were'} read from "
                f"{'a shard' if one else 'shards'} published before compose recorded the view "
                f"each declaration is filed under, so no cross-component `superseded-declaration` "
                f"row is judged on the keys {'it sets' if one else 'they set'} or the services "
                f"{'it claims' if one else 'they claim'}; re-publish "
                f"{'its shard' if one else 'their shards'} to have them judged",
                components=components))
        if config.get("narrowed") is not False:
            return res
        layout = canonical.workspace_layout(config)

        # The decision set, per (view, service): every compose package claiming the service.
        claims: dict[str, dict[str, dict[str, list[_canonical.PlainClaim]]]] = {}
        for edge in mine:
            if edge.type != CLAIM_TYPE or "view" not in edge.props:
                continue
            node = nodes_by_id.get(edge.src)
            facts = {**(dict(node.props) if node is not None else {}),
                     "package": canonical.declared_package(edge, nodes_by_id)}
            claims.setdefault(str(edge.props["view"]), {}).setdefault(
                ids.norm_id(str(edge.props.get("service", ""))), {}).setdefault(
                canonical.package_of(edge, nodes_by_id, layout), []).append(
                (canonical.component_of(edge, nodes_by_id), facts, edge.source_file, edge.line))

        # Each component's candidate, per (view, key): the declaration compose marks `wins` -
        # resolved, since an unresolved value was never a candidate (D24-1). Compose marks one
        # declaration per (view, key, component), `_merge`'s answer, so the candidate is the
        # same every run (D24-39).
        candidates: dict[tuple[str, str], dict[str, dict]] = {}
        for edge in sorted((e for e in mine if e.type == CONFIG_TYPE and "view" in e.props
                            and e.props.get("wins") and e.props.get("resolved")),
                           key=lambda e: (e.source_file, e.line)):
            component = canonical.component_of(edge, nodes_by_id)
            key = ids.relaxed_key(str(edge.props.get("declared_as", "")))
            # An inherited value is cited where its base writes it (decision 57, D24-79).
            file, line = edge.source_file, edge.line
            written = str(edge.props.get("inherited_from") or "")
            if written.rpartition(":")[2].isdigit():
                file, line = written.rpartition(":")[0], int(written.rpartition(":")[2])
            candidates.setdefault((str(edge.props["view"]), key), {}).setdefault(component, {
                "value": str(edge.props.get("value", "")), "file": file,
                "line": line, "service": str(edge.props.get("service", "")),
                "readings": list(edge.props.get("readings") or ()),
                "package": str(edge.props.get("package", "")), "component": component})

        unless = _unless(config, nodes)
        for (view, key), cands in sorted(candidates.items()):
            if len(cands) < 2 or key in old_keys or any(
                    ids.norm_id(c["service"]) in old_services for c in cands.values()):
                continue
            decision = _canonical.env_winner(cands, claims.get(view, {}), config)
            kept = cands[decision.winner]
            reader = (f"the view `{view}`'s code reads" if view in layout
                      else "a view no component's code reads")
            for component, lost in sorted(cands.items()):
                if component == decision.winner or lost["value"] == kept["value"]:
                    continue
                said = self._why(decision, key, view, reader, kept, lost, unless)
                res.unresolved.append(unresolved_row(
                    self.name, component, "superseded-declaration", lost["file"], lost["line"],
                    said + "; this one is reported because " + _ending(kept, lost),
                    key=key, value=lost["value"]))
        return res

    @staticmethod
    def _why(decision: _canonical.EnvDecision, key: str, view: str, reader: str, kept: dict,
             lost: dict, unless: str) -> str:
        """Why `lost` is not the value the view keeps, in the decision's form (D24-21, D24-99)."""
        winner, service = decision.winner, lost["service"]
        filed = f"both are filed under `{view}`, {reader}"
        choices = " | ".join(canonical.choice_text(c) for _, c in sorted(decision.choices.items()))
        if decision.form == 1:
            # (1) the block selected another package (D24-45).
            return (f"`{key}` for `{service}` is also set here to `{lost['value']}`; {filed}; "
                    f"the workspace's block for `{service}` ({_cite(decision.block or {})}) "
                    f"selects `{decision.package}`, so `{winner}`'s value `{kept['value']}` "
                    f"(`{kept['file']}:{kept['line']}`) is what the graph keeps{unless}")
        if decision.form == 2:
            # (2) no block selected one (D534-55): the choices over the DECISION set (D24-30).
            head = (f"`{key}` for `{service}` is also set here to `{lost['value']}`, and in "
                    f"`{kept['package']}` to `{kept['value']}`, which the graph keeps - the last "
                    f"component by name, as no block selects one of these packages{unless}; "
                    f"{filed}; ")
            if decision.block is None:
                return (head + f"to choose, add a `[[canonical]]` block naming service "
                               f"`{service}` and one of: {choices}")
            return (head + f"the workspace's block for `{service}` ({_cite(decision.block)}) "
                           f"decides none of these packages; change it to one of: {choices}")
        if decision.form == 3:
            # (3) the selected package sets no value (D24-45, D24-62).
            return (f"`{key}` for `{service}` is set here to `{lost['value']}`; {filed}; the "
                    f"workspace's block for `{service}` ({_cite(decision.block or {})}) selects "
                    f"`{decision.package}`, which sets no `{key}`, so the graph keeps the last "
                    f"component's by name, `{winner}`'s `{kept['value']}`{unless} - to choose, "
                    f"set `{key}` in `{decision.package}`, or change the block to one of: "
                    f"{choices}")
        # (4) two services in one view: no block decides the group (D534-54, D24-46, D24-55).
        named = ""
        if decision.block is not None:
            named = (f"; the workspace's block for `{decision.block.get('service')}` "
                     f"({_cite(decision.block)}) decides nothing here")
        return (f"`{key}` is declared as `{lost['value']}` here for `{service}` and as "
                f"`{kept['value']}` in `{winner}`'s `{kept['file']}:{kept['line']}` for "
                f"`{kept['service']}`; {filed}, and the graph keeps `{winner}`'s, the last "
                f"component by name{unless} - a block ranks one service's packages, never two "
                f"services{named}; to keep one value, leave one declaration of `{key}` in this "
                f"view")


LINK_PASSES = (EnvRankingPass(),)
