# src/taskanov/backends/google.py
from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from dataclasses import asdict
import os
import json
import time
import datetime as dt

from dateutil import tz
try:
    # google deps (declare in pyproject)
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except Exception as e:
    raise RuntimeError(
        "ERROR: install missing deps [project.dependencies]: "
        "'google-api-python-client', 'google-auth-httplib2', 'google-auth-oauthlib', 'python-dateutil'"
    ) from e

from .base import Task

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
]

LOCAL_TZ = tz.gettz(os.environ.get("TASKANOV_TZ", "Europe/Rome"))

# credentials path
CANDIDATE_CREDENTIAL_PATHS = [
    Path(os.environ.get("TASKANOV_GOOGLE_CREDENTIALS", "")),
    Path("~/.config/taskanov/google/credentials.json").expanduser(),
    Path(".secrets/google/credentials.json").absolute(),
]

DEFAULT_CALENDAR_NAME = "taskanov"


def _discover_credentials_path() -> Path:
    for p in CANDIDATE_CREDENTIAL_PATHS:
        if not p:
            continue
        if p.is_file():
            return p
        if p.is_dir() and (p / "credentials.json").is_file():
            return p / "credentials.json"
    raise FileNotFoundError(
        "credentials.json not found. Set TASKANOV_GOOGLE_CREDENTIALS or use ~/.config/taskanov/google/credentials.json"
    )


def _token_path_for(creds_path: Path) -> Path:
    return creds_path.with_name("token.json")


def _load_credentials() -> Credentials:
    creds_path = _discover_credentials_path()
    token_path = _token_path_for(creds_path)
    creds: Optional[Credentials] = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def _tasks_service():
    return build("tasks", "v1", credentials=_load_credentials(), cache_discovery=False)


def _calendar_service():
    return build("calendar", "v3", credentials=_load_credentials(), cache_discovery=False)


def _to_rfc3339(ts: float) -> str:
    d = dt.datetime.fromtimestamp(ts, tz=LOCAL_TZ)
    return d.isoformat()


def _ensure_calendar(name: str = DEFAULT_CALENDAR_NAME) -> str:
    cal = _calendar_service()
    page = cal.calendarList().list(maxResults=250).execute()
    while True:
        for it in page.get("items", []):
            if it.get("summary") == name:
                return it["id"]
        token = page.get("nextPageToken")
        if not token:
            break
        page = cal.calendarList().list(maxResults=250, pageToken=token).execute()
    created = cal.calendars().insert(body={"summary": name, "timeZone": "Europe/Rome"}).execute()
    cal.calendarList().insert(calendarId=created["id"]).execute()
    return created["id"]


def _write_time_slot(start_ts: float, end_ts: float, summary: str, description: str = "", calendar_name: str = DEFAULT_CALENDAR_NAME):
    if end_ts <= start_ts:
        raise ValueError("ended_ts deve essere > started_ts")
    cal = _calendar_service()
    cal_id = _ensure_calendar(calendar_name)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": _to_rfc3339(start_ts)},
        "end": {"dateTime": _to_rfc3339(end_ts)},
        "extendedProperties": {"private": {"taskanov": "1", "source": "taskanov"}},
    }
    return cal.events().insert(calendarId=cal_id, body=body).execute()


