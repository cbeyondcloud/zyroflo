"""Contract tests for the two things verified only by hand until now.

1. `profile.yml` -- the ONE file that makes the generic harness fit a workspace. It is a
   template with `@@TOKEN@@` slots filled by `render_profile()`. A token added to the
   template but not to the renderer ships a literal `@@TOKEN@@` into a user's repo and
   breaks their YAML; nothing caught that class before.
2. `.gitignore` -- the merged union that keeps `.env` out of a repo with a remote. It was
   proven correct once, interactively. This re-proves it on every run.
3. The refusals `main()` makes before it writes anything. A refusal and a traceback are
   only distinguishable as a PROCESS (item 96): in-process both raise, and both render as
   a failing test. These three were verified by hand and by nothing else.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    """Import sdlc-init.py despite the hyphen (not a valid module name)."""
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def install_into(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    # `--no-graph`: these tests are about the template/renderer contract, and an install at
    # a repository root now also builds that repository's graph (item 80). Letting it fire
    # would make three contract tests depend on uv, on the grammars, and on the graph layer
    # staying green — a failure over there would present as a template defect over here.
    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(target),
                           "--no-graph"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"installer failed:\n{proc.stdout}\n{proc.stderr}"
    return target / ".sdlc" / "profile.yml"


def rerun_install(target: Path) -> str:
    """Re-run with the SAME flags and hand back what the adopter is TOLD.

    `install_into` returns the path; the drift check's whole output is a sentence
    on stdout, and a test that only looks at the filesystem cannot tell a correct
    refusal from a silent skip."""
    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(target),
                           "--no-graph"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"re-install failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout + proc.stderr


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    return tmp_path


# ── the template/renderer contract ─────────────────────────────────────────────
def test_every_template_token_is_wired_to_the_renderer():
    """The failure this prevents: add `@@FOO@@` to the template, forget the renderer,
    ship a literal placeholder into someone's profile and break their YAML parse."""
    mod = load_installer()
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    in_template = set(re.findall(r"@@[A-Z_]+@@", template))

    rendered = mod.render_profile(
        {"stack": "python", "test_command": "pytest -q", "coverage_command": "pytest --cov",
         "coverage_threshold": 90, "lint_command": "", "build_command": "",
         "deploy_command": "", "vcs": {}},
        ["claude"],
    )
    unfilled = set(re.findall(r"@@[A-Z_]+@@", rendered))
    assert not unfilled, f"template tokens with no renderer mapping: {sorted(unfilled)}"
    assert in_template, "sanity: the template should still use tokens"


def test_rendered_profile_is_valid_yaml_with_v2_shape():
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "java", "test_command": 'mvn -q test', "coverage_command": "",
         "coverage_threshold": 80, "lint_command": "", "build_command": "mvn -q package",
         "deploy_command": "", "vcs": {}},
        ["claude", "cursor"],
    ))
    assert d["schema_version"] == 2
    assert d["scope"] == "repo"
    assert d["gate_profile"] == "solo"
    assert d["signing"] == "none"
    assert d["review"]["block_on"] == ["critical", "high"]
    assert "components" not in d, "a single repo must not need a components list"


def test_command_values_containing_quotes_stay_valid_yaml():
    """`dotnet test --collect:"XPlat Code Coverage"` must not break the parse."""
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "dotnet", "test_command": 'dotnet test --collect:"XPlat Code Coverage"',
         "coverage_command": "", "coverage_threshold": 80, "lint_command": "",
         "build_command": "", "deploy_command": "", "vcs": {}},
        ["claude"],
    ))
    assert d["test_command"] == 'dotnet test --collect:"XPlat Code Coverage"'


def test_vcs_block_round_trips_deny_raw_git():
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "python", "test_command": "pytest", "coverage_command": "",
         "coverage_threshold": 90, "lint_command": "", "build_command": "", "deploy_command": "",
         "vcs": {"commit_command": './checkin.sh "{message}"', "push_command": "",
                 "deny_raw_git": True, "notes": "open-core split"}},
        ["claude"],
    ))
    assert d["vcs"]["deny_raw_git"] is True
    assert "{message}" in d["vcs"]["commit_command"]


