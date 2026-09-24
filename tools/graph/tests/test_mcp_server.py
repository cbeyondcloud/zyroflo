"""Item 23 — the graph served over MCP: eight tools, no drift from the CLIs.

Fixture pins come from EXPECTED.md's facts through the same functions the CLIs
call: the producer seed's radius, the golden topic's neighbors at their cited
lines, item 78's generated flag riding the wire. The serving rules get their own
pins — tool stdout must land on stderr (an MCP stdio session frames JSON-RPC on
stdout), `check_fresh` must never overwrite the served build report, and a graph
rebuilt on disk must be served without a restart.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.mcp_server as M  # noqa: E402
import tools.graph.pack as P  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
PRODUCER = ("orders-api", "src/main/java/com/example/orderflow/api/OrderPublisher.java")
TOPIC = "topic:order-events-v1"
URLS_DIFF = ("--- a/src/config/urls.js\n+++ b/src/config/urls.js\n"
             "@@ -5,1 +5,1 @@\n-x\n+y\n")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("serve") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def service(built):
    return M.GraphService(built, workspace=FIXTURE / "workspace.toml")


def call(service: M.GraphService, tool: str, args: dict):
    """One in-process MCP round trip; the deserialized result."""
    async def go():
        async with Client(M.build_server(service)) as c:
            return (await c.call_tool(tool, args)).data
    return asyncio.run(go())


# ── the service, against the answer key ───────────────────────────────────────


def test_the_status_names_the_build_and_counts_nodes_once_each(service, built):
    st = service.status()
    report = json.loads((built / "build_report.json").read_text(encoding="utf-8"))
    assert st["run_id"] == report["run_id"]
    assert {"orders-api", "orders-worker", "web-console"} <= set(st["components"])
    assert len(st["components"]) == len(set(st["components"])), \
        "one repo row per emitting extractor; the declared list must dedupe"
    assert st["component_aliases"] >= len(st["components"]), \
        "the alias set (module-scoped props.component) contains the declared list"
    assert st["rules"] == "defaults"
    # graph.jsonl keeps one node row per emitting extractor; the count must be
    # of distinct ids, or the twice-declared table would count twice.
    rows = [json.loads(line) for line in
            (built / "graph.jsonl").read_text(encoding="utf-8").splitlines()]
    distinct_topics = {r["id"] for r in rows
                      if r.get("kind") == "node" and r.get("type") == "topic"}
    assert st["nodes"]["topic"] == len(distinct_topics)
    assert st["edges"], "an edgeless status of this fixture is a broken load"
    assert any(u["rows"] for u in st["unresolved"]), \
        "the fixture build records unresolved rows; a rollup of zeros is a misread"
    assert st["build_report"] == "ok", \
        "a report read as a real dict is not a could-not-look"


def test_status_names_a_could_not_look_build_report_not_a_measured_empty(built,
                                                                         tmp_path):
    """T237: could-not-look is not measured-empty. A deleted, truncated or
    wrong-shape build_report.json used to fold into {} exactly like a valid-but-
    empty report, so status() reported run_id '' either way — could-not-look worn
    as a measured zero. The unresolved rollup twenty lines up already names its
    own absent case; build_report must draw the same distinction (Auditor class 3).
    """
    gdir = tmp_path / "g"
    shutil.copytree(built, gdir)
    rpath = gdir / "build_report.json"

    def status_with(content):
        if content is None:
            rpath.unlink()
        else:
            rpath.write_text(content, encoding="utf-8")
        return M.GraphService(gdir).status()

    empty = status_with("{}")                     # a real, empty JSON object
    absent = status_with(None)                    # deleted
    truncated = status_with('{"run_id": "abc')    # truncated JSON
    wrong = status_with("[1, 2, 3]")              # valid JSON, wrong shape

    # The defect: every could-not-look shape produced a status byte-identical to
    # the measured-empty one. If it is still present these differ nowhere.
    for broken, why in ((absent, "deleted"), (truncated, "truncated"),
                        (wrong, "wrong-shape")):
        assert broken != empty, \
            f"a {why} build_report must not read as a measured-empty report"

    assert empty["build_report"] == "ok"
    assert "absent" in absent["build_report"]
    assert "JSON" in truncated["build_report"]
    assert "not an object" in wrong["build_report"]
    # the empty-but-real report is still a measured read: its run_id is genuinely ''
    assert empty["run_id"] == "" and absent["run_id"] == ""


def test_impact_from_the_producer_reaches_the_consumer(service):
    result = service.impact_seed(*PRODUCER)
    assert result["schema_version"] == 1
    paths = [f["path"] for f in result["files"]]
    assert any(p.endswith("OrderConsumer.java") for p in paths)
    gen = next(f for f in result["files"] if f["path"] == "gen/EventsRelay.java")
    assert gen["generated"] is True, "item 78's flag must ride the served result"


def test_impact_refuses_a_component_not_in_this_graph(service):
    with pytest.raises(ValueError, match="not in this graph"):
        service.impact_seed("no-such", "x.java")
    with pytest.raises(ValueError, match="orders-api"):
        service.impact_seed("no-such", "x.java")  # the refusal lists what IS here


def test_impact_lines_ride_into_the_seed_block(service):
    result = service.impact_seed(PRODUCER[0], PRODUCER[1], lines="5-40")
    assert result["seed"]["lines"] == [[5, 40]]
    assert isinstance(result["seed"]["lines_missed"], bool)


def test_a_diff_becomes_per_file_seed_records(service):
    result = service.impact_of_diff("web-console", URLS_DIFF)
    assert result["seed"]["mode"] == "hunk"
    (f,) = result["seed"]["files"]
    assert f["path"] == "src/config/urls.js" and f["lines_missed"] is False


def test_a_diff_with_no_headers_is_refused(service):
    with pytest.raises(ValueError, match="unified diff"):
        service.impact_of_diff("web-console", "just some text\n")


def test_the_pack_md_is_budgeted_and_carries_the_sections(service):
    pack = service.context_pack(PRODUCER[0], path=PRODUCER[1])
    assert pack["format"] == "md"
    md = pack["markdown"]
    for title in ("## Seeds", "Contracts", "## Direct", "Tests to run-or-edit"):
        assert title in md
    assert "regenerate, don't edit" in md
    assert pack["budget"]["elided"] == {}, \
        "this fixture pack fits the default budget; an elision is a regression"


def test_the_pack_json_is_complete_and_never_trimmed(service, built):
    pack = service.context_pack(PRODUCER[0], path=PRODUCER[1], format="json",
                                budget=M.BUDGET_FLOOR)
    assert pack["schema_version"] == 1
    assert {"contracts", "direct", "indirect", "config", "tests"} == \
        set(pack["sections"])
    full = service.context_pack(PRODUCER[0], path=PRODUCER[1], format="json")
    assert {k: len(v) for k, v in pack["sections"].items()} == \
        {k: len(v) for k, v in full["sections"].items()}, \
        "the budget shapes the md only; the machine copy is complete at any budget"
    assert pack["budget"]["elided"], \
        "the CLI's pack.json states what the md elided; the served copy must too"
    report = json.loads((built / "build_report.json").read_text(encoding="utf-8"))
    assert pack["graph"]["run_id"] == report["run_id"]


def test_the_served_pack_refuses_its_half_and_bad_requests(service):
    # Renamed (item 115 / T236): this only ever exercised the MCP path — its old
    # title claimed a comparison "the cli does" that the body never performed.
    # The real CLI/served-pack composition lives in the next test.
    with pytest.raises(ValueError, match="exactly one of path and diff_text"):
        service.context_pack(PRODUCER[0])
    with pytest.raises(ValueError, match="exactly one of path and diff_text"):
        service.context_pack(PRODUCER[0], path="a.java", diff_text=URLS_DIFF)
    with pytest.raises(ValueError, match="floor"):
        service.context_pack(PRODUCER[0], path=PRODUCER[1], budget=50)
    with pytest.raises(ValueError, match="lines refines"):
        service.context_pack(PRODUCER[0], diff_text=URLS_DIFF, lines="5")
    with pytest.raises(ValueError, match="md.*json|'md' or 'json'"):
        service.context_pack(PRODUCER[0], path=PRODUCER[1], format="d2")


def test_the_pack_cli_and_the_served_pack_share_one_budget_floor(service, built,
                                                                 tmp_path):
    """Item 115 weld (T236): the served floor is pack.py's OWN constant, imported,
    and the two doors refuse the same budgets — composed against the REAL CLI
    (item 96: a CLI's contract is a process), not a hand-typed regex. A change to
    pack.BUDGET_FLOOR moves both, or this fails.

    `M.BUDGET_FLOOR is P.BUDGET_FLOOR` pins the import (a second copy at a
    DIFFERENT value would break it and the boundary drive below); a same-value
    re-copy is behaviourally identical to the weld and is prevented only by there
    being one source, not caught here.
    """
    import subprocess
    root = Path(__file__).resolve().parents[3]
    floor = P.BUDGET_FLOOR
    assert M.BUDGET_FLOOR is floor, "the server must import pack's floor, not copy it"

    def run_cli(budget):
        return subprocess.run(
            [sys.executable, str(root / "tools" / "graph" / "pack.py"),
             "--graph-dir", str(built),
             "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
             "--out", str(tmp_path / f"out{budget}"),
             "--budget", str(budget), "--no-svg"],
            capture_output=True, text=True, cwd=root, timeout=300)

    below_cli = run_cli(floor - 1)
    assert below_cli.returncode == 2, below_cli.stderr
    assert "floor" in below_cli.stdout and str(floor) in below_cli.stdout
    with pytest.raises(ValueError, match=rf"floor.*{floor}|{floor}.*floor"):
        service.context_pack(PRODUCER[0], path=PRODUCER[1], budget=floor - 1)

    at_cli = run_cli(floor)
    assert at_cli.returncode == 0, at_cli.stderr
    assert service.context_pack(PRODUCER[0], path=PRODUCER[1],
                                budget=floor)["format"] == "md"


def test_search_finds_the_topic_and_states_what_it_elided(service):
    result = service.search_nodes("order-events")
    assert any(n["id"] == TOPIC for n in result["nodes"])
    assert result["total"] == result["shown"] + result["elided"]
    ids = [n["id"] for n in result["nodes"]]
    assert len(ids) == len(set(ids)), \
        "one row per emitting extractor in graph.jsonl; the listing must merge"
    tight = service.search_nodes("order", limit=1)
    assert tight["shown"] == 1 and tight["elided"] == tight["total"] - 1 > 0


def test_search_filters_by_type_and_matches_file_backed_nodes_by_path(service):
    topics = service.search_nodes("order", node_type="topic")
    assert topics["nodes"] and all(n["type"] == "topic" for n in topics["nodes"])
    # Path matching reaches the nodes that ARE files (tests, configs, charts).
    # A plain source file is not a node — facts cite it on edges — so the file
    # question belongs to `impact`, which the tool description says.
    by_path = service.search_nodes("OrderControllerTest.java")
    assert by_path["total"] >= 1 and by_path["nodes"][0]["type"] == "test"
    assert service.search_nodes("OrderConsumer.java")["total"] == 0


def test_search_refuses_a_bad_limit(service):
    with pytest.raises(ValueError, match="limit"):
        service.search_nodes("x", limit=0)


def test_neighbors_walk_the_golden_chain_with_provenance(service):
    result = service.node_neighbors(TOPIC)
    assert result["node"]["id"] == TOPIC
    rows = result["in"] + result["out"]
    consume = next(r for r in rows if r["type"] == "DG_CONSUMES")
    assert consume["source_file"].endswith("OrderConsumer.java")
    assert consume["line"] == 8, "EXPECTED.md cites the consumer at line 8"
    assert consume["confidence"] == 1.0 and consume["canonical"] is True
    assert service.node_neighbors(TOPIC) == result, "exploration must be stable"


def test_neighbors_state_their_truncation(service):
    result = service.node_neighbors(TOPIC, limit=1)
    assert len(result["in"]) == 1
    assert result["in_elided"] >= 1, "a silent cap reads as a complete listing"


def test_neighbors_refuse_an_unknown_id_and_point_at_search(service):
    with pytest.raises(ValueError, match="search_nodes"):
        service.node_neighbors("svc:nowhere@nothing")


def test_validate_runs_the_dashboard(service, built):
    result = service.validate_graph()
    assert result["verdict"] in ("met", "missed")
    assert len(result["metrics"]) >= 7
    report = json.loads((built / "build_report.json").read_text(encoding="utf-8"))
    assert result["run_id"] == report["run_id"]
    sampled = service.validate_graph(sample=3)
    assert len(sampled["sample"]["rows"]) == 3


def test_validate_refuses_an_unknown_target_key(service):
    with pytest.raises(ValueError, match="unknown target.*host_keys_resolved"):
        service.validate_graph(targets={"made_up_metric": 10.0})


def test_check_fresh_reports_the_unstamped_fixture_stale(service):
    result = service.check_fresh()
    assert result["status"] == "stale"
    assert set(result["freshness"]["stale"]) >= {"orders-api", "orders-worker"}
    assert all("no freshness stamps" in why
               for why in result["freshness"]["stale"].values())


def test_check_fresh_never_touches_the_served_build_report(service, built):
    """check_freshness writes a build_report.json wherever it is pointed. Pointed
    at the served graph dir it would replace the build's own provenance (run_id,
    status) with a freshness report — and every later tool answer would cite a
    build that no longer describes itself."""
    before = (built / "build_report.json").read_bytes()
    service.check_fresh()
    assert (built / "build_report.json").read_bytes() == before


def test_check_fresh_without_tree_context_is_refused(built):
    bare = M.GraphService(built)
    with pytest.raises(ValueError, match="--workspace or\\s+--target|--workspace"):
        bare.check_fresh()


def test_a_rebuilt_graph_is_served_without_a_restart(built, tmp_path):
    """The graph on disk wins: a rebuild while the server is up must be served.
    A server answering from its startup load at full confidence is the silent-
    staleness failure this repo keeps finding."""
    mine = tmp_path / "out"
    shutil.copytree(built, mine)
    svc = M.GraphService(mine)
    assert svc.search_nodes("probe-node")["total"] == 0
    with (mine / "graph.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "node", "id": "svc:probe-node@orders-api",
                             "type": "svc", "props": {}}) + "\n")
    assert svc.search_nodes("probe-node")["total"] == 1


# ── the MCP layer: the same answers, over the wire ────────────────────────────


def test_the_server_serves_exactly_the_eight_tools(service):
    async def go():
        async with Client(M.build_server(service)) as c:
            return await c.list_tools()
    tools = asyncio.run(go())
    assert sorted(t.name for t in tools) == sorted(M.TOOL_NAMES)
    assert all(t.description for t in tools), \
        "a tool without a description is undiscoverable to the agent choosing it"


def test_the_wire_result_is_the_service_result(service):
    over_wire = call(service, "impact",
                     {"component": PRODUCER[0], "path": PRODUCER[1]})
    assert over_wire == service.impact_seed(*PRODUCER)


def test_a_refusal_crosses_the_wire_as_a_tool_error(service):
    with pytest.raises(ToolError, match="not in this graph"):
        call(service, "impact", {"component": "no-such", "path": "x.java"})


def test_tool_stdout_lands_on_stderr_never_the_protocol_stream(service, capsys):
    """check_freshness narrates its verdict with print(). Over stdio that would
    corrupt the JSON-RPC framing, so the tool layer redirects stdout to stderr
    structurally — this is the pin that the shield is on."""
    call(service, "check_fresh", {})
    captured = capsys.readouterr()
    assert "freshness:" not in captured.out
    assert "freshness: STALE" in captured.err


# ── the CLI ───────────────────────────────────────────────────────────────────


def test_the_cli_refuses_when_fastmcp_is_absent(built, monkeypatch, capsys):
    monkeypatch.setattr(M, "FastMCP", None)
    assert M.main(["--graph-dir", str(built)]) == 2
    out = capsys.readouterr().out
    assert "fastmcp" in out and "--extra serve" in out


def test_the_missing_fastmcp_remedy_removes_nothing(built, monkeypatch, capsys):
    """T467: the hint told a person to run `uv sync --extra dev --extra serve`.

    `uv sync` is exact, so in an environment that also held the `sdlc` extra that
    command UNINSTALLED in-toto - and it installed `dev`, which serving a graph does
    not need. Every uv command the hint prints must add `serve` alone and remove
    nothing: a `uv sync` must say `--inexact`, and a `uv run` (inexact unless told
    otherwise) must not say `--exact`.
    """
    monkeypatch.setattr(M, "FastMCP", None)
    assert M.main(["--graph-dir", str(built)]) == 2
    commands = re.findall(r"uv (?:sync|run)\b[^`\n]*", capsys.readouterr().out)
    assert commands, "the hint prints no uv command at all"
    for command in commands:
        words = command.split()
        assert re.findall(r"--extra[ =](\S+)", command) == ["serve"], command
        if words[1] == "sync":
            assert "--inexact" in words, f"`uv sync` is exact - this removes extras: {command}"
        else:
            assert "--exact" not in words, command


def test_the_cli_refuses_a_missing_graph_loudly(tmp_path, capsys):
    assert M.main(["--graph-dir", str(tmp_path / "nowhere")]) == 2
    assert "does not exist" in capsys.readouterr().out
    assert M.main([]) == 2
    assert "--graph-dir" in capsys.readouterr().out


def test_the_cli_refuses_a_bad_rules_file_at_startup(built, tmp_path, capsys):
    bad = tmp_path / "traversal_rules.yml"
    bad.write_text("hops: [not, a, number]\n", encoding="utf-8")
    assert M.main(["--graph-dir", str(built), "--rules", str(bad)]) == 2
    assert "!!" in capsys.readouterr().out
