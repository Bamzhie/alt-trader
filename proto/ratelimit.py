"""Thread-safe rolling-window rate limiter.

MEXC documents several contract endpoints at "20 times / 2 seconds"
(e.g. the index/fair price klines and the funding-rate history). This
limiter enforces exactly that sliding-window cap for a caller, so a
collection flow cannot burst a documented endpoint.
"""

import threading
import time
from collections import deque


class RollingRateLimiter:
    """Allow at most ``rate`` acquisitions per ``window_s`` sliding window.

    Thread-safe. ``acquire`` blocks until a slot is free, then records the
    acquisition. ``reset`` clears the window (test isolation).
    """

    def __init__(self, rate, window_s=1.0):
        self.rate = max(1, int(rate))
        self.window_s = float(window_s)
        self._times = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self.window_s:
                    self._times.popleft()
                if len(self._times) < self.rate:
                    self._times.append(now)
                    return
                wait = self.window_s - (now - self._times[0])
            time.sleep(max(wait, 0.001))

    def reset(self):
        with self._lock:
            self._times.clear()
