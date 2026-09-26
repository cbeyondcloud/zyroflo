"""T531, measured where a person sees it: a real build of a component whose production code
is named like the test conventions.

Four identical Spring controllers in `src/main/java`, and a real test in `src/test/java`. Before
T531 only `CreditController` was read: `Credit.java` ended in a folded `it.java`, `Latest.java`
in `test.java`, and the `spec` and `it` packages were test directories - three routes gone with
no row, and on Windows two of them minted as test suites the Linux build never saw.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

CONTROLLER = """package com.example.%(pkg)s;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/%(route)s")
public class %(cls)s {
    @GetMapping("/limit")
    public String limit() { return "x"; }
}
"""

TEST = """package com.example.credit;

class CreditControllerTest {
    void callsIt() { String path = "/control/limit"; }
}
"""


def test_production_code_named_like_a_test_is_read_and_a_real_test_still_is_a_test(
        tmp_path: Path):
    comp = tmp_path / "credit-api"
    for rel, (pkg, route, cls) in {
        "src/main/java/com/example/credit/Credit.java": ("credit", "credit", "Credit"),
        "src/main/java/com/example/credit/CreditController.java":
            ("credit", "control", "CreditController"),
        "src/main/java/com/example/spec/Latest.java": ("spec", "latest", "Latest"),
        "src/main/java/com/example/it/Ops.java": ("it", "ops", "Ops"),
    }.items():
        (comp / rel).parent.mkdir(parents=True, exist_ok=True)
        (comp / rel).write_text(CONTROLLER % {"pkg": pkg, "route": route, "cls": cls},
                                encoding="utf-8")
    test = comp / "src/test/java/com/example/credit/CreditControllerTest.java"
    test.parent.mkdir(parents=True)
    test.write_text(TEST, encoding="utf-8")
    (tmp_path / "workspace.toml").write_text(
        'schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n'
        '[[component]]\nname = "credit-api"\npath = "credit-api"\n', encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-m", "tools.graph.build", "--workspace",
                           str(tmp_path / "workspace.toml"), "--out", str(out)],
                          cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    rows = [json.loads(line) for line in
            (out / "graph.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    exposed = sorted(r["dst"] for r in rows if r.get("type") == "DG_EXPOSES")
    assert exposed == ["ep:/control/limit", "ep:/credit/limit", "ep:/latest/limit",
                       "ep:/ops/limit"]
    tests = sorted(r["id"] for r in rows if str(r.get("id", "")).startswith("test:"))
    assert tests == [
        "test:credit-api/src/test/java/com/example/credit/CreditControllerTest.java"]


def _off_case_workspace(tmp_path: Path) -> Path:
    comp = tmp_path / "ledger"
    (comp / "db").mkdir(parents=True)
    (comp / "db" / "V4__add_owner.sql").write_text(
        "CREATE TABLE ledger_owner (id INT);\n", encoding="utf-8")
    (comp / "db" / "v3__add_index.sql").write_text(
        "CREATE TABLE ledger_index (id INT);\n", encoding="utf-8")
    (comp / "db" / "v2__seed.sql").write_text(
        "CREATE TABLE ledger_seed (id INT);\n", encoding="utf-8")
    (comp / "src" / "main" / "java").mkdir(parents=True)
    (comp / "src" / "main" / "java" / "Audit.java").write_text("class Audit {}\n",
                                                               encoding="utf-8")
    ws = tmp_path / "workspace.toml"
    ws.write_text('schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n'
                  '[[component]]\nname = "ledger"\npath = "ledger"\n', encoding="utf-8")
    return ws


def _build(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tools.graph.build", "--workspace", str(ws),
                           *args], cwd=str(ROOT), capture_output=True, text=True)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_a_file_no_extractor_reads_because_of_its_case_is_reported(tmp_path: Path):
    """T531: matching is case-sensitive everywhere, so a lowercase Flyway prefix is no
    longer a migration on Windows either - and until now it was silently not one on Linux.
    The activation walk names it, with the pattern and the extractor it missed. A file some
    extractor DOES claim - `Audit.java` misses `*IT.java` by case, and `*.java` reads it - is
    no loss, and earns no row."""
    ws = _off_case_workspace(tmp_path)
    proc = _build(ws, "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    rows = [r for r in _rows(tmp_path / "out" / "unresolved.jsonl")
            if r["kind"] == "off-case-input"]
    # B9-2: two migrations with a lowercase prefix are ONE row, the files listed.
    assert [(r["component"], r["files"], r["globs"], r["extractors"]) for r in rows] == [
        ("ledger", ["db/v2__seed.sql", "db/v3__add_index.sql"], ["V*__*.sql"],
         ["sql-migrations"])]
    assert rows[0]["source_file"] == "db/v2__seed.sql"
    assert rows[0]["extractor"] == "activation" and rows[0]["line"] == 0


def test_the_off_case_row_is_part_of_its_components_shard(tmp_path: Path):
    """The row is a fact about the component's files, so it lives in the component's
    committed shard like every extractor's rows - and `rebuild == snapshot` still holds,
    which is what the snapshot check is for."""
    ws = _off_case_workspace(tmp_path)
    proc = _build(ws, "--publish-snapshot", "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    shard = tmp_path / "ledger" / ".sdlc" / "graph" / "shard.unresolved.jsonl"
    assert [r["files"] for r in _rows(shard) if r["kind"] == "off-case-input"] == [
        ["db/v2__seed.sql", "db/v3__add_index.sql"]]
    check = _build(ws, "--check-snapshot")
    assert check.returncode == 0, check.stdout[-2000:] + check.stderr[-2000:]
    assert "DRIFT" not in check.stdout


#: Where a claimable file can sit. A name pattern is tried under all of them, a web front
#: end below `src/main/` included - the layout v1 of T531's design got wrong (D9-7).
_PREFIXES = ("", "src/test/java/com/x/", "src/it/java/com/x/", "src/main/java/com/x/",
             "src/main/webapp/app/orders/", "modules/orders/")


def _claimable_paths():
    from tools.graph.extractors import tests as tests_extractor
    for glob in tests_extractor.INPUT_GLOBS:
        name = glob.replace("*", "Orders" if glob.startswith("*") and glob[1:2].isupper()
                            else "orders")
        if "/" in glob:
            # A path pattern: under the root and a nested module. Below `src/main/` the
            # first source set decides, and a `src/test/` nested inside production code
            # names no layout anyone builds.
            name = glob.replace("*", "Helper.java")
            yield from (prefix + name for prefix in ("", "modules/orders/"))
        else:
            yield from (prefix + name for prefix in _PREFIXES)


def test_every_file_the_tests_extractor_can_claim_is_test_code():
    """A regression guard, green before T531 and after it: the tests extractor claims files
    by its globs and every other extractor skips what `looks_like_test` calls a test, so a
    file claimable as a suite that the predicate calls production is read by BOTH, as two
    different things. It would have failed against v1 of the design, which read
    `src/main/webapp/**/*.spec.ts` as production."""
    from tools.graph.core.lib import repo_walk
    paths = list(_claimable_paths())
    assert len(paths) > 40, "the generator must actually exercise the globs"
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        from tools.graph.extractors import tests as tests_extractor
        assert any(repo_walk.glob_match(rel, name, g) for g in tests_extractor.INPUT_GLOBS), rel
        assert repo_walk.looks_like_test(rel), rel


def test_a_project_kept_under_a_test_directory_is_a_fixture_not_production(tmp_path: Path):
    """B9-1: the `src/<set>` rule answered on the first `src/main` it met, so a fixture
    project under `tests/` became production code and its endpoint entered the graph as the
    component's own - the direction `looks_like_test` names as the worse one."""
    comp = tmp_path / "app"
    for rel, (pkg, route, cls) in {
        "tests/fixtures/demo/src/main/java/com/example/demo/DemoController.java":
            ("demo", "fixture-only", "DemoController"),
        "src/main/java/com/example/real/RealController.java": ("real", "real", "RealController"),
    }.items():
        (comp / rel).parent.mkdir(parents=True, exist_ok=True)
        (comp / rel).write_text(CONTROLLER % {"pkg": pkg, "route": route, "cls": cls},
                                encoding="utf-8")
    ws = tmp_path / "workspace.toml"
    ws.write_text('schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n'
                  '[[component]]\nname = "app"\npath = "app"\n', encoding="utf-8")
    proc = _build(ws, "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    exposed = sorted(r["dst"] for r in _rows(tmp_path / "out" / "graph.jsonl")
                     if r.get("type") == "DG_EXPOSES")
    assert exposed == ["ep:/real/limit"]


