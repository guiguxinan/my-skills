# -*- coding: utf-8 -*-
"""
测试点清单自检（test-point-generator 第 2 步专用）。

本 skill 的硬规则里有几条只能靠脚本查，人工容易漏：
  - 每个 TP 必须挂到链路 ID 与节点，挂不上就删或补节点；
  - 每个 TP 必须带六类标签之一（正向/异常/边界/状态/交互/兼容）；
  - 每个链路都要有六类覆盖，没覆盖的写"缺口"，不能糊过去；
  - 覆盖自查表要按链路逐行列出。

本脚本做两件事：
  1) 解析 02 文件，列出每条 TP 的 链路 / 类别 / 是否挂节点；
  2) 按链路生成「覆盖自查」表的骨架（缺口列留空供人工填）。

用法：
    python coverage_check.py ../../test-design/app/02-测试点清单.md
    python coverage_check.py ... --table          # 直接打印覆盖自查表
    python coverage_check.py ... --json out.json
"""
from __future__ import annotations


import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text  # noqa: E402

# 六类标签；「兼容」在不同项目写作「权限与兼容」/「权限」，三种写法都认
CATEGORIES = ["正向", "异常", "边界", "状态", "交互", "权限与兼容"]
# 接口档位的六类（canonical 短名），别名把文档里的长写法归一
API_CATEGORIES = ["正向", "参数", "状态", "幂等并发", "鉴权", "契约"]
CATEGORY_ALIASES = {
    "兼容": "权限与兼容", "权限": "权限与兼容",
    "状态与规则": "状态", "状态规则": "状态",
    "幂等与并发": "幂等并发", "幂等": "幂等并发",
    "鉴权与权限": "鉴权", "权限与鉴权": "鉴权",
    "契约与容错": "契约", "契约容错": "契约",
}
TP_RE = re.compile(r"^\s*-\s*(TP-\d+)\s*(?:\[(\S+?)\])?\s*(.*)$")
LINK_RE = re.compile(r"^\s*-\s*链路[：:]\s*(.+)$")
MOD_RE = re.compile(r"^#{2,4}\s*模块[：:]\s*(.+)$")


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()
def parse(text: str):
    """返回 (modules, tps)；modules 是 [{name, links:[{text, tps:[...]}]}]。"""
    modules = []
    cur_mod = None
    cur_link = None
    tps = []

    for line in text.split("\n"):
        mm = MOD_RE.match(line)
        if mm:
            cur_mod = {"name": mm.group(1).strip(), "links": []}
            modules.append(cur_mod)
            cur_link = None
            continue

        lm = LINK_RE.match(line)
        if lm:
            cur_link = {"text": lm.group(1).strip(), "tps": []}
            if cur_mod:
                cur_mod["links"].append(cur_link)
            continue

        tm = TP_RE.match(line)
        if tm:
            tp_id, cat, desc = tm.group(1), tm.group(2) or "", tm.group(3).strip()
            cat = CATEGORY_ALIASES.get(cat, cat)
            link_ids = re.findall(r"CL-\d+", cur_link["text"]) if cur_link else []
            node_in_link = "节点" in (cur_link["text"] if cur_link else "")
            rec = {
                "id": tp_id, "category": cat, "desc": desc,
                "module": cur_mod["name"] if cur_mod else "",
                "link_ids": link_ids,
                "has_node": node_in_link,
                "link_text": cur_link["text"] if cur_link else "",
            }
            tps.append(rec)
            if cur_link:
                cur_link["tps"].append(rec)
    return modules, tps


def build_table(modules, categories=None) -> str:
    """按链路生成覆盖自查表骨架。"""
    cats = categories or CATEGORIES
    header = ("| 链路 | 节点 | " + " | ".join(cats)
              + " | 缺口 |\n| :--- | :--- | "
              + " | ".join([":---"] * len(cats)) + " | :--- |")
    lines = [header]
    for mod in modules:
        for link in mod["links"]:
            ids = re.findall(r"CL-\d+", link["text"])
            link_id = ids[0] if ids else link["text"][:12]
            node_part = "有" if "节点" in link["text"] else "缺"
            counts = []
            for cat in cats:
                counts.append(str(sum(1 for t in link["tps"] if t["category"] == cat)))
            lines.append("| %s | %s | %s |  |"
                         % (link_id, node_part, " | ".join(counts)))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="测试点清单自检")
    ap.add_argument("src")
    ap.add_argument("--table", action="store_true", help="打印覆盖自查表")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--categories", default=None,
                    help="覆盖自查表的列名，逗号分隔（默认六类：正向/异常/边界/状态/交互/权限与兼容；"
                         "接口用例为：正向/参数/状态/幂等并发/鉴权/契约）")
    ap.add_argument("--preset", default=None, choices=["app", "api"],
                    help="档位预设：app = 正向/异常/边界/状态/交互/权限与兼容；"
                         "api = 正向/参数/状态/幂等并发/鉴权/契约（与 --categories 二选一）")
    args = ap.parse_args()

    cats = CATEGORIES
    if args.preset == "api":
        cats = list(API_CATEGORIES)
    if args.categories:
        cats = [c.strip() for c in args.categories.split(",") if c.strip()]

    text = read_text(args.src)
    modules, tps = parse(text)

    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"modules": modules, "tps": tps},
                               ensure_ascii=False, indent=2))
        print("已写出 %s" % args.json_out)

    problems = []
    warnings = []
    for t in tps:
        if not t["category"]:
            problems.append("%s 缺分类标签（%s）" % (t["id"], t["desc"][:20]))
        elif t["category"] not in cats:
            problems.append("%s 标签「%s」不在本 skill 的分类里（应为 %s）"
                            % (t["id"], t["category"], "/".join(cats)))
        if not t["link_ids"]:
            problems.append("%s 没挂到链路 ID" % t["id"])
        elif not t["has_node"]:
            # 挂多条链路（辅助链路，如"CL-01 / CL-02 升级安装"）时无节点串属正常
            if len(t["link_ids"]) >= 2:
                warnings.append("%s 挂 %s 但没写节点（多条链路可接受，建议补节点）"
                                % (t["id"], "/".join(t["link_ids"])))
            else:
                problems.append("%s 挂到了链路但没写节点" % t["id"])

    print("测试点 %d 条 / 模块 %d 个" % (len(tps), len(modules)))
    if warnings:
        print("提示 %d 条：" % len(warnings))
        for w in warnings:
            print("  · %s" % w)
    if problems:
        print("问题 %d 条：" % len(problems))
        for p in problems:
            print("  ⚠ %s" % p)
    else:
        print("✓ 每条 TP 都挂了链路 + 节点，且带六类标签")

    if args.table:
        print()
        print(build_table(modules, cats))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
