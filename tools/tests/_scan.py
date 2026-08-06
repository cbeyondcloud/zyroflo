"""Scanner behind the leak guard: finds machine paths and workspace coordinates in text.

This module holds no policy and reads no repo files — it is pure matching logic so that the
guard's behaviour can be proven against synthetic inputs (test_scan.py) independently of
whatever state the tree happens to be in. `test_no_private_coordinates.py` supplies the file
list and the ban list; this decides what counts as a hit.

Two rules shape the design, both learned the expensive way:

**Digests, not plaintext.** The ban list is SHA-256 hex. A committed file listing the terms it
guards would publish exactly what it protects, which is worse than the docstrings it polices.
Nothing here ever needs the plaintext of a banned term — matching hashes the candidate, not
the pattern — and a failure message quotes the offending token out of the file being scanned,
which discloses nothing that file did not already contain.

**Tokenise, don't regex.** A `\\b`-anchored regex cannot express what is wanted here: `_` is a
word character, so `ACME_SERVICE` slips past `\\bacme\\b`, while `digit` matches inside prose that
is talking about numerals. Splitting on non-alphanumerics *and* camelCase boundaries, then
comparing 1-, 2- and 3-grams, gets both cases right — `ripgrep` stays one token and never
matches, `ExampleRegistryHost` becomes three that do.
"""
from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize as _tokenize
from dataclasses import dataclass
from pathlib import Path

#: Longest phrase the ban list may express. Coined single tokens are 1-grams; anything that is
#: also an ordinary English word needs a 2- or 3-gram or the guard cries wolf until it is
#: switched off, which is the failure mode that matters most.
MAX_NGRAM = 3

#: Opt out of a single line, loudly. The whole-tree test pins the total count of these, so
#: adding one shows up as a changed number in a diff rather than as silence.
ALLOW_MARKER = "harness-allow:"

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

#: `X:\` or `X:/` preceded by something that is not a word character. The lookbehind is the
#: whole trick: it clears `lint_command:\s*` and `f"failed:\n{out}"` (where the drive letter is
#: really the tail of a longer word) while still catching `=<workspace-root>` and `--target D:/foo`.
_DRIVE = re.compile(r"(?:^|[^A-Za-z0-9_])([A-Za-z]:[\\/])")
_HOME = re.compile(r"/(?:home|Users)/([A-Za-z][A-Za-z0-9._-]*)")
_UNC = re.compile(r"\\\\[A-Za-z0-9_.-]+\\")

#: Names that mean "put yours here". A template saying /home/you is doing its job.
PLACEHOLDER_USERS = frozenset({
    "you", "your", "yourname", "your-name", "user", "username", "me", "someone",
    "developer", "dev", "runner", "root", "ubuntu", "admin", "name",
})


@dataclass(frozen=True)
class Finding:
    """One hit. `evidence` is quoted from the scanned file, never from the ban list."""

    line: int
    kind: str  # "path" | "token"
    evidence: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"line {self.line}: {self.kind}: {self.evidence}"


