from __future__ import annotations

from typing import Callable


class IdleDetector:
    def __init__(
        self,
        activity_provider: Callable[[], float],
        idle_threshold_seconds: int = 300,
    ) -> None:
        self.activity_provider = activity_provider
        self.idle_threshold_seconds = idle_threshold_seconds

    def is_idle(self) -> bool:
        return self.activity_provider() >= self.idle_threshold_seconds
