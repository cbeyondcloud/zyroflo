"""Rendering a multi-component `profile.yml` — and the self-contradiction it fixes.

`profile.yml` is the one file that makes the generic harness fit a workspace, and it is
copied verbatim into every adopter's repo. Until now its `gate_profile` was hardcoded
to `solo` while `LIFECYCLE.md` calls `solo` a **hard refusal** above one component — so
the first multi-component profile the installer ever rendered would have been rejected
by the very spec that generated it.

The YAML here is hand-emitted rather than dumped by PyYAML, because the installer is
stdlib-only by gate. That trade has a cost — escaping is now the installer's problem —
so these tests parse what it emits rather than eyeballing the string.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = ROOT / "sdlc" / "LIFECYCLE.md"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()

BASE = {"stack": "python", "test_command": "pytest -q", "coverage_command": "pytest --cov",
        "coverage_threshold": 90, "lint_command": "", "build_command": "",
        "deploy_command": "", "vcs": {"commit_command": "", "push_command": "",
                                      "deny_raw_git": False, "notes": ""}}


def component(name: str, **kw) -> dict:
    return {"name": name, "path": kw.pop("path", name), "role": kw.pop("role", "authoritative"),
            "shallow": kw.pop("shallow", False), "known_failures": kw.pop("known_failures", []),
            **{**BASE, **kw}}


def render(*components: dict) -> str:
    return si.render_profile({**BASE, "components": list(components)}, ["claude"])


# ── the single-repo path must not move ────────────────────────────────────────
def test_a_single_repo_profile_is_unchanged(tmp_path: Path):
    """Three new tokens landed in the template. The drop-in install path is the
    product for everyone who never runs --workspace, so it has to render exactly what
    it always did."""
    text = si.render_profile(BASE, ["claude"])
    doc = yaml.safe_load(text)
    assert "@@" not in text
    assert doc["scope"] == "repo"
    assert doc["gate_profile"] == "solo"
    assert "components" not in doc, "a single-repo profile must not carry a components list"


def test_the_components_placeholder_stays_commented_documentation(tmp_path: Path):
    """With no components the slot renders the commented example block. It is the only
    place the shape is documented for someone hand-writing one."""
    text = si.render_profile(BASE, ["claude"])
    assert "# components:" in text
    assert "#     role: authoritative" in text, "the placeholder must document `role`"


# ── the fix ───────────────────────────────────────────────────────────────────
def test_a_multi_component_profile_is_never_solo():
    """THE self-contradiction this item exists to remove."""
    doc = yaml.safe_load(render(component("orders"), component("billing")))
    assert doc["gate_profile"] == "team"
    assert doc["scope"] == "estate"


def test_the_spec_still_says_what_the_renderer_assumes():
    """Pin the code to the document. If someone relaxes the rule in LIFECYCLE.md, this
    fails and they have to change the renderer deliberately rather than leave the two
    disagreeing — which is the exact state this item found them in."""
    text = LIFECYCLE.read_text(encoding="utf-8")
    assert "`solo` is rejected when `components > 1`" in text
    assert "hard refusal" in text


def test_one_component_stays_solo_and_repo_scoped():
    """A one-component workspace is the degenerate case, not a small estate: there is
    no cross-component closure to compute and nothing for `team` granularity to buy."""
    doc = yaml.safe_load(render(component("orders")))
    assert doc["scope"] == "repo" and doc["gate_profile"] == "solo"


def test_one_component_still_records_the_component():
    """Scope is about which capabilities are active; the list is about what was found.
    Discovery ran, so its result is recorded either way."""
    doc = yaml.safe_load(render(component("orders", path="services/orders")))
    assert [c["name"] for c in doc["components"]] == ["orders"]
    assert doc["components"][0]["path"] == "services/orders"


# ── the emitted YAML is real YAML ─────────────────────────────────────────────
def test_the_rendered_profile_parses_and_round_trips():
    doc = yaml.safe_load(render(
        component("orders", path="services/orders", stack="java-maven",
                  test_command="mvn -q test", role="authoritative"),
        component("legacy-ui", role="legacy", stack="javascript", test_command="npm test")))
    got = {c["name"]: c for c in doc["components"]}
    assert set(got) == {"orders", "legacy-ui"}
    assert got["orders"]["stack"] == "java-maven"
    assert got["orders"]["test_command"] == "mvn -q test"
    assert got["legacy-ui"]["role"] == "legacy"


def test_component_order_is_preserved_as_given():
    doc = yaml.safe_load(render(component("a"), component("b"), component("c")))
    assert [c["name"] for c in doc["components"]] == ["a", "b", "c"]


@pytest.mark.parametrize("value", [
    'dotnet test --collect:"XPlat Code Coverage"',   # the escaping case already known
    "echo 'single quotes'",
    "run --flag=a:b",                                 # a colon, which YAML cares about
    "a\\b",                                           # a backslash
    "test # not a comment",                           # a hash inside a value
])
def test_a_command_with_awkward_characters_survives_the_round_trip(value: str):
    """Hand-emitted YAML means escaping is the installer's problem. A value that
    breaks the parse takes the whole profile with it, and the symptom is a YAML error
    in the adopter's repo pointing at a line they never wrote."""
    doc = yaml.safe_load(render(component("orders", test_command=value)))
    assert doc["components"][0]["test_command"] == value


