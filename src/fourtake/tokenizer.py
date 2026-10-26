try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


def estimate_tokens(text: str) -> int:
    """
    Estimates the number of tokens in the given text.
    If tiktoken is available, uses cl100k_base.
    Otherwise, falls back to a naive len(text) // 4.
    """
    if HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text, allowed_special="all"))
        except Exception:
            pass  # Fallback to naive if encoding fails

    # Naive fallback
    return max(1, len(text) // 4)
