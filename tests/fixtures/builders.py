from __future__ import annotations

import zipfile
from pathlib import Path


CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def _write_epub(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        for name, content in files.items():
            archive.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
    return path


def make_epub3_nav(path: Path) -> Path:
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>三径书屋</dc:title><dc:creator>测试作者甲</dc:creator>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    nav = """<html xmlns="http://www.w3.org/1999/xhtml"><body><nav epub:type="toc">
<ol><li><a href="text/chapter1.xhtml">卷一 入园</a><ol><li><a href="text/chapter2.xhtml#tea">卷二 煮茶</a></li></ol></li></ol>
</nav></body></html>"""
    chapter1 = """<html><body><h1>入园</h1><p>石径穿过竹林，清晨的露水还在叶尖。</p>
<p>抬头可见<ruby>山<rt>shān</rt></ruby>色，低头便闻到草木气息。</p></body></html>"""
    chapter2 = """<html><body><h1 id="tea">煮茶</h1><p>水初沸时，声音像松风。</p>
<aside epub:type="footnote">项目自制脚注，只显示而不朗读。</aside></body></html>"""
    return _write_epub(
        path,
        {
            "OEBPS/content.opf": opf,
            "OEBPS/nav.xhtml": nav,
            "OEBPS/text/chapter1.xhtml": chapter1,
            "OEBPS/text/chapter2.xhtml": chapter2,
        },
    )


def make_epub2_ncx(path: Path) -> Path:
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>灯下两章</dc:title><dc:creator>测试作者乙</dc:creator></metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>
    <item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="cover" linear="no"/><itemref idref="a"/><itemref idref="b"/></spine>
</package>"""
    ncx = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
<navMap><navPoint id="n1"><navLabel><text>上篇 风来</text></navLabel><content src="a.xhtml"/></navPoint>
<navPoint id="n2"><navLabel><text>下篇 月明</text></navLabel><content src="b.xhtml"/></navPoint></navMap></ncx>"""
    return _write_epub(
        path,
        {
            "OEBPS/content.opf": opf,
            "OEBPS/toc.ncx": ncx,
            "OEBPS/cover.xhtml": "<html><body><img src='cover.jpg'/></body></html>",
            "OEBPS/a.xhtml": "<html><body><p>风从半开的窗间经过，书页轻响。</p></body></html>",
            "OEBPS/b.xhtml": "<html><body><p>月色落在案头，字迹比白日更清楚。</p></body></html>",
        },
    )


def make_epub_without_toc(path: Path) -> Path:
    opf = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>无目录小记</dc:title></metadata>
<manifest><item id="one" href="part/one.html" media-type="text/html"/><item id="two" href="part/two.html" media-type="text/html"/></manifest>
<spine><itemref idref="one"/><itemref idref="two"/></spine></package>"""
    return _write_epub(
        path,
        {
            "OEBPS/content.opf": opf,
            "OEBPS/part/one.html": "<html><body><h2>第一札</h2><p>没有目录，也应当读到这段正文。</p></body></html>",
            "OEBPS/part/two.html": "<html><body><h2>第二札</h2><p>系统按书内顺序继续整理。</p></body></html>",
        },
    )


def make_epub_with_placeholder_nav(path: Path) -> Path:
    opf = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>献词测试</dc:title></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="one" href="Section0001.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="one"/></spine></package>"""
    nav = """<html><body><nav><a href="Section0001.xhtml">Section0001</a></nav></body></html>"""
    dedication = """<html><head><title>Section0001</title></head><body>
<p>献给倾听我故事的伦纳德</p><p>以及所有帮助过我的朋友</p></body></html>"""
    return _write_epub(
        path,
        {
            "OEBPS/content.opf": opf,
            "OEBPS/nav.xhtml": nav,
            "OEBPS/Section0001.xhtml": dedication,
        },
    )


def make_empty_epub(path: Path) -> Path:
    opf = """<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/>
<manifest><item id="x" href="x.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="x"/></spine></package>"""
    return _write_epub(path, {"OEBPS/content.opf": opf, "OEBPS/x.xhtml": "<html><body><img src='x.jpg'/></body></html>"})


def make_drm_epub(path: Path) -> Path:
    make_epub_without_toc(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("META-INF/encryption.xml", "<encryption/>")
    return path


def make_traversal_epub(path: Path) -> Path:
    make_epub_without_toc(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../outside.txt", "不应解压到书外")
    return path


def make_high_ratio_epub(path: Path) -> Path:
    make_epub_without_toc(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("OEBPS/repeated.bin", b"A" * 100_000, compress_type=zipfile.ZIP_DEFLATED)
    return path
