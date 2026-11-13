#!/usr/bin/env python3
"""把任意来源的用例表归一化成标准 JSON，供 signal_scan / pool_report 消费。

换项目只改字段映射（--map 或 --map-file），不改本脚本。

用法：
    python case_table_reader.py --input cases.xlsx --out cases.json
    python case_table_reader.py --input cases.md --map-file my_map.json --out cases.json
    python case_table_reader.py --input cases.csv --columns      # 只列出识别到的列

退出码：0 成功；2 输入或环境错误。
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

# 字段别名表：同一语义的不同列名，命中即映射。换项目时用 --map 追加即可。
FIELD_ALIASES = {
    "id": ["用例编号", "编号", "用例id", "用例ID", "caseid", "case id", "id", "no.", "序号"],
    "title": ["用例标题", "标题", "用例名称", "名称", "title", "case title", "summary"],
    "module": ["所属模块", "模块", "功能模块", "module", "component", "所属功能"],
    "pri": ["优先级", "级别", "priority", "pri", "level"],
    "pre": ["前置条件", "前置", "预置条件", "precondition", "preconditions", "pre-condition"],
    "steps": ["步骤", "操作步骤", "测试步骤", "执行步骤", "steps", "step"],
    "expect": ["预期结果", "预期", "期望结果", "预期输出", "expected", "expect", "expected result"],
    "type": ["类型", "用例类型", "type"],
}

KEY_FIELDS = ["id", "title", "module", "pri", "pre", "steps", "expect", "type"]


def norm_key(value):
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-()（）:：]", "", text)


def resolve_mapping(headers, extra_map=None):
    """把表头逐列映射到标准字段，返回 {标准字段: 列下标}，同列不重复认领。"""
    mapping, used = {}, set()
    aliases = {k: [norm_key(a) for a in v] for k, v in FIELD_ALIASES.items()}
    if extra_map:
        for key, names in extra_map.items():
            aliases.setdefault(key, [])
            aliases[key] = [norm_key(n) for n in ([names] if isinstance(names, str) else names)]
    for key in KEY_FIELDS:
        for idx, header in enumerate(headers):
            if idx in used or not header:
                continue
            if norm_key(header) in aliases.get(key, []):
                mapping[key] = idx
                used.add(idx)
                break
    return mapping


def rows_to_cases(headers, rows, extra_map=None):
    mapping = resolve_mapping(headers, extra_map)
    warnings = []
    missing = [k for k in ("id", "title", "steps", "expect") if k not in mapping]
    if missing:
        warnings.append("缺少关键列：" + "、".join(missing))
    cases = []
    for offset, row in enumerate(rows, start=1):
        if not any(str(cell).strip() for cell in row):
            continue

        def take(key, default=""):
            idx = mapping.get(key)
            if idx is None or idx >= len(row):
                return default
            return str(row[idx] if row[idx] is not None else "").strip()

        cases.append({
            "id": take("id") or f"ROW-{offset:03d}",
            "title": take("title"),
            "module": take("module"),
            "pri": take("pri"),
            "pre": take("pre"),
            "steps": take("steps").replace("\r\n", "\n"),
            "expect": take("expect").replace("\r\n", "\n"),
            "type": take("type"),
        })
    detected = {k: headers[v] for k, v in mapping.items()}
    return cases, detected, warnings


def read_xlsx(path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise SystemExit(f"[fatal] 读取 xlsx 需要 openpyxl：{exc}")
    wb = load_workbook(path, read_only=True, data_only=True)
    best = None
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        headers = [str(c).strip() if c is not None else "" for c in rows[0]]
        hit = len(resolve_mapping(headers))
        if best is None or hit > best[0]:
            best = (hit, headers, rows[1:], ws.title)
    wb.close()
    if best is None:
        raise SystemExit("[fatal] 工作簿内没有可用数据")
    return best[1], best[2], {"sheet": best[3]}


def read_csv(path):
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if raw is None:
        raise SystemExit("[fatal] CSV 编码无法识别，请另存为 UTF-8")
    rows = list(csv.reader(raw.splitlines()))
    if not rows:
        raise SystemExit("[fatal] CSV 为空")
    return rows[0], rows[1:], {"encoding_detected": True}


def read_md(path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks, current = [], []
    for line in lines:
        if line.strip().startswith("|") and line.strip().endswith("|"):
            current.append(line)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    best = None
    for block in blocks:
        cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in block]
        body = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
        if len(body) < 2:
            continue
        headers = body[0]
        hit = len(resolve_mapping(headers))
        if best is None or hit > best[0]:
            best = (hit, headers, body[1:])
    if best is None:
        raise SystemExit("[fatal] Markdown 内没有可识别的表格")
    return best[1], best[2], {"tables_found": len(blocks)}


def read_json(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    items = doc.get("cases") if isinstance(doc, dict) else doc
    if not isinstance(items, list) or not items:
        raise SystemExit("[fatal] JSON 内没有 cases 数组")
    headers, rows = list(items[0].keys()), []
    for item in items:
        rows.append([item.get(h) for h in headers])
    return headers, rows, {"json_passthrough": True}


def main():
    ap = argparse.ArgumentParser(description="用例表归一化")
    ap.add_argument("--input", required=True, help="用例表路径（xlsx/xls/csv/md/json）")
    ap.add_argument("--out", help="输出 JSON 路径，省略则打印到 stdout")
    ap.add_argument("--map", help='追加字段映射 JSON，如 {"id":"测试编号","expect":"检查点"}')
    ap.add_argument("--map-file", help="字段映射 JSON 文件")
    ap.add_argument("--columns", action="store_true", help="只输出识别到的列映射")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"[fatal] 找不到文件：{path}")
    extra = {}
    if args.map_file:
        extra.update(json.loads(Path(args.map_file).read_text(encoding="utf-8")))
    if args.map:
        extra.update(json.loads(args.map))

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        headers, rows, meta = read_xlsx(path)
    elif suffix == ".xls":
        raise SystemExit("[fatal] .xls 需先另存为 .xlsx（本脚本不引入 xlrd）")
    elif suffix == ".csv":
        headers, rows, meta = read_csv(path)
    elif suffix in (".md", ".markdown"):
        headers, rows, meta = read_md(path)
    elif suffix == ".json":
        headers, rows, meta = read_json(path)
    else:
        raise SystemExit(f"[fatal] 不支持的扩展名：{suffix}")

    cases, detected, warnings = rows_to_cases(headers, rows, extra)
    if args.columns:
        print(json.dumps({"headers": headers, "detected": detected}, ensure_ascii=False, indent=2))
        return
    payload = {
        "source": str(path.resolve()),
        "meta": meta,
        "detected_columns": detected,
        "warnings": warnings,
        "cases": cases,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[ok] {len(cases)} 条用例 -> {args.out}")
        if warnings:
            print("[warn] " + "；".join(warnings))
    else:
        print(text)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        sys.exit(2)
