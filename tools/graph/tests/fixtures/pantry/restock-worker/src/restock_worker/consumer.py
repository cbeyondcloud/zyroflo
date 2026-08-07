"""Drains the restock queue.

The queue is named by a DIFFERENT key than the publisher uses, and its value arrives from
an inline default rather than from the deployment layer. The two sides meet at the VALUE
and nowhere else: an extractor pairing producers to consumers by key name finds nothing
here and reports a queue with a producer and no consumer, which reads as a real
architectural finding rather than as a bug.
"""
import os

from confluent_kafka import Consumer

SOURCE_QUEUE = os.getenv("RESTOCK_SOURCE_QUEUE", "restock-events-v2")

_consumer = Consumer({"bootstrap.servers": "broker:9092", "group.id": "restock"})


def run() -> None:
    _consumer.subscribe([SOURCE_QUEUE])
    for sku in _drain():
        _record(sku)


def _drain() -> list[str]:
    return []


def _record(sku: str) -> None:
    del sku
