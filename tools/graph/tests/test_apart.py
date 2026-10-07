"""`[[apart]]` refuses a false name join (T594, batch 15b).

A deployment-only repository's claim joins the estate by name (T534): a stock `redis`
container beside a real `redis` component becomes that component's service, and a
`[[canonical]]` block can acknowledge the join but nothing could refuse it. The new block -
`service`, `component` (the DECLARING component), optional `note` - does, in `join-by-name`
against the live config:

* an apart claim is never name-joined, and stays on its own node;
* an apart chart on the service's estate id is RE-TARGETED to its own component's node
  (`moved`) - unless another package already claims that node (ruling 15), when it stays
  where its file put it, unjoined (`held`), and is never ranked canonical there (ruling 19);
* anything else it matches is `kept` where it is.

Every matched claim carries `apart`, and the block's `note` as `apart_note`. The refusal
changes identity only (ruling 23, T621): the refused file's environment still resolves as
the configuration of the component named like the service.

These cells pin the PLACEMENTS and props; the contest clauses are `mark-canonical`'s and the
accounting rows `mark-semantic`'s, pinned beside them. Everything here is synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links.namejoin import NameJoinPass, apart_matches  # noqa: E402
from tools.graph.tests._estate import build, compose, svc  # noqa: E402

JAVA = "public class C { }\n"
REDIS_READER = 'import os\n\nURL = os.environ["REDIS_URL"]\n'


def chart(name: str, under: str = "charts") -> dict:
    return {f"{under}/{name}/values.yaml": f"fullnameOverride: {name}\n",
            f"{under}/{name}/Chart.yaml": f"apiVersion: v2\nname: {name}\nversion: 0.1.0\n"}


def stock(name: str, image: str, env: dict[str, str] | None = None) -> str:
    body = f"  {name}:\n    image: {image}\n"
    if env:
        body += "    environment:\n" + "".join(f"      {k}: {v}\n" for k, v in env.items())
    return body


def apart(service: str, component: str, note: str = "") -> dict:
    return {"service": service, "component": component, **({"note": note} if note else {})}


def edge_props(built, source: str, service: str | None = None) -> dict:
    return built.deploy(source, service).get("props") or {}


#: The headline: a stock redis container in a deployment-only `deploy`, beside a real
#: `redis` component that reads REDIS_URL.
REDIS = {"deploy": {"docker-compose.yml": "services:\n" + stock(
             "redis", "redis:7", {"REDIS_URL": "redis://cache:6379"})},
         "redis": {"app.py": REDIS_READER}}


# ── the false join, refused ──────────────────────────────────────────────────


def test_the_false_redis_join_is_offered_the_block_then_refused(tmp_path):
    """Red on `main`: nothing could refuse the join. The row offers the block, saying where
    the claim then sits and what the block does not do; followed, the claim stays on
    `deploy`'s own node, `kept`, and the row is gone."""
    joined = build(tmp_path / "1", REDIS)
    assert joined.placed("deploy/docker-compose.yml")[0] == "svc:redis@redis"
    (row,) = joined.kinds("name-joined-service")
    assert '[[apart]] service = "redis" component = "deploy"' in row["why"]
    assert "its claim then sits on `svc:redis@deploy`, its own node" in row["why"]
    refused = build(tmp_path / "2", {**REDIS, "__apart__": [apart("redis", "deploy")]})
    assert refused.placed("deploy/docker-compose.yml")[:1] == ("svc:redis@deploy",)
    props = edge_props(refused, "deploy/docker-compose.yml")
    assert props["apart"] == "kept" and "join" not in props and "joined_by" not in props
    assert refused.kinds("name-joined-service") == []


def test_the_refusal_changes_identity_only(tmp_path):
    """Ruling 23 (T621): the offering row says the values the stock container sets are still
    read as `redis`'s configuration; after the refusal the component's REDIS_URL is still
    resolved from the container's file - `env-key-unset` stays absent, as on `main` - pinned
    so that a later fix of T621 is seen here."""
    joined = build(tmp_path / "1", REDIS)
    (row,) = joined.kinds("name-joined-service")
    assert ("the block refuses the join only; the values `deploy/docker-compose.yml` sets for "
            "`redis` are still read as `redis`'s configuration") in row["why"]
    assert "(filed as T621)" in row["why"]
    refused = build(tmp_path / "2", {**REDIS, "__apart__": [apart("redis", "deploy")]})
    assert not [r for r in refused.kinds("env-key-unset") if r.get("component") == "redis"]


