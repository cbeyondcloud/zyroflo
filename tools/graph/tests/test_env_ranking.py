"""Batch 24 (T590; design pass 24, sections 2 and 3): env values across components. Each
component's own winner for a (view, key) is its candidate; the view holds the winner by one
function, `_canonical.env_winner` - the workspace's one decision over the compose packages
claiming the service, `mark-canonical`'s two steps, else the last component by NAME, and a group
holding two services decided by no block - and `env-ranking` rows every losing component whose
value differs, in a whole run alone, in one of four forms, each pinned word for word (D24-21).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, compose, producer, svc  # noqa: E402

STALE = "a stale deployment nobody has noticed is a finding, not noise"
SPELLED = ("the two are spelled differently and may be one value or two, depending on the tool "
           "that reads them")


def image(name: str, value: str, key: str = "TOPIC") -> str:
    return svc(name, None, value, key=key, image="org/x:1")


def ranking_rows(built) -> list[dict]:
    """The cross-component rows: `env-ranking`'s, never compose's within-component ones."""
    return [r for r in built.kinds("superseded-declaration") if r.get("extractor") == "env-ranking"]


def s1(owner: str | None = None, *, order: tuple[str, str] = ("deploy-a", "deploy-b")) -> dict:
    """S1: `deploy-a` and `deploy-b` set `TOPIC` for `payments`, which `payments` reads."""
    files = {"deploy-a": {"docker-compose.yml": compose(image("payments", "a-value"))},
             "deploy-b": {"docker-compose.yml": compose(image("payments", "b-value"))}}
    estate = {name: files[name] for name in order}
    estate["payments"] = {"app/pub.py": producer("TOPIC")}
    if owner:
        estate["__canonical__"] = [{"service": "payments", "owner": owner}]
    return estate


# ── section 2: the one decision, across components ───────────────────────────────────────


@pytest.mark.parametrize("order", [("deploy-a", "deploy-b"), ("deploy-b", "deploy-a")])
def test_the_owners_value_wins_in_every_component_order(tmp_path, order):
    """S1, D534-48, D534-67, D24-38: `main` let the component read last replace the owner's value
    with no row; the block selects `deploy-a`'s package, whatever the config's order, and
    `deploy-b`'s value is a row - form (1)."""
    built = build(tmp_path, s1("deploy-a", order=order))
    assert built.topics == ["topic:a-value"]
    (row,) = ranking_rows(built)
    assert (row["component"], row["source_file"], row["line"], row["key"], row["value"]) == (
        "deploy-b", "docker-compose.yml", 5, "topic", "b-value")
    assert row["why"] == (
        "`topic` for `payments` is also set here to `b-value`; both are filed under `payments`, "
        "the view `payments`'s code reads; the workspace's block for `payments` "
        f"(`{_block_at(row)}`) selects `deploy-a/docker-compose.yml`, so `deploy-a`'s value "
        "`a-value` (`docker-compose.yml:5`) is what the graph keeps; this one is reported because "
        + STALE)


def _block_at(row: dict) -> str:
    """The block's `workspace.toml:<line>`, read off the row's own words (its line moves with the
    estate's layout)."""
    return row["why"].split("(`", 1)[1].split("`)", 1)[0]


