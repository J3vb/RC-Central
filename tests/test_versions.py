from app.versions import is_newer


def test_is_newer_basic():
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("2.0.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("1.0.0", "1.0.1")


def test_is_newer_tolerates_v_prefix():
    assert is_newer("v0.4.0", "v0.3.0")
    assert is_newer("0.4.0", "v0.3.0")
    assert not is_newer("V0.3.0", "0.3.0")


def test_is_newer_malformed_is_not_newer():
    # a non-numeric tag must never masquerade as an update
    assert not is_newer("nightly", "0.3.0")
    assert not is_newer("0.3.0", "latest")
