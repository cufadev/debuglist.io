"""Offline checks: syntax, role accounts, disposable domains, typo suggestions."""

import difflib
import re

# Pragmatic syntax check (not the full RFC 5322): local part + dotted domain with a 2+ letter TLD.
_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$")
_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")

ROLE_ACCOUNTS = frozenset({
    "info", "admin", "administracion", "contacto", "contact", "ventas", "sales", "soporte",
    "support", "hola", "hello", "reservas", "reservations", "recepcion", "reception",
    "webmaster", "postmaster", "abuse", "noreply", "no-reply", "donotreply", "mail",
    "office", "oficina", "marketing", "prensa", "press", "billing", "facturacion",
})

DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com",
    "maildrop.cc", "throwawaymail.com", "fakeinbox.com", "mintemail.com", "mailnesia.com",
})

COMMON_DOMAINS = (
    "gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "yahoo.com",
    "yahoo.com.ar", "yahoo.es", "icloud.com", "live.com", "msn.com", "protonmail.com",
    "gmx.com", "aol.com", "fibertel.com.ar", "speedy.com.ar", "arnet.com.ar",
    # real regional variants, listed so they are not flagged as typos of the .com ones
    "hotmail.com.ar", "outlook.com.ar", "live.com.ar", "yahoo.com.mx", "hotmail.com.mx",
    "hotmail.com.br", "hotmail.cl", "hotmail.it", "hotmail.fr", "yahoo.fr", "yahoo.it",
)


def split_address(email: str):
    """Return (local, domain) or None when the address has no single '@'."""
    if email.count("@") != 1:
        return None
    local, domain = email.split("@")
    return (local, domain.lower()) if local and domain else None


def syntax_ok(email: str) -> bool:
    parts = split_address(email)
    if parts is None or len(email) > 254:
        return False
    local, domain = parts
    if not _LOCAL.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return False
    return labels[-1].isalpha() and len(labels[-1]) >= 2


def is_role(email: str) -> bool:
    parts = split_address(email)
    return bool(parts) and parts[0].lower() in ROLE_ACCOUNTS


def is_disposable(domain: str, extra=frozenset()) -> bool:
    return domain.lower() in DISPOSABLE_DOMAINS or domain.lower() in extra


def suggest_domain(domain: str):
    """Suggest a well-known domain when `domain` looks like a typo of one (gmial.com -> gmail.com)."""
    domain = domain.lower()
    if domain in COMMON_DOMAINS:
        return None
    match = difflib.get_close_matches(domain, COMMON_DOMAINS, n=1, cutoff=0.82)
    return match[0] if match else None


def normalize(email: str) -> str:
    return email.strip().strip("<>").strip().lower()
