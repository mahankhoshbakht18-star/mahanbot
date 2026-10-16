"""
proxy_manager.py — Advanced Proxy Pool, Health Checker & Rotation Manager for MAHANBOT
Supports HTTP, HTTPS, and SOCKS5 proxies with credentials, health checking,
latency measurement, per-applicant sticky binding, and failover rotation.
"""
import re
import time
import socket
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("mahanbot.proxy")


def parse_proxy_string(raw: str) -> Optional[Dict[str, Any]]:
    """
    Parse various proxy string formats into a normalized Playwright proxy dict.
    Supported formats:
      - protocol://username:password@host:port
      - protocol://host:port
      - host:port:username:password
      - host:port
    Returns:
      {"server": "protocol://host:port", "username": "...", "password": "...", "raw": "...", "host": "...", "port": ...}
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.startswith("#"):
        return None

    protocol = "http"
    username: Optional[str] = None
    password: Optional[str] = None
    host = ""
    port = 8080

    if "://" in s:
        parsed = urlparse(s)
        protocol = parsed.scheme.lower() or "http"
        host = parsed.hostname or ""
        port = parsed.port or (1080 if "socks" in protocol else 8080)
        username = parsed.username
        password = parsed.password
    else:
        parts = s.split(":")
        if len(parts) == 4:
            # host:port:user:pass
            host, port_str, username, password = parts
            try:
                port = int(port_str)
            except ValueError:
                return None
        elif len(parts) == 2:
            # host:port
            host, port_str = parts
            try:
                port = int(port_str)
            except ValueError:
                return None
        else:
            return None

    if not host or not port:
        return None

    server_url = f"{protocol}://{host}:{port}"
    result: Dict[str, Any] = {
        "server": server_url,
        "protocol": protocol,
        "host": host,
        "port": port,
        "raw": s,
    }
    if username and password:
        result["username"] = username
        result["password"] = password

    return result


def check_proxy_health(proxy_dict: Dict[str, Any], timeout: float = 4.0) -> Dict[str, Any]:
    """
    Perform a lightweight TCP/HTTP connect ping to verify proxy availability and latency.
    """
    host = proxy_dict.get("host", "")
    port = int(proxy_dict.get("port", 8080))
    t0 = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        latency = round((time.time() - t0) * 1000, 1)
        sock.close()
        return {
            "healthy": True,
            "latency_ms": latency,
            "server": proxy_dict.get("server", ""),
            "raw": proxy_dict.get("raw", ""),
            "error": None,
        }
    except Exception as exc:
        latency = round((time.time() - t0) * 1000, 1)
        return {
            "healthy": False,
            "latency_ms": latency,
            "server": proxy_dict.get("server", ""),
            "raw": proxy_dict.get("raw", ""),
            "error": str(exc),
        }


class ProxyPool:
    """Thread-safe proxy pool with health tracking, cooldowns, and multiple rotation strategies."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proxies: List[Dict[str, Any]] = []
        self._rr_index = 0
        self._cooldowns: Dict[str, float] = {}
        self._health_cache: Dict[str, Dict[str, Any]] = {}
        self._raw_cache_key = ""

    def load_from_text(self, proxy_text: str) -> None:
        """Parse proxy list text (one proxy per line)."""
        with self._lock:
            if proxy_text == self._raw_cache_key:
                return
            self._raw_cache_key = proxy_text
            parsed_list = []
            for line in (proxy_text or "").splitlines():
                parsed = parse_proxy_string(line)
                if parsed:
                    parsed_list.append(parsed)
            self._proxies = parsed_list
            self._rr_index = 0

    def get_proxy(
        self,
        strategy: str = "round_robin",
        applicant_id: Optional[str] = None,
        exclude_servers: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get next eligible proxy according to strategy.
        Strategies:
          - 'round_robin': cycles through healthy proxies.
          - 'per_applicant': binds applicant_id to a deterministic proxy in the pool.
          - 'random_healthy': random choice from currently healthy proxies.
        """
        with self._lock:
            if not self._proxies:
                return None

            now = time.time()
            exclude = set(exclude_servers or [])
            # Filter proxies that are not in active cooldown or excluded
            available = [
                p for p in self._proxies
                if p["server"] not in exclude and self._cooldowns.get(p["server"], 0) <= now
            ]
            if not available:
                # If all available are on cooldown, reset cooldowns and use all
                available = [p for p in self._proxies if p["server"] not in exclude]
                if not available:
                    available = self._proxies

            if strategy == "per_applicant" and applicant_id:
                # Hash applicant ID to get deterministic proxy index
                h = abs(hash(str(applicant_id)))
                chosen = available[h % len(available)]
            elif strategy == "random_healthy":
                import random
                chosen = random.choice(available)
            else:
                # round_robin
                chosen = available[self._rr_index % len(available)]
                self._rr_index = (self._rr_index + 1) % len(available)

            return {
                "server": chosen["server"],
                "username": chosen.get("username"),
                "password": chosen.get("password"),
                "raw": chosen.get("raw"),
            }

    def mark_failure(self, server: str, cooldown_seconds: float = 60.0) -> None:
        """Mark a proxy as failed and place it on temporary cooldown."""
        with self._lock:
            self._cooldowns[server] = time.time() + cooldown_seconds
            logger.warning("Proxy %s placed on cooldown for %ss due to connection failure", server, cooldown_seconds)

    def mark_success(self, server: str) -> None:
        """Clear cooldown on successful operation."""
        with self._lock:
            self._cooldowns.pop(server, None)

    def test_all(self, timeout: float = 4.0) -> List[Dict[str, Any]]:
        """Run concurrent health checks on all proxies in the pool."""
        with self._lock:
            candidates = list(self._proxies)

        if not candidates:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=min(16, len(candidates))) as executor:
            future_to_proxy = {executor.submit(check_proxy_health, p, timeout): p for p in candidates}
            for future in as_completed(future_to_proxy):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    p = future_to_proxy[future]
                    results.append({
                        "healthy": False,
                        "latency_ms": 0,
                        "server": p.get("server", ""),
                        "raw": p.get("raw", ""),
                        "error": str(e),
                    })

        with self._lock:
            for r in results:
                self._health_cache[r["server"]] = r
                if not r["healthy"]:
                    self._cooldowns[r["server"]] = time.time() + 60.0
                else:
                    self._cooldowns.pop(r["server"], None)

        return results

    def get_pool_status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            total = len(self._proxies)
            cooldown_count = sum(1 for exp in self._cooldowns.values() if exp > now)
            return {
                "total_count": total,
                "active_count": max(0, total - cooldown_count),
                "cooldown_count": cooldown_count,
                "proxies": [
                    {
                        "server": p["server"],
                        "raw": p["raw"],
                        "on_cooldown": self._cooldowns.get(p["server"], 0) > now,
                        "last_health": self._health_cache.get(p["server"]),
                    }
                    for p in self._proxies
                ],
            }


_GLOBAL_PROXY_POOL = ProxyPool()


def get_proxy_pool() -> ProxyPool:
    return _GLOBAL_PROXY_POOL
