"""Item 29 — the provider-swap smoke (recurring M2-M6 gate).

Pins, in order of load: (1) the smoke's breadcrumbs pass on the REAL
installer's AGENTS.md output — the weld that makes Claude-coupling in the
instruction chain a CI failure, not a discovery at swap time; (2) the null
provider records its review in the SMOKE'S scratch metrics, never the
adopter's — a two-second mechanical row in the real reviews.jsonl would bias
the exact median the M4 gate reads (item 27's rule); (3) verification reads
artifacts, not runner exit codes, and an unfired pre-pass gate outranks a
findings run (the pre-pass's own exit-3-over-1 rule, carried through).

Canned credentials here are PLACEHOLDER-SHAPED on purpose and never sit next
to a real key-name identifier — the harness's own CI gitleaks scan flags
realistic pairs like OPENAI_API_KEY="sk-..." in test source (item 25's
recorded lesson; relearned once by this file's first push going red in CI).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.provider_smoke as S  # noqa: E402
import tools.review.hitl as H  # noqa: E402
import tools.review.prepass as PP  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_harness(tmp_path, monkeypatch):
    """No test may read the developer's REAL <harness>/.env. Three tests did:
    their scrub set was whatever the machine happened to hold, so an assertion
    could fail locally and never in CI - and a unit run parsed live
    credentials into memory for no test purpose. Tests that need a .env point
    HARNESS at their own tmp_path and override this."""
    monkeypatch.setattr(S, "HARNESS", tmp_path / "harness-fixture")


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

    def __call__(self, cmd, cwd=None, env=None):
        self.commands.append([str(c) for c in cmd])
        self.envs = getattr(self, "envs", [])
        self.envs.append(env)
        joined = " ".join(str(c) for c in cmd)
        if "prepass.py" in joined:
            p = self.out_dir / "prepass"
            p.mkdir(parents=True, exist_ok=True)
            (p / "prepass.json").write_text(json.dumps(
                {"tools": [{"name": "gitleaks", "status": "ok"},
                           {"name": "ast-grep", "status": "ok"}],
                 "sources": [{"diagnostics": self.diagnostics}]}), encoding="utf-8")
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

    def fake(cmd, cwd=None, env=None):
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
    problems = S.live_provider(
        "agent", tmp_path / "d", tmp_path / "o", cwd=tmp_path,
        run=lambda c, cwd=None, env=None: (9, "boom"))
    assert problems and "9" in problems[0]
    assert S.live_provider(
        "agent", tmp_path / "d", tmp_path / "o", cwd=tmp_path,
        run=lambda c, cwd=None, env=None: (0, "")) == [], \
        "exit 0 adds no problems - verify() is the judge, not the runner's code"


# ── credentials: .env in, secrets never out ──────────────────────────────────


#: Canned "secrets": all-one-letter runs — length over the secret_values
#: floor, entropy near zero, so no gitleaks generic rule (CI's or the
#: stricter vendored one) can read them as credentials. Names carry KEY only
#: where the test NEEDS secret_values to pick them.
A12, B12, Q16, W12, Z20 = "a" * 12, "b" * 12, "q" * 16, "w" * 12, "z" * 20


def test_load_env_parses_the_minimal_grammar(tmp_path):
    """Pure parser test — no secret-shaped names needed at all."""
    f = tmp_path / ".env"
    f.write_text("# comment\n\nALPHA=plain-value\n"
                 "QUOTED=\"in quotes\"\nSPACED = padded \n"
                 "not-a-pair\n=novalue\n", encoding="utf-8")
    assert S.load_env(f) == {"ALPHA": "plain-value",
                             "QUOTED": "in quotes",
                             "SPACED": "padded"}
    assert S.load_env(tmp_path / "absent") == {}


def test_runner_env_fills_from_dotenv_but_the_shell_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("FILE_ONLY_VAR=from-file-value\n"
                                   "BOTH_VAR=file-loses\n", encoding="utf-8")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    monkeypatch.setenv("BOTH_VAR", "shell-wins")
    env = S.runner_env()
    assert env["FILE_ONLY_VAR"] == "from-file-value"
    assert env["BOTH_VAR"] == "shell-wins"


def test_the_live_runner_gets_the_env_and_the_null_provider_does_not(tmp_path, monkeypatch):
    """Only the process we HAND the credential to sees it: the null provider's
    harness CLIs run with the inherited environment (env=None), never with
    the .env injection."""
    (tmp_path / ".env").write_text("SMOKE_VAR=injected-value\n", encoding="utf-8")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    seen = {}

    def fake(cmd, cwd=None, env=None):
        seen["env"] = env
        return 0, ""

    S.live_provider("agent", tmp_path / "d", tmp_path / "o", cwd=tmp_path,
                    run=fake)
    assert seen["env"]["SMOKE_VAR"] == "injected-value"

    out = tmp_path / "smoke"
    null_fake = FakeRun(out)
    S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".", out,
                    run=null_fake)
    assert all(e is None for e in null_fake.envs)


def test_secret_values_picks_secret_names_and_skips_short_ones():
    env = {"SMOKE_FILE_KEY": A12, "FAKE_PUSH_TOKEN": B12,
           "DEBUG_KEY": "true", "PATH": "/usr/bin:/very/long/path", "PORT": "8080"}
    assert S.secret_values(env) == {A12, B12}


def test_scrub_replaces_longest_first():
    hidden = {"pl-abc", "pl-abcdefgh"}
    assert S.scrub("x pl-abcdefgh y pl-abc z", hidden) == \
        "x <redacted> y <redacted> z"


def test_the_live_runners_output_is_scrubbed_before_truncation(tmp_path, monkeypatch):
    """A secret at the truncation boundary must be GONE, not clipped — a
    partial key is still a leak."""
    (tmp_path / ".env").write_text(f"CRASH_API_KEY={Z20}\n", encoding="utf-8")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    noise = "x" * 290
    problems = S.live_provider(
        "agent", tmp_path / "d", tmp_path / "o", cwd=tmp_path,
        run=lambda c, cwd=None, env=None: (9, noise + Z20))
    assert "zzzzzzzz" not in problems[0], \
        "the secret survived - scrub must run BEFORE the 300-char truncation"
    assert "<redacted>" in problems[0]


def test_the_record_scrubs_problems_and_the_runner_field(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"REC_API_KEY={Q16}\n", encoding="utf-8")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    p = S.record(tmp_path, f"agent --api-key {Q16}", "red",
                 [f"runner said {Q16} loudly"], tmp_path / "o")
    raw = p.read_text(encoding="utf-8")
    assert Q16 not in raw
    row = json.loads(raw)
    assert row["runner"] == "agent --api-key <redacted>"
    assert row["problems"] == ["runner said <redacted> loudly"]


def test_main_never_prints_a_secret(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(f"MAIN_API_KEY={W12}\n", encoding="utf-8")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    target = tmp_path / "t"
    target.mkdir()
    diff = tmp_path / "d.patch"
    diff.write_text("", encoding="utf-8")
    monkeypatch.setattr(S, "breadcrumbs", lambda t: [f"chain broke: {W12}"])
    assert S.main(["--target", str(target), "--diff", str(diff),
                   "--component", "c"]) == 1
    out = capsys.readouterr().out
    assert W12 not in out and "<redacted>" in out


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
    # A REAL diff: parse_diff (the generator's grammar, which pack also uses)
    # reads paths from the `+++ b/` side, so the headers are load-bearing.
    diff.write_text("diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,3 @@\n",
                    encoding="utf-8")


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


def test_main_refuses_a_target_that_is_not_a_directory(tmp_path, capsys):
    """A typo'd --target used to be reported as 'AGENTS.md missing', which
    reads as 'the installer never wrote it' rather than 'you pointed at
    nowhere' - and the scratch tree materialised under the typo."""
    diff = tmp_path / "d.patch"
    diff.write_text("", encoding="utf-8")
    assert S.main(["--target", str(tmp_path / "nowhere"), "--diff", str(diff),
                   "--component", "c"]) == 2
    assert "no directory" in capsys.readouterr().out


