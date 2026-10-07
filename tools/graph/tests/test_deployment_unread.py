"""Every deployment file the graph could not read is named (batch 15b, T573 decision 6).

A DEPLOYMENT FILE is one a reader reads for a service claim - a file one of a deployment
reader's `CLAIM_GLOBS` matches: a compose file, a chart's values or `Chart` file. A file
the readers lost carries no claim, so the graph would read as complete without it; instead
it is a node marked `unread` with the kind of the row that explains it, and the accounting
rows name it.

This file pins the third way a file is lost - by its CASE. Matching is case-sensitive on
every platform (T531), and T531's `off-case-input` row names only files NO extractor
claims, so a mis-cased `Docker-Compose.yml` or `Values.yaml` - which `openapi-specs` claims
by `*.yml`/`*.yaml` - was silent (round 9). The activation walk now records, per
deployment reader, every file its OWN globs match only once case is ignored; each gets
exactly one row (T531's where it already names the file, else `deployment-off-case`), and
a case-lost claim file gets its `unread` node. The record has its own stamp entry, so
`--check-fresh` names a deployment reader's case-lost file in a clause of its own.

Everything here is synthetic and names no real workspace.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import ROOT, build, compose, svc  # noqa: E402

DEPLOY_CLAUSE = "a file a deployment reader misses only by case changed"
T531_CLAUSE = "files no extractor reads because of their case changed"
CHART = "apiVersion: v2\nname: web\nversion: 0.1.0\n"
CM = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: web\ndata:\n  URL: http://x\n"


def unread_nodes(built) -> dict[str, str]:
    return {n["id"]: n["props"]["unread"] for n in built.nodes
            if (n.get("props") or {}).get("unread")}


def rows_naming(built, rel: str) -> list[dict]:
    """Every row that names `rel` (component-relative), in `files` or as its source."""
    return [r for r in built.rows
            if rel in (r.get("files") or []) or r.get("source_file") == rel]


def check_fresh(where: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "fresh"),
         "--check-fresh"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def stale_reason(proc: subprocess.CompletedProcess, component: str) -> str:
    prefix = f"freshness: STALE {component} - "
    found = [ln[len(prefix):] for ln in proc.stdout.splitlines() if ln.startswith(prefix)]
    assert len(found) == 1, proc.stdout + proc.stderr
    return found[0]


# ── one row per file lost to its case, and a node for a lost claim file ──────


def test_a_compose_file_lost_to_case_is_an_unread_node_and_one_row(tmp_path):
    """Red on `main`: `openapi-specs` claims `Docker-Compose.yml` by `*.yml`, so T531's
    row - files NO extractor claims - never named it, and the deployment was silent."""
    built = build(tmp_path, {"deploy": {
        "Docker-Compose.yml": compose(svc("web", None, None, image="org/web:1"))}})
    assert built.code == 0
    assert unread_nodes(built) == {"deploy:deploy/Docker-Compose.yml": "deployment-off-case"}
    (row,) = rows_naming(built, "Docker-Compose.yml")
    assert row["kind"] == "deployment-off-case"
    assert (row["component"], row["files"], row["globs"], row["extractors"]) == (
        "deploy", ["Docker-Compose.yml"], ["docker-compose*.yml"], ["compose-services"])
    assert "`compose-services` reads claims by it, so what it declares is missing" in row["why"]
    assert row["why"].endswith("rename it as the patterns spell it")


def test_a_values_file_lost_to_case_is_an_unread_node_and_one_row(tmp_path):
    built = build(tmp_path, {"deploy": {"charts/web/Values.yaml": "fullnameOverride: web\n",
                                        "charts/web/Chart.yaml": CHART}})
    assert built.code == 0
    assert unread_nodes(built) == {
        "deploy:deploy/charts/web/Values.yaml": "deployment-off-case"}
    (row,) = rows_naming(built, "charts/web/Values.yaml")
    assert (row["kind"], row["extractors"]) == ("deployment-off-case",
                                                ["helm-configmaps", "helm-deployments"])
    assert "`helm-deployments` reads claims by it, so what it declares is missing" in row["why"]
    # D15b-135: the host-map reader reads values files to know which directories are
    # charts, so that purpose is named too - never an empty purpose clause.
    assert "`helm-configmaps` reads it to know which directories are charts, so the charts " \
           "a claim on the root ranks are missing" in row["why"]


def test_a_template_lost_to_case_gets_its_row_and_no_node(tmp_path):
    """A template loses addresses, not a deployment (D15b-103): its row, never a node."""
    built = build(tmp_path, {"deploy": {"charts/web/values.yaml": "fullnameOverride: web\n",
                                        "charts/web/TEMPLATES/cm.yaml": CM}})
    assert built.code == 0
    assert unread_nodes(built) == {}
    (row,) = rows_naming(built, "charts/web/TEMPLATES/cm.yaml")
    assert row["kind"] == "deployment-off-case"
    assert "`helm-configmaps` reads host maps by it, so every address it publishes is " \
           "missing" in row["why"]
    assert "claims" not in row["why"], "a template is read for no claim"


def test_a_file_in_two_roles_says_both(tmp_path):
    """D15b-124: `TEMPLATES/Values.yaml` is missed as a claim AND as a host map; the row
    names each purpose with only the readers serving it."""
    built = build(tmp_path, {"deploy": {"charts/web/values.yaml": "fullnameOverride: web\n",
                                        "charts/web/TEMPLATES/Values.yaml": CM}})
    assert unread_nodes(built) == {
        "deploy:deploy/charts/web/TEMPLATES/Values.yaml": "deployment-off-case"}
    (row,) = rows_naming(built, "charts/web/TEMPLATES/Values.yaml")
    assert "`helm-deployments` reads claims by it" in row["why"]
    assert "`helm-configmaps` reads host maps by it" in row["why"]
    assert "`helm-deployments` counts it as a template of its chart" in row["why"]
    assert "`helm-configmaps` reads it to know which directories are charts" in row["why"]


def test_a_claim_file_no_extractor_reads_keeps_t531s_row_alone(tmp_path):
    """`compose.YAML` matches no pattern as spelled, so T531's `off-case-input` already
    names it: exactly one row, and its node says which row explains it."""
    built = build(tmp_path, {"deploy": {
        "compose.YAML": compose(svc("web", None, None, image="org/web:1"))}})
    (row,) = rows_naming(built, "compose.YAML")
    assert row["kind"] == "off-case-input"
    assert unread_nodes(built) == {"deploy:deploy/compose.YAML": "off-case-input"}


def test_a_file_no_deployment_reader_misses_gets_t531s_row_and_no_node(tmp_path):
    """The control: `Pom.XML` is maven's loss, not a deployment reader's."""
    built = build(tmp_path, {"lib": {"Pom.XML": "<project/>\n"}})
    (row,) = rows_naming(built, "Pom.XML")
    assert row["kind"] == "off-case-input"
    assert unread_nodes(built) == {}
    assert not built.kinds("deployment-off-case")


def test_a_case_lost_file_rides_in_its_components_shard(tmp_path):
    """A fact about the component's file names, so it is published with the component's
    facts: `rebuild == snapshot` holds, and a load carries the node and the row."""
    estate = {"deploy": {
        "Docker-Compose.yml": compose(svc("web", None, None, image="org/web:1"))}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    assert build(tmp_path, None, "--check-snapshot").code == 0, "the shard drifted"
    loaded = build(tmp_path, None, "--from-snapshot")
    assert unread_nodes(loaded) == {"deploy:deploy/Docker-Compose.yml": "deployment-off-case"}
    assert [r["kind"] for r in rows_naming(loaded, "Docker-Compose.yml")] == [
        "deployment-off-case"]


# ── the record is stamped in its own entry ──────────────────────────────────


def test_each_case_record_stales_with_its_own_clause(tmp_path):
    """D15b-123: one shared entry printed both clauses for either record. Each clause is
    pinned against a change of the OTHER record alone - the mutation that defends the
    split."""
    estate = {"deploy": {"charts/web/values.yaml": "fullnameOverride: web\n"},
              "db": {"db/migration/V1__init.sql": "create table t (id int);\n"}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    assert check_fresh(tmp_path).returncode == 0
    # A mis-cased values file `openapi-specs` reads: the deployment record alone moves.
    (tmp_path / "ws" / "deploy" / "charts" / "api").mkdir(parents=True)
    (tmp_path / "ws" / "deploy" / "charts" / "api" / "Values.yaml").write_text(
        "fullnameOverride: api\n", encoding="utf-8")
    why = stale_reason(check_fresh(tmp_path), "deploy")
    assert DEPLOY_CLAUSE in why and T531_CLAUSE not in why, why
    # A mis-cased migration no deployment reader misses: T531's record alone moves.
    (tmp_path / "ws" / "db" / "db" / "migration" / "V2__more.SQL").write_text(
        "create table u (id int);\n", encoding="utf-8")
    why = stale_reason(check_fresh(tmp_path), "db")
    assert T531_CLAUSE in why and DEPLOY_CLAUSE not in why, why


def test_the_rename_is_read_once_republished(tmp_path):
    """Followed: renamed as the patterns spell it, the shard is stale until re-published,
    and the republished graph reads the claim, with no node and no row left."""
    estate = {"deploy": {"charts/web/Values.yaml": "fullnameOverride: web\n",
                         "charts/web/Chart.yaml": CHART}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    lost = tmp_path / "ws" / "deploy" / "charts" / "web" / "Values.yaml"
    lost.rename(lost.with_name("values.yaml"))
    assert DEPLOY_CLAUSE in stale_reason(check_fresh(tmp_path), "deploy")
    built = build(tmp_path, None, "--publish-snapshot")
    assert unread_nodes(built) == {}
    assert not rows_naming(built, "charts/web/Values.yaml")
    assert built.deploy("deploy/charts/web/values.yaml")["dst"] == "svc:web@web"
    assert check_fresh(tmp_path).returncode == 0


# ── handed and not readable: compose (task 7) ────────────────────────────────

BROKEN_COMPOSE = "services:\n  web:\n    environment:\n      KEY: \"unterminated\n"


def compose_node(built, rel: str) -> dict:
    (node,) = [n for n in built.nodes if n["id"] == f"deploy:deploy/{rel}"]
    return node


def test_an_unparsable_compose_file_is_an_unread_node(tmp_path):
    """A deployment file the reader was handed and could not parse carries no claim; the
    graph says it holds an unread deployment instead of reading as complete."""
    built = build(tmp_path, {"deploy": {"docker-compose.yml": BROKEN_COMPOSE}})
    node = compose_node(built, "docker-compose.yml")
    assert node["props"]["unread"] == "compose-unparsed"
    assert node["props"]["kind"] == "compose" and "package" not in node["props"]
    assert not [e for e in built.deploys if e["src"] == node["id"]], "no claim"


def test_a_services_list_is_an_unread_node(tmp_path):
    built = build(tmp_path, {"deploy": {"docker-compose.yml": "services:\n  - web\n"}})
    assert compose_node(built, "docker-compose.yml")["props"]["unread"] == "compose-unparsed"


def test_an_unreadable_compose_file_is_an_unread_node(tmp_path):
    """A directory at the file's path makes open() fail on every platform (the technique
    `test_extractor_build_deploy.py` uses), with no chmod."""
    from tools.graph.core.spi import ExtractContext, RepoRef
    from tools.graph.extractors import compose as compose_reader
    root = tmp_path / "deploy"
    (root / "docker-compose.yml").mkdir(parents=True)
    res = compose_reader.ComposeExtractor().extract(ExtractContext(
        repos=[RepoRef(alias="deploy", path=root)], run_id="r",
        files={"deploy": [root / "docker-compose.yml"]}))
    (node,) = [n for n in res.nodes if n.id == "deploy:deploy/docker-compose.yml"]
    assert (node.props["unread"], node.props["kind"]) == ("unreadable-file", "compose")


def test_a_compose_file_declaring_no_services_gets_no_node(tmp_path):
    """It declares nothing - it is not unread (decision 6), as on `main`."""
    built = build(tmp_path, {"deploy": {"docker-compose.yml": "volumes:\n  data: {}\n"}})
    assert not [n for n in built.nodes if n["id"].startswith("deploy:deploy/")]


# ── ruling 20, end to end (task 8) ───────────────────────────────────────────

WORKLOAD = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"


def readers_changed(why: str) -> list[str]:
    """The readers a `--check-fresh` reason names in its "files read by ... changed"."""
    head = "files read by "
    assert why.startswith(head), why
    return why[len(head):].split(" changed", 1)[0].split(", ")


def check_snapshot(where: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "snap"),
         "--check-snapshot"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_a_chart_whose_only_template_is_lost_to_case_is_still_reported(tmp_path):
    """D15b-120, D15b-131: the chart's node and its row, and the case row naming both
    purposes the missed template serves; renamed, it is an ordinary `Chart.yaml`-only
    chart. Its negative control is the mutation that compares the `templates/` segment
    case-sensitively for case-record entries."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": CHART,
                                   "charts/web/TEMPLATES/cm.yaml": CM}})
    assert unread_nodes(built) == {"deploy:a/charts/web/Chart.yaml": "unnamed-deployment"}
    (case_row,) = rows_naming(built, "charts/web/TEMPLATES/cm.yaml")
    assert case_row["kind"] == "deployment-off-case"
    assert "`helm-configmaps` reads host maps by it" in case_row["why"]
    assert "`helm-deployments` counts it as a template of its chart" in case_row["why"]
    lost = tmp_path / "ws" / "a" / "charts" / "web" / "TEMPLATES"
    lost.rename(lost.with_name("templates"))
    built = build(tmp_path, None)
    assert unread_nodes(built) == {"deploy:a/charts/web/Chart.yaml": "unnamed-deployment"}
    assert not built.kinds("deployment-off-case")


