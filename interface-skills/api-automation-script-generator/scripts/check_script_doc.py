# -*- coding: utf-8 -*-
"""
自动化脚本文档自检（automation-script-generator 第 5 步通用，App / Web 共用）。

本 skill 的「自检」条目多是机械可查的，人工容易漏：
  - 每条脚本能追溯到原用例编号；
  - 每例有断言，且只判界面层事实；
  - 全文无 `sleep`、无编造控件、无下标或坐标定位、无硬编码凭据；
  - 代码里引用的页面对象方法/属性在仓库里真实存在。

栈相关的差异用「档位」区分，避免把别的栈的规矩套上来：
  app   —— Appium + pytest，断言走 `self.assertions.*` 工具类，禁裸 assert
  web   —— Selenium/Playwright + pytest，断言走原生 assert，禁 sleep/坐标定位

用法：
    python check_script_doc.py ../../test-design/app/05-自动化脚本.md
    python check_script_doc.py ... --repo d:/project/moto_app_ui --profile app
    python check_script_doc.py ... --repo d:/project/moto_web_ui --profile web
    python check_script_doc.py ... --json out.json

`--repo` 会扫描仓库的页面对象目录与公共目录，把代码块里调用的
不存在的方法/属性报出来——这是最容易出错、也最难肉眼发现的一类问题。
文档里"新增"的页面对象（写在文档中的类）不参与仓库校验。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mdutil import read_text, extract_code_blocks  # noqa: E402


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError —— 强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()

# 栈档位：禁用的模式 + 仓库目录 + 用例编号格式
PROFILES = {
    "app": {
        "repo_dirs": ["page_objects", "common"],
        "id_regex": r"\bTC-[A-Z]+-\d{3}\b",
        "forbidden": [
            ("裸 sleep", re.compile(r"(?<![\w.])time\.sleep\s*\(")),
            ("裸 assert", re.compile(r"(?<![\w.])assert\s+\w")),
            ("下标定位", re.compile(r"find_elements\s*\([^)]*\)\s*\[\s*\d+\s*\]")),
            ("坐标定位", re.compile(r"(?:click|tap)\s*\(\s*\d+\s*,\s*\d+\s*\)")),
            ("真实手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
            ("硬编码密码", re.compile(r"(?:password|passwd|pwd)\s*=\s*[\"'][^\"']{4,}[\"']")),
        ],
        "assert_call": re.compile(r"assertions\.(\w+)\s*\("),
    },
    "web": {
        "repo_dirs": ["pages", "core", "common", "utils"],
        "id_regex": r"\b[A-Z][A-Z0-9]*(?:-UI)?-\d{3}\b",
        "forbidden": [
            ("裸 sleep", re.compile(r"(?<![\w.])time\.sleep\s*\(")),
            ("下标定位", re.compile(r"find_elements\s*\([^)]*\)\s*\[\s*\d+\s*\]")),
            ("坐标定位", re.compile(r"(?:click|tap|move_to)\s*\(\s*\d+\s*,\s*\d+\s*\)")),
            ("真实手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
            ("硬编码密码", re.compile(r"(?:password|passwd|pwd)\s*=\s*[\"'][^\"']{4,}[\"']")),
        ],
        # Web 栈用 pytest 原生 assert，不算「裸 assert」问题
        "assert_call": re.compile(r"assertions\.(\w+)\s*\("),
    },
    "api": {
        "repo_dirs": ["api", "clients", "core", "common", "utils"],
        "id_regex": r"\b(?:TC|API)-[A-Z]+-\d{3}\b",
        "forbidden": [
            ("裸 sleep", re.compile(r"(?<![\w.])time\.sleep\s*\(")),
            ("真实手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
            ("硬编码密码", re.compile(r"(?:password|passwd|pwd)\s*=\s*[\"'][^\"']{4,}[\"']")),
            ("硬编码 token", re.compile(r"(?:token|access_token|bearer)\s*=\s*[\"'][^\"']{8,}[\"']",
                                       re.I)),
        ],
        # 接口栈用 pytest 原生 assert
        "assert_call": re.compile(r"assertions\.(\w+)\s*\("),
    },
}


def _in_wait_helper(text: str, pos: int) -> bool:
    """判断某位置是否落在显式等待函数（名字含 wait/poll）内部。"""
    head = text[:pos]
    last = None
    for m in re.finditer(r"def\s+(\w*(?:wait|poll)\w*)\s*\(", head, re.I):
        last = m
    if last is None:
        return False
    tail = head[last.end():]
    if re.search(r"\ndef\s+\w+\s*\(", tail):
        return False
    return True


def scan_repo(repo: str, repo_dirs):
    """收集仓库里真实存在的类 -> 成员集合（含基类继承成员与类级常量）。"""
    raw = {}
    bases = set()
    for sub in repo_dirs:
        base = os.path.join(repo, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                src = read_text(os.path.join(dirpath, fn))
                for m in re.finditer(r"^class\s+(\w+)\s*[:(]([^\n]*)", src, re.M):
                    name, tail = m.group(1), m.group(2)
                    body = src[m.end():]
                    members = set(re.findall(r"def\s+(\w+)\s*\(", body))
                    members |= set(re.findall(r"self\.(\w+)\s*=", body))
                    members |= set(re.findall(r"^\s{4}([A-Z_][A-Z0-9_]*)\s*=", body, re.M))
                    raw.setdefault(name, set()).update(members)
                    for b in re.findall(r"\b(\w+)\b", tail):
                        if b not in ("object", "BaseException", "Exception"):
                            bases.add((name, b))
    for child, parent in bases:
        if parent in raw:
            raw.setdefault(child, set()).update(raw[parent])
    return raw


def doc_classes(blocks):
    """文档里定义的类（新增页面对象）—— 这些不参与仓库存在性校验。"""
    names = set()
    for _lang, code, _s, _e in blocks:
        for m in re.finditer(r"^class\s+(\w+)\s*[:(]", code, re.M):
            names.add(m.group(1))
    return names


def doc_members(blocks):
    """文档代码块里新增的方法/属性名 —— 它们是"本次改动"，不算仓库里缺失。"""
    names = set()
    for _lang, code, _s, _e in blocks:
        names |= set(re.findall(r"def\s+(\w+)\s*\(", code))
        names |= set(re.findall(r"self\.([A-Z_][A-Z0-9_]*)\s*=", code))
    return names


def local_instances(code):
    """从代码块里推断 `self.<var> = <Class>(...)` 的对应关系。"""
    out = {}
    for m in re.finditer(r"self\.(\w+)\s*=\s*(\w+)\s*\(", code):
        out[m.group(1)] = m.group(2)
    return out


def check_api_usage(blocks, classes, skip, extra_members):
    """检查 `Cls.method(` 与 `self.<var>.method(` 调用的方法是否真实存在。"""
    problems = []
    for bi, (_lang, code, _start, _end) in enumerate(blocks):
        inst = local_instances(code)
        for mm in re.finditer(r"\b([A-Z]\w+)\.(\w+)\s*\(", code):
            cls, meth = mm.group(1), mm.group(2)
            if cls not in classes or cls in skip:
                continue
            if meth not in classes[cls] and meth not in extra_members:
                problems.append("第 %d 段代码：%s.%s 在仓库里不存在" % (bi + 1, cls, meth))
        for mm in re.finditer(r"self\.(\w+)\.(\w+)\s*\(", code):
            var, meth = mm.group(1), mm.group(2)
            cls = inst.get(var)
            if not cls or cls not in classes or cls in skip:
                continue
            if meth not in classes[cls] and meth not in extra_members:
                problems.append("第 %d 段代码：%s.%s 在仓库里不存在"
                                % (bi + 1, cls, meth))
    return problems


def check_attr_usage(blocks, classes, skip, extra_members):
    """检查 `self.<var>.<attr>` 读取的属性是否真实存在（方法调用已由上面覆盖）。"""
    problems = []
    for bi, (_lang, code, _start, _end) in enumerate(blocks):
        inst = local_instances(code)
        for mm in re.finditer(r"self\.(\w+)\.(\w+)\b(?!\s*\()", code):
            var, attr = mm.group(1), mm.group(2)
            cls = inst.get(var)
            if not cls or cls not in classes or cls in skip:
                continue
            if attr not in classes[cls] and attr not in extra_members:
                problems.append("第 %d 段代码：%s.%s 在仓库里不存在"
                                % (bi + 1, cls, attr))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="自动化脚本文档自检")
    ap.add_argument("src")
    ap.add_argument("--repo", default=None, help="目标仓库路径，用于校验 API 是否存在")
    ap.add_argument("--profile", default="app", choices=sorted(PROFILES),
                    help="栈档位：app / web（决定禁用模式与仓库目录）")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    prof = PROFILES[args.profile]
    text = read_text(args.src)
    blocks = extract_code_blocks(text)
    problems = []

    # 1) 禁止模式：只在代码块里扫（正文里提到"不用 sleep"这类说明不算违规）
    for bi, (_lang, code, start, _end) in enumerate(blocks):
        for label, pat in prof["forbidden"]:
            for m in pat.finditer(code):
                line = start + code[:m.start()].count("\n")
                if label == "裸 sleep" and _in_wait_helper(code, m.start()):
                    continue  # 显式等待函数内部的轮询间隔允许
                problems.append("第 %d 段代码（第 %d 行）出现%s：%s"
                                % (bi + 1, line, label, m.group(0)[:40].strip()))

    # 2) 可追溯性：每个用例函数附近要有用例编号
    case_ids = set(re.findall(prof["id_regex"], text))
    lines = text.split("\n")
    for _lang, code, start, end in blocks:
        for m in re.finditer(r"def\s+(test_\w+)", code):
            window = "\n".join(lines[max(0, start - 12):end + 2])
            if not re.search(prof["id_regex"], window):
                problems.append("函数 %s 附近找不到用例编号（无法追溯）" % m.group(1))

    # 3) 每个测试函数都要有断言
    for _lang, code, _start, _end in blocks:
        for m in re.finditer(r"def\s+(test_\w+)\(", code):
            fn = m.group(1)
            tail = code[m.end():]
            nxt = re.search(r"\n    (?:@|def )", tail)
            body = tail[:nxt.start()] if nxt else tail
            has_assert = re.search(r"assertions\.|assert_|(?<![\w.])assert\b", body)
            if not has_assert:
                # 整体 skip 的用例（待补清单里的卡点用例）不要求断言
                if re.search(r"pytest\.skip\s*\(", body):
                    continue
                problems.append("%s 没有断言" % fn)

    # 4) API 存在性
    if args.repo:
        classes = scan_repo(args.repo, prof["repo_dirs"])
        skip = doc_classes(blocks)
        extra = doc_members(blocks)
        problems += check_api_usage(blocks, classes, skip, extra)
        problems += check_attr_usage(blocks, classes, skip, extra)

    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"blocks": len(blocks), "cases": len(case_ids),
                                "problems": problems}, ensure_ascii=False, indent=2))
        print("已写出 %s" % args.json_out)

    print("代码块 %d 个 / 覆盖用例编号 %d 个（档位 %s）"
          % (len(blocks), len(case_ids), args.profile))
    if problems:
        print("问题 %d 条：" % len(problems))
        for p in problems[:60]:
            print("  ⚠ %s" % p)
        if len(problems) > 60:
            print("  …（其余 %d 条省略）" % (len(problems) - 60))
        return 1
    print("✓ 无 sleep / 无下标坐标定位 / 无硬编码凭据，脚本可追溯且有断言")
    return 0


if __name__ == "__main__":
    sys.exit(main())
