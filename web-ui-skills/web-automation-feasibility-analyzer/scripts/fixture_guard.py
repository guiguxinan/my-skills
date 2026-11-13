#!/usr/bin/env python3
"""保护破坏性用例的执行环境：执行前快照、执行后比对、生成回滚 SQL 草案。

它不执行任何写操作——回滚 SQL 只写进文件，由人复核后自己跑。

    python fixture_guard.py snapshot --dsn sqlite:///t.db --query-file q.json --out before.json
    python fixture_guard.py diff --before before.json --after after.json --out diff.json
    python fixture_guard.py plan-restore --diff diff.json --out rollback.sql

query-file 结构：[{"name":"车系总数","sql":"SELECT ...","key":"series_id"}]
    name  快照分组名（同一 name 前后比对）
    key   主键列名，用于按行匹配；省略则整行做集合比对

退出码：0 成功；1 存在差异（仅 diff 子命令）；2 输入或环境错误。
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def open_db(dsn):
    if dsn.startswith("sqlite:///"):
        import sqlite3
        return sqlite3.connect(dsn.replace("sqlite:///", "", 1))
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


def run_snapshot(args):
    queries = json.loads(Path(args.query_file).read_text(encoding="utf-8"))
    conn = open_db(args.dsn)
    items = []
    try:
        cursor = conn.cursor()
        for item in queries:
            cursor.execute(item["sql"])
            columns = [d[0] for d in (cursor.description or [])]
            rows = [[None if v is None else str(v) for v in r] for r in cursor.fetchall()]
            items.append({"name": item["name"], "sql": item["sql"],
                          "key": item.get("key"), "columns": columns, "rows": rows})
    finally:
        conn.close()
    payload = {"dsn": args.dsn, "taken_at": dt.datetime.now().isoformat(timespec="seconds"), "items": items}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 快照 {len(items)} 组 -> {args.out}")


def index_rows(item):
    key = item.get("key")
    columns = item["columns"]
    if key and key in columns:
        kidx = columns.index(key)
        return {str(row[kidx]): row for row in item["rows"]}, columns, kidx
    return {str(tuple(row)): row for row in item["rows"]}, columns, None


def run_diff(args):
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    bmap = {i["name"]: i for i in before["items"]}
    amap = {i["name"]: i for i in after["items"]}
    result = {"before": args.before, "after": args.after, "items": [], "changed": 0}
    for name in sorted(set(bmap) | set(amap)):
        bitem, aitem = bmap.get(name), amap.get(name)
        entry = {"name": name, "added": [], "removed": [], "changed": []}
        if bitem is None or aitem is None:
            entry["note"] = "仅一侧存在该查询，跳过比对"
            result["items"].append(entry)
            continue
        bindex, columns, kidx = index_rows(bitem)
        aindex, _, _ = index_rows(aitem)
        for key in aindex:
            if key not in bindex:
                entry["added"].append(aindex[key])
        for key in bindex:
            if key not in aindex:
                entry["removed"].append(bindex[key])
        for key in set(bindex) & set(aindex):
            if bindex[key] != aindex[key]:
                diffs = []
                for ci, col in enumerate(columns):
                    bv = bindex[key][ci] if ci < len(bindex[key]) else None
                    av = aindex[key][ci] if ci < len(aindex[key]) else None
                    if bv != av:
                        diffs.append({"column": col, "before": bv, "after": av})
                entry["changed"].append({"key": key, "differences": diffs})
        entry["columns"] = columns
        entry["key"] = bitem.get("key")
        if entry["added"] or entry["removed"] or entry["changed"]:
            result["changed"] += 1
        result["items"].append(entry)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(i["added"]) + len(i["removed"]) + len(i["changed"]) for i in result["items"])
    print(f"[ok] 差异 {total} 处，涉及 {result['changed']} 组 -> {args.out}")
    return 0 if total == 0 else 1


def sql_literal(value, quoted=True):
    if value is None:
        return "NULL"
    if not quoted:
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def run_plan_restore(args):
    diff = json.loads(Path(args.diff).read_text(encoding="utf-8"))
    lines = [
        "-- 回滚 SQL 草案（由 fixture_guard.py 生成，脚本不执行）",
        f"-- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        "-- 顺序按「先恢复被改、再补被删、最后清理被增」排列；表名与列名来自快照查询",
        "-- 执行前请人工复核：确认表名、主键、类型转换，并在测试库先跑一遍",
        "",
    ]
    for item in diff["items"]:
        columns = item.get("columns")
        name = item.get("name")
        if not columns:
            lines.append(f"-- {name}：缺少列信息，跳过")
            continue
        table = args.table or name
        if item["changed"]:
            lines.append(f"-- 恢复被修改的行（{name}）")
            for chg in item["changed"]:
                sets = "、".join(f"{d['column']}={sql_literal(d['before'])}" for d in chg["differences"])
                lines.append(f"UPDATE {table} SET {sets} WHERE {args.key_column}={sql_literal(chg['key'])};")
            lines.append("")
        if item["removed"]:
            lines.append(f"-- 补回被删除的行（{name}）")
            for row in item["removed"]:
                values = ", ".join(sql_literal(v) for v in row)
                lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});")
            lines.append("")
        if item["added"]:
            lines.append(f"-- 清理新增的行（{name}）")
            kidx = columns.index(args.key_column) if args.key_column in columns else 0
            for row in item["added"]:
                lines.append(f"DELETE FROM {table} WHERE {columns[kidx]}={sql_literal(row[kidx])};")
            lines.append("")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] 回滚草案 -> {args.out}（未执行）")


def main():
    ap = argparse.ArgumentParser(description="破坏性用例的环境快照与回滚草案")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="执行前/后快照")
    s.add_argument("--dsn", required=True)
    s.add_argument("--query-file", required=True)
    s.add_argument("--out", required=True)

    d = sub.add_parser("diff", help="比对两份快照")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--out", required=True)

    p = sub.add_parser("plan-restore", help="生成回滚 SQL 草案（不执行）")
    p.add_argument("--diff", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--table", help="覆盖表名，默认为快照查询组名")
    p.add_argument("--key-column", default="id")

    args = ap.parse_args()
    if args.cmd == "snapshot":
        return run_snapshot(args)
    if args.cmd == "diff":
        return run_diff(args)
    if args.cmd == "plan-restore":
        return run_plan_restore(args)
    raise SystemExit("[fatal] 未知子命令")


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        sys.exit(2)
