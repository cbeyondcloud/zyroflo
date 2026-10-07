"""`canonical-unmatched`: a `[[canonical]]` block that designates nothing the graph read.

T573 (the owner's ruling 12, batch 15b). A block whose `path` designates nothing - a typo, or
a file no reader reads - was silent: the env view fell back to last-wins, and the link pass
consulted the block only for a contested service. Now one row per first block per service,
cited at the block's line, judged only where its owner was READ in a run that saw the whole
workspace (a deployment reader was handed one of its files, or missed one by case):

* (a) the graph read no claim of the owner on the service (and the path reaches no host map
  of the owner): it says what the graph READ - never that the owner deploys nothing - and
  ends with one of four endings: the other deployments it read; every deployment file
  unread, each with its own remedy; files read that declare no deployment; or no deployment
  file at all, only host maps;
* (b) the owner claims the service, but the path reaches none of those files.

Where the owner has a deployment file the graph could not read, the row names it first, in
the spelling its kind needs. Every (a) spelling ends naming what the graph does not read.
Each cell is followed: doing what the row says is accepted and silences it.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, compose, svc  # noqa: E402

JAVA = "public class C { }\n"
CHART = "apiVersion: v2\nname: web\nversion: 0.1.0\n"
CM = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: h\ndata:\n  WEB_HOST: web:80\n"
WORKLOAD = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
NOT_READ = ("(the graph does not read a compose file under another name or one pulled in by "
            "`include:` or `extends:`, nor a chart's values overlays)")


def stock(name: str) -> str:
    return f"services:\n  {name}:\n    image: org/{name}:1\n"


def rows(built, kind: str = "canonical-unmatched") -> list[dict]:
    return built.kinds(kind)


def one(built, kind: str = "canonical-unmatched") -> dict:
    (row,) = rows(built, kind)
    return row


def block(service: str, owner: str, path: str | None = None) -> dict:
    return {"service": service, "owner": owner, **({"path": path} if path else {})}


# ── (b): the owner claims the service, the path reaches none of its files ─────


@pytest.mark.parametrize("files, path, listed", [
    ({"docker-compose.yml": stock("web")}, "docker-compose.ymll", "docker-compose.yml"),
    ({"charts/web/values.yaml": "fullnameOverride: web\n"}, "charts/web/values.ymal",
     "charts/web/values.yaml"),
    ({"docker-compose.yml": stock("web")}, "docs/x.md", "docker-compose.yml"),
], ids=["compose-typo", "helm-typo", "a-file-no-reader-reads"])
def test_a_path_designating_none_of_the_owners_files_is_row_b(tmp_path, files, path, listed):
    """Red on `main`, where the block was silent. Followed - naming a listed file - it
    designates, and the row is gone."""
    built = build(tmp_path / "1", {"a": files, "__canonical__": [block("web", "a", path)]})
    row = one(built)
    assert (row["component"], row["service"], row["path"]) == ("a", "web", path)
    assert row["source_file"] == "workspace.toml" and row["line"] > 0
    assert row["why"].startswith(f"`{path}` designates no file of `a` deploying `web` - a "
                                 f"typo, or a file no reader reads. The files that do: "
                                 f"`{listed}`; name one of them")
    assert "or remove the `path` to let `owner` decide alone" in row["why"]
    assert rows(build(tmp_path / "2", {"a": files, "__canonical__": [
        block("web", "a", listed)]})) == []


def test_the_remove_the_path_clause_is_offered_only_where_the_owner_decides_alone(tmp_path):
    """D15b-59: with two packages of the service the owner branch decides nothing, so only
    "name one of them" is offered - and followed, it decides."""
    files = {"docker-compose.yml": stock("web"), "legacy/docker-compose.yml": stock("web")}
    row = one(build(tmp_path / "1", {"a": files, "__canonical__": [
        block("web", "a", "docker-compose.ymll")]}))
    assert "remove the `path`" not in row["why"]
    followed = build(tmp_path / "2", {"a": files, "__canonical__": [
        block("web", "a", "docker-compose.yml")]})
    assert rows(followed) == [] and not followed.kinds("contested-service")


# ── (a): none read ───────────────────────────────────────────────────────────


def test_a_block_whose_owner_deploys_something_else_lists_what_was_read(tmp_path):
    built = build(tmp_path / "1", {"a": {"docker-compose.yml": stock("api")},
                                   "__canonical__": [block("wbe", "a")]})
    why = one(built)["why"]
    assert why.startswith("The graph read no deployment of `wbe` in `a`, so this block "
                          "designates nothing it read. It read: `api`; name one of them, or "
                          "remove the block")
    assert why.endswith(NOT_READ)
    assert rows(build(tmp_path / "2", {"a": {"docker-compose.yml": stock("api")},
                                       "__canonical__": [block("api", "a")]})) == []


def test_a_service_spelled_in_another_case_is_listed_as_read(tmp_path):
    """Review focus 3: `norm_id` keeps case, so `Web` is not `web` - the row lists the
    spelling the graph read, and followed, the block designates."""
    built = build(tmp_path, {"a": {"docker-compose.yml": stock("web")},
                             "__canonical__": [block("Web", "a")]})
    assert "It read: `web`; name one of them" in one(built)["why"]


def test_a_templates_only_owner_is_shown_its_chart_roots(tmp_path):
    """The B8-1 shape (D15b-43): no deployment file was handed at all - only host maps.
    Followed to a silent build: a path under a listed root ranks those host maps."""
    estate = {"a": {"charts/umb/charts/web/templates/cm.yaml": CM},
              "__canonical__": [block("web", "a", "charts/umb/values.yaml")]}
    why = one(build(tmp_path / "1", estate))["why"]
    assert why.startswith("The graph read no deployment of `web` in `a`, so this block "
                          "designates nothing it read. The graph read no deployment file of "
                          "`a`; its host maps are designated by a path under: "
                          "`charts/umb/charts/web` - set `path` to one of them, or remove the "
                          "block")
    assert "a path under a root makes that root's host maps the ones the ranking keeps" in why
    followed = build(tmp_path / "2", {**estate, "__canonical__": [
        block("web", "a", "charts/umb/charts/web")]})
    assert rows(followed) == []


def test_a_path_ranking_only_the_owners_host_maps_is_used(tmp_path):
    estate = {"a": {"charts/web/templates/cm.yaml": CM},
              "__canonical__": [block("billing", "a", "charts/web")]}
    assert rows(build(tmp_path, estate)) == []


def test_an_umbrella_whose_own_files_are_not_there_is_named(tmp_path):
    """B8-1's old fixture, red on `main`, which ranked the subchart's host maps silently
    under a claim naming an umbrella file that is not in the tree."""
    estate = {"a": {"charts/app/charts/orders/templates/cm.yaml": CM},
              "__canonical__": [block("orders", "a", "charts/app/values.yaml")]}
    assert rows(build(tmp_path, estate))


def test_a_root_values_file_naming_nothing_ranks_nothing_and_is_named(tmp_path):
    estate = {"a": {"values.yaml": "replicaCount: 2\n",
                    "charts/orders/templates/cm.yaml": CM},
              "__canonical__": [block("orders", "a", "values.yaml")]}
    assert rows(build(tmp_path, estate))


# ── what the graph could not read, named first ───────────────────────────────


UNREAD = [
    ({"docker-compose.yml": "services:\n  web:\n    environment:\n      K: \"x\n"},
     "`docker-compose.yml` could not be read (see its row), so what it declares is unknown; "
     "fix it"),
    ({"charts/web/values.yaml": "fullnameOverride: [\n"},
     "`charts/web/values.yaml` could not be read (see its row), so what it declares is "
     "unknown; fix it"),
    ({"charts/web/values.yaml": "replicaCount: 2\n"},
     "`charts/web/values.yaml` names no service, so what it deploys is unknown; name one in "
     "it"),
    ({"charts/web/Chart.yaml": CHART, "charts/web/templates/d.yaml": WORKLOAD},
     "`charts/web` has no `values.yaml`, so nothing the graph reads names the service its "
     "templates deploy; add one (`fullnameOverride: <name>`)"),
    ({"Docker-Compose.yml": stock("web")},
     "`Docker-Compose.yml` is not read because of its case (see its row), so what it "
     "declares is unknown; rename it as the patterns spell it"),
]


@pytest.mark.parametrize("files, named", UNREAD, ids=["compose-unparsed", "unparsable",
                                                      "unnamed", "chart-only", "case"])
def test_every_deployment_file_unread_names_each_with_its_remedy(tmp_path, files, named):
    """D15b-102: never one fixed "fix `<file>` first" - false for a chart that was read but
    names nothing, and for a file lost to its case."""
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert why.startswith("The graph read no deployment of `web` in `a`, so this block "
                          "designates nothing it read. None of `a`'s deployment files could "
                          "be read: ")
    assert named in why and why.rstrip().endswith(NOT_READ)


def test_several_unread_files_are_named_most_specific_first(tmp_path):
    files = {"Docker-Compose.yml": stock("web"),
             "charts/web/values.yaml": "fullnameOverride: [\n"}
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert why.index("Docker-Compose.yml") < why.index("charts/web/values.yaml")


def test_an_unread_file_is_named_before_what_the_graph_did_read(tmp_path):
    files = {"docker-compose.yml": stock("api"),
             "charts/web/values.yaml": "fullnameOverride: [\n"}
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert why.startswith("`charts/web/values.yaml` could not be read (see its row), so what "
                          "it declares is unknown; fix it, then: The graph read no deployment")
    assert "It read: `api`" in why


def test_a_path_naming_the_unread_file_says_so_directly(tmp_path):
    why = one(build(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: [\n"},
                               "__canonical__": [block("web", "a",
                                                       "charts/web/values.yaml")]}))["why"]
    assert why.startswith("`charts/web/values.yaml` names `a`'s file that could not be read")


def test_fixing_the_file_silences_the_row(tmp_path):
    estate = {"a": {"charts/web/values.yaml": "fullnameOverride: [\n"},
              "__canonical__": [block("web", "a")]}
    assert rows(build(tmp_path / "1", estate))
    estate["a"]["charts/web/values.yaml"] = "fullnameOverride: web\n"
    assert rows(build(tmp_path / "2", estate)) == []


@pytest.mark.parametrize("files, listed", [
    ({"charts/web/Chart.yaml": CHART + "type: library\n",
      "charts/web/templates/_h.yaml": WORKLOAD}, "charts/web/Chart.yaml"),
    ({"charts/web/Chart.yaml": CHART}, "charts/web/Chart.yaml"),
    ({"charts/web/Chart.yaml": CHART, "charts/web/templates/NOTES.txt": "hi\n"},
     "charts/web/Chart.yaml"),
    ({"docker-compose.yml": "volumes:\n  data: {}\n"}, "docker-compose.yml"),
], ids=["library", "no-templates", "notes-only", "volumes-only"])
def test_files_read_that_declare_no_deployment_are_named(tmp_path, files, listed):
    """D15b-122: read, not lost - so never "read no deployment file", and the compose case
    gets a row at all (round 12: none)."""
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert (f"The graph read `{listed}` of `a`, and none declares a deployment (a library "
            f"chart, a chart with no templates of its own, or a compose file with no "
            f"`services:`); remove the block, or name a service `a` deploys in a file the "
            f"graph reads") in why


def test_an_unread_file_and_a_read_one_declaring_nothing_are_each_named_once(tmp_path):
    """D15b-136: the prefix names the unparsable file, the ending the other, alone."""
    files = {"docker-compose.yml": "services:\n  web:\n    environment:\n      K: \"x\n",
             "sub/docker-compose.yml": "volumes:\n  data: {}\n"}
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert why.startswith("`docker-compose.yml` could not be read")
    assert "The graph read `sub/docker-compose.yml` of `a`, and none declares" in why
    assert why.count("`docker-compose.yml`") == 1


def test_a_component_whose_only_deployment_file_is_lost_to_case_is_judged(tmp_path):
    """D15b-132: never handed it, the owner was read through the case record - red on
    v13's gate, which withheld the row exactly there."""
    why = one(build(tmp_path, {"a": {"Docker-Compose.yml": stock("web"), "app.py": "x = 1\n"},
                               "__canonical__": [block("web", "a")]}))["why"]
    assert "`Docker-Compose.yml` is not read because of its case" in why
    assert "rename it as the patterns spell it" in why


