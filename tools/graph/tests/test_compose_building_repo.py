"""A building repository's own services, and the settings it gives them (T534 V3).

A component whose compose files build anything is BUILDING (ruling 1). Every service it does
not build stays its own - repo-scoped, never joined - and publishes its settings under the
service's name, as on main (ruling 4), so an orchestration repo still configures the
component whose image it runs. Where a read resolves to such a value, `env-keys` says where it
came from, carrying the key and the origin file only (ruling 5), since that file may be the
component's deployment or that repo's own local container, and nothing can tell which.

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

WINDOWS = os.name == "nt"
READER = 'import os\nQ = os.environ["QUEUE"]\n'


def from_building_repo(built) -> list[dict]:
    return built.kinds("env-key-from-building-repo")


def orchestration(queue: str = "q") -> dict:
    """An orchestration repo that builds itself and runs `payments`' prebuilt image."""
    return {"orch": {"docker-compose.yml": compose(
        svc("orch", ".", None), svc("payments", None, queue, key="QUEUE",
                                    image="org/payments:1"))},
        "payments": {"app/reader.py": READER}}


def test_a_building_repos_other_service_publishes_under_its_name_with_a_row(tmp_path):
    built = build(tmp_path, orchestration())
    assert not built.kinds("env-key-unset"), "the value resolves, as on main"
    row, = from_building_repo(built)
    assert (row["component"], row["source_file"], row["key"], row["origin"]) == (
        "payments", "app/reader.py", "queue", "orch/docker-compose.yml")
    assert "value" not in row, "ruling 5: the key and the origin file, no value"
    assert "a container it does not build" in row["why"]
    assert built.placed("orch/docker-compose.yml", "orch")[0] == "svc:orch@orch"
    assert built.placed("orch/docker-compose.yml", "payments") == ("svc:payments@orch", "", ""), \
        "repo-scoped, never joined (ruling 1)"


def test_a_dev_file_running_a_stock_container_gets_the_row(tmp_path):
    """The leak ruling 4 names: a dev `redis` beside a real component `redis`."""
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(
            svc("web", ".", None), svc("redis", None, "1gb", key="MAXMEMORY",
                                       image="redis:7"))},
        "redis": {"app/cache.py": 'import os\nLIMIT = os.environ["MAXMEMORY"]\n'}})
    row, = from_building_repo(built)
    assert (row["key"], row["origin"]) == ("maxmemory", "orders/docker-compose.yml")


@pytest.mark.parametrize("build_yaml, leaks", [
    ("    build: ./redis-custom\n", True),                   # D534-73: from a subdirectory
    ("    build:\n      dockerfile: Dockerfile\n", False),   # D534-80: with no context
])
def test_a_stock_container_the_repo_builds_leaks_from_a_subdirectory_alone(tmp_path, build_yaml,
                                                                            leaks):
    """A subdirectory's build (`own`) keys by the name rule, as on `main`, so its value reaches the
    same-named component and nothing says so - the dev leak, a remainder (D24-137, D24-140). A
    mapping with no context, in a file at the component's root, builds the root itself (`exact`),
    so its settings now reach the declaring component's code - the one leak the keying closes
    (D24-140, D24-155)."""
    built = build(tmp_path, {
        "platform": {"docker-compose.yml": (
            "services:\n  web:\n    build: .\n  redis:\n" + build_yaml
            + "    environment:\n      MAXMEMORY: 1gb\n")},
        "redis": {"app/cache.py": producer("MAXMEMORY")}})
    assert built.topics == (["topic:1gb"] if leaks else [])
    assert not from_building_repo(built)


@pytest.mark.parametrize("cell", ["absolute", "letter case"])
def test_a_components_own_unplaceable_build_of_its_own_root_gets_no_row(tmp_path, cell):
    """D534-81: the setting and the reading component are one, so the row is not for it. Placed
    nowhere, the build keys by the name rule (decision 55): neither an absolute path nor a
    letter-case twin reaches the root's code, on any platform (D24-155)."""
    context = str(tmp_path / "ws" / "orders") if cell == "absolute" else "../ORDERS"
    built = build(tmp_path, {
        "orders": {"docker-compose.yml": compose(svc("web", context)),
                   "app/pub.py": producer("TOPIC")},
        "other": {"README.md": "x\n"}})
    assert built.kinds("compose-build-unresolved")
    assert not from_building_repo(built)
    assert built.topics == []


