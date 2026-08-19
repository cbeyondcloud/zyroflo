"""The M1 blind A/B — impact vs grep on real merge commits, per the locked protocol.

Item 18. The protocol is `docs/m0/judging-protocol.md` (pre-registered 2026-08-05,
amended 2026-08-06): commit selection is mechanical (section 2), ground truth is what
the merged change actually touched (3.1), the baseline is a competent developer's grep
over the same tree with the same vendored ripgrep (3.2), outputs are blinded to X/Y
before scoring (3.3), and the primary metric is recall@20 with the pass rule fixed in
3.5. This runner exists so every one of those sentences is a function with a test,
rather than a judgment call made on the day.

Where the protocol leaves an operational gap, this file closes it mechanically and the
gate record states the reading:

* "any three components with `role: authoritative`" — taken in the workspace's own
  deterministic component order (discovery sorts by name), one commit per component,
  skipping a component that yields no qualifying commit and recording why.
* "each changed symbol / property key / topic name" — identifiers are extracted from
  the seed file's own hunks (frequency-ordered, length >= 4, capped), and the term list
  is written into the run record so the operationalization is auditable.
* criterion (4), "the snapshot predates the commit" — satisfied by construction: the
  graph is rebuilt with the judged component at the commit's first parent, extracted
  via `git archive` into scratch. Nothing is ever written into the adopter's tree; the
  other components enter the scratch workspace as links (symlink, junction fallback),
  and links are removed link-by-link before any scratch cleanup.
* test files are filtered from BOTH conditions' rankings: ground truth contains none by
  construction (3.1), so leaving them in either list only burns that list's slots.

v1 supports discovery-mode workspaces only (a bare `root`). A workspace declaring
`include`/`exclude`/`[component.*]`/`[[canonical]]` is refused loudly rather than
half-honoured — the derived scratch workspace would silently drop those instructions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.graph import build as build_mod  # noqa: E402
from tools.graph import impact as impact_mod  # noqa: E402

SCHEMA_VERSION = 1
RECALL_K = 20
PRECISION_K = 10
TERM_CAP = 40

_CONFIG_SUFFIXES = (".properties", ".yml", ".yaml", ".xml", ".json", ".toml",
                    ".conf", ".ini", ".env", ".cfg")
_TEST_SEGMENTS = {"test", "tests", "it", "e2e", "__tests__"}
_TEST_NAMES = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|Tests?\.)")
_EXCLUDED_SUBJECT = re.compile(
    r"\b(bump(s|ed)?|upgrade[sd]?|release|reformat|formatting)\b", re.I)
_GENERATED = ("/generated/", "/gen/", "/dist/", "/build/", ".min.")
_MANIFESTS = ("pom.xml", "build.gradle", "settings.gradle", "package.json",
              "pyproject.toml", "go.mod", "Cargo.toml")
_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _git(repo: Path, *args: str) -> tuple[str, str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"git did not run: {exc}"
    if proc.returncode != 0:
        return "", (proc.stderr or "git failed").strip().splitlines()[0]
    return proc.stdout, ""


def is_test_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(p.lower() in _TEST_SEGMENTS for p in parts[:-1]):
        return True
    return bool(_TEST_NAMES.search(parts[-1]))


def is_config_path(path: str) -> bool:
    return path.lower().endswith(_CONFIG_SUFFIXES)


def _is_generated(path: str) -> bool:
    p = "/" + path.replace("\\", "/").lower()
    return any(marker in p for marker in _GENERATED)


# -- section 2: selection ------------------------------------------------------------------


def merge_commits(repo: Path, limit: int) -> tuple[list[dict], str]:
    fmt = "%H%x01%aI%x01%s"
    out, err = _git(repo, "log", "--merges", "--first-parent",
                    f"--max-count={limit}", f"--format={fmt}")
    if err:
        return [], err
    rows = []
    for line in out.splitlines():
        sha, date, subject = (line.split("\x01") + ["", "", ""])[:3]
        if sha:
            rows.append({"sha": sha, "date": date, "subject": subject})
    return rows, ""


def files_of_commit(repo: Path, sha: str) -> list[str]:
    """Changed paths in git's own diff order — 3.1's seed rule depends on that order."""
    out, _ = _git(repo, "show", "--pretty=format:", "--name-only",
                  "--diff-merges=first-parent", sha)
    return [f for f in out.splitlines() if f.strip()]


def parent_of(repo: Path, sha: str) -> str:
    out, _ = _git(repo, "rev-parse", f"{sha}^1")
    return out.strip()


def manifest_dirs(repo: Path, sha: str) -> set[str]:
    out, _ = _git(repo, "ls-tree", "-r", "--name-only", sha)
    return {("" if "/" not in f else f.rsplit("/", 1)[0])
            for f in out.splitlines() if f.rsplit("/", 1)[-1] in _MANIFESTS}


def module_of(path: str, dirs: set[str]) -> str:
    best = ""
    for d in dirs:
        if d and (path == d or path.startswith(d + "/")) and len(d) > len(best):
            best = d
    return best


def choose_seed(files: list[str]) -> str:
    """3.1: the first non-config, non-test file in the commit's diff order."""
    for f in files:
        if not is_test_path(f) and not is_config_path(f):
            return f
    return ""


