"""E-mail verification: DNS (MX) + SMTP handshake + catch-all detection, cached per domain."""

import ipaddress
import re
import secrets
import smtplib
import socket
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import dns.exception
import dns.resolver

from . import checks

VALID, INVALID, RISKY, UNKNOWN = "valid", "invalid", "risky", "unknown"

_POLICY_WORDS = re.compile(r"spam|block|blacklist|denylist|rbl|policy|denied|reputation|not allowed|refused", re.I)


@dataclass
class Result:
    email: str
    status: str
    reason: str
    suggestion: str = ""
    catch_all: Optional[bool] = None
    mx: str = ""
    detail: str = field(default="", repr=False)

    def as_dict(self):
        return asdict(self)


@dataclass
class MXInfo:
    kind: str                      # "ok" | "nxdomain" | "nomx" | "error"
    hosts: tuple = ()


class UnsafeHost(Exception):
    """The mail server resolves to a non-public address (loopback, LAN, link-local...)."""


# --------------------------------------------------------------------------- defaults (network)

def default_resolve_mx(domain: str, timeout: float = 8.0) -> MXInfo:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    try:
        answer = resolver.resolve(domain, "MX")
        hosts = tuple(r.exchange.to_text().rstrip(".") for r in sorted(answer, key=lambda r: r.preference))
        hosts = tuple(h for h in hosts if h)          # a null MX ("." per RFC 7505) means "no mail"
        return MXInfo("ok", hosts) if hosts else MXInfo("nomx")
    except dns.resolver.NXDOMAIN:
        return MXInfo("nxdomain")
    except dns.resolver.NoAnswer:
        # RFC 5321: with no MX record, the domain's A/AAAA record acts as the mail server.
        try:
            resolver.resolve(domain, "A")
            return MXInfo("ok", (domain,))
        except dns.exception.DNSException:
            return MXInfo("nomx")
    except dns.exception.DNSException:
        return MXInfo("error")


def _assert_public(host: str):
    for info in socket.getaddrinfo(host, 25, proto=socket.IPPROTO_TCP):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise UnsafeHost(host)


def make_smtp_probe(helo: str, sender: str, timeout: float, allow_private: bool):
    """Return probe(host, rcpts) -> [(code, message), ...] using ONE SMTP session for all rcpts."""

    def probe(host, rcpts):
        if not allow_private:
            _assert_public(host)
        server = smtplib.SMTP(timeout=timeout)
        try:
            server.connect(host, 25)
            code, _ = server.ehlo(helo)
            if code != 250:
                code, _ = server.helo(helo)
            if code != 250:
                raise smtplib.SMTPHeloError(code, "HELO rejected")
            code, msg = server.mail(sender)
            if code != 250:
                raise smtplib.SMTPSenderRefused(code, msg, sender)
            return [(c, m.decode(errors="replace")) for c, m in (server.rcpt(r) for r in rcpts)]
        finally:
            try:
                server.close()
            except Exception:
                pass

    return probe


# --------------------------------------------------------------------------- verifier