def test_a_row_spanning_two_deployers_stands_until_both_are_refused(tmp_path):
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "ops": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "redis": {"app.py": REDIS_READER}}
    (row,) = build(tmp_path / "1", estate).kinds("name-joined-service")
    for deployer in ("deploy", "ops"):
        assert f'[[apart]] service = "redis" component = "{deployer}"' in row["why"]
    one = build(tmp_path / "2", {**estate, "__apart__": [apart("redis", "deploy")]})
    (row,) = one.kinds("name-joined-service")
    assert row["components"] == ["ops"]
    both = build(tmp_path / "3", {**estate, "__apart__": [apart("redis", "deploy"),
                                                          apart("redis", "ops")]})
    assert both.kinds("name-joined-service") == []


def test_one_block_refuses_every_claim_its_component_makes(tmp_path):
    """D15b-88: two compose files of one component naming the service - the clause names
    both, and following it refuses both."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7"),
                         "docker-compose.prod.yml": "services:\n" + stock("redis", "redis:7")},
              "redis": {"app.py": REDIS_READER}}
    (row,) = build(tmp_path / "1", estate).kinds("name-joined-service")
    assert ("this block refuses every claim `deploy` makes on `redis`: "
            "deploy/docker-compose.prod.yml, deploy/docker-compose.yml") in row["why"]
    refused = build(tmp_path / "2", {**estate, "__apart__": [apart("redis", "deploy")]})
    for rel in ("docker-compose.yml", "docker-compose.prod.yml"):
        assert refused.placed(f"deploy/{rel}")[0] == "svc:redis@deploy"
        assert edge_props(refused, f"deploy/{rel}")["apart"] == "kept"


def test_a_normalised_spelling_matches(tmp_path):
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("my-redis", "redis:7")},
              "my-redis": {"app.py": REDIS_READER},
              "__apart__": [apart("my redis", "deploy")]}
    built = build(tmp_path, estate)
    assert edge_props(built, "deploy/docker-compose.yml")["apart"] == "kept"
    assert built.kinds("name-joined-service") == []


def test_two_blocks_on_one_component_each_refuse_their_own_service(tmp_path):
    """Review focus 2: `deploy` apart from `redis` and from `postgres` - each refuses only
    its own claim, and removing one restores only that join."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")
                         + stock("postgres", "postgres:16")},
              "redis": {"app.py": REDIS_READER}, "postgres": {"app.py": REDIS_READER}}
    both = build(tmp_path / "1", {**estate, "__apart__": [apart("redis", "deploy"),
                                                          apart("postgres", "deploy")]})
    for service in ("redis", "postgres"):
        assert both.placed("deploy/docker-compose.yml", service)[0] == f"svc:{service}@deploy"
    one = build(tmp_path / "2", {**estate, "__apart__": [apart("redis", "deploy")]})
    assert one.placed("deploy/docker-compose.yml", "redis")[0] == "svc:redis@deploy"
    assert one.placed("deploy/docker-compose.yml", "postgres")[0] == "svc:postgres@postgres"


def test_a_block_naming_a_build_claim_keeps_it(tmp_path):
    estate = {"deploy": {"docker-compose.yml": compose(svc("web", "../web", None))},
              "web": {"src/main/java/C.java": JAVA},
              "__apart__": [apart("web", "deploy")]}
    built = build(tmp_path, estate)
    assert built.placed("deploy/docker-compose.yml")[:1] == ("svc:web@web",)
    assert edge_props(built, "deploy/docker-compose.yml")["apart"] == "kept"


