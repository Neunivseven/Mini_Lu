# Mini_Lu Skills 接入说明

Skills 是 Cursor 风格的短说明书：放在 `skills/<name>/SKILL.md`，Agent 按需加载，避免把大段流程塞进默认 prompt。

## 三步接入

### 1. 建目录与文件

```text
skills/
  my-review/
    SKILL.md
```

也可放在 `data/skills/`（同样被扫描）。

在「扩展 · MCP / Skills」面板可点 **新建 Skill…** 自动生成模板。

### 2. 写好 YAML 头

```yaml
---
name: my-review
description: >-
  用户要求做代码评审 / PR 检查时使用。
disable-model-invocation: false   # false=模型可自行 load_skill
always_inject: false              # true=正文自动注入 system（慎用）
---

# 正文：步骤、约定、检查清单…
```

| 字段 | 含义 |
|------|------|
| `name` | 唯一名；工具 `load_skill(name)` 用它 |
| `description` | 给模型看的检索摘要，写清「何时用」 |
| `disable-model-invocation: false` | 推荐：允许模型按需加载 |
| `always_inject: true` | 每次对话注入全文（占 token） |

### 3. 启用并验证

1. 打开右键菜单 → **扩展（MCP/Skills）…**
2. 点 **刷新**，列表出现 `✓ my-review`
3. 需要时用 **禁用 / 启用**（写入 `config/skills.local.yaml`）
4. 用面板 **Auto / Manual / Always** 切换调用模式（同样只写 local，**不改 SKILL.md**）
5. 聊天里提相关需求，或说「加载 skill my-review」

配置文件：

- `config/skills.yaml` — 默认
- `config/skills.local.yaml` — 本机启用/禁用、`skill_modes`

## 模式对照

优先读 `skills.local.yaml` 的 `skill_modes`；未覆盖时才看 SKILL.md frontmatter。

| 模式 | 默认来源（无 local 覆盖时） | 行为 |
|------|---------------------------|------|
| auto | `disable-model-invocation: false` | 目录可见；正文可注入（受字数上限）或 `load_skill` |
| always | `always_inject: true` | 正文优先注入 system |
| manual | `disable-model-invocation: true` 且非 always | 仅目录；需显式 `load_skill` |

## Agent 工具

- `list_skills` — 列出可用 skill
- `load_skill` — 加载指定 skill 全文到当前上下文

## 示例

仓库自带 `skills/mini-lu-coding/SKILL.md`（编程协作：TSA + `edit_file`）。可复制改名后改 description。