# ── hardening: the gate must be able to fail, and must not be able to lie ────
#
# Every test below pins one defect a max-effort review found in the first cut.


def test_the_prompt_names_the_paths_verification_reads(tmp_path):
    """THE LIVE CONTRACT. verify() demands prepass.json and pack.md at exact
    paths; the SOP leaves each step's --out free. A runner that is never told
    where to write cannot pass, however well it performs - so the prompt must
    name the very paths verify() reads, from the one spelling both use."""
    pre_dir, pack_dir = S.artifacts(tmp_path / "out")
    prompt = S.PROMPT.format(diff=tmp_path / "d.patch", prepass=pre_dir,
                             pack=pack_dir, metrics=tmp_path / "out",
                             component="widgets")
    assert str(pre_dir) in prompt and str(pack_dir) in prompt

    seen = {}
    S.live_provider("agent", tmp_path / "d.patch", tmp_path / "out",
                    cwd=tmp_path, secrets=set(),
                    run=lambda c, cwd=None, env=None: (seen.update(cmd=c), (0, ""))[1])
    handed = seen["cmd"][-1]
    assert str(pre_dir) in handed and str(pack_dir) in handed


def test_the_prompt_tells_the_runner_to_follow_the_document_not_improvise():
    """Item 33's class, third instance — found by the first live goose run
    (2026-08-30, M6-C2). "Execute the workflow it names" can be satisfied in an
    agent's head: goose read AGENTS.md, never opened the workflow file it
    points at, improvised from the block's one example command, invented an
    `--output` flag and an `hitl record` spelling, and exited clean in 15
    seconds. The sentence pinned here is the requirement the runner was never
    told: open and FOLLOW the document, and do not invent commands.

    Asserted as the SENTENCE, not the vocabulary — checking words appear
    somewhere let mutations through twice on this project."""
    assert ("Open and FOLLOW that workflow document, step by step, in order - "
            "its steps name the exact CLI commands; do not improvise commands "
            "or flags the document does not state.") in S.PROMPT
    # The 4th attempt's finding: goose followed the SOP's literal `--target
    # <repo>` over the prompt's hitl-target, landing the review row in the
    # repository's own metrics - which verify() rightly refuses to read (item
    # 27's median rule is WHY hitl is redirected to scratch). The substitution
    # is now stated rather than left for inference.
    assert ("Where the workflow's steps write <repo> in a hitl.py command, "
            "use the hitl-target value below instead") in S.PROMPT


def test_a_windows_runner_path_survives_tokenising(tmp_path, monkeypatch):
    """POSIX lexing eats backslashes, so a Windows program path launched as
    one mangled word: run_tool returned 127 and the gate blamed the provider
    for a quoting bug."""
    monkeypatch.setattr(S.os, "name", "nt")
    win = "C:" + chr(92) + "Tools" + chr(92) + "agent.exe"
    assert S.split_runner(win + " exec") == [win, "exec"]
    assert S.split_runner('"' + win + '" exec') == [win, "exec"]


def test_an_unbalanced_quote_in_the_runner_is_a_problem_not_a_traceback(tmp_path):
    """It escaped main() as a traceback, so no row reached the record at all."""
    problems = S.live_provider('agent "unclosed', tmp_path / "d",
                               tmp_path / "o", cwd=tmp_path, secrets=set(),
                               run=lambda c, cwd=None, env=None: (0, ""))
    assert problems and "not a parseable command line" in problems[0]


def test_the_shell_wins_even_when_the_dotenv_key_differs_in_case(tmp_path, monkeypatch):
    """Windows resolves environment names case-insensitively, so a plain
    setdefault let the file's spelling shadow the shell's in the child -
    silently inverting the documented precedence."""
    monkeypatch.setattr(S.os, "name", "nt")
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    (tmp_path / ".env").write_text("Both_Var=file-loses\n", encoding="utf-8")
    monkeypatch.setenv("BOTH_VAR", "shell-wins")
    env = S.runner_env()
    assert env["BOTH_VAR"] == "shell-wins"
    assert "Both_Var" not in env, "a case-variant duplicate is the child's ambiguity"


def test_load_env_drops_inline_comments_and_the_export_prefix(tmp_path):
    """The shipped .env.example teaches `KEY=value  # what it is`. The comment
    used to be welded onto the value: authentication would fail AND scrub()
    would search for the padded string while the runner echoes the bare one."""
    f = tmp_path / ".env"
    f.write_text("PLAIN=value  # what it is\n"
                 "export EXPORTED=exported-value\n"
                 "QUOTED=\"keeps # inside\"\n"
                 "HASHLESS=no-comment-here\n", encoding="utf-8")
    assert S.load_env(f) == {"PLAIN": "value",
                             "EXPORTED": "exported-value",
                             "QUOTED": "keeps # inside",
                             "HASHLESS": "no-comment-here"}


