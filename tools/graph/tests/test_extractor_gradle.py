"""The gradle extractor: the same module graph maven answers, from a second build system.

Item 9b. The join is coordinates, not file format — a gradle script and a pom naming the
same group:artifact must meet at ONE `mod:` node, or "who rebuilds?" splits by build
tool. The fixture's answer key claimed every edge by line before this extractor existed
(`79d6f74`), per the fixture's own pre-registration rule, including the line that must
produce NOTHING: a dependency on a module the same build declares.

Groovy is code. The extractor reads declarations and refuses to evaluate — what an
expression hides is rowed, what a variable version obscures is marked, and identity is
positional (settings/directory) because nothing inside a subproject's file names it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import gradle, maven  # noqa: E402
from tools.graph.tests.chain_further import MOD_SHARED  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def ctx_for(tmp_path: Path, by_alias: dict[str, dict[str, str]]) -> ExtractContext:
    repos, files = [], {}
    for alias, tree in by_alias.items():
        root = tmp_path / alias
        paths = []
        for rel, body in tree.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            paths.append(p)
        repos.append(RepoRef(alias=alias, path=root))
        files[alias] = paths
    return ExtractContext(repos=repos, run_id="r", files=files)


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    return gradle.GradleModulesExtractor().extract(ctx_for(tmp_path, {alias: files}))


def deps(res):
    return [e for e in res.edges if e.type == "DG_DEPENDS_ON"]


def built(res):
    return [e for e in res.edges if e.type == "DG_BUILT_FROM"]


def fixture_files() -> dict[str, str]:
    comp = FIXTURE / "orders-notifier"
    return {p.relative_to(comp).as_posix(): p.read_text(encoding="utf-8")
            for p in comp.rglob("*") if p.is_file()}


# ── the fixture chain, at the answer key's own lines ──────────────────────────


def test_the_fixture_chain_resolves_at_the_cited_lines(tmp_path):
    res = run(tmp_path, fixture_files(), alias="orders-notifier")
    svc = ids.component_service_id("orders-notifier")
    by_dst = {e.dst: e for e in deps(res)}

    shared = by_dst[MOD_SHARED]
    assert shared.src == svc
    assert shared.source_file == "build.gradle"
    assert shared.line == 5, "EXPECTED.md cites build.gradle:5"
    assert shared.props["version"] == "2.1.0"
    assert shared.props["version_source"] == "declared"

    kafka = by_dst[ids.module_id("org.springframework.kafka", "spring-kafka")]
    assert kafka.line == 7
    assert kafka.props["version"] == ""
    assert kafka.props["version_source"] == "inherited"

    clients = by_dst[ids.module_id("org.apache.kafka", "kafka-clients")]
    assert clients.source_file == "notifier-core/build.gradle"
    assert clients.line == 2
    assert clients.props["version_source"] == "declared"


def test_the_builds_own_submodule_is_never_a_dependency(tmp_path):
    """build.gradle:6 names a module settings.gradle:2 declares — the maven trap ("eight
    components consuming their own submodules") in gradle spelling. It must vanish into
    the subtraction, leaving exactly the three real dependencies."""
    res = run(tmp_path, fixture_files(), alias="orders-notifier")
    assert ids.module_id("com.example.orderflow", "notifier-core") not in \
        {e.dst for e in deps(res)}
    assert len(deps(res)) == 3
    assert res.unresolved == []


def test_both_manifests_build_modules_of_one_service(tmp_path):
    res = run(tmp_path, fixture_files(), alias="orders-notifier")
    by_dst = {e.dst: e for e in built(res)}
    root = by_dst[ids.module_id("com.example.orderflow", "orders-notifier")]
    assert root.line == 1, "the group declaration is the manifest's coordinate line"
    core = by_dst[ids.module_id("com.example.orderflow", "notifier-core")]
    assert core.source_file == "notifier-core/build.gradle"
    assert core.line == 0, "positional identity: nothing in the file declares it"
    assert {e.src for e in built(res)} == {ids.component_service_id("orders-notifier")}


def test_the_join_is_the_same_node_a_pom_produces(tmp_path):
    """THE deliverable. The pom's dependency and the gradle script's dependency meet at
    one module node; split it and the shared-library traversal stops at a format
    boundary nobody declared."""
    pom = (FIXTURE / "orders-api" / "pom.xml").read_text(encoding="utf-8")
    mvn = maven.ModulesExtractor().extract(
        ctx_for(tmp_path / "m", {"orders-api": {"pom.xml": pom}}))
    grd = run(tmp_path / "g", fixture_files(), alias="orders-notifier")
    assert {e.dst for e in mvn.edges if e.type == "DG_DEPENDS_ON"} & \
        {e.dst for e in deps(grd)} == {MOD_SHARED}


# ── declaration forms: read at face value, never evaluated ────────────────────


def test_a_variable_version_keeps_the_join_and_says_so(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         'dependencies {\n'
                         '    implementation "org.jetbrains.kotlin:kotlin-stdlib:$kotlin_version"\n'
                         '}\n'})
    (e,) = deps(res)
    assert e.dst == ids.module_id("org.jetbrains.kotlin", "kotlin-stdlib")
    assert e.props["version"] == "$kotlin_version", "the spelling is preserved, not guessed"
    assert e.props["version_source"] == "variable"


def test_map_notation_is_a_coordinate_too(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         "dependencies {\n"
                         "    implementation group: 'org.slf4j', name: 'slf4j-api', version: '2.0.9'\n"
                         "    runtimeOnly group: 'org.slf4j', name: 'slf4j-simple'\n"
                         "}\n"})
    by_dst = {e.dst: e for e in deps(res)}
    api = by_dst[ids.module_id("org.slf4j", "slf4j-api")]
    assert (api.props["version"], api.props["version_source"]) == ("2.0.9", "declared")
    simple = by_dst[ids.module_id("org.slf4j", "slf4j-simple")]
    assert simple.props["version_source"] == "inherited"
    assert simple.props["scope"] == "runtimeOnly"


def test_a_parenthesised_dependency_with_a_closure_reads_and_its_exclude_does_not(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         "dependencies {\n"
                         "    androidTestCompile('org.example.test:probe-core:2.2.2', {\n"
                         "        exclude group: 'org.example', module: 'annotations'\n"
                         "    })\n"
                         "}\n"})
    (e,) = deps(res)
    assert e.dst == ids.module_id("org.example.test", "probe-core")
    assert res.unresolved == [], "an exclude line is not a declaration and not a loss"


def test_a_platform_import_is_a_dependency(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         'dependencies {\n'
                         '    testImplementation(platform("org.junit:junit-bom:5.9.1"))\n'
                         '}\n'})
    (e,) = deps(res)
    assert e.dst == ids.module_id("org.junit", "junit-bom")
    assert e.props["version"] == "5.9.1"


def test_classpath_configures_the_build_tool_not_the_artifact(tmp_path):
    """Maven's reader ignores `<plugin>` blocks for the same reason."""
    res = run(tmp_path, {"build.gradle":
                         "buildscript {\n"
                         "    dependencies {\n"
                         "        classpath 'com.example.build:gradle-plugin:7.0.2'\n"
                         "    }\n"
                         "}\n"})
    assert deps(res) == [] and res.unresolved == []


def test_local_file_and_project_references_name_nothing_outside_the_build(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         "dependencies {\n"
                         "    compile fileTree(dir: 'libs', include: ['*.jar'])\n"
                         "    implementation files('libs/extra.jar')\n"
                         "    implementation project(':sibling')\n"
                         "}\n"})
    assert deps(res) == []
    assert res.unresolved == [], \
        "jars on disk have no module identity and project() is same-build by definition"


