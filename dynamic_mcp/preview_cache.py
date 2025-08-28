# dynamic_mcp/preview_cache.py

import time
import threading
from typing import Optional, Dict, Any

class PreviewCache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}
        # background cleaner
        t = threading.Thread(target=self._cleaner, daemon=True)
        t.start()

    def set(self, key: str, value: Dict[str, Any]):
        with self._lock:
            self._store[key] = {"ts": time.time(), "value": value}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            if time.time() - item["ts"] > self.ttl:
                del self._store[key]
                return None
            return item["value"]

    def _cleaner(self):
        while True:
            time.sleep(max(60, self.ttl // 10))
            with self._lock:
                now = time.time()
                keys = list(self._store.keys())
                for k in keys:
                    if now - self._store[k]["ts"] > self.ttl:
                        del self._store[k]
