"""A deployment-only repository's claims join the estate only at a meeting point (T534 V4, V5).

Extraction claims a deployment-only repo's services repo-scoped, as main does, and marks
each `join: name`. The link pass `join-by-name` moves a marked claim onto
`component_service_id(name)` where (a) a claim that DECLARES the name already targets that
id - a chart's or a build's, never one placed by the name alone - or (b) a component
carries the name (ruling 1). A join is INFERRED. One `name-joined-service` row per service
joined by (b) alone says so, until a `[[canonical]]` block DECIDES the service (rulings 6
and 7).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, compose, lay_down, spawn, svc  # noqa: E402

JAVA = "public class C { }\n"
ROUTER = ('from fastapi import APIRouter\nrouter = APIRouter(prefix="/cache")\n\n\n'
          '@router.get("/ping")\ndef ping():\n    return "ok"\n')


def chart(name: str) -> dict:
    return {f"charts/{name}/values.yaml": (f"fullnameOverride: {name}\nimage:\n"
                                           f"  repository: org/{name}\n"),
            f"charts/{name}/Chart.yaml": f"apiVersion: v2\nname: {name}\nversion: 0.1.0\n"}


def image(name: str, tag: str = "org/x:1") -> str:
    return compose(svc(name, None, None, image=tag))


def joined(edge: dict) -> tuple[str, str, str, str]:
    p = edge["props"]
    return edge["dst"], p.get("mapped_by", ""), edge["provenance"], p.get("joined_by", "")


def node(built, node_id: str) -> dict:
    found = [n for n in built.nodes if n["id"] == node_id]
    assert len(found) == 1, (node_id, len(found))
    return found[0]


# ── (a) a declared claim, (b) a component carrying the name ──────────────────────────────


def test_a_deployment_file_joins_the_node_a_chart_declares(tmp_path):
    """T534's own row: a chart and a compose deployment file declare one service, which
    was two nodes. Now one, contested between the two packages. Fails on main."""
    built = build(tmp_path, {"orders": {"src/main/java/C.java": JAVA},
                             "deploy-charts": chart("orders"),
                             "deploy-compose": {"docker-compose.yml": image("orders")}})
    assert joined(built.deploy("deploy-compose/docker-compose.yml")) == (
        "svc:orders@orders", "name", "INFERRED", "claim")
    row, = built.kinds("contested-service")
    assert row["claims"] == ["deploy-charts/charts/orders/values.yaml:1",
                             "deploy-compose/docker-compose.yml:2"]
    assert node(built, "svc:orders@orders")["props"]["component"] == "orders"   # V5


def test_a_chart_is_a_meeting_point_where_no_component_carries_the_name(tmp_path):
    """D9-23: the join is by the chart's claim; the node's `component` is empty, since no
    component carries the name (V5)."""
    built = build(tmp_path, {"deploy-charts": chart("payments"),
                             "deploy-compose": {"docker-compose.yml": image("payments")}})
    assert joined(built.deploy("deploy-compose/docker-compose.yml")) == (
        "svc:payments@payments", "name", "INFERRED", "claim")
    assert node(built, "svc:payments@payments")["props"]["component"] == ""
    assert built.kinds("contested-service")


def coincidence(extra: dict | None = None) -> dict:
    return {"deploy-compose": {"docker-compose.yml": image("redis", "redis:7")},
            "redis": {"src/main/java/C.java": JAVA}, **(extra or {})}


def test_a_component_carrying_the_name_is_a_meeting_point_and_the_row_says_so(tmp_path):
    built = build(tmp_path, coincidence())
    assert joined(built.deploy("deploy-compose/docker-compose.yml")) == (
        "svc:redis@redis", "name", "INFERRED", "component")
    row, = built.kinds("name-joined-service")
    assert (row["service"], row["target"], row["claims"], row["choices"]) == (
        "redis", "svc:redis@redis", ["deploy-compose/docker-compose.yml:2"],
        [{"owner": "deploy-compose"}])
    assert "ranks canonical" not in row["why"], "one package: nothing to rank (D534-51)"


def test_the_block_the_row_names_silences_it_and_keeps_the_join(tmp_path):
    built = build(tmp_path, coincidence(
        {"__canonical__": [{"service": "redis", "owner": "deploy-compose"}]}))
    assert joined(built.deploy("deploy-compose/docker-compose.yml"))[0] == "svc:redis@redis"
    assert not built.kinds("name-joined-service", "contested-service")


def test_a_claim_placed_by_the_name_alone_is_no_meeting_point(tmp_path):
    """D534-16: `redis`'s own file names `redis` and so sits on `svc:redis@redis` by name
    only; the deployment repo joins by the component, with the row, not by that claim."""
    built = build(tmp_path, {
        "redis": {"app/api.py": ROUTER, "docker-compose.yml": image("redis", "redis:7")},
        "deploy-billing": {"docker-compose.yml": image("redis", "redis:7")}})
    assert joined(built.deploy("deploy-billing/docker-compose.yml")) == (
        "svc:redis@redis", "name", "INFERRED", "component")
    assert built.kinds("name-joined-service")


@pytest.mark.parametrize("own_build, joined_by, rows", [
    (".", "claim", 0), ("../nope", "component", 1)])
def test_a_self_named_unplaced_build_is_no_meeting_point(tmp_path, own_build, joined_by,
                                                          rows):
    """D534-91: an unplaced build of a service named after its own component sits on that
    node only because the name matched, so a deployment-only claim joins by the component,
    with one row - which its block silences."""
    estate = {"orders": {"app/api.py": ROUTER,
                         "docker-compose.yml": compose(svc("orders", own_build, None))},
              "deploy-compose": {"docker-compose.yml": image("orders", "org/orders:1")}}
    built = build(tmp_path / "a", estate)
    assert joined(built.deploy("deploy-compose/docker-compose.yml"))[3] == joined_by
    assert len(built.kinds("name-joined-service")) == rows
    if rows:
        acked = build(tmp_path / "b", {**estate, "__canonical__": [
            {"service": "orders", "owner": "deploy-compose"}]})
        assert acked.code == 0
        assert not acked.kinds("name-joined-service")


@pytest.mark.parametrize("code", [{"README.md": "x\n"}, {"app/api.py": ROUTER}])
def test_an_entrypoint_on_a_joined_node_is_stamped_once(tmp_path, code):
    """D534-17: the join adds the target node to the live list only when it is absent, so
    `mark-semantic` sees it and stamps it once."""
    built = build(tmp_path, {"redis": code,
                             "deploy-compose": {"docker-compose.yml": image("redis", "redis:7")},
                             "__entrypoint__": ["svc:redis@redis"]})
    assert node(built, "svc:redis@redis")["props"].get("entrypoint") is True
    assert not built.kinds("entrypoint-unmatched")


def test_the_marker_is_stripped_by_the_pass_and_stays_where_it_did_not_run(tmp_path):
    full = build(tmp_path / "a", coincidence())
    assert "join" not in full.deploy("deploy-compose/docker-compose.yml")["props"]
    extract_only = build(tmp_path / "b", coincidence(), "--only", "compose-services")
    edge = extract_only.deploy("deploy-compose/docker-compose.yml")
    assert (edge["dst"], edge["props"].get("join")) == ("svc:redis@deploy-compose", "name")


def join_two_repos(extra: dict | None = None) -> dict:
    return {"redis": {"app/api.py": ROUTER},
            "deploy-a": {"docker-compose.yml": image("redis", "redis:7")},
            "deploy-b": {"docker-compose.yml": image("redis", "redis:6")}, **(extra or {})}


def join_nested(extra: dict | None = None) -> dict:
    return {"redis": {"app/api.py": ROUTER},
            "platform": {"docker-compose.yml": image("redis", "redis:7"),
                         "svc-a/docker-compose.yml": image("redis", "redis:6")},
            "platform/svc-a": {}, **(extra or {})}


def test_one_row_per_service_lists_every_joined_package(tmp_path):
    """Ruling 6 (D534-32): two deployment repos joining one component give one row."""
    built = build(tmp_path, join_two_repos())
    row, = built.kinds("name-joined-service")
    assert row["claims"] == ["deploy-a/docker-compose.yml:2", "deploy-b/docker-compose.yml:2"]
    assert row["choices"] == [{"owner": "deploy-a"}, {"owner": "deploy-b"}]
    assert "ranks canonical" in row["why"]


@pytest.mark.parametrize("shape, owner, stands", [
    ("two repos", "redis", True),        # the code component decides nothing
    ("two repos", "deploy-a", False),
    ("nested", "platform", True),        # a parent reading two packages decides nothing
    ("nested", "svc-a", False),
])
def test_only_a_block_that_decides_silences_the_row(tmp_path, shape, owner, stands):
    """Ruling 7 (D534-40): the row stops only when the block selects exactly one package
    among every package claiming the node, by `mark-canonical`'s own rule."""
    make = join_two_repos if shape == "two repos" else join_nested
    built = build(tmp_path, make({"__canonical__": [{"service": "redis", "owner": owner}]}))
    assert {e["dst"] for e in built.deploys} == {"svc:redis@redis"}, "joined either way"
    assert bool(built.kinds("name-joined-service")) is stands
    assert bool(built.kinds("contested-service")) is stands


