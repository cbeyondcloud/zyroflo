"""A run that could not see the whole workspace says so, and judges no accounting row.

Batch 15b (T573 decision 3, rulings 15 and 16). `canonical-unmatched` and
`apart-unmatched` are judgments of ABSENCE - "the graph read no deployment of `web` in
`a`" - so a run that could not see every component through its deployment readers must not
make them: a claim or a reader it could not see could change the answer. Such a run is
NARROWED, and its report says so in one plain line, with the way each reason is lifted:

* a build: `--components` leaving a discovered component out, `--only`, a deployment
  reader that failed, or one that could not parse its files at all (no parser installed);
* a `--from-snapshot` load: a component with no shard, a refused shard, or one that is
  stale by `--check-fresh`'s own verdict - which the load now computes by the one helper
  `check_freshness` prints from (ruling 16).

A failure of any other reader narrows nothing. `narrowed` is run-scoped: it rides in the
link passes' own copies of the config and never in the dict a freshness stamp digests.

Everything here is synthetic and names no real workspace.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.core.lib import props  # noqa: E402
from tools.graph.extractors import helm, openapi  # noqa: E402
from tools.graph.tests._estate import ROOT, build  # noqa: E402

JUDGED_NEITHER = ("so it judged neither `canonical-unmatched` nor `apart-unmatched`, which "
                  "what it could not see could change")
APART_CLAUSE = ("an `[[apart]]` refusal, judged over fewer claims, may move a chart onto its "
                "component's own node where a whole build holds it back, because the package "
                "that claims that node was not read")
STALE_CONFIG = "the workspace configuration changed since its shard was published"

#: Two components: `a` holds a chart, `b` holds code.
ESTATE = {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                "charts/web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n"},
          "b": {"app.py": "print('b')\n"}}


def notes(where: Path) -> list[str]:
    report = json.loads((where / "out" / "build_report.json").read_text(encoding="utf-8"))
    return report.get("notes", [])


def narrowed(where: Path) -> list[str]:
    return [n for n in notes(where) if n.startswith(("this run was narrowed",
                                                     "this load was narrowed"))]


def spawn(where: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "out"),
         "--run-id", "fixed", *flags],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


# ── a build ──────────────────────────────────────────────────────────────────


def test_a_whole_workspace_build_is_not_narrowed(tmp_path):
    assert build(tmp_path, ESTATE).code == 0
    assert narrowed(tmp_path) == []


def test_a_components_run_leaving_one_out_is_narrowed(tmp_path):
    """Spawned: the line a person reads, and the way on that lifts it."""
    build(tmp_path, ESTATE)
    proc = spawn(tmp_path, "--components", "a")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (line,) = narrowed(tmp_path)
    assert line.startswith("this run was narrowed (`--components` left out 1 component(s)), ")
    assert JUDGED_NEITHER in line and APART_CLAUSE in line
    assert "build the whole workspace" in line
    assert line in proc.stdout, "the report's line is printed as the build's notes are"
    # Followed: the whole workspace is not narrowed.
    assert spawn(tmp_path).returncode == 0
    assert narrowed(tmp_path) == []


def test_a_components_run_naming_every_component_is_not_narrowed(tmp_path):
    build(tmp_path, ESTATE)
    assert build(tmp_path, None, "--components", "a,b").code == 0
    assert narrowed(tmp_path) == []


def test_an_only_run_is_narrowed(tmp_path):
    assert build(tmp_path, ESTATE, "--only", "helm-deployments").code == 0
    (line,) = narrowed(tmp_path)
    assert line.startswith("this run was narrowed (`--only`), ")
    assert JUDGED_NEITHER in line


def test_narrowed_never_reaches_a_stamp(tmp_path):
    """D15b-20: an `--only` build, then a full publish, then `--check-fresh` at exit 0 -
    `narrowed` rides in the passes' copies, never in the config a stamp digests."""
    assert build(tmp_path, ESTATE, "--only", "helm-deployments").code == 0
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    proc = spawn(tmp_path, "--check-fresh")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _raising(self, ctx):
    raise RuntimeError("a stub reader failing mid-run")


