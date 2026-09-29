"""Ruling 10: every environment value resolves exactly as on main (T534, T579).

T579 moves a built service's NODE onto the component it is built from; it never moves which
component's code sees the service's settings. The env view is main's own rule
(`compose.env_view`), kept apart from placement, so a value resolves as it did whatever the
placement. Every expectation below is main's measured outcome, so most of these tests PIN
main's behaviour (they pass on main too). Three do not: the read-order tests fail on the
v5 to v9 prototypes, which sorted compose files as POSIX text, and the block-spelling test
is T534's one intended change to a value (D534-4, D534-24).

Ruling 9: compose ranks env values within one component, as on main; across components the
value read last wins, which T590 carries.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import (  # noqa: E402
    build, compose, lay_down, producer, read, spawn, svc)
import tools.graph.build as B  # noqa: E402

WINDOWS = os.name == "nt"
ENV_ROWS = ("env-key-unset", "superseded-declaration", "entrypoint-unmatched",
            "env-key-from-building-repo")


def env(built) -> tuple[list[str], list[str]]:
    """What a component's code resolved: the topics, and each env row as kind:where."""
    return built.topics, sorted(f"{r['kind']}:{r['component']}/{r['source_file']}"
                                for r in built.kinds(*ENV_ROWS))


def image(name: str, value: str) -> str:
    return svc(name, None, value, image="org/x:1")


# ── ruling 10: the shapes the round-7 review measured (D534-62 to D534-64) ───────────────

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
    "a layer's value stays under the service's name": (
        {"orders": {"docker-compose.yml": compose(svc("web", ".", "base-value")),
                    "docker-compose.zz.yml": compose(svc("web", None, "layer-value")),
                    "app/pub.py": producer("TOPIC")}},
        ["topic:base-value"], []),
    "a cross-component build under a differing name": (
        {"deploy": {"docker-compose.yml": compose(svc("web", "../orders"))},
         "orders": {"app/pub.py": producer("TOPIC")}},
        [], ["env-key-unset:orders/app/pub.py"]),
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
        [], ["env-key-unset:svc-a/app/pub.py"]),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_built_services_settings_resolve_as_on_main(tmp_path, shape):
    estate, topics, rows = SHAPES[shape]
    built = build(tmp_path, estate)
    assert built.code == 0
    assert env(built) == (topics, rows)


# ── files are read in main's order (fails on the v5 to v9 prototypes) ────────────────────


