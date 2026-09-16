"""Real-binary coverage for the vendored tools whose consumers are production code.

A vendored binary earns its pin by having a consumer (item 113). Two of them had
one and were still spawned by **no test at all** — measured with a spawn tracer over
the 249 tests in the eight files that own the release and SCA surface, with every
binary present on disk: `gh` 0 spawns, `osv-scanner` 0 spawns. The repository's own
census agreed and was the second witness: `third_party.SPAWNED_BY_TESTS` recorded
`fd`, `rg`, `gitleaks` and `ast-grep`, and named neither of these. It now records
`osv-scanner` at 3, because of this file.

Two tools spawned by one test is not the same defect. `test_artifact_crossings.py`
spawns gitleaks and ast-grep through `prepass.main` and asserts that the document
carries a `tools` key — that is a **weld**, and it is deliberately left one: its
question is whether two modules spell one filename the same way, and it must not
start failing because a scanner changed its output. Tool coverage is a different
question and lives here, so that each failure names one cause.

**What can be asked of these tools with no network, measured rather than assumed.**

`osv-scanner` — nearly everything that matters. MEASURED: the production argv over a
tree with no package manifests, and that same argv with the scanner's own `--offline`
flag added, give byte-identical stdout and the same exit status. INFERRED from that,
together with the tool's own stderr saying it found no package sources: the first run
asked the network nothing, because there was nothing to look up. Identity of output
is the weaker half of that argument on its own — a request whose answer changed
nothing would look the same — so it is marked rather than asserted. That path carries
the behaviour this repository has already paid to learn — a clean scan reports
`"results": null`, not `[]` — and `--offline` over a tree that DOES carry a manifest
gives a live positive control for the one regex standing between a scanned component
and a gate that reports it as unscanned.

`gh` — only its surface. Every call `release.py` makes is an authenticated request to
GitHub, so the reply path cannot be exercised here at all and is RECORDED as uncovered
below rather than faked. What a real spawn can still settle is that the pinned binary
accepts the flag vocabulary that code passes it, which nothing checked. There are SIX
such calls, and the sixth is why `_gh_call_sites` reads an AST rather than the source
text: see its docstring.

**No test here skips.** `SDLC_FORBID_SKIPS=1` turns any skip into exit 1, and
`third_party/bin/` is gitignored, so a binary is present only after a fetch. CI's fetch
step installs `fd rg gitleaks ast-grep osv-scanner`: the scanner is fetched FOR this
file, and `gh` is not fetched at all, because every reply path `release.py` has is an
authenticated request and a spawn could only re-read a version string.

**So be plain about what this file is worth where it actually runs**, which is two
different answers for the two tools. MEASURED under `SDLC_FORBID_SKIPS=1`:

- **With neither binary on disk** — the state before CI was told to fetch the scanner —
  11 pass, **57%** of this file runs, and a `subprocess` tracer over the whole run records
  **no vendored binary at all**. The tracer is the witness because it DISCRIMINATES: the
  same tracer answers `osv-scanner: 8` once the scanner is installed. *`_spawn` is never
  entered* used to be offered here and is no longer evidence of anything — it is true in
  BOTH states, because the scanner's spawn moved to `prepass.run_tool` when this file
  stopped re-typing the production argv.
  Eight of the eleven then assert a single fact, that the production resolver explains
  an absence rather than returning quietly, six of them about the identical tool with
  identical arguments. That is worth keeping and it is NOT tool coverage.
- **With `osv-scanner` fetched, which is what CI now does** — 11 pass, 89% of this
  file runs, and **3 tests spawn the real scanner 8 times**. That is the coverage this
  module exists for, and it is the whole reason the fetch list grew.
- **`gh` is not fetched, so on the gate of record it is spawned zero times** and its
  tests there assert only the resolver's explained absence. **Where somebody HAS
  installed it, they are real spawns** — `len(GH_SURFACE) + 1` of them, one per
  recorded subcommand plus the version probe, which is written as the expression rather
  than as a number so that adding a call site cannot leave a stale count behind. The
  surface itself is pinned by reading `release.py`'s own AST and needs no binary at all,
  which is why not fetching `gh` costs no coverage that the gate can see.

This is the shape already established by
`test_third_party.py::test_the_tools_ci_fetches_resolve_when_the_suite_requires_them`.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from tools.review import prepass, sca
from tools.sdlc import release

ROOT = Path(__file__).resolve().parents[2]


def _resolved(name: str) -> Path | None:
    """The production resolver's answer, and its refusal must be a REASON.

    A resolver that returned `None` with an empty explanation would leave every
    caller unable to tell "not installed" from "looked and found nothing", which
    is the distinction this whole module is about.
    """
    exe, why = prepass.binary(name)
    if exe is None:
        assert why.strip(), (
            f"{name} did not resolve and said nothing about why; an unexplained "
            f"absence is indistinguishable from a tool that ran and found nothing")
        return None
    assert Path(exe).exists(), f"{name} resolved to {Path(exe).name}, which is not there"
    return Path(exe)


def _pinned_here(name: str) -> bool:
    """Is this tool pinned for the platform the tests are running on?"""
    from tools import third_party as tp

    manifest = json.loads((ROOT / "third_party" / "manifest.json")
                          .read_text(encoding="utf-8"))
    asset = manifest["tools"][name]["assets"].get(tp.current_platform()) or {}
    return bool(asset.get("url"))


def _spawn(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], cwd=None if cwd is None else str(cwd),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


# ── the supply-chain scanner: spawned, and its output read by its real reader ──


def test_the_scanner_reports_a_clean_tree_as_a_null_result_not_an_empty_one(
        tmp_path, monkeypatch):
    """Measured live, and the distinction is the point: over a tree with no package
    manifests the scanner answers `"results": null` and says `No package sources
    found`. Nothing was read, which is not the same fact as *read and clean*, and
    `sca.run` turns exactly that difference into `nothing-scanned` rather than `ok`.

    **The production argv is ASKED FOR, not re-typed.** `sca._invoke` is called, and
    a spy over `prepass.run_tool` records the command and the environment it was
    handed. A copy typed here would drift, and the first thing the copy dropped was
    `READ_ONLY_ENV` — which is not decoration: this scanner once WROTE INTO THE
    REPOSITORY IT AUDITS (item 40), and that environment is the prevention. So it is
    asserted, by identity with the shipped constant.

    MEASURED: the recorded argv and the same argv with the tool's own `--offline`
    flag give identical stdout and the same exit status. INFERRED (rule 23): the
    first run therefore made no network request. Identity alone does not carry that
    — a request whose answer changed nothing looks the same — and the load-bearing
    half is the scanner's own report below that it found no package sources, so
    there was nothing to look up.
    """
    assert _pinned_here(sca.BINARY), f"{sca.BINARY} is consumed by sca.py but not pinned here"
    exe = _resolved(sca.BINARY)
    if exe is None:
        return
    seen: list[tuple[list[str], dict | None]] = []
    real = prepass.run_tool

    def spy(cmd, **kw):
        seen.append((list(cmd), kw.get("env")))
        return real(cmd, **kw)

    monkeypatch.setattr(prepass, "run_tool", spy)
    where = tmp_path / "clean"
    where.mkdir()
    (where / "a.py").write_text("x = 1\n", encoding="utf-8")
    code, out, err = sca._invoke(exe, where)
    monkeypatch.undo()

    assert len(seen) == 1, f"sca._invoke ran the tool {len(seen)} times, not once"
    argv, env = seen[0]
    assert env is sca.READ_ONLY_ENV and env, (
        f"the production scan was handed env={env!r}; the read-only environment is "
        f"what stops this scanner writing into the tree it is auditing")
    assert argv[-1] == str(where) and "--offline" not in argv, argv
    offline_code, offline_out, _ = prepass.run_tool(
        [*argv[:-1], "--offline", argv[-1]], timeout=sca.TIMEOUT, env=env)

    assert code == 0, f"exit {code}: {err[-300:]}"
    assert out == offline_out and code == offline_code, (
        "the production argv and the same argv in the scanner's own offline mode "
        "disagreed, so this scan was not network-free after all")
    doc = json.loads(out)
    assert doc["results"] is None, (
        f"a scan that read nothing reported {doc['results']!r}; an empty list here "
        f"would read as 'scanned and clean' to every caller downstream")
    assert "No package sources found" in err, err[-300:]
    assert sca.scanned_paths(err, ROOT) == [], \
        "the gate believes files were read when the scanner says it found no sources"


def test_the_line_the_sca_gate_parses_is_a_line_this_pinned_scanner_still_prints(
        tmp_path, monkeypatch):
    """The positive control for `sca._SCANNED`, against the real tool.

    That regex reads a THIRD-PARTY TOOL'S HUMAN-READABLE STDERR to decide which
    components were actually looked at, and `status` is `nothing-scanned` when it
    matches nothing. A reworded upstream line therefore reports a component that WAS
    scanned as one nobody looked at — a wrong answer wearing a gate's clothing —
    and until now the regex was only ever run against strings this repository wrote
    itself.

    **The argv is ASKED FOR, not re-typed**, by the same route as the test above and for
    the same reason: a copy of the production command drifts from the command it is
    supposed to be the control FOR, and it drifts silently. The first thing this file's
    other copy dropped was `READ_ONLY_ENV` (item 40), so that is asserted here too.
    `prepass.run_tool` is stubbed to RECORD AND NOT SPAWN, so `sca._invoke` assembles its
    real argv and environment while running nothing; the one spawn below is that argv with
    `--offline` inserted at the same place the test above inserts it.

    `--offline` is added here and nowhere in production: it makes the tool refuse to
    fetch its database, which is what keeps this test network-free — and it is also why
    the production argv is never run as-is over a tree that HAS a manifest.
    The stderr line under test is emitted by the extraction pass, which runs either way.
    """
    exe = _resolved(sca.BINARY)
    if exe is None:
        return
    where = tmp_path / "component"
    where.mkdir()
    (where / "requirements.txt").write_text("requests==2.25.0\nurllib3==1.26.0\n",
                                            encoding="utf-8")
    seen: list[tuple[list[str], dict | None]] = []

    def record_only(cmd, **kw):
        seen.append((list(cmd), kw.get("env")))
        return 0, "", ""

    monkeypatch.setattr(prepass, "run_tool", record_only)
    sca._invoke(exe, where)
    monkeypatch.undo()

    assert len(seen) == 1, f"sca._invoke built {len(seen)} commands, not one"
    argv, env = seen[0]
    assert env is sca.READ_ONLY_ENV and env, (
        f"the production scan is built with env={env!r}; the read-only environment is "
        f"what stops this scanner writing into the tree it is auditing")
    assert argv[-1] == str(where) and "--offline" not in argv, argv
    _, _, err = prepass.run_tool([*argv[:-1], "--offline", argv[-1]],
                                 timeout=sca.TIMEOUT, env=env)

    assert sca.scanned_paths(err, where) == ["requirements.txt"], (
        f"sca._SCANNED did not match this scanner's own report of what it read. "
        f"Its stderr was: {err[-400:]!r}")
    assert re.search(r"^Scanned .+ file and found \d+ package", err, re.M), err[-400:]


def test_the_gate_composes_the_real_scanner_with_its_real_reader(tmp_path):
    """The producer/consumer crossing: `sca.scan_one` spawns the real binary and
    reads the real reply, over a real git worktree.

    Two facts this pins that no fixture can: the row for a manifest-free component
    is `nothing-scanned` and not `ok`, and `mutated` is empty — a gate that edits the
    tree it audits puts changes nobody made into whatever is committed next, and
    `tracked_state` exists to catch it.
    """
    exe = _resolved(sca.BINARY)
    if exe is None:
        return
    where = tmp_path / "component"
    where.mkdir()
    (where / "a.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                       text=True, timeout=120)
    found = sca.scan_one(exe, where, tmp_path)
    row = found["row"]
    assert row["status"] == "nothing-scanned", row
    assert row["scanned"] == [] and row["findings"] == 0, row
    assert row["mutated"] == [], \
        f"the scan wrote into the tree it was auditing: {row['mutated']}"
    assert found["diagnostics"] == [], found["diagnostics"]


# ── the release tool: what a real spawn can settle, and what it cannot ────────

def _gh_call_sites(source: str) -> list[tuple[str, tuple[str, ...]]]:
    """Every `_gh(...)` in `release.py`, read from its AST rather than its text.

    **This is an AST because the text version could not see the sixth call.** The
    guard below was written to notice a new `gh` subcommand, and it matched
    `_gh\\(\\["..."` — an argument list written as a LITERAL at the call. `open_pr`
    builds its list in a variable and extends it under a condition, so `gh pr
    create`, the last outward-facing act of the release phase, was invisible to the
    one test whose job is to notice it. The record said five and there were six,
    and the guard passed at the exact moment its claim was violated.

    Resolving a `Name` argument back to the list assignments in the enclosing
    function reads both spellings the same way. Returned per call site: the leading
    run of plain string words (the subcommand — the walk stops at the first
    non-constant, so an interpolated argument ends it), and every `--flag` constant
    in the list, sorted. An argument whose VALUE is computed is not a flag and is
    deliberately not reported.
    """
    tree = ast.parse(source)
    found: list[tuple[str, tuple[str, ...]]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        built: dict[str, list[ast.expr]] = {}
        for node in ast.walk(fn):
            targets: tuple[ast.expr, ...] = ()
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = tuple(node.targets), node.value
            elif isinstance(node, ast.AugAssign):
                targets, value = (node.target,), node.value
            if isinstance(value, ast.List):
                for t in targets:
                    if isinstance(t, ast.Name):
                        built.setdefault(t.id, []).extend(value.elts)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_gh" and node.args):
                continue
            first = node.args[0]
            if isinstance(first, ast.List):
                elts = first.elts
            elif isinstance(first, ast.Name):
                elts = built.get(first.id, [])
            else:
                elts = []
            words: list[str] = []
            for e in elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str) \
                        and not e.value.startswith("-"):
                    words.append(e.value)
                else:
                    break
            flags = tuple(sorted({e.value for e in elts
                                  if isinstance(e, ast.Constant)
                                  and isinstance(e.value, str)
                                  and e.value.startswith("--")}))
            found.append((" ".join(words), flags))
    return sorted(found)


#: Every subcommand `release.py` puts through `_gh`, with the flags it passes.
#: SIX call sites, not five — `pr create` was the one the old text-matching guard
#: could not see. A literal, so it can never quietly become empty, and welded to
#: `release.py` in BOTH halves by the record test below: substitute another valid
#: `gh` flag here and that test names the difference. Flags sorted, because the
#: order a call happens to write them in is not part of the surface.
GH_SURFACE = [
    ("api", ("--jq",)),
    ("pr checks", ("--json", "--repo")),
    ("pr create", ("--base", "--body", "--head", "--repo", "--title")),
    ("pr list", ("--head", "--json", "--repo", "--state")),
    ("pr view", ("--jq", "--json", "--repo")),
    ("run view", ("--log-failed", "--repo")),
]


def test_the_pinned_release_binary_is_the_one_the_manifest_names():
    """A real spawn of `gh`, and the cheapest assertion that is still about the
    binary rather than about the file: it runs, exits 0, and reports the version
    that was pinned. A binary that resolves but cannot execute — wrong platform,
    truncated download — looks identical on disk and fails here."""
    assert _pinned_here(release.TOOL), f"{release.TOOL} is consumed by release.py but not pinned here"
    exe = _resolved(release.TOOL)
    if exe is None:
        return
    from tools import third_party as tp

    manifest = json.loads((ROOT / "third_party" / "manifest.json")
                          .read_text(encoding="utf-8"))
    want = manifest["tools"][release.TOOL]["assets"][tp.current_platform()]["version"]
    proc = _spawn([exe, "--version"])
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-300:]}"
    assert want in proc.stdout, f"pinned {want}, spawned binary said {proc.stdout!r}"


@pytest.mark.parametrize("subcommand,flags", GH_SURFACE, ids=[s for s, _ in GH_SURFACE])
def test_the_release_flow_passes_flags_this_pinned_release_binary_accepts(subcommand, flags):
    """`release.py` cannot be run against GitHub from a test, so the reply path is
    recorded below as uncovered. Its REQUEST path can be checked without a network:
    spawn the real binary and ask it what it accepts.

    This is what turns a `gh` upgrade that renames or drops a flag into a named red
    here instead of a `ReleaseError` on somebody's release day.
    """
    exe = _resolved(release.TOOL)
    if exe is None:
        return
    proc = _spawn([exe, *subcommand.split(), "--help"])
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-300:]}"
    text = proc.stdout + proc.stderr
    missing = [f for f in flags if f not in text]
    assert not missing, (
        f"release.py passes {missing} to `gh {subcommand}`, and this pinned gh does "
        f"not list them")


def test_the_uncovered_release_paths_are_recorded_and_the_record_cannot_go_stale():
    """The exit criterion's second branch, made enforceable.

    `release.py`'s replies from GitHub — check-run buckets, `REST_RUNNING`, the
    failed-log fetch — are UNCOVERED BY A REAL BINARY, and the reason is that every
    one of them is an authenticated network request; a test that made one would be
    asserting about GitHub's availability. They are covered by fakes elsewhere.

    A recorded reason is only worth something while it is complete, so this reads
    `release.py`'s own AST: every subcommand it puts through `_gh`, **and every flag
    it passes with one**, must be what `GH_SURFACE` records. Add a seventh call, or
    a flag to an existing one, and this test is what sends you back to decide
    whether the new one can be covered.

    **The flags half is asserted here and not only in the spawn test, and that is
    the point.** The spawn test asks the pinned `gh` whether it ACCEPTS each flag,
    so any valid flag passes it — swap `--json` for `--limit` on `pr list` and the
    binary is just as happy. Nothing then connects the recorded vocabulary to the
    vocabulary `release.py` actually uses, and the release-day failure this file
    exists to pre-empt is a flag the code passes and the tool dropped. Deriving the
    association from `release.py` rather than typing it twice is what makes a
    valid-but-wrong substitution fail.
    """
    source = (ROOT / "tools" / "sdlc" / "release.py").read_text(encoding="utf-8")
    called = _gh_call_sites(source)
    known = sorted(GH_SURFACE)
    assert called, "no `_gh(...)` call site was found at all, so this reads nothing"
    assert all(sub and flags for sub, flags in called), (
        f"a `_gh` call site read as having no subcommand or no flags, which is how "
        f"an extractor that stopped working looks from here: {called}")
    assert called == known, (
        f"release.py drives `gh` through {called}; this file records {known}. The "
        f"difference is a path, or a flag, nobody has decided is coverable or "
        f"uncoverable")
