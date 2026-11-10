# my-skills

测试设计流水线的三套 Agent Skill：Web UI、App UI、接口，每套五个阶段，可单步调用也可串成一条流水线。

## 目录

| 目录 | 用途 | 前缀 |
| :--- | :--- | :--- |
| `web-ui-skills/` | 网页 / Web 管理后台的界面测试设计 | `web-` |
| `app-ui-skills/` | Android / iOS App、小程序的端上测试设计 | `app-` |
| `interface-skills/` | 接口层测试设计，覆盖契约、鉴权、幂等并发 | `api-` |
| `pipelines/` | 编排入口，一条命令串跑五个阶段 | `web-pipeline` / `app-pipeline` |

每个目录内只有 `SKILL.md`，无 scripts / references / assets 附属文件。

## 五个阶段

| 阶段 | Web | App | 接口 |
| :--- | :--- | :--- | :--- |
| 1 核心链路分析 | `web-core-link-analyzer` | `app-core-link-analyzer` | `api-core-link-analyzer` |
| 2 测试点生成 | `web-test-point-generator` | `app-test-point-generator` | `api-test-point-generator` |
| 3 测试用例编写 | `web-test-case-writer` | `app-test-case-writer` | `api-test-case-writer` |
| 4 自动化选型 | `web-automation-feasibility-analyzer` | `app-automation-feasibility-analyzer` | `api-automation-feasibility-analyzer` |
| 5 脚本生成 | `web-automation-script-generator` | `app-automation-script-generator` | `api-automation-script-generator` |

## 使用

Claude Code 个人级安装：把某个 skill 目录复制或链接到 `~/.claude/skills/<name>/`，然后用 `/skill-name` 调用。

```text
/web-core-link-analyzer https://example.com/order/list
/app-core-link-analyzer D:\apk\app-release.apk
/api-core-link-analyzer docs\openapi.yaml
```

一条命令跑完整条链路（第 2 个参数是输出目录，第 3 个是止步位置）：

```text
/web-pipeline <网址或截图> [输出目录] [止步第几步]
/app-pipeline <安装包或截图> [输出目录] [止步第几步]
```

每个 Skill 也可单独调用：把上一步的产物文件（或任意来源的用例表、测试点清单）直接挂上去即可，不必从第 1 步开始。

## 产物约定

- 统一输出单个 Markdown 文档，节标题用中文序号固定名，表格列序固定
- Web / App 用例表列序：`用例编号 | 关联测试点 | 用例标题 | 优先级 | 前置条件 | 步骤 | 预期结果`
- 数据状态只取三态：`已有` / `[待准备]` / `[待确认]`；缺材料时停在缺料处提问，不靠假设推进
- 没有真实页面、控件、接口文档时不编造定位器、路径与文案，一律标 `[待补]` 并进待确认清单
