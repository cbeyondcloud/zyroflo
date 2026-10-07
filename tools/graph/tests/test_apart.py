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

import pytest

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


# ── task 10: the contest rows over refused claims (mark-canonical) ────────────

import json  # noqa: E402
import subprocess  # noqa: E402

from tools.graph.tests._estate import ROOT  # noqa: E402

#: `a` holds a chart and its code, and `deploy` builds `../a`: `svc:web@a` is the build's.
HELD = {"a": {**chart("web"), "src/main/java/C.java": JAVA},
        "deploy": {"docker-compose.yml": compose(svc("web", "../a", None))}}


def contest(built, node: str) -> dict:
    (row,) = [r for r in built.kinds("contested-service") if r["service"] == node]
    return row


def offered(row: dict) -> list[str]:
    return [c["owner"] + (f":{c['path']}" if c.get("path") else "") for c in row["choices"]]


def score_of(rows: list[dict], component: str, path: str) -> float:
    (score,) = [r["score"] for r in rows if (r["component"], r["path"]) == (component, path)]
    return score


def impact_files(where: Path, seed: str) -> list[dict]:
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "graph" / "impact.py"),
                           "--graph-dir", str(where / "out"), "--seed", seed, "--hops", "3",
                           "--top", "0", "--json"],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["files"]


def pack_files(where: Path, seed: str) -> list[dict]:
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "graph" / "pack.py"),
                           "--graph-dir", str(where / "out"), "--seed", seed, "--hops", "3",
                           "--out", str(where / "pack"), "--no-svg"],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pack = json.loads((where / "pack" / "pack.json").read_text(encoding="utf-8"))
    return [row for rows in pack["sections"].values() for row in rows]


def test_a_refused_chart_leaves_its_cross_component_contest(tmp_path):
    estate = {"a": chart("web"), "b": chart("web")}
    assert build(tmp_path / "1", estate).kinds("contested-service")
    refused = build(tmp_path / "2", {**estate, "__apart__": [apart("web", "a")]})
    assert refused.kinds("contested-service") == []
    assert refused.placed("a/charts/web/values.yaml")[0] == "svc:web@a"


def test_a_held_chart_in_a_contest_is_named_and_never_offered(tmp_path):
    """Ruling 19: a component `web` building `web` meets the held chart on `svc:web@web`.
    The row names why the chart is still there, and offers only `web`'s package;
    followed, `web`'s build is canonical there."""
    estate = {**HELD, "web": {"src/main/java/C.java": JAVA,
                              "docker-compose.yml": compose(svc("web", ".", None))},
              "__apart__": [apart("web", "a")]}
    row = contest(build(tmp_path / "1", estate), "svc:web@web")
    assert ("`a/charts/web/values.yaml` is declared apart (`workspace.toml:") in row["why"]
    assert ("but stays on this node: its component's own node `svc:web@a` holds the claims "
            "of `deploy/docker-compose.yml`, and a refusal never joins it there. Declared "
            "apart from this service, it cannot be ranked canonical here, so no choice below "
            "names it") in row["why"]
    assert offered(row) == ["web"]
    decided = build(tmp_path / "2", {**estate, "__canonical__": [
        {"service": "web", "owner": "web"}]})
    assert decided.kinds("contested-service") == []
    assert decided.deploy("web/docker-compose.yml")["canonical"] is True
    held = decided.deploy("a/charts/web/values.yaml")
    assert (held["canonical"], held["confidence"]) == (False, 1.0)
    assert "canonical_by" not in held["props"] and "superseded_by" not in held["props"]


def test_a_block_naming_the_refusing_component_ranks_nothing_where_it_is_held(tmp_path):
    """D15b-94: `owner = "a"` names the held chart's component; it is not ranked canonical
    on `svc:web@web` - the contest stands, its row saying the block decides none of these
    packages, offering `owner = "web"`."""
    estate = {**HELD, "web": {"src/main/java/C.java": JAVA,
                              "docker-compose.yml": compose(svc("web", ".", None))},
              "__apart__": [apart("web", "a")],
              "__canonical__": [{"service": "web", "owner": "a"}]}
    row = contest(build(tmp_path, estate), "svc:web@web")
    assert "the workspace's block for this service (`workspace.toml:" in row["why"]
    assert "decides none of these packages; change it to one of: owner = \"web\"" in row["why"]


