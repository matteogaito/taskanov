# src/taskanov/backends/localjson.py
from __future__ import annotations
import json, uuid, time
from pathlib import Path
from typing import List, Optional
from dataclasses import asdict

from .base import Task, Backend


class LocalJsonBackend:
    """
    Local JSON storage backend.
    - Tasks are stored as a JSON list of Task dicts.
    - Timer state is persisted in a separate JSON file (timer_state.json).
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

        # in-memory cache of tasks
        self._cache: List[Task] = []
        self.refresh()

        # timer state (persisted next to tasks file)
        self.timer_file = self.path.with_name("timer_state.json")
        self._timer_active: bool = False
        self._timer_title: str = ""
        self._timer_started: float = 0.0
        self._load_timer()

    # ---------------- internal helpers ----------------
    def _read(self) -> List[Task]:
        data = json.loads(self.path.read_text() or "[]")
        for t in data:
            t.setdefault("list_title", None)
        return [Task(**t) for t in data]

    def _write(self, tasks: List[Task]) -> None:
        self.path.write_text(json.dumps([asdict(t) for t in tasks], indent=2))

    def _save(self) -> None:
        self._write(self._cache)

    def _load_timer(self) -> None:
        try:
            if self.timer_file.exists():
                data = json.loads(self.timer_file.read_text())
                self._timer_active = bool(data.get("active", False))
                self._timer_title = str(data.get("title", ""))
                self._timer_started = float(data.get("started", 0.0))
        except Exception:
            self._timer_active, self._timer_title, self._timer_started = False, "", 0.0

    def _save_timer(self) -> None:
        try:
            self.timer_file.write_text(json.dumps({
                "active": self._timer_active,
                "title": self._timer_title,
                "started": self._timer_started,
            }))
        except Exception:
            pass

    # ---------------- Backend API ----------------
    def refresh(self) -> None:
        self._cache = self._read()

    def list_open(self) -> List[Task]:
        return [t for t in self._cache if not t.done]

    def list_done(self) -> List[Task]:
        return [t for t in self._cache if t.done]

    def toggle(self, task_id: str) -> Optional[Task]:
        for t in self._cache:
            if t.id == task_id:
                t.done = not t.done
                self._save()
                return t
        return None

    def delete(self, task_id: str) -> Optional[Task]:
        removed: Optional[Task] = None
        new_list: List[Task] = []
        for t in self._cache:
            if t.id == task_id and removed is None:
                removed = t
                continue
            new_list.append(t)
        self._cache = new_list
        self._save()
        return removed

    def ensure(self, title: str) -> Task:
        # Return existing open task with same title, otherwise create new.
        for t in self._cache:
            if t.title == title and not t.done:
                return t
        nt = Task(id=str(uuid.uuid4()), title=title, done=False)
        self._cache.append(nt)
        self._save()
        return nt

    # ---------------- Timer state (backend-owned) ----------------
    def get_active_timer(self) -> tuple[bool, str, float]:
        return self._timer_active, self._timer_title, self._timer_started

    def start_timer(self, title: str, started_ts: float) -> None:
        # If a different timer is running, stop it implicitly (no external side-effects here).
        if self._timer_active and self._timer_title != title:
            self._timer_active = False
            self._save_timer()
        self._timer_active = True
        self._timer_title = title
        self._timer_started = float(started_ts)
        self._save_timer()

    def stop_timer(self, ended_ts: float) -> None:
        self._timer_active = False
        self._timer_title = ""
        self._timer_started = 0.0
        self._save_timer()
