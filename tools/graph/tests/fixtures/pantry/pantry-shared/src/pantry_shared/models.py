"""The shared model both services depend on.

A change here reaches every dependent, which is the whole reason a version-carrying
dependency edge exists.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    sku: str
    on_hand: int
