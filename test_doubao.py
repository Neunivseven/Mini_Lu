"""
火山方舟 Doubao Seed 连通测试。

用法：
  1) 在下方 ARK_API_KEY 填入新密钥（或设置环境变量 ARK_API_KEY）
  2) python test_doubao.py
  3) python test_doubao.py --asr data/test_audio/hello_zh.wav
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

# ========== API Key：勿把真实密钥写进本文件 ==========
# 优先环境变量 ARK_API_KEY，其次 config/models.local.yaml
ARK_API_KEY = ""
# ====================================================

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# 支持音频输入的模型（2.1-pro 可能不支持音频）
MODEL = "doubao-seed-2-0-lite-260428"

DEMO_IMAGE = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/ark_demo_img_1.png"
)


def resolve_api_key() -> str:
    key = (ARK_API_KEY or "").strip() or os.getenv("ARK_API_KEY", "").strip()
    if key:
        return key
    # 兼容项目配置
    try:
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from agent.providers.config import load_models_config

        cfg = load_models_config()
        for pid in ("doubao_asr", "doubao_vision"):
            if pid in cfg.providers:
                k = cfg.providers[pid].resolve_api_key()
                if k:
                    return k
    except Exception:
        pass
    return ""


def extract_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    chunks: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            t = getattr(part, "text", None)
            if t:
                chunks.append(str(t))
    return "\n".join(chunks).strip() or str(resp)


def run_vision(client, model: str) -> None:
    print(f"[vision] model={model}")
    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": DEMO_IMAGE},
                    {"type": "input_text", "text": "你看见了什么？"},
                ],
            }
        ],
    )
    print("[vision] OK")
    print(extract_text(resp))


def run_asr(client, model: str, audio: Path) -> None:
    if not audio.is_file():
        raise SystemExit(f"音频不存在: {audio}")
    raw = audio.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    suffix = audio.suffix.lower().lstrip(".") or "wav"
    mime = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
    }.get(suffix, "audio/wav")
    data_url = f"data:{mime};base64,{b64}"
    print(f"[asr] model={model} file={audio}")
    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "audio_url": data_url},
                    {
                        "type": "input_text",
                        "text": "请把这段音频准确转写成文字。只输出识别文本，不要解释。",
                    },
                ],
            }
        ],
    )
    print("[asr] OK")
    print(extract_text(resp))


def main() -> int:
    parser = argparse.ArgumentParser(description="Doubao 方舟连通测试")
    parser.add_argument("--asr", metavar="AUDIO", help="本地音频转写")
    parser.add_argument("--model", default=MODEL, help=f"默认 {MODEL}")
    args = parser.parse_args()

    api_key = resolve_api_key()
    if not api_key:
        print(
            "未配置 API Key。请打开 test_doubao.py，在顶部 ARK_API_KEY = \"...\" 填入，"
            "或设置环境变量 ARK_API_KEY。",
            file=sys.stderr,
        )
        return 2

    from openai import OpenAI

    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    try:
        if args.asr:
            run_asr(client, args.model, Path(args.asr))
        else:
            run_vision(client, args.model)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
