import importlib
import sys
from unittest.mock import patch

from fourtake.tokenizer import estimate_tokens


def test_estimate_tokens_basic():
    count = estimate_tokens("hello world")
    assert count > 0


def test_estimate_tokens_empty():
    count = estimate_tokens("")
    assert count == 0


def test_estimate_tokens_large():
    text = "a " * 1000
    count = estimate_tokens(text)
    assert count > 500


def test_estimate_tokens_fallback_no_tiktoken(monkeypatch):
    import fourtake.tokenizer as tok

    monkeypatch.setattr(tok, "HAS_TIKTOKEN", False)
    assert tok.estimate_tokens("12345678") == 2
    assert tok.estimate_tokens("") == 1


def test_estimate_tokens_encoding_exception(monkeypatch):
    import tiktoken

    import fourtake.tokenizer as tok

    monkeypatch.setattr(tok, "HAS_TIKTOKEN", True)
    monkeypatch.setattr(
        tiktoken, "get_encoding", lambda _: (_ for _ in ()).throw(RuntimeError("Encoding error"))
    )
    assert tok.estimate_tokens("12345678") == 2


def test_tokenizer_import_error():
    with patch.dict(sys.modules, {"tiktoken": None}):
        import fourtake.tokenizer as tok

        importlib.reload(tok)
        assert tok.HAS_TIKTOKEN is False
        assert tok.estimate_tokens("12345678") == 2
    importlib.reload(tok)
    assert tok.HAS_TIKTOKEN is True
