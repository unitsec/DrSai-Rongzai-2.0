"""Bounded exact memoization of expensive GSAS-II TOF profile convolutions."""

from collections import OrderedDict
from contextlib import contextmanager
import threading

import numpy as np

_lock = threading.RLock()
_active = None


@contextmanager
def cached_tof_profiles(module, max_bytes=128 * 1024 * 1024):
    """Cache only bit-identical inputs; restore original functions on exit.

    Lattice/instrument refinement automatically produces new keys. Returning
    copies prevents callers from modifying cached scientific arrays.
    """
    global _active
    with _lock:
        if _active is not None and _active[0] is module:
            yield _active[1]
            return
    stats = {"hits": 0, "misses": 0, "bytes": 0, "peak_bytes": 0,
             "max_bytes": max_bytes, "method": "exact parameter and full x-array bytes"}
    cache = OrderedDict()
    originals = {name: getattr(module, name) for name in ("getEpsVoigt", "getdEpsVoigt")}

    def clone(value):
        return tuple(v.copy() if isinstance(v, np.ndarray) else v for v in value)

    def wrap(name, original):
        def evaluate(*args):
            x = args[-1]
            if np.ma.isMaskedArray(x) and np.any(np.ma.getmaskarray(x)):
                return original(*args)
            array = np.asarray(x)
            key = (name, *(float(v) for v in args[:-1]),
                   array.shape, array.dtype.str, array.tobytes())
            if key in cache:
                stats["hits"] += 1
                value, size = cache.pop(key)
                cache[key] = (value, size)
                return clone(value)
            stats["misses"] += 1
            value = original(*args)
            size = len(key[-1]) + sum(v.nbytes if isinstance(v, np.ndarray) else 8 for v in value) + 256
            if size <= max_bytes:
                while cache and stats["bytes"] + size > max_bytes:
                    _, (_, removed) = cache.popitem(last=False)
                    stats["bytes"] -= removed
                cache[key] = (clone(value), size)
                stats["bytes"] += size
                stats["peak_bytes"] = max(stats["peak_bytes"], stats["bytes"])
            return value
        return evaluate

    with _lock:
        previous = _active
        try:
            _active = (module, stats)
            for name, original in originals.items():
                setattr(module, name, wrap(name, original))
            yield stats
        finally:
            for name, original in originals.items():
                setattr(module, name, original)
            _active = previous
            cache.clear()
