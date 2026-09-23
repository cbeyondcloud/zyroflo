"""Local CI: the GitHub workflow's two jobs, run on THIS machine in Linux containers.

T461 (owner, 2026-09-23). The GitHub Actions budget ran to 90%, and CI stopped
being triggered by pushes (T460). The gate did not go away: it runs HERE, once
per logical chunk of work, right before that chunk merges.

It reproduces `.github/workflows/ci.yml` rather than approximating it, because
the reason CI is on Linux is the point: tests that skip on Windows (POSIX exec
bits, the tree-sitter snapshot drift) must EXECUTE somewhere, and
`SDLC_FORBID_SKIPS=1` fails the run if any of them skips.

    python tools/ci_local.py                 # HEAD of the current checkout
    python tools/ci_local.py --ref <branch>  # any ref this repository has

What it does, in order - each the workflow's own step:

1. A CLEAN clone of the ref with full history - what `actions/checkout` with
   `fetch-depth: 0` gives the runner, never the working tree, whose uncommitted
   edits CI would not see. Handed over as a `git bundle` and cloned INSIDE the
   container, onto its own filesystem (a Windows bind mount made the first run
   22.9 minutes), with `origin` set to this repository's real URL - the
   installer's cadence workflows read the harness slug off it.
   The suite runs as an UNPRIVILEGED user, as on the GitHub runner: measured,
   the first cut ran as root and four tests that force a write to fail by
   making a file read-only passed the write instead - root ignores the bit.
2. `suite (linux)`: a throwaway Neo4j on a private network, then - in the uv
   image, Python 3.11 - `uv sync --locked` with the CI extras, the vendored
   binaries fetched and `third_party/manifest.json` required unchanged, and
   the whole suite with `SDLC_FORBID_SKIPS=1` and `SDLC_REQUIRE_VENDORED_BINARIES=1`.
3. `secret scan`: gitleaks over the clone's full history.

Exit 0 only when both jobs pass. Containers and the network are removed
either way; the clone is removed unless `--keep`. Needs Docker with Linux
containers; spends nothing.
"""
from __future__ import annotations

import argparse
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402

cli.use_utf8()
#: The workflow's own pins, restated here and tied to it by a test.
PYTHON_VERSION = "3.11"
UV_IMAGE = f"ghcr.io/astral-sh/uv:python{PYTHON_VERSION}-bookworm"
NEO4J_IMAGE = "neo4j:5-community"
GITLEAKS_IMAGE = "ghcr.io/gitleaks/gitleaks:v8.18.4"
EXTRAS = "--extra dev --extra serve --extra sdlc"
VENDORED = "fd rg gitleaks ast-grep osv-scanner"
UV_CACHE_VOLUME = "sdlc-ci-uv-cache"

WAIT_NEO4J = """
import os, sys, time
from neo4j import GraphDatabase
last = None
for _ in range(60):
    try:
        d = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                 auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
        d.verify_connectivity(); d.close()
        print("neo4j authenticated and serving"); sys.exit(0)
    except Exception as exc:
        last = exc; time.sleep(2)
print(f"Neo4j never authenticated ({type(last).__name__}: {last})"); sys.exit(1)
"""


def suite_script(sha: str = "HEAD", origin: str = "") -> str:
    """The `suite (linux)` job's steps, as one shell script inside the container:
    an unprivileged `ci` user clones the bundle, then runs the workflow's steps."""
    run = f"uv run --locked --python {PYTHON_VERSION} {EXTRAS}"
    steps = " && ".join([
        "git clone --quiet /ci/repo.bundle /build/repo",
        "cd /build/repo",
        f"git checkout --quiet {sha}",
        *([f"git remote set-url origin {origin}"] if origin else []),
        f"uv sync --locked --python {PYTHON_VERSION} {EXTRAS}",
        f"{run} python /ci/wait_neo4j.py",
        f"{run} python tools/third_party.py --fetch --only {VENDORED}",
        "git diff --exit-code -- third_party/manifest.json",
        f"{run} pytest",
    ])
    return " && ".join([
        "useradd -m -d /build -u 1000 ci",
        "mkdir -p /uvcache && chown -R ci /uvcache",
        f"runuser -u ci -- env HOME=/build UV_CACHE_DIR=/uvcache bash -c '{steps}'",
    ])