@pytest.mark.parametrize("files", [
    ("docker-compose.yml", "docker/compose.yml"),
    ("compose.yaml", "compose/compose.override.yaml"),
])
def test_files_are_read_in_mains_order_so_the_same_value_is_read_last(tmp_path, files):
    """Main reads `sorted(ctx.inputs(repo))`, path by path, so `docker/compose.yml` comes
    before `docker-compose.yml`; POSIX text puts it after, and a value changed with it."""
    root_file, dir_file = files
    built = build(tmp_path, {
        "deploy": {root_file: compose(image("payments", "root-value")),
                   dir_file: compose(image("payments", "dir-value"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:root-value"]


@pytest.mark.parametrize("names, windows, posix", [
    (("docker-compose.a.yml", "docker-compose.B.yml"), "B-value", "a-value"),
    (("docker-compose.PROD.yml", "docker-compose.dev.yml"), "PROD-value", "dev-value"),
])
def test_mains_read_order_folds_case_on_windows_only(tmp_path, names, windows, posix):
    """Pinned as main's, and platform-dependent (D534-74, T590): Windows compares paths
    case-folded, POSIX raw, so the file read last differs wherever folding reorders two."""
    first, second = names
    built = build(tmp_path, {
        "deploy": {first: compose(image("payments", first.split(".")[1] + "-value")),
                   second: compose(image("payments", second.split(".")[1] + "-value"))},
        "payments": {"app/pub.py": producer("TOPIC")}})
    assert built.topics == [f"topic:{windows if WINDOWS else posix}"]


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


# ── main's own rule for the env view, across every build: spelling (D534-71, D534-72) ────

ORDERS_ROOT = "@@ORDERS@@"
RESOLVES, UNSET = True, False
SPELLINGS = {
    "dot": ("    build: .\n", RESOLVES),
    "dot slash": ("    build: ./\n", RESOLVES),
    "padded dot": ('    build: " . "\n', RESOLVES),
    "empty string": ('    build: ""\n', UNSET),
    "bare null": ("    build:\n", UNSET),
    "map, no context": ("    build:\n      dockerfile: Dockerfile\n", UNSET),
    "map, null context": ("    build:\n      context:\n      dockerfile: Dockerfile\n", UNSET),
    "map, tilde context": ("    build: {context: ~}\n", UNSET),
    "map, empty map": ("    build: {}\n", UNSET),
    "map, context dot": ("    build:\n      context: .\n", RESOLVES),
    "map, context empty": ('    build:\n      context: ""\n', UNSET),
    "map, context ../orders": ("    build:\n      context: ../orders\n", RESOLVES),
    "../orders": ("    build: ../orders\n", RESOLVES),
    # main resolves on the filesystem: case folds on Windows, not on POSIX.
    "../ORDERS": ("    build: ../ORDERS\n", WINDOWS),
    "../Orders": ("    build: ../Orders\n", WINDOWS),
    "./svc-a": ("    build: ./svc-a\n", UNSET),
    "./nope": ("    build: ./nope\n", UNSET),
    "..": ("    build: ..\n", UNSET),
    "../other": ("    build: ../other\n", UNSET),
    "interpolated": ("    build: ${CTX}\n", UNSET),
    "url": ("    build: https://github.com/x/y.git\n", UNSET),
    "list": ("    build: [a, b]\n", UNSET),
    "int": ("    build: 5\n", UNSET),
    # a backslash separates only on Windows; on POSIX it is part of a file name.
    "backslash dot": ("    build: '.\\'\n", WINDOWS),
    "absolute own root": (f"    build: '{ORDERS_ROOT}'\n", RESOLVES),
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_the_env_view_is_mains_for_every_build_spelling(tmp_path, spelling):
    """Placement and the env view differ on purpose (V2): whatever the placement, a value
    reaches the component's own code exactly when main's filesystem rule says so."""
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


# ── ruling 9: ranking within one component; across components the last read wins ────────

PANTRY = Path(__file__).resolve().parent / "fixtures" / "pantry"
MAINS_PANTRY_WHY = (
    "`restock.queue` is declared as 'restock-events-v1' here and as 'restock-events-v2' in "
    "docker-compose.yml, which the workspace names as the owner of service `pantry-api`. The "
    "owner's value is what the graph uses; this one is reported because a stale deployment "
    "nobody has noticed is a finding, not noise")


def test_within_one_component_the_superseded_row_reads_exactly_as_on_main(tmp_path):
    """Pantry's row is within one component, where ranking works, so it keeps main's text
    byte for byte (ruling 9). Only its kind, file and line were pinned before (D534-68)."""
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


def test_across_components_the_row_cites_the_dropped_declaration_and_claims_no_ranking(
        tmp_path):
    """The owner's file is read second and replaces the other component's value. The row
    names that value where it is written, qualifies the kept file, and does not claim the
    owner's value is used: a component read later could still replace it (T590)."""
    built = build(tmp_path, two_deploy_repos("deploy-b"))
    assert built.topics == ["topic:b-value"]
    row, = built.kinds("superseded-declaration")
    assert (row["component"], row["source_file"], row["line"], row["value"]) == (
        "deploy-a", "docker-compose.yml", 5, "a-value")
    assert "in deploy-b/docker-compose.yml" in row["why"]
    assert "not yet ranked against each other" in row["why"]
    assert "is what the graph uses" not in row["why"]


def test_with_the_owner_read_first_a_later_components_value_still_wins(tmp_path):
    """Pinned as main's until T590: ranking does not cross components, so the value read
    last wins, with no row."""
    built = build(tmp_path, two_deploy_repos("deploy-a"))
    assert built.topics == ["topic:b-value"]
    assert not built.kinds("superseded-declaration")


@pytest.mark.parametrize("owner, rows", [("zz", 1), ("mm", 1), ("aa", 0)])
def test_three_components_rank_as_on_main_in_every_arrival_order(tmp_path, owner, rows):
    """Pinned as main's (D534-68): owner read last, middle and first give 1, 1 and 0 rows,
    and the value read last wins every time. A shared ranking table would change this."""
    built = build(tmp_path, {
        **{f"{c}-deploy": {"docker-compose.yml": compose(image("payments", f"{c}-value"))}
           for c in ("aa", "mm", "zz")},
        "payments": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "payments", "owner": f"{owner}-deploy"}]})
    assert built.topics == ["topic:zz-value"]
    assert len(built.kinds("superseded-declaration")) == rows


def test_within_one_component_a_later_components_value_still_replaces_the_owners(tmp_path):
    """Pinned as main's (D534-67): the row keeps main's text, whose "the owner's value is
    what the graph uses" is false here, because another component set the key later."""
    built = build(tmp_path, {
        "aa": {"docker-compose.yml": compose(image("payments", "a1")),
               "docker-compose.zz.yml": compose(image("payments", "a2"))},
        "zz-deploy": {"docker-compose.yml": compose(image("payments", "z-value"))},
        "payments": {"app/pub.py": producer("TOPIC")},
        "__canonical__": [{"service": "payments", "owner": "aa", "path": "docker-compose.yml"}]})
    assert built.topics == ["topic:z-value"]
    row, = built.kinds("superseded-declaration")
    assert (row["component"], row["source_file"], row["value"]) == (
        "aa", "docker-compose.zz.yml", "a2")
    assert "The owner's value is what the graph uses" in row["why"]