def test_an_expression_dependency_is_a_loss_and_says_so(tmp_path):
    """A version catalog (`libs.…`) names a module this reader cannot identify. Silent
    skip would under-report exactly the modern repos; a row per line is the record."""
    res = run(tmp_path, {"build.gradle":
                         "dependencies {\n"
                         "    implementation libs.core.util\n"
                         "    implementation 'org.example:readable:1.0'\n"
                         "}\n"})
    (row,) = res.unresolved
    assert row["kind"] == "unreadable-declaration"
    assert row["raw"] == "implementation libs.core.util"
    assert row["line"] == 2
    assert len(deps(res)) == 1, "the readable line still produces its edge"


def test_android_dsl_words_that_start_like_configurations_are_not_dependencies(tmp_path):
    res = run(tmp_path, {"build.gradle":
                         "android {\n"
                         "    compileSdkVersion 26\n"
                         "    compileOptions {\n"
                         "    }\n"
                         "}\n"
                         "version = '1.0.1'\n"})
    assert deps(res) == [] and res.unresolved == []
    (b,) = built(res)
    assert b.dst == ids.module_id("svc", "svc"), "group-less: scoped to the component"


# ── identity: positional, inherited, and scoped when unpublishable ────────────


def test_two_group_less_app_modules_never_merge_across_components(tmp_path):
    """THE identity ruling. Without a `group`, every Android wrapper in every component
    is `:app`; one shared node would be a join nobody declared, in the layer whose whole
    value is that joins are declarations."""
    wrapper = {"android/settings.gradle": 'include ":app"\n',
               "android/build.gradle": "allprojects { }\n",
               "android/app/build.gradle": "dependencies {\n"
                                           "    implementation 'org.example:sdk:1.0'\n"
                                           "}\n"}
    res = gradle.GradleModulesExtractor().extract(
        ctx_for(tmp_path, {"app-one": dict(wrapper), "app-two": dict(wrapper)}))
    apps = {e.dst for e in built(res) if e.dst.endswith(":app")}
    assert apps == {ids.module_id("app-one", "app"), ids.module_id("app-two", "app")}
    assert {e.dst for e in deps(res)} == {ids.module_id("org.example", "sdk")}, \
        "a REAL coordinate still joins across components"


