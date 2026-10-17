"""Keying (decision 55, design pass 24 - ruling 10 revised): a declaration's settings reach the code
its own build places its claim on, where that build's context is a component's own directory
(`exact`), by path arithmetic, never the filesystem; a context inside a component - the declaring
one's (`own`) or another's (`within`) - an unplaced build, and a declaration with no build of its
own key by the name rule, as on `main`; a T588 override takes its base's, and a `-f` layer keys
with its component's placed builds in other files where they agree on one component, exactly.
The cells below are `main`'s measured outcomes where the keying keeps them, and the design's where
it moves them (each a recorded delta, `plans/b24-evidence/b24_ledger.md`).

Across components (T590, design pass 24, section 2): one decision - the workspace's block over the
compose packages claiming the service, else the last component by name - and the losers' rows are
`env-ranking`'s, judged in a whole run alone (`test_env_ranking.py`).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import (  # noqa: E402
    build, compose, lay_down, producer, read, spawn, svc)
import tools.graph.build as B  # noqa: E402

ENV_ROWS = ("env-key-unset", "superseded-declaration", "entrypoint-unmatched",
            "env-key-from-building-repo")


def env(built) -> tuple[list[str], list[str]]:
    """What a component's code resolved: the topics, and each env row as kind:where."""
    return built.topics, sorted(f"{r['kind']}:{r['component']}/{r['source_file']}"
                                for r in built.kinds(*ENV_ROWS))


def image(name: str, value: str) -> str:
    return svc(name, None, value, image="org/x:1")


# ── keying A: the shapes the round-7 review measured (D534-62 to D534-64) ────────────────

SHAPES = {
    "a subdirectory build files under the service's name": (
        {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a")),
                      "app/pub.py": producer("TOPIC")}},
        [], ["env-key-unset:platform/app/pub.py"]),
    "the component named after the service reads it": (
        {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a"))},
         "web": {"app/pub.py": producer("TOPIC")}},
        ["topic:web-value"], []),
    "a stock container built from a subdirectory keeps its own name (the dev leak)": (
        {"platform": {"docker-compose.yml": compose(
            svc("web", ".", None), svc("redis", "./redis-custom", "1gb", key="MAXMEMORY")),
            "app/cache.py": producer("MAXMEMORY")}},
        [], ["env-key-unset:platform/app/cache.py"]),
    "a -f layer's value meets its building file's in the built component": (
        {"orders": {"docker-compose.yml": compose(svc("web", ".", "base-value")),
                    "docker-compose.zz.yml": compose(svc("web", None, "layer-value")),
                    "app/pub.py": producer("TOPIC")}},
        ["topic:layer-value"], []),
    "a cross-component build reaches the code it is built from (S7)": (
        {"deploy": {"docker-compose.yml": compose(svc("web", "../orders"))},
         "orders": {"app/pub.py": producer("TOPIC")}},
        ["topic:web-value"], []),
    "a within placement does not join the holder's environment": (
        {"platform": {"svc-a/Dockerfile": "FROM scratch\n", "app/pub.py": producer("TOPIC")},
         "platform/svc-a": {},
         "deploy": {"docker-compose.yml": compose(svc("b", "../platform/svc-ab", "b-value"))}},
        [], ["env-key-unset:platform/app/pub.py"]),
    "an entrypoint on a value-derived topic still matches": (
        {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a"))},
         "web": {"app/pub.py": producer("TOPIC")}, "__entrypoint__": ["topic:web-value"]},
        ["topic:web-value"], []),
    "two services built from two subdirectories keep two views": (
        {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a"),
                                                    svc("worker", "./svc-b", "worker-value")),
                      "app/pub.py": producer("TOPIC")}},
        [], ["env-key-unset:platform/app/pub.py"]),
    "a block for one of them names no other service's value": (
        {"orders": {"docker-compose.yml": compose(svc("web", "./svc-a"),
                                                  svc("worker", "./svc-b", "worker-value")),
                    "app/pub.py": producer("TOPIC")},
         "__canonical__": [{"service": "worker", "owner": "orders"}]},
        [], ["env-key-unset:orders/app/pub.py"]),
    "two deploy repos building one directory under two names": (
        {"aa-deploy": {"docker-compose.yml": compose(svc("web", "../svc-a"))},
         "zz-deploy": {"docker-compose.yml": compose(svc("worker", "../svc-a", "worker-value"))},
         "svc-a": {"app/pub.py": producer("TOPIC")},
         "__canonical__": [{"service": "worker", "owner": "zz-deploy"}]},
        ["topic:worker-value"], ["superseded-declaration:aa-deploy/docker-compose.yml"]),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_built_services_settings_resolve_as_on_main(tmp_path, shape):
    estate, topics, rows = SHAPES[shape]
    built = build(tmp_path, estate)
    assert built.code == 0
    assert env(built) == (topics, rows)


