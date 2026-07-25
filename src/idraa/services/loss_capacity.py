"""Loss-cap minting for the PR2 capacity-bound epic (D13/D14).

D13 (owner-signed 2026-07-25): a single loss component is not modeled to
exceed one year of the owning org's revenue. The cap is
``k_capacity * Organization.annual_revenue``; ``k_capacity`` ships as
``Settings.capacity_k`` (default ``1.0``, a convention — see that field's
comment for the D13 caveat that D8 is a one-sided gate and does not itself
justify the value).

D14: no quantile-anchored fallback. A quantile anchor (e.g. "p99.9 of the
parent distribution") is scale-free — it removes the same tail slice from
every field regardless of scenario size, degrading small scenarios a
capacity cap would never touch (see design appendix B-CAP-ALT). So when
revenue is unknown or non-positive, :func:`capacity_max_for_org` returns
``None`` rather than inventing a number from some other statistic.

**Deliberate scope split — read before "helpfully" adding a floor here:**
This module mints a cap; it does NOT enforce the ``max > p95`` floor (D19).
A minter that silently raised a too-small cap up to the floor would invent
capacity the org does not have, and would do so silently. The floor is
enforced elsewhere, by design:
  - the FAIR-CAM validator (blocks at write time) — Task 3b;
  - operator-facing copy at the authoring surfaces — Tasks 4a/4b/4c;
  - a skip-with-WARNING at the backfill migration — Task 5.
Do not add a floor, clamp, or silent adjustment to the minter in this file.
"""

from __future__ import annotations

from decimal import Decimal


def capacity_max_for_org(annual_revenue: float | Decimal | None, k: float) -> float | None:
    """Mint the per-loss-component cap ``k * annual_revenue`` (D13).

    Returns ``None`` — never an invented number (D14) — when
    ``annual_revenue`` is ``None`` or ``<= 0``. ``annual_revenue`` commonly
    arrives as a ``Decimal`` (``Organization.annual_revenue`` is a
    SQLAlchemy ``Numeric`` column): it is converted to ``float`` BEFORE
    multiplying, because ``float * Decimal`` raises ``TypeError`` in Python.
    """
    if annual_revenue is None:
        return None
    revenue = float(annual_revenue)
    if revenue <= 0:
        return None
    return k * revenue
