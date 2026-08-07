"""Publishes restock requests.

The queue name is not in this repository. It is read from the environment, and the value
is written in the deployment layer — which is the point: an extractor that only reads the
component's own files sees a name it cannot resolve and stops one hop short of the worker.
"""
import os

from pantry_shared.models import Item

QUEUE = os.environ["RESTOCK_QUEUE"]

# Referenced, and set by nothing anywhere in this workspace. See E7.
LEDGER = os.environ["LEDGER_ENDPOINT"]


def publish(item: Item) -> None:
    _send(QUEUE, item.sku)


def _send(queue: str, payload: str) -> None:
    del queue, payload