def test_every_reader_a_file_missed_by_case_is_named(tmp_path: Path):
    """B9-3: `values.YAML` misses helm's `values.yaml` - the reader that lost it - and a
    catch-all `*.yaml`; naming the first pattern in sort order sent the reader elsewhere."""
    ws = _off_case_workspace(tmp_path)
    chart = tmp_path / "ledger" / "charts" / "api"
    chart.mkdir(parents=True)
    (chart / "values.YAML").write_text("service:\n  name: api\n", encoding="utf-8")
    proc = _build(ws, "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    rows = [r for r in _rows(tmp_path / "out" / "unresolved.jsonl")
            if r["kind"] == "off-case-input" and "charts/api/values.YAML" in r["files"]]
    assert len(rows) == 1, rows
    assert "values.yaml" in rows[0]["globs"] and "helm-deployments" in rows[0]["extractors"]


def test_a_narrowed_build_reports_no_loss_a_full_build_would_not(tmp_path: Path):
    """B9-13: `--only tests` walked with the tests extractor's globs alone, so `Audit.java`
    - which misses `*IT.java` by case and which `*.java` reads in any full build - was
    reported as read by nobody."""
    ws = _off_case_workspace(tmp_path)
    # The narrowed run claims nothing here, and a build that read nothing does not exit 0;
    # what matters is the rows it wrote.
    _build(ws, "--only", "tests", "--out", str(tmp_path / "out"))
    files = [f for r in _rows(tmp_path / "out" / "unresolved.jsonl")
             if r["kind"] == "off-case-input" for f in r["files"]]
    assert "src/main/java/Audit.java" not in files
    assert files == ["db/v2__seed.sql", "db/v3__add_index.sql"], \
        "a file NO registered extractor reads is still reported under --only"