class Verifier:
    def __init__(self, *, smtp=True, helo=None, sender="", timeout=10.0, max_mx=3,
                 min_interval=1.0, allow_private=False, extra_disposable=frozenset(),
                 resolve_mx: Callable = None, smtp_probe: Callable = None, sleep=time.sleep):
        self.smtp = smtp
        self.max_mx = max_mx
        self.min_interval = min_interval
        self.extra_disposable = frozenset(d.lower() for d in extra_disposable)
        self._resolve_mx = resolve_mx or default_resolve_mx
        self._probe = smtp_probe or make_smtp_probe(helo or socket.getfqdn(), sender, timeout, allow_private)
        self._sleep = sleep
        self._mx_cache, self._catch_all_cache = {}, {}
        self._locks, self._locks_guard = {}, threading.Lock()
        self._last_hit = {}

    # -- caches / throttling
    def _lock(self, key):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _mx(self, domain) -> MXInfo:
        with self._lock(("mx", domain)):
            if domain not in self._mx_cache:
                self._mx_cache[domain] = self._resolve_mx(domain)
            return self._mx_cache[domain]

    def _throttle(self, host):
        """Serialize connections per mail server and keep `min_interval` seconds between them."""
        lock = self._lock(("host", host))
        lock.acquire()
        wait = self._last_hit.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        return lock

    def _ask(self, hosts, rcpts):
        """Try up to `max_mx` servers; return (host, [(code,msg)...]) or (host, None) if all unreachable."""
        last = hosts[0]
        for host in hosts[: self.max_mx]:
            last = host
            lock = self._throttle(host)
            try:
                return host, self._probe(host, rcpts)
            except UnsafeHost:
                return host, "unsafe"
            except smtplib.SMTPSenderRefused:
                return host, "refused"
            except (OSError, smtplib.SMTPException):
                continue
            finally:
                self._last_hit[host] = time.monotonic()
                lock.release()
        return last, None

    # -- public API
    def check(self, email: str) -> Result:
        email = checks.normalize(email)
        if not checks.syntax_ok(email):
            return Result(email, INVALID, "syntax")
        local, domain = checks.split_address(email)
        if checks.is_disposable(domain, self.extra_disposable):
            return Result(email, RISKY, "disposable")

        typo = _suggest(local, domain)
        if typo:                    # typo domains often exist (gmial.com) and even have mail servers
            return Result(email, RISKY, "possible_typo", suggestion=typo)

        mx = self._mx(domain)
        if mx.kind in ("nxdomain", "nomx"):
            reason = "domain_not_found" if mx.kind == "nxdomain" else "no_mail_server"
            return Result(email, INVALID, reason)
        if mx.kind == "error":
            return Result(email, UNKNOWN, "dns_error")

        role = checks.is_role(email)
        if not self.smtp:
            return Result(email, RISKY if role else VALID, "role" if role else "domain_only", mx=mx.hosts[0])

        key = ("catch_all", domain)
        with self._lock(key):
            need_probe = domain not in self._catch_all_cache
            rcpts = [email] + ([f"bugmail-{secrets.token_hex(6)}@{domain}"] if need_probe else [])
            host, answers = self._ask(mx.hosts, rcpts)
            if answers == "unsafe":
                return Result(email, INVALID, "mx_not_public", mx=host)
            if answers == "refused":
                return Result(email, UNKNOWN, "sender_refused", mx=host)
            if answers is None:
                return Result(email, UNKNOWN, "smtp_unreachable", mx=host)
            if need_probe:
                self._catch_all_cache[domain] = answers[1][0] in (250, 251)
            catch_all = self._catch_all_cache[domain]

        code, message = answers[0]
        return _classify(email, code, message, catch_all, role, host)

    def check_many(self, emails, workers=8, on_result: Callable = None):
        """Check unique addresses concurrently; returns {normalized_email: Result}."""
        from concurrent.futures import ThreadPoolExecutor

        unique = list(dict.fromkeys(checks.normalize(e) for e in emails if e and e.strip()))
        out = {}

        def work(addr):
            res = self.check(addr)
            out[addr] = res
            if on_result:
                on_result(res)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(work, unique))
        return out


def _suggest(local, domain):
    s = checks.suggest_domain(domain)
    return f"{local}@{s}" if s else ""


def _classify(email, code, message, catch_all, role, host) -> Result:
    base = dict(catch_all=catch_all, mx=host, detail=message[:200])
    if code in (250, 251):
        if catch_all:
            return Result(email, RISKY, "catch_all", **base)
        if role:
            return Result(email, RISKY, "role", **base)
        return Result(email, VALID, "accepted", **base)
    if 400 <= code < 500:
        return Result(email, UNKNOWN, "temporary_failure", **base)
    if code == 552:
        return Result(email, RISKY, "mailbox_full", **base)
    if 500 <= code < 600:
        if _POLICY_WORDS.search(message):
            return Result(email, UNKNOWN, "blocked_by_server", **base)
        return Result(email, INVALID, "mailbox_not_found", **base)
    return Result(email, UNKNOWN, "unexpected_reply", **base)
