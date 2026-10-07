"""A `[[canonical]]` block designates a file by the rule its reader ranks by (batch 15b).

A block acts through three readers: the link pass's decision among the packages claiming
its service, compose's env ranking (`covers` on each file declaring the service) and helm's
host-map ranking (`covers_package`, which reaches a chart's templates and an umbrella's
vendored subcharts). On `main` the link pass used `covers` alone, so a block naming a chart's
template, its values overlay or its `Chart.yaml` ranked the host map and decided no contest
(D15b-10). `_canonical` now holds the one reading per reader, dispatched on the node a
claim came from - its reader's own facts, `kind` and `ships_in`, never the config:

* `designates` - does the block reach this file at all;
* `names_exactly` - 2 where its path is the file's own, 1 where it names the file's own
  chart, 0 otherwise - two ranks, so the most specific package wins by an ORDERED test
  (ruling 14, D15b-119);
* `is_root_path` - a path that reads as the component's root takes the owner branch.

A node without its reader's facts (an older shard, an in-process caller) falls back to
`main`'s rule - `covers` on the claim's source file - and never passes on an absent list:
round 5 measured `None` raising inside `mark-canonical` and `[]` answering a false
"designates nothing".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.extractors import _canonical as C  # noqa: E402
from tools.graph.extractors import helm  # noqa: E402


def block(path: str | None = None, owner: str = "a", service: str = "web") -> dict:
    out = {"service": service, "owner": owner}
    if path is not None:
        out["path"] = path
    return out


COMPOSE = {"kind": "compose", "path": "deploy/docker-compose.yml"}
HELM = {"kind": "helm", "path": "charts/web/values.yaml", "ships_in": ["charts/web"]}
#: A subchart vendored under an umbrella's `charts/`: it ships in both.
SUB = {"kind": "helm", "path": "charts/umb/charts/web/values.yaml",
       "ships_in": ["charts/umb/charts/web", "charts/umb"]}
CONFIGMAP = {"kind": "configmap", "path": "charts/web/templates/cm.yaml",
             "ships_in": ["charts/web"]}


def test_the_chart_rule_is_one_function_both_layers_read():
    """`chart_root` and `claimed_chart` move into `_canonical`; the helm reader imports
    them back, so the extract layer and the link pass apply the same rule."""
    assert helm.chart_root is C.chart_root
    assert helm.claimed_chart is C.claimed_chart


# ── designates ───────────────────────────────────────────────────────────────


def test_a_compose_file_is_designated_as_compose_ranks_it():
    assert C.designates(block("deploy/docker-compose.yml"), "a", COMPOSE, COMPOSE["path"])
    assert C.designates(block("deploy"), "a", COMPOSE, COMPOSE["path"])
    assert not C.designates(block("deploy/docker-compose.ymll"), "a", COMPOSE, COMPOSE["path"])
    assert not C.designates(block("deploy/docker-compose.yml", owner="b"), "a", COMPOSE,
                            COMPOSE["path"]), "another component's block"


@pytest.mark.parametrize("path", [
    "charts/web/values.yaml",            # its own file
    "charts/web",                        # its chart's directory
    "charts",                            # a directory above it
    "charts/web/templates/cm.yaml",      # a template of its chart (red on main: D15b-10)
    "charts/web/values-prod.yaml",       # a values overlay of its chart (red on main)
    "charts/web/Chart.yaml",             # its chart's Chart.yaml (red on main)
])
def test_a_chart_is_designated_as_the_host_map_ranking_reads_it(path):
    assert C.designates(block(path), "a", HELM, HELM["path"])


def test_a_sibling_chart_and_a_typo_designate_nothing():
    """`valuess.yaml` would NOT be a typo here: by the reader's own rule any `values*.yaml`
    in a chart's root is a values overlay of that chart (B8-2)."""
    assert not C.designates(block("charts/web2/values.yaml"), "a", HELM, HELM["path"])
    assert not C.designates(block("charts/web/value.yaml"), "a", HELM, HELM["path"])


def test_an_umbrellas_file_designates_its_vendored_subchart():
    """T529: a subchart is deployed by its umbrella's release."""
    assert C.designates(block("charts/umb/values.yaml"), "a", SUB, SUB["path"])


def test_a_host_map_file_with_its_charts_reads_the_same_rule():
    assert C.designates(block("charts/web/values.yaml"), "a", CONFIGMAP, CONFIGMAP["path"])


#: Nodes that lack their reader's facts: no kind (helm's deployment nodes on `main`), a
#: helm kind with no `ships_in` (`main` publishes `kind: "configmap"` without it), and the
#: two absent lists round 5 measured breaking the pass.
FALLBACK_NODES = [
    {},
    {"path": "charts/web/values.yaml"},
    {"kind": "configmap", "path": "charts/web/values.yaml"},
    {"kind": "helm", "path": "charts/web/values.yaml", "ships_in": None},
    {"kind": "helm", "path": "charts/web/values.yaml", "ships_in": []},
]