def test_whether_a_chart_has_templates_of_its_own_is_stamped(tmp_path):
    """D15b-99: the templates are `helm-deployments`' input now, so adding or deleting one
    stales the shard for `--check-fresh`, `--check-snapshot` and `--incremental` alike -
    round 10 measured an unstamped listing reading current to one and drifted to another.
    A `NOTES.txt` declares no resource and changes nothing on either side."""
    estate = {"a": {"charts/web/Chart.yaml": CHART,
                    "charts/web/templates/deploy.yaml": WORKLOAD}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    notes = tmp_path / "ws" / "a" / "charts" / "web" / "templates" / "NOTES.txt"
    notes.write_text("hello\n", encoding="utf-8")
    assert check_fresh(tmp_path).returncode == 0 and check_snapshot(tmp_path).returncode == 0
    notes.unlink()
    (tmp_path / "ws" / "a" / "charts" / "web" / "templates" / "deploy.yaml").unlink()
    assert "helm-deployments" in readers_changed(stale_reason(check_fresh(tmp_path), "a"))
    snap = check_snapshot(tmp_path)
    assert "snapshot: DRIFT in a" in snap.stdout, snap.stdout + snap.stderr
    built = build(tmp_path, None, "--incremental")
    assert unread_nodes(built) == {}, "the chart deploys nothing now, as on main"


def test_an_empty_templates_directory_gaining_a_template_stales_the_shard(tmp_path):
    estate = {"a": {"charts/web/Chart.yaml": CHART}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    templates = tmp_path / "ws" / "a" / "charts" / "web" / "templates"
    templates.mkdir()
    (templates / "deploy.yaml").write_text(WORKLOAD, encoding="utf-8")
    assert "helm-deployments" in readers_changed(stale_reason(check_fresh(tmp_path), "a"))
    assert "snapshot: DRIFT in a" in check_snapshot(tmp_path).stdout


def test_unparsable_templates_get_no_node(tmp_path):
    """D15b-103: a template loses addresses, not a deployment - 139 of one workspace's 143
    unparsable rows were templates - so its rows stay as on `main` and no node is made."""
    broken = "apiVersion: v1\nkind: ConfigMap\ndata: [\n"
    built = build(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                                   **{f"charts/web/templates/cm{i}.yaml": broken
                                      for i in range(3)}}})
    assert unread_nodes(built) == {}
    assert len([r for r in built.kinds("unparsable")
                if r["source_file"].startswith("charts/web/templates/")]) == 3