# ── keying A's cells beyond the shapes (decision 55) ─────────────────────────────────────


def test_an_overrides_settings_reach_the_component_its_base_builds(tmp_path):
    """D534-62's layer shape: the override takes its base's claim, its view included, so its
    values reach the code the base builds - `main` filed them under the service's name."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.yml": compose(svc("web", "../orders", None)),
                   "docker-compose.override.yml": compose(svc("web", None, "ov-value"))},
        "orders": {"app/pub.py": producer("TOPIC")}})
    assert env(built) == (["topic:ov-value"], [])


def test_a_f_layer_keys_with_its_components_build_in_another_file(tmp_path):
    """D24-20, D24-47: `docker-compose.zz.yml` declares no build of `web`, which `deploy`'s other
    file builds from `../orders` - the two files' values are ranked as one component's, the
    layer's last in segment order."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.yml": compose(svc("web", "../orders", "base-value")),
                   "docker-compose.zz.yml": compose(svc("web", None, "zz-value"))},
        "orders": {"app/pub.py": producer("TOPIC")}})
    assert env(built) == (["topic:zz-value"], [])


def test_an_unplaced_builder_keys_by_name_and_the_layer_follows_the_placed_one(tmp_path):
    """D24-68, D24-84: a build nothing could place is no placed build - its settings key by the
    name rule, reaching `web`, the component named like the service, which is told where the
    value came from (`env-key-from-building-repo`: a building repo's service whose build it does
    not place) - while the `-f` layer keys with the placed build, reaching `orders`."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.a.yml": compose(svc("web", "../orders", None)),
                   "docker-compose.b.yml": compose(svc("web", "../nope", "nope-value")),
                   "docker-compose.zz.yml": compose(svc("web", None, "zz-value"))},
        "orders": {"app/pub.py": producer("TOPIC")},
        "web": {"app/pub.py": producer("TOPIC")}})
    assert env(built) == (["topic:nope-value", "topic:zz-value"],
                          ["env-key-from-building-repo:web/app/pub.py"])


def test_the_own_cell_beside_a_f_layer_keeps_mains_contest_under_a_block(tmp_path):
    """D24-47, D24-137: a build from a directory inside `deploy` keys by the name rule, and so does
    the layer beside it - both to `payments`, contesting in `deploy` as on `main`: the block's file
    wins, the layer's value is its row, and `payments` reads the block's value."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.a.yml": compose(svc("payments", "./svc", "own-value")),
                   "docker-compose.b.yml": compose(svc("payments", None, "b-value")),
                   "svc/Dockerfile": "FROM scratch\n"},
        "payments": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "payments", "owner": "deploy",
                           "path": "docker-compose.a.yml"}]})
    assert env(built) == (["topic:own-value"],
                          ["superseded-declaration:deploy/docker-compose.b.yml"])