def test_a_non_utf8_dotenv_does_not_kill_every_run(tmp_path, monkeypatch):
    """read_text raised UnicodeDecodeError - a ValueError, so the OSError
    guard missed it - and every run died before recording anything, including
    null-provider runs that never needed the file."""
    monkeypatch.setattr(S, "HARNESS", tmp_path)
    (tmp_path / ".env").write_bytes(b"PLAIN_VAR=caf\xe9-value\n")
    assert S.load_env(tmp_path / ".env")["PLAIN_VAR"].startswith("caf")
    assert isinstance(S.runner_env(), dict)


def test_an_inline_runner_credential_is_masked_in_the_record(tmp_path):
    """scrub() can only hide values it harvests from the environment. A key
    passed inline in --runner is by definition not one of them, and the record
    is durable - which is exactly what record()'s docstring promised."""
    p = S.record(tmp_path, f"agent --api-key {Z20}", "green", [],
                 tmp_path / "o", secrets=set())
    raw = p.read_text(encoding="utf-8")
    assert Z20 not in raw
    assert json.loads(raw)["runner"] == "agent --api-key <redacted>"
    assert S.mask_inline(f"agent --token={Z20}") == "agent --token=<redacted>"


def test_reset_scratch_clears_stale_artifacts_and_a_dead_session(tmp_path):
    """Last run's artifacts would vouch for this one, and a session left open
    by a dead run made every later --begin refuse: the recurring gate could
    not recover without a human."""
    out = tmp_path / "smoke"
    pre, pack = S.artifacts(out)
    pre.mkdir(parents=True)
    pack.mkdir(parents=True)
    (pre / "prepass.json").write_text("{}", encoding="utf-8")
    reviews, pending = H.paths(out)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text("{}\n", encoding="utf-8")
    pending.write_text("{}", encoding="utf-8")

    notes = S.reset_scratch(out)
    assert not pre.exists() and not pack.exists() and not pending.exists()
    assert reviews.exists(), "reviews.jsonl is history, never cleared"
    assert len(notes) == 3 and any("left open" in n for n in notes)


def test_verify_refuses_artifacts_older_than_the_run(tmp_path):
    """A stale artifact reads exactly like a fresh one - this repository's
    'empty reads exactly like clean' failure, one level up."""
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    problems = "\n".join(S.verify(out, diff, since="2000-01-01",
                                  started=time.time() + 100))
    assert "prepass.json predates this run" in problems
    assert "pack.md predates this run" in problems


def test_a_corrupt_review_row_is_a_problem_not_a_traceback(tmp_path):
    """One truncated line in the reused scratch log crashed the smoke before
    record() ran, so the run vanished from the append-only history."""
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    reviews, _ = H.paths(out)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"kind": "revi\n"x"\n')
    problems = "\n".join(S.verify(out, diff, since="2000-01-01"))
    assert "session log is corrupt" in problems


def test_a_renamed_file_is_judged_by_the_name_the_pack_carries(tmp_path):
    """verify read the diff's OLD side while pack seeds from the NEW one, so
    any rename made a correct pack look incomplete. One grammar now: the
    generator's."""
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    _green_artifacts(out, diff)
    diff.write_text("diff --git a/src/old_name.py b/src/new_name.py\n"
                    "similarity index 90%\nrename from src/old_name.py\n"
                    "rename to src/new_name.py\n"
                    "--- a/src/old_name.py\n+++ b/src/new_name.py\n@@ -1 +1 @@\n",
                    encoding="utf-8")
    (out / "pack" / "pack.md").write_text("## Seeds\n- hunk 1 seed(s) new_name.py\n",
                                          encoding="utf-8")
    assert S.verify(out, diff, since="2000-01-01") == []


def test_a_prepass_crash_at_exit_one_is_not_a_findings_run(tmp_path):
    """Exit 1 is BOTH 'findings' and Python's unhandled traceback; the report
    discriminates, because a run that found things still wrote one."""
    out = tmp_path / "smoke"

    def crashed(cmd, cwd=None, env=None):
        return (1, "Traceback ...") if "prepass.py" in " ".join(cmd) else (0, "ok")

    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               out, run=crashed)
    assert any("crash, not findings" in p for p in problems)


def test_a_finding_that_failed_to_record_is_a_named_problem(tmp_path):
    """Zero silent drops: the exit code was discarded, so a review row could
    look complete while a machine finding never reached it."""
    diags = [{"code": {"value": "rule-x"}, "severity": "ERROR",
              "location": {"path": "a.py", "range": {"start": {"line": 7}}}}]
    fake = FakeRun(tmp_path / "smoke", exits={"--finding": 2}, diagnostics=diags)
    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               tmp_path / "smoke", run=fake)
    assert any("recording pre-pass finding rule-x failed" in p for p in problems)


def test_the_null_providers_output_is_scrubbed_before_truncation(tmp_path):
    """The live path scrubbed at capture; this one clipped first, so a secret
    straddling the cut survived as a prefix that no later replace could match."""
    def noisy(cmd, cwd=None, env=None):
        return (2, "x" * 190 + Z20) if "pack.py" in " ".join(cmd) else (0, "ok")

    problems = S.null_provider(tmp_path / "t", tmp_path / "d.patch", "comp", ".",
                               tmp_path / "smoke", run=noisy, secrets={Z20})
    assert "z" not in "".join(problems)
    assert "<redacted>" in "".join(problems)


def test_the_record_is_written_with_lf_endings(tmp_path):
    """One durable history appended from Windows and from CI: hitl.append_row
    pins newline='\\n' for exactly this reason."""
    p = S.record(tmp_path, "agent", "green", [], tmp_path / "o", secrets=set())
    assert b"\r\n" not in p.read_bytes()


def test_a_relative_sop_citation_resolves_against_the_target(tmp_path):
    """The citation is read by an agent sitting AT the target, not from
    whatever directory the smoke was invoked in."""
    target = _target_with(tmp_path, "impact-review via `uv run --project x` - "
                                    "see `harness/workflows/impact-review.md`")
    sop = target / "harness" / "workflows"
    sop.mkdir(parents=True)
    (sop / "impact-review.md").write_text("#", encoding="utf-8")
    assert S.breadcrumbs(target) == []


