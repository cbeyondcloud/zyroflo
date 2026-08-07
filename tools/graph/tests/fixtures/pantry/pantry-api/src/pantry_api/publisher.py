"""Publishes restock requests.

The queue name is not in this repository. It is read from the environment, and the value is
written in the deployment layer — which is the point: an extractor that only reads this
component's own files sees a name it cannot resolve and stops one hop short of the worker.

The client call is a real one. An earlier draft of this file called a local `_send` stub,
which left NOTHING marking this as a producer except the word "QUEUE" inside a variable
name — and reading intent out of a naming convention is the guess-in-a-fact's-clothing this
workspace refuses everywhere else.
"""
import os

from confluent_kafka import Producer

from pantry_shared.models import Item

QUEUE = os.environ["RESTOCK_QUEUE"]

# Referenced, and set by nothing anywhere in this workspace. See E7.
LEDGER = os.environ["LEDGER_ENDPOINT"]

_producer = Producer({"bootstrap.servers": "broker:9092"})


def publish(item: Item) -> None:
    _producer.produce(QUEUE, key=item.sku, value=str(item.on_hand))
