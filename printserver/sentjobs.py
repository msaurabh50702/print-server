"""Remembers print requests by the key the phone gives them, so a retry never prints twice.

Phones save print jobs while the server can't be reached and send them later.
On weak Wi-Fi a request can arrive and print while the reply is lost; the
phone then sends it again with the same key and gets the saved reply instead
of a second copy on paper.
"""
import json
import threading
import time
from pathlib import Path


class SentJobs:
    def __init__(self, path, ttl=7 * 24 * 3600, limit=1000):
        self.path = Path(path)
        self.ttl = ttl
        self.limit = limit
        self.lock = threading.Lock()
        self.busy = set()          # keys being processed right now (not persisted)
        self.done = self._load()   # key -> {"time", "response"}

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        cutoff = time.time() - self.ttl
        entries = sorted(((k, v) for k, v in self.done.items() if v.get("time", 0) >= cutoff),
                         key=lambda kv: kv[1]["time"])[-self.limit:]
        self.done = dict(entries)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done))
        tmp.replace(self.path)

    def claim(self, key):
        """("done", saved reply), ("busy", None) or ("new", None) - then call finish or release."""
        with self.lock:
            if key in self.done:
                return "done", self.done[key]["response"]
            if key in self.busy:
                return "busy", None
            self.busy.add(key)
            return "new", None

    def finish(self, key, response):
        with self.lock:
            self.busy.discard(key)
            self.done[key] = {"time": time.time(), "response": response}
            self._save()

    def release(self, key):
        """The request failed; the phone may try again."""
        with self.lock:
            self.busy.discard(key)