def test_moved_charts_contest_on_their_own_node_and_a_block_decides_there(tmp_path):
    """D15b-64, D15b-85: the two charts refused onto `svc:web@a` contest there, each with a
    "moved" clause; a block naming `a` with a path decides on `a`'s own node (ruling 19's
    other half)."""
    estate = {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                    "charts/web-v2/values.yaml": "fullnameOverride: web\n"},
              "__apart__": [apart("web", "a")]}
    row = contest(build(tmp_path / "1", estate), "svc:web@a")
    assert row["why"].count("and moved to its component's own node, with the claims of that "
                            "component the block also matches; they contest here") == 2
    decided = build(tmp_path / "2", {**estate, "__canonical__": [
        {"service": "web", "owner": "a", "path": "charts/web-v2/values.yaml"}]})
    assert decided.kinds("contested-service") == []
    assert decided.deploy("a/charts/web-v2/values.yaml")["canonical"] is True


def test_kept_compose_files_contest_with_their_kept_clauses(tmp_path):
    """D15b-73: three "already sits on its component's own node" clauses - none saying
    another package holds the node - and the choices followed decide."""
    files = {f"{d}/docker-compose.yml": "services:\n" + stock("web", "org/web:1")
             for d in ("x", "y", "z")}
    estate = {"a": files, "web": {"src/main/java/C.java": JAVA},
              "__apart__": [apart("web", "a")]}
    row = contest(build(tmp_path / "1", estate), "svc:web@a")
    assert row["why"].count("the block changes nothing here, since it already sits on its "
                            "component's own node") == 3
    assert "holds the claims of" not in row["why"]
    choice = row["choices"][0]
    decided = build(tmp_path / "2", {**estate, "__canonical__": [{"service": "web", **choice}]})
    assert not [r for r in decided.kinds("contested-service") if r["service"] == "svc:web@a"]


def test_a_name_joined_claim_in_a_contest_is_offered_the_block(tmp_path):
    """D15b-65: `runner`'s claim joins a chart's node by name; the contest says where it
    would sit if refused - read from its class, never from the joined `dst` - and following
    the offer puts it there."""
    estate = {"charts-repo": chart("web"),
              "runner": {"docker-compose.yml": "services:\n" + stock("web", "org/web:1")}}
    row = contest(build(tmp_path / "1", estate), "svc:web@web")
    assert ('[[apart]] service = "web" component = "runner" - its claim then sits on '
            '`svc:web@runner`, its own node') in row["why"]
    refused = build(tmp_path / "2", {**estate, "__apart__": [apart("web", "runner")]})
    assert refused.placed("runner/docker-compose.yml")[0] == "svc:web@runner"


#: D15b-101: `a` and `b` each hold a chart `web` beside code a build elsewhere builds.
ALL_HELD = {"a": {**chart("web"), "src/main/java/C.java": JAVA},
            "b": {**chart("web"), "src/main/java/C.java": JAVA},
            "deploy": {"docker-compose.yml": compose(svc("web", "../a", None))},
            "deploy2": {"docker-compose.yml": compose(svc("web", "../b", None))},
            "__apart__": [apart("web", "a"), apart("web", "b")]}


def test_a_node_every_package_of_which_is_held_offers_no_choice(tmp_path):
    row = contest(build(tmp_path / "1", ALL_HELD), "svc:web@web")
    assert row["choices"] == []
    assert ("every package claiming this node is declared apart from `web` "
            "(`workspace.toml:") in row["why"]
    assert ("and held here, its own component's node holding another package's claim, so "
            "nothing here can be ranked canonical; if one of them is this service, remove "
            "its `[[apart]]` block") in row["why"]
    assert "one of:" not in row["why"], "never an empty offer"
    # Followed: `a`'s block removed, its chart is offered, and its choice decides.
    un_held = build(tmp_path / "2", {**ALL_HELD, "__apart__": [apart("web", "b")]})
    row = contest(un_held, "svc:web@web")
    assert offered(row) == ["a"]
    decided = build(tmp_path / "3", {**ALL_HELD, "__apart__": [apart("web", "b")],
                                     "__canonical__": [{"service": "web", "owner": "a"}]})
    assert decided.deploy("a/charts/web/values.yaml")["canonical"] is True


