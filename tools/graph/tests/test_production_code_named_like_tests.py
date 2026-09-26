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
