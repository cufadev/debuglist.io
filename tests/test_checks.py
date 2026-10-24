import pytest

from bugmail import checks


@pytest.mark.parametrize("addr", ["ana@example.com", "a.b+tag@sub.example.com.ar", "x_y-z@ex-ample.org"])
def test_syntax_accepts_normal_addresses(addr):
    assert checks.syntax_ok(addr)


@pytest.mark.parametrize("addr", [
    "", "ana", "ana@", "@example.com", "ana@@example.com", "a na@example.com", ".ana@example.com",
    "ana.@example.com", "an..a@example.com", "ana@example", "ana@example.c", "ana@-example.com",
    "ana@example.123", "a" * 65 + "@example.com",
])
def test_syntax_rejects_bad_addresses(addr):
    assert not checks.syntax_ok(addr)


def test_role_and_disposable():
    assert checks.is_role("info@example.com")
    assert checks.is_role("Ventas@example.com")
    assert not checks.is_role("ana@example.com")
    assert checks.is_disposable("mailinator.com")
    assert checks.is_disposable("custom-temp.test", extra=frozenset({"custom-temp.test"}))
    assert not checks.is_disposable("example.com")


@pytest.mark.parametrize("typo,fixed", [
    ("gmial.com", "gmail.com"), ("gmail.con", "gmail.com"), ("hotmial.com", "hotmail.com"),
    ("yaho.com", "yahoo.com"),
])
def test_typo_suggestions(typo, fixed):
    assert checks.suggest_domain(typo) == fixed


def test_no_suggestion_for_correct_or_unrelated_domains():
    assert checks.suggest_domain("gmail.com") is None
    assert checks.suggest_domain("hotelspa-ficticio.com") is None


def test_normalize():
    assert checks.normalize("  <Ana@Example.COM> ") == "ana@example.com"
