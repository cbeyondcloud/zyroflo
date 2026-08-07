"""Route tests. Names the endpoint twice, and must never become a caller of it.

A test naming a path is a `DG_TESTS` edge and nothing else. Counting it as an inbound call
invents a caller that appears in every blast radius for this endpoint, forever, and looks
exactly like a real one.
"""
# Covers /pantry/v1/items — the list route.
from pantry_api.routes import list_items


async def test_list_items_is_empty():
    assert await list_items() == []


def test_mock_path_is_not_a_caller():
    mock_url = "/pantry/v1/items"
    assert mock_url.startswith("/pantry")