def _docker(*args: str, check: bool = True, quiet: bool = False) -> int:
    cmd = ["docker", *args]
    code = subprocess.run(cmd, stdout=subprocess.DEVNULL if quiet else None,
                          stderr=subprocess.DEVNULL if quiet else None).returncode
    if check and code != 0:
        raise SystemExit(f"!! `docker {args[0]}` failed (exit {code}) - is Docker running "
                         f"with Linux containers?")
    return code


def _bundle(ref: str, into: Path) -> tuple[str, str]:
    """Every ref of this repository as one bundle file; returns (the commit `ref`
    resolves to, this repository's origin URL)."""
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        raise SystemExit(f"!! {ref!r} is not a commit this repository has")
    subprocess.run(["git", "-C", str(ROOT), "bundle", "create", "--quiet", str(into),
                    "--all"], check=True)
    origin = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    return sha, origin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="HEAD", help="the commit to test (default HEAD)")
    ap.add_argument("--keep", action="store_true", help="keep the clone for inspection")
    ap.add_argument("--no-secret-scan", action="store_true",
                    help="skip the gitleaks job (the suite still runs)")
    args = ap.parse_args(argv)

    if shutil.which("docker") is None:
        print("!! docker is not on PATH - local CI runs the workflow's jobs in Linux "
              "containers and cannot run without it")
        return 2
    tag = secrets.token_hex(4)
    work = Path(tempfile.mkdtemp(prefix="sdlc-ci-"))
    ci = work / "ci"
    ci.mkdir()
    (ci / "wait_neo4j.py").write_text(WAIT_NEO4J, encoding="utf-8", newline="\n")
    net, neo = f"sdlc-ci-net-{tag}", f"sdlc-ci-neo4j-{tag}"
    password = secrets.token_hex(16)
    results: dict[str, int] = {}
    started = time.monotonic()
    try:
        sha, origin = _bundle(args.ref, ci / "repo.bundle")
        print(f"local CI: {args.ref} = {sha[:12]} (bundled; cloned inside the container)")
        _docker("network", "create", net, quiet=True)
        _docker("run", "-d", "--name", neo, "--network", net,
                "-e", f"NEO4J_AUTH=neo4j/{password}", NEO4J_IMAGE, quiet=True)
        print("== suite (linux) ==", flush=True)
        results["suite (linux)"] = _docker(
            "run", "--rm", "--network", net,
            "-v", f"{ci}:/ci:ro", "-v", f"{UV_CACHE_VOLUME}:/uvcache",
            "-e", f"NEO4J_URI=bolt://{neo}:7687", "-e", "NEO4J_USER=neo4j",
            "-e", f"NEO4J_PASSWORD={password}", "-e", "SDLC_FORBID_SKIPS=1",
            "-e", "SDLC_REQUIRE_VENDORED_BINARIES=1", "-e", "UV_LINK_MODE=copy",
            UV_IMAGE, "bash", "-c", suite_script(sha, origin), check=False)
        if not args.no_secret_scan:
            print("== secret scan ==", flush=True)
            # gitleaks reads a checkout; clone the bundle into a scratch dir for it.
            scan = work / "scan"
            subprocess.run(["git", "clone", "--quiet", str(ci / "repo.bundle"), str(scan)],
                           check=True)
            subprocess.run(["git", "-C", str(scan), "checkout", "--quiet", sha], check=True)
            results["secret scan"] = _docker(
                "run", "--rm", "-v", f"{scan}:/repo", GITLEAKS_IMAGE,
                "detect", "--source", "/repo", "--redact", check=False)
    finally:
        _docker("rm", "-f", neo, check=False, quiet=True)
        _docker("network", "rm", net, check=False, quiet=True)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    minutes = (time.monotonic() - started) / 60
    print(f"\nlocal CI for {sha[:12]} ({minutes:.1f} min):")
    for job, code in results.items():
        print(f"  {job:<14} {'pass' if code == 0 else f'FAIL (exit {code})'}")
    return 0 if results and all(c == 0 for c in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
