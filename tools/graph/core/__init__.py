"""Estate-agnostic graph core: meta-model, SPIs, store seam, shared lib.

Nothing in this package may reference a language, framework, or estate. Profiles under
tools/graph/profiles/<estate>/ implement the SPIs; onboarding an estate never edits core.
Enforced by tools/graph/tests/test_core_genericity.py.
"""
from .model import Edge, ExtractResult, Node, Provenance, NAMESPACE, SCHEMA_VERSION
from .spi import Extractor, ExtractContext, LinkPass, NullSymbolLayer, RepoRef, SymbolLayer

__all__ = [
    "Edge", "ExtractResult", "Node", "Provenance", "NAMESPACE", "SCHEMA_VERSION",
    "Extractor", "ExtractContext", "LinkPass", "NullSymbolLayer", "RepoRef", "SymbolLayer",
]