class GoogleBackend:
    """
    Google Backend
    """

    def __init__(self, path: Path, preferred_tasklist: Optional[str] = None, calendar_name: str = DEFAULT_CALENDAR_NAME):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: List[Task] = []
        self._id_index: Dict[str, Tuple[str, str]] = {}
        self._preferred_tasklist_name = preferred_tasklist
        self._calendar_name = calendar_name

        self.timer_file = self.path.with_name("timer_state.json")
        self._timer_active: bool = False
        self._timer_title: str = ""
        self._timer_started: float = 0.0
        self._load_timer()

        self.refresh()

    # ---------------- timer state ----------------
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

    # ---------------- helpers ----------------
    def _default_list_id(self) -> str:
        svc = _tasks_service()

        page = svc.tasklists().list(maxResults=100).execute()
        lists = page.get("items", [])
        if self._preferred_tasklist_name:
            for l in lists:
                if l.get("title") == self._preferred_tasklist_name:
                    return l["id"]

        if not lists:
            created = svc.tasklists().insert(body={"title": "My Tasks"}).execute()
            return created["id"]
        return lists[0]["id"]

    def _split_gid(self, gid: str) -> Tuple[str, str]:
        list_id, task_id = gid.split("::", 1)
        return list_id, task_id

    # ---------------- Backend API ----------------
    def refresh(self) -> None:
        svc = _tasks_service()
        self._cache = []
        self._id_index = {}

        lists_req = svc.tasklists().list(maxResults=100)
        while lists_req is not None:
            lists_resp = lists_req.execute()
            for lst in lists_resp.get("items", []):
                list_id = lst["id"]
                tasks_req = svc.tasks().list(tasklist=list_id, showDeleted=False, maxResults=100)
                while tasks_req is not None:
                    tasks_resp = tasks_req.execute()
                    for t in tasks_resp.get("items", []):
                        status = t.get("status", "needsAction")
                        done = status == "completed"
                        title = t.get("title") or "(senza titolo)"
                        gid = f"{list_id}::{t['id']}"
                        self._cache.append(Task(id=gid, title=title, done=done, list_title=lst.get("title")))
                        self._id_index[gid] = (list_id, t["id"])
                    tasks_req = svc.tasks().list_next(tasks_req, tasks_resp)
            lists_req = svc.tasklists().list_next(lists_req, lists_resp)

    def list_open(self) -> List[Task]:
        return [t for t in self._cache if not t.done]

    def list_done(self) -> List[Task]:
        return [t for t in self._cache if t.done]

    def toggle(self, task_id: str) -> Optional[Task]:
        if task_id not in self._id_index:
            return None
        list_id, tid = self._id_index[task_id]
        svc = _tasks_service()
        cur = svc.tasks().get(tasklist=list_id, task=tid).execute()
        now_iso = dt.datetime.now(tz=LOCAL_TZ).isoformat()
        if cur.get("status") == "completed":
            body = {"status": "needsAction", "completed": None}
        else:
            body = {"status": "completed", "completed": now_iso}
        updated = svc.tasks().patch(tasklist=list_id, task=tid, body=body).execute()
        for t in self._cache:
            if t.id == task_id:
                t.done = (updated.get("status") == "completed")
                return t
        return None

    def delete(self, task_id: str) -> Optional[Task]:
        if task_id not in self._id_index:
            return None
        list_id, tid = self._id_index[task_id]
        svc = _tasks_service()
        svc.tasks().delete(tasklist=list_id, task=tid).execute()
        removed: Optional[Task] = None
        new_list: List[Task] = []
        for t in self._cache:
            if t.id == task_id and removed is None:
                removed = t
                continue
            new_list.append(t)
        self._cache = new_list
        self._id_index.pop(task_id, None)
        return removed

    def ensure(self, title: str) -> Task:
        for t in self._cache:
            if t.title == title and not t.done:
                return t
        list_id = self._default_list_id()
        svc = _tasks_service()
        created = svc.tasks().insert(tasklist=list_id, body={"title": title}).execute()
        gid = f"{list_id}::{created['id']}"
        nt = Task(id=gid, title=title, done=False)
        self._cache.append(nt)
        self._id_index[gid] = (list_id, created["id"])
        return nt

    # ---------------- List and Task ------------------------
    def list_lists(self) -> list[tuple[str, str]]:
        svc = _tasks_service()
        out: list[tuple[str, str]] = []
        req = svc.tasklists().list(maxResults=100)
        while req is not None:
            resp = req.execute()
            for l in resp.get("items", []):
                out.append((l["id"], l.get("title", "")))
            req = svc.tasklists().list_next(req, resp)
        return out

    def create_in_list(self, title: str, list_id: str) -> Task:
        svc = _tasks_service()
        created = svc.tasks().insert(tasklist=list_id, body={"title": title}).execute()
        gid = f"{list_id}::{created['id']}"
        t = Task(id=gid, title=title, done=False, list_title=self._list_name_by_id(list_id))
        self._cache.append(t)
        self._id_index[gid] = (list_id, created["id"])
        return t

    def _list_name_by_id(self, list_id: str) -> str:
        for lid, name in self.list_lists():
            if lid == list_id:
                return name
        return "Tasks"

    # ---------------- Timer (backend-owned) ----------------
    def get_active_timer(self) -> tuple[bool, str, float]:
        return self._timer_active, self._timer_title, self._timer_started

    def start_timer(self, title: str, started_ts: float) -> None:
        if self._timer_active and self._timer_title != title:
            self._timer_active = False
            self._save_timer()
        self._timer_active = True
        self._timer_title = title
        self._timer_started = float(started_ts)
        self._save_timer()

    def stop_timer(self, ended_ts: float) -> None:
        if self._timer_active and self._timer_started > 0:
            try:
                _write_time_slot(
                    start_ts=self._timer_started,
                    end_ts=float(ended_ts),
                    summary=self._timer_title or "taskanov: work",
                    description="Logged via taskanov",
                    calendar_name=self._calendar_name,
                )
            except Exception:
                pass
        self._timer_active = False
        self._timer_title = ""
        self._timer_started = 0.0
        self._save_timer()
