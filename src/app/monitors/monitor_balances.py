"""Short-lived, account-isolated read cache; no wallet persistence or signing."""
import copy
from concurrent.futures import Future
import threading
import time


class BalanceReadCache:
    def __init__(self, capacity=512):
        self.capacity = capacity
        self.lock = threading.Lock()
        self.values = {}
        self.pending = {}

    def get(self, key, loader, max_age=20):
        with self.lock:
            cached = self.values.get(key)
            if cached and time.monotonic() - cached[0] < max_age:
                return copy.deepcopy(cached[1])
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self.pending[key] = future
        if not owner:
            return copy.deepcopy(future.result(timeout=25))
        try:
            value = loader()
            with self.lock:
                self.values[key] = (time.monotonic(), copy.deepcopy(value))
                while len(self.values) > self.capacity:
                    self.values.pop(next(iter(self.values)))
            future.set_result(value)
            return copy.deepcopy(value)
        except Exception as exc:
            future.set_exception(exc)
            raise  # Failed reads are never cached as zero balances.
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def clear(self):
        with self.lock:
            self.values.clear()