def test_the_row_offers_only_its_cited_packages_and_counts_the_others(tmp_path):
    """D534-49: `redis`'s own file claims the node too, by name. The row cites and offers
    only the deployment repo's package, and says one other package claims the node."""
    built = build(tmp_path, {
        "redis": {"app/api.py": ROUTER, "docker-compose.yml": image("redis", "redis:7")},
        "deploy-billing": {"docker-compose.yml": image("redis", "redis:7")}})
    row, = built.kinds("name-joined-service")
    assert (row["claims"], row["choices"]) == (["deploy-billing/docker-compose.yml:2"],
                                               [{"owner": "deploy-billing"}])
    assert "1 other package claims this node too" in row["why"]


def test_a_load_from_the_snapshot_recomputes_the_join(tmp_path):
    """D534-26: shards hold the extracted, marked claim; the join is derived at load."""
    lay_down(tmp_path, coincidence())
    published = spawn(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    loaded = spawn(tmp_path, "--from-snapshot", out="loaded")
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    from tools.graph.tests._estate import read
    again = read(0, tmp_path / "loaded")
    assert joined(again.deploy("deploy-compose/docker-compose.yml")) == (
        "svc:redis@redis", "name", "INFERRED", "component")
    assert again.kinds("name-joined-service")


# ── V5: a service node's `component` prop ────────────────────────────────────────────────


def test_a_built_services_node_names_the_component_it_is_scoped_to(tmp_path):
    built = build(tmp_path, {"deploy": {"docker-compose.yml": compose(
        svc("web", "../orders", None), svc("db", None, None, image="postgres:16"))},
        "orders": {"README.md": "x\n"}})
    assert node(built, "svc:web@orders")["props"]["component"] == "orders"
    assert node(built, "svc:db@deploy")["props"]["component"] == "deploy"
