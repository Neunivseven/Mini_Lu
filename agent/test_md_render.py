"""md_render.normalize_markdown 回归（挤行表格/标题/目录树）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).with_name("md_render.py")
    spec = importlib.util.spec_from_file_location("md_render", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_crushed_codereview_table_and_headings():
    md = _load()
    sample = (
        "以下是完整的**CodeReview**。---##项目概况：`bigwheel_ros2`包。"
        "---##一、文件清单|分类|文件|行数|说明||---|---|---|---||"
        "**包元数据**|`package.xml`|32|说明A|||"
        "`setup.py`|33|说明B||**配置**|`c.yaml`|61|velocity）|"
        "---##二、功能拆解###1.启动器（Launch）####`launch.py`—桌面"
        "可视化-启动**robot**+**RViz2**###✅优点1.**双模式**清晰"
        "2.**延迟**机制"
    )
    out = md.normalize_markdown(sample)
    lines = out.splitlines()
    assert "|分类|文件|行数|说明|" in lines
    assert "|---|---|---|---|" in lines
    assert any(l.startswith("|**包元数据**|") for l in lines)
    assert "|**配置**|`c.yaml`|61|velocity）|" in lines
    assert "## 一、文件清单" in lines
    assert "## 二、功能拆解" in lines
    assert any(l.startswith("### 1.") for l in lines)
    assert any("优点" in l and l.startswith("###") for l in lines)
    assert any(l.startswith("1.**") for l in lines)
    assert "├── ##" not in out
    assert not any(l.endswith("|---|") and "velocity" in l for l in lines)


def test_directory_tree_still_wraps():
    md = _load()
    out = md.normalize_markdown("my_item/←桌宠│├──agent/│└──assets/")
    assert "```text" in out
    assert "├── agent/" in out
    assert "└── assets/" in out


def test_normal_table_untouched():
    md = _load()
    src = "|a|b|\n|---|---|\n|1|2|\n"
    out = md.normalize_markdown(src)
    assert "|a|b|" in out
    assert "|---|---|" in out
    assert "|1|2|" in out


def test_mismatched_table_columns_renders():
    """表头多一列标题 + 分隔/数据 3 列 → 修复后能出 <table>。"""
    md = _load()
    sample = (
        "|总结优先级:|优先级|问题|建议|\n"
        "|--------|------|------|\n"
        "|🔴P0|URDF不一致|清理冗余|\n"
        "|🟡P1|damping重复+Gazebo|合并到<gazebo>块|\n"
        "|🟢P3|过大|拆分|需要我对哪些动手修改? |\n"
    )
    out = md.normalize_markdown(sample)
    assert "### 总结优先级" in out
    assert "|优先级|问题|建议|" in out
    assert "|---|---|---|" in out
    html = md.markdown_to_html(sample)
    assert "<table" in html
    assert "优先级" in html
    assert "gazebo" in html.lower()


def test_issue_ref_hash_not_split_table_cell():
    """单元格内 (# 7) 不得被当成 ATX 标题拆行。"""
    md = _load()
    src = (
        "|优先级|问题|影响|\n"
        "|---|---|---|\n"
        "|● 高|链传动插件可能缺失 (# 7)|仿真物理行为与实车不一致|\n"
        "|● 高|dual_wheel硬编码依赖 (# 9)|outer_wheel丢失时仿真异常|\n"
    )
    out = md.normalize_markdown(src)
    assert "|● 高|链传动插件可能缺失 (# 7)|仿真物理行为与实车不一致|" in out
    assert out.count("|● 高|") == 2
    html = md.markdown_to_html(src)
    assert "<table" in html
    assert html.count("<tr>") == 3  # thead + 2 body


def test_table_continuation_rows_merge_into_last_col():
    md = _load()
    src = (
        "|优先级|问题|影响|\n"
        "|---|---|---|\n"
        "|● 高|链传动插件可能缺失 (# 7)|\n"
        "|仿真物理行为与实车不一致|\n"
        "|● 中|URDF阻尼不被SDF识别 (# 1)|\n"
        "|关节阻尼行为偏差|\n"
    )
    out = md.normalize_markdown(src)
    assert "|● 高|链传动插件可能缺失 (# 7)|仿真物理行为与实车不一致|" in out
    assert "|● 中|URDF阻尼不被SDF识别 (# 1)|关节阻尼行为偏差|" in out
    # 不应再残留单独描述行
    assert not any(
        ln.strip() in ("|仿真物理行为与实车不一致|", "|关节阻尼行为偏差|")
        for ln in out.splitlines()
    )
    html = md.markdown_to_html(src)
    assert html.count("<tr>") == 3


def test_two_tables_and_prose_not_swallowed():
    """第二表分隔行不得变成字面 ---；表后 ### / 后果 须出表。"""
    md = _load()
    src = (
        "|世界文件|worldname|\n"
        "|---|---|\n"
        "|balance.sdf|balance✅|\n"
        "|empty.sdf|未知|\n"
        "|**后果**：/clock收不到Gazebo时间 use_sim_time=true 节点错乱|\n"
        "|### 3. ⚠️包元数据未填写|\n"
        "|文件|结构|\n"
        "|---|---|\n"
        "|bigwheel.urdf|完整|\n"
    )
    out = md.normalize_markdown(src)
    assert "|---|---|" in out
    html = md.markdown_to_html(src)
    assert "<td>---</td>" not in html
    assert html.count("<table") == 2
    assert html.count("<h3") >= 1
    first = html.split("</table>")[0]
    assert "后果" not in first
    assert "后果" in html.split("</table>", 1)[1]


if __name__ == "__main__":
    test_crushed_codereview_table_and_headings()
    test_directory_tree_still_wraps()
    test_normal_table_untouched()
    test_mismatched_table_columns_renders()
    test_issue_ref_hash_not_split_table_cell()
    test_table_continuation_rows_merge_into_last_col()
    test_two_tables_and_prose_not_swallowed()
    print("ok")
