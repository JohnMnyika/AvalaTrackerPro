from __future__ import annotations

import threading
import time
from typing import Optional

try:
    from pynput import keyboard, mouse
except Exception:  # pragma: no cover
    keyboard = None
    mouse = None


class ActivityMonitor:
    """Tracks keyboard/mouse activity timestamps using pynput.

    Falls back to manual ping-only mode when pynput hooks are unavailable.
    """

    def __init__(self) -> None:
        self._last_activity_ts = time.time()
        self._mouse_listener: Optional[object] = None
        self._keyboard_listener: Optional[object] = None
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True

        if mouse is not None:
            self._mouse_listener = mouse.Listener(
                on_move=lambda *_: self.mark_activity(),
                on_click=lambda *_: self.mark_activity(),
                on_scroll=lambda *_: self.mark_activity(),
            )
            self._mouse_listener.start()

        if keyboard is not None:
            self._keyboard_listener = keyboard.Listener(
                on_press=lambda *_: self.mark_activity(),
            )
            self._keyboard_listener.start()

    def stop(self) -> None:
        self._started = False
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

    def mark_activity(self) -> None:
        with self._lock:
            self._last_activity_ts = time.time()

    def last_activity_seconds(self) -> float:
        with self._lock:
            return max(0.0, time.time() - self._last_activity_ts)
