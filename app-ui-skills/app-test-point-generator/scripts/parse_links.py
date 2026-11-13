# -*- coding: utf-8 -*-
"""
把上游「核心链路清单」解析成结构化数据，供测试点生成与覆盖自查使用。

上游 01 文件是 Markdown，链路有两种排法，本脚本都认：
  A. 以 `### CL-xx 名称` 分节（App 版），节内是 `- 链路ID：` / `- 关键节点：` 等键值行；
  B. 平铺成一串 `- 链路ID：CL-xx` 键值组（Web 版），每组到下一个 `- 链路ID：` 为止。
把两种排法的链路抽成 JSON，避免后续手工誊抄时漏字段、抄错编号。

用法：
    python parse_links.py ../../test-design/app/01-核心链路清单.md
    python parse_links.py ../../test-design/app/01-核心链路清单.md --json links.json
    python parse_links.py ... --nodes-only        # 只打印节点串（人工核对用）

输出字段：
    id / name / type / trigger / roles / nodes / branch / source
    nodes_list：把「①… → ②…」拆成节点数组
"""
from __future__ import annotations


import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, iter_headings  # noqa: E402

# 节点串用圈号标注（①②③…）；箭头只是分隔符，正文里也可能有箭头（分支说明），
# 所以按圈号切分才准确。
NODE_SPLIT = re.compile(r"\s*(?=[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])")
NODE_FALLBACK = re.compile(r"\s*(?:→|->|➜|＞>)\s*")
FIELD_RE = re.compile(r"^-\s*([^：:]{1,12})\s*[：:]\s*(.*)$")
# 节点另起一行的排法：`- ① …` / `→ ② …` —— 属于上一条字段的续行
CONT_RE = re.compile(r"^\s*(?:-\s*)?(?:[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|→|➜)\s*")


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()
def parse(text: str) -> list[dict]:
    """解析两种排法的链路清单，返回链路数组。"""
    lines = text.split("\n")

    # 排法 A：`### CL-xx 名称` 分节
    sections = []
    for idx, line in enumerate(lines):
        m = re.match(r"^#{2,4}\s+(CL-\d+)\s*(.*)$", line)
        if m:
            sections.append((m.group(1), m.group(2).strip(), idx))

    if sections:
        out = []
        for i, (cid, name, start) in enumerate(sections):
            end = sections[i + 1][2] if i + 1 < len(sections) else len(lines)
            out.append(_build(cid, name, lines[start + 1:end]))
        return out

    # 排法 B：平铺的 `- 链路ID：CL-xx` 键值组
    groups = []
    cur = None
    for line in lines:
        fm = FIELD_RE.match(line.strip())
        if fm and fm.group(1).strip() == "链路ID":
            cur = []
            groups.append(cur)
        if cur is not None:
            cur.append(line)
    out = []
    for g in groups:
        fields = _fields(g)
        cid = fields.get("链路ID", "").strip()
        if not cid:
            continue
        out.append(_build(cid, "", g, fields))
    return out


def _fields(body):
    fields = {}
    last_key = None
    for ln in body:
        s = ln.strip()
        fm = FIELD_RE.match(s)
        if fm and not CONT_RE.match(s):
            key = fm.group(1).strip()
            if key not in fields:
                fields[key] = fm.group(2).strip()
            last_key = key
            continue
        # 节点续行：`- ① …` / `- → ② …`，并到上一条字段的取值后面
        if CONT_RE.match(s) and last_key:
            piece = s.lstrip("- ").strip()
            cur = fields.get(last_key, "")
            # 续行以圈号开头、上一段没以箭头结尾时，补一个箭头（原文漏写分隔符）
            if piece and piece[0] in CIRCLED and cur and not cur.rstrip().endswith(("→", "➜")):
                piece = "→ " + piece
            fields[last_key] = (cur + " " + piece).strip()
    return fields


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _split_nodes(raw):
    """按圈号切节点，但只在「箭头后」或「串首」的圈号处切。

    节点描述里也可能出现圈号（如「items ← ②，返回 orderId」），
    那里不是节点起点，不能切。
    """
    if not raw:
        return []
    if "→" not in raw and "➜" not in raw:
        return [n.strip() for n in NODE_SPLIT.split(raw) if n.strip()]
    starts = []
    for i, ch in enumerate(raw):
        if ch not in CIRCLED:
            continue
        if i == 0 or raw[:i].rstrip().endswith(("→", "➜")):
            starts.append(i)
    if not starts:
        return [raw.strip()]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(raw)
        out.append(raw[s:e].strip())
    return out


def _build(cid, name, body, fields=None):
    if fields is None:
        fields = _fields(body)
    node_raw = fields.get("关键节点", "") or fields.get("节点", "")
    nodes = _split_nodes(node_raw)
    if len(nodes) <= 1:
        nodes = [n.strip() for n in NODE_FALLBACK.split(node_raw) if n.strip()]
    # 去掉节点前后残留的箭头分隔符
    nodes = [re.sub(r"^[→➜>\-]+\s*", "", re.sub(r"\s*[→➜>]+$", "", n)).strip()
             for n in nodes]
    nodes = [n for n in nodes if n]
    return {
        "id": cid,
        "name": name or fields.get("链路名称", ""),
        "type": fields.get("链路类型", ""),
        "trigger": fields.get("触发条件", ""),
        "roles": fields.get("参与角色", ""),
        "auth": fields.get("鉴权方式", ""),
        "data_dep": fields.get("数据依赖", ""),
        "idempotency": fields.get("幂等与状态", fields.get("幂等与状态机", "")),
        "nodes": node_raw,
        "nodes_list": nodes,
        "branch": fields.get("主/分支", ""),
        "source": fields.get("来源依据", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="解析上游核心链路清单")
    ap.add_argument("src")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--nodes-only", action="store_true")
    args = ap.parse_args()

    text = read_text(args.src)
    links = parse(text)

    if args.nodes_only:
        for lk in links:
            print("%s %s（%s）" % (lk["id"], lk["name"], lk["type"]))
            for i, nd in enumerate(lk["nodes_list"], 1):
                print("   %d. %s" % (i, nd))
        return 0

    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            f.write(json.dumps(links, ensure_ascii=False, indent=2))
        print("已写出 %s" % args.json_out)

    print("链路 %d 条：" % len(links))
    for lk in links:
        missing = [k for k, v in (("节点", lk["nodes_list"]),
                                  ("来源依据", lk["source"])) if not v]
        flag = "  ⚠ 缺字段：%s" % ",".join(missing) if missing else ""
        print("  %-7s %-28s 节点 %2d  %s%s"
              % (lk["id"], lk["name"][:28], len(lk["nodes_list"]), lk["type"], flag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
