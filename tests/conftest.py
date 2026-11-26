from contextlib import nullcontext

import pytest

from eth_rescue.report import Reporter

REPORTER_EVENTS = {
    name for name in vars(Reporter) if not name.startswith("_") and name != "waiting"
}


class RecordingReporter:
    """Reporter double that records every event as (name, args). Only the
    Reporter protocol's methods exist, so a misspelled call fails the test."""

    def __init__(self):
        self.events: list[tuple[str, tuple]] = []

    def waiting(self, text):
        return nullcontext()

    def __getattr__(self, name):
        if name not in REPORTER_EVENTS:
            raise AttributeError(name)

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