# ── when it is judged ────────────────────────────────────────────────────────


def test_a_narrowed_run_judges_no_block(tmp_path):
    estate = {"a": {"docker-compose.yml": stock("api")}, "b": {"app.py": "x = 1\n"},
              "__canonical__": [block("web", "a")]}
    assert rows(build(tmp_path, estate))
    assert rows(build(tmp_path, None, "--components", "a")) == []
    assert rows(build(tmp_path, None, "--only", "compose-services")) == []


def test_an_owner_no_deployment_reader_read_is_never_judged(tmp_path):
    estate = {"a": {"app.py": "x = 1\n"}, "__canonical__": [block("web", "a")]}
    assert rows(build(tmp_path, estate)) == []


def test_a_duplicate_block_is_judged_once(tmp_path):
    built = build(tmp_path, {"a": {"docker-compose.yml": stock("api")},
                             "__canonical__": [block("my web", "a"), block("my-web", "a")]})
    assert len(rows(built)) == 1 and rows(built, "canonical-duplicate")


def test_a_template_less_chart_with_an_off_case_claim_gets_both_rows(tmp_path):
    """D15b-11, D15b-32: `helm-configmaps` now reads the chart's values files, so its
    `off-case-claim` loop runs where there are no templates - beside the block's own row."""
    built = build(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: api\n"},
                             "__canonical__": [block("web", "a", "charts/web/Values.yaml")]})
    assert built.kinds("off-case-claim") and rows(built)


