from __future__ import annotations


class BookParseError(ValueError):
    """An expected, user-facing import rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