@pytest.mark.parametrize("node", FALLBACK_NODES,
                         ids=["empty", "no-kind", "configmap-no-ships-in", "ships-in-none",
                              "ships-in-empty"])
def test_a_node_without_its_readers_facts_reads_by_covers_and_never_raises(node):
    src = "charts/web/values.yaml"
    assert C.designates(block(src), "a", node, src) is True
    assert C.designates(block("charts"), "a", node, src) is True
    # `covers` alone: a template names no other file, as on `main`.
    assert C.designates(block("charts/web/templates/cm.yaml"), "a", node, src) is False
    assert C.names_exactly(block(src), "a", node, src) == 2
    assert C.names_exactly(block("charts"), "a", node, src) == 0


# ── names_exactly ────────────────────────────────────────────────────────────


def test_the_own_file_outranks_the_own_chart():
    assert C.names_exactly(block("charts/web/values.yaml"), "a", HELM, HELM["path"]) == 2
    assert C.names_exactly(block("charts/web/Chart.yaml"), "a", HELM, HELM["path"]) == 1
    assert C.names_exactly(block("charts/web/templates/cm.yaml"), "a", HELM, HELM["path"]) == 1
    assert C.names_exactly(block("charts/web/values-prod.yaml"), "a", HELM, HELM["path"]) == 1
    assert C.names_exactly(block("deploy/docker-compose.yml"), "a", COMPOSE,
                           COMPOSE["path"]) == 2


def test_a_chart_directory_names_no_package_exactly():
    """D15b-74, ruling 14: an umbrella claimed by its own directory decides nothing, as on
    `main` - the generous reading (`path == ships_in[0]`) would select it, and is pinned
    out."""
    assert C.names_exactly(block("charts/web"), "a", HELM, HELM["path"]) == 0
    assert C.names_exactly(block("deploy"), "a", COMPOSE, COMPOSE["path"]) == 0


def test_an_umbrellas_file_names_the_umbrella_not_its_subchart():
    assert C.names_exactly(block("charts/umb/values.yaml"), "a", SUB, SUB["path"]) == 0
    assert C.names_exactly(block("charts/umb/charts/web/values.yaml"), "a", SUB,
                           SUB["path"]) == 2


def test_another_components_block_names_nothing():
    assert C.names_exactly(block("charts/web/values.yaml", owner="b"), "a", HELM,
                           HELM["path"]) == 0


# ── is_root_path ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [".", "./", "/", "//"])
def test_a_root_spelling_reads_as_the_owners_root(path):
    assert C.is_root_path(block(path)) is True


@pytest.mark.parametrize("path", [None, "", "charts", "./charts", "charts/web/values.yaml"])
def test_a_real_path_or_no_path_is_not_a_root_path(path):
    assert C.is_root_path(block(path)) is False


# ── review focus 4: T575's spellings decide as the plain path ────────────────


@pytest.mark.parametrize("spelling", ["charts\\web\\values.yaml", "./charts/web/values.yaml",
                                      "charts//web/values.yaml/"],
                         ids=["backslash", "dot-slash", "doubled-and-trailing"])
def test_t575_spellings_read_as_the_plain_path(spelling):
    assert C.designates(block(spelling), "a", HELM, HELM["path"]) is True
    assert C.names_exactly(block(spelling), "a", HELM, HELM["path"]) == 2
    assert C.names_exactly(block(spelling), "a", {}, "charts/web/values.yaml") == 2
    assert C.is_root_path(block(spelling)) is False


# ── task 10: mark-canonical decides by the readers' rules ────────────────────

from tools.graph.tests._estate import build, compose, svc  # noqa: E402

JAVA = "public class C { }\n"


def values(name: str) -> str:
    return f"fullnameOverride: {name}\n"


#: One component holding the live chart and a superseded one, both naming `web`.
TWO_TREES = {"a": {"charts/web/values.yaml": values("web"),
                   "charts/web/templates/cm.yaml": "apiVersion: v1\nkind: ConfigMap\n"
                                                   "metadata:\n  name: h\ndata:\n  X: y\n",
                   "legacy/web/values.yaml": values("web")}}


def decided_by(built) -> dict[str, tuple[bool, str]]:
    """Each claim on `svc:web@web`: (canonical, how it was decided)."""
    return {e["src"]: (e["canonical"], (e.get("props") or {}).get("canonical_by", ""))
            for e in built.deploys if e["dst"] == "svc:web@web"}


@pytest.mark.parametrize("path", [
    "charts/web/values.yaml", "charts/web",                 # as on main
    "charts/web/templates/cm.yaml",                         # red on main (D15b-10)
    "charts/web/values-prod.yaml",                          # red on main
    "charts/web/Chart.yaml",                                # red on main
])
def test_a_path_decides_as_the_reader_ranks(tmp_path, path):
    built = build(tmp_path, {**TWO_TREES, "__canonical__": [
        {"service": "web", "owner": "a", "path": path}]})
    assert not built.kinds("contested-service")
    assert decided_by(built) == {
        "deploy:a/charts/web/values.yaml": (True, f"path={path}"),
        "deploy:a/legacy/web/values.yaml": (False, f"path={path}")}