@pytest.mark.parametrize("spelling", ["charts\\web\\values.yaml", "./charts/web/values.yaml",
                                      "charts//web/values.yaml/"],
                         ids=["backslash", "dot-slash", "doubled-and-trailing"])
def test_t575_spellings_decide_as_the_plain_path(tmp_path, spelling):
    """Review focus 4: never row (b)."""
    built = build(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: web\n",
                                   "legacy/web/values.yaml": "fullnameOverride: web\n"},
                             "__canonical__": [block("web", "a", spelling)]})
    assert rows(built) == [] and not built.kinds("contested-service")


# ── through a load ───────────────────────────────────────────────────────────


def test_a_stale_shard_narrows_the_load_and_republished_it_is_judged(tmp_path):
    """Ruling 16: the owner's compose file gains the service after publish - the load is
    narrowed, so no false (a) row; re-published, the block is satisfied."""
    estate = {"a": {"docker-compose.yml": stock("api")}, "__canonical__": [block("web", "a")]}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(stock("web"), encoding="utf-8")
    assert rows(build(tmp_path, None, "--from-snapshot")) == []
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    assert rows(build(tmp_path, None, "--from-snapshot")) == []


def test_a_case_lost_file_is_named_through_a_load(tmp_path):
    estate = {"a": {"Docker-Compose.yml": stock("web")}, "__canonical__": [block("web", "a")]}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    assert "is not read because of its case" in one(build(tmp_path, None, "--from-snapshot"))["why"]


