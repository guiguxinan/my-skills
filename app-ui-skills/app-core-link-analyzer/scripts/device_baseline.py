# -*- coding: utf-8 -*-
"""
设备基线采集 / 核对（core-link-analyzer 第 1 步专用）。

核心链路分析允许在设备上做真实操作，但"先记基线，再动手"是硬规则：
进入分析前采集一次设备基线，结束时再采一次做对比，确认状态一致。

用法：
    python device_baseline.py snapshot            # 采集并打印 Markdown 基线表
    python device_baseline.py snapshot --json out.json
    python device_baseline.py compare --before a.json --after b.json
    python device_baseline.py restore --proxy 10.0.2.2:9000

只依赖标准库 + adb。所有 adb 调用统一带 -s <udid>（设备列表可能有多条同设备条目，
不带 -s 会静默失败）。输出统一 UTF-8。
"""
from __future__ import annotations


import argparse
import io
import json
import os
import re
import subprocess
import sys


# ------------------------------------------------------------------ adb


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()
def adb(udid: str, args: list[str], timeout: int = 30) -> str:
    """执行一次 adb 调用（统一带 -s），返回 stdout（UTF-8，容错解码）。"""
    cmd = ["adb", "-s", udid] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise SystemExit("找不到 adb，请确认在 PATH 中")
    except subprocess.TimeoutExpired:
        return ""
    out = proc.stdout.decode("utf-8", errors="replace")
    return out.strip()


def adb_devices() -> list[str]:
    out = adb("", ["devices"], timeout=15) if False else subprocess.run(
        ["adb", "devices"], capture_output=True, timeout=15
    ).stdout.decode("utf-8", errors="replace")
    result = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            result.append(parts[0])
    return result


def pick_device(udid: str | None) -> str:
    devices = adb_devices()
    if not devices:
        raise SystemExit("没有在线设备（adb devices 为空）")
    if udid:
        if udid not in devices:
            raise SystemExit("设备 %s 不在线，在线的是：%s" % (udid, devices))
        return udid
    if len(devices) > 1:
        raise SystemExit(
            "有 %d 台在线设备，请用 --udid 指定：%s" % (len(devices), devices))
    return devices[0]


# ------------------------------------------------------------------ 采集项


def collect(udid: str, package: str | None) -> dict:
    """采集设备基线快照。"""
    base = {"udid": udid}

    # 包版本
    base["package"] = package
    base["version"] = ""
    if package:
        ver = adb(udid, ["shell", "dumpsys", "package", package])
        m = re.search(r"versionName=(\S+)", ver)
        base["version"] = m.group(1) if m else ""
        m = re.search(r"versionCode=(\d+)", ver)
        base["versionCode"] = m.group(1) if m else ""

    # 前台窗口
    focus = adb(udid, ["shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"])
    base["foreground"] = focus.strip()

    # 代理
    base["http_proxy"] = adb(
        udid, ["shell", "settings", "get", "global", "http_proxy"])

    # 系统语言 / 时区
    base["locale"] = adb(udid, ["shell", "getprop", "persist.sys.locale"]) or \
        adb(udid, ["shell", "getprop", "ro.product.locale"])
    base["timezone"] = adb(udid, ["shell", "getprop", "persist.sys.timezone"])

    # 屏幕分辨率
    size = adb(udid, ["shell", "wm", "size"])
    m = re.search(r"(\d+)x(\d+)", size)
    base["resolution"] = "%sx%s" % (m.group(1), m.group(2)) if m else size

    # 系统版本
    base["android"] = adb(udid, ["shell", "getprop", "ro.build.version.release"])
    base["sdk"] = adb(udid, ["shell", "getprop", "ro.build.version.sdk"])

    # 已授予的权限
    if package:
        base["granted_permissions"] = sorted(
            set(re.findall(r"^\s*([\w.]+): granted=true",
                           adb(udid, ["shell", "dumpsys", "package", package]),
                           re.M)))
    else:
        base["granted_permissions"] = []

    # 屏幕状态（息屏会影响后续实测）
    base["screen_on"] = "unlock" if adb(
        udid, ["shell", "dumpsys", "power"]) .count("mWakefulness=Awake") else "unknown"

    return base


# ------------------------------------------------------------------ 输出


def to_markdown(base: dict) -> str:
    rows = [
        ("被测包", base.get("package") or "—"),
        ("版本", base.get("version") or "—"),
        ("设备", "%s（Android %s / SDK %s / %s）"
         % (base.get("udid"), base.get("android"), base.get("sdk"),
            base.get("resolution"))),
        ("前台窗口", base.get("foreground") or "—"),
        ("系统代理", base.get("http_proxy") or "（无）"),
        ("系统语言", base.get("locale") or "—"),
        ("时区", base.get("timezone") or "—"),
        ("已授予权限", "、".join(base.get("granted_permissions") or []) or "—"),
    ]
    out = ["| 项 | 值 |", "| :--- | :--- |"]
    for k, v in rows:
        out.append("| %s | %s |" % (k, str(v).replace("|", "/")))
    return "\n".join(out)


def diff(before: dict, after: dict) -> list[str]:
    """比对两份基线，返回差异描述列表（空表示完全一致）。"""
    notes = []
    for key, label in [("version", "包版本"), ("foreground", "前台窗口"),
                       ("http_proxy", "系统代理"), ("locale", "系统语言"),
                       ("timezone", "时区"), ("resolution", "分辨率")]:
        b, a = before.get(key), after.get(key)
        if b != a:
            notes.append("%s：基线 `%s` → 当前 `%s`" % (label, b, a))
    bp = set(before.get("granted_permissions") or [])
    ap = set(after.get("granted_permissions") or [])
    for p in sorted(ap - bp):
        notes.append("权限新增授予：%s" % p)
    for p in sorted(bp - ap):
        notes.append("权限被撤销：%s" % p)
    return notes


# ------------------------------------------------------------------ CLI


def main() -> int:
    ap = argparse.ArgumentParser(description="设备基线采集 / 核对")
    ap.add_argument("action", choices=["snapshot", "compare", "restore"])
    ap.add_argument("--udid", default=None)
    ap.add_argument("--package", default=None)
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--before", default=None)
    ap.add_argument("--after", default=None)
    ap.add_argument("--proxy", default=None, help="restore 时要写回的代理，如 10.0.2.2:9000")
    args = ap.parse_args()

    udid = pick_device(args.udid)

    if args.action == "snapshot":
        base = collect(udid, args.package)
        md = to_markdown(base)
        if args.json_out:
            with io.open(args.json_out, "w", encoding="utf-8") as f:
                f.write(json.dumps(base, ensure_ascii=False, indent=2))
        print(md)
        return 0

    if args.action == "compare":
        with io.open(args.before, encoding="utf-8") as f:
            before = json.load(f)
        after = (json.load(io.open(args.after, encoding="utf-8"))
                 if args.after else collect(udid, args.package))
        notes = diff(before, after)
        if notes:
            print("与基线不一致，需要回填或恢复：")
            for n in notes:
                print("- %s" % n)
            return 1
        print("与基线一致")
        return 0

    if args.action == "restore":
        if not args.proxy:
            raise SystemExit("restore 需要 --proxy")
        adb(udid, ["shell", "settings", "put", "global", "http_proxy", args.proxy])
        now = adb(udid, ["shell", "settings", "get", "global", "http_proxy"])
        print("代理已写回：%s" % now)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