def test_the_blocks_note_rides_every_claim_it_matches(tmp_path):
    """D15b-115: a declared fact is never a silent no-op."""
    built = build(tmp_path, {**REDIS, "__apart__": [
        apart("redis", "deploy", "a stock redis container, not the redis component")]})
    assert edge_props(built, "deploy/docker-compose.yml")["apart_note"] == \
        "a stock redis container, not the redis component"
    plain = build(tmp_path / "2", {**REDIS, "__apart__": [apart("redis", "deploy")]})
    assert "apart_note" not in edge_props(plain, "deploy/docker-compose.yml")


# ── a refused chart: moved, held or kept (ruling 15) ─────────────────────────


def test_a_refused_chart_moves_to_its_own_components_node(tmp_path):
    """The control: no build anywhere builds `a`, so its chart re-targets to `svc:web@a`,
    its `component` its own, and the node it left - edge-less now - is dropped."""
    built = build(tmp_path, {"a": chart("web"), "__apart__": [apart("web", "a")]})
    assert built.placed("a/charts/web/values.yaml")[:2] == ("svc:web@a", "a")
    assert edge_props(built, "a/charts/web/values.yaml")["apart"] == "moved"
    assert "svc:web@web" not in built.svc_ids()


def test_a_chart_whose_component_a_build_elsewhere_builds_is_held(tmp_path):
    """Ruling 15: `svc:web@a` is `deploy`'s build's node, so the refused chart stays on the
    node its file gave it, unjoined - placed on `svc:web@a`, the refusal itself would have
    joined it to the build. Held, it carries `canonical=False` explicitly (D15b-121)."""
    built = build(tmp_path, {"a": {**chart("web"), "src/main/java/C.java": JAVA},
                             "deploy": {"docker-compose.yml": compose(svc("web", "../a", None))},
                             "__apart__": [apart("web", "a")]})
    edge = built.deploy("a/charts/web/values.yaml")
    assert edge["dst"] == "svc:web@web" and edge["props"]["apart"] == "held"
    assert edge["canonical"] is False
    assert built.placed("deploy/docker-compose.yml")[0] == "svc:web@a"


def test_a_chart_and_a_build_of_one_component_is_held(tmp_path):
    built = build(tmp_path, {"a": {**chart("web"), "src/main/java/C.java": JAVA,
                                   "docker-compose.yml": compose(svc("web", ".", None))},
                             "__apart__": [apart("web", "a")]})
    assert edge_props(built, "a/charts/web/values.yaml")["apart"] == "held"
    assert built.deploy("a/charts/web/values.yaml")["dst"] == "svc:web@web"


def test_a_chart_and_a_compose_project_of_one_component_neither_joins_the_other(tmp_path):
    built = build(tmp_path, {"a": {**chart("web"), "docker-compose.yml":
                                   "services:\n" + stock("web", "org/web:1")},
                             "__apart__": [apart("web", "a")]})
    assert (built.deploy("a/docker-compose.yml")["dst"],
            edge_props(built, "a/docker-compose.yml")["apart"]) == ("svc:web@a", "kept")
    assert (built.deploy("a/charts/web/values.yaml")["dst"],
            edge_props(built, "a/charts/web/values.yaml")["apart"]) == ("svc:web@web", "held")


def test_one_block_keeps_a_components_compose_files_on_its_own_node(tmp_path):
    """D15b-73: three compose files of `a` naming `web`, each name-joined to `web`'s node;
    refused, all three on `svc:web@a`, `kept`."""
    files = {f"{d}/docker-compose.yml": "services:\n" + stock("web", "org/web:1")
             for d in ("x", "y", "z")}
    estate = {"a": files, "web": {"src/main/java/C.java": JAVA}}
    joined = build(tmp_path / "1", estate)
    assert {joined.placed(f"a/{rel}")[0] for rel in files} == {"svc:web@web"}
    refused = build(tmp_path / "2", {**estate, "__apart__": [apart("web", "a")]})
    for rel in files:
        assert (refused.placed(f"a/{rel}")[0], edge_props(refused, f"a/{rel}")["apart"]) == \
            ("svc:web@a", "kept")


