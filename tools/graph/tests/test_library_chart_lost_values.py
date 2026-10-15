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

import pytest  # noqa: E402

import tools.graph.build as B  # noqa: E402
from tools.graph.core.lib import repo_walk  # noqa: E402
from tools.graph.tests._estate import build, lay_down, spawn  # noqa: E402

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


@pytest.mark.parametrize("chart, reason", [
    (LIBRARY, "its chart is a library chart"),
    ("apiVersion: v2\nname: lib\nversion: 1.0.0\ntype: Library\n", "helm refuses to load its chart"),
])
def test_the_readers_own_row_for_a_values_file_claiming_nothing_says_so(tmp_path, chart, reason):
    """Review B21-9: the reader's own `unparsable` row said "the service it declares is missing
    from the graph" of a file its node says would claim nothing - fixing the parse changes
    nothing, B15b-23's complaint again."""
    built = build(tmp_path, {"a": {"charts/lib/Chart.yaml": chart,
                                   "charts/lib/values.yaml": "a: [\n"}})
    (row,) = [r for r in built.kinds("unparsable") if r["source_file"] == "charts/lib/values.yaml"]
    assert f"would claim nothing even read: {reason}" in row["why"]
    assert "missing from the graph" not in row["why"]


def test_an_unreadable_values_file_claiming_nothing_says_so(tmp_path, monkeypatch):
    real = repo_walk.read_text_reporting

    def locked(path):
        return ("", "PermissionError: locked") if path.name == "values.yaml" else real(path)

    monkeypatch.setattr(repo_walk, "read_text_reporting", locked)
    built = build(tmp_path, {"a": {"charts/lib/Chart.yaml": LIBRARY,
                                   "charts/lib/values.yaml": "fullnameOverride: lib\n"}})
    (row,) = [r for r in built.kinds("unreadable-file")
              if r["extractor"] == "helm-deployments" and r["source_file"] == "charts/lib/values.yaml"]
    assert "would claim nothing even read: its chart is a library chart" in row["why"]
    assert "missing from the graph" not in row["why"]


def test_a_control_values_file_that_does_not_parse_is_still_missing(tmp_path):
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": APPLICATION,
                                   "charts/web/values.yaml": "a: [\n"}})
    (row,) = [r for r in built.kinds("unparsable") if r["source_file"] == "charts/web/values.yaml"]
    assert "missing from the graph" in row["why"]


def test_an_umbrellas_values_file_lost_to_its_case_claims_nothing_for_its_subchart(tmp_path):
    """Review B21-4 at the case record: the build asks the reader whether a file lost to its
    case would claim, and an umbrella helm refuses for its subchart would not."""
    built = build(tmp_path, {"a": {
        "charts/umb/Chart.yaml": "apiVersion: v2\nname: umb\nversion: 1.0.0\n",
        "charts/umb/Values.yaml": "fullnameOverride: umb\n",
        "charts/umb/charts/sub/Chart.yaml": "apiVersion: v2\nname: sub\nversion: 1.0.0\n"
                                            "type: Library\n"}})
    (case,) = [r for r in built.kinds("deployment-off-case")
               if r["source_file"] == "charts/umb/Values.yaml"]
    assert ("would read none from it: helm refuses to load its chart (its subchart "
            "`charts/sub` cannot be loaded") in case["why"]


def test_a_subchart_a_declared_child_reads_is_not_judged_for_the_parent(tmp_path):
    """Review B21-20: the case record judged an umbrella's subcharts on the filesystem, a
    subchart inside a declared child component included - which the reader, keeping out of
    its children's files (T591), never reads. The two readings now agree: the off-case row
    does not say the file would claim nothing, and renamed it claims, unrefused."""
    estate = {"a": {"charts/umb/Chart.yaml": "apiVersion: v2\nname: umb\nversion: 1.0.0\n",
                    "charts/umb/Values.yaml": "fullnameOverride: umb\n"},
              "a/charts/umb/charts/sub": {"Chart.yaml": "apiVersion: v2\nname: sub\n"
                                                        "version: 1.0.0\ntype: Library\n"}}
    built = build(tmp_path / "lost", estate)
    (case,) = [r for r in built.kinds("deployment-off-case")
               if r["source_file"] == "charts/umb/Values.yaml"]
    assert "would read none from it" not in case["why"]
    renamed = {**estate, "a": {"charts/umb/Chart.yaml": estate["a"]["charts/umb/Chart.yaml"],
                               "charts/umb/values.yaml": "fullnameOverride: umb\n"}}
    claimed = build(tmp_path / "renamed", renamed)
    assert [e for e in claimed.deploys if e["src"] == "deploy:a/charts/umb/values.yaml"]