# ── the seam: producer and consumer, composed ────────────────────────────────


def _real_shaped_prepass_doc(tmp_path, monkeypatch) -> dict:
    """The REAL prepass() document (its binaries stubbed absent), with the
    statuses a run that fired would carry. Welded to the generator on
    purpose: if prepass.json's shape moves, this moves with it - and the
    smoke reads that shape."""
    import tools.review.prepass as P
    monkeypatch.setattr(P, "binary", lambda name: (None, "not run: fixture"))
    doc = P.prepass(tmp_path, diff_text="diff --git a/x.py b/x.py\n"
                                        "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n")
    for t in doc["tools"]:
        t["status"] = "ok"
    return doc


class SpineRun(FakeRun):
    """Writes what the REAL CLIs write, in their real shapes, so the two
    halves of the smoke can be run against each other."""

    def __init__(self, out_dir: Path, doc: dict, seeded: str = "src/app.py"):
        super().__init__(out_dir)
        self.doc, self.seeded = doc, seeded

    def __call__(self, cmd, cwd=None, env=None):
        self.commands.append([str(c) for c in cmd])
        self.envs = getattr(self, "envs", [])
        self.envs.append(env)
        joined = " ".join(str(c) for c in cmd)
        pre, pack = S.artifacts(self.out_dir)
        reviews, pending = H.paths(self.out_dir)
        if "--begin" in cmd:
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text("{}", encoding="utf-8")
        elif "prepass.py" in joined:
            pre.mkdir(parents=True, exist_ok=True)
            (pre / "prepass.json").write_text(json.dumps(self.doc),
                                              encoding="utf-8")
        elif "pack.py" in joined:
            pack.mkdir(parents=True, exist_ok=True)
            (pack / "pack.md").write_text(
                f"## Seeds\n- hunk 1 seed(s) {self.seeded}\n", encoding="utf-8")
            (pack / "pack.json").write_text("{}", encoding="utf-8")
        elif "--end" in cmd:
            reviews.parent.mkdir(parents=True, exist_ok=True)
            with reviews.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"kind": "review",
                                     "started_at": "2999-01-01T00:00:00+00:00"})
                         + "\n")
            pending.unlink(missing_ok=True)
        return 0, "ok"


def test_the_null_provider_writes_exactly_what_verify_demands(tmp_path, monkeypatch):
    """THE SEAM, and the reason the defects above survived a green suite:
    every other test exercises ONE half against a hand-made fixture, so a
    layout the null provider never writes could still satisfy verify() - and
    did, for three separate artifacts. Compose them and the contract is real."""
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    diff.write_text("diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n",
                    encoding="utf-8")
    run = SpineRun(out, _real_shaped_prepass_doc(tmp_path, monkeypatch))
    started = time.time()

    assert S.null_provider(tmp_path / "t", diff, "comp", ".", out, run=run) == []
    assert S.verify(out, diff, since="2000-01-01", started=started) == [], \
        "what the producer wrote must satisfy the consumer, at the same paths"


def test_the_seam_still_fails_when_a_step_writes_nothing(tmp_path, monkeypatch):
    """The composition test would be worthless if it could not go red."""
    out, diff = tmp_path / "smoke", tmp_path / "d.patch"
    diff.write_text("diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n",
                    encoding="utf-8")
    run = SpineRun(out, _real_shaped_prepass_doc(tmp_path, monkeypatch))
    run.seeded = "somewhere/else.py"
    S.null_provider(tmp_path / "t", diff, "comp", ".", out, run=run)
    problems = "\n".join(S.verify(out, diff, since="2000-01-01"))
    assert "never names" in problems


# ── the recurring question: the record is read, not only written ─────────────
#
# The first cut wrote provider-smoke.jsonl and NOTHING read it - the only
# callers anywhere were the two tests above. The module docstring's claim, that
# "green at every milestone" is a file rather than a memory, was therefore not
# true as shipped: the row carried no milestone, and no code ever put a
# question to the file. Every test below pins one half of that closure.


def _record(target, rows):
    """Write a record file directly, so a test can state a HISTORY rather than
    having to produce one run at a time."""
    path = target / ".sdlc" / "metrics" / "provider-smoke.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            base = {"schema_version": 1, "kind": "provider-smoke",
                    "at": "2026-01-01T00:00:00+00:00",
                    "workflow": "impact-review", "runner": "r",
                    "verdict": "green", "problems": [], "artifacts": "o"}
            base.update(row)
            fh.write(json.dumps(base, sort_keys=True) + "\n")
    return path


def _rows(target):
    return [json.loads(x) for x in
            (target / ".sdlc" / "metrics" / "provider-smoke.jsonl")
            .read_text(encoding="utf-8").splitlines()]


def _run_main_recording(target, monkeypatch, extra):
    diff = target / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    monkeypatch.setattr(S, "breadcrumbs", lambda t: [])
    monkeypatch.setattr(S, "null_provider", lambda *a, **k: [])
    monkeypatch.setattr(S, "live_provider", lambda *a, **k: [])
    monkeypatch.setattr(S, "verify", lambda *a, **k: [])
    return S.main(["--target", str(target), "--diff", str(diff),
                   "--component", "c"] + extra)


def test_the_record_names_the_milestone_and_the_tier_it_was_run_at(tmp_path):
    path = S.record(tmp_path, "some runner", "green", [], tmp_path / "o",
                    milestone="M4", tier="agent")
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["milestone"] == "M4" and row["runner_tier"] == "agent"


def test_a_milestone_with_no_instance_is_unsatisfied_and_says_so(tmp_path):
    _record(tmp_path, [{"milestone": "M2"}])
    report, code = S.status(tmp_path, "M4")
    assert code == 1 and "M4" in report and "UNSATISFIED" in report


def test_a_milestone_with_a_green_instance_is_satisfied(tmp_path):
    _record(tmp_path, [{"milestone": "M2", "runner_tier": "null", "verdict": "green"}])
    report, code = S.status(tmp_path, "M2")
    assert code == 0 and "green at the null runner tier" in report


