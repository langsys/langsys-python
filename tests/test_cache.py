import time

from langsys.cache import FileCache, MemoryCache, NullCache


def _roundtrip(cache):
    assert cache.get("k") is None
    cache.set("k", {"a": 1})
    assert cache.get("k") == {"a": 1}
    cache.delete("k")
    assert cache.get("k") is None


def test_memory_cache_roundtrip():
    _roundtrip(MemoryCache())


def test_memory_cache_expiry():
    cache = MemoryCache()
    cache.set("k", 1, ttl=1)
    assert cache.get("k") == 1
    # simulate expiry by rewriting with a past deadline
    cache._store["k"] = (time.time() - 10, 1)
    assert cache.get("k") is None


def test_null_cache_never_stores():
    cache = NullCache()
    cache.set("k", 1)
    assert cache.get("k") is None


def test_file_cache_roundtrip_and_clear(tmp_path):
    cache = FileCache(str(tmp_path))
    _roundtrip(cache)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None and cache.get("b") is None


def test_file_cache_no_expiry_when_ttl_zero(tmp_path):
    cache = FileCache(str(tmp_path))
    cache.set("k", "v", ttl=0)
    assert cache.get("k") == "v"
