"""Small on-disk log of jobs sent from the web app, shown on the queue page."""
import json
import threading
import time
from pathlib import Path


class JobHistory:
    def __init__(self, path, limit=50):
        self.path = Path(path)
        self.limit = limit
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, entries):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[-self.limit:]))
        tmp.replace(self.path)

    def add(self, job_id, printer, title, copies=1):
        with self.lock:
            entries = self._load()
            entries.append({"id": job_id, "printer": printer, "title": title,
                            "copies": copies, "time": time.time(), "cancelled": False})
            self._save(entries)

    def mark_cancelled(self, job_id):
        with self.lock:
            entries = self._load()
            for entry in entries:
                if entry.get("id") == job_id:
                    entry["cancelled"] = True
            self._save(entries)

    def recent(self):
        """Newest first."""
        with self.lock:
            return list(reversed(self._load()))