# ── tokenisation ───────────────────────────────────────────────────────────────
def tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric tokens, split on punctuation AND camelCase boundaries.

    `ripgrep` -> ['ripgrep'] (one token, so a banned 'acme' cannot match inside it)
    `ACME_SERVICE` -> ['acme', 'service'] (underscore splits, which \\b would not)
    `ExampleRegistryHost` -> ['example', 'registry', 'host']
    """
    return [t.lower() for t in _NON_ALNUM.split(_CAMEL.sub(" ", text)) if t]


def windows(tokens: list[str], max_n: int = MAX_NGRAM) -> list[list[str]]:
    """Every 1..max_n consecutive token window. Order preserved, duplicates kept."""
    return [tokens[i:i + n]
            for n in range(1, max_n + 1)
            for i in range(len(tokens) - n + 1)]


def canonical(tokens: list[str]) -> str:
    """The comparison form: tokens joined with NOTHING.

    Separator-free is what makes a coinage un-evadable by re-spelling it. `AcmeCorp` splits
    into two tokens while `acmecorp` is one, so a space-joined form would hash them
    differently and a ban on either spelling would miss the other — `eCorp` sliding past a ban
    on `example` is the same bug. Concatenating collapses every spelling of a compound onto one
    digest, and 1-grams stay distinguishable from n-grams because `widget` and `widgetcore`
    are still different strings.
    """
    return "".join(tokens)


def ngrams(tokens: list[str], max_n: int = MAX_NGRAM) -> list[str]:
    """Canonical forms of every 1..max_n window."""
    return [canonical(w) for w in windows(tokens, max_n)]


def digest(phrase: str) -> str:
    """The stored form. Normalised through the tokeniser so the ban list cannot drift from the
    matcher: `Acme-Corp`, `acme_corp`, `AcmeCorp` and `acmecorp` are all one entry."""
    return hashlib.sha256(canonical(tokenize(phrase)).encode("utf-8")).hexdigest()


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load_banned(path: Path) -> set[str]:
    """Read a digest file. Blank lines and `#` comments are skipped; anything else must be a
    64-char lowercase hex digest, because a stray plaintext line would be a silent disclosure
    AND a permanently non-matching entry."""
    banned: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _HEX64.match(line):
            raise ValueError(f"{path.name}:{lineno} is not a lowercase sha256 hex digest")
        banned.add(line)
    return banned


# ── matching ───────────────────────────────────────────────────────────────────
def find_machine_paths(text: str) -> list[str]:
    """Absolute paths that only exist on one machine. Returns the matched fragments."""
    hits = [m.group(1) for m in _DRIVE.finditer(text)]
    hits += [f"/home|Users/{u}" for u in _HOME.findall(text)
             if u.lower() not in PLACEHOLDER_USERS]
    hits += [m.group(0) for m in _UNC.finditer(text)]
    return hits


def find_banned_tokens(text: str, banned: set[str]) -> list[str]:
    """Workspace coordinates. Hashes the canonical form, reports the readable one."""
    if not banned:
        return []
    hits = []
    for w in windows(tokenize(text)):
        if hashlib.sha256(canonical(w).encode("utf-8")).hexdigest() in banned:
            hits.append(" ".join(w))
    return hits


def scan_text(text: str, banned: set[str], *, allow_lines: frozenset[int] = frozenset()) -> list[Finding]:
    """Scan raw text line by line. Used for every file type except Python."""
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if lineno in allow_lines or ALLOW_MARKER in line:
            continue
        findings += [Finding(lineno, "path", h) for h in find_machine_paths(line)]
        findings += [Finding(lineno, "token", h) for h in find_banned_tokens(line, banned)]
    return findings


def python_scannables(source: str) -> list[tuple[int, str]]:
    """(line, text) pairs for a Python file: string literal VALUES, comments, identifiers.

    Literals come from `ast`, which means the *post-escape* value: the source `"x:\\n"` arrives
    here as `x` + a real newline and cannot masquerade as a drive letter, while `"D:\\\\Example"`
    correctly arrives as `D:\\Example`. That single choice removes the only false-positive class
    the path regex has left. Comments are invisible to `ast`, so they come from `tokenize`.
    """
    out: list[tuple[int, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.lineno, node.name))
            for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
                out.append((node.lineno, a.arg))
        elif isinstance(node, ast.ClassDef):
            out.append((node.lineno, node.name))
        elif isinstance(node, ast.Name):
            out.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            out.append((node.lineno, node.attr))
    try:
        for tok in _tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == _tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (_tokenize.TokenError, IndentationError):  # pragma: no cover - ast would have raised first
        pass
    return out


def scan_python(source: str, banned: set[str]) -> list[Finding]:
    """Scan a Python module. Falls back to raw-text scanning if it will not parse, because a
    file that cannot be parsed is exactly the one nobody has looked at."""
    lines = source.splitlines()
    allow = frozenset(i for i, ln in enumerate(lines, 1) if ALLOW_MARKER in ln)
    try:
        scannables = python_scannables(source)
    except SyntaxError:
        return scan_text(source, banned, allow_lines=allow)

    findings: list[Finding] = []
    for lineno, text in scannables:
        if lineno in allow:
            continue
        findings += [Finding(lineno, "path", h) for h in find_machine_paths(text)]
        findings += [Finding(lineno, "token", h) for h in find_banned_tokens(text, banned)]
    return findings


def scan_file(path: Path, banned: set[str]) -> list[Finding]:
    """Dispatch on suffix. Unreadable bytes are decoded permissively rather than skipped —
    a file the guard cannot read must not read as a file with nothing in it."""
    raw = path.read_bytes()
    if b"\x00" in raw:
        return []
    source = raw.decode("utf-8", errors="replace")
    if path.suffix == ".py":
        return scan_python(source, banned)
    return scan_text(source, banned)
