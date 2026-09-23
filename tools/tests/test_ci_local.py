"""T461: local CI restates the GitHub workflow's pins - tied here so they cannot drift.

`tools/ci_local.py` runs `.github/workflows/ci.yml`'s jobs on this machine.
Every value it restates (the Python version, the extras, the vendored tools,
the gitleaks image, the skip gates) is read back out of the workflow here: a
workflow edited without the script is a local CI that tests something else.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import ci_local as C  # noqa: E402

WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_python_version_is_the_workflows():
    assert f'PYTHON_VERSION: "{C.PYTHON_VERSION}"' in WORKFLOW


def test_the_extras_and_the_vendored_tools_are_the_workflows():
    assert f"uv sync --locked --python \"${{PYTHON_VERSION}}\" {C.EXTRAS}" in WORKFLOW
    assert f"--fetch --only {C.VENDORED}" in WORKFLOW


def test_the_images_are_the_workflows():
    assert C.GITLEAKS_IMAGE in WORKFLOW and C.NEO4J_IMAGE in WORKFLOW


def test_the_skip_gates_the_workflow_sets_are_set_here():
    for gate in ("SDLC_FORBID_SKIPS", "SDLC_REQUIRE_VENDORED_BINARIES"):
        assert f'{gate}: "1"' in WORKFLOW
        assert f"{gate}=1" in (ROOT / "tools" / "ci_local.py").read_text(encoding="utf-8")


def test_the_suite_script_runs_the_workflows_steps_in_order():
    script = C.suite_script()
    order = ["uv sync --locked", "wait_neo4j.py", "third_party.py --fetch",
             "git diff --exit-code -- third_party/manifest.json", "pytest"]
    at = [script.index(step) for step in order]
    assert at == sorted(at), script
    assert " && " in script, "a failed step must stop the job, as it does on the runner"


def test_the_workflow_is_not_triggered_by_a_push():
    """T460/T461: CI is local now; the workflow must not spend on a push or a PR."""
    on = WORKFLOW.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert "workflow_dispatch" in on
    assert "push:" not in on and "pull_request:" not in on
