"""产品身份：软件名 Mini_Lu；用户可自定义 Agent 昵称。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir

# 固定产品名（安装包 / 窗口标题 / 关于信息）
PRODUCT_NAME = "Mini_Lu"
# 默认昵称（未自定义时，对话里也用这个称呼）
DEFAULT_DISPLAY_NAME = "Mini_Lu"

_MAX_NAME_LEN = 24


def data_path() -> Path:
    p = app_dir() / "data" / "identity.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    path = data_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = data_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def product_name() -> str:
    return PRODUCT_NAME


def display_name() -> str:
    """对话与 UI 中对 Agent 的称呼（可自定义）。"""
    raw = str(_load().get("display_name") or "").strip()
    if not raw:
        return DEFAULT_DISPLAY_NAME
    return raw[:_MAX_NAME_LEN]


def assistant_label() -> str:
    """消息头「助手」侧标签。"""
    return display_name()


def set_display_name(name: str | None) -> str:
    """设置昵称；空字符串则恢复默认。返回最终生效的名字。"""
    data = _load()
    cleaned = (name or "").strip()
    if not cleaned:
        data.pop("display_name", None)
        _save(data)
        return DEFAULT_DISPLAY_NAME
    cleaned = cleaned[:_MAX_NAME_LEN]
    data["display_name"] = cleaned
    _save(data)
    return cleaned


def format_identity_block() -> str:
    """每轮注入 system，保证旧版 prompt 也会用对名字。"""
    name = display_name()
    product = product_name()
    return (
        f"【身份】产品名：{product}；你的名字：{name}。"
        f"在对话中用「{name}」自称与被称呼，不要自称「桌宠」或 DesktopPet。"
        f"你是桌面 Agent（办公 / 编程协作），不是宠物玩偶人设。"
    )