def test_a_new_off_case_file_makes_its_component_stale(tmp_path: Path):
    """B9-4: the freshness stamp digested only the files extractors read, so a new off-case
    file left it unchanged - `--check-fresh` called a drifted snapshot current, and
    `--incremental` reused the shard without the new row."""
    ws = _off_case_workspace(tmp_path)
    proc = _build(ws, "--publish-snapshot", "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    (tmp_path / "ledger" / "db" / "v5__late.sql").write_text("CREATE TABLE t (id INT);\n",
                                                            encoding="utf-8")
    fresh = _build(ws, "--check-fresh")
    assert fresh.returncode == 2, fresh.stdout[-2000:]
    assert "STALE ledger" in fresh.stdout and "because of their case" in fresh.stdout
    inc = _build(ws, "--incremental", "--out", str(tmp_path / "inc"))
    assert inc.returncode == 0, inc.stdout[-2000:] + inc.stderr[-2000:]
    files = [f for r in _rows(tmp_path / "inc" / "unresolved.jsonl")
             if r["kind"] == "off-case-input" for f in r["files"]]
    assert "db/v5__late.sql" in files


def test_the_report_says_how_many_files_a_grouped_row_stands_for(tmp_path: Path):
    """B9-17: one row stands for every file missing the same globs, and the build report's
    projection kept one site per ROW - so a dashboard read two unread migrations as one."""
    ws = _off_case_workspace(tmp_path)
    proc = _build(ws, "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    report = json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))
    groups = [g for g in report["unresolved_patterns"] if g["kind"] == "off-case-input"]
    assert [s.get("files") for g in groups for s in g["sites"]] == [2]
