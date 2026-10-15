"""A chart helm refuses to load claims nothing (T624).

helm's loader unmarshals every chart's `Chart.yaml` into `chart.Metadata` and validates it
(`Metadata.Validate`, helm `release-3.16`), refusing the chart on a type it does not know as
spelled (`type: Library`), a `version` Masterminds semver does not parse, a missing `name`,
and more - yet the graph read such a chart's values file as a deployment claim (measured by
batch 15b's review, B15b-18; `main` the same). Now a refused chart's values file claims
nothing: an `unread` node says so, and a `chart-refused` row on its `Chart.yaml` quotes helm's
own words. Every chart helm loads still claims - each refusal class sits beside a control.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

VALUES = "fullnameOverride: web\n"
BASE = "apiVersion: v2\nname: web\nversion: 1.0.0\n"
VALUES_NODE = "deploy:a/charts/web/values.yaml"

REFUSED = {
    "a-type-spelled-otherwise": (BASE + "type: Library\n",
                                 "chart.metadata.type must be application or library"),
    "a-type-key-in-another-case": (BASE + "Type: Library\n",
                                   "chart.metadata.type must be application or library"),
    "the-name-variant-sorting-last": ("apiVersion: v2\nversion: 1.0.0\nNAME: web\n"
                                      "Name: a/web\n", 'chart.metadata.name "a/web" is invalid'),
    "the-name-variant-sorting-last-written-first": (
        "apiVersion: v2\nversion: 1.0.0\nName: a/web\nNAME: web\n",
        'chart.metadata.name "a/web" is invalid'),
    "a-name-of-a-separator-control-go-drops": ('apiVersion: v2\nname: "\\x1c"\n'
                                               'version: 1.0.0\n',
                                               "chart.metadata.name is required"),
    "a-null-key": (BASE + "~: x\n", "cannot load Chart.yaml"),
    "a-null-key-in-annotations": (BASE + "annotations:\n  ~: x\n", "cannot load Chart.yaml"),
    "a-null-key-under-a-key-no-field-reads": (BASE + "extra:\n  ~: x\n",
                                              "cannot load Chart.yaml"),
    # Review B21-32: the key B21-21 named sits under a dependency's key no field reads - a
    # null key directly in the entry was refused before the fix, so it is the control only.
    "a-null-key-under-a-dependencys-extra-key": (
        BASE + "dependencies:\n  - name: lib\n    extra:\n      ~: x\n", "cannot load Chart.yaml"),
    "a-null-key-directly-in-a-dependency": (BASE + "dependencies:\n  - name: lib\n    ~: x\n",
                                            "cannot load Chart.yaml"),
    # Review B21-31: a float key is named as `sigs.k8s.io/yaml` writes it - float32 text.
    "a-float-key-named-as-go-writes-it": (BASE + "annotations:\n  16777217.0:\n    a: b\n",
                                          "`annotations.1.6777216e+07` is a mapping"),
    "four-version-segments": ("apiVersion: v2\nname: web\nversion: 1.0.0.0\n",
                              'chart.metadata.version "1.0.0.0" is invalid'),
    "a-prerelease-with-a-leading-zero": ("apiVersion: v2\nname: web\nversion: 1.0.0-01\n",
                                         'chart.metadata.version "1.0.0-01" is invalid'),
    "a-version-a-float-writes-with-an-exponent": (
        "apiVersion: v2\nname: web\nversion: 1000000.0\n",
        'chart.metadata.version "1e+06" is invalid'),
    "no-version": ("apiVersion: v2\nname: web\n", "chart.metadata.version is required"),
    "a-version-that-is-a-list": ("apiVersion: v2\nname: web\nversion: [1]\n",
                                 "`version` is a list, not a string"),
    "no-name": ("apiVersion: v2\nversion: 1.0.0\n", "chart.metadata.name is required"),
    "a-name-with-a-separator": ("apiVersion: v2\nname: a/web\nversion: 1.0.0\n",
                                'chart.metadata.name "a/web" is invalid'),
    "a-null-maintainer": (BASE + "maintainers:\n  - ~\n",
                          "maintainers must not contain empty or null nodes"),
    "an-alias-with-a-dot": (BASE + "dependencies:\n  - name: lib\n    alias: my.lib\n",
                            'dependency "lib" has disallowed characters in the alias'),
    "two-dependencies-of-one-name": (BASE + "dependencies:\n  - name: lib\n  - name: lib\n",
                                     'more than one dependency with name or alias "lib"'),
    "a-boolean-that-is-text": (BASE + "deprecated: \"yes\"\n", "`deprecated` is not a boolean"),
    "not-yaml": ("apiVersion: v2\nname: [web\n", "cannot load Chart.yaml"),
}


@pytest.mark.parametrize("shape", sorted(REFUSED))
def test_a_chart_helm_refuses_claims_nothing_and_says_why(tmp_path, shape):
    text, fragment = REFUSED[shape]
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": text,
                                   "charts/web/values.yaml": VALUES}})
    assert not [e for e in built.deploys if e["src"] == VALUES_NODE]
    (row,) = built.kinds("chart-refused")
    assert row["source_file"] == "charts/web/Chart.yaml" and fragment in row["error"], row
    node = next(n for n in built.nodes if n["id"] == VALUES_NODE)
    assert node["props"]["unread"] == "chart-refused"


def test_a_chart_directory_holding_chart_yml_alone_is_refused(tmp_path):
    """helm reads `Chart.yaml` and nothing else: a directory with `Chart.yml` alone is not a
    chart it loads ("Chart.yaml file is missing")."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yml": BASE,
                                   "charts/web/values.yaml": VALUES}})
    assert not [e for e in built.deploys if e["src"] == VALUES_NODE]
    (row,) = built.kinds("chart-refused")
    assert (row["source_file"], row["error"]) == ("charts/web/Chart.yml",
                                                  "Chart.yaml file is missing")