def qualifies(subject: str, files: list[str], mdirs: set[str]) -> tuple[bool, str]:
    """Section 2's four criteria, as one checkable sentence each. Criterion (4) is met
    by construction (the graph is rebuilt at the commit's parent) and not tested here."""
    non_test = [f for f in files if not is_test_path(f)]
    if len(non_test) < 3:
        return False, "fewer than 3 non-test files (criterion 3)"
    if len({module_of(f, mdirs) for f in non_test}) < 2:
        return False, "touches a single module (criterion 1)"
    if _EXCLUDED_SUBJECT.search(subject):
        return False, "dependency/version/formatting subject (criterion 2)"
    if all(_is_generated(f) for f in non_test):
        return False, "generated files only (criterion 2)"
    if not choose_seed(files):
        return False, "no non-config non-test seed (3.1)"
    return True, ""


def seed_and_truth(files: list[str]) -> tuple[str, list[str]]:
    seed = choose_seed(files)
    truth = [f for f in files if not is_test_path(f) and f != seed]
    return seed, truth


def select(components: list[dict], root: Path, limit_back: int,
           wanted: int = 3) -> dict:
    """One qualifying merge commit per authoritative component, newest first, in the
    workspace's own component order. Rejections are recorded — that is what makes
    'we walked back and took the first three' checkable rather than merely claimed."""
    picks, rejections, componentless = [], [], []
    for c in [c for c in components if c.get("role") == "authoritative"]:
        if len(picks) == wanted:
            break
        repo = root / c["path"]
        merges, err = merge_commits(repo, limit_back)
        if err:
            componentless.append({"component": c["name"], "why": err})
            continue
        picked = False
        for m in merges:
            files = files_of_commit(repo, m["sha"])
            ok, why = qualifies(m["subject"], files, manifest_dirs(repo, m["sha"]))
            if not ok:
                rejections.append({"component": c["name"], "sha": m["sha"], "rule": why})
                continue
            seed, truth = seed_and_truth(files)
            picks.append({"component": c["name"], "path": c["path"], "sha": m["sha"],
                          "parent": parent_of(repo, m["sha"]), "date": m["date"],
                          "subject": m["subject"], "files": files, "seed": seed,
                          "truth": truth})
            picked = True
            break
        if not picked and not any(x["component"] == c["name"] for x in componentless):
            componentless.append({"component": c["name"],
                                  "why": "no qualifying merge in the window"})
    return {"picks": picks, "rejections": rejections, "componentless": componentless}


# -- section 3.2 condition A: the grep baseline --------------------------------------------


def extract_terms(repo: Path, sha: str, seed: str, cap: int = TERM_CAP) -> list[str]:
    out, _ = _git(repo, "show", "--pretty=format:", "--diff-merges=first-parent",
                  sha, "--", seed)
    freq: dict[str, int] = {}
    for line in out.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        for token in _TERM.findall(line):
            freq[token] = freq.get(token, 0) + 1
    ordered = sorted(freq, key=lambda t: (-freq[t], t))
    return ordered[:cap]


