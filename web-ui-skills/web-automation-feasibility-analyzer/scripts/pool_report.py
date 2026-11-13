#!/usr/bin/env python3
"""把逐条评估结果渲染成三分池 Markdown：可自动化 / 有条件自动化 / 手工测试。

输入是评估表（JSON 或 xlsx/csv），每条含五维分数与卡点；判定由脚本按固定阈值推导，
也可在输入里显式给 verdict 覆盖。

用法：
    python pool_report.py --input assessment.json --out pool.md
    python pool_report.py --input assessment.xlsx --title "订单模块分池" --out pool.md
    python pool_report.py --input assessment.json --automation-cost-ratio 0.15

退出码：0 成功；2 输入错误。
"""
import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

SCORE_KEYS = ("freq", "stab", "data", "assert", "cost")
SCORE_LABELS = {"freq": "执行频次", "stab": "稳定性", "data": "数据可构造",
                "assert": "断言可判定", "cost": "单次执行成本"}

# 评估表列名别名（表格输入用）
COLUMN_ALIASES = {
    "id": ["用例编号", "编号", "id", "caseid"],
    "title": ["用例标题", "标题", "title"],
    "module": ["所属模块", "模块", "module"],
    "pri": ["优先级", "pri", "priority"],
    "type": ["类型", "type", "自动化类型"],
    "freq": ["频", "执行频次", "freq", "frequency"],
    "stab": ["稳", "稳定性", "stab", "stability"],
    "data": ["数", "数据可构造", "data"],
    "assert": ["断", "断言可判定", "assert", "assertable"],
    "cost": ["本", "单次执行成本", "cost"],
    "veto": ["一票否决", "否决项", "veto"],
    "blocker": ["卡点", "阻塞", "blocker"],
    "actions": ["改造前置", "前置动作", "actions"],
    "batch": ["批次", "batch"],
    "minutes_manual": ["单次手工分钟", "手工时长", "minutes_manual"],
    "yearly_runs": ["年回归次数", "yearly_runs", "runs"],
    "note": ["备注", "note"],
}


