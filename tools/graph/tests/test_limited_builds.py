"""A limited build never changes facts (T534 V7, T587; the owner's ruling 3).

`config["components"]` is the whole workspace's list even under `--components`, so a limited
graph places services on the same ids as a full build; what needs other components' FILES
may still differ, as on main. Publishing a limited build is refused (T587), and so is an
`--only` that names a link pass without the passes it reads (D534-23). Both refusals are a
process's contract, so they are spawned, and each remedy is followed.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, compose, lay_down, producer, spawn, svc  # noqa: E402

ESTATE = {"deploy": {"docker-compose.yml": compose(svc("web", "../orders", None),
                                                   svc("orders", None, "o-value",
                                                       image="org/orders:1"))},
          "orders": {"app/pub.py": producer("TOPIC")}}


def ids_from(built, component: str) -> list[tuple[str, str]]:
    return sorted((e["src"], e["dst"]) for e in built.deploys
                  if e["src"].startswith(f"deploy:{component}/"))


def test_a_limited_build_places_services_on_the_same_ids_as_a_full_one(tmp_path):
    """Fails on main, where `--components deploy` could not see `orders` and so left the
    build on a node scoped to the deployment repo (T579)."""
    full = build(tmp_path / "full", ESTATE)
    limited = build(tmp_path / "limited", ESTATE, "--components", "deploy")
    assert ids_from(limited, "deploy") == ids_from(full, "deploy")
    assert ("deploy:deploy/docker-compose.yml", "svc:web@orders") in ids_from(limited, "deploy")


def test_a_limited_builds_env_differs_only_where_another_components_file_sets_it(tmp_path):
    """D534-58: `orders` reads a value only `deploy`'s file sets, so without `deploy` it is
    unset; that is the one difference, as on main."""
    full = build(tmp_path / "full", ESTATE)
    limited = build(tmp_path / "limited", ESTATE, "--components", "orders")
    assert full.topics == ["topic:o-value"]
    assert limited.topics == []
    assert [r["kind"] for r in limited.kinds("env-key-unset")] == ["env-key-unset"]


def test_publishing_a_limited_build_is_refused_and_a_full_publish_is_accepted(tmp_path):
    """T587: a narrowed build's facts are not the component's facts, so its shards would
    be stamped current while wrong."""
    lay_down(tmp_path, ESTATE)
    refused = spawn(tmp_path, "--components", "deploy", "--publish-snapshot")
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "--publish-snapshot writes each named component's committed shard" in refused.stdout
    assert "Traceback" not in refused.stderr
    assert not (tmp_path / "ws" / "deploy" / ".sdlc").exists(), "nothing written"
    published = spawn(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr


def test_only_without_a_passes_requirements_is_refused_and_adding_them_is_accepted(tmp_path):
    """D534-23: `mark-canonical` reads contests after `join-by-name` has moved claims; named
    without it, it would answer about a graph no build produces."""
    lay_down(tmp_path, ESTATE)
    refused = spawn(tmp_path, "--only", "compose-services,mark-canonical")
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "--only names mark-canonical, which reads what join-by-name decides first" in (
        refused.stdout + refused.stderr)
    assert "Traceback" not in refused.stderr
    accepted = spawn(tmp_path, "--only", "compose-services,join-by-name,mark-canonical",
                     out="again")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
