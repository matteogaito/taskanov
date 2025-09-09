from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Tuple, List
import time, json
import asyncio, time
from .backends import Task, Backend

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.styles import Style
from prompt_toolkit.filters import has_focus, Condition
from prompt_toolkit.layout.containers import FloatContainer, Float, ConditionalContainer
from prompt_toolkit.formatted_text import fragment_list_width

# Inline modal widgets
from prompt_toolkit.widgets import Dialog, Button, RadioList
from prompt_toolkit.widgets import TextArea as PTTextArea

from taskanov.logging_setup import setup_logging
from taskanov.config import state_dir

log = setup_logging()


STYLE = Style.from_dict({
    # UI colors
    "title":          "bold",
    "section-title":  "bold underline",
    "selected":       "reverse",
    "done":           "fg:#7a7a7a",
    "muted":          "fg:#9aa0a6",
    "accent":         "fg:#00afff",
    "border":         "fg:#3a3a3a",
    "popup":          "bg:#1e1e1e",
    "popup-border":   "fg:#5a5a5a",
    "popup-title":    "bold",
    "popup-hint":     "fg:#9aa0a6",
    "veil":           "bg:#000000",
    "popup-box":      "bg:#1e1e1e",
    "popup-border":   "fg:#5a5a5a",
    "timer":          "fg:#ffd700",
    "timer-muted":    "fg:#9aa0a6",
})

# ------------------------ Helpers to draw 1-line framed bars ------------------------
def _line_top() -> List[tuple]:
    cols = get_app().output.get_size().columns
    if cols < 2:
        cols = 2
    return [("class:border", "┌" + "─" * (cols - 2) + "┐")]

def _line_bottom() -> List[tuple]:
    cols = get_app().output.get_size().columns
    if cols < 2:
        cols = 2
    return [("class:border", "└" + "─" * (cols - 2) + "┘")]

def _line_middle(inner: List[tuple]) -> List[tuple]:
    cols = get_app().output.get_size().columns
    if cols < 2:
        cols = 2
    out: List[tuple] = [("class:border", "│ ")]
    out.extend(inner)

    # Use display width (handles emojis/combining), not len()
    used = 2 + fragment_list_width(inner)  # left border + inner display width

    # Reserve 1 char for right border; avoid negative pad
    pad = max(0, cols - 1 - used)
    if pad:
        out.append(("", " " * pad))
    out.append(("class:border", "│"))
    return out


def framed_bar(inner_func) -> HSplit:
    return HSplit([
        Window(height=1, content=FormattedTextControl(_line_top)),
        Window(height=1, content=FormattedTextControl(lambda: _line_middle(inner_func()))),
        Window(height=1, content=FormattedTextControl(_line_bottom)),
    ])

def popup_box(body) -> HSplit:
    """
    Simple framed box for popups (used by search & inline new-task modal).
    """
    top = VSplit([
        Window(width=1, char="┌", style="class:popup-border"),
        Window(char="─", style="class:popup-border"),
        Window(width=1, char="┐", style="class:popup-border"),
    ])
    middle = VSplit([
        Window(width=1, char="│", style="class:popup-border"),
        HSplit([
            Window(height=1, char=" "),        # padding top
            body,
            Window(height=1, char=" "),        # padding bottom
        ], style="class:popup-box"),
        Window(width=1, char="│", style="class:popup-border"),
    ])
    bottom = VSplit([
        Window(width=1, char="└", style="class:popup-border"),
        Window(char="─", style="class:popup-border"),
        Window(width=1, char="┘", style="class:popup-border"),
    ])
    return HSplit([top, middle, bottom])

def _fmt_dur(seconds: int) -> str:
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _render_timer_inner(self):
    # Ask backend for current timer state
    active, title, started = self.backend.get_active_timer()
    if not active:
        return [("class:timer-muted", "No active timer")]
    elapsed = time.time() - float(started)
    return [
        ("class:timer", "⏱  "),
        ("", "Task: "),
        ("class:accent", title),
        ("", "  •  "),
        ("class:timer", _fmt_dur(int(elapsed))),
    ]


