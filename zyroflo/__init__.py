"""Zyroflo - the `tools` package under its product's name (T696, Z4).

`import zyroflo.sdlc.approve` is `import tools.sdlc.approve`: the SAME module
object under a second name, never a second copy. A copy would hold its own
globals, so a monkeypatch through one name would go unseen through the other,
and a class from one copy would fail `isinstance` against the other's. Every
`tools.*` import, path-based run and the `sdlc` command work exactly as before;
this package adds a name and changes nothing else.

How: a finder at the front of `sys.meta_path` answers every `zyroflo.<rest>`
with a loader that imports `tools.<rest>` and hands back that module.
The import system then files it under the alias name too. It must stand FIRST:
`zyroflo.sdlc` is `tools.sdlc`, whose `__path__` the ordinary path finder would
otherwise search for `zyroflo.sdlc.approve` and execute `approve.py` a second
time under the new name. The import system stamps the alias spec on the module
it is handed; the loader puts the module's own spec back, so `__spec__`,
`importlib.reload` and pickling keep naming `tools.*`.

This package (`zyroflo` itself) is the one module that is not `tools`: it holds
the finder, and hands out `tools`' subpackages as attributes (`zyroflo.sdlc`).
Stdlib only, as everything shipped is: the harness runs with no packages.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys

_ALIAS = __name__
_TARGET = "tools"


def _target(fullname: str) -> str:
    """The `tools` name for an alias name: `zyroflo.sdlc` -> `tools.sdlc`."""
    return _TARGET + fullname[len(_ALIAS):]


class _AliasLoader(importlib.abc.Loader):
    """Loads an alias name as the `tools` module it names."""

    def __init__(self, target: importlib.machinery.ModuleSpec) -> None:
        self._target = target

    def create_module(self, spec):
        module = importlib.import_module(self._target.name)
        self._own_spec = module.__spec__  # put back in exec_module
        return module

    def exec_module(self, module) -> None:
        # Already executed under its own name; undo the alias spec the import
        # system stamped on it after `create_module`.
        module.__spec__ = self._own_spec

    def is_package(self, fullname: str) -> bool:
        return self._target.submodule_search_locations is not None

    def get_code(self, fullname: str):
        """For `python -m zyroflo.sdlc`: runpy asks the loader for code to run."""
        return self._target.loader.get_code(self._target.name)


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Answers `zyroflo.<rest>` with the module `tools.<rest>`."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_ALIAS + "."):
            return None
        found = importlib.util.find_spec(_target(fullname))
        if found is None:
            return None
        spec = importlib.machinery.ModuleSpec(
            fullname, _AliasLoader(found), origin=found.origin,
            is_package=found.submodule_search_locations is not None)
        # runpy sets `__file__` from these; an import keeps the module's own.
        spec.has_location = found.has_location
        if found.submodule_search_locations is not None:
            spec.submodule_search_locations = list(found.submodule_search_locations)
        return spec


def __getattr__(name: str):
    """`zyroflo.sdlc` as an attribute, as `import zyroflo.sdlc` would bind it."""
    if name.startswith("_"):
        raise AttributeError(f"module {_ALIAS!r} has no attribute {name!r}")
    try:
        return importlib.import_module(f"{_ALIAS}.{name}")
    except ModuleNotFoundError as error:
        if error.name != f"{_ALIAS}.{name}":
            raise
        raise AttributeError(f"module {_ALIAS!r} has no attribute {name!r}") from None


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())