def test_a_refused_chart_with_no_values_file_is_refused_not_unnamed(tmp_path):
    """Ruling 20 names a chart with templates and no values file `unnamed-deployment`, whose
    remedy - add a values file - would leave a chart helm refuses still refused."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": BASE + "type: Library\n",
                                   "charts/web/templates/deploy.yaml": "kind: Deployment\n"}})
    assert not built.kinds("unnamed-deployment")
    (row,) = built.kinds("chart-refused")
    assert row["source_file"] == "charts/web/Chart.yaml"
    node = next(n for n in built.nodes if n["id"] == "deploy:a/charts/web/Chart.yaml")
    assert node["props"]["unread"] == "chart-refused"


ACCEPTED = {
    "a-float-version-written-as-text": "apiVersion: v2\nname: web\nversion: 1.0\n",
    "an-integer-version-written-as-text": "apiVersion: v2\nname: web\nversion: 2\n",
    "a-version-with-a-v": "apiVersion: v2\nname: web\nversion: v1.2\n",
    "a-leading-zero-in-the-major": "apiVersion: v2\nname: web\nversion: 01.2.3\n",
    "a-large-float-written-plainly": "apiVersion: v2\nname: web\nversion: 100000.0\n",
    "no-api-version": "name: web\nversion: 1.0.0\n",
    "an-empty-type": BASE + "type: \"\"\n",
    "an-alias-of-word-characters": BASE + "dependencies:\n  - name: lib\n    alias: my_lib-2\n",
    "a-later-application-type": BASE + "type: Library\ntype: application\n",
    "a-type-variant-written-after-the-exact-key": BASE + "type: application\nTYPE: Library\n",
    "a-type-variant-written-before-the-exact-key": BASE + "TYPE: Library\ntype: application\n",
}


@pytest.mark.parametrize("shape", sorted(ACCEPTED))
def test_a_chart_helm_loads_still_claims(tmp_path, shape):
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": ACCEPTED[shape],
                                   "charts/web/values.yaml": VALUES}})
    assert [e for e in built.deploys if e["src"] == VALUES_NODE]
    assert not built.kinds("chart-refused")


def test_a_library_type_in_another_key_case_is_read_as_helm_reads_it(tmp_path):
    """`encoding/json` matches `Type:` to the `type` field, so helm reads a library chart: its
    values file deploys nothing, and no row calls the chart refused."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": BASE + "Type: library\n",
                                   "charts/web/values.yaml": VALUES}})
    assert not [e for e in built.deploys if e["src"] == VALUES_NODE]
    assert not built.kinds("chart-refused")


