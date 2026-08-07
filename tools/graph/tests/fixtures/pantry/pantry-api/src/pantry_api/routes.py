"""HTTP surface for the pantry service.

The prefix lives on the router and the path on the decorator. Neither is the endpoint:
reading only the decorator yields `/items`, which no caller ever requests.
"""
from fastapi import APIRouter

from pantry_shared.models import Item

router = APIRouter(prefix="/pantry/v1")


@router.get("/items")
async def list_items() -> list[Item]:
    return []


@router.get("/items/{sku}")
async def get_item(sku: str) -> Item | None:
    return None
