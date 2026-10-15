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
    "the-later-of-two-type-keys": (BASE + "type: application\nTYPE: Library\n",
                                   "chart.metadata.type must be application or library"),
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
    that could not be read - and give the remedy that ends the row: fix `Chart.yaml`."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": BASE + "type: Library\n",
                                   "charts/web/values.yaml": VALUES},
                             "__canonical__": [{"service": "web", "owner": "a"}]})
    (row,) = built.kinds("canonical-unmatched")
    assert "could be read" not in row["why"]
    assert ("`charts/web/values.yaml` is in a chart helm refuses to load (see its row), so it "
            "deploys nothing until the chart loads; fix the chart's `Chart.yaml`") in row["why"]
    fixed = build(tmp_path / "fixed", {"a": {"charts/web/Chart.yaml": BASE,
                                             "charts/web/values.yaml": VALUES},
                                       "__canonical__": [{"service": "web", "owner": "a"}]})
    assert not fixed.kinds("canonical-unmatched", "chart-refused")
