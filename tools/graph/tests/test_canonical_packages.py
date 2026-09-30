"""The canonical pass on T534's ids: packages, owners, choices and the proposal (T534 V6).

One FILE is one package, keyed on its workspace-relative path, so a parent and a nested
child reading one compose file are one package, named by its most specific reader. A block's
`owner` selects the packages that component reads and its `path` the packages it covers; a
decision needs exactly one. `--propose-canonical` drafts only choices that decide, and a
draft pasted unfilled decides nothing.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.extractors import _canonical  # noqa: E402
from tools.graph.tests._estate import build, compose, spawn, svc  # noqa: E402

ROUTER = 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/cache")\n'
BUILD_DOT = compose(svc("web", ".", None))


def nested(extra: dict | None = None) -> dict:
    """`platform` and its declared child `svc-a` both read `svc-a`'s file; `deploy` builds
    `svc-a` too."""
    return {"platform": {"svc-a/docker-compose.yml": BUILD_DOT}, "platform/svc-a": {},
            "deploy": {"docker-compose.yml": compose(svc("web", "../platform/svc-a", None))},
            **(extra or {})}


def parent_reads_two(extra: dict | None = None) -> dict:
    return {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a", None)),
                         "svc-a/docker-compose.yml": BUILD_DOT},
            "platform/svc-a": {}, **(extra or {})}


def root_reads_all(extra: dict | None = None) -> dict:
    return {".": {"svc-a/Dockerfile": "FROM scratch\n",
                  "other/docker-compose.yml": compose(svc("web", "../svc-a", None)),
                  "deploy/docker-compose.yml": compose(svc("web", "../svc-a", None))},
            "svc-a": {}, "deploy": {}, "__names__": {".": "mono"}, **(extra or {})}


def one_component_two_files(extra: dict | None = None) -> dict:
    return {"orders": {"docker-compose.yml": BUILD_DOT, "docker-compose.prod.yml": BUILD_DOT},
            **(extra or {})}


def two_repos(extra: dict | None = None) -> dict:
    return {"svc-a": {"README.md": "x\n"},
            "deploy-a": {"docker-compose.yml": compose(svc("web", "../svc-a", None))},
            "deploy-b": {"docker-compose.yml": compose(svc("web", "../svc-a", None))},
            **(extra or {})}


def contested(built) -> dict:
    row, = built.kinds("contested-service")
    return row


# ── packages are files, named by their most specific reader (D534-18, D534-30, D534-42) ──


def test_a_file_a_parent_and_its_child_both_read_is_one_package(tmp_path):
    built = build(tmp_path, nested())
    assert contested(built)["claims"] == ["deploy/docker-compose.yml:2",
                                          "svc-a/docker-compose.yml:2"]


def test_a_nested_or_root_layout_invents_no_contest(tmp_path):
    nested_only = build(tmp_path / "a", {"platform": {"svc-a/docker-compose.yml": BUILD_DOT},
                                         "platform/svc-a": {}})
    root = build(tmp_path / "b", {".": {"svc-a/Dockerfile": "FROM scratch\n",
                                        "deploy/docker-compose.yml": compose(
                                            svc("a", "../svc-a", None),
                                            svc("b", None, None, image="x:1"))},
                                  "svc-a": {}, "deploy": {}, "__names__": {".": "mono"}})
    assert not nested_only.kinds("contested-service")
    assert not root.kinds("contested-service")


def test_a_component_spelt_dot_slash_names_its_packages_the_same(tmp_path):
    """D534-42: `./platform` and `platform` are one place, through the one normalizing
    reading of the component list that compose, this pass and the join share."""
    built = build(tmp_path, {"./platform": {"svc-a/docker-compose.yml": BUILD_DOT},
                             "platform/svc-a": {},
                             "deploy": {"docker-compose.yml": compose(
                                 svc("web", "../platform/svc-a", None))},
                             "__names__": {"./platform": "platform"}})
    assert contested(built)["claims"] == ["deploy/docker-compose.yml:2",
                                          "svc-a/docker-compose.yml:2"]


@pytest.mark.parametrize("owner, won", [
    ("platform", "svc-a/docker-compose.yml"),     # any reader of the package decides it
    ("svc-a", "svc-a/docker-compose.yml"),
    ("deploy", "deploy/docker-compose.yml"),
])
def test_an_owner_naming_any_reader_of_a_package_decides_it(tmp_path, owner, won):
    built = build(tmp_path, nested({"__canonical__": [{"service": "web", "owner": owner}]}))
    assert not built.kinds("contested-service")
    lost = [e for e in built.deploys if e.get("canonical") is False]
    assert lost and all(e["props"]["superseded_by"] == won for e in lost)


@pytest.mark.parametrize("make, block, decides", [
    (parent_reads_two, {"owner": "platform"}, False),     # it reads both packages
    (parent_reads_two, {"owner": "svc-a"}, True),
    (parent_reads_two, {"owner": "platform", "path": "docker-compose.yml"}, True),
    (root_reads_all, {"owner": "mono"}, False),            # the root reads every package
    (root_reads_all, {"owner": "deploy"}, True),
])
def test_an_owner_that_reads_two_packages_decides_nothing(tmp_path, make, block, decides):
    """D534-39: selecting takes exactly one package; a parent reading its own file and its
    child's, or a root component reading every file, selects two."""
    built = build(tmp_path, make({"__canonical__": [{"service": "web", **block}]}))
    assert (not built.kinds("contested-service")) is decides