def read_assessment(path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        cases = doc.get("cases") if isinstance(doc, dict) else doc
        if not isinstance(cases, list):
            raise SystemExit("[fatal] JSON 内没有 cases 数组")
        meta = doc if isinstance(doc, dict) else {}
        return cases, meta
    rows, headers = [], None
    if suffix in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise SystemExit(f"[fatal] 读取 xlsx 需要 openpyxl：{exc}")
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        data = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        if not data:
            raise SystemExit("[fatal] 表格为空")
        headers, rows = data[0], data[1:]
    elif suffix == ".csv":
        raw = path.read_text(encoding="utf-8-sig")
        parsed = list(csv.reader(raw.splitlines()))
        if not parsed:
            raise SystemExit("[fatal] CSV 为空")
        headers, rows = parsed[0], parsed[1:]
    else:
        raise SystemExit(f"[fatal] 不支持的扩展名：{suffix}")

    idx = {}
    for key, aliases in COLUMN_ALIASES.items():
        for i, header in enumerate(headers):
            if str(header or "").strip().lower() in [a.lower() for a in aliases]:
                idx[key] = i
                break
    if "id" not in idx:
        raise SystemExit("[fatal] 未识别到用例编号列，请用 --input JSON 或补列名")

    cases = []
    for row in rows:
        if not any(str(c or "").strip() for c in row):
            continue

        def get(key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row) or row[i] is None:
                return default
            return row[i]

        item = {"id": str(get("id", "")).strip(), "title": str(get("title", "") or "").strip(),
                "module": str(get("module", "") or "").strip(), "pri": str(get("pri", "") or "").strip(),
                "type": str(get("type", "") or "").strip(), "veto": str(get("veto", "") or "").strip(),
                "blocker": str(get("blocker", "") or "").strip(), "note": str(get("note", "") or "").strip()}
        item["scores"] = {k: int(get(k) or 0) for k in SCORE_KEYS}
        actions = str(get("actions", "") or "").strip()
        item["actions"] = [a for a in actions.replace("；", ";").split(";") if a.strip()]
        item["batch"] = get("batch")
        item["minutes_manual"] = get("minutes_manual")
        item["yearly_runs"] = get("yearly_runs")
        cases.append(item)
    return cases, {"source": str(path.resolve())}


def classify(case):
    scores = case.get("scores") or {}
    total = case.get("total")
    if total is None:
        total = sum(int(scores.get(k) or 0) for k in SCORE_KEYS)
    case["total"] = total
    if case.get("veto"):
        case["verdict"] = "manual"
    elif case.get("verdict") in ("auto", "condition", "manual"):
        pass
    elif total >= 18:
        case["verdict"] = "auto"
    elif total >= 12:
        case["verdict"] = "condition"
    else:
        case["verdict"] = "manual"
    if case["verdict"] == "manual" and not case.get("veto"):
        low = [SCORE_LABELS[k] for k in SCORE_KEYS if int(scores.get(k) or 0) <= 2]
        case["veto"] = "低分维度：" + "、".join(low) if low else "总分低于 12"
    return case


def fmt_scores(case):
    scores = case.get("scores") or {}
    return "/".join(str(int(scores.get(k) or 0)) for k in SCORE_KEYS)


def fmt_actions(case):
    actions = case.get("actions") or []
    return "；".join(actions) if actions else "无（可直接编写脚本）"


def fmt_scripts(case):
    scripts = case.get("scripts") or []
    if not scripts:
        return "—"
    out = []
    for s in scripts:
        if isinstance(s, str):
            out.append(s)
        else:
            name = s.get("name", "")
            sub = s.get("sub", "")
            params = s.get("params", "")
            text = f"{name} {sub}".strip()
            out.append(f"{text}（{params}）" if params else text)
    return "；".join(out)


def cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render(cases, meta, args):
    today = args.date or dt.date.today().isoformat()
    pool = {"auto": [], "condition": [], "manual": []}
    for case in cases:
        pool[classify(case)["verdict"]].append(case)

    auto_types = {}
    for c in pool["auto"]:
        key = c.get("type") or "未标注"
        auto_types[key] = auto_types.get(key, 0) + 1

    blocker_count = {}
    for c in pool["condition"]:
        key = c.get("blocker") or "未标注卡点"
        blocker_count[key] = blocker_count.get(key, 0) + 1

    hours_saved, assumed, skipped = 0.0, 0, 0
    for c in pool["auto"] + pool["condition"]:
        mins, runs = c.get("minutes_manual"), c.get("yearly_runs")
        if mins in (None, "", 0) or runs in (None, "", 0):
            skipped += 1
            continue
        hours_saved += float(mins) * float(runs) * (1 - args.automation_cost_ratio) / 60.0
        assumed += 1

    lines = []
    lines.append(f"# {args.title}")
    source = args.source or meta.get("source") or "[未提供]"
    lines.append(f"> 来源：{source}｜生成：{today}")
    lines.append("")

    lines.append("### 一、可自动化用例清单")
    lines.append("")
    lines.append("| 用例编号 | 用例标题 | 类型 | 五维得分（频/稳/数/断/本） | 总分 | 判定依据 | 改造前置 | 批次 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for c in pool["auto"]:
        basis = c.get("note") or "分数达标，断言可判定"
        lines.append(f"| {cell(c['id'])} | {cell(c.get('title'))} | {cell(c.get('type') or '未标注')} | "
                     f"{fmt_scores(c)} | {c['total']} | {cell(basis)} | {cell(fmt_actions(c))} | {cell(c.get('batch'))} |")
    if not pool["auto"]:
        lines.append("| — | 本轮无 | — | — | — | — | — | — |")
    lines.append("")

    lines.append("### 二、有条件自动化用例清单")
    lines.append("")
    lines.append("| 用例编号 | 用例标题 | 类型 | 五维得分（频/稳/数/断/本） | 总分 | 卡点 | 改造前置 | 可用脚本 | 批次 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for c in pool["condition"]:
        lines.append(f"| {cell(c['id'])} | {cell(c.get('title'))} | {cell(c.get('type') or '未标注')} | "
                     f"{fmt_scores(c)} | {c['total']} | {cell(c.get('blocker') or '未标注')} | "
                     f"{cell(fmt_actions(c))} | {cell(fmt_scripts(c))} | {cell(c.get('batch'))} |")
    if not pool["condition"]:
        lines.append("| — | 本轮无 | — | — | — | — | — | — | — |")
    lines.append("")

    lines.append("### 三、手工测试池")
    lines.append("")
    lines.append("| 用例编号 | 用例标题 | 不自动化原因 | 对应判据 | 复核周期 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for c in pool["manual"]:
        reason = c.get("blocker") or c.get("note") or "见判据列"
        judge = c.get("veto") or "总分低于 12"
        lines.append(f"| {cell(c['id'])} | {cell(c.get('title'))} | {cell(reason)} | {cell(judge)} | {cell(args.review_cycle)} |")
    if not pool["manual"]:
        lines.append("| — | 本轮无 | — | — | — |")
    lines.append("")

    lines.append("### 四、汇总")
    lines.append("")
    lines.append("| 项 | 数量 | 说明 |")
    lines.append("| :--- | :--- | :--- |")
    lines.append(f"| 输入用例总数 | {len(cases)} | 按用例编号去重后统计 |")
    type_text = "、".join(f"{k} {v}" for k, v in auto_types.items()) or "—"
    lines.append(f"| 可自动化 | {len(pool['auto'])} | 其中 {type_text} |")
    blocker_text = "；".join(f"{k} {v}" for k, v in blocker_count.items()) or "—"
    lines.append(f"| 有条件自动化 | {len(pool['condition'])} | 主要卡点：{blocker_text} |")
    veto_share = f"{len(pool['manual'])}/{len(cases)}" if cases else "0/0"
    lines.append(f"| 手工 | {len(pool['manual'])} | 一票否决占比 {veto_share} |")
    formula = (f"Σ 单次手工分钟 × 年回归次数 × (1-{args.automation_cost_ratio}) ÷ 60；"
               f"纳入 {assumed} 条，缺时长或次数未纳入 {skipped} 条")
    lines.append(f"| 预估年节省工时 | {hours_saved:.1f} 小时 | {formula} |")
    lines.append("")

    lines.append("### 五、假设与待确认")
    lines.append("")
    assumptions = meta.get("assumptions") or []
    if assumptions:
        for item in assumptions:
            lines.append(f"- {item}")
    if skipped:
        lines.append(f"- 有 {skipped} 条缺「单次手工分钟」或「年回归次数」，未计入节省工时；补齐后可重算。")
    if not assumptions and not skipped:
        lines.append("- 无。五维分值与卡点均来自输入评估表，未新增假设。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="三分池 Markdown 报告生成")
    ap.add_argument("--input", required=True, help="评估表（json/xlsx/csv）")
    ap.add_argument("--out", help="输出 Markdown 路径，省略则打印")
    ap.add_argument("--title", default="Web 自动化适用性分池", help="产物标题")
    ap.add_argument("--source", help="覆盖来源行内容")
    ap.add_argument("--date", help="生成日期，默认今天")
    ap.add_argument("--review-cycle", default="季度", help="手工池复核周期")
    ap.add_argument("--automation-cost-ratio", type=float, default=0.1,
                    help="自动化单次执行成本相对手工的比值，默认 0.1")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"[fatal] 找不到文件：{path}")
    cases, meta = read_assessment(path)
    if not cases:
        raise SystemExit("[fatal] 评估表内没有数据行")
    text = render(cases, meta, args)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"[ok] {len(cases)} 条 -> {args.out}")
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
