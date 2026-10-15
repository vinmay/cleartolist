"""In-memory bounded job queue for hosted scans.

Photos live only in memory, inside the job, and are dropped the moment the scan finishes, fails or times out.
Nothing is written to disk and nothing survives a restart.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

MAX_QUEUE = 8  # queued + running, across all users: a flood can never pile up GPU work
MAX_PENDING_PER_CLIENT = 2
QUEUED_TTL_S = 90  # unclaimed jobs expire
RUNNING_TTL_S = 120  # a claimed job that gets no result expires
RESULT_TTL_S = 600  # finished results (text only, no photos) are kept briefly so the page can fetch them


class QueueFull(Exception):
    pass


class TooManyPending(Exception):
    pass


@dataclass
class Job:
    id: str
    client: str
    created: float
    photos: dict[str, bytes]
    state: str = "queued"  # queued | running | done | failed | expired
    claimed: float | None = None
    finished: float | None = None
    result: dict[str, Any] | None = None

    def drop_photos(self) -> None:
        self.photos = {}


@dataclass
class JobQueue:
    jobs: dict[str, Job] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # anonymous event counts only
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def count(self, event: str) -> None:
        self.counts[event] = self.counts.get(event, 0) + 1

    def _pending(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state in ("queued", "running")]

    def sweep(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for j in list(self.jobs.values()):
                if j.state == "queued" and now - j.created > QUEUED_TTL_S or (
                        j.state == "running" and now - (j.claimed or now) > RUNNING_TTL_S):
                    j.state, j.finished = "expired", now
                    j.drop_photos()
                    self.count("expired")
                elif j.state in ("done", "failed", "expired") and now - (j.finished or now) > RESULT_TTL_S:
                    del self.jobs[j.id]

    def submit(self, client: str, photos: dict[str, bytes], now: float | None = None) -> Job:
        now = time.time() if now is None else now
        self.sweep(now)
        with self._lock:
            pending = self._pending()
            if len(pending) >= MAX_QUEUE:
                self.count("rejected_queue_full")
                raise QueueFull
            if sum(j.client == client for j in pending) >= MAX_PENDING_PER_CLIENT:
                self.count("rejected_per_client")
                raise TooManyPending
            job = Job(secrets.token_urlsafe(16), client, now, photos)
            self.jobs[job.id] = job
            self.count("accepted")
            return job

    def claim(self, now: float | None = None) -> Job | None:
        now = time.time() if now is None else now
        self.sweep(now)
        with self._lock:
            queued = sorted((j for j in self.jobs.values() if j.state == "queued"), key=lambda j: j.created)
            if not queued:
                return None
            job = queued[0]
            job.state, job.claimed = "running", now
            return job

    def get(self, job_id: str) -> Job | None:
        self.sweep()
        return self.jobs.get(job_id)

    def finish(self, job_id: str, result: dict[str, Any] | None, now: float | None = None) -> bool:
        """Record a result (or a failure when result is None) for a running job and drop its photos."""
        now = time.time() if now is None else now
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job.state != "running":
                return False
            job.state, job.finished, job.result = ("done" if result is not None else "failed"), now, result
            job.drop_photos()
            self.count("done" if result is not None else "failed")
            return True