def test_with_no_block_the_last_component_by_name_wins_and_the_row_offers_the_choices(tmp_path):
    """S1 with no block, D534-55, D24-30: the deterministic winner is the last component by NAME,
    the loser's row offers every package of the decision set, and following a choice decides."""
    built = build(tmp_path / "a", s1())
    assert built.topics == ["topic:b-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == "deploy-a"
    assert row["why"] == (
        "`topic` for `payments` is also set here to `a-value`, and in "
        "`deploy-b/docker-compose.yml` to `b-value`, which the graph keeps - the last component "
        "by name, as no block selects one of these packages; both are filed under `payments`, "
        "the view `payments`'s code reads; to choose, add a `[[canonical]]` block naming service "
        "`payments` and one of: "
        "owner = \"deploy-a\" | owner = \"deploy-b\"; this one is reported because " + STALE)
    followed = build(tmp_path / "b", s1("deploy-a"))
    assert followed.topics == ["topic:a-value"]


def test_two_components_setting_one_value_are_no_contest(tmp_path):
    """D24-17: identical values are no contest - no row."""
    built = build(tmp_path, {
        "deploy-a": {"docker-compose.yml": compose(image("payments", "same"))},
        "deploy-b": {"docker-compose.yml": compose(image("payments", "same"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:same"]
    assert ranking_rows(built) == []


def test_a_block_whose_owner_reads_two_packages_decides_none_and_its_row_offers_a_path(tmp_path):
    """D534-39, D24-18, D24-30: `deploy-a` holds two packages of `payments` and `deploy-b` a third;
    an owner-only block for `deploy-a` selects none of them, so the last component by name wins,
    and the row offers `deploy-a` with a path - `choice_text`'s - which, followed, decides."""
    deploy_a = {"docker-compose.yml": compose(image("payments", "a1")),
                "docker-compose.prod.yml": compose(image("payments", "a2"))}
    base = {"deploy-a": deploy_a,
            "deploy-b": {"docker-compose.yml": compose(image("payments", "b-value"))},
            "payments": {"app/pub.py": producer("TOPIC")}}
    built = build(tmp_path / "a", {**base, "__canonical__": [
        {"service": "payments", "owner": "deploy-a"}]})
    assert built.topics == ["topic:b-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == "deploy-a"
    assert ("decides none of these packages; change it to one of: owner = \"deploy-a\", path = "
            "\"docker-compose.prod.yml\" | owner = \"deploy-a\", path = \"docker-compose.yml\" | "
            "owner = \"deploy-b\"") in row["why"], row["why"]
    followed = build(tmp_path / "b", {**base, "__canonical__": [
        {"service": "payments", "owner": "deploy-a", "path": "docker-compose.yml"}]})
    assert followed.topics == ["topic:a1"]


def test_both_files_named_alike_stay_two_packages(tmp_path):
    """D24-31: keyed by WORKSPACE path, `deploy-a/docker-compose.yml` and `deploy-b/...` are two
    packages - folded, the owner's block selected the fold and `b-value` won."""
    built = build(tmp_path, s1("deploy-a"))
    assert built.topics == ["topic:a-value"]


def test_a_block_selecting_a_keyless_package_decides_the_key_as_with_no_block(tmp_path):
    """D24-45, D24-62: the block selects `deploy-c`'s package, which sets no `TOPIC` - so the key
    is decided as with no block, and the row says the selected package sets none - form (3);
    followed (the key set in the selected package), that value wins."""
    def estate(c_value: str | None) -> dict:
        return {**s1(), "deploy-c": {"docker-compose.yml": compose(svc("payments", None, c_value,
                                                                      image="org/x:1"))},
                "__canonical__": [{"service": "payments", "owner": "deploy-c"}]}
    built = build(tmp_path / "a", estate(None))
    assert built.topics == ["topic:b-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == "deploy-a"
    assert row["why"] == (
        "`topic` for `payments` is set here to `a-value`; both are filed under `payments`, the "
        f"view `payments`'s code reads; the workspace's block for `payments` (`{_block_at(row)}`) "
        "selects `deploy-c/docker-compose.yml`, which sets no `topic`, so the graph keeps the last "
        "component's by name, `deploy-b`'s `b-value` - to choose, set `topic` in "
        "`deploy-c/docker-compose.yml`, or change the block to one of: owner = \"deploy-a\" | "
        "owner = \"deploy-b\" | owner = \"deploy-c\"; this one is reported because " + STALE)
    followed = build(tmp_path / "b", estate("c-value"))
    assert followed.topics == ["topic:c-value"]


def test_spellings_a_reader_may_take_alike_are_said_to_be(tmp_path):
    """D24-3: `yes` and `true` differ as written but may be one value - the row's other ending,
    from the readings compose publishes."""
    built = build(tmp_path, {
        "deploy-a": {"docker-compose.yml": compose(image("payments", "yes"))},
        "deploy-b": {"docker-compose.yml": compose(image("payments", "true"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    (row,) = ranking_rows(built)
    assert row["why"].endswith("this one is reported because " + SPELLED), row["why"]


def test_an_unresolved_value_is_never_a_candidate(tmp_path):
    """D24-1: `${MISSING}` was never a value - only `deploy-a`'s resolved one is ranked."""
    built = build(tmp_path, {
        "deploy-a": {"docker-compose.yml": compose(image("payments", "a-value"))},
        "deploy-b": {"docker-compose.yml": compose(image("payments", "${MISSING}"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:a-value"]
    assert ranking_rows(built) == []


def test_a_view_no_component_carries_is_said_to_be_read_by_none(tmp_path):
    """D24-4: `gateway` names no component, so nothing reads the view its values are filed under."""
    built = build(tmp_path, {
        "deploy-a": {"docker-compose.yml": compose(image("gateway", "a-value"))},
        "deploy-b": {"docker-compose.yml": compose(image("gateway", "b-value"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    (row,) = ranking_rows(built)
    assert "both are filed under `gateway`, a view no component's code reads" in row["why"]


def test_two_building_components_contest_across_components_and_the_block_decides(tmp_path):
    """S2, D534-55, D24-13, D24-66: two building components set the key for `payments`, which
    neither builds - the row names both, filed under `payments`' view; with the block, its owner's
    value, and `env-key-from-building-repo` naming the winning file, from the winning candidate's
    `local`."""
    def estate(owner: str | None) -> dict:
        built_a = compose(svc("a", ".", None), image("payments", "a-value"))
        built_b = compose(svc("b", ".", None), image("payments", "b-value"))
        return {"a": {"docker-compose.yml": built_a, "app.py": "x = 1\n"},
                "b": {"docker-compose.yml": built_b, "app.py": "x = 1\n"},
                "payments": {"app/pub.py": producer("TOPIC")},
                **({"__canonical__": [{"service": "payments", "owner": owner}]} if owner else {})}
    built = build(tmp_path / "a", estate(None))
    (row,) = ranking_rows(built)
    assert row["component"] == "a" and "`payments`" in row["why"]
    decided = build(tmp_path / "b", estate("a"))
    assert decided.topics == ["topic:a-value"]
    (origin,) = decided.kinds("env-key-from-building-repo")
    assert origin["origin"] == "a/docker-compose.yml"
    # D24-66: the origin is the winning candidate's, `local` carried - with no block the last
    # component by name, `b`.
    (undecided,) = built.kinds("env-key-from-building-repo")
    assert undecided["origin"] == "b/docker-compose.yml"



# ── groups of two services (D534-54, D24-46, D24-60, D24-67) ───────────────────────────────


def test_a_group_of_two_services_is_decided_by_no_block(tmp_path):
    """D24-46, D24-55, D24-67, D24-105: two components build one directory for two services, a
    block names one of them - a block ranks one service's packages, never two, so the last
    component by name is kept, and the loser's row (form 4), naming the winner's component and
    line, says the block decides nothing here."""
    built = build(tmp_path, {
        "aa-deploy": {"docker-compose.yml": compose(svc("web", "../svc-a", "web-value"))},
        "zz-deploy": {"docker-compose.yml": compose(svc("worker", "../svc-a", "worker-value"))},
        "svc-a": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "worker", "owner": "zz-deploy"}]})
    assert built.topics == ["topic:worker-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == "aa-deploy"
    block = row["why"].split("block for `worker` (`", 1)[1].split("`)", 1)[0]
    assert row["why"] == (
        "`topic` is declared as `web-value` here for `web` and as `worker-value` in `zz-deploy`'s "
        "`docker-compose.yml:5` for `worker`; both are filed under `svc-a`, the view `svc-a`'s "
        "code reads, and the graph keeps `zz-deploy`'s, the last component by name - a block "
        "ranks one service's packages, never two services; the workspace's block for `worker` "
        f"(`{block}`) decides nothing here; to keep one value, leave one declaration of `topic` in "
        "this view; this one is reported because " + STALE)


@pytest.mark.parametrize("first, value, rows", [
    ("worker", "web-value", 1),       # the block decides within `orders`, as on main
    ("web", "worker-value", 1),       # `worker` overwrites `web` there: a group, no block decides
])
def test_one_components_two_services_beside_another_components_web(tmp_path, first, value, rows):
    """D24-60, D24-67: `orders` files `web` and `worker` under one view, `billing` files `web`
    too, a block names `web`. With `worker` first, the block decides within `orders` (`web-value`,
    `main`'s row) and across components the block's package wins; with `web` first, `worker-value`
    overwrites it within `orders`, so the group holds two services, no block decides it, and the
    last component by name is kept."""
    services = [svc("web", ".", "web-value"), svc("worker", ".", "worker-value")]
    if first == "worker":
        services.reverse()
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(*services), "app/pub.py": producer("TOPIC")},
        "billing": {"docker-compose.yml": compose(svc("web", "../orders", "billing-value"))},
        "__canonical__": [{"service": "web", "owner": "orders"}]})
    assert built.topics == ([f"topic:{value}"] if first == "worker" else ["topic:worker-value"])
    assert len(ranking_rows(built)) == rows


def test_two_components_each_holding_two_services_have_one_row_per_loser(tmp_path):
    """D24-69: one candidate per component, so each component that loses across components has
    its row - never one per declaration."""
    built = build(tmp_path, {
        "aa": {"docker-compose.yml": compose(svc("web", "../orders", "a-web"),
                                             svc("worker", "../orders", "a-worker"))},
        "mm": {"docker-compose.yml": compose(svc("web", "../orders", "m-web"),
                                             svc("worker", "../orders", "m-worker"))},
        "zz": {"docker-compose.yml": compose(svc("web", "../orders", "z-web"),
                                             svc("worker", "../orders", "z-worker"))},
        "orders": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:z-worker"]
    # Each row at its component's own winner - `worker`'s value, declared last there.
    assert sorted((r["component"], r["value"]) for r in ranking_rows(built)) == [
        ("aa", "a-worker"), ("mm", "m-worker")]


# ── what the run could not read (D24-5, D24-36, D24-70, D24-90) ───────────────────────────


def test_an_unparsable_compose_file_is_named_in_every_cross_component_row(tmp_path):
    """D24-5, D24-90: a file compose did not read may set the key too, so the whole run's row
    says so - and a narrowed run judges no row at all."""
    estate = {**s1("deploy-a"), "deploy-c": {"docker-compose.yml": "services: [\n"}}
    built = build(tmp_path / "w", estate)
    (row,) = ranking_rows(built)
    assert ("is what the graph keeps, unless `deploy-c/docker-compose.yml`, which was not read, "
            "sets it too; this one") in row["why"], row["why"]
    narrowed = build(tmp_path / "n", estate, "--components", "deploy-a,deploy-b,payments")
    assert narrowed.topics == ["topic:a-value"] and ranking_rows(narrowed) == []


@pytest.mark.parametrize("name", ["Docker-Compose.yml", "docker-compose.YML"])
def test_a_compose_file_lost_to_its_case_is_named_and_a_charts_is_not(tmp_path, name):
    """D24-36: known by its node's `readers` - compose's globs miss it only by case - in either
    spelling; a chart lost to its case adds no clause, since compose would not read it."""
    estate = {**s1("deploy-a"), "deploy-c": {name: compose(image("payments", "c-value"))},
              "charts": {"web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
                         "web/Values.yaml": "fullnameOverride: payments\n"}}
    built = build(tmp_path, estate)
    (row,) = ranking_rows(built)
    assert f"unless `deploy-c/{name}`, which was not read, sets it too" in row["why"], row["why"]
    assert "Values.yaml" not in row["why"]


def test_a_directory_the_walk_could_not_list_is_named_in_every_cross_component_row(tmp_path):
    """D24-70: no node marks it, so `config["unlisted"]` says it - a compose file could sit there
    unread. Whether the refusal binds is measured."""
    from tools.graph.tests._estate import lay_down, read, spawn
    from tools.graph.tests._listing import allow_listing, deny_listing, lists
    lay_down(tmp_path, {**s1("deploy-a"), "deploy-c": {"sub/notes.txt": "x\n"}})
    guarded = tmp_path / "ws" / "deploy-c" / "sub"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        done = spawn(tmp_path)
    finally:
        allow_listing(guarded)
    assert done.returncode == 0, done.stdout + done.stderr
    (row,) = ranking_rows(read(done.returncode, tmp_path / "out"))
    clause = "unless a compose file in `deploy-c/sub`, which could not be listed, sets it too"
    assert (clause in row["why"]) == binds, row["why"]


# ── a narrowed run judges none (D24-33, D24-78 reversed, D24-118, D24-124) ─────────────────


def s3_estate() -> dict:
    """S3: `deploy` sets the key in two files, a block's `path` selecting `docker-compose.a.yml`;
    `deploy-c` sets it too (D24-118, D24-129)."""
    return {"deploy": {"docker-compose.a.yml": compose(image("payments", "a-value")),
                       "docker-compose.b.yml": compose(image("payments", "b-value"))},
            "deploy-c": {"docker-compose.yml": compose(image("payments", "c-value"))},
            "payments": {"app/pub.py": producer("TOPIC")},
            "__canonical__": [{"service": "payments", "owner": "deploy",
                               "path": "docker-compose.a.yml"}]}


def within_rows(built) -> list[dict]:
    return [r for r in built.kinds("superseded-declaration")
            if r.get("extractor") == "compose-services"]


def test_s3_whole_the_blocks_path_wins_and_both_rows_stand(tmp_path):
    """D24-118, D24-129: whole, the block's path selects `deploy/docker-compose.a.yml` - the
    within row at `docker-compose.b.yml` and `deploy-c`'s form (1) row (`main` resolved
    `c-value`)."""
    built = build(tmp_path, s3_estate())
    assert built.topics == ["topic:a-value"]
    assert [(r["component"], r["source_file"]) for r in within_rows(built)] == [
        ("deploy", "docker-compose.b.yml")]
    assert [(r["component"], r["source_file"]) for r in ranking_rows(built)] == [
        ("deploy-c", "docker-compose.yml")]


def test_s3_narrowed_serves_the_within_row_and_judges_no_cross_component_row(tmp_path):
    """D24-33, D24-88, D24-95, D24-118, D24-125: built with `--components payments,deploy` - the
    within row served, no cross-component row, the narrowed line's clause naming them; the view
    still ranked over what the run holds."""
    built = build(tmp_path, s3_estate(), "--components", "payments,deploy")
    assert built.topics == ["topic:a-value"]
    assert [r["source_file"] for r in within_rows(built)] == ["docker-compose.b.yml"]
    assert ranking_rows(built) == []
    import json
    notes = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    line = next(n for n in notes["notes"] if n.startswith("this run was narrowed"))
    assert ("and none of the env ranking's cross-component `superseded-declaration` rows, judged "
            "in a whole run alone") in line


def test_a_load_missing_a_shard_judges_no_cross_component_row(tmp_path):
    """D24-101, D24-107, D24-118: a load takes no `--components`; one missing `deploy-c`'s shard
    is narrowed - the within row served, no cross-component row."""
    import shutil
    assert build(tmp_path, s3_estate(), "--publish-snapshot").code == 0
    shutil.rmtree(tmp_path / "ws" / "deploy-c" / ".sdlc")
    loaded = build(tmp_path, None, "--from-snapshot")
    assert [r["source_file"] for r in within_rows(loaded)] == ["docker-compose.b.yml"]
    assert ranking_rows(loaded) == []


def test_an_only_run_naming_every_reader_withholds_the_rows_too(tmp_path):
    """D24-124: keyed on the narrowing flag, the rule withholds the rows in an `--only` run that
    read compose whole - a cost the line's way on lifts: the whole build judges them."""
    narrowed = build(tmp_path / "n", s1("deploy-a"), "--only",
                     "compose-services,join-by-name,mark-canonical,env-ranking")
    assert narrowed.code == 0
    assert ranking_rows(narrowed) == []
    assert ranking_rows(build(tmp_path / "w", s1("deploy-a")))


# ── two blocks, and the pass over a pre-batch edge (D24-2, D24-19) ─────────────────────────


@pytest.mark.parametrize("order", [("deploy-a", "deploy-b"), ("deploy-b", "deploy-a")])
def test_two_blocks_naming_the_service_agree_on_the_first(tmp_path, order):
    """D24-2: the view, the node decision and the rows all take the first block designating a
    package of the set - `deploying_block`'s one rule."""
    built = build(tmp_path, {**s1(), "__canonical__": [
        {"service": "payments", "owner": order[0]}, {"service": "payments", "owner": order[1]}]})
    first = order[0].split("-")[1]
    assert built.topics == [f"topic:{first}-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == order[1]
    assert not built.kinds("contested-service")


def test_the_pass_judges_no_row_on_a_key_an_edge_without_view_carries(tmp_path):
    """D24-19: a compose edge with no `view` comes from a shard published before the batch - no
    row on its key, and one row naming its component, with the re-publish."""
    from tools.graph.core.model import Edge, Node
    from tools.graph.links.envranking import EnvRankingPass

    class Ctx:
        config = {"narrowed": False, "components": [{"name": "deploy-a", "path": "deploy-a"},
                                                    {"name": "deploy-b", "path": "deploy-b"}]}
    nodes = [Node(id=f"cfg:{c}/docker-compose.yml", type="cfg",
                  props={"kind": "compose", "path": "docker-compose.yml", "component": c})
             for c in ("deploy-a", "deploy-b")]
    edges = [Edge(src=f"cfg:{c}/docker-compose.yml", type="DG_CONFIGURES", dst="envkey:topic",
                  source_file="docker-compose.yml", line=5, extractor="compose-services",
                  props={"service": "payments", "value": v, "raw": v, "declared_as": "TOPIC"})
             for c, v in (("deploy-a", "a"), ("deploy-b", "b"))]
    res = EnvRankingPass().link(Ctx(), nodes, edges)
    assert [r["kind"] for r in res.unresolved] == ["env-ranking-unjudged"]
    (row,) = res.unresolved
    assert row["components"] == ["deploy-a", "deploy-b"]
    assert "re-publish" in row["why"]


def test_no_shard_holds_a_row_quoting_another_components_value(tmp_path):
    """D534-57: the cross-component rows are the link pass's, never a shard's."""
    import json
    assert build(tmp_path, s1("deploy-a"), "--publish-snapshot").code == 0
    shards = list((tmp_path / "ws").glob("*/.sdlc/graph/shard.unresolved.jsonl"))
    assert shards, "the shards' rows were found"
    for shard in shards:
        for line in shard.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert not (row.get("kind") == "superseded-declaration"
                        and row.get("extractor") == "env-ranking"), shard


# ── the decision set (D24-32, D24-54, D24-58, D24-59) ──────────────────────────────────────


def test_a_keyless_package_is_in_the_decision_set_and_the_choices_agree_with_the_contest(
        tmp_path):
    """D24-32: `deploy-c` claims `payments` and sets no `TOPIC` - no `DG_CONFIGURES` edge - yet it
    is one of the packages the decision reads, so the env row offers the contest's own choices."""
    from tools.graph.links.canonical import choice_text
    built = build(tmp_path, {**s1(), "deploy-c": {"docker-compose.yml": compose(
        svc("payments", None, None, image="org/x:1"))}})
    (row,) = ranking_rows(built)
    (contest,) = built.kinds("contested-service")
    offered = " | ".join(choice_text(c) for c in contest["choices"])
    assert offered == 'owner = "deploy-a" | owner = "deploy-b" | owner = "deploy-c"'
    assert row["why"].split("one of: ", 1)[1].split("; this one", 1)[0] == offered


def test_the_moved_rule_never_selects_or_offers_a_held_package():
    """D24-54, D24-59: ruling 19's filter stays in the links-level `select_package` and
    `choices_by_package`, which every caller reaches - `mark-canonical` and the name join alike;
    `_canonical`'s plain form, which compose calls over packages no `[[apart]]` holds, selects
    what it is handed."""
    from tools.graph.core.model import Edge, Node
    from tools.graph.extractors import _canonical
    from tools.graph.links import canonical
    by_id = {f"deploy:{c}/x.yml": Node(id=f"deploy:{c}/x.yml", type="deploy", props={
        "kind": "compose", "path": "x.yml", "component": c}) for c in ("a", "b")}

    def claim(component: str, **props: str) -> tuple[int, Edge]:
        return (0, Edge(src=f"deploy:{component}/x.yml", type="DG_DEPLOYS", dst="svc:web@web",
                        source_file="x.yml", line=1, extractor="compose-services",
                        props={"service": "web", **props}))
    packages = {"a/x.yml": [claim("a", apart="held")], "b/x.yml": [claim("b")]}
    for block in ({"service": "web", "owner": "a"},
                  {"service": "web", "owner": "a", "path": "x.yml"}):
        assert canonical.select_package(packages, block, by_id) == (None, "")
    assert list(canonical.choices_by_package(packages, by_id)) == ["b/x.yml"]
    assert _canonical.select_package_by_claims(
        canonical.plain_claims(packages, by_id), {"service": "web", "owner": "a"}) == (
        "a/x.yml", "owner=a")


def test_the_pass_reads_the_decision_set_per_view_as_extraction_does(tmp_path):
    """D24-58: `deploy-a`'s build files `payments`' settings under `orders`, so the set deciding the
    `payments` view is `deploy-b`'s and `deploy-c`'s packages alone - the block naming `deploy-a`
    decides none of them, and the pass says so, as extraction ranked it: read over every claim on
    the service, it would say the block selects `deploy-a`'s package."""
    built = build(tmp_path, {
        "orders": {"app/pub.py": producer("TOPIC")},
        "payments": {"app/pub.py": producer("TOPIC")},
        "deploy-a": {"docker-compose.yml": compose(svc("payments", "../orders", "own-value"))},
        "deploy-b": {"docker-compose.yml": compose(image("payments", "b-value"))},
        "deploy-c": {"docker-compose.yml": compose(image("payments", "c-value"))},
        "__canonical__": [{"service": "payments", "owner": "deploy-a"}]})
    assert built.topics == ["topic:c-value", "topic:own-value"]
    (row,) = ranking_rows(built)
    assert row["component"] == "deploy-b"
    assert "as no block selects one of these packages" in row["why"], row["why"]
    assert 'one of: owner = "deploy-b" | owner = "deploy-c"; this one' in row["why"]
    assert "deploy-a" not in row["why"]


# ── an anchored block, helm, and a failed reader (D24-1, D24-17, D24-39, D24-124) ──────────


ANCHORED = ("x-env: &env\n  TOPIC: same\nservices:\n"
            "  web:\n    build: '../orders'\n    environment: *env\n"
            "  worker:\n    build: '../orders'\n    environment: *env\n")


def test_two_services_sharing_an_anchored_block_are_no_contest_and_one_origin(tmp_path):
    """D24-17, D24-39: one value from one anchored block, for two services in one view - no row
    of either kind - and one declaration its component's winner, the same each run."""
    def winners(built) -> list[tuple]:
        return sorted((e["props"]["service"], e["source_file"], e["line"])
                      for e in built.edges if e.get("type") == "DG_CONFIGURES"
                      and e["props"].get("wins"))
    estate = {"deploy": {"docker-compose.yml": ANCHORED},
              "deploy-b": {"docker-compose.yml": ANCHORED},
              "orders": {"app/pub.py": producer("TOPIC")}}
    first = build(tmp_path / "1", estate)
    assert first.topics == ["topic:same"]
    assert first.kinds("superseded-declaration") == []
    assert len(winners(first)) == 2, winners(first)       # one per component
    assert winners(build(tmp_path / "2", estate)) == winners(first)


def test_helms_configuration_edges_are_never_ranked(tmp_path):
    """D24-1: the pass reads compose's edges alone, by the reader that publishes them - a
    chart's `DG_CONFIGURES` edges carry no `view`, and are neither ranked nor read as a pre-batch
    shard's."""
    def chart(value: str) -> dict:
        return {"web/values.yaml": "fullnameOverride: payments\n",
                "web/templates/cm.yaml": ("apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
                                          f"  name: h\ndata:\n  TOPIC: {value}\n")}
    built = build(tmp_path, {**s1("deploy-a"), "charts-a": chart("helm-a"),
                             "charts-b": chart("helm-b")})
    assert any(e.get("type") == "DG_CONFIGURES" and e.get("extractor") == "helm-configmaps"
               for e in built.edges), "the charts published configuration edges"
    assert [r["component"] for r in ranking_rows(built)] == ["deploy-b"]
    assert built.kinds("env-ranking-unjudged") == []


def test_a_failed_helm_reader_withholds_the_rows_and_fixing_it_judges_them(tmp_path, monkeypatch):
    """D24-119, D24-124: a whole-workspace build whose helm reader fails reads compose whole, yet
    is narrowed - the rows are withheld, a named cost - and following the line's way on (the
    reader fixed, the build rerun) judges the row."""
    from tools.graph.extractors import helm

    def raising(self, ctx):
        raise RuntimeError("a stub reader failing mid-run")
    # A chart, so the helm reader has a file to fail on.
    estate = {**s1("deploy-a"), "charts": {
        "web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
        "web/values.yaml": "fullnameOverride: web\n"}}
    monkeypatch.setattr(helm.DeploymentsExtractor, "extract", raising)
    failed = build(tmp_path / "1", estate)
    assert failed.topics == ["topic:a-value"]
    assert ranking_rows(failed) == []
    monkeypatch.undo()
    fixed = build(tmp_path / "2", estate)
    assert [r["component"] for r in ranking_rows(fixed)] == ["deploy-b"]


def test_a_load_over_shards_a_block_edit_staled_judges_no_cross_component_row(tmp_path):
    """D24-102, D24-113, D24-120: a block edited after the publish stales every shard, so the
    load is narrowed - no cross-component row, the line naming them - where the whole build judges
    it."""
    import json
    assert build(tmp_path, s1("deploy-a"), "--publish-snapshot").code == 0
    toml = tmp_path / "ws" / "workspace.toml"
    toml.write_bytes(toml.read_bytes().replace(b'owner = "deploy-a"', b'owner = "deploy-b"'))
    loaded = build(tmp_path, None, "--from-snapshot")
    assert ranking_rows(loaded) == []
    notes = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    assert any("none of the env ranking's cross-component `superseded-declaration` rows" in n
               for n in notes["notes"]), notes["notes"]


def test_an_incremental_build_decides_over_every_components_candidate(tmp_path):
    """D24-15: editing one setter rebuilds it and its readers, and the shared cache's candidates
    fill as in a full build (`cache_exts` runs compose everywhere for a rebuilt reader) - the owner
    read first keeps its new value, and the loser's row carries its new one."""
    estate = s1("deploy-a")
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    ws = tmp_path / "ws"
    for component, old, new, loser in (("deploy-a", b"a-value", b"a2-value", "b-value"),
                                       ("deploy-b", b"b-value", b"b2-value", "b2-value")):
        f = ws / component / "docker-compose.yml"
        f.write_bytes(f.read_bytes().replace(old, new))
        again = build(tmp_path, None, "--incremental", "--publish-snapshot")
        assert again.code == 0
        assert again.topics == ["topic:a2-value"], component
        assert [(r["component"], r["value"]) for r in ranking_rows(again)] == [
            ("deploy-b", loser)], component


def test_a_file_an_include_names_that_another_component_reads_is_never_called_unread(tmp_path):
    """Review B24-2: `deploy-c`'s `include:` names `other`'s compose file, which `other` reads as
    its own - its value is one of the candidates - so no row says it "was not read", nor spells it
    from the including component's root."""
    built = build(tmp_path, {
        **s1("deploy-a"),
        "other": {"docker-compose.yml": compose(image("payments", "other-value"))},
        "deploy-c": {"docker-compose.yml": "include:\n  - ../other/docker-compose.yml\n"
                                           "services: {}\n"}})
    rows = ranking_rows(built)
    assert sorted(r["component"] for r in rows) == ["deploy-b", "other"]
    assert all("which was not read" not in r["why"] for r in rows), [r["why"] for r in rows]
