"""Safe book parsing and generation planning for the audiobook project."""

from .errors import BookParseError
from .parser import parse_book

__all__ = ["BookParseError", "parse_book"]
