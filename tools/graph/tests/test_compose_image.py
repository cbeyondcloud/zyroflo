"""Batch 23 (T589; design pass 23, section 1): a compose claim carries the image its service runs
with, as compose merges it - its own scalar `image:`, else the first `image:` on its `extends:`
chain, the chain walked whole, then a T588 package's base - and `image_from`, the `<file>:<line>`
writing it, wherever its own declaration does not. A T588 package's image is computed once and
published on both its claims. An `image:` that is a mapping or a list is one row and no image.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

CODE = {"main.py": "print(1)\n"}


def deploy(compose_yaml: str, **files: str) -> dict:
    """A `deploy` component holding the compose files, beside `orders`, which holds code."""
    return {"deploy": {"compose.yaml": compose_yaml, **files}, "orders": dict(CODE)}


def image_of(built, source: str, service: str) -> tuple[str | None, str | None]:
    props = built.deploy(source, service)["props"]
    return props.get("image"), props.get("image_from")


def test_a_services_own_image_is_carried_with_no_citation(tmp_path):
    built = build(tmp_path, deploy("services:\n  web:\n    image: org/web:2\n"))
    assert image_of(built, "deploy/compose.yaml", "web") == ("org/web:2", None)


def test_an_image_written_in_an_extends_base_is_the_services_and_cites_the_base(tmp_path):
    """D23-76: read from its own declaration alone, `shipper` extending `app`'s
    `image: org/shipper` named none."""
    built = build(tmp_path, deploy("services:\n  shipper:\n    extends: app\n"
                                   "  app:\n    image: org/shipper\n"))
    assert image_of(built, "deploy/compose.yaml", "shipper") == ("org/shipper", "compose.yaml:5")
    assert image_of(built, "deploy/compose.yaml", "app") == ("org/shipper", None)


def test_a_chain_is_walked_whole_past_the_base_that_places_the_build(tmp_path):
    """D23-86, D23-96: `web` extends `mid`, whose `build:` places it, extending `base`, which
    names the image - in the build's own file, which `image_from` cites."""
    built = build(tmp_path, deploy("services:\n  web:\n    extends: mid\n"
                                   "  mid:\n    build: ../orders\n    extends: base\n"
                                   "  base:\n    image: org/web\n"))
    edge = built.deploy("deploy/compose.yaml", "web")
    assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:web@orders", "build")
    assert image_of(built, "deploy/compose.yaml", "web") == ("org/web", "compose.yaml:8")


def test_a_build_of_its_own_extending_a_base_that_names_the_image_takes_it(tmp_path):
    built = build(tmp_path, deploy("services:\n  api:\n    build: ../orders\n    extends: base\n"
                                   "  base:\n    image: org/api\n"))
    assert image_of(built, "deploy/compose.yaml", "api") == ("org/api", "compose.yaml:6")


def test_an_image_in_another_compose_file_is_cited_there(tmp_path):
    built = build(tmp_path, deploy(
        "services:\n  api:\n    extends:\n      file: docker-compose.base.yml\n"
        "      service: apibase\n",
        **{"docker-compose.base.yml": "services:\n  apibase:\n    image: org/api:3\n"}))
    assert image_of(built, "deploy/compose.yaml", "api") == (
        "org/api:3", "docker-compose.base.yml:3")


@pytest.mark.parametrize("written, shape", [
    ("    image:\n      name: org/web\n", "a mapping"),
    ("    image: [org/web]\n", "a list"),
])
def test_an_image_that_is_not_one_string_is_a_row_and_no_image(tmp_path, written, shape):
    built = build(tmp_path, deploy("services:\n  web:\n" + written))
    assert image_of(built, "deploy/compose.yaml", "web") == (None, None)
    (row,) = built.kinds("compose-image-unresolved")
    assert (row["service"], row["source_file"], row["line"]) == ("web", "compose.yaml", 2)
    assert row["why"] == (f"`web`'s `image:` is {shape}, not one image reference, so it takes "
                          f"part in no image proposal; write it as one string")


def test_written_as_one_string_the_row_is_gone_and_the_image_read(tmp_path):
    built = build(tmp_path, deploy("services:\n  web:\n    image: org/web\n"))
    assert not built.kinds("compose-image-unresolved")
    assert image_of(built, "deploy/compose.yaml", "web") == ("org/web", None)


@pytest.mark.parametrize("written", ['    image: ""\n', "    image:\n", "    image: null\n"])
def test_an_empty_or_null_image_names_nothing_and_is_no_row(tmp_path, written):
    built = build(tmp_path, deploy("services:\n  web:\n" + written))
    assert image_of(built, "deploy/compose.yaml", "web") == (None, None)
    assert not built.kinds("compose-image-unresolved")


def test_a_t588_package_publishes_its_one_merged_image_on_both_claims(tmp_path):
    """D23-153, D23-158: an override writing its own `image:` over a base writing another - the
    package runs the override's, so both claims carry it and one key lands on the node; the
    base's superseded image is carried by neither."""
    built = build(tmp_path, deploy(
        "services:\n  web:\n    build: ../orders\n    image: org/base:1\n",
        **{"compose.override.yml": "services:\n  web:\n    image: org/override:2\n"}))
    assert image_of(built, "deploy/compose.yaml", "web") == (
        "org/override:2", "compose.override.yml:3")
    assert image_of(built, "deploy/compose.override.yml", "web") == ("org/override:2", None)


def test_an_override_with_no_image_runs_its_bases_from_the_bases_own_chain(tmp_path):
    """D23-166: the base's own chain walked after it - the base takes its image from `common`,
    and the package runs that image, on both claims."""
    built = build(tmp_path, deploy(
        "services:\n  web:\n    build: ../orders\n    extends: common\n"
        "  common:\n    image: org/common:1\n",
        **{"compose.override.yml": "services:\n  web:\n    environment:\n      A: \"1\"\n"}))
    for source in ("deploy/compose.yaml", "deploy/compose.override.yml"):
        assert image_of(built, source, "web") == ("org/common:1", "compose.yaml:6"), source