def test_a_layer_over_builds_in_two_components_keys_by_name(tmp_path):
    """`_claim`'s ambiguous branch: the layer's builds place the service in two components, so it
    keys by the name rule, as on `main` - neither component reads the layer's value."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.a.yml": compose(svc("web", "../orders", None)),
                   "docker-compose.b.yml": compose(svc("web", "../billing", None)),
                   "docker-compose.zz.yml": compose(svc("web", None, "zz-value"))},
        "orders": {"app/pub.py": producer("TOPIC")},
        "billing": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == []
    assert built.kinds("compose-build-ambiguous")


def test_an_override_takes_its_bases_view_beside_a_third_building_file(tmp_path):
    """T588 decides an override by its base, never by the component's other files: with `prod`
    building `web` elsewhere, the override still configures what its base builds."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.yml": compose(svc("web", "../orders", None)),
                   "docker-compose.override.yml": compose(svc("web", None, "ov-value")),
                   "docker-compose.prod.yml": compose(svc("web", "../billing", None))},
        "orders": {"app/pub.py": producer("TOPIC")},
        "billing": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:ov-value"]
    assert built.deploy("deploy/docker-compose.override.yml")["dst"] == "svc:web@orders"


def test_an_exact_and_a_within_build_beside_a_layer_key_it_by_name(tmp_path):
    """D24-57: the layer's builds do not all place it in one component exactly - a `within` is an
    inference, which never carries settings to code - so it keys by the name rule."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.a.yml": compose(svc("web", "../orders", None)),
                   "docker-compose.b.yml": compose(svc("web", "../platform/svc-x", None)),
                   "docker-compose.zz.yml": compose(svc("web", None, "zz-value"))},
        "orders": {"app/pub.py": producer("TOPIC")},
        "platform": {"svc-x/Dockerfile": "FROM scratch\n", "app/pub.py": producer("TOPIC")}})
    assert "topic:zz-value" not in built.topics


# ── files are read in main's order (fails on the v5 to v9 prototypes) ────────────────────


@pytest.mark.parametrize("files", [
    ("docker-compose.yml", "docker/compose.yml"),
    ("compose.yaml", "compose/compose.override.yaml"),
])
def test_files_are_read_in_mains_order_so_the_same_value_is_read_last(tmp_path, files):
    """Main reads `sorted(ctx.inputs(repo))`, path by path, so `docker/compose.yml` comes
    before `docker-compose.yml`; POSIX text puts it after, and a value changed with it. Segment
    order keeps `main`'s answer for both nested pairs (D24-136)."""
    root_file, dir_file = files
    built = build(tmp_path, {
        "deploy": {root_file: compose(image("payments", "root-value")),
                   dir_file: compose(image("payments", "dir-value"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:root-value"]


@pytest.mark.parametrize("names, value", [
    (("docker-compose.a.yml", "docker-compose.B.yml"), "a-value"),       # S4
    (("docker-compose.PROD.yml", "docker-compose.dev.yml"), "dev-value"),
])
def test_files_are_read_in_segment_order_on_every_platform(tmp_path, names, value):
    """D534-74, D24-14, D24-143: a component's files are read in their POSIX paths' segment order,
    case-sensitively, on every platform - `main` compared the platform's `Path`, which folds case
    on Windows, so the file read last, and the value kept, differed by machine."""
    first, second = names
    built = build(tmp_path, {
        "deploy": {first: compose(image("payments", first.split(".")[1] + "-value")),
                   second: compose(image("payments", second.split(".")[1] + "-value"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == [f"topic:{value}"]


@pytest.mark.parametrize("third, value", [
    (None, "override-value"),                        # S9
    ("docker-compose.a.yml", "override-value"),      # the pair after the file sorting first
    ("docker-compose.zz.yml", "zz-value"),           # the pair before the file sorting last
])
def test_an_override_is_read_right_after_its_base(tmp_path, third, value):
    """D24-11, D24-27, D24-29: segment order alone puts `docker-compose.override.yml` before
    `docker-compose.yml`; compose merges the override over its base, so it is read right after
    it - its value kept, with no row - and the pair keeps its place among the other files."""
    files = {"docker-compose.yml": compose(image("payments", "base-value")),
             "docker-compose.override.yml": compose(image("payments", "override-value"))}
    if third:
        files[third] = compose(image("payments", third.split(".")[1] + "-value"))
    built = build(tmp_path, {"deploy": files, "payments": {"app/pub.py": producer("TOPIC")}})
    assert env(built) == ([f"topic:{value}"], [])


# ── two services building one root share its view, as on main (D534-54, D534-64) ─────────


def test_a_within_placements_value_edit_drifts_only_the_editing_components_shard(tmp_path):
    """D534-65, as a process: a `within` placement decides the claim's node, never which
    component's environment its value joins, so editing that value drifts only the file's
    own component - as on main, and unlike the v9 prototype, which drifted the holder too."""
    lay_down(tmp_path, SHAPES["a within placement does not join the holder's environment"][0])
    published = spawn(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    clean = spawn(tmp_path, "--check-snapshot", out="out1")
    assert clean.returncode == 0, ("the unedited snapshot is clean (C534-12): "
                                   + clean.stdout + clean.stderr)
    compose_file = tmp_path / "ws" / "deploy" / "docker-compose.yml"
    compose_file.write_bytes(compose_file.read_bytes().replace(b"b-value", b"b-value2"))
    checked = spawn(tmp_path, "--check-snapshot", out="out2")
    drifted = sorted(line.split("DRIFT in ", 1)[1].split(" ", 1)[0]
                     for line in checked.stdout.splitlines() if "snapshot: DRIFT in " in line)
    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert drifted == ["deploy"]


def test_two_services_building_one_root_share_its_view(tmp_path):
    estate = {"orders": {"docker-compose.yml": compose(svc("web", ".", "web-value"),
                                                       svc("worker", ".", "worker-value")),
                         "app/pub.py": producer("TOPIC")}}
    assert env(build(tmp_path / "a", estate)) == (["topic:worker-value"], [])
    blocked = build(tmp_path / "b", {**estate, "__canonical__": [
        {"service": "worker", "owner": "orders"}]})
    assert env(blocked) == (["topic:worker-value"],
                            ["superseded-declaration:orders/docker-compose.yml"])
    row, = blocked.kinds("superseded-declaration")
    assert (row["line"], row["value"]) == (5, "web-value")
    # D534-54, D24-128: facts only - never `web`'s value called a declaration of `worker`.
    assert row["why"] == (
        "`topic` is filed under `orders` as `web-value` here for `web` and as `worker-value` at "
        "`docker-compose.yml:9` for `worker`; one declaration of `topic` per view reaches the "
        "code, and this one is not it. Both containers still run with their own value; to keep "
        "this one in the graph, make it the only declaration of `topic` filed under `orders`")


# ── main's own rule for the env view, across every build: spelling (D534-71, D534-72) ────

ORDERS_ROOT = "@@ORDERS@@"
RESOLVES, UNSET = True, False
SPELLINGS = {
    "dot": ("    build: .\n", RESOLVES),
    "dot slash": ("    build: ./\n", RESOLVES),
    "padded dot": ('    build: " . "\n', RESOLVES),
    "empty string": ('    build: ""\n', UNSET),
    "bare null": ("    build:\n", UNSET),
    # a mapping with no context builds the project directory - here the component's root
    "map, no context": ("    build:\n      dockerfile: Dockerfile\n", RESOLVES),
    "map, null context": ("    build:\n      context:\n      dockerfile: Dockerfile\n", RESOLVES),
    "map, tilde context": ("    build: {context: ~}\n", RESOLVES),
    "map, empty map": ("    build: {}\n", RESOLVES),
    "map, context dot": ("    build:\n      context: .\n", RESOLVES),
    "map, context empty": ('    build:\n      context: ""\n', UNSET),
    "map, context ../orders": ("    build:\n      context: ../orders\n", RESOLVES),
    "../orders": ("    build: ../orders\n", RESOLVES),
    # paths are matched exactly, never on the filesystem: a case twin keys by the name rule
    "../ORDERS": ("    build: ../ORDERS\n", UNSET),
    "../Orders": ("    build: ../Orders\n", UNSET),
    "./svc-a": ("    build: ./svc-a\n", UNSET),
    "./nope": ("    build: ./nope\n", UNSET),
    "..": ("    build: ..\n", UNSET),
    "../other": ("    build: ../other\n", UNSET),
    "interpolated": ("    build: ${CTX}\n", UNSET),
    "url": ("    build: https://github.com/x/y.git\n", UNSET),
    "list": ("    build: [a, b]\n", UNSET),
    "int": ("    build: 5\n", UNSET),
    # path arithmetic reads a backslash as a separator on every platform (D24-145)
    "backslash dot": ("    build: '.\\'\n", RESOLVES),
    # an absolute path is placed nowhere, so it keys by the name rule (D24-154)
    "absolute own root": (f"    build: '{ORDERS_ROOT}'\n", UNSET),
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_the_env_view_follows_the_builds_placement_for_every_build_spelling(tmp_path, spelling):
    """Decision 55: a value reaches the component's own code exactly where its build is placed
    on the component's own directory, by path arithmetic - the same on every platform."""
    build_yaml, resolves = SPELLINGS[spelling]
    build_yaml = build_yaml.replace(ORDERS_ROOT, str(tmp_path / "ws" / "orders"))
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": ("services:\n  web:\n" + build_yaml
                                          + "    environment:\n      TOPIC: web-value\n"),
                   "app/pub.py": producer("TOPIC"), "svc-a/Dockerfile": "FROM scratch\n"},
        "other": {"README.md": "x\n"}})
    assert built.code == 0
    if resolves:
        assert env(built) == (["topic:web-value"], [])
    else:
        assert env(built) == ([], ["env-key-unset:orders/app/pub.py"])


# ── a block in either spelling ranks the component's env (D534-4, D534-24): T534's change ─


def test_a_block_in_the_ids_spelling_ranks_env_values_within_the_component(tmp_path):
    """Main decided the edges with a block spelled as the id (`my-api`) but not the env,
    so the value read last won; now one block decides both halves. Fails on main."""
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(svc('"my api"', ".", "a1")),
                   "docker-compose.prod.yml": compose(svc('"my api"', ".", "a2")),
                   "app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "my-api", "owner": "orders",
                           "path": "docker-compose.prod.yml"}]})
    assert env(built) == (["topic:a2"], ["superseded-declaration:orders/docker-compose.yml"])
    assert not built.kinds("contested-service"), "the block decides the contest too"


def test_the_one_service_row_claims_no_value_kept_where_another_service_sets_it(tmp_path):
    """D24-123: a third file's `cron`, filed under the same view, sets the key after the owner's -
    so the row for the superseded declaration says it is not kept, never that the owner's value
    is what the graph uses (here it is `cron`'s)."""
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(svc("web", ".", "owner-value")),
                   "docker-compose.prod.yml": compose(svc("web", ".", "stale-value")),
                   "docker-compose.zz.yml": compose(svc("cron", ".", "cron-value")),
                   "app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "web", "owner": "orders", "path": "docker-compose.yml"}]})
    assert built.topics == ["topic:cron-value"]
    (row,) = built.kinds("superseded-declaration")
    assert row["source_file"] == "docker-compose.prod.yml"
    assert row["why"] == (
        "`topic` is declared as 'stale-value' here and as 'owner-value' in docker-compose.yml, "
        "which the workspace names as the owner of service `web`, so this declaration is not "
        "kept; it is reported because a stale deployment nobody has noticed is a finding, not "
        "noise")


#: Round 8's block shapes: two services of one component filed under one view, each block shape
#: in both declaration orders (D24-80, D24-87, D24-93, D24-94, D24-97).
TWO_SERVICES_BLOCKS = {"none": [], "web": ["web"], "worker": ["worker"],
                       "both": ["web", "worker"]}


@pytest.mark.parametrize("order", ["web-first", "worker-first"])
@pytest.mark.parametrize("blocks", sorted(TWO_SERVICES_BLOCKS))
def test_the_two_services_row_states_facts_in_every_block_shape(tmp_path, blocks, order):
    """Where the row is emitted its words are factual - both values, where the other is, no block
    and no `main` in them - and its way on, followed, leaves no contest: one service's declaration
    of the key, the only one filed under the view, its value kept."""
    services = [svc("web", ".", "web-value"), svc("worker", ".", "worker-value")]
    if order == "worker-first":
        services.reverse()
    estate = {"orders": {"docker-compose.yml": compose(*services),
                         "app/pub.py": producer("TOPIC")},
              "__canonical__": [{"service": name, "owner": "orders"}
                                for name in TWO_SERVICES_BLOCKS[blocks]]}
    rows = build(tmp_path / "a", estate).kinds("superseded-declaration")
    for row in rows:
        assert "is filed under `orders` as" in row["why"], row["why"]
        assert "web-value" in row["why"] and "worker-value" in row["why"]
        assert "block" not in row["why"] and "main" not in row["why"]
        assert "make it the only declaration of `topic` filed under `orders`" in row["why"]
    for row in rows:
        # Followed: the other service's declaration of the key removed - this one the only one.
        kept = "web" if row["value"] == "web-value" else "worker"
        other = "worker" if kept == "web" else "web"
        followed = {**estate, "orders": {**estate["orders"], "docker-compose.yml": compose(
            svc(kept, ".", f"{kept}-value"), svc(other, ".", None))}}
        after = build(tmp_path / f"followed-{kept}", followed)
        assert after.topics == [f"topic:{kept}-value"]
        assert not after.kinds("superseded-declaration")


def test_the_two_services_row_cites_a_kept_declaration_above_it(tmp_path):
    """D24-114: the other declaration named by file and line, whichever way it lies - here above
    the row in the same file: `worker`'s block owns the view's key first, and `web`, whose block
    designates another file, is contested and not the owner, so its value is the row."""
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(svc("worker", ".", "worker-value"),
                                                 svc("web", ".", "web-value")),
                   "docker-compose.prod.yml": compose(svc("web", ".", None)),
                   "app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "worker", "owner": "orders"},
                          {"service": "web", "owner": "orders",
                           "path": "docker-compose.prod.yml"}]})
    (row,) = built.kinds("superseded-declaration")
    assert row["line"] == 9 and "at `docker-compose.yml:5` for `worker`" in row["why"], row["why"]


# ── within one component as on main; across components, one decision (T590) ──────────────

PANTRY = Path(__file__).resolve().parent / "fixtures" / "pantry"
MAINS_PANTRY_WHY = (
    "`restock.queue` is declared as 'restock-events-v1' here and as 'restock-events-v2' in "
    "docker-compose.yml, which the workspace names as the owner of service `pantry-api`, so "
    "this declaration is not kept; it is reported because a stale deployment nobody has "
    "noticed is a finding, not noise")


def test_within_one_component_the_superseded_row_states_facts_only(tmp_path):
    """Pantry's row is within one component and keeps `main`'s text but for its one clause the
    design makes false - "The owner's value is what the graph uses", which another service of
    the view or another component can contradict (D24-56, D24-123, D24-130)."""
    code = B.main(["--workspace", str(PANTRY / "workspace.toml"), "--out", str(tmp_path),
                   "--run-id", "fixed"])
    row, = read(code, tmp_path).kinds("superseded-declaration")
    assert (row["component"], row["source_file"], row["line"]) == (
        "deploy-compose", "legacy/docker-compose.yml", 15)
    assert row["why"] == MAINS_PANTRY_WHY


def two_deploy_repos(owner: str) -> dict:
    return {"deploy-a": {"docker-compose.yml": compose(image("payments", "a-value"))},
            "deploy-b": {"docker-compose.yml": compose(image("payments", "b-value"))},
            "payments": {"app/pub.py": producer("TOPIC")},
            "__canonical__": [{"service": "payments", "owner": owner}]}


def test_across_components_the_row_cites_the_dropped_declaration_and_the_block(tmp_path):
    """D24-63, D24-138: the owner's value wins whatever the read order, and the other component's
    row - the pass's form (1) - names its value where it is written and the block that decided;
    `main`'s "not yet ranked against each other" is gone."""
    built = build(tmp_path, two_deploy_repos("deploy-b"))
    assert built.topics == ["topic:b-value"]
    row, = built.kinds("superseded-declaration")
    assert (row["component"], row["source_file"], row["line"], row["value"]) == (
        "deploy-a", "docker-compose.yml", 5, "a-value")
    assert "selects `deploy-b/docker-compose.yml`" in row["why"]
    assert "not yet ranked against each other" not in row["why"]


def test_with_the_owner_read_first_the_owners_value_still_wins(tmp_path):
    """D534-48, D534-67 (T590): the owner read first is no longer replaced by a component read
    later - the block decides, and the later component's value is a row."""
    built = build(tmp_path, two_deploy_repos("deploy-a"))
    assert built.topics == ["topic:a-value"]
    row, = built.kinds("superseded-declaration")
    assert (row["component"], row["value"]) == ("deploy-b", "b-value")


@pytest.mark.parametrize("owner", ["zz", "mm", "aa"])
def test_three_components_the_owners_value_wins_in_every_arrival_order(tmp_path, owner):
    """D534-53, D24-53a: the owner read last, middle or first wins every time, and every losing
    component has its row - `main` rowed only the value read last against the owner."""
    built = build(tmp_path, {
        **{f"{c}-deploy": {"docker-compose.yml": compose(image("payments", f"{c}-value"))}
           for c in ("aa", "mm", "zz")},
        "payments": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "payments", "owner": f"{owner}-deploy"}]})
    assert built.topics == [f"topic:{owner}-value"]
    assert sorted(r["component"] for r in built.kinds("superseded-declaration")) == sorted(
        f"{c}-deploy" for c in ("aa", "mm", "zz") if c != owner)


