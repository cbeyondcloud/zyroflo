"""Real-binary coverage for the vendored tools whose consumers are production code.

A vendored binary earns its pin by having a consumer (item 113). Two of them had
one and were still spawned by **no test at all** — measured with a spawn tracer over
the 249 tests in the eight files that own the release and SCA surface, with every
binary present on disk: `gh` 0 spawns, `osv-scanner` 0 spawns. The repository's own
census agrees and is the second witness: `third_party.SPAWNED_BY_TESTS` records
`fd`, `rg`, `gitleaks` and `ast-grep`, and names neither of these.

Two tools spawned by one test is not the same defect. `test_artifact_crossings.py`
spawns gitleaks and ast-grep through `prepass.main` and asserts that the document
carries a `tools` key — that is a **weld**, and it is deliberately left one: its
question is whether two modules spell one filename the same way, and it must not
start failing because a scanner changed its output. Tool coverage is a different
question and lives here, so that each failure names one cause.

**What can be asked of these tools with no network, measured rather than assumed.**

`osv-scanner` — nearly everything that matters. The production argv over a tree with
no package manifests and the same argv with the scanner's own `--offline` flag give
byte-identical stdout and the same exit status, which is the proof that the first
made no request: there was nothing to look up. That path carries the behaviour this
repository has already paid to learn — a clean scan reports `"results": null`, not
`[]` — and `--offline` over a tree that DOES carry a manifest gives a live positive
control for the one regex standing between a scanned component and a gate that
reports it as unscanned.

`gh` — only its surface. Every call `release.py` makes (`pr list`, `pr checks`,
`api`, `pr view`, `run view`) is an authenticated request to GitHub, so the reply
path cannot be exercised here at all and is RECORDED as uncovered below rather than
faked. What a real spawn can still settle is that the pinned binary accepts the flag
vocabulary that code passes it, which nothing checked.

**No test here skips.** `SDLC_FORBID_SKIPS=1` turns any skip into exit 1, and
`third_party/bin/` is gitignored, so the binaries are present only after a fetch —
and CI's fetch step installs `fd rg gitleaks ast-grep` and not these two. So each
test asserts the part that is true everywhere (the tool is pinned for this platform,
and the production resolver names its absence rather than returning quietly), and
asserts the spawned part when the binary is actually there. This is the shape already
established by `test_third_party.py::test_the_tools_ci_fetches_resolve_when_the_suite_requires_them`.
"""
from __future__ import annotations

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


def test_the_scanner_reports_a_clean_tree_as_a_null_result_not_an_empty_one():
    """Measured live, and the distinction is the point: over a tree with no package
    manifests the scanner answers `"results": null` and says `No package sources
    found`. Nothing was read, which is not the same fact as *read and clean*, and
    `sca.run` turns exactly that difference into `nothing-scanned` rather than `ok`.

    The same argv with the scanner's own `--offline` flag returns identical stdout
    and the same exit status, which is how this test knows it asked nothing of the
    network.
    """
    assert _pinned_here(sca.BINARY), f"{sca.BINARY} is consumed by sca.py but not pinned here"
    exe = _resolved(sca.BINARY)
    if exe is None:
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        where = Path(tmp)
        (where / "a.py").write_text("x = 1\n", encoding="utf-8")
        argv = [exe, "scan", "source", "-r", "--allow-no-lockfiles",
                sca.NO_IGNORE, "--format", "json"]
        live = _spawn([*argv, where])
        offline = _spawn([*argv, "--offline", where])

    assert live.returncode == 0, f"exit {live.returncode}: {live.stderr[-300:]}"
    assert live.stdout == offline.stdout and live.returncode == offline.returncode, (
        "the production argv and the same argv in the scanner's own offline mode "
        "disagreed, so this scan was not network-free after all")
    doc = json.loads(live.stdout)
    assert doc["results"] is None, (
        f"a scan that read nothing reported {doc['results']!r}; an empty list here "
        f"would read as 'scanned and clean' to every caller downstream")
    assert "No package sources found" in live.stderr, live.stderr[-300:]
    assert sca.scanned_paths(live.stderr, ROOT) == [], \
        "the gate believes files were read when the scanner says it found no sources"


def test_the_line_the_sca_gate_parses_is_a_line_this_pinned_scanner_still_prints():
    """The positive control for `sca._SCANNED`, against the real tool.

    That regex reads a THIRD-PARTY TOOL'S HUMAN-READABLE STDERR to decide which
    components were actually looked at, and `status` is `nothing-scanned` when it
    matches nothing. A reworded upstream line therefore reports a component that WAS
    scanned as one nobody looked at — a wrong answer wearing a gate's clothing —
    and until now the regex was only ever run against strings this repository wrote
    itself.

    `--offline` is added here and nowhere in production: it makes the tool refuse to
    fetch its database, which is what keeps this test network-free. The stderr line
    under test is emitted by the extraction pass, which runs either way.
    """
    exe = _resolved(sca.BINARY)
    if exe is None:
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        where = Path(tmp)
        (where / "requirements.txt").write_text("requests==2.25.0\nurllib3==1.26.0\n",
                                                encoding="utf-8")
        proc = _spawn([exe, "scan", "source", "-r", "--allow-no-lockfiles",
                       sca.NO_IGNORE, "--format", "json", "--offline", where])
        assert sca.scanned_paths(proc.stderr, where) == ["requirements.txt"], (
            f"sca._SCANNED did not match this scanner's own report of what it read. "
            f"Its stderr was: {proc.stderr[-400:]!r}")
    assert re.search(r"^Scanned .+ file and found \d+ package", proc.stderr, re.M), \
        proc.stderr[-400:]


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

#: Every subcommand `release.py` puts through `_gh`, with the flags it passes.
#: Derived by reading its five call sites (`pr list`, `pr checks`, `api`,
#: `pr view`, `run view`). A literal, so it can never quietly become empty.
GH_SURFACE = [
    ("pr list", ["--repo", "--head", "--state", "--json"]),
    ("pr checks", ["--repo", "--json"]),
    ("api", ["--jq"]),
    ("pr view", ["--repo", "--json", "--jq"]),
    ("run view", ["--repo", "--log-failed"]),
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
    `release.py`'s own source: every subcommand it puts through `_gh` must be one
    `GH_SURFACE` names. Add a sixth call and this test is what sends you back to
    decide whether the new one can be covered.
    """
    source = (ROOT / "tools" / "sdlc" / "release.py").read_text(encoding="utf-8")
    called = sorted({" ".join(g for g in m.group(1, 2) if g) for m in
                     re.finditer(r'_gh\(\[\s*"([a-z]+)"(?:\s*,\s*"([a-z]+)")?', source)})
    known = sorted(s for s, _ in GH_SURFACE)
    assert called, "no `_gh([...])` call site was found at all, so this reads nothing"
    assert called == known, (
        f"release.py drives `gh` through {called}; this file records {known}. The "
        f"difference is a path nobody has decided is coverable or uncoverable")
