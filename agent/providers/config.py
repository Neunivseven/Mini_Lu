"""
模型配置加载。

优先级：
  models.local.yaml / llm.local.yaml 覆盖 models.yaml / llm.yaml
  环境变量（各 provider 的 api_key_env）优先于文件中的 api_key

兼容：若未写 models.yaml，仍可用旧版扁平 llm.yaml。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.util_merge import deep_merge as _deep_merge


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


_USER_DIR: Path | None = None

# 迁移旧安装目录时视为"用户所有"的配置文件（*.local.yaml 之外）
_USER_OWNED_CONFIGS = (
    "studio_prefs.yaml",
    "ui_theme.yaml",
    "workspace.yaml",
)


def user_dir() -> Path:
    """用户数据根目录。

    源码运行：等同 app_dir()（开发行为不变）。
    打包运行：放系统用户目录，重新打包/覆盖安装不会丢 data 与本地配置。
      Linux: $XDG_DATA_HOME/Mini_Lu（默认 ~/.local/share/Mini_Lu）
      Windows: %APPDATA%/Mini_Lu
      可用环境变量 MINI_LU_HOME 覆盖。
    """
    global _USER_DIR
    if _USER_DIR is not None:
        return _USER_DIR
    if not getattr(sys, "frozen", False):
        _USER_DIR = app_dir()
        return _USER_DIR
    env = os.environ.get("MINI_LU_HOME", "").strip()
    if env:
        base = Path(env).expanduser()
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "").strip() or "~/AppData/Roaming"
        base = Path(appdata).expanduser() / "Mini_Lu"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip() or "~/.local/share"
        base = Path(xdg).expanduser() / "Mini_Lu"
    _migrate_legacy_user_files(base)
    _USER_DIR = base
    return _USER_DIR


def _migrate_legacy_user_files(base: Path) -> None:
    """启动时补齐：把安装目录里的用户数据/本地配置/数据种子搬到用户目录。

    只复制用户目录里"还不存在"的文件：
    - 旧版安装目录里的运行数据（聊天记录、笔记等）→ 一次性迁入
    - 随包发行的数据种子（如 prompts.json）→ 更新版本后也能补齐
    - *.local.yaml 与偏好文件 → 旧安装的 API 配置无缝接续
    """
    import shutil

    try:
        (base / "config").mkdir(parents=True, exist_ok=True)
        (base / "data").mkdir(parents=True, exist_ok=True)
        install = app_dir()
        src_data = install / "data"
        if src_data.is_dir():
            for item in src_data.iterdir():
                dst = base / "data" / item.name
                if dst.exists():
                    continue
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
        src_cfg = install / "config"
        if src_cfg.is_dir():
            for item in src_cfg.iterdir():
                if not item.is_file():
                    continue
                owned = item.name.endswith(".local.yaml") or item.name in _USER_OWNED_CONFIGS
                if not owned:
                    continue
                dst = base / "config" / item.name
                if not dst.exists():
                    shutil.copy2(item, dst)
    except Exception:
        pass


def data_dir() -> Path:
    """用户数据目录（data/）：聊天记录、笔记、提醒、长期记忆等。"""
    p = user_dir() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_config_dir() -> Path:
    """用户配置目录（config/）：*.local.yaml 与偏好文件的写入位置。"""
    p = user_dir() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_read_path(name: str) -> Path:
    """读配置：优先用户目录，回退安装目录（随包模板/默认值）。"""
    u = user_dir() / "config" / name
    if u.is_file():
        return u
    return app_dir() / "config" / name


def config_write_path(name: str) -> Path:
    """写配置：始终写用户目录，避免污染安装目录、更新时丢失。"""
    return user_config_dir() / name


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误（应为映射）: {path}")
    return data


@dataclass
class ProviderSpec:
    """单个 provider 配置快照。"""

    id: str
    type: str  # chat | asr | vision | image
    driver: str
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def capabilities(self) -> set[str]:
        """模态能力：text / image / video（video 预留）。"""
        caps = self.raw.get("capabilities")
        if isinstance(caps, list) and caps:
            return {str(c).strip().lower() for c in caps if str(c).strip()}
        # 默认：chat 仅文本；vision 含 image；asr 含 audio 语义不进此集合
        t = (self.type or "").lower()
        if t == "chat":
            return {"text"}
        if t == "vision":
            return {"image"}
        return set()

    def supports(self, *need: str) -> bool:
        have = self.capabilities()
        return all(str(n).strip().lower() in have for n in need if str(n).strip())

    def resolve_api_key(self) -> str:
        env_name = str(self.raw.get("api_key_env") or "").strip()
        if env_name:
            env_val = os.environ.get(env_name, "").strip()
            if env_val:
                return env_val
        # 亦接受 DEEPSEEK_API_KEY
        if self.id == "deepseek" or self.raw.get("provider") == "deepseek":
            legacy = os.environ.get("DEEPSEEK_API_KEY", "").strip()
            if legacy:
                return legacy
        return str(self.raw.get("api_key") or "").strip()


@dataclass
class ModelsConfig:
    defaults: dict[str, Any]
    active: dict[str, str | None]
    providers: dict[str, ProviderSpec]

    def active_id(self, capability: str) -> str | None:
        v = self.active.get(capability)
        if v is None or v == "" or str(v).lower() in ("null", "none", "false"):
            return None
        return str(v)

    def spec(self, provider_id: str) -> ProviderSpec:
        if provider_id not in self.providers:
            raise KeyError(f"未知 provider: {provider_id}")
        return self.providers[provider_id]


def _legacy_llm_as_models(flat: dict[str, Any]) -> dict[str, Any]:
    """把旧 llm.yaml 扁平结构转成 models 结构。"""
    pid = str(flat.get("provider") or "deepseek")
    return {
        "defaults": {
            "system_prompt": flat.get(
                "system_prompt", "你是桌面宠物办公助手，回答简洁有用。"
            ),
            "timeout_seconds": flat.get("timeout_seconds", 60),
        },
        "active": {"chat": pid, "asr": None, "vision": None, "image": None},
        "providers": {
            pid: {
                "type": "chat",
                "driver": "openai_compat",
                "api_key_env": "DEEPSEEK_API_KEY",
                "api_key": flat.get("api_key", ""),
                "base_url": flat.get("base_url", "https://api.deepseek.com"),
                "model": flat.get("model", "deepseek-v4-flash"),
                "reasoning_effort": flat.get("reasoning_effort"),
                "enable_thinking": flat.get("enable_thinking", False),
                "provider": pid,
            }
        },
    }


def load_models_config(config_dir: Path | None = None) -> ModelsConfig:
    if config_dir is not None:
        _p = lambda name: config_dir / name  # noqa: E731
    else:
        _p = config_read_path

    models = _read_yaml(_p("models.yaml"))
    models_local = _read_yaml(_p("models.local.yaml"))
    llm = _read_yaml(_p("llm.yaml"))
    llm_local = _read_yaml(_p("llm.local.yaml"))

    if not models:
        # 无 models.yaml：用 llm.yaml 构建
        merged_flat = {**llm, **llm_local}
        models = _legacy_llm_as_models(merged_flat)
    else:
        models = _deep_merge(models, models_local)
        # llm.yaml 只回填 llm.provider 对应条目，勿覆盖当前 active.chat
        if llm_local or llm:
            flat = {**llm, **llm_local}
            legacy_pid = str(flat.get("provider") or "deepseek").strip() or "deepseek"
            providers = models.setdefault("providers", {})
            legacy_p = providers.get(legacy_pid)
            if not isinstance(legacy_p, dict):
                legacy_p = {
                    "type": "chat",
                    "driver": "openai_compat",
                    "label": legacy_pid,
                }
                providers[legacy_pid] = legacy_p
            local_legacy = (models_local.get("providers") or {}).get(legacy_pid)
            if not isinstance(local_legacy, dict):
                local_legacy = {}
            for key in (
                "api_key",
                "base_url",
                "model",
                "reasoning_effort",
                "enable_thinking",
                "timeout_seconds",
            ):
                if key not in flat or flat[key] in (None, ""):
                    continue
                # models.local 已有字段优先
                if key in local_legacy and local_legacy[key] not in (None, ""):
                    continue
                # llm.local 可覆盖；llm.yaml 只填空缺
                if key in llm_local and llm_local.get(key) not in (None, ""):
                    legacy_p[key] = flat[key]
                elif key not in legacy_p or legacy_p.get(key) in (None, ""):
                    legacy_p[key] = flat[key]
            if flat.get("system_prompt"):
                models.setdefault("defaults", {})["system_prompt"] = flat["system_prompt"]
            if flat.get("timeout_seconds") is not None:
                models.setdefault("defaults", {})["timeout_seconds"] = flat[
                    "timeout_seconds"
                ]

    defaults = models.get("defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    active_raw = models.get("active") or {}
    if not isinstance(active_raw, dict):
        active_raw = {}
    active: dict[str, str | None] = {
        "chat": active_raw.get("chat"),
        "asr": active_raw.get("asr"),
        "vision": active_raw.get("vision"),
        "image": active_raw.get("image"),
    }

    providers_raw = models.get("providers") or {}
    providers: dict[str, ProviderSpec] = {}
    for pid, raw in providers_raw.items():
        if not isinstance(raw, dict):
            continue
        ptype = str(raw.get("type") or "chat")
        driver = str(raw.get("driver") or "openai_compat")
        # 继承 defaults 超时
        if "timeout_seconds" not in raw and "timeout_seconds" in defaults:
            raw = {**raw, "timeout_seconds": defaults["timeout_seconds"]}
        providers[str(pid)] = ProviderSpec(
            id=str(pid), type=ptype, driver=driver, raw=dict(raw)
        )

    return ModelsConfig(defaults=defaults, active=active, providers=providers)
