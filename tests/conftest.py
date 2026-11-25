from contextlib import nullcontext

import pytest


class RecordingReporter:
    """Reporter double that records every event as (name, args)."""

    def __init__(self):
        self.events: list[tuple[str, tuple]] = []

    def waiting(self, text):
        return nullcontext()

    def __getattr__(self, name):
        def record(*args):
            self.events.append((name, args))

        return record

    def named(self, name: str) -> list[tuple]:
        return [args for event, args in self.events if event == name]

    def messages(self, level: str | None = None) -> list[str]:
        return [
            text
            for event_level, text in self.named("message")
            if level is None or event_level == level
        ]


@pytest.fixture
def reporter() -> RecordingReporter:
    return RecordingReporter()