@pytest.mark.parametrize("make, choices", [
    (two_repos, [{"owner": "deploy-a"}, {"owner": "deploy-b"}]),
    (one_component_two_files, [{"owner": "orders", "path": "docker-compose.prod.yml"},
                               {"owner": "orders", "path": "docker-compose.yml"}]),
    (parent_reads_two, [{"owner": "platform", "path": "docker-compose.yml"},
                        {"owner": "svc-a"}]),
    (root_reads_all, [{"owner": "deploy"}, {"owner": "mono", "path": "other/docker-compose.yml"}]),
])
def test_each_package_is_offered_the_smallest_block_that_selects_exactly_it(
        tmp_path, make, choices):
    assert contested(build(tmp_path, make()))["choices"] == choices


# ── --propose-canonical: three forms, each decides when filled (D534-50) ─────────────────

FORMS = {
    "an owner list": (two_repos, 'owner = ""            # one of: deploy-a | deploy-b',
                      ('owner = ""', 'owner = "deploy-b"')),
    "an owner pre-filled with a path slot": (
        one_component_two_files,
        'owner = "orders"\npath = ""             # one of: "docker-compose.prod.yml" | '
        '"docker-compose.yml"',
        ('path = ""', 'path = "docker-compose.prod.yml"')),
    "choice lines": (parent_reads_two, '# choice: owner = "svc-a"',
                     ('owner = ""', 'owner = "svc-a"')),
}


def drafted(tmp_path: Path, make) -> str:
    built = build(tmp_path, make(), "--propose-canonical")
    assert built.code == 0
    text = (tmp_path / "out" / "canonical.proposed.toml").read_text(encoding="utf-8")
    return "[[canonical]]" + text.split("[[canonical]]", 1)[1]


def paste(where: Path, block: str) -> None:
    toml = where / "ws" / "workspace.toml"
    toml.write_bytes(toml.read_bytes() + ("\n" + block + "\n").encode("utf-8"))


@pytest.mark.parametrize("form", sorted(FORMS))
def test_each_draft_form_decides_once_filled(tmp_path, form):
    make, shows, (blank, filled) = FORMS[form]
    block = drafted(tmp_path, make)
    assert shows in block
    paste(tmp_path, re.sub(r"^" + re.escape(blank) + r".*$", filled, block, count=1,
                           flags=re.M))
    rebuilt = build(tmp_path)
    assert rebuilt.code == 0
    assert not rebuilt.kinds("contested-service")


@pytest.mark.parametrize("form, refused", [("an owner list", True),
                                           ("an owner pre-filled with a path slot", False),
                                           ("choice lines", True)])
def test_a_draft_pasted_unfilled_decides_nothing(tmp_path, form, refused):
    """As a process: a blank owner is refused loudly by the workspace parser; a blank path
    builds and stays reported. Neither decides silently."""
    make, _, _ = FORMS[form]
    paste(tmp_path, drafted(tmp_path, make))
    run = spawn(tmp_path, out="again")
    if refused:
        assert run.returncode == 2, run.stdout + run.stderr
        assert "Traceback" not in run.stderr
    else:
        assert run.returncode == 0, run.stdout + run.stderr
        from tools.graph.tests._estate import read
        assert read(0, tmp_path / "again").kinds("contested-service")


# ── the shared reading of the component list (D534-42) ──────────────────────────────────


@pytest.mark.parametrize("written, read", [
    ("./x", "x"), ("x/", "x"), ("x//y", "x/y"), ("x/./y", "x/y"), (".", "."), ("", "."),
    ("x\\y", "x/y"), ("./", "."),
])
def test_the_component_layout_reads_each_spelling_as_one_place(written, read):
    layout = _canonical.workspace_layout({"components": [{"name": "c", "path": written}]})
    assert layout == {"c": read}


# ── block names compare normalized; a second spelling is reported (D534-24, D534-34) ─────

HALVES = {"orders": {
    "docker-compose.yml": 'services:\n  "my api":\n    build: .\n    environment:\n'
                          '      TOPIC: base\n',
    "docker-compose.prod.yml": 'services:\n  "my api":\n    build: .\n    environment:\n'
                               '      TOPIC: prod\n'}}


@pytest.mark.parametrize("spelling", ["my api", "my-api"])
def test_a_block_decides_both_halves_in_either_spelling(tmp_path, spelling):
    """D534-4, D534-24: the edges and the env, in the name as compose writes it and as the
    id spells it."""
    built = build(tmp_path, {**HALVES, "__canonical__": [
        {"service": spelling, "owner": "orders", "path": "docker-compose.yml"}]})
    assert not built.kinds("contested-service")
    row, = built.kinds("superseded-declaration")
    assert (row["source_file"], row["value"]) == ("docker-compose.prod.yml", "prod")


def test_a_raw_spelling_acknowledges_a_name_join(tmp_path):
    built = build(tmp_path, {"deploy": {"docker-compose.yml": 'services:\n  "my redis":\n'
                                                               '    image: redis:7\n'},
                             "my-redis": {"app/api.py": ROUTER},
                             "__canonical__": [{"service": "my redis", "owner": "deploy"}]})
    assert built.deploy("deploy/docker-compose.yml")["dst"] == "svc:my-redis@my-redis"
    assert not built.kinds("name-joined-service")


def test_two_blocks_naming_one_service_the_first_decides_and_the_other_is_reported(tmp_path):
    """D534-34: the first block after normalizing decides; `canonical-duplicate` names the
    ignored one."""
    built = build(tmp_path, {**HALVES, "__canonical__": [
        {"service": "my api", "owner": "orders", "path": "docker-compose.prod.yml"},
        {"service": "my-api", "owner": "orders", "path": "docker-compose.yml"}]})
    dup, = built.kinds("canonical-duplicate")
    assert dup["service"] == "my-api"
    lost, = [e for e in built.deploys if e.get("canonical") is False]
    assert lost["props"]["superseded_by"] == "orders/docker-compose.prod.yml"
