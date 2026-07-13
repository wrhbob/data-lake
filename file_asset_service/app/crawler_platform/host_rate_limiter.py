from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _default_jitter(max_seconds: float) -> float:
    return random.uniform(0.0, max_seconds)


@dataclass
class HostLimiter:
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    jitter: Callable[[float], float] = _default_jitter
    _last_request: dict[str, float] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def wait(self, host: str, min_delay_seconds: float, jitter_seconds: float = 0) -> None:
        min_delay = max(0.0, float(min_delay_seconds))
        jitter_delay = max(0.0, float(jitter_seconds))
        with self._lock:
            now = self.clock()
            last_request = self._last_request.get(host)
            wait_time = 0.0
            if last_request is not None:
                wait_time = max(0.0, min_delay - (now - last_request))
            if jitter_delay:
                wait_time += self.jitter(jitter_delay)
            if wait_time > 0:
                self.sleeper(wait_time)
            self._last_request[host] = self.clock()


_host_limiter = HostLimiter()


def get_host_limiter() -> HostLimiter:
    return _host_limiter