def resolve_rg(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    from tools import third_party as tp
    plat = tp.current_platform()
    rg = tp.bin_dir(plat) / ("rg.exe" if plat.startswith("windows") else "rg")
    if not rg.exists():
        raise SystemExit(f"!! vendored ripgrep missing at {rg}\n"
                         f"   fetch it: python tools/third_party.py --fetch")
    return rg


def _rg_counts(rg: Path, term: str, root: Path) -> list[tuple[str, int]]:
    """`rg -c` for one term over the scratch workspace. `-L` follows the component
    links — without it the baseline would silently search only the extracted repo."""
    proc = subprocess.run(
        [str(rg), "-c", "--no-messages", "-L", "-F", term, "."],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(root), timeout=600)
    rows = []
    for line in proc.stdout.splitlines():
        path, sep, count = line.rpartition(":")
        if sep and count.isdigit():
            path = path.replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            rows.append((path, int(count)))
    return rows


def grep_rank(terms: list[str], root: Path, dir_to_alias: dict[str, str],
              exclude: tuple[str, str], runner=None) -> list[tuple[str, str]]:
    """Files ranked by total hit count, descending — 3.2's Condition A. `runner` is
    injectable so the ranking rule is testable without the binary."""
    runner = runner or (lambda term: _rg_counts(resolve_rg(), term, root))
    hits: dict[tuple[str, str], int] = {}
    for term in terms:
        for path, count in runner(term):
            head = max((d for d in dir_to_alias
                        if path == d or path.startswith(d + "/")),
                       key=len, default="")
            if not head:
                continue
            key = (dir_to_alias[head], path[len(head) + 1:])
            if is_test_path(key[1]) or key == exclude:
                continue
            hits[key] = hits.get(key, 0) + count
    return [k for k, _ in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))]


# -- section 3.2 condition B: the graph ----------------------------------------------------


def impact_rank(graph_dir: Path, alias: str, seed_rel: str,
                exclude: tuple[str, str]) -> list[tuple[str, str]]:
    nodes, edges = impact_mod.load_graph(graph_dir)
    result = impact_mod.impact(nodes, edges, alias, seed_rel)
    if result is None:
        return []
    return [(f["component"], f["path"]) for f in result["files"]
            if not is_test_path(f["path"]) and (f["component"], f["path"]) != exclude]


# -- the parent-state workspace ------------------------------------------------------------


def _is_link(p: Path) -> bool:
    if os.path.islink(p):
        return True
    if os.name == "nt":
        try:
            return bool(os.lstat(p).st_file_attributes
                        & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except OSError:
            return False
    return False


def make_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(src), str(dst), target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise OSError(f"junction failed: {proc.stderr.strip() or proc.stdout.strip()}")


def remove_link(p: Path) -> None:
    """Remove the reparse point / symlink itself, never what it points at."""
    try:
        os.rmdir(p)
    except (NotADirectoryError, OSError):
        os.unlink(p)


def _extract_tar(payload: bytes, dest: Path) -> int:
    """Extract an archive, skipping link members (returns how many). Real repos commit
    symlinks — some pointing at absolute paths, which the 'data' filter rightly refuses
    — and a link's target is not part of the tracked tree anyway. The count is recorded
    rather than hidden."""
    dest.mkdir(parents=True, exist_ok=True)
    skipped = 0
    with tarfile.open(fileobj=BytesIO(payload)) as tar:
        for member in tar:
            if member.issym() or member.islnk():
                skipped += 1
                continue
            tar.extract(member, dest, filter="data")
    return skipped


def extract_parent(repo: Path, sha: str, dest: Path) -> int:
    proc = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha],
                          capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit(f"!! git archive failed in {repo}: "
                         f"{proc.stderr.decode(errors='replace').strip()}")
    return _extract_tar(proc.stdout, dest)


def derived_workspace(components: list[dict], root: Path, target_alias: str,
                      parent_sha: str, scratch: Path) -> tuple[Path, list[Path]]:
    """A scratch workspace: every component linked in place, except the judged one,
    which is a real extraction at the commit's first parent. Aliases survive because
    the entry names are the original relative paths and the target is declared."""
    ws_root = scratch / f"ab-{target_alias}-{parent_sha[:8]}"
    cleanup(ws_root)          # a crashed prior run leaves links; start from nothing
    ws_root.mkdir(parents=True, exist_ok=True)
    links: list[Path] = []
    target_rel = ""
    links_skipped = 0
    for c in components:
        if c["name"] == target_alias:
            target_rel = c["path"]
            links_skipped = extract_parent(root / c["path"], parent_sha,
                                           ws_root / c["path"])
        else:
            make_link((root / c["path"]).resolve(), ws_root / c["path"])
            links.append(ws_root / c["path"])
    toml = ws_root / "sdlc-ab-workspace.toml"
    toml.write_text(
        f'root = "{ws_root.as_posix()}"\n\n'
        f'[[component]]\n'
        f'name = {json.dumps(target_alias)}\n'
        f'path = "{target_rel}"\n', encoding="utf-8")
    return toml, len(links), links_skipped


