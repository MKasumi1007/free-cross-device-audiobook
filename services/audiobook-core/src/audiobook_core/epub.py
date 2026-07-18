from __future__ import annotations

import posixpath
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET

from .errors import BookParseError
from .html_text import extract_html_blocks
from .models import Chapter, ParserLimits
from .normalize import is_placeholder_chapter_title, make_chapter, normalize_display_text


@dataclass(frozen=True)
class ManifestItem:
    item_id: str
    path: str
    media_type: str
    properties: str


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _safe_archive_path(value: str) -> str:
    decoded = unquote(urlsplit(value).path).replace("\\", "/")
    if decoded.startswith("/"):
        raise BookParseError("PATH_TRAVERSAL", "EPUB 包含不安全的绝对路径。")
    normalized = posixpath.normpath(decoded)
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise BookParseError("PATH_TRAVERSAL", "EPUB 包含越界路径，已停止导入。")
    return normalized


def _resolve(base_file: str, href: str) -> str:
    base = str(PurePosixPath(base_file).parent)
    return _safe_archive_path(posixpath.join(base, href))


def _read_xml(data: bytes, error_message: str) -> Element:
    try:
        return SafeET.fromstring(data)
    except Exception as exc:
        raise BookParseError("BROKEN_EPUB", error_message) from exc


