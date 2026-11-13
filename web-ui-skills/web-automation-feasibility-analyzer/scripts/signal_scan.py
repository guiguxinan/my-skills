#!/usr/bin/env python3
"""按规则扫描用例表，给出「一票否决候选 / 有条件自动化候选 / 未识别」与建议前置脚本。

只做候选提示，不替代五维打分与人工语义判断：命中的词可能只是恰好出现在文案里。

用法：
    python signal_scan.py --input cases.json --out signals.json
    python signal_scan.py --input cases.xlsx --rules my_rules.json --scope title,expect
    python signal_scan.py --input cases.json --quiet          # 只看汇总

退出码：0 完成（含命中为 0）；2 输入错误。
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# verdict: veto = 候选一票否决；condition = 候选「有条件自动化」，需补前置
DEFAULT_RULES = [
    {
        "key": "visual", "verdict": "veto", "judge": "依赖人工视觉/主观判断",
        "patterns": ["截图", "视觉", "观感", "像素", "配色", "版式", "变形", "清晰度", "裁剪",
                     "比例", "错位", "遮挡", "重叠", "美观", "渲染", "水印", "字体大小", "排版"],
        "action": "保留手工；若需回归兜底，补截图基线对比并设置像素阈值，阈值内不判缺陷",
        "script": {"name": "checks.py", "sub": "artifact", "note": "仅用于文件/表格类产物，视觉基线需另配截图比对工具"},
    },
    {
        "key": "subjective", "verdict": "veto", "judge": "结论依赖主观判定",
        "patterns": ["主观", "体验是否", "感受", "拟真", "语气", "是否合理", "是否自然", "是否友好"],
        "action": "保留手工；先把判定口径写成可勾选清单（如「允许/不允许」二值项），再考虑转断言",
        "script": None,
    },
    {
        "key": "oneoff", "verdict": "veto", "judge": "一次性活动或时效性验证",
        "patterns": ["一次性", "活动当天", "限时", "临时活动", "当日有效", "仅在当天"],
        "action": "不投入自动化；回归清单里标注豁免并记录豁免原因",
        "script": None,
    },
    {
        "key": "destructive", "verdict": "veto", "judge": "破坏性且不可回滚的数据操作",
        "patterns": ["删除", "清空", "销毁", "不可恢复", "覆盖生产", "重置", "物理删除", "drop"],
        "action": "先建独立测试库与临时数据，再用 fixture_guard 做执行前后快照；未隔离前保持手工",
        "script": {"name": "fixture_guard.py", "sub": "snapshot", "note": "snapshot 存前后态，plan-restore 生成回滚 SQL（不自动执行）"},
    },
    {
        "key": "thirdparty", "verdict": "veto", "judge": "强依赖真实第三方与人工审批",
        "patterns": ["短信", "支付回调", "物流", "人工审批", "外部审批", "真实第三方", "第三方平台", "实名核验"],
        "action": "先做 mock 或沙箱；无 mock 时保留手工，并把第三方侧结果记为外部依赖",
        "script": None,
    },
    {
        "key": "hardenv", "verdict": "veto", "judge": "极端难构造的异常",
        "patterns": ["并发", "同一秒", "极短时间窗", "会话过期", "断网", "弱网", "断电", "硬件故障", "超时后"],
        "action": "可构造时用 checks.py api 的并发/重复提交参数替代手工；不可构造则保留手工",
        "script": {"name": "checks.py", "sub": "api", "note": "--repeat N 连续提交，断言最终条数只加 1"},
    },
    {
        "key": "bypass_frontend", "verdict": "condition", "judge": "需绕过前端改包或控制台注入",
        "patterns": ["改包", "控制台", "绕过前端", "直接调用接口", "postman", "apifox", "curl", "抓包", "移除 maxlength"],
        "action": "改成接口用例：直接构造请求体断言服务端返回，不再点页面",
        "script": {"name": "checks.py", "sub": "api", "note": "--method/--url/--body-file/--expect-json"},
    },
    {
        "key": "db_assert", "verdict": "condition", "judge": "断言落在数据库，页面无可判定信号",
        "patterns": ["数据库", "库表", "数据表", "sql", "查询表", "落库", "表内", "唯一索引", "悬空引用", "is_deleted"],
        "action": "UI 只做触发，断言交给 SQL：checks.py db 直接比对行数/字段",
        "script": {"name": "checks.py", "sub": "db", "note": "--dsn 支持 sqlite 内置，mysql/postgres 装可选驱动"},
    },
    {
        "key": "file_artifact", "verdict": "condition", "judge": "断言落在导出/模板/明细文件内容",
        "patterns": ["导出", "下载", "文件", "xlsx", "csv", "模板", "明细", "逐格", "逐列", "表头", "合并单元格"],
        "action": "把文件当接口产物：checks.py artifact 按 cell/row/rowcount/merged 逐项断言",
        "script": {"name": "checks.py", "sub": "artifact", "note": "--rule-file 支持 cell/row/header/rowcount/merged 五类断言"},
    },
    {
        "key": "locator", "verdict": "condition", "judge": "缺稳定定位标识",
        "patterns": ["按坐标", "按文本定位", "无 id", "定位标识", "改版频繁", "位置固定"],
        "action": "先让开发补 data-testid 或用业务主键定位，再写脚本",
        "script": None,
    },
    {
        "key": "account", "verdict": "condition", "judge": "依赖多角色账号或特定权限",
        "patterns": ["多角色", "非管理员", "权限", "账号", "角色", "越权", "无权限"],
        "action": "申请固定测试账号并在用例前置里写死账号与角色，不要临时借号",
        "script": {"name": "checks.py", "sub": "api", "note": "同一接口用不同账号 token 批量比对响应码"},
    },
    {
        "key": "data_fixture", "verdict": "condition", "judge": "需专门造数与清理",
        "patterns": ["构造数据", "准备数据", "测试数据", "造数", "清数", "夹具", "临时数据"],
        "action": "先补造数/清数脚本，用一次性前缀命名避免撞既有数据",
        "script": {"name": "fixture_guard.py", "sub": "snapshot", "note": "配合 checks.py db 做造数后校验与清理后回归基线"},
    },
    {
        "key": "cross_page", "verdict": "condition", "judge": "需跨页面或跨模块比对",
        "patterns": ["跨页", "多个页面", "跳转后", "弹层", "抽屉", "回跳", "另一个页面", "多处一致"],
        "action": "拆成「页面 A 取数 → 页面 B 取数 → 字段对比」三步，断言只做等值比较",
        "script": {"name": "checks.py", "sub": "db", "note": "两处取值都落库时优先 SQL 比对，避免两套 UI 取数"},
    },
    {
        "key": "timing", "verdict": "condition", "judge": "需构造并发或时序窗口",
        "patterns": ["重复点击", "幂等", "连点", "快速点击", "并发提交"],
        "action": "用接口层并发提交替代手工连点，断言最终只落一条",
        "script": {"name": "checks.py", "sub": "api", "note": "--repeat 与 --expect-json 组合"},
    },
]

SCOPE_FIELDS = ("title", "pre", "steps", "expect")


def load_cases(path):
    if path.suffix.lower() == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        cases = doc.get("cases") if isinstance(doc, dict) else doc
        if not isinstance(cases, list):
            raise SystemExit("[fatal] JSON 内没有 cases 数组")
        return str(path.resolve()), cases
    # 其他格式复用 case_table_reader
    spec = importlib.util.spec_from_file_location("case_table_reader", SCRIPTS_DIR / "case_table_reader.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        headers, rows, meta = reader.read_xlsx(path)
    elif suffix == ".csv":
        headers, rows, meta = reader.read_csv(path)
    elif suffix in (".md", ".markdown"):
        headers, rows, meta = reader.read_md(path)
    else:
        raise SystemExit(f"[fatal] 不支持的扩展名：{suffix}")
    cases, detected, _ = rows_to_cases_wrapper(reader, headers, rows)
    return str(path.resolve()), cases


def rows_to_cases_wrapper(reader, headers, rows):
    return reader.rows_to_cases(headers, rows)


def merge_rules(extra_path):
    rules = [dict(r) for r in DEFAULT_RULES]
    if not extra_path:
        return rules
    extra = json.loads(Path(extra_path).read_text(encoding="utf-8"))
    by_key = {r["key"]: r for r in rules}
    for item in extra:
        if item.get("key") and item["key"] in by_key:
            by_key[item["key"]].update(item)
        else:
            rules.append(item)
    return rules


def scan_case(case, rules, scope):
    text = " ".join(str(case.get(f) or "") for f in scope).lower()
    signals = []
    for rule in rules:
        hits = [p for p in rule["patterns"] if p.lower() in text]
        if not hits:
            continue
        signals.append({
            "key": rule["key"],
            "verdict": rule.get("verdict", "condition"),
            "judge": rule.get("judge", ""),
            "matched": hits[:4],
            "action": rule.get("action", ""),
            "script": rule.get("script"),
            "confidence": "high" if len(hits) >= 2 else "low",
        })
    if any(s["verdict"] == "veto" for s in signals):
        hint = "veto"
    elif signals:
        hint = "condition"
    else:
        hint = "unknown"
    return {"id": case.get("id"), "title": case.get("title"), "pri": case.get("pri"),
            "pool_hint": hint, "signals": signals}


def main():
    ap = argparse.ArgumentParser(description="用例分池信号扫描")
    ap.add_argument("--input", required=True, help="cases.json 或用例表（xlsx/csv/md）")
    ap.add_argument("--out", help="输出 JSON 路径，省略则打印")
    ap.add_argument("--rules", help="追加/覆盖规则 JSON 数组")
    ap.add_argument("--scope", default=",".join(SCOPE_FIELDS), help="扫描字段，逗号分隔")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"[fatal] 找不到文件：{path}")
    source, cases = load_cases(path)
    rules = merge_rules(args.rules)
    scope = [s.strip() for s in args.scope.split(",") if s.strip() in SCOPE_FIELDS] or list(SCOPE_FIELDS)

    results = [scan_case(c, rules, scope) for c in cases]
    summary = {"total": len(results), "veto": 0, "condition": 0, "unknown": 0, "by_signal": {}}
    for item in results:
        summary[item["pool_hint"]] += 1
        for sig in item["signals"]:
            summary["by_signal"][sig["key"]] = summary["by_signal"].get(sig["key"], 0) + 1

    payload = {"source": source, "scope": scope, "summary": summary, "cases": results}
    if args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[ok] 扫描 {summary['total']} 条：否决候选 {summary['veto']}、条件候选 {summary['condition']}、未识别 {summary['unknown']} -> {args.out}")
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