def cleanup(ws_root: Path) -> None:
    """Delete a scratch workspace, links first and link-aware: every link is removed as
    a link (never followed), and the walk prunes link directories before descending —
    on Windows a junction walks like a directory, and descending through one would put
    the adopter's real tree inside the deletion."""
    if not ws_root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(ws_root):
        for name in list(dirnames):
            p = Path(dirpath) / name
            if _is_link(p):
                remove_link(p)
                dirnames.remove(name)
        for name in filenames:
            p = Path(dirpath) / name
            if _is_link(p):
                remove_link(p)
    for child in ws_root.rglob("*"):
        if _is_link(child):
            raise SystemExit(f"!! refusing to delete {ws_root}: {child} is still a link")
    shutil.rmtree(ws_root, ignore_errors=True)


# -- sections 3.3 + 3.5: blinding, metrics, verdict ----------------------------------------


def recall_at(ranked: list, truth: set, k: int = RECALL_K) -> float:
    if not truth:
        return 0.0
    return round(len(set(ranked[:k]) & truth) / len(truth), 4)


def precision_at(ranked: list, truth: set, k: int = PRECISION_K) -> float:
    return round(len(set(ranked[:k]) & truth) / k, 4)


def first_tp_rank(ranked: list, truth: set) -> int | None:
    for i, item in enumerate(ranked, start=1):
        if item in truth:
            return i
    return None


def blind_and_score(a_ranked: list, b_ranked: list, truth: set, flip: int) -> dict:
    """Scores computed on X/Y before the mapping is attached — 3.3's discipline in code:
    nothing downstream of the metric can be nudged by knowing which list is which."""
    x_list, y_list = (b_ranked, a_ranked) if flip else (a_ranked, b_ranked)
    scores = {label: {"recall_at_20": recall_at(lst, truth),
                      "precision_at_10": precision_at(lst, truth),
                      "first_true_positive": first_tp_rank(lst, truth)}
              for label, lst in (("X", x_list), ("Y", y_list))}
    mapping = {"X": "B" if flip else "A", "Y": "A" if flip else "B"}
    return {"scores": scores, "mapping": mapping, "flip": flip}


def verdict(per_commit: list[dict]) -> dict:
    """3.5, fixed before any run: B beats A (strict — a tie is not a beat) on >= 2 of
    the commits, and never loses by more than 0.20 recall@20."""
    wins = sum(1 for r in per_commit if r["recall_b"] > r["recall_a"])
    worst_loss = max((r["recall_a"] - r["recall_b"] for r in per_commit
                      if r["recall_b"] < r["recall_a"]), default=0.0)
    passed = wins >= 2 and worst_loss <= 0.20
    return {"wins_b": wins, "of": len(per_commit),
            "worst_loss_recall": round(worst_loss, 4),
            "verdict": "PASS" if passed else "FAIL"}


# -- the run -------------------------------------------------------------------------------


def read_workspace_components(workspace: Path) -> tuple[Path, list[dict]]:
    import importlib.util
    root_dir = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("sdlc_init", root_dir / "sdlc-init.py")
    si = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", si)
    spec.loader.exec_module(si)
    ws = si.read_workspace(workspace)
    if ws["include"] or ws["exclude"] or ws["overrides"] or ws["canonical"]:
        raise SystemExit("!! the A/B derives a scratch workspace and supports "
                         "discovery-mode workspaces only in v1 — this one declares "
                         "include/exclude/[component.*]/[[canonical]] blocks, which "
                         "the derived workspace would silently drop")
    found = si.discover_components(ws)["components"]
    return ws["root"], [{"name": c["name"], "path": c["path"], "role": c["role"]}
                        for c in found]


