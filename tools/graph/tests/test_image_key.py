"""Batch 23 (T589; design pass 23, section 2): the image key - Docker's reference grammar, so compose's
reference as its file writes it and helm's `image.repository`, with its `registry` beside it and a
template span written as `_yaml.TOKEN`, compare equal where they name one image - and the literal tail
an interpolated reference keeps, compared on a path-segment boundary."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph.extractors._image import image_key, literal_tail, tail_of  # noqa: E402
from tools.graph.extractors._yaml import TOKEN  # noqa: E402


@pytest.mark.parametrize("reference, registry, key", [
    # the tag and the digest dropped
    ("org/web:1.0", "", "docker.io/org/web"),
    ("org/web@sha256:" + "a" * 64, "", "docker.io/org/web"),
    ("org/web:1.0@sha256:" + "a" * 64, "", "docker.io/org/web"),
    # Docker Hub: no registry, a single segment, its two other host names, written explicitly
    ("redis", "", "docker.io/library/redis"),
    ("library/redis", "", "docker.io/library/redis"),
    ("docker.io/library/redis", "", "docker.io/library/redis"),
    ("docker.io/redis:7", "", "docker.io/library/redis"),
    ("index.docker.io/org/web", "", "docker.io/org/web"),
    ("registry-1.docker.io/org/web:2", "", "docker.io/org/web"),
    # a registry: its host lower-cased, a port kept, `localhost` a host, the path exact
    ("GHCR.IO/org/web", "", "ghcr.io/org/web"),
    ("Registry.Example.com:5000/team/web:3", "", "registry.example.com:5000/team/web"),
    ("localhost/web", "", "localhost/web"),
    ("localhost:5000/web:dev", "", "localhost:5000/web"),
    ("Myreg/web", "", "myreg/web"),          # upper case: a host by Docker's own test
    # helm's `registry`, prefixed where the repository names none - never over one it names
    ("org/web", "ghcr.io", "ghcr.io/org/web"),
    ("org/web", "GHCR.io/", "ghcr.io/org/web"),
    ("quay.io/org/web", "ghcr.io", "quay.io/org/web"),
    # the reference a chart renders, its registry before it, read by the same grammar: a registry
    # naming no host is a Docker Hub path segment, as Docker reads `myreg/org/web` (review B23-1)
    ("org/web", "myreg", "docker.io/myreg/org/web"),
    ("web", "myreg", "docker.io/myreg/web"),
    ("org/web", "MyReg", "myreg/org/web"),
    ("org/web", "localhost:5000", "localhost:5000/org/web"),
    ("org/web", "registry.example.com/team", "registry.example.com/team/org/web"),
    ("redis", "docker.io", "docker.io/library/redis"),
    # an interpolated tag alone leaves the key intact
    ("org/web:${TAG}", "", "docker.io/org/web"),
    (f"org/web:{TOKEN}", "", "docker.io/org/web"),
    # a registry or path interpolated or templated has no key
    ("${REGISTRY}/org/web:1.0", "", ""),
    ("$REGISTRY/org/web", "", ""),
    ("ghcr.io/${ORG}/web", "", ""),
    ("${IMAGE}", "", ""),
    (f"{TOKEN}/org/web", "", ""),
    ("org/web", "${REGISTRY}", ""),
    ("org/web", TOKEN, ""),
    # nothing to key
    ("", "", ""),
    ("   ", "", ""),
    ("@sha256:" + "a" * 64, "", ""),
], ids=lambda value: repr(value)[:40])
def test_the_key_reads_dockers_reference_grammar(reference, registry, key):
    assert image_key(reference, registry) == key


def test_two_vocabularies_meet_on_one_key():
    """A compose reference as its file writes it and helm's repository with its registry."""
    assert image_key("ghcr.io/org/web:1.4") == image_key("org/web", "ghcr.io")
    assert image_key("org/web:2") == image_key("docker.io/org/web") == image_key("org/web")
    assert image_key("ghcr.io/org/web") != image_key("org/web"), "a registry is never guessed"
    assert image_key("myreg/org/web") == image_key("org/web", "myreg"), "review B23-1"


@pytest.mark.parametrize("reference, tail", [
    ("${REG}/org/web:1", "org/web"),
    (f"{TOKEN}/org/web", "org/web"),
    ("ghcr.io/${ORG}/web:2", "web"),
    ("${REG}/${ORG}/web:1", "web"),
    ("${IMAGE}", ""),
    ("org/${NAME}", ""),
    ("org/web:${TAG}", "org/web"),
    ("org/web", "org/web"),
])
def test_the_literal_tail_is_the_path_after_the_last_interpolated_segment(reference, tail):
    assert literal_tail(reference) == tail


@pytest.mark.parametrize("key, tail, meets", [
    ("docker.io/org/web", "org/web", True),
    ("docker.io/org/web", "web", True),
    ("ghcr.io/org/web", "org/web", True),
    ("docker.io/org/web", "docker.io/org/web", True),
    ("docker.io/org/myweb", "web", False),
    ("docker.io/org/web", "eb", False),
    ("docker.io/org/web", "", False),
    ("", "web", False),
])
def test_a_tail_meets_a_key_on_a_path_segment_boundary_and_an_empty_tail_meets_none(key, tail,
                                                                                   meets):
    """D23-20: `str.endswith("")` is true of every key, so an empty tail - `${IMAGE}` - would
    name every build; it names none."""
    assert tail_of(key, tail) is meets
