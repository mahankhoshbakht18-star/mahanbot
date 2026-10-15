import logging
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from database import DBHandler

DEFAULT_ALLOWED_DOMAINS = ("localhost", "127.0.0.1")
ALLOW_WILDCARDS = os.getenv("ALLOWED_DOMAINS_ALLOW_WILDCARDS", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


def normalize_domain(domain: str, allow_wildcards: bool = ALLOW_WILDCARDS) -> str:
    if not isinstance(domain, str):
        raise ValueError("domain must be a string")
    value = domain.strip().lower()
    if not value:
        raise ValueError("domain is empty")
    if "://" in value:
        raise ValueError("domain must not include a scheme")
    if any(token in value for token in ("/", "?", "#")):
        raise ValueError("domain must not include a path or query")
    if ":" in value:
        raise ValueError("domain must not include a port")
    if "*" in value:
        if not allow_wildcards:
            raise ValueError("wildcards are not allowed")
        if value != "*" and not value.startswith("*.") and not value.startswith("."):
            raise ValueError("wildcard format is invalid")
    if value.startswith(".") and not allow_wildcards:
        raise ValueError("wildcards are not allowed")

    candidate = value.lstrip("*").lstrip(".")
    if not candidate:
        raise ValueError("domain is invalid")
    if not re.match(r"^[a-z0-9.-]+$", candidate):
        raise ValueError("domain contains invalid characters")
    return value


def normalize_domain_list(
    domains: Iterable[str], allow_wildcards: bool = ALLOW_WILDCARDS
) -> Tuple[List[str], List[str]]:
    normalized: List[str] = []
    invalid: List[str] = []
    for item in domains:
        try:
            value = normalize_domain(item, allow_wildcards=allow_wildcards)
        except ValueError:
            invalid.append(str(item))
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized, invalid


def get_env_domains(allow_wildcards: bool = ALLOW_WILDCARDS) -> List[str]:
    raw = os.getenv("ALLOWED_DOMAINS")
    items = []
    if raw:
        items = raw.split(",")
    defaults, _ = normalize_domain_list(DEFAULT_ALLOWED_DOMAINS, allow_wildcards=allow_wildcards)
    parsed, invalid = normalize_domain_list(items, allow_wildcards=allow_wildcards)
    if invalid:
        logging.warning("Ignoring invalid ALLOWED_DOMAINS entries: %s", ", ".join(invalid))
    combined = defaults[:]
    for item in parsed:
        if item not in combined:
            combined.append(item)
    return combined


def get_db_domains(allow_wildcards: bool = ALLOW_WILDCARDS) -> List[str]:
    stored = DBHandler.get_setting("allowed_domains")
    if not isinstance(stored, list):
        return []
    parsed, _ = normalize_domain_list(stored, allow_wildcards=allow_wildcards)
    return parsed


def get_allowlist_snapshot(allow_wildcards: bool = ALLOW_WILDCARDS) -> Dict[str, List[str]]:
    env_domains = get_env_domains(allow_wildcards=allow_wildcards)
    db_domains = get_db_domains(allow_wildcards=allow_wildcards)
    effective = sorted({*env_domains, *db_domains})
    return {"env": env_domains, "db": db_domains, "effective": effective}


def get_allowed_domains(allow_wildcards: bool = ALLOW_WILDCARDS) -> List[str]:
    return get_allowlist_snapshot(allow_wildcards=allow_wildcards)["effective"]


def domain_matches(allowed: str, hostname: str) -> bool:
    allowed = allowed.lower()
    hostname = hostname.lower()
    if allowed == "*":
        return True
    if allowed.startswith("*."):
        return hostname.endswith(allowed.lstrip("*"))
    if allowed.startswith("."):
        return hostname.endswith(allowed)
    return hostname == allowed


def parse_url_host(url: str) -> Optional[str]:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname and "://" not in url:
        parsed = urlparse(f"http://{url}")
        hostname = parsed.hostname
    return hostname.lower() if hostname else None


def is_url_allowed(
    url: str, allowed_domains: Optional[Iterable[str]] = None
) -> Tuple[bool, Optional[str], List[str]]:
    host = parse_url_host(url)
    domains = list(allowed_domains or get_allowed_domains())
    if not host:
        return False, None, domains
    allowed = any(domain_matches(domain, host) for domain in domains)
    return allowed, host, domains
