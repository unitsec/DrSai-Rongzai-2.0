#!/usr/bin/env python3
"""build_docx.py —— 把这本书的 Markdown 转成适合打印的 DOCX（附 PDF）

为什么不用 pandoc？环境里没有，而且我们的需求很具体：
  * 中英文混排要好看（中文宋体、代码等宽、行距合适）；
  * 代码块要灰底等宽方框；表格要真的 Word 表格（表头跨页重复）；
  * A4、留装订边距、页脚页码、页眉显示当前章节名；每章另起一页；
  * 目录带**真实页码 + 超链接**（打印可用，Word 里可点）。

两遍构建（脚本自动完成）：
  第 1 遍：生成 docx + PDF → 从 PDF 量出每一章的实际页码 → 写 tools/toc_pages.json
  第 2 遍：把页码填进目录后再生成一次（目录行数不变，分页不漂移），并核对页码是否一致

用法：
    python tools/build_docx.py              # 完整构建：docx + pdf（推荐）
    python tools/build_docx.py --no-pdf     # 只出 docx（快）
    python tools/build_docx.py --pass1      # 只重新测量页码
    python tools/build_docx.py --check      # 只做链接自检
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import (WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING,
                            WD_TAB_ALIGNMENT, WD_TAB_LEADER)
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent
BOOK_DIR = HERE.parent
TITLE = "从零到智能体自主实验：EPICS 中文实践教程"
SUBTITLE = "面向软件工程师的散裂中子源设备接入指南"
OUT_STEM = "EPICS从零到智能体自主实验-完整书稿"
BUILD_DIR = BOOK_DIR / ".build"
PAGES_JSON = HERE / "toc_pages.json"

ORDER = [
    ("00-导读-这本书怎么用.md", "ch00"),
    ("01-软件工程师眼中的硬件.md", "ch01"),
    ("02-EPICS是什么-一张图看懂.md", "ch02"),
    ("03-搭实验台-30分钟跑起第一个IOC.md", "ch03"),
    ("04-记录与数据库-EPICS的日常.md", "ch04"),
    ("05-Python客户端-从caget到可靠读写.md", "ch05"),
    ("06-第一个闭环-虚拟恒温箱与PID.md", "ch06"),
    ("07-真硬件-两百元实验台.md", "ch07"),
    ("08-智能体驱动实验-从MCP到自主闭环.md", "ch08"),
    ("09-安全与工程化-散裂级别的现实.md", "ch09"),
    ("10-综合实战-自动寻峰衍射仪.md", "ch10"),
    ("附录A-速查表.md", "apA"),
    ("附录B-术语表.md", "apB"),
    ("附录C-踩坑清单.md", "apC"),
    ("附录D-资料精读指南.md", "apD"),
    ("附录E-90天路线图.md", "apE"),
    ("附录F-配套代码清单.md", "apF"),
]

FONT_CJK, FONT_CJK_HEAD = "宋体", "微软雅黑"
FONT_LATIN, FONT_CODE = "Georgia", "Consolas"
CODE_SIZE = Pt(8.5)
CODE_SHADING, TABLE_HEAD_SHADING, QUOTE_SHADING = "F2F2F2", "E8EEF7", "FFF8E1"
CONTENT_WIDTH_CM = 16.8          # A4 宽 21 - 左 2.2 - 右 2.0


# ============================================================ 低层工具
def set_run_fonts(run, cjk=FONT_CJK, latin=FONT_LATIN, size=None, bold=None,
                  italic=None, color=None):
    run.font.name = latin
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for key in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(key), latin)
    rfonts.set(qn("w:eastAsia"), cjk)
    if size is not None:
        run.font.size = size
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color
    return run


def shade(element, fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    element.append(shd)


def style_shade(style, fill):
    shade(style.element.get_or_add_pPr(), fill)


def para_border(paragraph, edges=("bottom",), size=6, color="BFBFBF"):
    borders = OxmlElement("w:pBdr")
    for edge in edges:
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "4")
        el.set(qn("w:color"), color)
        borders.append(el)
    paragraph._p.get_or_add_pPr().append(borders)


def add_field(paragraph, instr, placeholder=""):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = instr
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr_el, sep, text, end):
        run._element.append(el)
    return run


def add_bookmark(paragraph, name, bid):
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bid))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bid))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def add_internal_link(paragraph, anchor, text, size=Pt(10)):
    """目录条目：指向章节书签的超链接（Word 里可 Ctrl+点击跳转）。"""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), anchor)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rfonts = OxmlElement("w:rFonts")
    rfonts.set(qn("w:ascii"), FONT_LATIN)
    rfonts.set(qn("w:hAnsi"), FONT_LATIN)
    rfonts.set(qn("w:eastAsia"), FONT_CJK)
    rpr.append(rfonts)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size.pt * 2)))
    rpr.append(sz)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F4E79")
    rpr.append(color)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(rpr)
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def add_page_break(doc):
    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)
    return p


# ============================================================ 文档骨架
def build_document(with_running_head=True) -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin, section.right_margin = Cm(2.2), Cm(2.0)
    section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
    section.different_first_page_header_footer = True

    normal = doc.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    normal.paragraph_format.line_spacing = 1.25
    normal.paragraph_format.space_after = Pt(5)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts")) or OxmlElement("w:rFonts")
    for key, val in (("w:eastAsia", FONT_CJK), ("w:ascii", FONT_LATIN),
                     ("w:hAnsi", FONT_LATIN)):
        rfonts.set(qn(key), val)
    if rfonts.getparent() is None:
        rpr.append(rfonts)

    for level, size in {1: 19, 2: 14.5, 3: 12, 4: 11}.items():
        st = doc.styles[f"Heading {level}"]
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = (RGBColor(0x1F, 0x33, 0x64) if level <= 2
                             else RGBColor(0x2A, 0x2A, 0x2A))
        h_rpr = st.element.get_or_add_rPr()
        h_rfonts = OxmlElement("w:rFonts")
        h_rfonts.set(qn("w:eastAsia"), FONT_CJK_HEAD)
        h_rfonts.set(qn("w:ascii"), "Segoe UI")
        h_rfonts.set(qn("w:hAnsi"), "Segoe UI")
        h_rpr.append(h_rfonts)
        st.paragraph_format.space_before = Pt(14 if level <= 2 else 10)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True

    code = doc.styles.add_style("CodeBlock", 1)
    code.base_style = doc.styles["Normal"]
    code.font.size = CODE_SIZE
    c_rpr = code.element.get_or_add_rPr()
    c_rfonts = OxmlElement("w:rFonts")
    for key in ("w:ascii", "w:hAnsi", "w:cs"):
        c_rfonts.set(qn(key), FONT_CODE)
    c_rfonts.set(qn("w:eastAsia"), FONT_CJK)
    c_rpr.append(c_rfonts)
    style_shade(code, CODE_SHADING)
    code.paragraph_format.space_before = Pt(0)
    code.paragraph_format.space_after = Pt(0)
    code.paragraph_format.line_spacing = 1.0
    code.paragraph_format.left_indent = Cm(0.3)

    quote = doc.styles.add_style("Callout", 1)
    quote.base_style = doc.styles["Normal"]
    style_shade(quote, QUOTE_SHADING)
    quote.paragraph_format.left_indent = Cm(0.4)
    quote.paragraph_format.right_indent = Cm(0.2)
    quote.paragraph_format.space_before = Pt(4)
    quote.paragraph_format.space_after = Pt(4)

    # 页脚：页码（首页不显示，因为设置了 different_first_page）
    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(fp, "PAGE", "1")

    # 页眉：当前章节名（Word 域，随位置变化）
    if with_running_head:
        hp = section.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = hp.add_run()
        set_run_fonts(run, size=Pt(8.5), color=RGBColor(0x90, 0x90, 0x90))
        add_field(hp, 'STYLEREF "Heading 1" \\* MERGEFORMAT', "")
        para_border(hp, edges=("bottom",), size=4, color="D0D0D0")
    return doc


def add_title_page(doc):
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_fonts(p.add_run(TITLE), FONT_CJK_HEAD, "Segoe UI", size=Pt(26), bold=True,
                  color=RGBColor(0x1F, 0x33, 0x64))
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_fonts(p.add_run(SUBTITLE), FONT_CJK_HEAD, "Segoe UI", size=Pt(13),
                  color=RGBColor(0x44, 0x44, 0x44))
    doc.add_paragraph()
    for line in [
        "读者：有软件开发 / 数据分析基础、没有硬件知识的人",
        "目标：把散裂中子源的硬件设备接入智能体，自主驱动实验",
        "正文：第 0~10 章 + 附录 A~F，45 个练习",
        "代码：配套 24 个文件，全部在 EPICS Base 7.0.10 上实测运行过",
        "版本：全部结论基于 EPICS Base 7.0.10（源码版与 conda-forge 版）实测",
    ]:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_run_fonts(p.add_run(line), size=Pt(10.5), color=RGBColor(0x33, 0x33, 0x33))
        p.paragraph_format.space_after = Pt(2)
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_fonts(p.add_run("⚠ 安全提示：本书所有硬件练习只用 5V/12V 低压直流，不涉及市电。\n"
                            "接入真实设备前请阅读第 9 章，并完成第 9.7 节的清单。"),
                  size=Pt(10), color=RGBColor(0xA0, 0x30, 0x30))


def chapter_title_of(md_path: Path) -> str:
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return md_path.stem


def add_toc_page(doc, pages):
    """目录：带真实页码 + 内部超链接。"""
    h = doc.add_paragraph()
    h.paragraph_format.page_break_before = True
    h.paragraph_format.space_after = Pt(10)
    set_run_fonts(h.add_run("目录"), FONT_CJK_HEAD, "Segoe UI", size=Pt(19), bold=True,
                  color=RGBColor(0x1F, 0x33, 0x64))
    note = doc.add_paragraph()
    set_run_fonts(note.add_run("页码为 A4 打印版实际页码；在 Word / WPS 里 Ctrl+点击条目可直接跳转。"),
                  size=Pt(9.5), color=RGBColor(0x70, 0x70, 0x70))

    for file_name, anchor in ORDER:
        title = chapter_title_of(BOOK_DIR / file_name)
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.tab_stops.add_tab_stop(
            Cm(CONTENT_WIDTH_CM), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        add_internal_link(p, anchor, title)
        tail = f"\t{pages.get(file_name, '')}" if pages else "\t—"
        set_run_fonts(p.add_run(tail), size=Pt(10), color=RGBColor(0x40, 0x40, 0x40))


# ============================================================ 行内解析
INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*\n]+?\*|\[[^\]]+\]\([^)]+\))")


def add_inline(paragraph, text, base_size=None):
    text = text.replace("\\|", "|").replace("\\*", "*").replace("\\_", "_")
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            set_run_fonts(paragraph.add_run(part[2:-2]), size=base_size, bold=True)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            size = Pt((base_size.pt if base_size else 10.5) - 1.2)
            set_run_fonts(paragraph.add_run(part[1:-1]), latin=FONT_CODE, size=size,
                          color=RGBColor(0x8B, 0x2E, 0x1A))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            set_run_fonts(paragraph.add_run(part[1:-1]), size=base_size, italic=True)
        elif part.startswith("[") and "](" in part:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            # 链接文字可能是 `代码` 形式（书里大量指向代码文件），去掉反引号按代码排版
            label_text, is_code = label, False
            if label.startswith("`") and label.endswith("`") and len(label) > 2:
                label_text, is_code = label[1:-1], True
            if is_code:
                set_run_fonts(paragraph.add_run(label_text), latin=FONT_CODE,
                              size=Pt((base_size.pt if base_size else 10.5) - 1.2),
                              color=RGBColor(0x1F, 0x4E, 0x79))
            else:
                set_run_fonts(paragraph.add_run(label_text), size=base_size,
                              color=RGBColor(0x1F, 0x4E, 0x79))
            # 只在"外链"时把网址印出来（纸上能照着敲）；指向本仓库的相对路径不重复打印
            if url.startswith(("http://", "https://")) and label_text != url:
                size = Pt((base_size.pt if base_size else 10.5) - 1.5)
                set_run_fonts(paragraph.add_run(f"（{url}）"), size=size,
                              color=RGBColor(0x80, 0x80, 0x80))
        else:
            set_run_fonts(paragraph.add_run(part), size=base_size)


# ============================================================ 块级解析
def is_table_row(line):
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def split_row(line):
    return [c.strip().replace("\\|", "|")
            for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def is_sep_row(line):
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line.strip())) and "-" in line


def add_code_block(doc, lines):
    if not lines:
        return
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.cell(0, 0)
    cell.text = ""
    shade(cell._tc.get_or_add_tcPr(), CODE_SHADING)
    first = True
    for line in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.style = doc.styles["CodeBlock"]
        set_run_fonts(p.add_run(line if line.strip() else " "),
                      latin=FONT_CODE, size=CODE_SIZE)
    gap = doc.add_paragraph()
    gap.paragraph_format.space_after = Pt(2)
    gap.paragraph_format.space_before = Pt(0)


def add_table(doc, rows):
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r_index, row in enumerate(rows):
        cells = table.add_row().cells
        for c_index in range(ncols):
            text = row[c_index] if c_index < len(row) else ""
            cell = cells[c_index]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.05
            add_inline(p, text, base_size=Pt(9))
            if r_index == 0:
                for run in p.runs:
                    run.font.bold = True
                shade(cell._tc.get_or_add_tcPr(), TABLE_HEAD_SHADING)
    trPr = table.rows[0]._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    trPr.append(header)
    gap = doc.add_paragraph()
    gap.paragraph_format.space_after = Pt(3)
    gap.paragraph_format.space_before = Pt(0)


def convert_markdown(doc, text, anchor=None, bookmark_id=None):
    if bookmark_id is None:
        bookmark_id = [1000]
    lines = text.splitlines()
    i = 0
    in_code = False
    code_lines = []
    quote_lines = []

    def flush_quote():
        if quote_lines:
            joined = " ".join(l.strip().lstrip(">").strip()
                              for l in quote_lines if l.strip())
            if joined:
                p = doc.add_paragraph(style="Callout")
                add_inline(p, joined, base_size=Pt(10))
                para_border(p, edges=("left",), size=18, color="E0B000")
            quote_lines.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if not in_code:
                in_code, code_lines = True, []
            else:
                in_code = False
                add_code_block(doc, code_lines)
            i += 1
            continue
        if in_code:
            code_lines.append(line.rstrip())
            i += 1
            continue

        if stripped.startswith(">"):
            quote_lines.append(stripped)
            i += 1
            continue
        flush_quote()

        if is_table_row(line) and i + 1 < len(lines) and is_sep_row(lines[i + 1]):
            rows = [split_row(line)]
            i += 2
            while i < len(lines) and is_table_row(lines[i]):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows)
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(6)
            para_border(p, edges=("bottom",), size=6, color="CCCCCC")
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            h = doc.add_heading(level=min(level, 4))
            add_inline(h, title)
            if level == 1:
                h.paragraph_format.page_break_before = True
                h.paragraph_format.space_before = Pt(0)
                if anchor:
                    bookmark_id[0] += 1
                    add_bookmark(h, anchor, bookmark_id[0])
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            indent, marker, content = m.group(1), m.group(2), m.group(3)
            depth = len(indent) // 2
            if content.startswith("[ ]"):
                content = "☐ " + content[3:].strip()
            elif content.startswith("[x]"):
                content = "☑ " + content[3:].strip()
            style = "List Number" if marker[0].isdigit() else "List Bullet"
            p = doc.add_paragraph(style=style)
            p.paragraph_format.left_indent = Cm(0.6 + 0.5 * depth)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.2
            add_inline(p, content)
            i += 1
            continue

        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()
            if (not ns or ns.startswith(("#", ">", "```", "|"))
                    or re.fullmatch(r"(-{3,}|\*{3,})", ns)
                    or re.match(r"^(\s*)([-*+]|\d+\.)\s+", nxt)):
                break
            para_lines.append(ns)
            i += 1
        add_inline(doc.add_paragraph(), " ".join(para_lines))
    flush_quote()


# ============================================================ 构建与校验
def build_docx(pages):
    doc = build_document()
    add_title_page(doc)
    add_toc_page(doc, pages)
    bookmark_id = [1000]
    for idx, (name, anchor) in enumerate(ORDER):
        path = BOOK_DIR / name
        if not path.exists():
            print(f"!! 缺少 {name}")
            continue
        convert_markdown(doc, path.read_text(encoding="utf-8"),
                         anchor=anchor, bookmark_id=bookmark_id)
        if idx != len(ORDER) - 1:
            add_page_break(doc)
    out = BOOK_DIR / f"{OUT_STEM}.docx"
    doc.save(out)
    return out


def to_pdf(docx_path):
    profile = BUILD_DIR / "lo-profile"
    for sub in ("lo-profile", "config", "cache"):
        (BUILD_DIR / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(BUILD_DIR)
    env["XDG_CONFIG_HOME"] = str(BUILD_DIR / "config")
    env["XDG_CACHE_HOME"] = str(BUILD_DIR / "cache")
    subprocess.run(["libreoffice", "--headless",
                    f"-env:UserInstallation=file://{profile}",
                    "--convert-to", "pdf", "--outdir", str(docx_path.parent),
                    str(docx_path)],
                   capture_output=True, text=True, timeout=900, env=env)
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def measure_pages(pdf):
    """从 PDF 量出每章起始页（跳过带点线引导的目录行）。"""
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    pdf_pages = txt.split("\f")

    def norm(s):
        return re.sub(r"\s+", "", s)

    mapping = {}
    for file_name, _anchor in ORDER:
        # 只用标题前 12 个字符做键：标题太长时 PDF 里会折行，
        # 键取太长就永远匹配不上（第 5 章就踩过这个坑）。
        key = norm(chapter_title_of(BOOK_DIR / file_name))[:12]
        for pno, ptext in enumerate(pdf_pages, 1):
            found = False
            for line in ptext.splitlines():
                if not line.strip():
                    continue
                if "…" in line or re.search(r"\.{3,}", line):
                    continue                      # 目录行，跳过
                if key and key in norm(line):
                    mapping[file_name] = pno
                    found = True
                    break
            if found:
                break
    return mapping


def check_links():
    link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    missing, checked = [], 0
    for md in list(BOOK_DIR.glob("*.md")) + list((BOOK_DIR / "code").rglob("*.md")):
        for label, target in link_re.findall(md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if target.lower().endswith((".docx", ".pdf")):
                continue                          # 本文档的产物
            target = target.split("#")[0].strip()
            if not target:
                continue
            checked += 1
            if not (md.parent / target).resolve().exists():
                missing.append(f"{md.name}: [{label}]({target})")
    print(f"[自检] 相对链接 {checked} 个，缺失 {len(missing)} 个")
    for m in missing:
        print("   ✗", m)
    return len(missing)


def verify_layout(pdf):
    """排版自检：文字有没有越出可打印区域、有没有空页（页脚页码不算越界）。"""
    import xml.etree.ElementTree as ET
    xml = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    root = ET.fromstring(xml)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    right, bottom = 538.6, 785.2
    over_x = over_y_body = 0
    empty = []
    pages = root.findall(".//x:page", ns)
    for pno, page in enumerate(pages, 1):
        words = page.findall(".//x:word", ns)
        if not words:
            empty.append(pno)
            continue
        over_x += sum(1 for w in words if float(w.get("xMax")) > right + 2)
        # 正文不该进下边距；页脚页码在页边距里，属正常
        for w in words:
            if float(w.get("yMax")) > bottom and (w.text or "").strip() not in ("",) :
                txt = (w.text or "").strip()
                if not txt.isdigit():
                    over_y_body += 1
    print(f"[排版自检] {len(pages)} 页；越出右边距 {over_x} 词；"
          f"正文侵入下边距 {over_y_body} 词；空页 {empty if empty else '无'}")


def print_stats(docx_path):
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    print(f"[结构] 一级标题 {xml.count('w:val=\"Heading1\"')}，"
          f"二级 {xml.count('w:val=\"Heading2\"')}，三级 {xml.count('w:val=\"Heading3\"')}；"
          f"表格 {xml.count('<w:tbl>')}；代码段落 {xml.count('w:val=\"CodeBlock\"')}；"
          f"书签 {xml.count('<w:bookmarkStart')}")


def main():
    ap = argparse.ArgumentParser(description="构建打印用 DOCX/PDF")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--pass1", action="store_true", help="只重新测量页码")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        return 1 if check_links() else 0
    check_links()

    pages = None
    if PAGES_JSON.exists() and not args.pass1:
        pages = json.loads(PAGES_JSON.read_text(encoding="utf-8"))
        print(f"[目录] 使用已测量的页码（{PAGES_JSON.name}，{len(pages)} 章）")

    if args.no_pdf:
        docx_path = build_docx(pages)
        print(f"[完成] {docx_path.name}  {docx_path.stat().st_size/1024:.0f} KB")
        print_stats(docx_path)
        if pages is None:
            print("（未生成 PDF，无法测量页码；要页码请跑完整构建）")
        return 0

    # 第一遍：生成 + 测量页码
    docx_path = build_docx(pages)
    print(f"[完成] {docx_path.name}  {docx_path.stat().st_size/1024:.0f} KB")
    print_stats(docx_path)
    pdf = to_pdf(docx_path)
    if not pdf:
        print("[警告] PDF 未生成（LibreOffice 转换失败）")
        return 1
    verify_layout(pdf)
    measured = measure_pages(pdf)
    missing = [f for f, _ in ORDER if f not in measured]
    if missing:
        print(f"[警告] 有 {len(missing)} 章没量到页码：{missing}")
    PAGES_JSON.write_text(json.dumps(measured, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    for file_name, anchor in ORDER:
        title = chapter_title_of(BOOK_DIR / file_name)
        print(f"        {title[:34]:<36} 第 {measured.get(file_name, '?'):>3} 页")

    # 第二遍：把页码写进目录，重建并核对页码没有漂移
    if not args.pass1:
        docx_path = build_docx(measured)
        pdf = to_pdf(docx_path)
        if pdf:
            again = measure_pages(pdf)
            drift = {k: (measured.get(k), again.get(k))
                     for k in measured if measured.get(k) != again.get(k)}
            if drift:
                print(f"[第二遍] ⚠ 页码有漂移（再跑一次可收敛）：{drift}")
            else:
                print("[第二遍] 目录页码与正文一致 ✓")
            verify_layout(pdf)
            size = pdf.stat().st_size / 1024
            n = len(subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                                   capture_output=True, text=True).stdout.split("\f")) - 1
            print(f"[最终] {pdf.name}  {size:.0f} KB  {n} 页")
    return 0


if __name__ == "__main__":
    sys.exit(main())
