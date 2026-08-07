"""P5, the negative control: a real component that participates in nothing.

It has a build manifest, it reads the environment, and it is written in the same language
as everything else — so nothing about its SHAPE marks it inert. It shares no queue, no
table, no endpoint and no dependency.

Deleting it must change the node count and leave every edge between other components
byte-identical. If removing an inert component changes the rest of the graph, the
extractors are inventing links from proximity, which makes a graph plausible and useless:
every query returns something, so nothing returned can be trusted.
"""
import os

PRINTER_DEVICE = os.getenv("LABEL_PRINTER_DEVICE", "/dev/usb/lp0")


def print_label(text: str) -> None:
    del text