def test_solo_phase_list_matches_the_lifecycle_spec():
    """LIFECYCLE.md and the shipped default must not drift apart."""
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "python", "test_command": "pytest", "coverage_command": "",
         "coverage_threshold": 90, "lint_command": "", "build_command": "",
         "deploy_command": "", "vcs": {}}, ["claude"]))
    expected = ["intake", "requirements", "design", "implementation",
                "test", "review", "triage", "closeout"]
    assert d["phases"] == expected

    spec_text = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert ", ".join(expected) in spec_text.replace("`", ""), \
        "the spec's stated solo default must match what the installer writes"


# ── end-to-end install ─────────────────────────────────────────────────────────
def test_fresh_install_emits_a_parseable_v2_profile(py_repo: Path):
    profile = install_into(py_repo)
    text = profile.read_text(encoding="utf-8")
    assert "@@" not in text, "unrendered placeholder reached a user's repo"
    d = yaml.safe_load(text)
    assert d["schema_version"] == 2 and len(d["phases"]) == 8
    assert d["test_command"], "stack detection should have found a test command"


def test_reinstall_preserves_a_hand_edited_profile(py_repo: Path):
    """The installer's core promise: your edits survive an upgrade."""
    profile = install_into(py_repo)
    edited = profile.read_text(encoding="utf-8").replace(
        "coverage_scope: changed", "coverage_scope: whole   # mine")
    profile.write_text(edited, encoding="utf-8")

    install_into(py_repo)
    assert "coverage_scope: whole" in profile.read_text(encoding="utf-8")
    assert (profile.parent / "profile.yml.new").exists(), \
        "the new version must be staged alongside, not dropped"


#: Any ISO calendar date in the rendered profile. The installer used to stamp
#: `# Generated: <today>` into the file whose bytes it later compares against a
#: fresh render, so the ONLY difference a re-run on a later day could see was one
#: the installer had written itself.
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_the_rendered_profile_carries_no_per_run_value(py_repo: Path):
    """Nothing in the profile may differ between two renders of the same version.

    The profile is `preserve`-compared byte-for-byte on every re-run, so any value
    that changes on its own — a date, a clock, a host — is read as the adopter's
    edit. Measured when this test was written: `date.today()` was the installer's
    only such value, and nothing in the tree read the line it wrote."""
    text = install_into(py_repo).read_text(encoding="utf-8")
    assert "# Generated:" not in text, \
        "the installer stamped its own date into the file it later compares"
    assert not ISO_DATE.search(text), \
        f"a per-run date reached the profile: {ISO_DATE.search(text).group()}"


def test_a_reinstall_a_calendar_day_later_is_not_reported_as_your_edit(py_repo: Path):
    """Re-running on a later day must report the profile unchanged, and the control
    in the same test proves a REAL edit is still preserved and still reported.

    The apparent date is moved by ageing every calendar date in the INSTALLED file
    (there is no injectable clock in a spawned installer) — which is also how the
    defect was first reproduced. After the fix there is no date left to age, and
    the first assertion is what says so."""
    profile = install_into(py_repo)
    text = profile.read_text(encoding="utf-8")
    aged = ISO_DATE.sub("2000-01-01", text)
    assert aged == text, "the installed profile still carries a date of its own"
    profile.write_text(aged, encoding="utf-8")

    out = rerun_install(py_repo)
    assert not (profile.parent / "profile.yml.new").exists(), \
        "a day passing is not an edit: the installer wrote that difference itself"
    assert "preserved" not in out, f"the re-run called an untouched profile edited:\n{out}"

    # CONTROL: the drift check must still fire for a line a human really changed.
    profile.write_text(text.replace("schema_version: 2", "schema_version: 2  # mine"),
                       encoding="utf-8")
    out = rerun_install(py_repo)
    assert (profile.parent / "profile.yml.new").is_file(), \
        "a real edit must still be staged alongside, not clobbered"
    assert "preserved" in out, f"a real edit must still be reported:\n{out}"
    assert "# mine" in profile.read_text(encoding="utf-8")


