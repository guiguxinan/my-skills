# -*- coding: utf-8 -*-
"""
生成「二、用例映射表」（automation-script-generator 第 5 步专用，接口档位）。

映射表有 198 行，手写极易出错（漏号、重号、状态写错）。
本脚本从上游「可自动化用例清单」读出全部用例编号与总分，按配置分派
"文件 / 页面对象方法 / 设备矩阵 / 状态"，再整表重写进目标文档。

配置用 JSON 描述，便于不同项目复用：

{
  "module_file":  {"BOOT": "tests/app/test_startup_chain.py", "LOGIN": "tests/login/test_login.py"},
  "file_override":{"TC-BOOT-001": "tests/app/test_privacy_page.py"},
  "covered":      {"TC-LOGIN-001": "test_bound_login_success"},
  "partial":      {"TC-CAR-001": "test_love_car_page_elements"},
  "methods":      {"TC-BOOT-004": "PrivacyPage.TITLE、driver.background_app()"},
  "special":      {"TC-BOOT-015": "可运行（本次新增，与 TC-LOGIN-003 同一脚本）"},
  "skip":         {"TC-BOOT-005": "skip 待补（缺断网方案）"},
  "auto_min": 18
}

用法：
    python gen_mapping.py --from 04-自动化选型.md --to 05-自动化脚本.md --config cfg.json
    python gen_mapping.py ... --dry-run      # 只打印，不改文件
    python gen_mapping.py ... --section "二、用例映射表"
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, write_text, split_row, is_sep_row  # noqa: E402


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()


def find_tables(lines):
    tables = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip().startswith("|") and i + 1 < n and is_sep_row(lines[i + 1]):
            header = split_row(lines[i])
            rows = []
            j = i + 2
            while j < n and lines[j].strip().startswith("|"):
                if not is_sep_row(lines[j]):
                    rows.append(split_row(lines[j]))
                j += 1
            tables.append((header, rows))
            i = j
            continue
        i += 1
    return tables


def cell(row, header, key):
    for idx, h in enumerate(header):
        if h.strip() == key and idx < len(row):
            return row[idx].strip()
    return ""


def parse_upstream(text: str):
    """从上游 04 文档里读出全部用例行：编号、标题、类型、总分、批次、改造前置。

    「一、可自动化用例清单」与「有条件自动化用例清单」是两张同构的表，
    两张都要收；手工池是另一张表，单独收。
    """
    out = []
    seen = set()
    for header, rows in find_tables(text.split("\n")):
        if "用例编号" not in header:
            continue
        if "总分" not in header:
            # 手工池表
            for r in rows:
                cid = cell(r, header, "用例编号").strip()
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                out.append({
                    "id": cid, "title": cell(r, header, "用例标题").strip(),
                    "type": "", "total": "", "judge": cell(r, header, "对应判据").strip(),
                    "prereq": "", "batch": "手工", "manual": True,
                })
            continue
        for r in rows:
            cid = cell(r, header, "用例编号").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            out.append({
                "id": cid,
                "title": cell(r, header, "用例标题").strip(),
                "type": cell(r, header, "类型").strip(),
                "total": cell(r, header, "总分").strip(),
                "judge": cell(r, header, "判定依据").strip(),
                "prereq": cell(r, header, "改造前置").strip(),
                "batch": cell(r, header, "批次").strip(),
                "manual": False,
            })
    return out


def shorten(text, limit=40):
    text = (text or "").replace("|", "/").strip()
    if not text:
        return "—"
    return text if len(text) <= limit else text[:limit] + "…"


DEFAULT_COLUMNS = ["原用例编号", "文件", "函数或请求项", "状态"]


def _module_of(cid, cfg):
    """从用例编号里取模块前缀。cfg['module_regex'] 优先，否则取第二段。"""
    mr = cfg.get("module_regex")
    if mr:
        m = re.match(mr, cid)
        if m:
            return m.group(1)
    parts = cid.split("-")
    return parts[1] if len(parts) > 1 else ""


def build_rows(cases, cfg):
    module_file = cfg.get("module_file", {})
    file_override = cfg.get("file_override", {})
    covered = cfg.get("covered", {})
    partial = cfg.get("partial", {})
    methods = cfg.get("methods", {})
    special = cfg.get("special", {})
    skip = cfg.get("skip", {})
    auto_min = cfg.get("auto_min", 18)
    matrix_skip = cfg.get("matrix_skip", "skip 待补（设备池 E2–E6 未接入）")
    columns = cfg.get("columns", DEFAULT_COLUMNS)
    manual_method = cfg.get("manual_method", "—（依赖真实车辆 / 硬件动作）")
    pending_method = cfg.get("pending_method", "—（待实现）")
    matrix_method = cfg.get("matrix_method", "—（兼容矩阵用例，按设备分片执行）")
    default_device = cfg.get("default_device", "E1")

    rows = []
    for c in cases:
        cid = c["id"]
        module = _module_of(cid, cfg)
        path = file_override.get(cid) or module_file.get(module, "—")
        total = int(c["total"]) if str(c["total"]).isdigit() else 0
        conditional = 0 < total < auto_min

        if cid in special:
            method = methods.get(cid, "—")
            status = special[cid]
            devices = default_device
        elif cid in skip:
            method = methods.get(cid, "—")
            status = skip[cid]
            devices = default_device
        elif cid in covered:
            method = "既有 %s" % covered[cid]
            status = ("可运行（既有已覆盖；上游有条件卡点：%s）" % shorten(c["prereq"])
                      if conditional else "可运行（既有已覆盖）")
            devices = default_device
        elif cid in partial:
            method = "既有 %s（部分）+ 本次补齐字段断言" % partial[cid]
            status = ("可运行（本次补齐；上游有条件卡点：%s）" % shorten(c["prereq"])
                      if conditional else "可运行（本次补齐）")
            devices = default_device
        elif cid in methods:
            method = methods[cid]
            status = ("可运行（本次新增；上游有条件卡点：%s）" % shorten(c["prereq"])
                      if conditional else "可运行（本次新增）")
            devices = default_device
        elif c["manual"]:
            method = manual_method
            status = "skip 待补（手工池：一票否决）"
            devices = default_device
        elif c["batch"] == "兼容矩阵批":
            method = matrix_method
            status = matrix_skip
            devices = cfg.get("matrix_devices", "E1–E6")
        else:
            method = pending_method
            status = "skip 待补（上游批次：%s；改造前置：%s）" % (
                c["batch"] or "手工池", shorten(c["prereq"]))
            devices = default_device

        values = {
            "原用例编号": cid, "文件": path, "页面对象方法": method,
            "函数或请求项": method, "设备矩阵": devices, "状态": status,
        }
        rows.append("| " + " | ".join(values.get(col, "—") for col in columns) + " |")
    return rows


def replace_table_block(text: str, section: str, rows, header_key: str = "原用例编号") -> str:
    """把「二、用例映射表」节里那张表的**数据行**整表替换掉（保留表头与节内说明）。

    用 header_key 定位目标表——节内常还有"状态分布"这类辅助表，
    按第一张表替换会毁掉文档。
    """
    lines = text.split("\n")
    m = re.search(r"^#{2,3}\s*%s\s*$" % re.escape(section), "\n".join(lines), re.M)
    if not m:
        raise SystemExit("目标文档里找不到「%s」节" % section)
    start = text[:m.start()].count("\n")
    i = start
    n = len(lines)
    while i < n:
        if lines[i].strip().startswith("|") and i + 1 < n and is_sep_row(lines[i + 1]):
            header = split_row(lines[i])
            j = i + 2
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            if any(header_key in h for h in header):
                new_lines = lines[:i + 2] + rows + lines[j:]
                return "\n".join(new_lines)
            i = j
            continue
        if re.match(r"^#{2,3}\s", lines[i]) and i > start:
            break
        i += 1
    raise SystemExit("「%s」节里没找到表头含「%s」的表" % (section, header_key))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成用例映射表")
    ap.add_argument("--from", dest="src", required=True, help="上游可自动化清单(04)")
    ap.add_argument("--to", dest="dst", required=True, help="目标文档(05)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--section", default="二、用例映射表")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(io.open(args.config, encoding="utf-8"))
    cases = parse_upstream(read_text(args.src))
    rows = build_rows(cases, cfg)

    ids = [r.split("|")[1].strip() for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        print("⚠ 用例编号重复：%s" % dup)

    print("生成 %d 行映射（上游用例 %d 条）" % (len(rows), len(cases)))
    if args.dry_run:
        for r in rows[:5]:
            print("  " + r)
        print("  …")
        return 0

    dst_text = read_text(args.dst)
    new_text = replace_table_block(dst_text, args.section, rows)
    write_text(args.dst, new_text)
    print("已写入 %s" % args.dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