def test_shards_from_another_extractor_set_narrow_the_load(tmp_path):
    """Shards published before batch 15b carry another extractor set: the load counts them
    as narrowing, decides as on `main` and judges no block over them."""
    import json
    estate = {"a": {"docker-compose.yml": stock("api")},
              "__canonical__": [block("web", "a", "docker-compose.ymll")]}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    manifest = next((tmp_path / "ws" / "a" / ".sdlc" / "graph").glob("snapshot.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["extractor_version"] = "published-before-batch-15b"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert rows(build(tmp_path, None, "--from-snapshot")) == []
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    assert rows(build(tmp_path, None, "--from-snapshot"))


def test_a_charts_metadata_beside_its_unread_values_file_is_not_named_as_read(tmp_path):
    """D15b-136: a chart's `Chart.yaml` is metadata beside its values file, as the helm
    reader reads it - the values file's node speaks for it, so every deployment file of the
    owner is unread, not "read, declaring nothing"."""
    why = one(build(tmp_path, {"a": {"charts/web/values.yaml": "fullnameOverride: [\n",
                                     "charts/web/Chart.yaml": CHART},
                               "__canonical__": [block("web", "a")]}))["why"]
    assert "None of `a`'s deployment files could be read: `charts/web/values.yaml`" in why
    assert "The graph read `charts/web/Chart.yaml`" not in why


def test_a_charts_metadata_beside_its_values_file_lost_to_case_is_not_named_as_read(tmp_path):
    """The values file speaks for its `Chart.yaml` in any case, as the helm reader decides
    a chart holds one (ruling 20): lost to its case, it is still that chart's."""
    why = one(build(tmp_path, {"a": {"charts/web/Values.yaml": "fullnameOverride: web\n",
                                     "charts/web/Chart.yaml": CHART,
                                     "charts/web/templates/d.yaml": WORKLOAD},
                               "__canonical__": [block("web", "a")]}))["why"]
    assert "`charts/web/Values.yaml` is not read because of its case" in why
    assert "The graph read `charts/web/Chart.yaml`" not in why
    assert "has no `values.yaml`" not in why, "the chart holds one, lost to its case"


@pytest.mark.parametrize("second", ["docker-compose.prod.yml", "compose.yaml"],
                         ids=["prod-is-not-a-layer", "another-family"])
def test_a_compose_package_beside_an_unread_one_is_named_as_read(tmp_path, second):
    """Review B15b-1: one directory holds two compose packages (T588). A rule that gave
    every file sharing a directory with a deployment node to that node's package hid the
    second, read and declaring nothing - and both rows said "None of `a`'s deployment
    files could be read" of a component the graph read a file of."""
    files = {"docker-compose.yml": "services: [\n", second: "volumes:\n  v: {}\n",
             "app.py": "x = 1\n"}
    built = build(tmp_path, {"a": files, "__canonical__": [block("web", "a")],
                             "__apart__": [{"service": "web", "component": "a"}]})
    for kind in ("canonical-unmatched", "apart-unmatched"):
        why = one(built, kind)["why"]
        assert why.startswith("`docker-compose.yml` could not be read"), kind
        assert f"The graph read `{second}` of `a`, and none declares a deployment" in why, kind
        assert "None of `a`'s deployment files could be read" not in why, kind


def test_a_compose_package_beside_a_charts_unread_values_file_is_named_as_read(tmp_path):
    """Review B15b-12: the third cell of B15b-1's class - only a chart's `Chart.yaml` is
    spoken for by its values file, never another package sharing the chart's directory."""
    files = {"charts/web/Chart.yaml": CHART, "charts/web/values.yaml": "fullnameOverride: [\n",
             "charts/web/docker-compose.yml": "volumes:\n  v: {}\n",
             "charts/web/templates/d.yaml": WORKLOAD}
    built = build(tmp_path, {"a": files, "__canonical__": [block("web", "a")],
                             "__apart__": [{"service": "web", "component": "a"}]})
    for kind in ("canonical-unmatched", "apart-unmatched"):
        why = one(built, kind)["why"]
        assert why.startswith("`charts/web/values.yaml` could not be read"), kind
        assert ("The graph read `charts/web/docker-compose.yml` of `a`, and none declares a "
                "deployment") in why, kind
        assert "`charts/web/Chart.yaml`" not in why, kind


@pytest.mark.parametrize("values", ["global:\n  x: 1\n", "fullnameOverride: web\n"],
                         ids=["naming-nothing", "naming-a-service"])
def test_a_library_charts_values_file_is_read_declaring_nothing(tmp_path, values):
    """Review B15b-13: helm never installs a library chart, so its values file is no
    deployment package. It was an `unnamed-deployment` node, whose row's remedy - name a
    service in it - published a canonical claim from a chart that deploys nothing; and a
    library chart's values naming a service was a claim outright. Now both files are read,
    declaring nothing (the fourth ending), and no claim comes of them."""
    files = {"charts/lib/Chart.yaml": CHART + "type: library\n",
             "charts/lib/values.yaml": values, "charts/lib/templates/_h.tpl": "{{- x -}}\n"}
    built = build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]})
    why = one(built)["why"]
    assert ("The graph read `charts/lib/Chart.yaml`, `charts/lib/values.yaml` of `a`, and "
            "none declares a deployment (a library chart") in why
    assert "name one in it" not in why
    assert not built.kinds("unnamed-deployment")
    assert not [e for e in built.edges if e.get("type") == "DG_DEPLOYS"]