def test_an_all_held_node_names_the_block_that_cannot_decide_it(tmp_path):
    """D15b-109: one sentence, naming the workspace's block as unable to decide."""
    row = contest(build(tmp_path, {**ALL_HELD, "__canonical__": [
        {"service": "web", "owner": "a"}]}), "svc:web@web")
    assert ("the workspace's block for this service (`workspace.toml:") in row["why"]
    assert "cannot decide it either, since every package here is held" in row["why"]
    assert "change it to one of" not in row["why"] and "one of:" not in row["why"]


#: Ruling 22: `a` holds two charts naming `web`, both held (`deploy` builds `../a`).
LIVE_LEGACY = {"a": {"charts/live/values.yaml": "fullnameOverride: web\n",
                     "charts/legacy/values.yaml": "fullnameOverride: web\n",
                     "src/main/java/C.java": JAVA},
               "deploy": {"docker-compose.yml": compose(svc("web", "../a", None))},
               "__apart__": [apart("web", "a")]}
LIVE_BLOCK = {"service": "web", "owner": "a", "path": "charts/live/values.yaml"}


def test_held_claims_rank_among_themselves(tmp_path):
    built = build(tmp_path, {**LIVE_LEGACY, "__canonical__": [LIVE_BLOCK]})
    live, legacy = (built.deploy(f"a/charts/{n}/values.yaml") for n in ("live", "legacy"))
    assert (live["canonical"], live["confidence"]) == (False, 1.0)
    assert "superseded_by" not in live["props"]
    assert (legacy["canonical"], legacy["confidence"]) == (False, 0.5)
    assert legacy["props"]["superseded_by"] == "a/charts/live/values.yaml"
    row = contest(built, "svc:web@web")
    assert ("ranks `a/charts/live/values.yaml` above the other packages of `a`, but cannot "
            "make any of them canonical here, since every package here is held") in row["why"]
    reached = score_of(impact_files(tmp_path, "a/charts/live/values.yaml"), "a",
                       "charts/legacy/values.yaml")
    assert reached == 0.15, "the legacy chart reached at the damped weight, as on main"


def test_an_owner_only_block_over_two_held_packages_ranks_nothing(tmp_path):
    built = build(tmp_path, {**LIVE_LEGACY, "__canonical__": [{"service": "web", "owner": "a"}]})
    for name in ("live", "legacy"):
        edge = built.deploy(f"a/charts/{name}/values.yaml")
        assert (edge["confidence"], "superseded_by" in edge["props"]) == (1.0, False)


def test_a_ranking_beside_a_rankable_package_says_what_changing_the_block_ends(tmp_path):
    """D15b-133: the node also holds `b`'s chart, which can be ranked: the row says the block
    decides none of these packages but still ranks `live` - and that changing it ends that;
    followed (`owner = "b"`), `b` is canonical and `legacy` is undamped again."""
    estate = {**LIVE_LEGACY, "b": chart("web"), "__canonical__": [LIVE_BLOCK]}
    row = contest(build(tmp_path / "1", estate), "svc:web@web")
    assert ("decides none of these packages, but ranks `a/charts/live/values.yaml` above "
            "`a`'s other held packages; changing it to one of the choices below ends that "
            "ranking") in row["why"]
    assert offered(row) == ["b"]
    followed = build(tmp_path / "2", {**estate, "__canonical__": [{"service": "web",
                                                                   "owner": "b"}]})
    assert followed.deploy("b/charts/web/values.yaml")["canonical"] is True
    legacy = followed.deploy("a/charts/legacy/values.yaml")
    assert (legacy["canonical"], legacy["confidence"]) == (False, 1.0)
    assert "superseded_by" not in legacy["props"]


