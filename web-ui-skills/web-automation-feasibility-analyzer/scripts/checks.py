#!/usr/bin/env python3
"""通用断言工具箱，覆盖「有条件自动化」最常见的四类卡点：

    artifact  导出/模板/失败明细等文件产物：逐格、整行、表头、行数、合并区断言
    api       绕过前端直接打接口：状态码、JSON 子集、重复/并发提交
    db        SQL 断言：行数、首行首列；可把查询结果存 JSON 供 fixture_guard 比对
    dom       页面字段齐全性/元素数量（需 playwright，缺失时明确报错）

所有断言只依赖标准库；sqlite 内置，mysql/postgres 需装可选驱动，playwright 需装可选包。
换项目不需要改脚本：连接串、URL、期望值全部走参数或 rule 文件。

    python checks.py artifact --file out.xlsx --rule-file rules.json
    python checks.py api --method POST --url http://x/api --body-file body.json --expect-status 200 --expect-json '{"code":500}'
    python checks.py db --dsn sqlite:///t.db --query "SELECT count(*) FROM s" --expect-value 3
    python checks.py dom --url http://x/list --selector "tbody tr" --expect-count 10

退出码：0 全部通过；1 有断言失败；2 输入或环境错误。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERDICT_OK, VERDICT_FAIL, ENV_ERROR = 0, 1, 2


def emit(tool, checks, errors=None):
    ok = all(c["ok"] for c in checks) and not errors
    print(json.dumps({"ok": ok, "tool": tool, "checks": checks, "errors": errors or []},
                     ensure_ascii=False, indent=2))
    return VERDICT_OK if ok else VERDICT_FAIL


def check(name, expected, actual):
    ok = expected == actual
    return {"name": name, "ok": ok, "expected": expected, "actual": actual}


# ---------------- artifact --------------------------

def read_grid(path, sheet=None):
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise SystemExit(f"[fatal] 读取 xlsx 需要 openpyxl：{exc}")
        wb = load_workbook(path, read_only=False, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]
        merged = [str(r) for r in ws.merged_cells.ranges]
        wb.close()
        return grid, merged
    if suffix == ".csv":
        import csv
        raw = path.read_text(encoding="utf-8-sig")
        grid = [row for row in csv.reader(raw.splitlines())]
        return grid, []
    raise SystemExit(f"[fatal] artifact 只支持 xlsx/csv，收到 {suffix}")


def cellref(row, col):
    """把 (row, col) 1-based 转 A1。"""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def parse_a1(ref):
    letters, digits = "", ""
    for ch in ref:
        if ch.isalpha():
            letters += ch.upper()
        elif ch.isdigit():
            digits += ch
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return int(digits or 1), col


def norm_grid(grid):
    return [["" if v is None else str(v).strip() for v in row] for row in grid]


def run_artifact(args):
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"[fatal] 找不到文件：{path}")
    grid, merged = read_grid(path, args.sheet)
    values = norm_grid(grid)
    rules = json.loads(Path(args.rule_file).read_text(encoding="utf-8"))
    checks = []
    for idx, rule in enumerate(rules, start=1):
        kind = rule.get("type")
        name = rule.get("name") or f"R{idx}:{kind}"
        if kind == "cell":
            row, col = parse_a1(rule["cell"])
            actual = values[row - 1][col - 1] if row - 1 < len(values) and col - 1 < len(values[row - 1]) else ""
            checks.append(check(name, str(rule.get("equals", "")), actual))
        elif kind == "contains":
            row, col = parse_a1(rule["cell"])
            actual = values[row - 1][col - 1] if row - 1 < len(values) and col - 1 < len(values[row - 1]) else ""
            checks.append({"name": name, "ok": str(rule["value"]) in actual,
                           "expected": f"包含 {rule['value']}", "actual": actual})
        elif kind == "empty":
            row, col = parse_a1(rule["cell"])
            actual = values[row - 1][col - 1] if row - 1 < len(values) and col - 1 < len(values[row - 1]) else ""
            checks.append(check(name, "", actual))
        elif kind == "header":
            row = int(rule.get("row", 1))
            actual = [c for c in values[row - 1]] if row - 1 < len(values) else []
            expected = [str(c) for c in rule["equals"]]
            trimmed = actual[:len(expected)]
            checks.append(check(name, expected, trimmed))
        elif kind == "row":
            row = int(rule["row"])
            actual = [c for c in values[row - 1]] if row - 1 < len(values) else []
            expected = [str(c) for c in rule["equals"]]
            actual_cmp = [c for c in actual[:len(expected)]]
            checks.append(check(name, expected, actual_cmp))
        elif kind == "rowcount":
            non_empty = [r for r in values if any(c for c in r)]
            count = len(non_empty) - int(rule.get("exclude_head", 0))
            ok = True
            if "equals" in rule:
                ok = count == int(rule["equals"])
            if "min" in rule:
                ok = ok and count >= int(rule["min"])
            if "max" in rule:
                ok = ok and count <= int(rule["max"])
            checks.append({"name": name, "ok": ok,
                           "expected": {k: rule[k] for k in ("equals", "min", "max") if k in rule},
                           "actual": count})
        elif kind == "merged":
            rng = rule["range"].replace("$", "")
            checks.append({"name": name, "ok": rng in merged, "expected": f"合并区 {rng}", "actual": merged})
        else:
            raise SystemExit(f"[fatal] 未知 rule type：{kind}")
    return emit("artifact", checks)


# ---------------- api --------------------------

def subset_match(expected, actual, path="root"):
    """expected 的每个键都要在 actual 里且相等（嵌套递归），返回不符项列表。"""
    missing = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 期望对象，实际 {type(actual).__name__}"]
        for key, val in expected.items():
            if key not in actual:
                missing.append(f"{path}.{key}: 字段缺失")
            else:
                missing += subset_match(val, actual[key], f"{path}.{key}")
    elif expected != actual:
        missing.append(f"{path}: 期望 {expected!r}，实际 {actual!r}")
    return missing


def http_call(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)
    except urllib.error.URLError as exc:
        raise SystemExit(f"[fatal] 请求失败：{exc.reason}")


def run_api(args):
    headers = {"Content-Type": "application/json"}
    if args.headers_file:
        headers.update(json.loads(Path(args.headers_file).read_text(encoding="utf-8")))
    if args.cookie:
        headers["Cookie"] = args.cookie
    body = None
    if args.body_file:
        raw = Path(args.body_file).read_bytes()
        body = raw if args.body_raw else json.dumps(json.loads(raw.decode("utf-8")),
                                                   ensure_ascii=False).encode("utf-8")
    checks = []
    if args.repeat and args.repeat > 1:
        def once(_):
            return http_call(args.method, args.url, headers, body, args.timeout)
        with ThreadPoolExecutor(max_workers=args.repeat) as pool:
            results = list(pool.map(once, range(args.repeat)))
        statuses = [r[0] for r in results]
        checks.append({"name": f"并发 {args.repeat} 次的状态码分布", "ok": True,
                       "expected": "记录", "actual": statuses})
        if args.expect_status:
            checks.append(check("并发场景状态码", [int(args.expect_status)] * args.repeat, statuses))
        if args.expect_one_success:
            success = sum(1 for _, txt, _ in results if args.expect_one_success in txt)
            checks.append({"name": "成功提示只出现一次", "ok": success == 1,
                           "expected": 1, "actual": success})
        if args.expect_json:
            expected = json.loads(args.expect_json)
            bad = []
            for i, (status, text, _) in enumerate(results, start=1):
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    bad.append(f"#{i} 非 JSON 响应：{text[:80]}")
                    continue
                diff = subset_match(expected, payload, f"#{i}")
                bad += diff
            checks.append({"name": "并发后 JSON 断言", "ok": not bad,
                           "expected": expected, "actual": bad or "全部满足"})
        return emit("api", checks)

    status, text, _ = http_call(args.method, args.url, headers, body, args.timeout)
    checks.append({"name": "HTTP 状态码", "ok": (not args.expect_status) or status == int(args.expect_status),
                   "expected": args.expect_status or "不校验", "actual": status})
    if args.expect_json:
        expected = json.loads(args.expect_json)
        try:
            payload = json.loads(text)
            diff = subset_match(expected, payload)
            checks.append({"name": "响应 JSON 子集", "ok": not diff, "expected": expected,
                           "actual": diff or "全部满足"})
        except json.JSONDecodeError:
            checks.append({"name": "响应 JSON 可解析", "ok": False, "expected": "JSON",
                           "actual": text[:120]})
    if args.expect_text:
        checks.append({"name": "响应体包含文案", "ok": args.expect_text in text,
                       "expected": f"包含 {args.expect_text}", "actual": text[:120]})
    if args.out:
        Path(args.out).write_text(json.dumps({"status": status, "body": text}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return emit("api", checks)


# ---------------- db --------------------------

def open_db(dsn):
    if dsn.startswith("sqlite:///"):
        import sqlite3
        conn = sqlite3.connect(dsn.replace("sqlite:///", "", 1))
        conn.row_factory = None
        return conn
    if dsn.startswith(("mysql://", "mariadb://")):
        try:
            import pymysql
        except ImportError as exc:
            raise SystemExit(f"[fatal] 连 mysql 需要 pymysql：python -m pip install pymysql（{exc}）")
        from urllib.parse import urlparse
        u = urlparse(dsn.replace("mariadb://", "mysql://", 1))
        return pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                              password=u.password or "", database=(u.path or "/").lstrip("/"),
                              charset="utf8mb4", autocommit=True)
    if dsn.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg2
        except ImportError as exc:
            raise SystemExit(f"[fatal] 连 postgres 需要 psycopg2：python -m pip install psycopg2-binary（{exc}）")
        import psycopg2
        return psycopg2.connect(dsn, connect_timeout=10)
    raise SystemExit("[fatal] dsn 支持 sqlite:/// / mysql:// / postgres://")


def run_db(args):
    conn = open_db(args.dsn)
    queries = json.loads(Path(args.query_file).read_text(encoding="utf-8")) if args.query_file else None
    items = queries or [{"sql": args.query,
                         "expect": getattr(args, "inline_expect", None) or {},
                         "name": "inline"}]
    checks = []
    try:
        cursor = conn.cursor()
        for idx, item in enumerate(items, start=1):
            cursor.execute(item["sql"])
            rows = cursor.fetchall()
            name = item.get("name") or f"Q{idx}"
            expect = item.get("expect") or {}
            if "rows" in expect:
                checks.append(check(f"{name} 行数", int(expect["rows"]), len(rows)))
            if "min_rows" in expect:
                checks.append({"name": f"{name} 至少 {expect['min_rows']} 行",
                               "ok": len(rows) >= int(expect["min_rows"]),
                               "expected": f">= {expect['min_rows']}", "actual": len(rows)})
            if "value" in expect:
                actual = rows[0][0] if rows and rows[0] else None
                checks.append(check(f"{name} 首行首列", str(expect["value"]), "" if actual is None else str(actual)))
            if "first_row" in expect:
                actual = [None if v is None else str(v) for v in (rows[0] if rows else ())]
                expected = [None if v is None else str(v) for v in expect["first_row"]]
                checks.append(check(f"{name} 首行", expected, actual[:len(expected)]))
            if args.out:
                item["_rows"] = [[None if v is None else str(v) for v in r] for r in rows]
    finally:
        conn.close()
    if args.out:
        Path(args.out).write_text(json.dumps({"dsn": args.dsn, "items": items},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    return emit("db", checks)


# ---------------- dom --------------------------

def run_dom(args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"ok": False, "tool": "dom", "checks": [],
                          "errors": ["未安装 playwright：python -m pip install playwright && playwright install"]},
                         ensure_ascii=False, indent=2))
        return ENV_ERROR
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        try:
            page = browser.new_page()
            try:
                page.goto(args.url, timeout=args.timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"ok": False, "tool": "dom", "checks": [],
                                  "errors": [f"页面访问失败：{exc}"]},
                                 ensure_ascii=False, indent=2))
                return ENV_ERROR
            if args.wait_ms:
                page.wait_for_timeout(args.wait_ms)
            nodes = page.query_selector_all(args.selector)
            texts = [(n.inner_text() or "").strip() for n in nodes]
            if args.expect_count is not None:
                checks.append(check("元素数量", int(args.expect_count), len(texts)))
            if args.expect_texts:
                expected = [t.strip() for t in args.expect_texts.split(",") if t.strip()]
                checks.append(check("元素文本序列", expected, texts[:len(expected)]))
            if args.out:
                Path(args.out).write_text(json.dumps({"url": args.url, "texts": texts},
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            browser.close()
    return emit("dom", checks)


def main():
    ap = argparse.ArgumentParser(description="通用断言工具箱")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("artifact", help="文件产物断言")
    a.add_argument("--file", required=True)
    a.add_argument("--rule-file", required=True, help="断言规则 JSON 数组")
    a.add_argument("--sheet", help="xlsx 子表名，默认第一个")

    b = sub.add_parser("api", help="接口断言")
    b.add_argument("--method", default="GET")
    b.add_argument("--url", required=True)
    b.add_argument("--headers-file")
    b.add_argument("--body-file")
    b.add_argument("--body-raw", action="store_true", help="body-file 原样发送，不做 JSON 重编码")
    b.add_argument("--cookie")
    b.add_argument("--expect-status", type=int)
    b.add_argument("--expect-json", help="响应 JSON 子集，如 '{\"code\":500}'")
    b.add_argument("--expect-text")
    b.add_argument("--repeat", type=int, default=1, help="并发发送次数，用于幂等/连点验证")
    b.add_argument("--expect-one-success", help="该文案在 N 份响应里只允许出现一次")
    b.add_argument("--timeout", type=int, default=15)
    b.add_argument("--out")

    c = sub.add_parser("db", help="SQL 断言")
    c.add_argument("--dsn", required=True, help="sqlite:///x.db | mysql://u:p@h:3306/db")
    c.add_argument("--query")
    c.add_argument("--query-file", help="批量查询 JSON：数组项 {name,sql,expect}")
    c.add_argument("--expect-rows", type=int)
    c.add_argument("--expect-value")
    c.add_argument("--out")

    d = sub.add_parser("dom", help="页面元素断言（需 playwright）")
    d.add_argument("--url", required=True)
    d.add_argument("--selector", required=True)
    d.add_argument("--expect-count", type=int)
    d.add_argument("--expect-texts", help="逗号分隔的期望文本序列")
    d.add_argument("--wait-ms", type=int, default=0)
    d.add_argument("--headful", action="store_true")
    d.add_argument("--timeout", type=int, default=20)
    d.add_argument("--out")

    args = ap.parse_args()
    if args.cmd == "artifact":
        return run_artifact(args)
    if args.cmd == "api":
        return run_api(args)
    if args.cmd == "db":
        expect = {}
        if args.expect_rows is not None:
            expect["rows"] = args.expect_rows
        if args.expect_value is not None:
            expect["value"] = args.expect_value
        args.inline_expect = expect
        if args.query_file is None and not args.query:
            raise SystemExit("[fatal] --query 或 --query-file 必传其一")
        return run_db(args)
    if args.cmd == "dom":
        return run_dom(args)
    raise SystemExit("[fatal] 未知子命令")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            sys.exit(ENV_ERROR)
        sys.exit(exc.code if isinstance(exc.code, int) else ENV_ERROR)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        sys.exit(ENV_ERROR)
