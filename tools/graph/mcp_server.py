"""The graph served over MCP: eight tools, the same facts the CLIs print.

Item 23. The serving tier the reference architecture names ("MCP tools > Python
CLIs > OKF bundle"): a stage agent mid-task asks the graph directly — blast radius,
context pack, a node's neighbors — instead of shelling out and parsing text. Every
tool is a thin reading of the same functions the CLIs call, so the served answer and
the printed answer cannot drift; refusals reuse the CLIs' own wording.

Three serving rules, each load-bearing:

* **stdout belongs to the protocol.** An MCP stdio session frames JSON-RPC on
  stdout, so one stray `print` corrupts the stream. Library functions here do print
  (`check_freshness` narrates its verdict), so every tool body runs with stdout
  redirected to stderr — structurally, not by auditing call sites.
* **the graph on disk wins.** A rebuild while the server is up must be served, not
  the load from startup: every call re-stats `graph.jsonl` + `build_report.json`
  and reloads when they moved. A server answering from yesterday's graph at today's
  confidence is the silent-staleness failure this repo keeps finding.
* **fastmcp is the optional `serve` extra.** The module imports without it (the
  zero-dependency gate covers that), and `main` refuses loudly with the install
  line — degraded and saying so, never dead at an ImportError.

`check_fresh` needs a source tree to compare against, so the server takes the same
`--workspace`/`--target` context the builder does; without one, that tool refuses
and the other seven still serve. It writes nothing: `check_freshness` used to emit
its verdict as a `build_report.json` and would have overwritten the served build's
own provenance, which this server worked around with a scratch directory until item
120 removed the write.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from tools import cli  # noqa: E402
from tools.graph import validate as validate_lib
from tools.graph.impact import (GraphError, impact, impact_diff, known_components, load_graph,
                                parse_diff, parse_lines)
from tools.graph.pack import BUDGET_FLOOR, DEFAULT_BUDGET, build_pack, render_md
from tools.graph.rules import RULES_BASENAME, RulesError, discover_rules

cli.use_utf8()

try:
    from fastmcp import FastMCP
except ImportError:  # the serve extra is optional; `main` owns the refusal
    FastMCP = None

SCHEMA_VERSION = 1
SERVER_NAME = "sdlc-graph"

#: The eight, pinned: a registration that grows or shrinks must change this tuple,
#: and the test that lists the served tools fails until it does.
TOOL_NAMES = ("graph_status", "impact", "impact_diff", "context_pack",
              "search_nodes", "node_neighbors", "validate_graph", "check_fresh")

#: BUDGET_FLOOR is imported from pack.py, not copied: one source, so the served
#: floor cannot silently drift from the CLI's (item 115).


def _shield():
    """stdout → stderr for the duration of a tool body (see the module docstring)."""
    return contextlib.redirect_stdout(sys.stderr)


def graph_problem(graph_dir: Path) -> str:
    """Why `graph_dir` holds no graph to serve, or '' when it holds one. One sentence
    for the startup check and the reload, so the two cannot drift (T520)."""
    path = Path(graph_dir) / "graph.jsonl"
    if path.is_file():
        return ""
    return (f"{path} does not exist — point --graph-dir at a completed build's output "
            f"directory")


class GraphService:
    """The eight tools as plain methods — testable and served without drift.

    Refusals raise ValueError with the CLIs' wording; the MCP layer surfaces them
    as tool errors verbatim.
    """

    def __init__(self, graph_dir: Path, workspace: Path | None = None,
                 target: Path | None = None, rules_path: Path | None = None):
        self.graph_dir = Path(graph_dir)
        self.workspace = workspace
        self.target = target
        self.rules_path = rules_path
        self._sig: tuple | None = None
        self._nodes: list[dict] = []
        self._edges: list[dict] = []
        self._rules = None
        self._rules_source = ""

    # ── the load, current by construction ─────────────────────────────────────

    def _signature(self) -> tuple:
        rows = []
        for name in ("graph.jsonl", "build_report.json"):
            try:
                st = (self.graph_dir / name).stat()
                rows.append((name, st.st_mtime_ns, st.st_size))
            except OSError:
                rows.append((name, -1, -1))
        return tuple(rows)

    def data(self) -> tuple[list[dict], list[dict]]:
        sig = self._signature()
        if sig != self._sig:
            # T520: the reload is a door too. `load_graph` answers a missing or empty
            # file with no rows - its callers check that the graph is there - and this
            # one did not, so a graph deleted or truncated under a running server was
            # served as 0 nodes beside the last build's `ok`.
            if problem := graph_problem(self.graph_dir):
                raise GraphError(problem)
            nodes, edges = load_graph(self.graph_dir)
            if not nodes:
                raise GraphError(
                    f"{self.graph_dir / 'graph.jsonl'} holds no nodes - every completed "
                    f"build writes one repo node per component, so this graph is "
                    f"truncated or was never completed. Rebuild it (tools/graph/build.py) "
                    f"rather than serving an empty answer as a whole one")
            self._nodes, self._edges = nodes, edges
            self._rules, self._rules_source = discover_rules(self.graph_dir,
                                                             self.rules_path)
            self._sig = sig
        return self._nodes, self._edges

    def _report(self) -> tuple[dict, str]:
        """The committed build_report.json as a dict, plus a could-not-look note.

        Deleted, truncated and wrong-shape all used to fold into `{}`, so a `run_id`
        of '' from a report that could not be READ was indistinguishable from one a
        build genuinely left empty. The unresolved rollup twenty lines down already
        names its own absent case; this draws the same distinction (Auditor class 3
        — could-not-look is not measured-empty). The note is '' only when a real
        JSON object was read.
        """
        path = self.graph_dir / "build_report.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, "build_report.json is absent from this graph dir"
        except OSError as exc:
            return {}, (f"build_report.json could not be read "
                        f"({exc.__class__.__name__})")
        except ValueError:
            return {}, "build_report.json is not valid JSON (truncated or damaged)"
        if not isinstance(report, dict):
            return {}, (f"build_report.json is a JSON {type(report).__name__}, "
                        f"not an object")
        return report, ""

    # ── the eight ─────────────────────────────────────────────────────────────

    def status(self) -> dict:
        nodes, edges = self.data()
        report, report_note = self._report()
        # graph.jsonl keeps one node row per emitting extractor; counts must
        # dedupe by id or a twice-declared node counts twice (the item 22 defect).
        ids_by_type: dict[str, set[str]] = {}
        for n in nodes:
            ids_by_type.setdefault(n.get("type", ""), set()).add(n["id"])
        edge_counts: dict[str, int] = {}
        for e in edges:
            edge_counts[e["type"]] = edge_counts.get(e["type"], 0) + 1
        unresolved_path = self.graph_dir / "unresolved.jsonl"
        unresolved = (validate_lib.unresolved_rollup(
            validate_lib._rows(unresolved_path))
            if unresolved_path.exists()
            else [{"kind": "unresolved.jsonl is absent from this graph dir",
                   "rows": 0}])
        return {
            "schema_version": SCHEMA_VERSION,
            "server": SERVER_NAME,
            "graph_dir": str(self.graph_dir),
            "run_id": report.get("run_id", ""),
            "workspace": report.get("workspace", {}).get("name", "")
            if isinstance(report.get("workspace"), dict) else "",
            "build_status": report.get("status", ""),
            # could-not-look (absent / unreadable / damaged / wrong-shape) is
            # named here, not folded into the '' run_id above (T237); "ok" means a
            # real JSON object was read, mirroring the unresolved sentinel below.
            "build_report": report_note or "ok",
            # Whether the build dropped nothing in silence - every extractor and link
            # pass ran, no contract was violated - by the one rule `validate` grades
            # with (T639): `build_status` stays `ok` over a crashed link pass, and this
            # door named it nowhere. A report it could not read is no clean build.
            "clean_build": (validate_lib.compute_clean_build(report) if not report_note
                            else {"metric": "zero_silent_drops", "met": False,
                                  "problems": [report_note]}),
            # The declared workspace (repo nodes) is the orientation; the full
            # alias set the other tools accept also holds every module-scoped
            # props.component (256 vs 26 on the measured estate), so it is
            # counted here rather than listed — a wrong alias still gets the
            # complete list back in the refusal.
            "components": sorted({n["id"].split(":", 1)[1] for n in nodes
                                  if n.get("type") == "repo"}),
            "component_aliases": len(known_components(nodes)),
            "nodes": {t: len(ids) for t, ids in sorted(ids_by_type.items())},
            "edges": dict(sorted(edge_counts.items())),
            "rules": self._rules_source,
            "unresolved": unresolved,
        }

    def impact_seed(self, component: str, path: str, lines: str | None = None,
                    hops: int | None = None) -> dict:
        nodes, edges = self.data()
        ranges = parse_lines(lines) if lines else None
        result = impact(nodes, edges, component, path, hops=hops, lines=ranges,
                        rules=self._rules)
        if result is None:
            raise ValueError(self._unknown_component(component, nodes))
        return result

    def impact_of_diff(self, component: str, diff_text: str,
                       hops: int | None = None) -> dict:
        nodes, edges = self.data()
        targets = sorted(parse_diff(diff_text).items())
        if not targets:
            raise ValueError("the diff contains no file headers a unified diff "
                             "would have")
        result = impact_diff(nodes, edges, component, targets, hops=hops,
                             rules=self._rules)
        if result is None:
            raise ValueError(self._unknown_component(component, nodes))
        return result

    def context_pack(self, component: str, path: str | None = None,
                     lines: str | None = None, diff_text: str | None = None,
                     budget: int = DEFAULT_BUDGET, format: str = "md") -> dict:
        if bool(path) == bool(diff_text):
            raise ValueError("exactly one of path and diff_text")
        if lines and not path:
            raise ValueError("lines refines path's file; with diff_text the hunks "
                             "come from the diff itself")
        if format not in ("md", "json"):
            raise ValueError("format must be 'md' or 'json'")
        if budget < BUDGET_FLOOR:
            raise ValueError(f"budget {budget} is below the floor of {BUDGET_FLOOR} "
                             f"tokens - the pack's header alone would overrun it")
        nodes, edges = self.data()
        if diff_text is not None:
            targets = sorted(parse_diff(diff_text).items())
            if not targets:
                raise ValueError("the diff contains no file headers a unified diff "
                                 "would have")
        else:
            targets = [(path, parse_lines(lines) if lines else None)]
        pack = build_pack(nodes, edges, component, targets, rules=self._rules,
                          graph_dir=self.graph_dir)
        if pack is None:
            raise ValueError(self._unknown_component(component, nodes))
        work = dict(pack)
        markdown, budget_info = render_md(work, budget=budget)
        work["budget"] = budget_info
        if format == "json":
            # The machine copy is never trimmed — but it carries the budget
            # block exactly as the CLI's pack.json does, stating what the md
            # rendering elided at this budget.
            return work
        return {"format": "md", "markdown": markdown, "budget": budget_info,
                "graph": pack["graph"]}

    def search_nodes(self, query: str, node_type: str | None = None,
                     limit: int = 25) -> dict:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        nodes, _ = self.data()
        q = query.lower()
        seen: set[str] = set()
        matches = []
        for n in nodes:
            if n["id"] in seen:
                continue  # one row per emitting extractor; list each node once
            props = n.get("props", {})
            if node_type and n.get("type") != node_type:
                continue
            if q not in n["id"].lower() and q not in str(props.get("path", "")).lower():
                continue
            seen.add(n["id"])
            matches.append({"id": n["id"], "type": n.get("type", ""),
                            "component": props.get("component", ""),
                            "path": props.get("path", ""),
                            # Batch 15b (D15b-48): a deployment file the graph could not
                            # read says so where an agent looks.
                            **({"unread": props["unread"]} if props.get("unread") else {})})
        matches.sort(key=lambda r: (r["type"], r["id"]))
        shown = matches[:limit]
        return {"total": len(matches), "shown": len(shown),
                "elided": len(matches) - len(shown), "nodes": shown}

    def node_neighbors(self, node_id: str, limit: int = 50) -> dict:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        nodes, edges = self.data()
        node = next((n for n in nodes if n["id"] == node_id), None)
        if node is None:
            raise ValueError(f"node {node_id!r} is not in this graph - use "
                             f"search_nodes to find ids")

        def row(e: dict, other: str) -> dict:
            out = {"type": e["type"], "other": other,
                   "source_file": e["source_file"], "line": e["line"],
                   "confidence": e.get("confidence", 1.0),
                   "canonical": e.get("canonical", True)}
            if e.get("props", {}).get("generated"):
                out["generated"] = True
            if e.get("props", {}).get("declared"):
                out["declared"] = True  # item 79: a hand-declared join, cited to
                # the declaration file, never blended into extraction
                if e["props"].get("declared_at"):
                    # Batch 23: where a `[[deploys]]` block declared it (D23-26).
                    out["declared_at"] = e["props"]["declared_at"]
            return out

        outgoing = sorted((row(e, e["dst"]) for e in edges if e["src"] == node_id),
                          key=lambda r: (r["type"], r["other"], r["source_file"],
                                         r["line"]))
        incoming = sorted((row(e, e["src"]) for e in edges if e["dst"] == node_id),
                          key=lambda r: (r["type"], r["other"], r["source_file"],
                                         r["line"]))
        return {
            "node": {"id": node["id"], "type": node.get("type", ""),
                     "props": node.get("props", {})},
            "out": outgoing[:limit], "out_elided": max(0, len(outgoing) - limit),
            "in": incoming[:limit], "in_elided": max(0, len(incoming) - limit),
        }

    def validate_graph(self, sample: int = 0,
                       targets: dict[str, float] | None = None) -> dict:
        if sample < 0:
            # T523's refusal, at this door too: `random.sample` otherwise answers
            # in its own words, about a population nobody here named.
            raise ValueError(f"sample {sample} is not a number of edges to draw - "
                             f"pass 0 or more (0 draws none)")
        merged = dict(validate_lib.DEFAULT_TARGETS)
        if targets:
            unknown = sorted(set(targets) - set(merged))
            if unknown:
                raise ValueError(f"unknown target(s): {', '.join(unknown)} "
                                 f"(known: {', '.join(sorted(merged))})")
            merged.update(targets)
        return validate_lib.validate(self.graph_dir, merged, sample=sample)

    def check_fresh(self) -> dict:
        if self.workspace is None and self.target is None:
            raise ValueError("the server was started without --workspace or "
                             "--target, and freshness is a statement about a "
                             "source tree; restart it with one")
        from tools.graph.build import BuildError, check_freshness
        # The scratch directory this used to need is gone with the defect it
        # worked around: `check_freshness` wrote its verdict as a
        # `build_report.json` and would have overwritten the served build's own
        # provenance. Item 120 removed the write; leaving the workaround here
        # would teach the next reader a rule that is no longer true.
        try:
            return check_freshness(self.workspace, target=self.target)
        except BuildError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _unknown_component(component: str, nodes: list[dict]) -> str:
        return (f"component {component!r} is not in this graph; components: "
                f"{', '.join(sorted(known_components(nodes)))}")


# `uv sync` is exact: without --inexact it would uninstall every extra the
# environment holds and this line does not name - `sdlc`'s in-toto among them (T467).
_MISSING_FASTMCP = (
    "fastmcp is not installed - the MCP server is the optional serve layer.\n"
    "Add it to the harness environment, removing nothing already there, with:\n"
    "    uv sync --inexact --extra serve\n"
    "(or run via `uv run --extra serve ...`, as the installed wiring does). "
    "Every other tool works without it.")


def build_server(service: GraphService):
    """The FastMCP instance with the eight tools registered over `service`.

    Descriptions are the agent-facing UX: each says what the answer is and when to
    reach for it. Every body runs under the stdout shield.
    """
    if FastMCP is None:
        raise RuntimeError(_MISSING_FASTMCP)
    mcp = FastMCP(SERVER_NAME)

    @mcp.tool
    def graph_status() -> dict:
        """What this graph is: run id, workspace, declared components, node/edge
        counts by type, the traversal-rules source, the unresolved rollup, and
        whether the build was clean (`clean_build`: the zero-silent-drops verdict
        `validate_graph` grades by, each problem it found named in `problems`).
        Call first to orient. Other tools also accept module-scoped component
        aliases beyond the declared list (their count is reported); a wrong alias
        is refused with the complete list."""
        with _shield():
            return service.status()

    @mcp.tool
    def impact(component: str, path: str, lines: str | None = None,
               hops: int | None = None) -> dict:
        """Blast radius of a change to one file: ranked impacted files and services,
        each with a score and hop depth. `path` is component-relative exactly as the
        graph cites it; `lines` (e.g. '12-40,55') narrows the seed to the facts
        declared in those ranges."""
        with _shield():
            return service.impact_seed(component, path, lines, hops)

    @mcp.tool
    def impact_diff(component: str, diff_text: str,
                    hops: int | None = None) -> dict:
        """Blast radius of a whole unified diff: every touched file seeds, hunk-
        refined where it declares facts, with per-file seed records. Use for 'what
        does this change break' on a diff you already hold."""
        with _shield():
            return service.impact_of_diff(component, diff_text, hops)

    @mcp.tool
    def context_pack(component: str, path: str | None = None,
                     lines: str | None = None, diff_text: str | None = None,
                     budget: int = DEFAULT_BUDGET, format: str = "md") -> dict:
        """The review-ready context pack for a change (exactly one of `path` and
        `diff_text`): seeds, contracts with declared-at citations, direct/indirect
        files with routes, config coupling, tests to run-or-edit. format='md' is
        budget-trimmed with elisions stated; format='json' is complete and never
        trimmed."""
        with _shield():
            return service.context_pack(component, path, lines, diff_text,
                                        budget, format)

    @mcp.tool
    def search_nodes(query: str, node_type: str | None = None,
                     limit: int = 25) -> dict:
        """Find graph nodes by case-insensitive substring over node ids and the
        paths of file-backed nodes (tests, configs, charts), optionally filtered
        by type (svc, topic, ep, table, cfg, test, ...). The entry point for
        'where does X live' - feed the ids to node_neighbors. For what the graph
        knows about a SOURCE file, use `impact`: source files are cited on edges,
        not stored as nodes."""
        with _shield():
            return service.search_nodes(query, node_type, limit)

    @mcp.tool
    def node_neighbors(node_id: str, limit: int = 50) -> dict:
        """One node's direct edges, in and out, each with the other endpoint,
        source_file:line provenance, confidence and canonicality. Hop-by-hop graph
        exploration; truncation beyond `limit` is counted, never silent."""
        with _shield():
            return service.node_neighbors(node_id, limit)

    @mcp.tool
    def validate_graph(sample: int = 0,
                       targets: dict[str, float] | None = None) -> dict:
        """The validation dashboard: per-metric ratios with numerators shown, the
        unresolved rollup, and a met/missed verdict against the default targets
        (override explicitly via `targets`). `sample` adds a seeded spot-audit."""
        with _shield():
            return service.validate_graph(sample, targets)

    @mcp.tool
    def check_fresh() -> dict:
        """Is the committed snapshot current with the source tree? Per component:
        fresh; stale with the reason (files moved; the extractors or their
        extraction code changed, or a file of that code unreadable; the workspace
        config changed; no stamps; inputs it read from another component that
        moved, or a reader no longer or newly active there; a parser or its
        version that changed; a directory that could not be listed); or
        refused - a shard it cannot
        trust - with the reason, and its remedy under `remedies`. A deployment
        reader still without its parser is under `unparsed`, the parsers its rows
        name under `unparsed_parsers` ("" for a row naming none its reader
        declares, whose state cannot be asked), and one way on for the whole set
        under `unparsed_way`, joining both ways when the set needs both. Needs
        the server started with --workspace or --target."""
        with _shield():
            return service.check_fresh()

    return mcp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph-dir", metavar="DIR",
                    help="a completed build's output directory (graph.jsonl); "
                         "default: <target>/.sdlc/graph when --target is given")
    ap.add_argument("--target", metavar="DIR",
                    help="the single repository the graph describes - supplies the "
                         "default graph dir and gives check_fresh its source tree")
    ap.add_argument("--workspace", metavar="TOML",
                    help="the workspace config the graph was built from - gives "
                         "check_fresh its source tree in estate mode")
    ap.add_argument("--rules", metavar="FILE",
                    help=f"a {RULES_BASENAME} overriding the built-in damping table "
                         f"(default: {RULES_BASENAME} beside the graph if the build "
                         f"placed one there, else the built-ins)")
    args = ap.parse_args(argv)

    if FastMCP is None:
        print(_MISSING_FASTMCP)
        return 2

    if not args.graph_dir and not args.target:
        print("!! one of --graph-dir and --target: the server needs a graph to serve")
        return 2
    graph_dir = (Path(args.graph_dir) if args.graph_dir
                 else Path(args.target) / ".sdlc" / "graph")
    if problem := graph_problem(graph_dir):
        print(f"!! {problem}")
        return 2

    service = GraphService(
        graph_dir,
        workspace=Path(args.workspace) if args.workspace else None,
        target=Path(args.target) if args.target else None,
        rules_path=Path(args.rules) if args.rules else None)
    try:
        service.data()  # a bad graph or rules file refuses at startup, not first call
    except (GraphError, RulesError, OSError, ValueError) as exc:
        print(f"!! {exc}")
        return 2

    build_server(service).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