def test_a_path_with_spaces_survives():
    """Routine in real workspaces, and fatal to naive quoting."""
    doc = yaml.safe_load(render(component("orders", path="my services/orders api")))
    assert doc["components"][0]["path"] == "my services/orders api"


def test_an_empty_command_renders_as_an_empty_string_not_null():
    """`deploy_command:` with nothing after it parses as None, and the convention is
    that a BLANK STRING skips the step. None would make a downstream `.strip()` crash
    instead of skipping."""
    doc = yaml.safe_load(render(component("orders", build_command="")))
    assert doc["components"][0]["build_command"] == ""


def test_known_failures_renders_as_an_empty_list_not_a_blank():
    doc = yaml.safe_load(render(component("orders")))
    assert doc["components"][0]["known_failures"] == []


def test_known_failures_carries_its_entries():
    doc = yaml.safe_load(render(component("orders", known_failures=["test_a", "test_b"])))
    assert doc["components"][0]["known_failures"] == ["test_a", "test_b"]


def test_the_vcs_block_renders_per_component():
    doc = yaml.safe_load(render(component("orders", vcs={
        "commit_command": './checkin.sh "{message}"', "push_command": "",
        "deny_raw_git": True, "notes": "open-core split"})))
    vcs = doc["components"][0]["vcs"]
    assert vcs["deny_raw_git"] is True
    assert vcs["commit_command"] == './checkin.sh "{message}"'
    assert vcs["notes"] == "open-core split"


# ── what is deliberately NOT written down ─────────────────────────────────────
def test_shallow_is_not_persisted_into_the_profile():
    """It is true at install time and false the moment someone runs
    `git fetch --unshallow`. A config value that silently goes stale is worse than a
    `.git/shallow` stat at the point of use — so the installer REPORTS it and does not
    record it."""
    text = render(component("orders", shallow=True))
    assert "shallow" not in text
    assert yaml.safe_load(text)["components"][0]["name"] == "orders"


# ── discovery to profile, end to end ──────────────────────────────────────────
def test_discovered_components_render_into_a_valid_profile(tmp_path: Path):
    """The only test here that runs the real discovery. Everything above builds
    component dicts directly, which would happily keep passing if discovery started
    emitting a different shape."""
    root = tmp_path / "workspaces"
    root.mkdir()
    for name, marker, body in [("orders", "pom.xml", "<project/>"),
                               ("billing", "go.mod", "module billing\n"),
                               ("web-ui", "package.json", '{"scripts": {"test": "jest"}}')]:
        repo = root / name
        (repo / ".git").mkdir(parents=True)
        (repo / marker).write_text(body, encoding="utf-8")

    report = si.discover_components({
        "schema_version": 1, "config_path": tmp_path / "sdlc-workspace.toml", "root": root,
        "name": "", "include": [], "exclude": [], "overrides": {}, "canonical": []})
    text = si.render_profile({**BASE, "components": report["components"]}, ["claude"])

    assert "@@" not in text, "unrendered placeholder reached the profile"
    doc = yaml.safe_load(text)
    assert doc["scope"] == "estate" and doc["gate_profile"] == "team"
    got = {c["name"]: c["stack"] for c in doc["components"]}
    assert got == {"orders": "java-maven", "billing": "go", "web-ui": "javascript"}


def test_every_field_discovery_produces_is_either_rendered_or_deliberately_dropped(tmp_path: Path):
    """A field added to discovery and forgotten in the renderer is invisible: the
    profile is still valid YAML and still installs, it just quietly lacks the thing.

    `shallow` is the one intentional omission and is named here, so dropping a second
    field silently requires editing this list and saying why.
    """
    root = tmp_path / "workspaces"
    (root / "orders" / ".git").mkdir(parents=True)
    report = si.discover_components({
        "schema_version": 1, "config_path": tmp_path / "sdlc-workspace.toml", "root": root,
        "name": "", "include": [], "exclude": [], "overrides": {}, "canonical": []})

    produced = set(report["components"][0])
    rendered = set(yaml.safe_load(render(*report["components"]))["components"][0])
    deliberately_dropped = {"shallow"}
    assert produced - rendered == deliberately_dropped, (
        f"discovery produces fields the renderer drops without a decision: "
        f"{sorted(produced - rendered - deliberately_dropped)}"
    )