def test_a_block_naming_a_refused_chart_is_told_to_fix_its_chart(tmp_path):
    """The accounting rows read the refused chart's values file as read - never as a file
    that could not be read - and give the remedy that ends the row: what the chart's row
    names, which is the chart's own `Chart.yaml` here and a subchart's for an umbrella
    (review B21-4)."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": BASE + "type: Library\n",
                                   "charts/web/values.yaml": VALUES},
                             "__canonical__": [{"service": "web", "owner": "a"}]})
    (row,) = built.kinds("canonical-unmatched")
    assert "could be read" not in row["why"]
    assert ("`charts/web/values.yaml` is in a chart helm refuses to load, so it deploys "
            "nothing until the chart loads; fix what its `chart-refused` row names") in row["why"]
    fixed = build(tmp_path / "fixed", {"a": {"charts/web/Chart.yaml": BASE,
                                             "charts/web/values.yaml": VALUES},
                                       "__canonical__": [{"service": "web", "owner": "a"}]})
    assert not fixed.kinds("canonical-unmatched", "chart-refused")


UMBRELLA = "apiVersion: v2\nname: umb\nversion: 1.0.0\n"
UMB_NODE = "deploy:a/charts/umb/values.yaml"
LIBRARY_TYPO = "apiVersion: v2\nname: sub\nversion: 1.0.0\ntype: Library\n"
TYPE_REASON = "chart.metadata.type must be application or library"


def umbrella(sub: str = "sub", chart: str = LIBRARY_TYPO) -> dict:
    """`umb` vendoring one subchart under `charts/`, each with a values file naming itself."""
    return {"a": {"charts/umb/Chart.yaml": UMBRELLA,
                  "charts/umb/values.yaml": "fullnameOverride: umb\n",
                  f"charts/umb/charts/{sub}/Chart.yaml": chart,
                  f"charts/umb/charts/{sub}/values.yaml": f"fullnameOverride: {sub}\n"}}


def test_an_umbrella_helm_refuses_for_its_subchart_claims_nothing(tmp_path):
    """Review B21-4: helm's loader loads each directory under a chart's `charts/` as a
    subchart - all but one named from `_` or `.` - and refuses the chart when it refuses one
    ("error unpacking <name> in <chart>"), so the umbrella claims nothing either; its row
    says which subchart, and that the fix is there."""
    built = build(tmp_path, umbrella())
    assert not [e for e in built.deploys if e["src"] == UMB_NODE]
    node = next(n for n in built.nodes if n["id"] == UMB_NODE)
    assert node["props"]["unread"] == "chart-refused"
    rows = {r["source_file"]: r for r in built.kinds("chart-refused")}
    assert set(rows) == {"charts/umb/Chart.yaml", "charts/umb/charts/sub/Chart.yaml"}
    umb = rows["charts/umb/Chart.yaml"]
    assert umb["error"] == f"its subchart `charts/sub` cannot be loaded: {TYPE_REASON}"
    assert "fix `charts/sub`" in umb["why"] and "fix the chart's `Chart.yaml`" not in umb["why"]


def test_a_refusal_reaches_every_umbrella_outward(tmp_path):
    estate = umbrella("mid", "apiVersion: v2\nname: mid\nversion: 1.0.0\n")
    estate["a"]["charts/umb/charts/mid/charts/inner/Chart.yaml"] = LIBRARY_TYPO
    built = build(tmp_path, estate)
    rows = {r["source_file"]: r for r in built.kinds("chart-refused")}
    assert rows["charts/umb/Chart.yaml"]["error"] == (
        f"its subchart `charts/mid` cannot be loaded: its subchart `charts/inner` cannot be "
        f"loaded: {TYPE_REASON}")
    # Review B21-16: `inner`, with no values file and no templates, has no row of its own,
    # so no remedy may send anyone to one; the reason itself follows the chain down.
    assert "charts/umb/charts/mid/charts/inner/Chart.yaml" not in rows
    for row in rows.values():
        assert "its own row" not in row["why"]
        assert "the reason above follows it down to the chart helm refuses" in row["why"]
    assert not [e for e in built.deploys if e["src"] in (
        UMB_NODE, "deploy:a/charts/umb/charts/mid/values.yaml")]


@pytest.mark.parametrize("sub, chart", [
    ("_sub", LIBRARY_TYPO),                                     # helm skips it
    ("sub", "apiVersion: v2\nname: sub\nversion: 1.0.0\n"),     # the remedy, followed
])
def test_an_umbrella_whose_subcharts_helm_loads_or_skips_still_claims(tmp_path, sub, chart):
    built = build(tmp_path, umbrella(sub, chart))
    assert [e for e in built.deploys if e["src"] == UMB_NODE]
    assert "charts/umb/Chart.yaml" not in {r["source_file"] for r in built.kinds("chart-refused")}