def test_a_held_claims_weight_is_noncanonical_and_undamped(tmp_path):
    """D15b-138: `b` canonical, `a`'s chart held and unranked - `impact.py` and `pack.py`
    weigh it 0.30, where `main`, which damped it as a superseded variant, gave 0.15."""
    built = build(tmp_path, {**HELD, "b": chart("web"), "__apart__": [apart("web", "a")],
                             "__canonical__": [{"service": "web", "owner": "b"}]})
    assert built.deploy("b/charts/web/values.yaml")["canonical"] is True
    assert built.deploy("a/charts/web/values.yaml")["props"]["apart"] == "held"
    assert score_of(impact_files(tmp_path, "b/charts/web/values.yaml"), "a",
                    "charts/web/values.yaml") == 0.3
    assert score_of(pack_files(tmp_path, "b/charts/web/values.yaml"), "a",
                    "charts/web/values.yaml") == 0.3


def test_a_contest_clause_quotes_the_blocks_note(tmp_path):
    estate = {**HELD, "web": {"src/main/java/C.java": JAVA,
                              "docker-compose.yml": compose(svc("web", ".", None))},
              "__apart__": [apart("web", "a", "the chart is a demo")]}
    assert '"the chart is a demo"' in contest(build(tmp_path, estate), "svc:web@web")["why"]


# ── task 11: the blocks that refuse nothing, and the duplicates (mark-semantic) ─

NOT_READ = ("(the graph does not read a compose file under another name or one pulled in by "
            "`include:` or `extends:`, nor a chart's values overlays)")


def unmatched(built) -> dict:
    (row,) = built.kinds("apart-unmatched")
    return row


def test_a_block_refusing_nothing_says_what_was_read(tmp_path):
    """Followed - naming a service the graph read - the block refuses it, and the row is
    gone."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "__apart__": [apart("rediss", "deploy")]}
    row = unmatched(build(tmp_path / "1", estate))
    assert (row["component"], row["service"], row["source_file"]) == (
        "deploy", "rediss", "workspace.toml")
    assert row["why"].startswith("The graph read no claim of `deploy` on a service `rediss`, "
                                 "so this block refuses nothing it read. It read: `redis`; "
                                 "name one of them, or remove the block")
    assert row["why"].endswith(NOT_READ)
    followed = build(tmp_path / "2", {**estate, "__apart__": [apart("redis", "deploy")]})
    assert followed.kinds("apart-unmatched") == []


def test_a_case_spelling_is_not_the_service(tmp_path):
    """Review focus 3: `Redis` against the claim `redis` - listed, and followed, refused."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "redis": {"app.py": REDIS_READER}}
    built = build(tmp_path / "1", {**estate, "__apart__": [apart("Redis", "deploy")]})
    assert "It read: `redis`" in unmatched(built)["why"]
    refused = build(tmp_path / "2", {**estate, "__apart__": [apart("redis", "deploy")]})
    assert edge_props(refused, "deploy/docker-compose.yml")["apart"] == "kept"


def test_an_unparsable_file_of_the_component_is_named_first(tmp_path):
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7"),
                         "ops/docker-compose.yml": "services:\n  x:\n    image: \"y\n"},
              "__apart__": [apart("web", "deploy")]}
    why = unmatched(build(tmp_path, estate))["why"]
    assert why.startswith("`ops/docker-compose.yml` could not be read (see its row), so what "
                          "it declares is unknown; fix it, then: The graph read no claim")


def test_a_component_whose_every_file_is_unread_drops_the_list(tmp_path):
    estate = {"deploy": {"docker-compose.yml": "services:\n  x:\n    image: \"y\n"},
              "__apart__": [apart("web", "deploy")]}
    why = unmatched(build(tmp_path, estate))["why"]
    assert "None of `deploy`'s deployment files could be read: `docker-compose.yml` could not " \
           "be read" in why
    assert "It read:" not in why


def test_a_templates_only_component_was_handed_no_deployment_file(tmp_path):
    estate = {"a": {"charts/web/templates/cm.yaml": "apiVersion: v1\nkind: ConfigMap\n"
                                                    "metadata:\n  name: h\ndata:\n  X: y\n"},
              "__apart__": [apart("web", "a")]}
    why = unmatched(build(tmp_path, estate))["why"]
    assert why.startswith("The graph read no claim of `a` on a service `web`, so this block "
                          "refuses nothing it read. The graph read no deployment file of `a`, "
                          "so this block can refuse nothing it read; remove the block")


