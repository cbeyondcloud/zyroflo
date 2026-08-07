"""Calls back into the pantry API.

The host comes from the environment and the path is a literal here. Neither half is an
endpoint on its own: pairing them is what produces a call edge, and an unpaired half
belongs in `unresolved` rather than dropped.
"""
import os

import httpx

API_BASE = os.environ["PANTRY_API_URL"]

ITEMS_PATH = "/pantry/v1/items"
ITEM_PATH = "/pantry/v1/items/{item_sku}"


def fetch_items() -> list[dict]:
    return httpx.get(f"{API_BASE}{ITEMS_PATH}").json()