def test_a_failed_deployment_reader_narrows(tmp_path, monkeypatch):
    monkeypatch.setattr(helm.DeploymentsExtractor, "extract", _raising)
    build(tmp_path, ESTATE)
    (line,) = narrowed(tmp_path)
    assert line.startswith("this run was narrowed (`helm-deployments` failed), ")
    assert "fix what `helm-deployments`'s warning names" in line
    assert "a whole-workspace build is narrowed too while a deployment reader fails or has " \
           "no parser" in line
    # Followed - the reader no longer fails - the run is not narrowed.
    monkeypatch.undo()
    build(tmp_path, None)
    assert narrowed(tmp_path) == []


def test_a_failed_reader_of_anything_else_narrows_nothing(tmp_path, monkeypatch):
    """Round 8 measured a failed `maven-modules` narrowing the whole run: only a reader
    whose facts the judgments read narrows it."""
    monkeypatch.setattr(openapi.OpenApiExtractor, "extract", _raising)
    build(tmp_path, ESTATE)
    extractors = json.loads((tmp_path / "out" / "build_report.json")
                            .read_text(encoding="utf-8"))["extractors"]
    assert {e["name"]: e["failed"] for e in extractors}["openapi-specs"], \
        "the stub must actually fail for this to say anything"
    assert narrowed(tmp_path) == []


def test_a_deployment_reader_with_no_parser_narrows(tmp_path, monkeypatch):
    """D15b-130: with PyYAML absent the deployment readers file `grammar-unavailable`
    instead of failing, and round 13 measured both accounting rows judged over a graph
    with no deployment facts."""
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    build(tmp_path, ESTATE)
    (line,) = narrowed(tmp_path)
    assert "`helm-deployments` could not parse its files - no parser installed" in line
    assert "install the parser `helm-deployments`'s `grammar-unavailable` row names" in line
    monkeypatch.undo()
    build(tmp_path, None)
    assert narrowed(tmp_path) == []


# ── a load ───────────────────────────────────────────────────────────────────


def test_a_shard_published_with_no_parser_narrows_its_load(tmp_path, monkeypatch):
    """Review B15b-21: a build whose deployment reader had no parser is narrowed, yet the
    shard it published loaded as current, and the load judged both accounting rows over a
    shard holding no deployment facts - telling a developer to remove two correct blocks.
    The load reads the shard's own `grammar-unavailable` rows by the build's rule; followed
    (the parser installed, the shard re-published), it judges, and the blocks stand."""
    estate = {**ESTATE, "__canonical__": [{"service": "web", "owner": "a"}],
              "__apart__": [{"service": "web", "component": "a"}]}
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    monkeypatch.undo()
    loaded = build(tmp_path, None, "--from-snapshot")
    assert not loaded.kinds("canonical-unmatched", "apart-unmatched")
    (line,) = narrowed(tmp_path)
    assert line.startswith("this load was narrowed (")
    assert ("`helm-deployments` could not parse its files when its shard was published - no "
            "parser installed: a") in line
    assert JUDGED_NEITHER in line
    assert ("install the parser each `grammar-unavailable` row names, then re-publish the "
            "shards it names") in line
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    judged = build(tmp_path, None, "--from-snapshot")
    assert narrowed(tmp_path) == []
    assert not judged.kinds("canonical-unmatched", "apart-unmatched")


def test_a_current_snapshot_loads_unnarrowed(tmp_path):
    assert build(tmp_path, ESTATE, "--publish-snapshot").code == 0
    proc = spawn(tmp_path, "--from-snapshot")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert narrowed(tmp_path) == []


def test_a_missing_shard_narrows_a_load(tmp_path):
    assert build(tmp_path, ESTATE, "--publish-snapshot").code == 0
    import shutil
    shutil.rmtree(tmp_path / "ws" / "b" / ".sdlc")
    proc = spawn(tmp_path, "--from-snapshot")
    (line,) = narrowed(tmp_path)
    assert line.startswith("this load was narrowed (no shard: b), ")
    assert JUDGED_NEITHER in line and APART_CLAUSE in line
    assert "re-publish" in line and "`--check-fresh` then exits 0" in line
    assert line in proc.stdout, "the snapshot branch prints its notes"