def test_two_charts_of_one_component_move_together_in_either_order(tmp_path):
    """D15b-64: one block matches both; judged against ONE snapshot of the edges as
    extracted, the order the pass meets them in decides nothing."""
    for first, second in (("web", "web-v2"), ("web-v2", "web")):
        estate = {"a": {f"charts/{first}/values.yaml": "fullnameOverride: web\n",
                        f"charts/{second}/values.yaml": "fullnameOverride: web\n"},
                  "__apart__": [apart("web", "a")]}
        built = build(tmp_path / first, estate)
        for name in (first, second):
            assert (built.placed(f"a/charts/{name}/values.yaml")[0],
                    edge_props(built, f"a/charts/{name}/values.yaml")["apart"]) == \
                ("svc:web@a", "moved")


def test_the_re_target_comes_before_the_name_rule(tmp_path):
    """The refused chart moved off `svc:web@web` leaves no declaration there, so a
    deployment-only `runner` naming `web` meets nothing and stays on its own node - a
    re-target after the name rule would have left it alone on `svc:web@web`."""
    built = build(tmp_path, {"a": chart("web"),
                             "runner": {"docker-compose.yml": "services:\n"
                                        + stock("web", "org/web:1")},
                             "__apart__": [apart("web", "a")]})
    assert built.placed("runner/docker-compose.yml")[0] == "svc:web@runner"
    assert "svc:web@web" not in built.svc_ids()


def test_a_narrowed_run_may_move_a_chart_a_whole_build_holds(tmp_path):
    """D15b-72: judged over fewer claims, the guard cannot see the build that claims
    `svc:web@a`, so the chart moves - and the narrowed line says so."""
    estate = {"a": {**chart("web"), "src/main/java/C.java": JAVA},
              "deploy": {"docker-compose.yml": compose(svc("web", "../a", None))},
              "__apart__": [apart("web", "a")]}
    build(tmp_path, estate)
    narrowed = build(tmp_path, None, "--components", "a")
    assert (narrowed.placed("a/charts/web/values.yaml")[0],
            edge_props(narrowed, "a/charts/web/values.yaml")["apart"]) == ("svc:web@a", "moved")


# ── the pass, in-process: over shards published before this batch ────────────


def test_the_re_target_needs_no_reader_prop(tmp_path):
    """D15b-44: the structural test reads ids alone, so a block applies over a shard
    published before batch 15b - a helm deployment node with no `kind` and no `ships_in`."""
    from types import SimpleNamespace
    nodes = [Node(id="deploy:a/charts/web/values.yaml", type="deploy",
                  props={"component": "a", "path": "charts/web/values.yaml"}),
             Node(id="svc:web@web", type="svc", props={"component": ""})]
    edges = [Edge(src="deploy:a/charts/web/values.yaml", type="DG_DEPLOYS", dst="svc:web@web",
                  source_file="charts/web/values.yaml", line=1, extractor="helm-deployments",
                  confidence=1.0, props={"service": "web"})]
    ctx = SimpleNamespace(config={"apart": [apart("web", "a")],
                                  "components": [{"name": "a", "path": "a"}]})
    NameJoinPass().link(ctx, nodes, edges)
    assert (edges[0].dst, edges[0].props["apart"]) == ("svc:web@a", "moved")
    assert {n.id for n in nodes} == {"deploy:a/charts/web/values.yaml", "svc:web@a"}


def test_apart_matches_reads_the_declaring_component_and_the_normalised_service():
    nodes = {"deploy:d/x.yml": Node(id="deploy:d/x.yml", type="deploy",
                                    props={"component": "d"})}

    def claim(service: str) -> Edge:
        return Edge(src="deploy:d/x.yml", type="DG_DEPLOYS", dst="svc:x@d", source_file="x.yml",
                    line=1, extractor="compose-services", confidence=1.0,
                    props={"service": service})
    assert apart_matches(apart("my redis", "d"), claim("my-redis"), nodes)
    assert not apart_matches(apart("redis", "e"), claim("redis"), nodes), "another component"
    assert not apart_matches(apart("Redis", "d"), claim("redis"), nodes), \
        "norm_id keeps case: `Redis` is another service (review focus 3)"
