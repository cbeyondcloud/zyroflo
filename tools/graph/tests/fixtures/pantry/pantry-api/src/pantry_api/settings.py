"""Settings, spelled three ways on purpose.

`RESTOCK_QUEUE`, `restock.queue` and `restock-queue` are one setting under relaxed binding.
A reader that treats them as three keys grows two phantom queues beside the real one, and
each phantom looks exactly as real as the topic that actually exists.
"""
import os

QUEUE_ENV = os.environ["RESTOCK_QUEUE"]
QUEUE_DOTTED = os.environ.get("restock.queue", "")
QUEUE_DASHED = os.environ.get("restock-queue", "")

DB_DSN = os.environ["PANTRY_DB_DSN"]