def test_a_stale_shard_narrows_a_load_with_check_freshs_reason(tmp_path):
    assert build(tmp_path, ESTATE, "--publish-snapshot").code == 0
    (tmp_path / "ws" / "b" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    spawn(tmp_path, "--from-snapshot")
    (line,) = narrowed(tmp_path)
    reason = B.check_freshness(tmp_path / "ws" / "workspace.toml")["freshness"]["stale"]["b"]
    assert f"stale - {reason}: b" in line, line
    # Followed: re-published, `--check-fresh` exits 0 and the load judges.
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    assert spawn(tmp_path, "--check-fresh").returncode == 0
    spawn(tmp_path, "--from-snapshot")
    assert narrowed(tmp_path) == []


def test_a_configuration_change_says_what_the_configuration_covers(tmp_path):
    """D15b-96: `--check-fresh`'s reason is kept word for word, and the line adds what that
    configuration now covers (D15b-84)."""
    assert build(tmp_path, ESTATE, "--publish-snapshot").code == 0
    toml = tmp_path / "ws" / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[[canonical]]\nservice = "web"\nowner = "a"\n', encoding="utf-8")
    spawn(tmp_path, "--from-snapshot")
    (line,) = narrowed(tmp_path)
    assert f"stale - {STALE_CONFIG}" in line
    assert ("the `[[canonical]]` blocks, the layout, the profile's name, and each "
            "component's own path as written, role, stack and shallow flag") in line


def test_a_long_group_admits_its_cap(tmp_path):
    estate = {f"c{i:02d}": {"app.py": "print(1)\n"} for i in range(8)}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    import shutil
    for i in range(8):
        shutil.rmtree(tmp_path / "ws" / f"c{i:02d}" / ".sdlc")
    spawn(tmp_path, "--from-snapshot")
    (line,) = narrowed(tmp_path)
    assert "no shard: c00, c01, c02, c03, c04 and 3 more" in line


# ── the run-scoped keys reach each pass, and nothing else ────────────────────


def test_each_pass_gets_its_own_copy_with_the_run_scoped_keys():
    """D15b-20: the caller's config is the dict the stamps digest, so it never holds
    `narrowed`; each pass gets its own copy, which it cannot leak into the next."""
    from tools.graph.core.model import ExtractResult
    seen: list[dict] = []

    class Recorder:
        name = "recorder"

        def link(self, ctx, nodes, edges):
            seen.append(dict(ctx.config))
            ctx.config["leaked"] = True
            return ExtractResult()

    passes = [B.LinkRegistration(name=f"r{i}", layer="link", link_pass=Recorder())
              for i in (1, 2)]
    config = {"canonical": []}
    B.run_link_passes(passes, ExtractResult(), [], Path("."), "rid", config, narrowed=True,
                      deployment_inputs={"a": ["values.yaml"]},
                      deployment_claims={"a": ["values.yaml"]},
                      deployment_off_case={"a": ["Docker-Compose.yml"]})
    assert config == {"canonical": []}
    assert [c["narrowed"] for c in seen] == [True, True]
    assert seen[1]["deployment_inputs"] == {"a": ["values.yaml"]}
    assert seen[1]["deployment_off_case"] == {"a": ["Docker-Compose.yml"]}
    assert "leaked" not in seen[1]


def test_the_deployment_scope_names_what_each_reader_was_handed_and_missed(tmp_path):
    from tools.graph.tests._estate import lay_down
    lay_down(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                              "Docker-Compose.yml": "services:\n  web:\n    image: x\n",
                              "app.py": "print(1)\n"}})
    components = [{"name": "a", "path": "a"}]
    regs = B.discover_extractors()
    act = B.activate(regs, components, tmp_path / "ws", workspace=components)
    assert B.deployment_scope(act, components, tmp_path / "ws", regs) == {
        "deployment_inputs": {"a": ["charts/web/values.yaml"]},
        "deployment_claims": {"a": ["charts/web/values.yaml"]},
        "deployment_off_case": {"a": ["Docker-Compose.yml"]}}