def test_a_subproject_inherits_the_build_roots_group_and_is_named_by_its_directory(tmp_path):
    res = run(tmp_path, {"settings.gradle": "rootProject.name = 'shop'\ninclude ':cart'\n",
                         "build.gradle": "group = 'com.example.shop'\nversion = '3.1.0'\n",
                         "cart/build.gradle": "dependencies {\n"
                                              "    api 'org.example:money:2.0'\n"
                                              "}\n"})
    by_dst = {e.dst: e for e in built(res)}
    assert ids.module_id("com.example.shop", "cart") in by_dst
    assert ids.module_id("com.example.shop", "shop") in by_dst


def test_a_bare_manifest_with_no_settings_anywhere_still_builds(tmp_path):
    res = run(tmp_path, {"build.gradle": "dependencies {\n"
                                         "    implementation 'org.example:ui:1.0'\n"
                                         "}\n"}, alias="standalone")
    (b,) = built(res)
    assert b.dst == ids.module_id("standalone", "standalone"), \
        "at the repo root the component alias is the positional name"
    assert len(deps(res)) == 1


def test_include_build_is_not_include(tmp_path):
    """The composite-build idiom (`includeBuild("../path")`) names a PATH, not a module
    of this build — reading it as an include would subtract a coordinate nobody owns."""
    assert gradle.parse_settings(
        'pluginManagement {\n    includeBuild("$sdk/tools/gradle")\n}\n'
        "include ':app', ':core'\n") == {"root_name": "", "includes": ["app", "core"]}


def test_a_nested_include_subtracts_by_its_last_segment(tmp_path):
    res = run(tmp_path, {"settings.gradle": "include ':libs:money'\n",
                         "build.gradle": "group = 'com.example.shop'\n"
                                         "dependencies {\n"
                                         "    implementation 'com.example.shop:money:1.0'\n"
                                         "}\n"})
    assert deps(res) == [], "':libs:money' IS this build's money module"


def test_a_manifest_under_a_test_tree_is_not_read(tmp_path):
    res = run(tmp_path, {"src/test/resources/build.gradle":
                         "dependencies {\n"
                         "    implementation 'org.example:decoy:1.0'\n"
                         "}\n"})
    assert res.edges == [] and res.nodes == []


# ── the registry ──────────────────────────────────────────────────────────────


def test_the_package_registers_through_the_real_pipeline():
    regs = {r.name: r for r in B.discover_extractors()}
    assert "gradle-modules" in regs
    assert regs["gradle-modules"].layer == "stack"
    assert regs["gradle-modules"].globs == ("build.gradle", "settings.gradle")
