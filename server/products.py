"""The apps that share the license service, and how each one is licensed.

CareerCraft users sign in to an account and then activate a key; Sidekick and
SnowLink activate with the key alone. Only CareerCraft reports presence (its
sync updates ``clients.last_seen``), so only its machines get an online dot in
the admin site; the others show when they last renewed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    # True: activate/refresh need a signed-in account (Bearer token).
    requires_account: bool
    # True: the admin site shows an online dot from sync presence.
    tracks_online: bool


DEFAULT_PRODUCT = "careercraft"

PRODUCTS: dict[str, Product] = {
    product.id: product
    for product in (
        Product("careercraft", "CareerCraft", requires_account=True, tracks_online=True),
        Product("sidekick", "Sidekick", requires_account=False, tracks_online=False),
        Product("snowlink", "SnowLink", requires_account=False, tracks_online=False),
    )
}


def get_product(product_id: str | None) -> Product | None:
    return PRODUCTS.get((product_id or "").strip().lower())


def product_name(product_id: str | None) -> str:
    product = get_product(product_id)
    return product.name if product else (product_id or "Unknown")
