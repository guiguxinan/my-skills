# -*- coding: utf-8 -*-
"""
流水线收尾：统计各步产物并生成汇总表（app-pipeline 专用）。

流水线结束时必须输出一张汇总表：步 / 文件路径 / 节数 / 未决待确认项数 / 建议下一步。
这些数字靠人工数很容易错，本脚本直接从产物 Markdown 里数出来。

用法：
    python pipeline_summary.py ../../test-design/app
    python pipeline_summary.py ../../test-design/app --out summary.md
    python pipeline_summary.py ../../test-design/app --md          # 打印 Markdown 表

统计口径：
  节数        —— 二级标题数（每个 Skill 规定的固定节）
  待确认项数  —— 形如 A-01 / B-01 / C-01 / D-01 / TC-XXX-001 的编号去重计数
                （只算假设/待确认类编号，不算用例编号）
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, count_sections, iter_headings  # noqa: E402


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()

STEPS = [
    (1, "app-core-link-analyzer", "01-核心链路清单.md"),
    (2, "app-test-point-generator", "02-测试点清单.md"),
    (3, "app-test-case-writer", "03-测试用例.md"),
    (4, "app-automation-feasibility-analyzer", "04-自动化选型.md"),
    (5, "app-automation-script-generator", "05-自动化脚本.md"),
]

# 另一条流水线（web）用同名产物文件，但 Skill 名不同
STEPS_BY_PROFILE = {
    "app": STEPS,
    "web": [
        (1, "web-core-link-analyzer", "01-核心链路清单.md"),
        (2, "web-test-point-generator", "02-测试点清单.md"),
        (3, "web-test-case-writer", "03-测试用例.md"),
        (4, "web-automation-feasibility-analyzer", "04-自动化选型.md"),
        (5, "web-automation-script-generator", "05-自动化脚本.md"),
    ],
}

# 待确认编号：单字母前缀 + 两位数字（A-01 / B-01 / C-01 / D-01）
PENDING_RE = re.compile(r"\b([A-Z])-(\d{2})\b")

NEXT_STEP = {
    1: "补 CL-11 deeplink scheme、CL-04 车控指令授权口径",
    2: "补输入框边界规则与登录异常提示文案",
    3: "申请未绑定车辆测试账号，确认 VIN / 上限阈值",
    4: "确认造数接口、设备池规模、Toast 关键词表",
    5: "落代码并按模块跑通，补齐设备池与断网方案",
}

NEXT_STEP_BY_PROFILE = {
    "app": NEXT_STEP,
    "web": {
        1: "补未实测链路的路由与字段口径",
        2: "补输入框边界规则与提示文案",
        3: "申请测试账号与可造数环境",
        4: "确认造数/清理接口与断言阈值",
        5: "落代码并按模块跑通，补齐待补清单",
    },
}


def analyze(path: str) -> dict:
    text = read_text(path)
    cnt = count_sections(text)
    h2 = [title for lvl, title, _ in iter_headings(text) if lvl == 2]
    pending = sorted({m.group(0) for m in PENDING_RE.finditer(text)})
    return {
        "h2": h2,
        "h2_count": cnt["h2"],
        "h3_count": cnt["h3"],
        "total_headings": cnt["total"],
        "pending": pending,
        "pending_count": len(pending),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="流水线汇总表")
    ap.add_argument("outdir")
    ap.add_argument("--out", default=None, help="写出汇总表 Markdown")
    ap.add_argument("--md", action="store_true", help="只打印 Markdown 表")
    ap.add_argument("--profile", default="app", choices=sorted(STEPS_BY_PROFILE),
                    help="流水线档位：app / web")
    args = ap.parse_args()

    steps = STEPS_BY_PROFILE[args.profile]
    next_step = NEXT_STEP_BY_PROFILE[args.profile]

    rows = []
    for step, skill, fname in steps:
        path = os.path.join(args.outdir, fname)
        if not os.path.exists(path):
            rows.append((step, fname, "-", "-", "-", "产物缺失，尚未生成"))
            continue
        info = analyze(path)
        rows.append((step, fname, str(info["h2_count"]),
                     str(info["pending_count"]),
                     os.path.join(args.outdir, fname),
                     next_step.get(step, "")))

    header = "| 步 | 文件路径 | 节数 | 未决待确认项数 | 建议下一步 |\n| :--- | :--- | :--- | :--- | :--- |"
    lines = [header]
    for step, fname, secs, pend, path, nxt in rows:
        lines.append("| %d | %s | %s | %s | %s |"
                     % (step, path, secs, pend, nxt))
    table = "\n".join(lines)

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(table + "\n")
        print("已写出 %s" % args.out)
    else:
        print(table)

    if not args.md:
        print()
        for step, fname, _s, _p, path, _n in rows:
            if os.path.exists(os.path.join(args.outdir, fname)):
                info = analyze(os.path.join(args.outdir, fname))
                print("%d %s：二级节 %d / 三级节 %d / 待确认 %d 项（%s）"
                      % (step, fname, info["h2_count"], info["h3_count"],
                         info["pending_count"],
                         "、".join(info["pending"][:6])
                         + ("…" if len(info["pending"]) > 6 else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