def test_an_unplaced_build_is_worded_as_one(tmp_path):
    """D534-35: a build the file declares and nothing could place is not "a container it
    does not build"."""
    built = build(tmp_path, {
        "orch": {"docker-compose.yml": compose(
            svc("orch", ".", None), svc("payments", "${CTX}", "q", key="QUEUE"))},
        "payments": {"app/reader.py": READER}})
    row, = from_building_repo(built)
    assert "whose build that file does not place" in row["why"]


def test_a_value_edit_drifts_only_the_edited_repos_shard(tmp_path):
    """Ruling 5, as a process: the row names the origin file, never the value, so editing
    the value in the orchestration repo leaves the reading component's shard as it was."""
    lay_down(tmp_path, orchestration("q"))
    published = spawn(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    assert from_building_repo(read(0, tmp_path / "out")), "the row is in the reader's shard"
    clean = spawn(tmp_path, "--check-snapshot", out="out1")
    assert clean.returncode == 0, ("the unedited snapshot is clean (C534-12): "
                                   + clean.stdout + clean.stderr)
    compose_file = tmp_path / "ws" / "orch" / "docker-compose.yml"
    compose_file.write_bytes(compose_file.read_bytes().replace(b"QUEUE: q", b"QUEUE: q2"))
    checked = spawn(tmp_path, "--check-snapshot", out="out2")
    drifted = sorted(line.split("DRIFT in ", 1)[1].split(" ", 1)[0]
                     for line in checked.stdout.splitlines() if "snapshot: DRIFT in " in line)
    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert drifted == ["orch"]


# ── a service named after its own component keeps main's label (D534-83, D534-94) ───────


@pytest.mark.parametrize("files", [
    {"docker-compose.yml": compose(svc("orders", "${CTX}"))},
    {"docker-compose.yml": compose(svc("orders", "../nope"))},
    {"docker-compose.a.yml": compose(svc("orders", "../x", None)),
     "docker-compose.b.yml": compose(svc("orders", "../y", None)),
     "docker-compose.c.yml": compose(svc("orders", None))},
], ids=["interpolated", "no component holds it", "an ambiguous layer"])
def test_a_self_named_service_keeps_mains_label_beside_its_row(tmp_path, files):
    """Pinned as main's: the service is the component's own node, which main labels by
    name, so an unreadable build does not take the label away; the row says why."""
    built = build(tmp_path, {"orders": {**files, "app/pub.py": producer("TOPIC")},
                             "x": {"README.md": "x\n"}, "y": {"README.md": "y\n"}})
    last = sorted(files)[-1]
    assert built.placed(f"orders/{last}") == ("svc:orders@orders", "orders", "name")
    assert built.kinds("compose-build-unresolved", "compose-build-ambiguous")
    assert built.topics == ["topic:web-value"], "its settings resolve, as on main"


def test_the_self_named_match_is_normalized_which_is_wider_than_mains(tmp_path):
    """D534-94, D534-108: `"my api"` normalizes to component `my-api`'s own id, so it is
    labelled by name here, where main's exact alias match left the edge unlabelled. The
    node id is unchanged. Fails on main."""
    built = build(tmp_path, {"my-api": {"docker-compose.yml": compose(
        svc('"my api"', "../nope"))}})
    edge, = built.deploys
    assert (edge["dst"], edge["props"]["component"], edge["props"]["mapped_by"]) == (
        "svc:my-api@my-api", "my-api", "name")


# ── the URL, absolute and outside rows are worded by layout (D534-87, D534-90, D534-92) ──


@pytest.mark.parametrize("single, where", [(True, "this repository"),
                                           (False, "this workspace")])
def test_rows_name_the_repository_or_the_workspace_by_layout(tmp_path, single, where):
    estate = {"orders": {"docker-compose.yml": compose(
        svc("url", "https://github.com/x/y.git", None), svc("abs", "/opt/x", None),
        svc("out", "../../elsewhere", None))}}
    if not single:
        estate["other"] = {"README.md": "x\n"}
    built = build(tmp_path, estate)
    why = {r["service"]: r["why"] for r in built.kinds("compose-build-unresolved")}
    assert f"which is not a directory in {where}" in why["url"]
    assert f"if it names a directory in {where}, write it relative to this file" in why["abs"]
    assert f"lies outside {where} as written" in why["out"]