def test_a_root_chart_among_other_roots_is_named_by_its_own_file(tmp_path):
    """Review B15b-14: `.` designates every package of the owner, so offered beside another
    chart's root it ranked nothing. Where the host maps ship in more than one root, the root
    chart is named by its own host-map file, which designates that chart alone; followed,
    the other root's value is the one superseded."""
    cm = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: h\ndata:\n  WEB_HOST: {}\n"
    estate = {"a": {"templates/cm.yaml": cm.format("web-root:80"),
                    "web/x/templates/cm.yaml": cm.format("web-x:9090"), "app.py": "x = 1\n"},
              "__canonical__": [block("web", "a")]}
    why = one(build(tmp_path / "1", estate))["why"]
    assert "designated by a path under: `templates/cm.yaml`, `web/x`" in why
    assert "`.`" not in why
    followed = build(tmp_path / "2", {**estate, "__canonical__": [
        block("web", "a", "templates/cm.yaml")]})
    assert rows(followed) == []
    (superseded,) = followed.kinds("superseded-declaration")
    assert superseded["source_file"] == "web/x/templates/cm.yaml"
    everything = build(tmp_path / "3", {**estate, "__canonical__": [block("web", "a", ".")]})
    assert not everything.kinds("superseded-declaration"), "why `.` is not offered: it ranks none"


