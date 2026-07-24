"""Selezione centralizzata del motore di spedizione."""
from __future__ import annotations

from enum import Enum

import config


class ShippingEngine(str, Enum):
    LEGACY = "LEGACY"
    V2 = "V2"


def get_shipping_engine() -> ShippingEngine:
    """Restituisce V2 solo quando entrambi i flag sono esplicitamente attivi."""
    if config.is_shipping_v2_activation_allowed():
        return ShippingEngine.V2
    return ShippingEngine.LEGACY


def is_shipping_v2_active() -> bool:
    return get_shipping_engine() is ShippingEngine.V2