def test_bundled_single_file_installer_emits_the_same_schema(tmp_path: Path, py_repo: Path):
    """`--bundle` embeds the templates; it must not fall behind the template dir."""
    bundle = tmp_path / "bundled.py"
    subprocess.run([sys.executable, str(INSTALLER), "--bundle", str(bundle)],
                   check=True, capture_output=True)
    target = tmp_path / "viabundle"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    (target / "pyproject.toml").write_text('[project]\nname="y"\n', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(bundle), "--target", str(target)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    d = yaml.safe_load((target / ".sdlc" / "profile.yml").read_text(encoding="utf-8"))
    assert d["schema_version"] == 2 and d["gate_profile"] == "solo"


# ── .gitignore ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    ".env", "credentials.json", "token.json",
    ".tmp/scratch.json",
    # Per-platform since item 74. Both spellings are asserted: the old flat path proves the
    # rule did not narrow, the nested one proves it reaches the layout actually in use.
    "third_party/bin/rg.exe", "third_party/bin/windows-x86_64/rg.exe",
    "third_party/bin/linux-x86_64/rg", "third_party/bin/darwin-aarch64/fd",
    ".pytest_cache/x", ".coverage", "coverage.xml", "htmlcov/index.html",
    ".venv/pyvenv.cfg", "__pycache__/m.pyc", "mod.pyc",
    ".claude/settings.local.json",
])
def test_sensitive_and_generated_paths_stay_ignored(path: str):
    """`.env` holds a live Neo4j password. A secret in git history outlives the commit
    that added it, so this is asserted per path rather than trusted to review."""
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path],
                          capture_output=True)
    assert proc.returncode == 0, f"{path} is NOT ignored"


@pytest.mark.parametrize("path", [
    "sdlc-init.py", "README.md", "third_party/manifest.json",
    "tools/graph/core/model.py", "sdlc/LIFECYCLE.md", "sdlc_templates/profile.yml",
])
def test_product_files_are_not_ignored(path: str):
    """The mirror image: an over-broad rule that silently stops tracking real code."""
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path],
                          capture_output=True)
    assert proc.returncode == 1, f"{path} IS ignored but must be tracked"


# ── the spec must not contradict itself ────────────────────────────────────────
def test_the_review_loop_exits_on_block_on_not_on_the_score():
    """SKILL.md told the agent to exit at `score >= review_threshold` while profile.yml
    called that score "REPORTED for continuity — not the gate" and named `block_on` as THE
    exit condition. Two shipped files, opposite instructions, and the agent reads SKILL.md."""
    skill = (ROOT / "sdlc_templates" / "SKILL.md").read_text(encoding="utf-8")
    assert "block_on" in skill, "the categorical exit condition must appear in the SOP"
    assert "score ≥ `review.review_threshold`** → exit" not in skill, \
        "the score must not be stated as the loop's exit condition"

    mod = load_installer()
    profile = yaml.safe_load(mod.render_profile(
        {"stack": "python", "test_command": "pytest", "coverage_command": "",
         "coverage_threshold": 90, "lint_command": "", "build_command": "",
         "deploy_command": "", "vcs": {}}, ["claude"]))
    assert profile["review"]["block_on"] == ["critical", "high"]


