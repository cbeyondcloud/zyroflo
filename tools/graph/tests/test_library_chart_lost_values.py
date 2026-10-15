"""A library chart's values file deploys nothing, whether or not it can be read (T625).

helm reads a library chart's `Chart.yaml` (`type: library`) and never installs it, so its
values file deploys nothing whatever it holds. Yet where it did not parse, the accounting row
said none of the owner's deployment files could be read and sent the developer to fix it; and
where it was lost to its case, the `deployment-off-case` row said `helm-deployments` reads
claims by it - followed, either remedy left the row standing with the ending that was true all
along (measured by batch 15b's review, B15b-23). The reader now publishes the chart's answer on
the file's `unread` node, the accounting rows word the file as claiming nothing and leave it
out of the "could not be read" ending, and the build's case record learns the same answer
through a hook a deployment reader declares - so `build.py` names no reader.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

LIBRARY = "apiVersion: v2\nname: lib\nversion: 1.0.0\ntype: library\n"
APPLICATION = "apiVersion: v2\nname: web\nversion: 1.0.0\n"
BLOCK = {"service": "web", "owner": "a"}
NOTHING = "would claim nothing even read: its chart is a library chart"


def test_a_library_charts_values_file_that_does_not_parse_claims_nothing_either_way(tmp_path):
    built = build(tmp_path, {"a": {"charts/lib/Chart.yaml": LIBRARY,
                                   "charts/lib/values.yaml": "web: [\n"},
                             "__canonical__": [BLOCK]})
    node = next(n for n in built.nodes if n["id"] == "deploy:a/charts/lib/values.yaml")
    assert node["props"]["unread"] == "unparsable"
    assert "library chart" in node["props"]["claims_nothing"]
    (row,) = built.kinds("canonical-unmatched")
    assert f"`charts/lib/values.yaml` {NOTHING}" in row["why"]
    assert "could be read" not in row["why"] and "fix it" not in row["why"]


def test_a_library_charts_values_file_lost_to_its_case_claims_nothing_either_way(tmp_path):
    built = build(tmp_path, {"a": {"charts/lib/Chart.yaml": LIBRARY,
                                   "charts/lib/Values.yaml": "fullnameOverride: web\n"},
                             "__canonical__": [BLOCK]})
    (case,) = built.kinds("deployment-off-case")
    assert "`helm-deployments` reads claims by it, but would read none from it: its chart " \
           "is a library chart" in case["why"]
    assert "so what it declares is missing" not in case["why"]
    node = next(n for n in built.nodes if n["id"] == "deploy:a/charts/lib/Values.yaml")
    assert "library chart" in node["props"]["claims_nothing"]
    (row,) = built.kinds("canonical-unmatched")
    assert f"`charts/lib/Values.yaml` {NOTHING}" in row["why"]
    assert "rename it" not in row["why"]


def test_an_application_charts_values_file_lost_to_its_case_is_lost_as_before(tmp_path):
    """The control: renaming this one makes it claim, so its rows keep their remedy."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": APPLICATION,
                                   "charts/web/Values.yaml": "fullnameOverride: web\n"},
                             "__canonical__": [BLOCK]})
    (case,) = built.kinds("deployment-off-case")
    assert "`helm-deployments` reads claims by it, so what it declares is missing" \
        in case["why"]
    node = next(n for n in built.nodes if n["id"] == "deploy:a/charts/web/Values.yaml")
    assert "claims_nothing" not in node["props"]
    (row,) = built.kinds("canonical-unmatched")
    assert "rename it as the patterns spell it" in row["why"]


def test_two_files_lost_to_their_case_are_said_apart_by_what_their_charts_mean(tmp_path):
    """One component, one library chart's values file and one application chart's, both lost
    to their case: one row each, since renaming one makes it claim and the other never."""
    built = build(tmp_path, {"a": {"charts/lib/Chart.yaml": LIBRARY,
                                   "charts/lib/Values.yaml": "fullnameOverride: web\n",
                                   "charts/web/Chart.yaml": APPLICATION,
                                   "charts/web/Values.yaml": "fullnameOverride: web\n"}})
    rows = {r["source_file"]: r["why"] for r in built.kinds("deployment-off-case")}
    assert sorted(rows) == ["charts/lib/Values.yaml", "charts/web/Values.yaml"]
    assert "would read none from it: its chart is a library chart" \
        in rows["charts/lib/Values.yaml"]
    assert "so what it declares is missing" in rows["charts/web/Values.yaml"]