def test_a_red_instance_does_not_satisfy_a_milestone(tmp_path):
    _record(tmp_path, [{"milestone": "M2", "runner_tier": "null", "verdict": "red"}])
    assert S.status(tmp_path, "M2")[1] == 1


def test_a_green_superseded_by_a_red_at_the_same_tier_stops_counting(tmp_path):
    """The LAST instance at a tier is the state of that tier. A gate that could
    never be un-passed by re-running it is one nobody would learn from."""
    _record(tmp_path, [{"milestone": "M2", "runner_tier": "null", "verdict": "green"},
                       {"milestone": "M2", "runner_tier": "null", "verdict": "red"}])
    assert S.status(tmp_path, "M2")[1] == 1


def test_a_red_at_a_stronger_tier_leaves_the_weaker_tier_standing(tmp_path):
    """Per (milestone, TIER), because one verdict per milestone can only say
    one of two different things: "the agent evidence evaporated" and "the CLI
    tier broke" are not the same event."""
    _record(tmp_path, [{"milestone": "M2", "runner_tier": "null", "verdict": "green"},
                       {"milestone": "M2", "runner_tier": "agent", "verdict": "red"}])
    assert S.status(tmp_path, "M2")[1] == 0
    assert S.status(tmp_path, "M2", "agent")[1] == 1


def test_file_order_decides_the_latest_not_the_at_field(tmp_path):
    """`at` is a clock each row wrote about ITSELF; the file is the sequence.
    A machine with a skewed clock must not be able to resurrect a red."""
    _record(tmp_path, [
        {"milestone": "M2", "runner_tier": "null", "verdict": "red",
         "at": "2999-01-01T00:00:00+00:00"},
        {"milestone": "M2", "runner_tier": "null", "verdict": "green",
         "at": "2000-01-01T00:00:00+00:00"}])
    assert S.status(tmp_path, "M2")[1] == 0


# ── the tier: what the smoke can witness, and what it can only be told ───────


def test_a_runner_alone_records_the_contract_tier_not_the_agent_tier(
        tmp_path, monkeypatch):
    """The defect measuring found. `--runner` was the only signal separating a
    live agent from a fixed CLI sequence - but the REFERENCE RUNNER is also a
    `--runner`, and RUNNER_CONTRACT section 8 already says its green is "a
    green CONTRACT, never a green agent". Inferring the tier from the presence
    of a runner would have filed the reference runner as agent evidence."""
    _run_main_recording(tmp_path, monkeypatch, ["--runner", "some-runner"])
    assert _rows(tmp_path)[0]["runner_tier"] == "contract"


def test_the_agent_tier_is_an_attestation_and_only_the_flag_raises_it(
        tmp_path, monkeypatch):
    _run_main_recording(tmp_path, monkeypatch,
                        ["--runner", "some-runner", "--agent"])
    assert _rows(tmp_path)[0]["runner_tier"] == "agent"


def test_the_null_provider_records_the_null_tier(tmp_path, monkeypatch):
    _run_main_recording(tmp_path, monkeypatch, [])
    assert _rows(tmp_path)[0]["runner_tier"] == "null"


def test_a_contract_green_does_not_satisfy_a_requirement_for_an_agent(tmp_path):
    _record(tmp_path,
            [{"milestone": "M4", "runner_tier": "contract", "verdict": "green"}])
    assert S.status(tmp_path, "M4")[1] == 0
    assert S.status(tmp_path, "M4", "agent")[1] == 1


def test_a_stronger_tier_satisfies_a_weaker_requirement(tmp_path):
    _record(tmp_path, [{"milestone": "M4", "runner_tier": "agent", "verdict": "green"}])
    assert S.status(tmp_path, "M4", "contract")[1] == 0
    assert S.status(tmp_path, "M4", "null")[1] == 0


def test_the_runner_tier_is_named_in_full_because_tier_is_already_taken(
        tmp_path, monkeypatch):
    """The CLI tier is the ACCESS axis in this module - no MCP, no skill files
    - and it is spoken aloud in the prompt a runner is handed. Two meanings of
    one word in one file is item 36 vocabulary defect exactly, so the record
    key says which axis it is."""
    _run_main_recording(tmp_path, monkeypatch, [])
    row = _rows(tmp_path)[0]
    assert "runner_tier" in row and "tier" not in row


def test_the_runner_tiers_are_ordered_weakest_first():
    assert S.RUNNER_TIERS == ("null", "contract", "agent")
    assert S._rank("agent") > S._rank("contract") > S._rank("null")


# ── it must not be able to lie ───────────────────────────────────────────────


def test_a_pass_names_a_stronger_tier_that_is_red(tmp_path):
    """The exit code answers what was ASKED. A pass that leaves a stronger
    tier's red unsaid is a green with one hidden inside it."""
    _record(tmp_path, [{"milestone": "M4", "runner_tier": "null", "verdict": "green"},
                       {"milestone": "M4", "runner_tier": "agent", "verdict": "red"}])
    report, code = S.status(tmp_path, "M4")
    assert code == 0 and "agent runner tier is red" in report


def test_an_unattributed_instance_satisfies_no_milestone(tmp_path):
    """The field arrived after the first instances were recorded. Folding those
    into a milestone would invent evidence; dropping them would be the silent
    hole read_record refuses. They are a footnote, and they are shown."""
    _record(tmp_path, [{"runner_tier": "null", "verdict": "green"}])
    report, code = S.status(tmp_path, "M2")
    assert code == 1
    assert "no milestone recorded" in report and "satisfies nothing" in report


def test_an_unreadable_row_is_counted_not_skipped(tmp_path):
    """A history with holes in it reads exactly like a history that is simply
    short - this repository's oldest failure shape."""
    path = _record(tmp_path, [{"milestone": "M2"}])
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("{ not json\n")
    rows, unreadable = S.read_record(tmp_path)
    assert len(rows) == 1 and unreadable == 1
    assert "1 unreadable row" in S.status(tmp_path)[0]


def test_an_unreadable_row_is_not_a_traceback(tmp_path):
    """Item 44's recorded lesson from the other side: a required check that
    dies on a traceback names no run at all."""
    path = tmp_path / ".sdlc" / "metrics" / "provider-smoke.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("[]\nnot json\n7\n", encoding="utf-8")
    rows, unreadable = S.read_record(tmp_path)
    assert rows == [] and unreadable == 3


