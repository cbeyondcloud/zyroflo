"""Item 29 — the provider-swap smoke (recurring M2-M6 gate).

Pins, in order of load: (1) the smoke's breadcrumbs pass on the REAL
installer's AGENTS.md output — the weld that makes Claude-coupling in the
instruction chain a CI failure, not a discovery at swap time; (2) the null
provider records its review in the SMOKE'S scratch metrics, never the
adopter's — a two-second mechanical row in the real reviews.jsonl would bias
the exact median the M4 gate reads (item 27's rule); (3) verification reads
artifacts, not runner exit codes, and an unfired pre-pass gate outranks a
findings run (the pre-pass's own exit-3-over-1 rule, carried through).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.provider_smoke as S  # noqa: E402


def _load_init():
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", mod)
    spec.loader.exec_module(mod)
    return mod


# ── breadcrumbs: the discovery chain ─────────────────────────────────────────


def _target_with(tmp_path: Path, agents_text: str | None, graph: bool = True) -> Path:
    t = tmp_path / "target"
    t.mkdir(exist_ok=True)
    if agents_text is not None:
        (t / "AGENTS.md").write_text(agents_text, encoding="utf-8")
    if graph:
        g = t / ".sdlc" / "graph"
        g.mkdir(parents=True, exist_ok=True)
        (g / "graph.jsonl").write_text("", encoding="utf-8")
    return t


def test_missing_agents_md_is_the_one_problem_and_names_the_installer(tmp_path):
    (problem,) = S.breadcrumbs(_target_with(tmp_path, None))
    assert "AGENTS.md" in problem and "sdlc-init" in problem


def test_every_broken_link_is_named(tmp_path):
    target = _target_with(tmp_path, "nothing useful here", graph=False)
    problems = "\n".join(S.breadcrumbs(target))
    assert "impact-review" in problems
    assert "uv run --project" in problems
    assert "no path to the impact-review SOP" in problems
    assert "graph" in problems


def test_a_cited_sop_that_does_not_exist_fails_with_the_rerun_hint(tmp_path):
    text = ("impact-review via `uv run --project x` — see "
            "`/nowhere/workflows/impact-review.md`")
    problems = "\n".join(S.breadcrumbs(_target_with(tmp_path, text)))
    assert "does not exist" in problems and "re-run" in problems


def test_the_real_installer_output_walks_clean(tmp_path):
    """THE WELD: the smoke's discovery chain must hold on what sdlc-init.py
    actually writes. If the addendum ever drops the SOP path or the CLI-tier
    prefix, this fails in CI — before any swap day."""
    si = _load_init()
    target = _target_with(tmp_path, si.graph_addendum(ROOT))
    assert S.breadcrumbs(target) == []


# ── the null provider ────────────────────────────────────────────────────────


class FakeRun:
    """Records commands; scripted exits by substring; writes the artifacts the
    real tools would, so downstream steps have something to read."""

    def __init__(self, out_dir: Path, exits: dict[str, int] | None = None,
                 diagnostics: list[dict] | None = None):
        self.out_dir, self.exits = out_dir, exits or {}
        self.diagnostics = diagnostics or []
        self.commands: list[list[str]] = []

    def __call__(self, cmd, cwd=None):
        self.commands.append([str(c) for c in cmd])
        joined = " ".join(str(c) for c in cmd)
        if "prepass.py" in joined:
            p = self.out_dir / "prepass"
            p.mkdir(parents=True, exist_ok=True)
            (p / "prepass.json").write_text(json.dumps(
                {"sources": [{"diagnostics": self.diagnostics}]}), encoding="utf-8")
        for key, code in self.exits.items():
            if key in joined:
                return code, f"scripted {key} -> {code}"
        return 0, "ok"


def _steps(commands: list[list[str]]) -> list[str]:
    out = []
    for c in commands:
        joined = " ".join(c)
        if "--begin" in c:
            out.append("begin")
        elif "prepass.py" in joined:
            out.append("prepass")
        elif "pack.py" in joined:
            out.append("pack")
        elif "--finding" in c:
            out.append("finding")
        elif "--end" in c:
            out.append("end")
        elif "--abandon" in c:
            out.append("abandon")
    return out


def test_null_provider_runs_the_sop_spine_in_order(tmp_path):
    out = tmp_path / "smoke"
    fake = FakeRun(out)
    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               out, run=fake)
    assert problems == []
    assert _steps(fake.commands) == ["begin", "prepass", "pack", "end"]


def test_the_smoke_never_writes_the_adopters_metrics(tmp_path):
    """Item 27's rule carried through: every hitl call targets the smoke's
    scratch dir, not the adopter root — a mechanical row in the real
    reviews.jsonl would bias the median the M4 gate reads."""
    target, out = tmp_path / "t", tmp_path / "smoke"
    fake = FakeRun(out)
    S.null_provider(target, tmp_path / "d.patch", "comp", ".", out, run=fake)
    for cmd in fake.commands:
        if "hitl.py" in " ".join(cmd):
            assert str(out) in cmd, "hitl must target the scratch metrics"
            assert str(target) not in cmd


def test_an_unfired_prepass_gate_outranks_and_the_session_is_abandoned(tmp_path):
    out = tmp_path / "smoke"
    fake = FakeRun(out, exits={"prepass.py": 3})
    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               out, run=fake)
    assert any("outranks" in p for p in problems)
    assert _steps(fake.commands)[-1] == "abandon", \
        "a failed smoke still closes its session honestly - as a recorded abandon"


def test_a_findings_exit_is_not_a_failure_and_real_diagnostics_are_recorded(tmp_path):
    out = tmp_path / "smoke"
    diags = [{"code": {"value": "rule-x"}, "severity": "ERROR",
              "location": {"path": "a.py", "range": {"start": {"line": 7}}}}]
    fake = FakeRun(out, exits={"prepass.py": 1}, diagnostics=diags)
    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               out, run=fake)
    assert problems == []
    finding = next(c for c in fake.commands if "--finding" in c)
    assert "rule-x" in finding and "prepass" in finding
    assert _steps(fake.commands) == ["begin", "prepass", "pack", "finding", "end"]


def test_a_pack_failure_is_a_named_problem(tmp_path):
    fake = FakeRun(tmp_path / "smoke", exits={"pack.py": 2})
    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               tmp_path / "smoke", run=fake)
    assert any("pack failed" in p for p in problems)


# ── the live runner ──────────────────────────────────────────────────────────


def test_live_provider_hands_the_prompt_as_the_final_argument(tmp_path):
    seen = {}

    def fake(cmd, cwd=None):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return 0, ""

    assert S.live_provider("some-agent --yolo", tmp_path / "d.patch",
                           tmp_path / "out", cwd=tmp_path, run=fake) == []
    assert seen["cmd"][:2] == ["some-agent", "--yolo"]
    prompt = seen["cmd"][-1]
    assert "AGENTS.md" in prompt and "CLI tier" in prompt and "no MCP" in prompt
    assert str(tmp_path / "out") in prompt, \
        "the prompt routes hitl at the scratch metrics, same as the null provider"


def test_a_runner_crash_is_a_problem_but_success_is_not_trusted(tmp_path):
    problems = S.live_provider("agent", tmp_path / "d", tmp_path / "o",
                               cwd=tmp_path, run=lambda c, cwd=None: (9, "boom"))
    assert problems and "9" in problems[0]
    assert S.live_provider("agent", tmp_path / "d", tmp_path / "o",
                           cwd=tmp_path, run=lambda c, cwd=None: (0, "")) == [], \
        "exit 0 adds no problems - verify() is the judge, not the runner's code"


# ── verification: artifacts, not exit codes ──────────────────────────────────


def _green_artifacts(out: Path, diff: Path, since: str = "2000-01-01") -> None:
    m = out / ".sdlc" / "metrics"
    m.mkdir(parents=True, exist_ok=True)
    (m / "reviews.jsonl").write_text(json.dumps(
        {"kind": "review", "started_at": "2999-01-01T00:00:00+00:00"}) + "\n",
        encoding="utf-8")
    p = out / "prepass"
    p.mkdir(parents=True, exist_ok=True)
    (p / "prepass.json").write_text(json.dumps(
        {"tools": [{"name": "gitleaks", "status": "ok"},
                   {"name": "ast-grep", "status": "ok"}]}), encoding="utf-8")
    k = out / "pack"
    k.mkdir(parents=True, exist_ok=True)
    (k / "pack.md").write_text("## Seeds\n- hunk 2 seed(s) src/app.py\n",
                               encoding="utf-8")
    (k / "pack.json").write_text("{}", encoding="utf-8")
    diff.write_text("diff --git a/src/app.py b/src/app.py\n", encoding="utf-8")


def test_verify_green_on_complete_artifacts(tmp_path):
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    assert S.verify(out, diff, since="2000-01-01") == []


@pytest.mark.parametrize("wreck, expect", [
    (lambda o: (o / ".sdlc" / "metrics" / "reviews.jsonl").unlink(),
     "no completed review"),
    (lambda o: (o / ".sdlc" / "metrics" / "review-in-progress.json")
     .write_text("{}", encoding="utf-8"), "never closed"),
    (lambda o: (o / "prepass" / "prepass.json").write_text(json.dumps(
        {"tools": [{"name": "gitleaks", "status": "not-run"},
                   {"name": "ast-grep", "status": "ok"}]}), encoding="utf-8"),
     "'gitleaks' did not fire"),
    (lambda o: (o / "pack" / "pack.md").write_text("no seeds here",
                                                   encoding="utf-8"),
     "no Seeds block"),
    (lambda o: (o / "pack" / "pack.md").write_text("## Seeds\n(empty)\n",
                                                   encoding="utf-8"),
     "never names"),
    (lambda o: (o / "pack" / "pack.json").unlink(), "untrimmed machine copy"),
])
def test_verify_names_each_missing_artifact(tmp_path, wreck, expect):
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    wreck(out)
    problems = "\n".join(S.verify(out, diff, since="2000-01-01"))
    assert expect in problems


def test_an_abandoned_row_alone_is_not_a_completed_run(tmp_path):
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    (out / ".sdlc" / "metrics" / "reviews.jsonl").write_text(json.dumps(
        {"kind": "review-abandoned", "started_at": "2999-01-01T00:00:00+00:00"})
        + "\n", encoding="utf-8")
    problems = "\n".join(S.verify(out, diff, since="2000-01-01"))
    assert "did not finish" in problems


# ── the recurring record ─────────────────────────────────────────────────────


def test_the_record_is_append_only_and_names_the_runner(tmp_path):
    p1 = S.record(tmp_path, "null-provider (CLI-tier closure; not a live agent)",
                  "green", [], tmp_path / "o")
    S.record(tmp_path, "codex exec", "red", ["x"], tmp_path / "o")
    rows = [json.loads(x) for x in p1.read_text(encoding="utf-8").splitlines()]
    assert [r["runner"] for r in rows] == [
        "null-provider (CLI-tier closure; not a live agent)", "codex exec"]
    assert rows[0]["kind"] == "provider-smoke" and rows[1]["problems"] == ["x"]
    assert p1 == tmp_path / ".sdlc" / "metrics" / "provider-smoke.jsonl"


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_main_green_writes_the_record_and_exits_zero(tmp_path, monkeypatch, capsys):
    target = tmp_path / "t"
    target.mkdir()
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    monkeypatch.setattr(S, "breadcrumbs", lambda t: [])
    monkeypatch.setattr(S, "null_provider", lambda *a, **k: [])
    monkeypatch.setattr(S, "verify", lambda *a, **k: [])
    assert S.main(["--target", str(target), "--diff", str(diff),
                   "--component", "comp"]) == 0
    out = capsys.readouterr().out
    assert "green" in out and "null-provider" in out
    row = json.loads((target / ".sdlc" / "metrics" / "provider-smoke.jsonl")
                     .read_text(encoding="utf-8"))
    assert row["verdict"] == "green" and row["workflow"] == "impact-review"


def test_main_red_exits_one_with_problems_printed(tmp_path, monkeypatch, capsys):
    target = tmp_path / "t"
    target.mkdir()
    diff = tmp_path / "d.patch"
    diff.write_text("", encoding="utf-8")
    monkeypatch.setattr(S, "breadcrumbs", lambda t: ["chain broken"])
    assert S.main(["--target", str(target), "--diff", str(diff),
                   "--component", "comp"]) == 1
    out = capsys.readouterr().out
    assert "red" in out and "chain broken" in out


def test_main_refuses_a_missing_diff(tmp_path, capsys):
    target = tmp_path / "t"
    target.mkdir()
    assert S.main(["--target", str(target), "--diff", str(tmp_path / "nope"),
                   "--component", "c"]) == 2
