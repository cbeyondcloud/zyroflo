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
    assert [(r["component"], r["source_file"], r["glob"], r["extractors"]) for r in rows] == [
        ("ledger", "db/v3__add_index.sql", "V*__*.sql", ["sql-migrations"])]
    assert rows[0]["extractor"] == "activation" and rows[0]["line"] == 0


def test_the_off_case_row_is_part_of_its_components_shard(tmp_path: Path):
    """The row is a fact about the component's files, so it lives in the component's
    committed shard like every extractor's rows - and `rebuild == snapshot` still holds,
    which is what the snapshot check is for."""
    ws = _off_case_workspace(tmp_path)
    proc = _build(ws, "--publish-snapshot", "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    shard = tmp_path / "ledger" / ".sdlc" / "graph" / "shard.unresolved.jsonl"
    assert [r["source_file"] for r in _rows(shard) if r["kind"] == "off-case-input"] == [
        "db/v3__add_index.sql"]
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
