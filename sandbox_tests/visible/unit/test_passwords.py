from taskflow.api.seed import hash_password


def test_hash_is_deterministic_and_not_plaintext() -> None:
    assert hash_password("a") == hash_password("a")
    assert hash_password("a") != hash_password("b")
    assert "a" != hash_password("a")