def test_another_kind_of_row_in_the_file_is_never_evidence(tmp_path):
    path = _record(tmp_path, [{"milestone": "M2"}])
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"kind": "review", "milestone": "M9"}) + "\n")
    assert "M9" not in S.status(tmp_path)[0]


def test_rows_written_before_the_two_fields_existed_are_still_read(tmp_path):
    """The M2 instance on the real install has neither field. Its tier is
    inferred from the one runner name this module itself chose, and anything
    else falls to `contract` - the weakest claim that fits a command whose
    tier nobody attested."""
    _record(tmp_path, [{"runner": S.NULL_RUNNER, "verdict": "green"},
                       {"runner": "a command from somewhere else",
                        "verdict": "green"}])
    state = S.latest_by_runner_tier(S.read_record(tmp_path)[0])
    assert set(state[""]) == {"null", "contract"}


def test_a_secret_in_the_milestone_label_never_reaches_the_record(
        tmp_path, monkeypatch):
    monkeypatch.setattr(S, "runner_env",
                        lambda: {"SOME_API_TOKEN": "abcdefghijklmnop"})
    path = S.record(tmp_path, "r", "green", [], tmp_path / "o",
                    milestone="M4-abcdefghijklmnop")
    assert "abcdefghijklmnop" not in path.read_text(encoding="utf-8")


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_status_asks_nothing_without_a_milestone_and_exits_zero(tmp_path,
                                                                capsys):
    _record(tmp_path, [{"milestone": "M2", "verdict": "red"}])
    assert S.main(["--target", str(tmp_path), "--status"]) == 0
    assert "M2" in capsys.readouterr().out


def test_status_needs_neither_a_diff_nor_a_component(tmp_path):
    assert S.main(["--target", str(tmp_path), "--status"]) == 0