def test_a_later_component_no_longer_replaces_the_owners_value(tmp_path):
    """D534-67, D24-130: the block's `path` selects `aa/docker-compose.yml` across components too,
    so `a1` is what the graph keeps - `main` let `zz-deploy`, read later, replace it - with the
    within row for `a2` (compose's) and `zz-deploy`'s form (1) row (the pass's)."""
    built = build(tmp_path, {
        "aa": {"docker-compose.yml": compose(image("payments", "a1")),
               "docker-compose.zz.yml": compose(image("payments", "a2"))},
        "zz-deploy": {"docker-compose.yml": compose(image("payments", "z-value"))},
        "payments": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "payments", "owner": "aa", "path": "docker-compose.yml"}]})
    assert built.topics == ["topic:a1"]
    rows = sorted((r["extractor"], r["component"], r["source_file"], r["value"])
                  for r in built.kinds("superseded-declaration"))
    assert rows == [("compose-services", "aa", "docker-compose.zz.yml", "a2"),
                    ("env-ranking", "zz-deploy", "docker-compose.yml", "z-value")]
    within = next(r for r in built.kinds("superseded-declaration") if r["component"] == "aa")
    assert "so this declaration is not kept" in within["why"]


# ── the env-key cells a keying moves (D24-61) ───────────────────────────────────────────────


