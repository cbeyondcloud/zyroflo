"""`sdlc` - one console command over the harness's module CLIs (T448).

    sdlc stage --target . --list        is    python tools/sdlc/stage.py --target . --list
    sdlc status --target . --awaiting   is    python tools/sdlc/status.py --target . --awaiting
    sdlc init --target ../repo          is    python sdlc-init.py --target ../repo

A THIN dispatcher, not a rewrite: the subcommand picks a module and the rest of
argv is handed to it untouched. The module runs as `__main__` with `argv[0]` set
to its own path, so its `--help`, its output and its exit code are exactly the
path-based form's - which a test asserts subcommand by subcommand. Every
path-based invocation keeps working; this is a second spelling, never a
replacement, and SKILL.md keeps teaching agents the explicit form.

**The subcommand set is DERIVED**: every `tools/sdlc/*.py` that has a
`__main__` block, plus `init` for the installer beside a harness clone. A
module added tomorrow is a subcommand tomorrow, and a hand-kept list that
forgot one is the drift this project keeps paying for.
"""
from __future__ import annotations

import runpy
import sys
from difflib import get_close_matches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402

# The console rule (CLAUDE.md: Windows is a first-class target). The listing
# prints every module's docstring line, and those carry em-dashes a cp1252
# console cannot spell; each module sets its own console up, and the
# dispatcher must set up its own.
cli.use_utf8()

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
INSTALLER = HARNESS / "sdlc-init.py"
_MAIN_GUARD = 'if __name__ == "__main__":'


def subcommands() -> dict[str, Path]:
    """{name: script} - the derived set, sorted by name."""
    found = {p.stem: p for p in sorted(HERE.glob("*.py"))
             if not p.stem.startswith("_")
             and _MAIN_GUARD in p.read_text(encoding="utf-8", errors="replace")}
    if INSTALLER.is_file():
        found["init"] = INSTALLER
    return dict(sorted(found.items()))


def _summary(path: Path) -> str:
    """A module's first docstring line, for the listing."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for quote in ('"""', "'''"):
        if quote in text:
            body = text.split(quote, 2)[1].strip()
            return body.splitlines()[0].strip() if body else ""
    return ""


def usage(stream=sys.stdout) -> None:
    subs = subcommands()
    print("usage: sdlc <subcommand> [args...]   (args go to the subcommand untouched)",
          file=stream)
    print("\nsubcommands:", file=stream)
    width = max(len(n) for n in subs)
    for name, path in subs.items():
        print(f"  {name:<{width}}  {_summary(path)}", file=stream)
    print("\n`sdlc <subcommand> --help` for its own flags.", file=stream)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        usage()
        return 0 if args else 2
    name, rest = args[0], args[1:]
    subs = subcommands()
    if name not in subs:
        hint = get_close_matches(name, list(subs), n=1)
        print(f"!! unknown subcommand {name!r}"
              + (f" - did you mean {hint[0]!r}?" if hint else "")
              + f" One of: {', '.join(subs)}", file=sys.stderr)
        if name == "init":
            print("   `init` needs sdlc-init.py beside the harness clone; this "
                  "install has none - run it from a clone of the harness.",
                  file=sys.stderr)
        return 2
    script = subs[name]
    saved = sys.argv
    sys.argv = [str(script), *rest]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)       # SystemExit("message") prints and exits 1
        return 1
    finally:
        sys.argv = saved
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
