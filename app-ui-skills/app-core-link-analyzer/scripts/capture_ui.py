# -*- coding: utf-8 -*-
"""
界面取证：抓当前屏的控件树与可见文案（core-link-analyzer 第 1 步专用）。

链路分析里最花时间的一步是"把这一屏真实有什么抄下来"。
本脚本一次输出三样东西，全部落盘到 --out 目录：

    ui.xml         uiautomator dump 的原始控件树（证据留存）
    texts.txt      可见文案的纯文本列表（抄文案用）
    summary.md     文案 + 关键控件摘要（直接粘进链路清单的"关键节点"）

用法：
    python capture_ui.py --out out/ui_01
    python capture_ui.py --out out/ui_02 --package uni.UNIEDFB430

注意：uni-app WebView 内的元素在 uiautomator 树里**没有** resource-id 前缀；
原生组件才有 `uni.UNIEDFB430:id/` 前缀。脚本会把两者分开标注，避免误判。
"""
from __future__ import annotations


import argparse
import io
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

RES_ID_RE = re.compile(r"^([\w.]+):id/(.+)$")


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()
def adb(udid: str, args: list[str], timeout: int = 40) -> str:
    cmd = ["adb", "-s", udid] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise SystemExit("找不到 adb")
    except subprocess.TimeoutExpired:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


def adb_devices() -> list[str]:
    out = subprocess.run(["adb", "devices"], capture_output=True,
                         timeout=15).stdout.decode("utf-8", errors="replace")
    return [l.split()[0] for l in out.splitlines()[1:]
            if len(l.split()) >= 2 and l.split()[1] == "device"]


def pick_device(udid: str | None) -> str:
    devs = adb_devices()
    if not devs:
        raise SystemExit("没有在线设备")
    if udid:
        return udid
    if len(devs) > 1:
        raise SystemExit("多台在线设备，请用 --udid 指定：%s" % devs)
    return devs[0]


def dump_xml(udid: str, remote: str = "/sdcard/ui.xml") -> str:
    adb(udid, ["shell", "uiautomator", "dump", remote])
    return adb(udid, ["shell", "cat", remote])


def parse_nodes(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    nodes = []
    for el in root.iter("node"):
        nodes.append({
            "text": el.get("text", ""),
            "desc": el.get("content-desc", ""),
            "res_id": el.get("resource-id", ""),
            "cls": el.get("class", ""),
            "pkg": el.get("package", ""),
            "clickable": el.get("clickable") == "true",
            "bounds": el.get("bounds", ""),
            "checked": el.get("checked"),
            "focused": el.get("focused"),
        })
    return nodes


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="抓当前屏控件树与文案")
    ap.add_argument("--udid", default=None)
    ap.add_argument("--package", default=None, help="只保留该包的节点")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()

    udid = pick_device(args.udid)
    xml_text = dump_xml(udid)
    if not xml_text or "<hierarchy" not in xml_text:
        raise SystemExit("uiautomator dump 失败（可能正在动画或屏幕已锁）")

    nodes = parse_nodes(xml_text)
    if args.package:
        nodes = [n for n in nodes if n["pkg"] == args.package]

    write(os.path.join(args.out, "ui.xml"), xml_text)

    texts, native_ids, web_ids = [], [], []
    for n in nodes:
        for t in (n["text"], n["desc"]):
            if t and t.strip() and t.strip() not in texts:
                texts.append(t.strip())
        rid = n["res_id"]
        if rid:
            m = RES_ID_RE.match(rid)
            if m:
                native_ids.append("%s -> %s" % (rid, n["cls"]))
            else:
                web_ids.append("%s" % rid)

    write(os.path.join(args.out, "texts.txt"), "\n".join(texts))

    md = ["# 当前屏取证", "",
          "- 设备：%s" % udid,
          "- 节点总数：%d" % len(nodes),
          "- 可见文案 %d 条" % len(texts), "",
          "## 文案", ""]
    md += ["- %s" % t for t in texts]
    md += ["", "## 原生控件（带 package:id/ 前缀）", ""]
    md += ["- %s" % x for x in native_ids] or ["- （无）"]
    md += ["", "## WebView 元素（无前缀，只能用 XPath）", ""]
    md += ["- %s" % x for x in web_ids] or ["- （无）"]
    write(os.path.join(args.out, "summary.md"), "\n".join(md))

    print("输出目录：%s" % args.out)
    print("节点 %d 个 / 文案 %d 条 / 原生 id %d 个 / WebView 元素 %d 个"
          % (len(nodes), len(texts), len(native_ids), len(web_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
