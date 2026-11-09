"""Each license key's color on the admin site.

The color is picked once, when the key is issued, so one customer's keys look
different from each other on every tab (Keys, Machines, Events). The admin
site keeps the same list of colors (``keyColors`` in
``web/src/react/pages/licenses/licenseUi.tsx``).
"""

from __future__ import annotations

import zlib
from typing import Iterable

KEY_COLORS = 10


def customer_group(customer: str | None) -> str:
    """Keys of the same customer, whatever the spacing or capitals."""
    return " ".join((customer or "").split()).lower()


def pick_key_color(used: Iterable[int | None], customer: str | None) -> int:
    """The color this customer's other keys use least.

    Ties go to the first color from a starting point that depends on the
    customer, so different customers don't all start on the same color.
    """
    counts = [0] * KEY_COLORS
    for color in used:
        if color is not None and 0 <= color < KEY_COLORS:
            counts[color] += 1
    start = zlib.crc32(customer_group(customer).encode("utf-8")) % KEY_COLORS
    order = [(start + step) % KEY_COLORS for step in range(KEY_COLORS)]
    return min(order, key=lambda color: counts[color])