# ── the four cells only a case-sensitive filesystem can build ────────────────


def _case_sensitive(where: Path) -> bool:
    """Measured, not assumed: whether this filesystem keeps two spellings of one name."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "probe.txt").write_text("x", encoding="utf-8")
    return not (where / "PROBE.txt").exists()


def test_a_mis_cased_values_file_is_found_by_listing_not_by_a_probe(tmp_path):
    """D15b-107: `Path("values.yaml").exists()` answers True beside a `Values.yaml` on a
    case-insensitive filesystem and False on a case-sensitive one, so a probe would fix
    this on a developer's machine and not on the CI gate. The `exists()` control asserts
    what a probe would answer on THIS platform; the verdict must not depend on it."""
    built = build(tmp_path, {"a": {"charts/web/Chart.yaml": CHART,
                                   "charts/web/Values.yaml": "fullnameOverride: web\n",
                                   "charts/web/templates/cm.yaml": CM}})
    chart = tmp_path / "ws" / "a" / "charts" / "web"
    if _case_sensitive(tmp_path / "probe"):
        assert not (chart / "values.yaml").exists(), "a probe would miss the file here"
    else:
        assert (chart / "values.yaml").exists(), "a probe would find it here, by folding"
    assert unread_nodes(built) == {"deploy:a/charts/web/Values.yaml": "deployment-off-case"}
    assert not built.kinds("unnamed-deployment")


def test_two_spellings_of_a_values_file_in_one_chart(tmp_path):
    """Linux CI only: a chart holding both `values.yaml` and `Values.yaml` - the first read,
    the second its own `unread` node; a FILE named `values.yaml` beside the other spelling
    carries the chart. Where the filesystem folds case, the second write lands in the first."""
    chart = tmp_path / "ws" / "a" / "charts" / "web"
    chart.mkdir(parents=True)
    (chart / "values.yaml").write_text("fullnameOverride: web\n", encoding="utf-8")
    (chart / "Values.yaml").write_text("fullnameOverride: web2\n", encoding="utf-8")
    if not _case_sensitive(tmp_path / "probe"):
        assert sorted(p.name for p in chart.iterdir()) == ["values.yaml"], \
            "a case-folding filesystem keeps one name"
        return
    built = build(tmp_path, {"a": {}})
    assert built.deploy("a/charts/web/values.yaml")["dst"] == "svc:web@web"
    assert unread_nodes(built) == {"deploy:a/charts/web/Values.yaml": "deployment-off-case"}
    assert not built.kinds("unnamed-deployment")


def test_two_mis_cased_spellings_are_one_case_group(tmp_path):
    """Linux CI only: `Values.yaml` and `VALUES.yaml` miss the same pattern by case - one
    `deployment-off-case` row naming both."""
    chart = tmp_path / "ws" / "a" / "charts" / "web"
    chart.mkdir(parents=True)
    (chart / "Values.yaml").write_text("fullnameOverride: web\n", encoding="utf-8")
    (chart / "VALUES.yaml").write_text("fullnameOverride: web\n", encoding="utf-8")
    if not _case_sensitive(tmp_path / "probe"):
        assert len(list(chart.iterdir())) == 1, "a case-folding filesystem keeps one name"
        return
    built = build(tmp_path, {"a": {}})
    (row,) = built.kinds("deployment-off-case")
    assert row["files"] == ["charts/web/VALUES.yaml", "charts/web/Values.yaml"]
