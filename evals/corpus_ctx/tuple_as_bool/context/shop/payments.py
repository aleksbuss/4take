from shop.gateway import gateway


def charge(card, amount):
    """Charge `card`. Returns (ok, error); on a decline ok is False and error is the decline code."""
    response = gateway.post("/charges", {"card": card.token, "amount": str(amount)})
    if response.status != 201:
        return False, response.body.get("decline_code", "unknown")
    return True, None