@pytest.mark.parametrize("pruned", ["build", "vendor"])
def test_a_subchart_the_walk_prunes_is_not_judged_for_the_parent(tmp_path, pruned):
    """Review B21-28: the case record judged an umbrella's subcharts on the filesystem, one in
    a directory the build's walk prunes included - which the reader is never handed, so no
    `chart-refused` row exists for it, and its bytes sit outside every stamp. The case record
    now judges from the files the walk found: the off-case row does not say the file would
    claim nothing, and renamed it claims, as the reader reads it."""
    assert pruned in repo_walk.DEFAULT_EXCLUDES
    umbrella = {"charts/umb/Chart.yaml": "apiVersion: v2\nname: umb\nversion: 1.0.0\n",
                f"charts/umb/charts/{pruned}/Chart.yaml": "apiVersion: v2\nname: sub\n"
                                                          "version: 1.0.0\ntype: Library\n"}
    built = build(tmp_path / "lost", {"a": {**umbrella,
                                            "charts/umb/Values.yaml": "fullnameOverride: umb\n"}})
    (case,) = [r for r in built.kinds("deployment-off-case")
               if r["source_file"] == "charts/umb/Values.yaml"]
    assert "would read none from it" not in case["why"]
    node = next(n for n in built.nodes if n["id"] == "deploy:a/charts/umb/Values.yaml")
    assert "claims_nothing" not in node["props"]
    claimed = build(tmp_path / "renamed", {"a": {**umbrella,
                                                 "charts/umb/values.yaml": "fullnameOverride: umb\n"}})
    assert [e for e in claimed.deploys if e["src"] == "deploy:a/charts/umb/values.yaml"]
    assert not claimed.kinds("chart-refused")


def _reader(hook: str) -> str:
    """A deployment reader's package, its `claims_nothing` as `hook` writes it."""
    return ("class _E:\n"
            "    name = 'inert'\n"
            "    INPUT_GLOBS = ('*.txt',)\n"
            "    DEPLOYMENT_READER = True\n"
            "    CLAIM_GLOBS = ('*.txt',)\n"
            + hook +
            "    def extract(self, ctx):\n"
            "        from tools.graph.core.model import ExtractResult\n"
            "        return ExtractResult()\n"
            "EXTRACTORS = (_E(),)\n")


TWO_ARGUMENTS = "    def claims_nothing(self, root, rel):\n        return ''\n"


@pytest.mark.parametrize("hook, refused", [
    ("    claims_nothing = 'no'\n", True),
    (TWO_ARGUMENTS, True),
    ("    def claims_nothing(self, root, rel, walked):\n        return ''\n", False),
])
def test_a_claims_nothing_hook_is_held_to_its_arguments_at_registration(tmp_path, hook,
                                                                         refused):
    """Review B21-27: a hook written to the two-argument contract the message stated took the
    build down with a traceback mid-activation; it is refused where every other contract is,
    naming the three arguments it is called with."""
    pkg = tmp_path / "reader"
    pkg.mkdir()
    (pkg / "__init__.py").write_bytes(_reader(hook).encode("utf-8"))
    if refused:
        with pytest.raises(B.BuildError,
                           match=r"`\(component root, path, walked\) -> reason`"):
            B.discover_extractors(tmp_path)
    else:
        (reg,) = B.discover_extractors(tmp_path)
        assert callable(reg.claims_nothing)


def test_a_profile_readers_two_argument_hook_is_refused_by_the_build_not_a_traceback(tmp_path):
    """The same refusal as a person meets it: a spawned build exits 2 with the message, and
    never with a traceback (a CLI's contract is a process)."""
    toml = lay_down(tmp_path, {"a": {"app.txt": "x\n"}})
    toml.write_bytes(toml.read_bytes().replace(b'profile = ""', b'profile = "./prof"'))
    prof = tmp_path / "ws" / "prof"
    prof.mkdir()
    (prof / "__init__.py").write_bytes(_reader(TWO_ARGUMENTS).encode("utf-8"))
    proc = spawn(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "Traceback" not in out
    assert "`claims_nothing` on 'inert'" in out and "(component root, path, walked)" in out