def _tree(root):
    """Every file under `root`, with its bytes - so "wrote nothing" can mean
    what it says rather than "added no top-level entry"."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_status_writes_nothing(tmp_path):
    """Read-only by construction: the scratch reset, the metrics directory and
    the record itself are all side effects a report must not have.

    The record has to EXIST for that last clause to mean anything. The first
    version listed an empty `tmp_path` non-recursively and compared it with
    itself, so it could only notice a brand-new top-level entry - and a
    reviewer proved the gap by making `--status` truncate the record on every
    read: a report that wiped the recurring gate's entire milestone history
    left all 92 tests in this file green."""
    _record(tmp_path, [{"milestone": "M2", "runner_tier": "null",
                        "verdict": "green"},
                       {"milestone": "M4", "runner_tier": "agent",
                        "verdict": "red"}])
    before = _tree(tmp_path)
    assert before, "the fixture must leave something a write could damage"
    assert S.main(["--target", str(tmp_path), "--status"]) == 0
    assert _tree(tmp_path) == before


def test_status_writes_nothing_when_it_refuses_either(tmp_path):
    """The refusal paths take their own returns, so they get their own check."""
    _record(tmp_path, [{"milestone": "M2", "verdict": "green"}])
    before = _tree(tmp_path)
    assert S.main(["--target", str(tmp_path), "--status",
                   "--require", "agent"]) == 2
    assert S.main(["--target", str(tmp_path), "--status",
                   "--milestone", "M9"]) == 1
    assert _tree(tmp_path) == before


def test_a_smoke_run_still_needs_a_diff_and_a_component(tmp_path, capsys):
    assert S.main(["--target", str(tmp_path)]) == 2
    assert "--diff and --component are required" in capsys.readouterr().out


def test_require_without_a_milestone_is_refused_rather_than_ignored(tmp_path,
                                                                    capsys):
    """Accepting it and ignoring it would ship the very defect this addition
    closes: a flag that is read by nothing."""
    assert S.main(["--target", str(tmp_path), "--status",
                   "--require", "agent"]) == 2
    assert "needs a --milestone" in capsys.readouterr().out


def test_a_tier_that_is_not_a_tier_is_refused_and_the_real_ones_named(
        tmp_path, capsys):
    assert S.main(["--target", str(tmp_path), "--status", "--milestone", "M4",
                   "--require", "live"]) == 2
    out = capsys.readouterr().out
    assert "not a runner tier" in out and "contract" in out


def test_asking_about_a_milestone_with_no_instance_exits_one(tmp_path):
    _record(tmp_path, [{"milestone": "M2", "verdict": "green"}])
    assert S.main(["--target", str(tmp_path), "--status",
                   "--milestone", "M4"]) == 1


def test_a_run_that_names_no_milestone_says_it_satisfies_nothing(
        tmp_path, monkeypatch, capsys):
    _run_main_recording(tmp_path, monkeypatch, [])
    assert "UNATTRIBUTED" in capsys.readouterr().out


def test_a_run_that_names_one_does_not_say_that(tmp_path, monkeypatch, capsys):
    _run_main_recording(tmp_path, monkeypatch, ["--milestone", "M4"])
    out = capsys.readouterr().out
    assert "UNATTRIBUTED" not in out and "milestone: M4" in out


def test_a_milestone_label_is_free_text_and_no_list_of_them_is_shipped(
        tmp_path, monkeypatch):
    """Standing constraint: harness code must work in ANY repository, and an
    adopter's milestones are their own. A validated enum of this project's own
    milestone names is exactly what must not be hard-coded here."""
    _run_main_recording(tmp_path, monkeypatch, ["--milestone", "phase two"])
    assert _rows(tmp_path)[0]["milestone"] == "phase two"
    assert S.status(tmp_path, "phase two")[1] == 0


# ── the pre-pass vocabulary is imported, not restated ────────────────────────
#
# `verify()` hard-coded the pair ("gitleaks", "ast-grep") and the "ok" status.
# That is not a style point: a THIRD gate that never fired was invisible here
# while `prepass.exit_code()` returned 3 for the same document, so the
# recurring gate would have recorded a GREEN instance over a change its own
# pre-pass called unreviewed.


def _green_prepass_doc(extra=()):
    return {"schema_version": 1, "scope": {},
            "tools": [{"name": g, "status": PP.STATUS_OK, "findings": 0,
                       "detail": ""} for g in PP.GATES] + list(extra),
            "summary": {"total": 0}}


def _smoke_tree(tmp_path, doc):
    pre, pack = S.artifacts(tmp_path)
    pre.mkdir(parents=True, exist_ok=True)
    pack.mkdir(parents=True, exist_ok=True)
    (pre / "prepass.json").write_text(json.dumps(doc), encoding="utf-8")
    (pack / "pack.md").write_text("## Seeds\n- x.py\n", encoding="utf-8")
    (pack / "pack.json").write_text("{}", encoding="utf-8")
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",
                    encoding="utf-8")
    H.begin(tmp_path, component="c")
    H.end(tmp_path, "approved")
    return diff


def test_a_third_gate_that_never_fired_is_not_invisible(tmp_path):
    """The defect, measured before it was fixed: exit_code() said 3 for this
    exact document and verify() reported no problem at all."""
    doc = _green_prepass_doc([{"name": "a-third-gate", "status": "not-run",
                               "findings": 0, "detail": ""}])
    assert PP.exit_code(doc) == 3
    diff = _smoke_tree(tmp_path, doc)
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("a-third-gate" in p and "did not fire" in p for p in problems)


def test_a_gate_missing_from_the_report_entirely_is_its_own_finding(tmp_path):
    """Absent is not the same as failed, and the old `fired.get(tool) != "ok"`
    said both with one sentence."""
    doc = _green_prepass_doc()
    doc["tools"] = [t for t in doc["tools"] if t["name"] != PP.GATES[0]]
    diff = _smoke_tree(tmp_path, doc)
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any(PP.GATES[0] in p and "not in the report" in p for p in problems)


def test_the_smoke_follows_the_prepass_gate_list_rather_than_a_copy_of_it(
        tmp_path, monkeypatch):
    """Pins the IMPORT, not the values: a gate added to the pre-pass is
    checked here without anyone remembering to edit this file."""
    # The document is built BEFORE the patch, or it would contain the new gate
    # too and the test would assert nothing - which is exactly what the first
    # cut of it did.
    doc = _green_prepass_doc()
    monkeypatch.setattr(PP, "GATES", ("gitleaks", "ast-grep", "a-later-gate"))
    diff = _smoke_tree(tmp_path, doc)
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("a-later-gate" in p for p in problems)


def test_a_complete_green_prepass_is_still_green(tmp_path):
    """The negative control: the sharpening must not have made every run red."""
    diff = _smoke_tree(tmp_path, _green_prepass_doc())
    assert S.verify(tmp_path, diff, since="2000-01-01") == []


# ── review of this arc: five things the author missed ────────────────────────
#
# Every test below pins a defect found by reviewing `defd3fa..6e0df09` after it
# was pushed and green. Four of the five are the same shape: a rule this
# project had already written down, applied in one place and not in the one
# next to it.


def test_a_record_with_a_byte_that_is_not_utf8_is_a_finding_not_a_traceback(
        tmp_path):
    """`read_record`'s own docstring promises an unreadable line is "never
    raised on" - and the decode happened OUTSIDE the try, so one bad byte took
    the whole CLI down with a UnicodeDecodeError. The suite already had this
    lesson: `test_a_non_utf8_dotenv_does_not_kill_every_run`."""
    path = tmp_path / ".sdlc" / "metrics" / "provider-smoke.jsonl"
    path.parent.mkdir(parents=True)
    good = json.dumps({"kind": "provider-smoke", "milestone": "M4",
                       "runner_tier": "null", "verdict": "green"})
    path.write_bytes(good.encode("utf-8") + b"\n\xff\xfe binary\n")
    rows, unreadable = S.read_record(tmp_path)
    assert len(rows) == 1 and unreadable == 1


def test_a_runner_tier_that_is_not_a_tier_is_not_promoted_to_one(tmp_path):
    """A row claiming `runner_tier: "garbage"` was laundered into `contract`
    and SATISFIED a `--require contract` gate. Absent and invalid are
    different findings - the very distinction `prepass.missing_gates` versus
    `prepass.unfired` was written to make one commit later. The legacy path is
    for rows with NO field, not for rows with a wrong one."""
    _record(tmp_path, [{"milestone": "M4", "runner_tier": "garbage-not-a-tier",
                        "verdict": "green"}])
    rows, unreadable = S.read_record(tmp_path)
    assert rows == [] and unreadable == 1
    assert S.status(tmp_path, "M4", "contract")[1] != 0


def test_a_record_that_cannot_be_counted_cannot_evidence_a_milestone(tmp_path):
    """It printed "this history is incomplete and cannot be counted" and then
    counted it and exited 0. Exit 3 is this project's own word for could-not-
    look, and `prepass.exit_code` outranks findings with it for exactly this
    reason: the change is not reviewed while a gate never ran."""
    path = _record(tmp_path, [{"milestone": "M4", "runner_tier": "null",
                               "verdict": "green"}])
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("CORRUPTED\n")
    report, code = S.status(tmp_path, "M4")
    assert code == 3 and "cannot be counted" in report


def test_a_plain_report_over_a_damaged_record_still_reports(tmp_path):
    """The negative control: with no milestone nothing was ASKED, so the view
    is still a view. Only the question inherits the doubt."""
    path = _record(tmp_path, [{"milestone": "M4", "verdict": "green"}])
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("CORRUPTED\n")
    assert S.status(tmp_path)[1] == 0


def test_attesting_an_agent_without_a_runner_is_refused(tmp_path, capsys):
    """`--agent` says "this command has a model in it" and there was no
    command: it was silently dropped and the run recorded `null`. That is a
    flag read by nothing, which this same function refuses two lines away for
    `--require` without `--milestone`."""
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    assert S.main(["--target", str(tmp_path), "--diff", str(diff),
                   "--component", "c", "--agent"]) == 2
    assert "--agent" in capsys.readouterr().out


def test_record_refuses_a_runner_tier_that_is_not_one(tmp_path):
    """`record()`'s docstring says every recorded string passes through
    safe(); `runner_tier` did not, on the stated ground that it comes from a
    closed set - which the signature did not enforce. Enforcing it makes the
    claim true and closes the laundering above at its source."""
    with pytest.raises(ValueError):
        S.record(tmp_path, "r", "green", [], tmp_path / "o",
                 tier="not-a-tier")


def test_a_prepass_report_whose_tools_field_is_null_is_a_named_problem(
        tmp_path):
    """Item 40 recorded that a clean osv-scanner scan emits `"results": null`
    and crashed the field run. `verify()` reads a prepass.json written by an
    arbitrary RUNNER, so the same shape arrives here - and null is not a
    missing key, so `.get("tools", [])` returns it and the smoke tracebacks
    instead of naming the report as the problem."""
    doc = {"schema_version": 1, "scope": {}, "tools": None,
           "summary": {"total": 0}}
    diff = _smoke_tree(tmp_path, doc)
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("no readable tools[] list" in p for p in problems)


# ── the review's fixes ───────────────────────────────────────────────────────


def test_require_on_a_run_is_refused_rather_than_dropped(tmp_path, capsys):
    """`--require` was read only under `--status`, so on a run it was accepted
    and referenced by nothing — a flag read by nothing, which this same
    function refuses one branch along for `--agent` without `--runner`. An
    operator wiring the CI gate as one command believed the agent tier had
    been asserted."""
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    assert S.main(["--target", str(tmp_path), "--diff", str(diff),
                   "--component", "c", "--milestone", "M4",
                   "--require", "agent"]) == 2
    assert "--require" in capsys.readouterr().out


def test_a_runner_tier_of_null_is_corrupt_not_legacy(tmp_path):
    """The corruption check was `tier is not None and tier not in TIERS`, so
    an explicit JSON null took the legacy path and was promoted to `contract`
    — the outcome the comment beside it says must not happen. The string
    spelling was refused and the null spelling was not, so two spellings of
    one corruption gave opposite answers."""
    _record(tmp_path, [{"milestone": "M4", "runner_tier": None,
                        "runner": "some-command", "verdict": "green"}])
    rows, unreadable = S.read_record(tmp_path)
    assert rows == [] and unreadable == 1
    assert S.status(tmp_path, "M4", "contract")[1] != 0


def test_a_row_that_predates_the_field_is_still_legacy(tmp_path):
    """The negative control: ABSENT is what the legacy path is for, and it
    must keep working — the real M2 instance has no such field."""
    _record(tmp_path, [{"milestone": "", "runner": S.NULL_RUNNER,
                        "verdict": "green"}])
    rows, unreadable = S.read_record(tmp_path)
    assert len(rows) == 1 and unreadable == 0


def test_a_change_with_every_file_excluded_is_not_a_green_smoke(tmp_path):
    """verify() adopted two of the pre-pass's three rules. A report saying
    `review_exclude` folded out every changed file made `prepass.exit_code`
    return 3 and `verify()` report nothing at all, so the recurring gate
    recorded green over a change nobody reviewed."""
    doc = _green_prepass_doc()
    doc["scope"] = {"empty_after_exclude": True}
    diff = _smoke_tree(tmp_path, doc)
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("folded out" in p for p in problems)


@pytest.mark.parametrize("document", ["[]", '"oops"', "3"])
def test_a_prepass_report_that_is_not_an_object_is_a_problem(tmp_path, document):
    """`_read_json` returns any JSON value and only `is None` was checked, so
    a runner writing `[]` raised AttributeError out of verify() and out of
    main() — and the run appended NO row, which is the one outcome the
    recurring record must never have."""
    pre, pack = S.artifacts(tmp_path)
    pre.mkdir(parents=True)
    pack.mkdir(parents=True)
    (pre / "prepass.json").write_text(document, encoding="utf-8")
    (pack / "pack.md").write_text("## Seeds\n- x.py\n", encoding="utf-8")
    (pack / "pack.json").write_text("{}", encoding="utf-8")
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",
                    encoding="utf-8")
    H.begin(tmp_path, component="c")
    H.end(tmp_path, "approved")
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("no readable prepass.json object" in p for p in problems)


def test_a_session_row_that_is_not_an_object_is_a_problem_not_a_traceback(
        tmp_path):
    """`r.get("started_at", "")` was asked before `isinstance(r, dict)`."""
    reviews, _ = H.paths(tmp_path)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text('"just a string"\n', encoding="utf-8")
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    problems = S.verify(tmp_path, diff, since="2000-01-01")
    assert any("not a session record" in p for p in problems)


def test_a_crash_still_leaves_the_attempt_in_the_record(tmp_path, monkeypatch,
                                                        capsys):
    """A run that died left NO row, so `--status` could not tell "never tried"
    from "tried and died" — the distinction the unreadable-row counting exists
    to preserve. The failure is a red problem now, and 3 rather than 1,
    because a run that died did not judge the target."""
    diff = tmp_path / "d.patch"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    monkeypatch.setattr(S, "breadcrumbs", lambda t: [])
    monkeypatch.setattr(S, "null_provider",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("a disk error, say")))
    code = S.main(["--target", str(tmp_path), "--diff", str(diff),
                   "--component", "c", "--milestone", "M4"])
    assert code == 3
    rows, _ = S.read_record(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "red"
    assert any("a disk error" in p for p in rows[0]["problems"])


# ── one spelling of the record's path ────────────────────────────────────────


def test_the_writer_and_the_reader_cannot_disagree_about_the_path(tmp_path,
                                                                  monkeypatch):
    """`"provider-smoke.jsonl"` was a bare literal in `record()` and again in
    `read_record()`. Renaming one would leave the reader looking at a file
    nobody writes — and an absent file makes `--status` answer "no green
    instance recorded", which is exactly what a silently broken reader looks
    like. `hitl` keeps its basenames as constants for the same reason.

    Moving the constant must move BOTH, which is the property a shared literal
    does not have and a shared constant does.
    """
    monkeypatch.setattr(S, "RECORD", "somewhere-else.jsonl")
    written = S.record(tmp_path, S.NULL_RUNNER, "green", [], tmp_path,
                       milestone="M4", tier="null")
    assert written.name == "somewhere-else.jsonl"
    rows, unreadable = S.read_record(tmp_path)
    assert len(rows) == 1 and unreadable == 0


def test_record_path_is_the_one_place_the_name_is_spelled():
    """The mechanism, so the test above cannot pass on a coincidence: both
    call sites must go through `record_path`, not merely happen to agree."""
    src = (ROOT / "tools" / "provider_smoke.py").read_text(encoding="utf-8")
    assert src.count('"provider-smoke.jsonl"') == 1, (
        "the basename is spelled more than once again")
    assert "RECORD = " in src and src.count("record_path(") >= 3
