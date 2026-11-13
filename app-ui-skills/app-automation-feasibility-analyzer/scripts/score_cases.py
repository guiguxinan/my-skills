# -*- coding: utf-8 -*-
"""
五维打分与分池核算（automation-feasibility-analyzer 第 4 步专用）。

本 skill 的规则里最容易被"空转"的地方：
  - 每维 1–5 分，总分 = 五维之和；
  - ≥18 可自动化 / 12–17 有条件 / <12 手工；
  - 一票否决优先于分数（命中即手工，不论分数多高）；
  - 汇总表的数字必须与清单一致。

本脚本按列名解析上游用例表与本步产物，核算这些数字并生成汇总表骨架，
避免手数、手加出错。

用法：
    python score_cases.py ../../test-design/app/03-测试用例.md --out 选型草稿.md
    python score_cases.py ../../test-design/app/04-自动化选型.md --verify
    python score_cases.py ... --csv scores.csv     # 打分输入模板

CSV 模板列：编号,频,稳,数,断,本,否决,类型,批次,前置
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, split_row, is_sep_row  # noqa: E402


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()

AUTO_MIN = 18
COND_MAX = 17
VETO_WORDS = ["感官", "真实车辆", "硬件", "来电", "系统弹窗", "反调试", "一次性活动", "不可回滚"]


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


def parse_score_csv(path):
    """读打分 CSV → (rows, problems)。"""
    rows, problems = [], []
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for rec in csv.DictReader(f):
            cid = (rec.get("编号") or "").strip()
            dims = []
            bad = False
            for k in ("频", "稳", "数", "断", "本"):
                v = (rec.get(k) or "").strip()
                try:
                    n = int(v)
                except ValueError:
                    problems.append("%s 维度「%s」不是 1–5 的整数：%r" % (cid, k, v))
                    bad = True
                    continue
                if not 1 <= n <= 5:
                    problems.append("%s 维度「%s」越界：%d" % (cid, k, n))
                    bad = True
                dims.append(n)
            total = sum(dims) if len(dims) == 5 else 0
            veto = (rec.get("否决") or "").strip()
            if veto and veto not in ("-", "无"):
                pool = "手工"
                reason = veto
            elif total >= AUTO_MIN:
                pool = "可自动化"
                reason = ""
            elif total > COND_MAX:
                pool = "可自动化"
                reason = ""
            elif total >= 12:
                pool = "有条件自动化"
                reason = (rec.get("前置") or "").strip()
            else:
                pool = "手工"
                reason = "总分 %d < 12" % total
            rows.append({
                "id": cid, "dims": dims, "total": total, "pool": pool,
                "reason": reason, "type": (rec.get("类型") or "").strip(),
                "batch": (rec.get("批次") or "").strip(),
            })
    return rows, problems


def summarize(rows):
    c = {"可自动化": 0, "有条件自动化": 0, "手工": 0}
    types = {}
    for r in rows:
        c[r["pool"]] = c.get(r["pool"], 0) + 1
        if r["pool"] == "可自动化":
            t = r["type"] or "未标注"
            types[t] = types.get(t, 0) + 1
    return c, types


def build_summary_table(rows):
    c, types = summarize(rows)
    total = len(rows)
    out = ["| 项 | 数量 | 说明 |", "| :--- | :--- | :--- |",
           "| 输入用例总数 | %d | |" % total,
           "| 可自动化 | %d | 其中纯 UI %d / 混合 %d / 兼容矩阵 %d |"
           % (c["可自动化"], types.get("纯 UI", 0), types.get("混合", 0),
              types.get("兼容矩阵", 0)),
           "| 有条件自动化 | %d | 卡点见清单「改造前置」列 |" % c["有条件自动化"],
           "| 手工 | %d | 一票否决占比 |" % c["手工"]]
    return "\n".join(out)


def write_csv_template(path, cases):
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["编号", "频", "稳", "数", "断", "本", "否决", "类型", "批次", "前置"])
        for cid in cases:
            w.writerow([cid, "", "", "", "", "", "", "", "", ""])


def extract_case_ids(text):
    return re.findall(r"\bTC-[A-Z]+-\d{3}\b", text)


def main() -> int:
    ap = argparse.ArgumentParser(description="五维打分与分池核算")
    ap.add_argument("src", help="上游用例表(03)或本步产物(04)")
    ap.add_argument("--csv", dest="csv_path", default=None, help="打分 CSV")
    ap.add_argument("--out", default=None, help="写出汇总表草稿")
    ap.add_argument("--template", default=None, help="生成打分 CSV 模板")
    ap.add_argument("--verify", action="store_true", help="核对 04 文件里的汇总数字")
    args = ap.parse_args()

    text = read_text(args.src)

    if args.template:
        ids = sorted(set(extract_case_ids(text)))
        write_csv_template(args.template, ids)
        print("已生成打分模板 %s（%d 条用例）" % (args.template, len(ids)))
        return 0

    if args.csv_path:
        rows, problems = parse_score_csv(args.csv_path)
        for p in problems:
            print("  ⚠ %s" % p)
        table = build_summary_table(rows)
        print(table)
        if args.out:
            io.open(args.out, "w", encoding="utf-8").write(table + "\n")
        return 1 if problems else 0

    # --verify：核对 04 文件的三张表与汇总数字是否自洽
    problems = []
    lines = text.split("\n")
    tables = find_tables(lines)

    auto_rows, cond_rows, manual_rows, summary = [], [], [], None
    for header, rows in tables:
        if any("五维得分" in h for h in header) and any("判定依据" in h for h in header):
            if not auto_rows:
                auto_rows = (header, rows)
            else:
                cond_rows = (header, rows)
        elif any("不自动化原因" in h for h in header):
            manual_rows = (header, rows)
        elif any(any("输入用例总数" in c for c in r) for r in rows):
            summary = (header, rows)

    if not auto_rows:
        print("没找到「可自动化用例清单」表")
        return 1

    def total_of(h, r):
        try:
            return int(cell(r, h, "总分"))
        except ValueError:
            problems.append("总分不是整数：%s（%s）" % (cell(r, h, "总分"), cell(r, h, "用例编号")))
            return None

    bad_auto = []
    for r in auto_rows[1]:
        t = total_of(auto_rows[0], r)
        if t is not None and t < AUTO_MIN:
            bad_auto.append("%s 总分 %d < %d 却放在可自动化清单"
                            % (cell(r, auto_rows[0], "用例编号"), t, AUTO_MIN))
    bad_cond = []
    for r in (cond_rows[1] if cond_rows else []):
        t = total_of(cond_rows[0], r)
        if t is not None and not (12 <= t <= COND_MAX):
            bad_cond.append("%s 总分 %d 不在有条件区间 12–%d"
                            % (cell(r, cond_rows[0], "用例编号"), t, COND_MAX))
    problems.extend(bad_auto + bad_cond)

    n_auto = len(auto_rows[1])
    n_cond = len(cond_rows[1]) if cond_rows else 0
    n_manual = len(manual_rows[1]) if manual_rows else 0
    print("清单实数：可自动化 %d / 有条件 %d / 手工 %d（合计 %d）"
          % (n_auto, n_cond, n_manual, n_auto + n_cond + n_manual))

    if summary:
        sh, srows = summary
        want = {
            "输入用例总数": n_auto + n_cond + n_manual,
            "可自动化": n_auto,
            "有条件自动化": n_cond,
            "手工": n_manual,
        }
        for key, expect in want.items():
            got = ""
            for r in srows:
                if r and r[0].strip() == key:
                    got = r[1] if len(r) > 1 else ""
                    break
            m = re.search(r"\d+", got)
            got_n = int(m.group(0)) if m else None
            if got_n != expect:
                problems.append("汇总表「%s」写 %s，实际应为 %d" % (key, got or "（空）", expect))
            else:
                print("  ✓ %s = %d" % (key, expect))
    else:
        problems.append("没找到「三、汇总」表")

    if problems:
        print("问题 %d 条：" % len(problems))
        for p in problems:
            print("  ⚠ %s" % p)
        return 1
    print("✓ 三张表与汇总数字自洽，分池区间无越界")
    return 0


if __name__ == "__main__":
    sys.exit(main())
