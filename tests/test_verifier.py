"""Verifier tests with a fake DNS and a fake SMTP server (no network)."""

import smtplib

from bugmail.verifier import Verifier, MXInfo, UnsafeHost

MX = {
    "good.test": MXInfo("ok", ("mx1.good.test", "mx2.good.test")),
    "catchall.test": MXInfo("ok", ("mx.catchall.test",)),
    "down.test": MXInfo("ok", ("mx.down.test",)),
    "gone.test": MXInfo("nxdomain"),
    "nomail.test": MXInfo("nomx"),
    "flaky-dns.test": MXInfo("error"),
    "private.test": MXInfo("ok", ("mx.private.test",)),
    "strict.test": MXInfo("ok", ("mx.strict.test",)),
    "gmial.com": MXInfo("nxdomain"),
}
EXISTING = {"ana@good.test", "info@good.test", "luis@catchall.test", "full@good.test"}


class FakeSMTP:
    def __init__(self):
        self.calls = []

    def __call__(self, host, rcpts):
        self.calls.append((host, list(rcpts)))
        if host == "mx.down.test":
            raise OSError("connection refused")
        if host == "mx1.good.test":
            raise smtplib.SMTPConnectError(421, "busy")          # first MX fails, second works
        if host == "mx.private.test":
            raise UnsafeHost(host)
        if host == "mx.strict.test":
            raise smtplib.SMTPSenderRefused(550, b"sender not allowed", "")
        out = []
        for r in rcpts:
            domain = r.split("@")[1]
            if domain == "catchall.test":
                out.append((250, "ok"))
            elif r == "full@good.test":
                out.append((552, "mailbox full"))
            elif r in EXISTING:
                out.append((250, "ok"))
            elif r.startswith("greylist@"):
                out.append((451, "try later"))
            elif r.startswith("spam@"):
                out.append((550, "blocked by spam policy"))
            else:
                out.append((550, "5.1.1 user unknown"))
        return out


def make(smtp=True, fake=None):
    fake = fake or FakeSMTP()
    v = Verifier(smtp=smtp, resolve_mx=lambda d: MX.get(d, MXInfo("nxdomain")), smtp_probe=fake,
                 min_interval=0, sleep=lambda s: None)
    return v, fake


def test_valid_address_uses_second_mx_when_first_fails():
    v, fake = make()
    res = v.check("Ana@Good.test")
    assert (res.status, res.reason, res.mx) == ("valid", "accepted", "mx2.good.test")
    assert [c[0] for c in fake.calls] == ["mx1.good.test", "mx2.good.test"]


def test_unknown_mailbox_is_invalid():
    v, _ = make()
    assert v.check("nadie@good.test").reason == "mailbox_not_found"
    assert v.check("nadie@good.test").status == "invalid"


def test_role_account_is_risky_even_when_accepted():
    v, _ = make()
    res = v.check("info@good.test")
    assert (res.status, res.reason) == ("risky", "role")


def test_catch_all_domain_is_risky():
    v, _ = make()
    res = v.check("luis@catchall.test")
    assert (res.status, res.reason, res.catch_all) == ("risky", "catch_all", True)


def test_catch_all_probe_runs_once_per_domain():
    v, fake = make()
    v.check("ana@good.test")
    v.check("nadie@good.test")
    probes = [c for c in fake.calls if c[0] == "mx2.good.test"]
    assert len(probes[0][1]) == 2          # real address + random probe
    assert len(probes[1][1]) == 1          # cached afterwards


def test_mailbox_full_greylisting_and_policy_blocks():
    v, _ = make()
    assert v.check("full@good.test").reason == "mailbox_full"
    assert (v.check("greylist@good.test").status, v.check("greylist@good.test").reason) == ("unknown", "temporary_failure")
    assert v.check("spam@good.test").reason == "blocked_by_server"


def test_dns_outcomes():
    v, _ = make()
    assert v.check("x@gone.test").reason == "domain_not_found"
    assert v.check("x@nomail.test").reason == "no_mail_server"
    assert (v.check("x@flaky-dns.test").status, v.check("x@flaky-dns.test").reason) == ("unknown", "dns_error")


def test_typo_domain_gets_suggestion_even_if_it_exists():
    v, fake = make()
    res = v.check("ana@gmial.com")
    assert (res.status, res.reason, res.suggestion) == ("risky", "possible_typo", "ana@gmail.com")
    assert fake.calls == []


def test_regional_variants_are_not_typos():
    v, _ = make(smtp=False)
    MX["hotmail.com.ar"] = MXInfo("ok", ("mx.hotmail.test",))
    assert v.check("ana@hotmail.com.ar").reason == "domain_only"


def test_syntax_and_disposable_never_touch_network():
    v, fake = make()
    assert v.check("not-an-email").reason == "syntax"
    assert v.check("x@mailinator.com").reason == "disposable"
    assert fake.calls == []


def test_unreachable_private_and_refused_sender():
    v, _ = make()
    assert v.check("x@down.test").reason == "smtp_unreachable"
    assert v.check("x@private.test").reason == "mx_not_public"
    assert v.check("x@strict.test").reason == "sender_refused"


def test_no_smtp_mode_checks_domain_only():
    v, fake = make(smtp=False)
    assert v.check("ana@good.test").reason == "domain_only"
    assert v.check("info@good.test").reason == "role"
    assert fake.calls == []


def test_check_many_dedupes_and_is_case_insensitive():
    v, _ = make()
    out = v.check_many(["ana@good.test", "ANA@good.test ", "", "nadie@good.test"], workers=3)
    assert sorted(out) == ["ana@good.test", "nadie@good.test"]


def test_mx_lookup_is_cached():
    calls = []
    v = Verifier(smtp=False, resolve_mx=lambda d: calls.append(d) or MX["good.test"], min_interval=0)
    v.check("a@good.test")
    v.check("b@good.test")
    assert calls == ["good.test"]