def _validate_archive(archive: zipfile.ZipFile, limits: ParserLimits) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > limits.max_zip_entries:
        raise BookParseError("ZIP_BOMB", "EPUB 内文件数量过多，已停止导入。")
    if "META-INF/encryption.xml" in archive.namelist():
        raise BookParseError("DRM_NOT_SUPPORTED", "这本 EPUB 已加密或带 DRM，无法导入。")

    total_size = 0
    by_path: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        safe_name = _safe_archive_path(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise BookParseError("PATH_TRAVERSAL", "EPUB 包含不安全的符号链接。")
        if info.flag_bits & 0x1:
            raise BookParseError("DRM_NOT_SUPPORTED", "这本 EPUB 已加密或带 DRM，无法导入。")
        if info.file_size > limits.max_entry_uncompressed_bytes:
            raise BookParseError("ZIP_BOMB", "EPUB 内单个文件解压后过大，已停止导入。")
        total_size += info.file_size
        if total_size > limits.max_total_uncompressed_bytes:
            raise BookParseError("ZIP_BOMB", "EPUB 解压后的总大小过大，已停止导入。")
        if info.file_size and info.compress_size == 0:
            raise BookParseError("ZIP_BOMB", "EPUB 压缩比例异常，已停止导入。")
        if info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
            raise BookParseError("ZIP_BOMB", "EPUB 压缩比例过高，可能是 Zip Bomb。")
        by_path[safe_name] = info
    return by_path


def _metadata_text(root: Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text:
            value = normalize_display_text(element.text)
            if value:
                return value
    return ""


def _nav_titles(archive: zipfile.ZipFile, item: ManifestItem) -> dict[str, str]:
    content = archive.read(item.path)
    soup = BeautifulSoup(content, "html.parser")
    result: dict[str, str] = {}
    for anchor in soup.select("nav a[href]"):
        href = str(anchor.get("href", ""))
        title = normalize_display_text(anchor.get_text(" ", strip=True))
        if href and title:
            result.setdefault(_resolve(item.path, href), title)
    return result


def _ncx_titles(archive: zipfile.ZipFile, item: ManifestItem) -> dict[str, str]:
    root = _read_xml(archive.read(item.path), "EPUB 的 NCX 目录已损坏。")
    result: dict[str, str] = {}
    for nav_point in root.iter():
        if _local_name(nav_point.tag) != "navPoint":
            continue
        title = ""
        href = ""
        for child in nav_point.iter():
            if _local_name(child.tag) == "text" and child.text and not title:
                title = normalize_display_text(child.text)
            if _local_name(child.tag) == "content" and not href:
                href = child.attrib.get("src", "")
        if title and href:
            result.setdefault(_resolve(item.path, href), title)
    return result


def parse_epub_chapters(
    path: Path,
    source_sha256: str,
    limits: ParserLimits,
) -> tuple[str, str, tuple[Chapter, ...], tuple[str, ...]]:
    if path.read_bytes()[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
        raise BookParseError("BAD_MAGIC", "文件扩展名是 EPUB，但内容不是有效的 EPUB。")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BookParseError("BROKEN_EPUB", "EPUB 文件已损坏，无法打开。") from exc

    with archive:
        entries = _validate_archive(archive, limits)
        mimetype = entries.get("mimetype")
        if mimetype is None or archive.read(mimetype).strip() != b"application/epub+zip":
            raise BookParseError("BROKEN_EPUB", "文件缺少正确的 EPUB 类型标记。")
        container = entries.get("META-INF/container.xml")
        if container is None:
            raise BookParseError("BROKEN_EPUB", "EPUB 缺少 container.xml。")
        container_root = _read_xml(archive.read(container), "EPUB 的 container.xml 已损坏。")
        opf_path = ""
        for element in container_root.iter():
            if _local_name(element.tag) == "rootfile":
                opf_path = _safe_archive_path(element.attrib.get("full-path", ""))
                break
        if not opf_path or opf_path not in entries:
            raise BookParseError("BROKEN_EPUB", "EPUB 找不到书籍清单文件。")

        opf_root = _read_xml(archive.read(entries[opf_path]), "EPUB 的书籍清单已损坏。")
        title = _metadata_text(opf_root, "title") or path.stem
        author = _metadata_text(opf_root, "creator")
        manifest: dict[str, ManifestItem] = {}
        for element in opf_root.iter():
            if _local_name(element.tag) != "item":
                continue
            item_id = element.attrib.get("id", "")
            href = element.attrib.get("href", "")
            if not item_id or not href:
                continue
            item_path = _resolve(opf_path, href)
            manifest[item_id] = ManifestItem(
                item_id=item_id,
                path=item_path,
                media_type=element.attrib.get("media-type", ""),
                properties=element.attrib.get("properties", ""),
            )

        toc_titles: dict[str, str] = {}
        nav_item = next((item for item in manifest.values() if "nav" in item.properties.split()), None)
        if nav_item and nav_item.path in entries:
            toc_titles.update(_nav_titles(archive, nav_item))
        ncx_item = next((item for item in manifest.values() if item.media_type == "application/x-dtbncx+xml"), None)
        if ncx_item and ncx_item.path in entries:
            toc_titles.update(_ncx_titles(archive, ncx_item))

        spine_items: list[ManifestItem] = []
        for element in opf_root.iter():
            if _local_name(element.tag) != "itemref" or element.attrib.get("linear", "yes").lower() == "no":
                continue
            item = manifest.get(element.attrib.get("idref", ""))
            if item and item.path in entries and item.media_type in {"application/xhtml+xml", "text/html"}:
                spine_items.append(item)

        chapters: list[Chapter] = []
        warnings: list[str] = []
        for item in spine_items:
            fallback_title, blocks = extract_html_blocks(archive.read(entries[item.path]), item.path)
            if not blocks:
                warnings.append(f"已跳过没有正文的页面：{item.path}")
                continue
            toc_title = toc_titles.get(item.path, "")
            chapter_title = (
                fallback_title
                if is_placeholder_chapter_title(toc_title)
                else toc_title or fallback_title
            )
            chapters.append(
                make_chapter(
                    source_sha256=source_sha256,
                    order=len(chapters),
                    title=chapter_title,
                    source_href=item.path,
                    blocks=blocks,
                )
            )

        if not chapters or not any(segment.spoken_text for chapter in chapters for segment in chapter.segments):
            raise BookParseError("EMPTY_BOOK", "这本 EPUB 没有可朗读的正文。")
        if not toc_titles:
            warnings.append("没有找到可靠目录，已按书内正文顺序生成章节。")
        return title, author, tuple(chapters), tuple(warnings)