def run_one(pick: dict, components: list[dict], root: Path, scratch: Path,
            out_dir: Path, flip: int, rg: str | None, keep: bool,
            grep_runner=None) -> dict:
    alias, seed, sha = pick["component"], pick["seed"], pick["sha"]
    truth = {(alias, f) for f in pick["truth"]}
    toml, _, links_skipped = derived_workspace(components, root, alias,
                                               pick["parent"], scratch)
    ws_root = toml.parent
    try:
        graph_out = ws_root / "graph-out"
        t0 = time.perf_counter()
        build_mod.build(toml, graph_out, run_id=f"ab-{sha[:8]}")
        build_secs = round(time.perf_counter() - t0, 1)

        t0 = time.perf_counter()
        b_ranked = impact_rank(graph_out, alias, seed, exclude=(alias, seed))
        b_secs = round(time.perf_counter() - t0, 1)

        terms = extract_terms(root / pick["path"], sha, seed)
        dir_to_alias = {c["path"]: c["name"] for c in components}
        t0 = time.perf_counter()
        runner = ((lambda term: grep_runner(term, ws_root)) if grep_runner
                  else (lambda term: _rg_counts(resolve_rg(rg), term, ws_root)))
        a_ranked = grep_rank(terms, ws_root, dir_to_alias, exclude=(alias, seed),
                             runner=runner)
        a_secs = round(time.perf_counter() - t0, 1)
    finally:
        if not keep:
            cleanup(ws_root)
        else:
            print(f"   scratch kept: {ws_root}")

    case_dir = out_dir / f"{alias}-{sha[:8]}"
    case_dir.mkdir(parents=True, exist_ok=True)
    blinded = blind_and_score(a_ranked, b_ranked, truth, flip)
    for label, lst in (("X", b_ranked if flip else a_ranked),
                       ("Y", a_ranked if flip else b_ranked)):
        (case_dir / f"{label}.txt").write_text(
            "\n".join(f"{c}/{p}" for c, p in lst) + "\n", encoding="utf-8")
    a_scores = blinded["scores"]["Y" if flip else "X"]
    b_scores = blinded["scores"]["X" if flip else "Y"]
    record = {
        "schema_version": SCHEMA_VERSION, "component": alias, "sha": sha,
        "parent": pick["parent"], "date": pick["date"], "seed": seed,
        "truth_size": len(truth), "terms": terms, "blinded": blinded,
        "recall_a": a_scores["recall_at_20"], "recall_b": b_scores["recall_at_20"],
        "a": a_scores, "b": b_scores, "archive_links_skipped": links_skipped,
        "wall_clock_secs": {"graph_build": build_secs, "impact": b_secs,
                            "grep": a_secs},
    }
    (case_dir / "case.json").write_text(json.dumps(record, indent=2, sort_keys=True)
                                        + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, metavar="TOML")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help="where selection records, blinded lists and scores land")
    ap.add_argument("--limit-back", type=int, default=200, metavar="N",
                    help="merge-commit window per component, newest first — a "
                         "recorded bound")
    ap.add_argument("--select-only", action="store_true",
                    help="run section 2 and stop: print the selection record without "
                         "building or scoring anything")
    ap.add_argument("--flips", metavar="0,1,0",
                    help="recorded coin flips for 3.3 (default: fresh random flips, "
                         "recorded in the output)")
    ap.add_argument("--rg", metavar="PATH", help="override the vendored ripgrep")
    ap.add_argument("--keep-scratch", action="store_true")
    args = ap.parse_args(argv)

    root, components = read_workspace_components(Path(args.workspace))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    selection = select(components, root, args.limit_back)
    (out_dir / "selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"selected {len(selection['picks'])} commit(s); "
          f"{len(selection['rejections'])} rejection(s) recorded; "
          f"{len(selection['componentless'])} component(s) yielded nothing")
    for p in selection["picks"]:
        print(f"  {p['component']}  {p['sha'][:12]}  {p['date']}  "
              f"seed={p['seed']}  truth={len(p['truth'])} file(s)")
    if args.select_only:
        return 0
    if len(selection["picks"]) < 3:
        print("!! the protocol needs 3 qualifying commits; widen --limit-back or "
              "check component roles")
        return 2

    flips = ([int(x) for x in args.flips.split(",")] if args.flips
             else [secrets.randbits(1) for _ in selection["picks"]])
    if len(flips) < len(selection["picks"]):
        print(f"!! {len(selection['picks'])} commits need {len(selection['picks'])} "
              f"flips, got {len(flips)}")
        return 2
    scratch = out_dir / "scratch"
    results = []
    for pick, flip in zip(selection["picks"], flips):
        print(f"\n== {pick['component']} @ {pick['sha'][:12]} (flip recorded)")
        results.append(run_one(pick, components, root, scratch, out_dir, flip,
                               args.rg, args.keep_scratch))
        r = results[-1]
        print(f"   recall@20  A={r['recall_a']}  B={r['recall_b']}  "
              f"(truth {r['truth_size']}, build {r['wall_clock_secs']['graph_build']}s)")

    final = verdict(results)
    payload = {"schema_version": SCHEMA_VERSION, "protocol":
               "docs/m0/judging-protocol.md (2026-08-05, amended 2026-08-06)",
               "cases": results, "verdict": final}
    (out_dir / "ab_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nverdict: {final['verdict']}  (B wins {final['wins_b']} of "
          f"{final['of']}; worst loss {final['worst_loss_recall']})")
    print(f"records: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
