# scripts

本目录是「把有条件自动化变成可自动化」的通用工具集。**不绑定任何项目**：表名、字段名、URL、账号、期望值全部走参数或配置文件，换项目只用改传入的值，不改脚本。

## 脚本清单

| 脚本 | 职责 | 什么时候用 | 依赖 |
| :--- | :--- | :--- | :--- |
| `case_table_reader.py` | 把 xlsx / xls / csv / md / json 用例表归一成标准 JSON（列名按别名映射） | 分池第一步，先把用例表读成统一结构 | 读 xlsx 需 openpyxl |
| `signal_scan.py` | 按关键词规则扫出「一票否决候选 / 有条件候选 / 未识别」，并给出建议前置动作与建议脚本 | 拿到用例表后先跑一遍，定位卡点分布 | 无 |
| `pool_report.py` | 把逐条五维评分渲染成三分池 Markdown（固定节标题与列序） | 评估完成后出正式分池文档 | 读 xlsx 需 openpyxl |
| `checks.py` | 断言工具箱：`artifact` 文件产物、`api` 接口、`db` SQL、`dom` 页面元素 | 有条件项补前置时，替代人眼逐格核对与手点页面 | 标准库；sqlite 内置；mysql/pg 与 playwright 为可选 |
| `fixture_guard.py` | 破坏性用例的环境保护：执行前后快照、差异比对、生成回滚 SQL 草案（不执行） | 删除/覆盖/直接写库类用例 | sqlite 内置；mysql 需 pymysql |
| `score_cases.py` | 五维打分与分池核算：`--template` 按用例编号生成打分 CSV，`--csv` 算汇总表草稿，`--verify` 核对产物三张表与汇总数字是否自洽、分池区间有无越界 | 走 Markdown 工作流（03-测试用例.md → 04-自动化选型.md）时 | 无（import 同目录 mdutil） |
| `mdutil.py` | Markdown 通用工具库：表格解析、章节替换、标题统计、代码块提取 | 被上面的脚本 import，不单独调用 | 无 |

Markdown 工作流与 JSON 工作流二选一：源与产物都是 `.md` 时用 `score_cases.py`（能回读既有 04 文档做自洽校验）；源是表格、或需要脚本直接渲染三分池 Markdown 时用 `case_table_reader.py` + `pool_report.py`。

## 一条链跑完

```bash
python scripts/case_table_reader.py --input cases.xlsx --out cases.json
python scripts/signal_scan.py --input cases.json --out signals.json
# 人工/模型按 signals 填五维分与卡点，产出 assessment.json（结构见下）
python scripts/pool_report.py --input assessment.json --out pool.md
```

评估表最小结构（JSON）：

```json
{"source": "cases.xlsx", "assumptions": ["环境为测试库"], "cases": [
  {"id": "TC-001", "title": "导入覆盖只覆盖中文名", "module": "车系", "pri": "P1", "type": "混合",
   "scores": {"freq": 4, "stab": 4, "data": 2, "assert": 3, "cost": 4},
   "blocker": "需造多字段文件并核对库", "actions": ["造数夹具", "SQL 断言"],
   "scripts": [{"name": "checks.py", "sub": "db", "params": "--dsn … --query …"}],
   "batch": 1, "minutes_manual": 20, "yearly_runs": 12}
]}
```

判定由 `pool_report.py` 自动推导：命中 `veto` → 手工；否则总分 ≥18 可自动化、12–17 有条件、<12 手工。

## 断言用法

文件产物（导出件、模板、失败明细）：

```bash
python scripts/checks.py artifact --file out.xlsx --rule-file rules.json
```

`rules.json` 支持的断言类型：

```json
[
  {"type": "cell", "cell": "A1", "equals": "导入失败数据"},
  {"type": "header", "row": 2, "equals": ["中文名", "英文名", "描述", "失败原因"]},
  {"type": "row", "row": 3, "equals": ["A", "", "", "名称重复"]},
  {"type": "rowcount", "min": 3, "exclude_head": 2},
  {"type": "merged", "range": "A1:D1"},
  {"type": "empty", "cell": "B3"},
  {"type": "contains", "cell": "D3", "value": "重复"}
]
```

接口（绕过前端、幂等、权限）：

```bash
python scripts/checks.py api --method POST --url "$BASE/x/add" \
  --headers-file auth.json --body-file body.json \
  --expect-status 200 --expect-json '{"code":500,"msg":"长度不能超过50位"}'
python scripts/checks.py api --method POST --url "$BASE/x/add" --body-file body.json \
  --repeat 5 --expect-one-success "提交成功"
```

数据库：

```bash
python scripts/checks.py db --dsn "sqlite:///t.db" \
  --query "SELECT count(*) FROM s WHERE is_deleted=0" --expect-value 2
python scripts/checks.py db --dsn "mysql://u:p@h:3306/db" --query-file queries.json --out db.json
```

`queries.json` 每项：`{"name": "未删关联数", "sql": "...", "expect": {"rows": 2}}`，`expect` 支持 `rows` / `min_rows` / `value` / `first_row`。

破坏性操作前后：

```bash
python scripts/fixture_guard.py snapshot --dsn "mysql://…" --query-file q.json --out before.json
# 执行被测操作
python scripts/fixture_guard.py snapshot --dsn "mysql://…" --query-file q.json --out after.json
python scripts/fixture_guard.py diff --before before.json --after after.json --out diff.json
python scripts/fixture_guard.py plan-restore --diff diff.json --out rollback.sql
```

`plan-restore` 只写文件，**不执行**回滚；执行前必须人工复核表名与主键。

## 换项目要改什么

1. 用例表列名不同 → `case_table_reader.py --map '{"id":"测试编号","expect":"检查点"}'`，或 `--map-file`。
2. 判据关键词不同 → `signal_scan.py --rules my_rules.json`，只叠加/覆盖命中的规则项，不改代码。
3. 连接串、URL、账号 → 全部命令行传参；不写死在脚本里。
4. 断言期望值 → 写进 `rules.json` / `queries.json`，脚本本身不含任何业务字段。

## 退出码

`0` 全部通过；`1` 有断言失败或存在差异；`2` 输入错误、缺依赖等环境问题。缺依赖时脚本会直接说明装什么，不做静默跳过。