#: An umbrella with a vendored subchart, both naming `web` - one component.
UMBRELLA = {"a": {"charts/umb/Chart.yaml": "apiVersion: v2\nname: umb\nversion: 0.1.0\n",
                  "charts/umb/values.yaml": values("web"),
                  "charts/umb/templates/cm.yaml": "apiVersion: v1\nkind: ConfigMap\n"
                                                  "metadata:\n  name: h\ndata:\n  X: y\n",
                  "charts/umb/charts/web/values.yaml": values("web")}}


@pytest.mark.parametrize("path, winner", [
    ("charts/umb/values.yaml", "deploy:a/charts/umb/values.yaml"),             # as on main
    ("charts/umb/charts/web/values.yaml", "deploy:a/charts/umb/charts/web/values.yaml"),
    ("charts/umb/templates/cm.yaml", "deploy:a/charts/umb/values.yaml"),       # new
])
def test_the_most_specific_package_wins(tmp_path, path, winner):
    """Ruling 14, D15b-119: an ORDERED test - first the package whose own file the path
    names, then the one whose own chart it names."""
    built = build(tmp_path, {**UMBRELLA, "__canonical__": [
        {"service": "web", "owner": "a", "path": path}]})
    assert not built.kinds("contested-service")
    assert [src for src, (canonical, _) in decided_by(built).items() if canonical] == [winner]


@pytest.mark.parametrize("path", ["charts/umb", "charts"])
def test_a_directory_over_two_packages_decides_nothing(tmp_path, path):
    """D15b-74: the umbrella claimed by its own directory, or a directory above both,
    decides nothing, as on `main` - and the contest row says the block decides none."""
    built = build(tmp_path, {**UMBRELLA, "__canonical__": [
        {"service": "web", "owner": "a", "path": path}]})
    (row,) = built.kinds("contested-service")
    assert "the workspace's block for this service (`workspace.toml:" in row["why"]
    assert "decides none of these packages; change it to one of:" in row["why"]


def test_a_values_file_and_a_templates_values_file_are_decided_by_the_one_named(tmp_path):
    """D15b-119: both naming `web`; the old disjunction had both packages "exact" and
    decided neither, offering only spellings it would refuse again."""
    for named, other in (("charts/web/values.yaml", "charts/web/templates/values.yaml"),
                         ("charts/web/templates/values.yaml", "charts/web/values.yaml")):
        built = build(tmp_path / named.replace("/", "_"), {
            "a": {"charts/web/values.yaml": values("web"),
                  "charts/web/templates/values.yaml": values("web")},
            "__canonical__": [{"service": "web", "owner": "a", "path": named}]})
        assert decided_by(built)[f"deploy:a/{named}"][0] is True
        assert decided_by(built)[f"deploy:a/{other}"][0] is False


@pytest.mark.parametrize("path", [".", "./", "/", "//"])
def test_a_root_path_is_the_owner_branch(tmp_path, path):
    """D15b-9, D15b-30: one decision, recorded `owner=` (on `main` `.` and `./` were
    recorded `path=.`)."""
    built = build(tmp_path, {"a": {"charts/web/values.yaml": values("web")},
                             "b": {"charts/web/values.yaml": values("web")},
                             "__canonical__": [{"service": "web", "owner": "a", "path": path}]})
    assert decided_by(built)["deploy:a/charts/web/values.yaml"] == (True, "owner=a")


@pytest.mark.parametrize("path", ["docker-compose.yml", "docker-compose.override.yml"])
def test_a_block_naming_either_file_of_an_override_pair_designates_the_package(tmp_path, path):
    """T588's pair is one package: a block naming its base or its override selects it."""
    built = build(tmp_path, {
        "web": {"src/main/java/C.java": JAVA},
        "a": {"docker-compose.yml": compose(svc("web", "../web", None)),
              "docker-compose.override.yml": compose(svc("web", None, "x"))},
        "b": {"docker-compose.yml": compose(svc("web", "../web", None))},
        "__canonical__": [{"service": "web", "owner": "a", "path": path}]})
    assert not built.kinds("contested-service")
    assert built.deploy("a/docker-compose.yml")["canonical"] is True
    assert built.deploy("b/docker-compose.yml")["canonical"] is False


def test_a_duplicate_block_is_cited_at_its_line(tmp_path):
    """D15b-50: the installer records every block's file and line, so the row points at
    the block a developer would remove."""
    built = build(tmp_path, {"a": {"charts/web/values.yaml": values("my-api")},
                             "__canonical__": [{"service": "my api", "owner": "a"},
                                               {"service": "my-api", "owner": "a"}]})
    (row,) = built.kinds("canonical-duplicate")
    toml = (tmp_path / "ws" / "workspace.toml").read_text(encoding="utf-8").split("\n")
    second = [i + 1 for i, ln in enumerate(toml) if ln == "[[canonical]]"][1]
    assert (row["source_file"], row["line"]) == ("workspace.toml", second)
