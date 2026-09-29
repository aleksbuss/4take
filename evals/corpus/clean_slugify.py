import re
import unicodedata

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 80) -> str:
    """Lowercase ASCII slug, collapsed dashes, never longer than max_length."""
    if max_length < 1:
        raise ValueError("max_length must be positive")
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", ascii_only).strip("-")
    if len(slug) <= max_length:
        return slug
    return slug[:max_length].rstrip("-")