@pytest.mark.parametrize("files, named, fix", [
    ({"charts/web/Values.yaml": "fullnameOverride: web\n", "charts/web/Chart.yaml": "x: 1\n"},
     "`charts/web/Values.yaml` is not read because of its case",
     ("charts/web/Values.yaml", "charts/web/values.yaml")),
    ({"charts/web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
      "charts/web/templates/d.yaml": "apiVersion: apps/v1\nkind: Deployment\n"},
     "`charts/web` has no `values.yaml`", None),
], ids=["mis-cased-values", "chart-only"])
def test_a_deployment_held_but_unnamed_is_named_and_followed(tmp_path, files, named, fix):
    """D15b-93: the row names the file instead of saying there is none; renamed, or a values
    file naming the service added, the block refuses what it names."""
    estate = {"a": files, "__apart__": [apart("web", "a")]}
    assert named in unmatched(build(tmp_path / "1", estate))["why"]
    if fix:
        before, after = fix
        lost = tmp_path / "1" / "ws" / "a" / before
        lost.rename(lost.with_name(Path(after).name))
        followed = build(tmp_path / "1", None)
    else:
        followed = build(tmp_path / "2", {"a": {**files, "charts/web/values.yaml":
                                                "fullnameOverride: web\n"},
                                          "__apart__": [apart("web", "a")]})
    assert followed.kinds("apart-unmatched") == []
    assert edge_props(followed, "a/charts/web/values.yaml")["apart"] == "moved"


def test_a_workspace_whose_only_block_is_apart_is_judged(tmp_path):
    """D15b-22: the pass's early return fired when `entrypoint` and `alias` were empty."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "__apart__": [apart("nothing", "deploy")]}
    assert build(tmp_path, estate).kinds("apart-unmatched")


def test_a_normalised_duplicate_is_named_and_followed(tmp_path):
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("my-redis", "redis:7")},
              "__apart__": [apart("my redis", "deploy"), apart("my-redis", "deploy")]}
    (row,) = build(tmp_path / "1", estate).kinds("apart-duplicate")
    assert (row["service"], row["ignored"], row["source_file"]) == (
        "my-redis", "my-redis", "workspace.toml")
    assert row["why"].endswith("Remove one of them")
    followed = build(tmp_path / "2", {**estate, "__apart__": [apart("my redis", "deploy")]})
    assert followed.kinds("apart-duplicate") == []


def test_the_duplicates_are_reported_even_by_a_narrowed_run(tmp_path):
    """D15b-56: facts about the config alone, never withheld - while `apart-unmatched` is."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")},
              "web": {"app.py": "x = 1\n"},
              "__apart__": [apart("my x", "deploy"), apart("my-x", "deploy")],
              "__canonical__": [{"service": "my api", "owner": "deploy"},
                                {"service": "my-api", "owner": "deploy"}]}
    build(tmp_path, estate)
    narrowed = build(tmp_path, None, "--components", "deploy")
    assert narrowed.kinds("canonical-duplicate") and narrowed.kinds("apart-duplicate")
    assert narrowed.kinds("apart-unmatched") == []


def test_a_load_over_shards_published_before_the_block_judges_it(tmp_path):
    """`[[apart]]` is not stamped (ruling 17): the load stays current and judges, as a fresh
    build does."""
    estate = {"deploy": {"docker-compose.yml": "services:\n" + stock("redis", "redis:7")}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    toml = tmp_path / "ws" / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[[apart]]\nservice = "rediss"\ncomponent = "deploy"\n',
                    encoding="utf-8")
    assert build(tmp_path, None, "--from-snapshot").kinds("apart-unmatched")


def test_mains_workaround_and_the_block_together(tmp_path):
    """Review focus 1: a workspace keeping the `[[canonical]]` block it wrote on `main` to
    silence `name-joined-service` adds `[[apart]]` for the same pair - refused, and no row
    of any of the three kinds."""
    built = build(tmp_path, {**REDIS, "__apart__": [apart("redis", "deploy")],
                             "__canonical__": [{"service": "redis", "owner": "deploy"}]})
    assert edge_props(built, "deploy/docker-compose.yml")["apart"] == "kept"
    assert not built.kinds("name-joined-service", "canonical-unmatched", "contested-service")