def test_a_root_charts_metadata_beside_an_unread_compose_file_is_named_as_read(tmp_path):
    """B15b-1's other half: only a chart's own values file speaks for its `Chart.yaml` - a
    compose file sharing the component's root speaks for no chart."""
    files = {"docker-compose.yml": "services: [\n", "Chart.yaml": CHART + "type: library\n",
             "app.py": "x = 1\n"}
    why = one(build(tmp_path, {"a": files, "__canonical__": [block("web", "a")]}))["why"]
    assert why.startswith("`docker-compose.yml` could not be read")
    assert "The graph read `Chart.yaml` of `a`, and none declares a deployment" in why


def test_a_chart_only_chart_at_the_root_is_spelled_by_its_root(tmp_path):
    """Review B15b-3: the chart directory of a root `Chart.yaml` is the component's root,
    spelled `.` as a block's `path` spells it - never an empty name."""
    built = build(tmp_path, {"a": {"Chart.yaml": CHART, "templates/d.yaml": WORKLOAD},
                             "__canonical__": [block("web", "a")],
                             "__apart__": [{"service": "web", "component": "a"}]})
    for kind in ("canonical-unmatched", "apart-unmatched"):
        why = one(built, kind)["why"]
        assert "`.` has no `values.yaml`, so nothing the graph reads names the service" in why
        assert "``" not in why, kind


def test_host_maps_at_the_root_are_designated_by_dot_and_followed_it_is_silent(tmp_path):
    """B15b-3's class: a template at the component's root ships in the root chart, so the
    host-map clause names `.` - and setting `path` to it designates the host map."""
    estate = {"a": {"templates/cm.yaml": CM, "app.py": "x = 1\n"},
              "__canonical__": [block("web", "a")]}
    why = one(build(tmp_path / "1", estate))["why"]
    assert "designated by a path under: `.`" in why and "``" not in why
    assert rows(build(tmp_path / "2", {**estate, "__canonical__": [
        block("web", "a", ".")]})) == []


def test_the_belt_a_node_without_its_readers_facts_is_never_judged():
    """In-process: a helm deployment node carrying no `ships_in` - a shard from before
    batch 15b, read by a caller that judges anyway - leaves the block unjudged (D15b-54)."""
    from types import SimpleNamespace
    from tools.graph.core.model import Edge, Node
    from tools.graph.links.canonical import CanonicalPass
    nodes = [Node(id="deploy:a/charts/web/values.yaml", type="deploy",
                  props={"component": "a", "path": "charts/web/values.yaml", "kind": "helm"}),
             Node(id="svc:api@api", type="svc", props={"component": ""})]
    edges = [Edge(src="deploy:a/charts/web/values.yaml", type="DG_DEPLOYS", dst="svc:api@api",
                  source_file="charts/web/values.yaml", line=1, extractor="helm-deployments",
                  confidence=1.0, props={"service": "api"})]
    config = {"canonical": [{**block("web", "a"), "file": "w.toml", "line": 3}],
              "components": [{"name": "a", "path": "a"}], "narrowed": False,
              "deployment_inputs": {"a": ["charts/web/values.yaml"]},
              "deployment_claims": {"a": ["charts/web/values.yaml"]},
              "deployment_off_case": {}}
    res = CanonicalPass().link(SimpleNamespace(config=config), nodes, edges)
    assert [r for r in res.unresolved if r["kind"] == "canonical-unmatched"] == []
    nodes[0] = Node(id=nodes[0].id, type="deploy",
                    props={**nodes[0].props, "ships_in": ["charts/web"]})
    res = CanonicalPass().link(SimpleNamespace(config=config), nodes, edges)
    assert [r["kind"] for r in res.unresolved] == ["canonical-unmatched"], \
        "the control: with its reader's facts the same block is judged"
    # Review B15b-5: a host-map file carrying no `ships_in` leaves the block unjudged too.
    host_map = Node(id="cfg:a/charts/web/templates/cm.yaml", type="cfg",
                    props={"component": "a", "path": "charts/web/templates/cm.yaml",
                           "kind": "configmap"})
    res = CanonicalPass().link(SimpleNamespace(config=config), [*nodes, host_map], edges)
    assert [r for r in res.unresolved if r["kind"] == "canonical-unmatched"] == []
    host_map = Node(id=host_map.id, type="cfg",
                    props={**host_map.props, "ships_in": ["charts/web"]})
    res = CanonicalPass().link(SimpleNamespace(config=config), [*nodes, host_map], edges)
    assert [r["kind"] for r in res.unresolved] == ["canonical-unmatched"], \
        "the control: with its `ships_in` the host-map file lets the block be judged"