def test_readme_does_not_advertise_the_old_score_gate():
    """Third file in the same drift class. The exit condition appeared in SKILL.md,
    profile.yml and README.md; two were fixed and this one still told readers the loop
    runs until >=9.5, which is the behavior the categorical gate replaced."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert ">=9.5" not in readme and "≥9.5" not in readme, \
        "README still states a numeric score as the review exit condition"


# ── the refusals main() makes before it writes anything ────────────────────────
@pytest.mark.parametrize("flags, sentence", [
    (["--tools", "bogus"], "unknown tool(s): bogus"),
    (["--estate"], "--estate needs --workspace"),
    (["--from-snapshot"], "--from-snapshot only means something with --estate"),
])
def test_main_refuses_a_bad_flag_combination_as_a_process(tmp_path: Path, flags, sentence):
    """Exit code AND the sentence AND an untouched target, from a spawned process.

    Asserting the SENTENCE, not just the exit code, is what keeps the second and
    third cases apart: both guards sit three lines from each other and both exit 1,
    so a test that only read the code would pass whichever one fired."""
    target = tmp_path / "repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target), "--no-graph"] + flags,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a refusal must exit 1, got {proc.returncode}:\n{out}"
    assert sentence in out, f"the refusal must say why:\n{out}"
    assert "Traceback" not in out, f"a refusal must not be a traceback:\n{out}"
    assert not (target / ".sdlc").exists(), "a refused install must write nothing"


def test_the_same_spawn_accepts_the_flags_it_refuses_when_they_are_complete(tmp_path: Path):
    """Positive control: the parametrised refusals above must fail because the flags
    are wrong, not because a spawn of this installer never exits 0 from tmp_path."""
    target = tmp_path / "repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    (target / "pyproject.toml").write_text('[project]\nname="z"\n', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target), "--no-graph",
         "--tools", "claude"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / ".sdlc" / "profile.yml").is_file()


def test_requirements_txt_covers_every_declared_dependency():
    """The generated file had drifted to 2 of 9 declared dependencies, so anyone without uv
    who trusted it got an environment missing neo4j, pyyaml, pydantic and all of tree-sitter
    -- a broken install that looks like a code bug. uv.lock is canonical; this proves the
    compat export is not silently stale."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {re.split(r"[><=!~\[]", d)[0].strip().lower().replace("_", "-")
                for d in pyproject["project"]["dependencies"]}

    req = (ROOT / "tools" / "requirements.txt").read_text(encoding="utf-8")
    exported = {re.split(r"[><=!~]", line)[0].strip().lower().replace("_", "-")
                for line in req.splitlines()
                if line.strip() and not line.lstrip().startswith("#")}

    missing = declared - exported
    assert not missing, (
        f"tools/requirements.txt is missing {sorted(missing)}. Regenerate it with the "
        f"command in its header rather than adding lines by hand."
    )


def test_the_installed_profile_has_no_dangling_path_references():
    """`sdlc/LIFECYCLE.md` lives in the harness repo. The template is installed into someone
    else's repo, where that path does not exist — so the comment pointed a reader at
    nothing. Anything the user is told to open must be reachable from where they are."""
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    # `.sdlc/...` is the INSTALLED location and IS reachable from where the reader
    # is; the bare `sdlc/...` spellings are harness-repo paths that are not. Item 35
    # began shipping `.sdlc/policy.yml` into the adopter, which turned the plain
    # substring ban into a false positive on the one policy path a reader can open.
    reachable = template.replace(".sdlc/", "<installed>/")
    for ref in ("sdlc/LIFECYCLE.md", "sdlc/policy.yml", "tools/sdlc/"):
        assert ref not in reachable, f"template points at {ref}, absent from an installed repo"


def test_neo4j_credentials_have_exactly_one_source():
    """docker-compose defaulted to `harness-local-dev`, .env.example said `change-me`, and
    the store defaulted to `""`. `compose up` then succeeded while every query failed to
    authenticate against a database that was visibly running."""
    compose = (ROOT / "docker-compose.neo4j.yml").read_text(encoding="utf-8")
    assert "harness-local-dev" not in compose, "a default password here disagrees with .env"
    assert "NEO4J_PASSWORD:?" in compose, "compose must refuse to start without .env"


def test_the_store_reads_dotenv_like_the_tests_always_did():
    """Only the test suite called load_dotenv(), so the tests passed against a developer's
    real .env while any production caller silently got an empty password."""
    src = (ROOT / "tools" / "graph" / "core" / "store" / "neo4j_store.py").read_text(encoding="utf-8")
    assert "load_dotenv" in src
    assert "_load_env()" in src


def test_no_secret_is_currently_tracked():
    """Belt and braces: rules can be right while a file was committed before them."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    tracked = {f.strip() for f in out if f.strip()}
    for bad in (".env", "credentials.json", "token.json", ".claude/settings.local.json"):
        assert bad not in tracked, f"{bad} is tracked in git"
    assert not [f for f in tracked if f.startswith("third_party/bin/")], \
        "vendored binaries must be fetched and verified, never committed"
