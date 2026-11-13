# -*- coding: utf-8 -*-
"""
把上游「假设与待确认」原样带进下一份文档（app-pipeline 各步通用）。

流水线规则：上一步的「假设与待确认」原样带进下一步并累计，不删、不合并、不改写。
每一步都要做这件事，手工复制最容易漏编号、改措辞。

本脚本读上游文档里最后一个「假设与待确认」节（或指定节），原样搬到目标文档末尾，
并把内嵌标题降一级，避免与目标文档的章节层级打架。

用法：
    python carry_forward.py --from 04-自动化选型.md --to 05-自动化脚本.md
    python carry_forward.py --from 03.md --to 04.md --heading "假设与待确认" --dest-heading "待补清单"
    python carry_forward.py --from 03.md --to 04.md --dry-run

行为：
  - 目标文档已有同名内容时，默认跳过（--force 才覆盖）；
  - 搬运的内容原样保留（含编号、措辞），只调整标题层级。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import (read_text, write_text, find_section,  # noqa: E402
                     iter_headings, append_section)


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()


def find_assumption_headings(text: str) -> list[str]:
    """列出文档里所有「假设与待确认」相关的标题。"""
    return [title for _lvl, title, _idx in iter_headings(text)
            if "假设与待确认" in title or "待确认" in title]


def pick_section(text: str, heading: str | None):
    """
    取要搬运的整节内容。
    heading 为 None 时，取文档里**最后一个**「假设与待确认」节（避免重复搬前几轮的）。
    """
    if heading:
        start, end = find_section(text, heading)
        return text.split("\n")[start:end]
    candidates = find_assumption_headings(text)
    if not candidates:
        return None
    # 取**层级最高**的那节（通常是 "## 四、假设与待确认"）——
    # 它下面的所有子节才是要整体搬运的内容。
    # 直接取最后一个会把 "本步新增待确认" 这种小节当成全部，漏掉其余部分。
    best = None
    for lvl, title, _idx in iter_headings(text):
        if "假设与待确认" in title or "待确认" in title:
            if best is None or lvl < best[0]:
                best = (lvl, title)
    if best is None:
        return None
    start, end = find_section(text, best[1])
    return text.split("\n")[start:end]


CARRIED_MARK = "原样带入，未删改"
# 搬运块在目标文档里的标题形态：### 上游「xxx」（来自 yyy，原样带入，未删改）
CARRIED_HEAD_RE = re.compile(
    r"^(#{2,6})\s*上游「.*（来自 .*%s）\s*$" % re.escape(CARRIED_MARK))


def strip_cn_prefix(title: str) -> str:
    """去掉标题里的中文序号前缀：'四、假设与待确认' -> '假设与待确认'。"""
    return re.sub(r"^[一二三四五六七八九十]+、\s*", "", title.strip())


def find_carried_block(text: str):
    """在目标文档里找已搬运过的块，返回 (start, end) 行号区间；找不到返回 None。"""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if CARRIED_HEAD_RE.match(ln):
            start = i
            break
    if start is None:
        return None
    # 块延伸到下一个同级或更高级标题为止
    lvl = len(CARRIED_HEAD_RE.match(lines[start]).group(1))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[j])
        if m and len(m.group(1)) <= lvl:
            end = j
            break
    return start, end


def demote(block_lines, level=4):
    """把块内除首行外的标题降级（内层标题统一落到 level 之下）。"""
    out = []
    for i, ln in enumerate(block_lines):
        if i == 0:
            out.append(ln)
            continue
        m = re.match(r"^(#{1,6})\s", ln)
        if m:
            old = len(m.group(1))
            # old=3 -> level，old=4 -> level+1，依此类推
            new = level if old <= 3 else min(6, level + (old - 3))
            out.append("#" * new + ln[old:])
        else:
            out.append(ln)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="搬运上游「假设与待确认」")
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--heading", default=None, help="上游节标题；默认取最后一个待确认节")
    ap.add_argument("--dest-heading", default=None,
                    help="目标文档里放到哪个标题下；默认追加到文末")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_text = read_text(args.src)
    dst_text = read_text(args.dst)

    block = pick_section(src_text, args.heading)
    if not block:
        print("上游文档里找不到「假设与待确认」节")
        return 1

    first = block[0]
    m = re.match(r"^(#{1,6})\s*(.*)$", first)
    new_head = "### 上游「%s」（来自 %s，原样带入，未删改）" % (
        strip_cn_prefix(m.group(2)) if m else "假设与待确认",
        os.path.splitext(os.path.basename(args.src))[0])
    new_block = demote(block)[1:]
    # 去掉末尾多余空行，避免与目标文档的收尾空行叠出差异
    while new_block and not new_block[-1].strip():
        new_block.pop()
    payload = "\n".join([new_head] + new_block)

    existing = find_carried_block(dst_text)
    if existing and not args.force:
        print("目标文档已含搬运内容，跳过（--force 可覆盖）")
        return 0

    if args.dry_run:
        print("--- 将要插入的内容（前 20 行）---")
        print("\n".join(payload.split("\n")[:20]))
        return 0

    if existing:
        # 已有搬运块 → 原地替换，不重复追加
        lines = dst_text.split("\n")
        s, e = existing
        tail = lines[e:]
        sep = [""]
        dst_text = "\n".join(lines[:s] + payload.split("\n") + sep + tail)
    elif args.dest_heading:
        start, end = find_section(dst_text, args.dest_heading)
        lines = dst_text.split("\n")
        dst_text = "\n".join(lines[:end] + payload.split("\n") + [""] + lines[end:])
    else:
        dst_text = append_section(dst_text, payload)

    write_text(args.dst, dst_text)
    print("已搬运 %d 行到 %s" % (len(payload.split("\n")), args.dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
