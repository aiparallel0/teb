"""In-memory caching layer with optional Redis backend.

Default implementation uses a simple dict with TTL support.
Set REDIS_URL environment variable to enable Redis caching.
"""
from __future__ import annotations

import time
from typing import Any, Optional


class CacheLayer:
    """Simple cache with TTL support.

    Uses an in-memory dict by default.  To switch to Redis:
    1. ``pip install redis``
    2. Set ``REDIS_URL`` env var (e.g. ``redis://localhost:6379/0``)
    3. Instantiate ``RedisCacheLayer`` instead of ``CacheLayer``
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expires_at)
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None if missing / expired."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, expires_at = entry
        if expires_at and time.monotonic() > expires_at:
            del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Store a value with a TTL in seconds (default 5 min)."""
        expires_at = time.monotonic() + ttl if ttl > 0 else 0.0
        self._store[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        """Remove a key from the cache."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        """Return cache statistics."""
        # Purge expired entries for an accurate count
        now = time.monotonic()
        expired_keys = [
            k for k, (_, exp) in self._store.items() if exp and now > exp
        ]
        for k in expired_keys:
            del self._store[k]

        return {
            "backend": "memory",
            "keys": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 3),
        }


# Singleton cache instance
_cache = CacheLayer()


def get_cache() -> CacheLayer:
    """Return the global cache instance."""
    return _cache