def test_a_build_placed_on_another_components_directory_leaves_the_same_named_one_unset(
        tmp_path):
    """D24-61: `deploy` builds `payments` from `orders`' own directory, so its settings reach
    `orders`' code - and `payments`, which `main` gave the value by the name rule, reads it unset;
    a `-f` layer follows its building file there."""
    for files in ({"docker-compose.yml": compose(svc("payments", "../orders", "x-value"))},
                  {"docker-compose.yml": compose(svc("payments", "../orders", None)),
                   "docker-compose.prod.yml": compose(svc("payments", None, "x-value"))}):
        built = build(tmp_path / str(len(files)), {
            "deploy": files, "orders": {"app/pub.py": producer("TOPIC")},
            "payments": {"app/pub.py": producer("TOPIC")}})
        assert built.topics == ["topic:x-value"]
        assert [r["component"] for r in built.kinds("env-key-unset")] == ["payments"]


def test_a_filesystem_only_context_gives_the_same_named_component_its_value_with_a_row(
        tmp_path):
    """D24-61: `../DEPLOY` reaches `deploy`'s own directory only through a filesystem that folds
    case - `main` keyed the settings to `deploy` there on Windows, and gave `payments` nothing - so
    nothing places the build and its settings key by name on every platform: `payments` reads
    them, and `env-key-from-building-repo` says they come from a build that file does not
    place."""
    built = build(tmp_path, {
        "deploy": {"docker-compose.yml": compose(svc("payments", "../DEPLOY", "x-value"))},
        "orders": {"app/pub.py": producer("TOPIC")},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:x-value"]
    (row,) = built.kinds("env-key-from-building-repo")
    assert row["component"] == "payments" and row["origin"] == "deploy/docker-compose.yml"
    assert "whose build that file does not place" in row["why"]


# ── the two-services row, true whatever wins later (D24-103 to D24-116) ───────────────────

TWO_SERVICES_WHY = (
    "`topic` is filed under `orders` as `web-value` here for `web` and as `worker-value` at "
    "`docker-compose.yml:9` for `worker`; one declaration of `topic` per view reaches the code, "
    "and this one is not it. Both containers still run with their own value; to keep this one in "
    "the graph, make it the only declaration of `topic` filed under `orders`")


def two_services(**extra) -> dict:
    return {"orders": {"docker-compose.yml": compose(svc("web", ".", "web-value"),
                                                     svc("worker", ".", "worker-value")),
                       "app/pub.py": producer("TOPIC"), **extra},
            "__canonical__": [{"service": "worker", "owner": "orders"}]}


def test_the_two_services_row_is_true_where_another_component_wins(tmp_path):
    """D24-103, D24-104, D24-116: `orders` writes its row before `zz` is read, and `zz` wins the
    view across components - the row says only what holds within `orders`: where the other
    declaration is and that this one is not it, never a value kept nor a cross-component row."""
    built = build(tmp_path, {**two_services(),
                             "zz": {"docker-compose.yml": compose(svc("web", "../orders",
                                                                      "zz-value"))}})
    assert built.topics == ["topic:zz-value"]
    (row,) = [r for r in built.kinds("superseded-declaration")
              if r["extractor"] == "compose-services"]
    assert row["why"] == TWO_SERVICES_WHY


def test_the_two_services_row_is_true_where_a_third_services_later_file_wins(tmp_path):
    """D24-115: `cron`, in a file read later, overwrites the key within `orders` - the row still
    says only where `worker`'s declaration is, and that this one is not it."""
    built = build(tmp_path, two_services(**{"docker-compose.zz.yml": compose(
        svc("cron", ".", "cron-value"))}))
    assert built.topics == ["topic:cron-value"]
    (row,) = built.kinds("superseded-declaration")
    assert row["why"] == TWO_SERVICES_WHY


def test_three_services_in_one_view_follow_the_rows_way_on_in_one_step(tmp_path):
    """D24-111, D24-128: three services in one view, a block for `cron` - each row's way on,
    followed (this declaration made the only one), keeps its value in one step, no contest left."""
    names = ("web", "worker", "cron")
    estate = {"orders": {"docker-compose.yml": compose(*(svc(n, ".", f"{n}-value")
                                                         for n in names)),
                         "app/pub.py": producer("TOPIC")},
              "__canonical__": [{"service": "cron", "owner": "orders"}]}
    rows = build(tmp_path / "a", estate).kinds("superseded-declaration")
    assert rows
    for row in rows:
        assert "make it the only declaration of `topic` filed under `orders`" in row["why"]
        kept = next(n for n in names if row["value"] == f"{n}-value")
        followed = {**estate, "orders": {**estate["orders"], "docker-compose.yml": compose(
            *(svc(n, ".", f"{n}-value" if n == kept else None) for n in names))}}
        after = build(tmp_path / f"followed-{kept}", followed)
        assert after.topics == [f"topic:{kept}-value"]
        assert not after.kinds("superseded-declaration")
