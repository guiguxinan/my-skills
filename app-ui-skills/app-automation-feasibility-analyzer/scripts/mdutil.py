# -*- coding: utf-8 -*-
"""
Markdown 文档通用工具（各 skill scripts/ 下各存一份同名副本，内容一致）。

只依赖标准库。所有读写一律 UTF-8；不引入第三方包，保证在任何环境可跑。

提供：
  read_text / write_text          —— 文件读写
  split_row / parse_table         —— 表格行切分与表格解析
  iter_table_rows                 —— 遍历某文件中所有表格行
  replace_section                 —— 按标题替换整节
  insert_after_heading            —— 在某标题后插入内容
  extract_code_blocks             —— 提取 ```lang ... ``` 代码块
  count_sections                  —— 统计各级标题数量
  collect_ids                     —— 收集形如 A-01 / TC-BOOT-001 的编号
  append_section                  —— 在文末追加一节
  strip_blank_in_tables           —— 清掉表格里的空行（Markdown 表格不能有空行）
"""
from __future__ import annotations

import io
import os
import re

# ---------------------------------------------------------------- 文件读写


def read_text(path: str) -> str:
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------- 表格处理

_ROW_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def split_row(line: str) -> list[str]:
    """把一行 Markdown 表格切成单元格列表（去掉首尾空单元格）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_sep_row(line: str) -> bool:
    return bool(_ROW_SEP.match(line)) and "|" in line


def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """
    从若干行里解析一张表：返回 (表头, 数据行)。
    表头 = 第一行非分隔行；分隔行被跳过。
    """
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        if is_sep_row(line):
            continue
        cells = split_row(line)
        if not header:
            header = cells
        else:
            rows.append(cells)
    return header, rows


def iter_table_rows(text: str) -> list[list[str]]:
    """遍历文档中所有表格的所有数据行（不含表头）。"""
    out: list[list[str]] = []
    for chunk in re.split(r"\n\s*\n", text):
        lines = [ln for ln in chunk.split("\n") if ln.strip().startswith("|")]
        if not lines:
            continue
        _, rows = parse_table(lines)
        out.extend(rows)
    return out


def strip_blank_in_tables(text: str) -> str:
    """删除表格块内部的空行（表格块之间被空行切开时，逐块处理）。"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        out.append(lines[i])
        if lines[i].strip().startswith("|") and is_sep_row(lines[i]):
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and lines[j].strip().startswith("|"):
                out.extend(lines[i + 1:j])
                i = j
                continue
        i += 1
    return "\n".join(out)


# ---------------------------------------------------------------- 章节操作


def _heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def iter_headings(text: str):
    """产出 (层级, 标题文本, 行号)。"""
    for idx, line in enumerate(text.split("\n")):
        lvl = _heading_level(line)
        if lvl:
            yield lvl, line.lstrip("#").strip(), idx


def find_section(text: str, heading: str) -> tuple[int, int]:
    """
    找到标题 `heading`（不含 #）所在整节的 [start, end) 行号区间。
    区间从标题行开始，到下一个同级或更高级标题行之前结束。
    找不到时抛 ValueError。
    """
    lines = text.split("\n")
    target = None
    for idx, line in enumerate(lines):
        lvl = _heading_level(line)
        if lvl and line.lstrip("#").strip() == heading:
            target = (lvl, idx)
            break
    if target is None:
        raise ValueError("找不到标题：%s" % heading)
    lvl, start = target
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        l2 = _heading_level(lines[idx])
        if l2 and l2 <= lvl:
            end = idx
            break
    return start, end


def replace_section(text: str, heading: str, new_body: str) -> str:
    """替换某标题下的正文（保留标题行本身）。"""
    lines = text.split("\n")
    start, end = find_section(text, heading)
    head = lines[start]
    body = new_body.rstrip("\n").split("\n")
    return "\n".join(lines[: start + 1] + body + lines[end:])


def insert_after_heading(text: str, heading: str, block: str) -> str:
    """在某标题（连同其正文）之后插入一段内容。"""
    lines = text.split("\n")
    _, end = find_section(text, heading)
    return "\n".join(lines[:end] + block.rstrip("\n").split("\n") + lines[end:])


def append_section(text: str, block: str) -> str:
    """在文末追加内容（自动补一个空行）。"""
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


def demote_headings(text: str, heading: str, level: int = 4) -> str:
    """把某节内部的所有标题整体降/升到指定层级（用于嵌套搬运）。"""
    lines = text.split("\n")
    start, end = find_section(text, heading)
    for i in range(start + 1, end):
        m = re.match(r"^(#{1,6})\s", lines[i])
        if m:
            old = len(m.group(1))
            new = max(1, min(6, level + (old - 2)))
            lines[i] = "#" * new + lines[i][old:]
    return "\n".join(lines)


# ---------------------------------------------------------------- 代码块


def extract_code_blocks(text: str, lang: str | None = None):
    """提取代码块，返回 [(lang, code, start_line, end_line)]。"""
    blocks = []
    lines = text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        m = re.match(r"^```(\w*)\s*$", lines[i])
        if m:
            lang_here = m.group(1)
            j = i + 1
            body = []
            while j < n and not lines[j].strip().startswith("```"):
                body.append(lines[j])
                j += 1
            if lang is None or lang_here == lang:
                blocks.append((lang_here, "\n".join(body), i, j))
            i = j + 1
            continue
        i += 1
    return blocks


# ---------------------------------------------------------------- 统计


def count_sections(text: str) -> dict:
    """统计各级标题数量：{'h1': n, 'h2': n, 'h3': n, 'h4': n, 'total': n}。"""
    cnt = {"h1": 0, "h2": 0, "h3": 0, "h4": 0, "total": 0}
    for lvl, _, _ in iter_headings(text):
        cnt["total"] += 1
        key = "h%d" % lvl
        if key in cnt:
            cnt[key] += 1
    return cnt


def collect_ids(text: str, pattern: str = r"\b([A-Z]{1,3}(?:-[A-Z]+)*-\d{2,3})\b") -> list[str]:
    """收集形如 A-01 / TC-BOOT-001 的编号（去重、保序）。"""
    seen = []
    for m in re.finditer(pattern, text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen
