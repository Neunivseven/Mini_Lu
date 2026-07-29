---
name: mini-lu-coding
description: >-
  Mini_Lu 编程协作约定：结构优先用 TSA（read_outline/list_symbols/find_callers），
  小改动用 edit_file，禁止默认整文件 Read。在用户要求改代码、查调用关系、梳理项目结构时使用。
disable-model-invocation: false
always_inject: false
---
# Mini_Lu 编程协作

## 原则

1. **结构优先**：`glob_files` → `read_outline` / `list_symbols`；跨文件用 `index_codebase` + `find_callers` / `find_callees`。
2. **精确修改**：优先 `edit_file`；不要为小改动 `write_file`。
3. **省 token**：`read_file` 带 `focus` / `offset`/`limit`；默认约 80 行。
4. **工作区**：相对路径相对当前项目根；先确认 `list_workspaces` / 当前工作区。

## 回答

改完后只复核相关片段；用中文简述改了什么、为什么。