# -----------------------------------------------------------------------------------


class TaskTUI:
    """
    Two columns (Open/Done) divided by a vertical bar. Top and bottom are single-line framed bars.
    '/' opens a modal search popup; live-filter updates on each keystroke.
    Keys: ↑/↓ move • Space complete/reopen • d delete • / search • Tab focus • r refresh • n new (inline modal) • s start timer • x stop timer • q quit
    """
    def __init__(self, backend: Backend):
        self.backend = backend  # unified backend (LocalJson/Google/CalDAV…)
        self.focus_done = False
        self.idx_open = 0
        self.idx_done = 0
        self.filter_text = ""
        self._ticker_paused = False

        # Inline "New task" modal state
        self.newtask_visible = False
        self.newtask_title_input: PTTextArea | None = None
        self.newtask_list_widget: RadioList | None = None

        self._build_main_layout()

    # ---------- layout builders ----------
    def _build_main_layout(self):
        log.info("Building layout")
        # Header (framed)
        self.header = framed_bar(self._render_header_inner)

        # Timer bar (framed)
        self.timer_bar = framed_bar(self._render_timer_inner)

        # Open tasks column
        self.open_title = Window(
            height=1,
            content=FormattedTextControl(lambda: [("class:section-title", " Aperti ")]),
        )
        self.open_ctrl = FormattedTextControl(self._render_open, focusable=True)
        self.open_win = Window(content=self.open_ctrl, always_hide_cursor=True)

        # Done tasks column
        self.done_title = Window(
            height=1,
            content=FormattedTextControl(lambda: [("class:section-title", " Completati ")]),
        )
        self.done_ctrl = FormattedTextControl(self._render_done, focusable=True)
        self.done_win = Window(content=self.done_ctrl, always_hide_cursor=True)

        # Center split: open | vertical bar | done
        self.center = VSplit([
            HSplit([self.open_title, self.open_win]),
            Window(width=1, char="│", style="class:border"),
            HSplit([self.done_title, self.done_win]),
        ])

        # Footer (framed)
        self.status = framed_bar(self._render_status_inner)

        # ---------- Modal search popup ----------
        self.search_visible = False

        def _popup_results_line():
            # Live result count
            count = len(self._filtered_open()) + len(self._filtered_done())
            return [("class:popup-hint", f" Results: {count}  •  Enter/Esc: close")]

        def _accept_search(_buffer):
            # Close popup on Enter
            self._close_search()
            return True

        self.search_input = TextArea(
            prompt=" Search: ",
            multiline=False,
            focusable=True,
            style="",
            accept_handler=_accept_search,
        )
        # Live update filter on each keystroke
        self.search_input.buffer.on_text_changed += self._on_search_change

        popup_body = HSplit([
            Window(height=1, content=FormattedTextControl(lambda: [("class:popup-title", " Live search ")])),
            self.search_input,
            Window(height=1, content=FormattedTextControl(_popup_results_line)),
        ])

        # Dark veil behind popups (fills screen when search OR new-task modal is visible)
        self.search_veil = Float(
            content=ConditionalContainer(
                content=Window(style="class:veil"),
                filter=Condition(lambda: self.search_visible or self.newtask_visible),
            )
        )

        # Popup box (search)
        self.search_float = Float(
            content=ConditionalContainer(
                content=popup_box(popup_body),
                filter=Condition(lambda: self.search_visible),
            ),
            top=3, left=6, right=6,
        )

        # ---------- Inline "New task" modal ----------
        # Placeholder float; content is swapped on open to rebuild fresh widgets.
        self.newtask_float = Float(
            content=ConditionalContainer(
                content=Window(height=1, char=" "),
                filter=Condition(lambda: self.newtask_visible),
            ),
            top=3, left=6, right=6,
        )

        # Root layout INSIDE a FloatContainer (so we can float the popups)
        self.root_main = HSplit([
            self.header,
            Window(height=1, char=" "),   # spacer
            self.timer_bar,
            Window(height=1, char=" "),   # spacer
            self.center,
            Window(height=1, char=" "),   # spacer
            self.status,
        ])
        self.root = FloatContainer(
            content=self.root_main,
            floats=[self.search_veil, self.search_float, self.newtask_float],
        )

        self.kb = self._keys()
        self.app = Application(
            layout=Layout(self.root, focused_element=self.open_win),
            key_bindings=self.kb,
            style=STYLE,
            full_screen=True,
        )

    def _soft_rebuild(self):
        """Rebuild main layout tree and force a clean relayout/repaint."""
        log.info("running soft_rebuild")
        self._build_main_layout()
        self.root.content = self.root_main
        self.app.layout.focus(self.open_win)
        self.app.layout.reset()
        get_app().invalidate()

    # ---------- inline "New task" modal ----------
    def _open_newtask_modal(self):
        """Open the inline 'New task' dialog (list selector if available + title)."""
        # Rebuild widgets fresh each time to avoid stale cursor/measure state
        self.newtask_title_input = PTTextArea(multiline=False, prompt=" Title: ")
        lists = []
        if hasattr(self.backend, "list_lists"):
            try:
                lists = self.backend.list_lists()
            except Exception:
                lists = []

        if lists:
            self.newtask_list_widget = RadioList([(lid, name) for (lid, name) in lists])
            body = HSplit([self.newtask_list_widget, self.newtask_title_input], padding=1)
        else:
            self.newtask_list_widget = None
            body = HSplit([self.newtask_title_input], padding=1)

        def on_ok():
            self._confirm_newtask_modal()

        def on_cancel():
            self._close_newtask_modal()

        dialog = Dialog(
            title="New task",
            body=body,
            buttons=[Button(text="OK", handler=on_ok), Button(text="Cancel", handler=on_cancel)],
            with_background=True,
        )

        # Swap float content to our framed popup box
        self.newtask_float.content = ConditionalContainer(
            content=popup_box(dialog),
            filter=Condition(lambda: self.newtask_visible),
        )
        self.newtask_visible = True
        self.app.layout.focus(self.newtask_title_input)
        self.app.invalidate()

    def _confirm_newtask_modal(self):
        """Read values, create task, autostart timer, then close modal."""
        title = (self.newtask_title_input.text or "").strip() if self.newtask_title_input else ""
        if not title:
            # Keep focus if empty title
            if self.newtask_title_input:
                self.app.layout.focus(self.newtask_title_input)
            return

        list_choice = self.newtask_list_widget.current_value if self.newtask_list_widget else None

        try:
            # Create task in backend
            if list_choice and hasattr(self.backend, "create_in_list"):
                self.backend.create_in_list(title, list_choice)
            else:
                self.backend.ensure(title)
            self.backend.refresh()
            self.idx_open = 0

            # Auto-start timer on the newly created task
            now = time.time()
            active, cur_title, _started = self.backend.get_active_timer()
            if active and cur_title != title:
                self.backend.stop_timer(now)
            self.backend.start_timer(title, now)

        except Exception:
            # Log silently; keep modal open (you can add an error label if you want)
            try:
                log.exception("Failed to create/start timer for new task")
            except Exception:
                pass
            return

        # Close modal + soft relayout
        self._close_newtask_modal()

    def _close_newtask_modal(self):
        """Close the inline modal and restore focus + repaint."""
        self.newtask_visible = False
        self.app.layout.focus(self.open_win)
        self.app.layout.reset()
        self.app.invalidate()

    # ---------- header & status inner content (without borders) ----------
    def _render_header_inner(self) -> List[tuple]:
        return [
            ("class:title", "taskanov "), ("", "• "),
            ("class:accent", "↑/↓"), ("", " move  "),
            ("class:accent", "n"), ("", " new  "),
            ("class:accent", "s"), ("", " start timer  "),
            ("class:accent", "x"), ("", " stop timer "),
            ("class:accent", "Space"), ("", " done  "),
            ("class:accent", "d"), ("", " del  "),
            ("class:accent", "/"), ("", " search  "),
            ("class:accent", "Tab"), ("", " focus  "),
            ("class:accent", "q"), ("", " quit"),
        ]

    def _render_status_inner(self) -> List[tuple]:
        from datetime import datetime
        o, d = len(self._filtered_open()), len(self._filtered_done())
        clock = datetime.now().strftime("%H:%M:%S")
        filt = f" | filter: '{self.filter_text}'" if self.filter_text else ""
        pane = "DONE" if self.focus_done else "OPEN"
        return [
            ("", f"Open: {o}  •  Done: {d}{filt}"),
            ("", "  |  pane: "),
            ("class:accent", pane),
            ("", "    "),
            ("class:muted", clock),
        ]

    def _render_timer_inner(self):
        # Ask backend for current timer state
        active, title, started = self.backend.get_active_timer()
        if not active:
            return [("class:timer-muted", "No active timer")]
        elapsed = time.time() - float(started)
        return [
            ("class:timer", "⏱  "),
            ("", "Task: "),
            ("class:accent", title),
            ("", "  •  "),
            ("class:timer", _fmt_dur(int(elapsed))),
        ]

    # ---------- filtering ----------
    def _filtered_open(self) -> List[Task]:
        items = self.backend.list_open()
        if not self.filter_text:
            return items
        ft = self.filter_text.lower()
        return [
            t for t in items
            if (ft in t.title.lower()) or (getattr(t, "list_title", None) and ft in t.list_title.lower())
        ]

    def _filtered_done(self) -> List[Task]:
        items = self.backend.list_done()
        if not self.filter_text:
            return items
        ft = self.filter_text.lower()
        return [t for t in items if ft in t.title.lower()]

    # ---------- list renderers ----------
    def _label_task(self, t) -> str:
        list_name = (t.list_title or "").replace(" ", "")
        return f"{list_name} / {t.title}" if getattr(t, "list_title", None) else t.title

    def _render_open(self) -> List[tuple]:
        lines: List[tuple] = []
        items = self._filtered_open()
        if not items:
            return [("class:muted", "\n  (no open tasks)\n")]
        for i, t in enumerate(items):
            marker = "● "
            style = "class:selected" if (not self.focus_done and i == self.idx_open) else ""
            lines.append((style, f"  {marker}{self._label_task(t)}\n"))
        return lines

    def _render_done(self) -> List[tuple]:
        lines: List[tuple] = []
        items = self._filtered_done()
        if not items:
            return [("class:muted", "\n  (no completed tasks)\n")]
        for i, t in enumerate(items):
            marker = "✔ "
            style = "class:selected" if (self.focus_done and i == self.idx_done) else "class:done"
            lines.append((style, f"  {marker}{t.title}\n"))
            lines.append((style, f"  {marker}{self._label_task(t)}\n"))
        return lines

    # ---------- logic ----------
    def _move(self, delta: int):
        if self.focus_done:
            n = len(self._filtered_done())
            if n:
                self.idx_done = max(0, min(self.idx_done + delta, n - 1))
        else:
            n = len(self._filtered_open())
            if n:
                self.idx_open = max(0, min(self.idx_open + delta, n - 1))

    def _current_task(self) -> Tuple[str, Task | None]:
        if self.focus_done:
            items = self._filtered_done()
            return ("done", items[self.idx_done]) if items else ("done", None)
        else:
            items = self._filtered_open()
            return ("open", items[self.idx_open]) if items else ("open", None)

    # ---------- helper: filter to disable global shortcuts while typing ----------
    def _not_typing_filter(self):
        """
        Returns a Condition that is True when we are NOT typing in a text input
        (search or new-task modal). Use it to guard global shortcuts.
        """
        def _fn():
            app = None
            try:
                app = get_app()
            except Exception:
                pass

            # Typing in search input?
            if self.search_visible and app and app.layout.has_focus(self.search_input):
                return False

            # Typing in new-task title?
            if self.newtask_visible and self.newtask_title_input and app and app.layout.has_focus(self.newtask_title_input):
                return False

            return True

        return Condition(_fn)

    # ---------- async run & clock ----------
    async def _ticker(self):
        while True:
            await asyncio.sleep(1)
            if self._ticker_paused:
                continue
            try:
                app = get_app()
            except Exception:
                app = None
            if app is self.app:
                self.app.invalidate()

    async def _run_async(self):
        self.app.create_background_task(self._ticker())
        await self.app.run_async()

    def run(self):
        asyncio.run(self._run_async())

    # ---------- search popup ----------
    def _on_search_change(self, _):
        # Live update the filter while typing
        self.filter_text = self.search_input.text.strip()
        # reset selections to top for both lists
        self.idx_open = self.idx_done = 0
        get_app().invalidate()

    def _open_search(self):
        if self.search_visible or self.newtask_visible:
            return
        self.search_visible = True
        # preload with current filter (nice UX)
        self.search_input.text = self.filter_text
        self.app.layout.focus(self.search_input)

    def _close_search(self):
        if not self.search_visible:
            return
        self.search_visible = False
        self.app.layout.focus(self.open_win)
        get_app().invalidate()

    # ---------- key bindings ----------
    def _keys(self):
        kb = KeyBindings()

        typing_ok = self._not_typing_filter()  # guard for global shortcuts

        @kb.add("q", filter=typing_ok)
        def _(e): e.app.exit()

        @kb.add("up", filter=typing_ok)
        def _(e):
            self._move(-1)

        @kb.add("down", filter=typing_ok)
        def _(e):
            self._move(1)

        @kb.add(" ", filter=typing_ok)
        def _(e):
            pane, t = self._current_task()
            if not t:
                return
            self.backend.toggle(t.id)

        @kb.add("d", filter=typing_ok)
        def _(e):
            pane, t = self._current_task()
            if not t:
                return
            self.backend.delete(t.id)
            if self.focus_done:
                self.idx_done = max(0, self.idx_done - 1)
            else:
                self.idx_open = max(0, self.idx_open - 1)

        @kb.add("tab", filter=typing_ok)
        def _(e):
            if self.focus_done:
                self.app.layout.focus(self.open_win)
                self.focus_done = False
            else:
                self.app.layout.focus(self.done_win)
                self.focus_done = True

        @kb.add("/", filter=typing_ok)
        def _(e): self._open_search()

        @kb.add("escape")
        def _(e):
            # Esc always closes popups; do not guard with typing_ok
            if self.search_visible:
                self._close_search()
            elif self.newtask_visible:
                self._close_newtask_modal()

        @kb.add("c-u")
        def _(e):
            # Only when search input is focused, clear it
            if self.search_visible and has_focus(self.search_input)(e):
                self.search_input.buffer.document = self.search_input.buffer.document.set_text("")

        @kb.add("r", filter=typing_ok)
        def _(e):
            self.backend.refresh()

        @kb.add("x", filter=typing_ok)
        def _(e):
            # Force stop current timer
            active, title, started = self.backend.get_active_timer()
            if active:
                self.backend.stop_timer(time.time())
                get_app().invalidate()

        @kb.add("s", filter=typing_ok)
        def _(e):
            """
            Start the timer on the selected task (NO toggle).
            - If another task has an active timer: stop it first, then start this one.
            - If this task already has the active timer: do nothing.
            """
            pane, t = self._current_task()
            if not t:
                return
            now = time.time()
            try:
                active, cur_title, _started = self.backend.get_active_timer()
                if active and cur_title != t.title:
                    self.backend.stop_timer(now)
                if not active or cur_title != t.title:
                    self.backend.start_timer(t.title, now)
                get_app().invalidate()
            except Exception:
                # TODO: log/show error
                pass

        @kb.add("n", filter=typing_ok)
        def _(e):
            """Open the inline 'New task' modal (list selector if available + title)."""
            if self.search_visible or self.newtask_visible:
                return
            self._open_newtask_modal()

        return kb


def run_tui(backend: Backend):
    TaskTUI(backend).run()
