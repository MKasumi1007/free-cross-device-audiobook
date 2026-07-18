from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from .models import SegmentKind
from .normalize import (
    concise_chapter_title,
    is_placeholder_chapter_title,
    normalize_display_text,
    normalize_spoken_text,
)


SKIP_TAGS = {
    "audio",
    "button",
    "canvas",
    "form",
    "head",
    "iframe",
    "img",
    "input",
    "nav",
    "noscript",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "textarea",
    "video",
}
BLOCK_TAGS = {"aside", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _is_footnote(tag: Tag) -> bool:
    role = str(tag.attrs.get("role", "")).lower()
    epub_type = str(tag.attrs.get("epub:type", tag.attrs.get("type", ""))).lower()
    class_names = " ".join(str(item) for item in tag.attrs.get("class", [])).lower()
    signals = f"{role} {epub_type} {class_names}"
    return any(value in signals for value in ("doc-footnote", "footnote", "endnote", "note"))


def _node_text(node: Any, *, spoken: bool) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name in SKIP_TAGS:
        return ""
    if spoken and node.name in {"rt", "rp"}:
        return ""
    if node.name == "br":
        return "\n"
    return "".join(_node_text(child, spoken=spoken) for child in node.children)


def _top_level_blocks(root: Tag) -> Iterable[Tag]:
    for tag in root.find_all(BLOCK_TAGS):
        if not any(isinstance(parent, Tag) and parent.name in BLOCK_TAGS for parent in tag.parents if parent is not root):
            yield tag


def extract_html_blocks(content: bytes, source_href: str) -> tuple[str, list[tuple[str, str, SegmentKind]]]:
    soup = BeautifulSoup(content, "html.parser")
    title_tag = soup.find("title")
    document_title = (
        normalize_display_text(_node_text(title_tag, spoken=False))
        if title_tag
        else ""
    )
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    root = soup.body or soup

    blocks: list[tuple[str, str, SegmentKind]] = []
    title = ""
    for tag in _top_level_blocks(root):
        display = normalize_display_text(_node_text(tag, spoken=False))
        if not display:
            continue
        footnote = _is_footnote(tag) or any(_is_footnote(parent) for parent in tag.parents if isinstance(parent, Tag))
        kind = SegmentKind.FOOTNOTE if footnote else (
            SegmentKind.HEADING if tag.name in HEADING_TAGS else SegmentKind.PARAGRAPH
        )
        spoken = "" if footnote else normalize_spoken_text(_node_text(tag, spoken=True))
        blocks.append((display, spoken, kind))
        if (
            not title
            and kind is SegmentKind.HEADING
            and not is_placeholder_chapter_title(display)
        ):
            title = display

    if not blocks:
        display = normalize_display_text(_node_text(root, spoken=False))
        spoken = normalize_spoken_text(_node_text(root, spoken=True))
        if display:
            blocks.append((display, spoken, SegmentKind.PARAGRAPH))

    source_title = source_href.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not title and document_title and not is_placeholder_chapter_title(document_title):
        title = document_title
    if not title and source_title and not is_placeholder_chapter_title(source_title):
        title = source_title
    if not title:
        readable = next(
            (
                display
                for display, _spoken, kind in blocks
                if kind is not SegmentKind.FOOTNOTE
                and not is_placeholder_chapter_title(display)
            ),
            "",
        )
        title = concise_chapter_title(readable)
    if not title:
        title = source_title
    return title, blocks
