# src/taskanov/notify_os.py
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
import time
import itertools
from typing import Optional

log = logging.getLogger("taskanov.notify")

# Monotonic counter to build unique groups when "rotate" mode is used
_seq = itertools.count(1)

def notify(
    title: str,
    message: str,
    *,
    app_name: str = "Taskanov",
    group: str = "taskanov.workcheck",
    mode: str = "rotate",       # "rotate" => always show a new banner; "replace" => update same card
    sound: bool = True,
    sender: Optional[str] = None,  # macOS only; if None, will try env vars
) -> bool:
    """
    Send a desktop notification in a best-effort, cross-platform way.
    Returns True if we *think* it was sent, False otherwise.

    - macOS: prefers terminal-notifier; falls back to osascript.
      If `sender` provided (or env set), try once with -sender; on error, retry without.
      `mode="rotate"` forces a new visible banner each time (unique group).
      `mode="replace"` overwrites the previous one (same group).
    - Linux: uses notify-send if present. In "replace" mode, uses Canonical hint
      to coalesce cards on GNOME-based desktops (best-effort).
    - Windows/others: falls back to a terminal bell + no-op return.
    """
    try:
        if sys.platform == "darwin":
            return _notify_macos(
                title=title or app_name,
                message=message,
                group=group,
                rotate=(mode == "rotate"),
                sound=sound,
                sender=sender,
            )
        elif sys.platform.startswith("linux"):
            return _notify_linux(
                title=title or app_name,
                message=message,
                app_name=app_name,
                group=group,
                replace=(mode == "replace"),
            )
        else:
            # Very conservative fallback (no popup). Avoid printing to stdout.
            try:
                sys.stdout.write("\a")  # terminal bell
                sys.stdout.flush()
            except Exception:
                pass
            log.debug("notify: unsupported OS, fell back to bell only")
            return False
    except Exception:
        log.exception("notify: unexpected error")
        return False


# ------------------------ macOS ------------------------

def _notify_macos(
    *,
    title: str,
    message: str,
    group: str,
    rotate: bool,
    sound: bool,
    sender: Optional[str],
) -> bool:
    tn = shutil.which("terminal-notifier")
    if tn:
        grp = f"{group}.{int(time.time()*1000)}.{next(_seq)}" if rotate else group
        base = [tn, "-title", title, "-message", message, "-group", grp]
        if sound:
            base += ["-sound", "default"]

        # Use explicit sender if provided, else env overrides; try once.
        snd = sender or os.environ.get("TASKANOV_NOTIFY_SENDER") or os.environ.get("__CFBundleIdentifier")
        if snd:
            res = subprocess.run(
                base + ["-sender", snd, "-activate", snd] ,
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                return True
            # Retry without -sender (shows as 'terminal-notifier')
            log.debug(
                "terminal-notifier with -sender=%r failed (rc=%s, err=%r). Retrying without.",
                snd,
                res.returncode,
                (res.stderr or "").strip(),
            )

        # No sender or sender failed → send without -sender
        subprocess.run(
            base,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    return True


# ------------------------ Linux ------------------------

def _notify_linux(
    *,
    title: str,
    message: str,
    app_name: str,
    group: str,
    replace: bool,
) -> bool:
    ns = shutil.which("notify-send")
    if not ns:
        log.debug("notify-send not found")
        return False

    # Some desktops (GNOME) support coalescing via Canonical private hint.
    # If replace=True, we add a stable hint to overwrite the previous card.
    # Otherwise, each call is a new card.
    args = [ns, title, message, "--app-name", app_name]
    if replace:
        args += ["--hint", f"string:x-canonical-private-synchronous:{group}"]

    # Other niceties you might want: urgency, expire-time, icon, etc.
    # args += ["--urgency", "normal", "--expire-time", "5000"]

    subprocess.run(args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
