def normalize_email(address):
    """Canonical form used for every lookup: login, password reset, invitations."""
    local, _, domain = address.strip().lower().partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"
