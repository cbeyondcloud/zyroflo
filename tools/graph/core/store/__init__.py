"""Graph storage seam. Import `make_store` rather than a concrete backend."""
from .graphstore import GraphStore, Neighbor, make_store

__all__ = ["GraphStore", "Neighbor", "make_store"]
