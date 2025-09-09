# src/taskanov/backends/__init__.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any

from .base import Task, Backend
from .localjson import LocalJsonBackend

def make_backend(cfg: Dict[str, Any]) -> Backend:
    """
    Factory that returns a Backend instance based on config.
    """
    b = cfg.get("backend", {})
    btype = (b.get("type") or "localjson").lower()
    state_base = cfg.get("state_dir")

    if btype == "localjson":
        lj = b.get("localjson", {}) if isinstance(b.get("localjson"), dict) else {}
        filename = lj.get("filename") or "tasks.json"
        return LocalJsonBackend(state_base / filename)

    if btype == "google":
        from .google import GoogleBackend
        gcfg = b.get("google", {})
        statefile = Path(gcfg.get("statefile", "google_state.json"))
        tasklist = gcfg.get("tasklist")
        calendar = gcfg.get("calendar", "taskanov")
        state_dir = gcfg.get("")
        state_file_path = state_base / statefile
        return GoogleBackend(
            path=state_file_path,
            preferred_tasklist=tasklist,
            calendar_name=calendar,
        )

    # if btype == "caldav":
    #     from .caldav import CalDavBackend
    #     return CalDavBackend(b.get("caldav", {}), state_base)

    # Fallback
    return LocalJsonBackend(state_base / "tasks.json")

__all__ = ["Task", "Backend", "make_backend"]
