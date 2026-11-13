# -*- coding: utf-8 -*-
r"""
测试用例表自检（test-case-writer 第 3 步专用）。

本 skill 的规则里有几条必须机械校验，人眼看会漏：
  - 编号 TC-<模块缩写>-<三位序号>，模块内连续、不跳号、不复用；
  - 步骤数与动作数一致，且预期结果与步骤同序号一一对应；
  - 前置条件里不能出现操作动作（出现即说明该写到步骤里去了）；
  - 优先级取值合法（P1/P2/P3，或团队约定的 P0–P2）；
  - 用例引用的设备编号必须能在「环境矩阵」表里找到；
  - 不允许出现虚构的 VIN / 手机号 / 账号。

用法：
    python check_cases.py ../../test-design/app/03-测试用例.md
    python check_cases.py ... --json out.json
    python check_cases.py ... --id-regex '^([A-Z]+-UI)-(\d{3})$' --allow-priority P0,P1,P2

`--id-regex` 用于适配不同编号体系：App 默认 `TC-<模块>-<序号>`，
Web 项目常用 `<模块>-UI-<序号>`（如 BRAND-UI-001）。正则需有两个捕获组：
模块前缀 + 序号。`--allow-priority` 同样可按团队约定改（如 P0,P1,P2）。
"""
from __future__ import annotations


import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, split_row, is_sep_row  # noqa: E402

CASE_HEADER_KEYS = ["用例编号", "步骤", "预期结果", "优先级", "用例标题"]
PHI_PATTERNS = [
    ("疑似手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("疑似 VIN", re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")),
    ("疑似车牌", re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-Z0-9]{5}\b")),
]


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()
def find_tables(lines: list[str]) -> list[list[list[str]]]:
    """把文档里的所有表格拆成 [[表头, [数据行...]], ...]。"""
    tables = []
    i = 0
    n = len(lines)
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


def cell(row: list[str], header: list[str], key: str) -> str:
    for idx, h in enumerate(header):
        if h.strip() == key and idx < len(row):
            return row[idx]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="测试用例表自检")
    ap.add_argument("src")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--allow-priority", default="P1,P2,P3",
                    help="允许的优先级取值，逗号分隔")
    ap.add_argument("--id-regex", dest="id_regex",
                    default=r"^TC-([A-Z]+)-(\d{3})$",
                    help="用例编号正则，需两个捕获组：模块前缀 + 序号")
    ap.add_argument("--steps-header", default="步骤",
                    help="步骤列名（接口用例写作「请求步骤」）")
    args = ap.parse_args()

    text = read_text(args.src)
    lines = text.split("\n")
    tables = find_tables(lines)

    allowed = set(x.strip() for x in args.allow_priority.split(",") if x.strip())

    # 环境矩阵里的设备编号
    devices = set()
    for header, rows in tables:
        if "设备编号" in header:
            for r in rows:
                devices.add(cell(r, header, "设备编号").strip())

    # 用例表
    cases = []
    for header, rows in tables:
        if "用例编号" not in header:
            continue
        for r in rows:
            cases.append({
                "id": cell(r, header, "用例编号").strip(),
                "title": cell(r, header, "用例标题").strip(),
                "priority": cell(r, header, "优先级").strip(),
                "prereq": cell(r, header, "前置条件").strip(),
                "steps": cell(r, header, args.steps_header).strip(),
                "expected": cell(r, header, "预期结果").strip(),
                "tp": cell(r, header, "关联测试点").strip(),
            })

    problems = []

    # 1) 编号：格式 + 模块内连续
    id_re = re.compile(args.id_regex)
    by_module = {}
    for c in cases:
        m = id_re.match(c["id"])
        if not m:
            problems.append("编号格式非法：%s" % c["id"])
            continue
        by_module.setdefault(m.group(1), []).append((int(m.group(2)), c["id"]))

    for mod, items in by_module.items():
        nums = sorted(x[0] for x in items)
        if len(set(nums)) != len(nums):
            dup = sorted({n for n in nums if nums.count(n) > 1})
            problems.append("模块 %s 编号重复：%s" % (mod, dup))
        if nums and nums != list(range(1, len(nums) + 1)):
            missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
            problems.append("模块 %s 编号跳号，缺：%s" % (mod, missing))

    # 2) 步骤数 == 预期数
    for c in cases:
        s = len(re.findall(r"(?:^|\s)\d+\.\s", c["steps"]))
        e = len(re.findall(r"(?:^|\s)\d+\.\s", c["expected"]))
        if s and e and s != e:
            problems.append("%s 步骤 %d 条但预期 %d 条（应一一对应）"
                            % (c["id"], s, e))

    # 3) 前置条件里混了操作动作
    #    只认"必须用手操作"的动作词；"登录成功进入我的爱车"是状态描述，不算动作。
    #    「进入/打开」单独出现时无法区分状态与动作，故不列入，避免误报。
    action_words = ["点击", "长按", "双击", "拖动", "滑动", "输入"]
    for c in cases:
        hits = [w for w in action_words if w in c["prereq"]]
        if hits:
            problems.append("%s 前置条件含操作动作 %s（应移到步骤）"
                            % (c["id"], "/".join(hits)))

    # 4) 优先级
    for c in cases:
        if c["priority"] and allowed and c["priority"] not in allowed:
            problems.append("%s 优先级取值非法：%s" % (c["id"], c["priority"]))

    # 5) 设备编号
    if devices:
        for c in cases:
            for dev in re.findall(r"\bE\d+\b", c["prereq"]):
                if dev not in devices:
                    problems.append("%s 引用了环境矩阵里没有的设备 %s" % (c["id"], dev))

    # 6) 疑似敏感数据
    for c in cases:
        for label, pat in PHI_PATTERNS:
            if pat.search(c["steps"]) or pat.search(c["prereq"]):
                problems.append("%s %s（应登记进数据准备清单，不要写死）"
                                % (c["id"], label))

    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"cases": cases, "devices": sorted(devices),
                                "problems": problems}, ensure_ascii=False, indent=2))
        print("已写出 %s" % args.json_out)

    print("用例 %d 条 / 设备 %d 个 / 表 %d 张" % (len(cases), len(devices), len(tables)))
    if problems:
        print("问题 %d 条：" % len(problems))
        for p in problems:
            print("  ⚠ %s" % p)
        return 1
    print("✓ 编号连续、步骤与预期一一对应、前置无操作、优先级合法、设备可溯源、无写死敏感数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
