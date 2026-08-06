"""Keep pytest out of the fixture workspaces.

`fixtures/` holds synthetic *workspaces*, not test code: whole repos-in-miniature that the
golden build walks. Some of them will contain files pytest would happily collect — a
Python component fixture needs a `test_*.py` to look like a real Python component, and
`conftest.py` is itself a plausible fixture file.

Collecting one is not a loud failure. It is an import error in a file nobody meant to run,
or worse, a fixture's own `conftest.py` silently overriding this one for the whole
directory. Excluding the tree by glob costs one line and removes the class.
"""
from __future__ import annotations

#: Everything under any `fixtures/` directory, at any depth.
collect_ignore_glob = ["fixtures/*", "fixtures/**/*"]
