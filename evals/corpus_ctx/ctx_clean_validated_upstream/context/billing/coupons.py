from decimal import Decimal

from billing.discounts import _apply_percentage

MAX_PERCENT = Decimal(50)


def apply_coupon(order, coupon):
    """Coupons are the only source of percentage discounts."""
    percent = Decimal(coupon.percent)
    if not (Decimal(0) < percent <= MAX_PERCENT):
        raise ValueError(f"coupon {coupon.code} has an invalid percentage")
    order.total = _apply_percentage(order.total, percent)
