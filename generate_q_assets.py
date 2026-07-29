"""
Q版素材在线 AI 作图流水线（仅维护 Q版卡通）

流程：在线文生图 → rembg 抠图 → 统一尺寸 → 写入 assets/skins/Q版卡通/

支持的在线服务（通过环境变量 IMAGE_API_PROVIDER 选择）：
  - openai      : OpenAI Images API (gpt-image-1 / dall-e-3)
  - siliconflow : 硅基流动 OpenAI 兼容接口（推荐国内）
  - dashscope   : 通义万相

配置：
  复制 .env.example 为 .env，填入 API Key。
  必须使用 rembg_env 运行抠图步骤，或本脚本自动调用该环境的 python。

用法：
  # 只生成行走 4 帧
  python generate_q_assets.py --action walk_left

  # 生成全部动作
  python generate_q_assets.py --action all

  # 已有原图，只抠图入库
  python generate_q_assets.py --rembg-only --input-dir raw_q_gen
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).parent
CHAR_FILE = ROOT / "character_q.yaml"
OUT_SKIN = ROOT / "assets" / "skins" / "Q版卡通"
RAW_DIR = ROOT / "raw_q_gen"
REMBG_PYTHON = Path(r"D:\Anaconda\envs\rembg_env\python.exe")
CANVAS = (1024, 1331)
TARGET_HEIGHT = 1100
BOTTOM_MARGIN = 10

# 可选：从 .env 读取
def load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_character() -> dict:
    with open(CHAR_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(char: dict, pose: str) -> str:
    h = char["hair"]
    o = char["outfit"]
    f = char["face"]
    parts = [
        f"{char['style']} full-body character sprite",
        char["proportions"],
        f"hair: {h['style']}, {h['locked_phrase']}, top {h['top_color']}, sides {h['side_color']}",
        f"face: {f['eyes']}, {f['blush']}, {f['mouth']}",
        f"wearing {o['shirt']}, {o['pants']}, {o['shoes']}",
        pose,
        char["generation"]["background"],
        char["generation"]["camera"],
        "identical character design every frame, locked appearance",
    ]
    return ", ".join(parts)


def build_negative(char: dict) -> str:
    return char["generation"]["negative"]


# ---------- 在线 API ----------

def _http_json(url: str, headers: dict, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def generate_openai_compatible(prompt: str, negative: str, api_key: str, base_url: str, model: str) -> Image.Image:
    """OpenAI Images 兼容接口（OpenAI / 硅基流动等）"""
    url = base_url.rstrip("/") + "/images/generations"
    payload = {
        "model": model,
        "prompt": prompt + f"\n\nAvoid: {negative}",
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    result = _http_json(url, headers, payload)
    b64 = result["data"][0]["b64_json"]
    return Image.open(BytesIO(base64.b64decode(b64))).convert("RGBA")


def generate_dashscope(prompt: str, negative: str, api_key: str, model: str) -> Image.Image:
    """通义万相"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": model,
        "input": {"prompt": prompt, "negative_prompt": negative},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    # 简化：若账号支持同步接口可改；此处用 generation 同步兼容端点
    sync_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers_sync = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload_sync = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": {"negative_prompt": negative},
    }
    result = _http_json(sync_url, headers_sync, payload_sync)
    # 解析常见返回结构
    try:
        content = result["output"]["choices"][0]["message"]["content"]
        if isinstance(content, list):
            for item in content:
                if "image" in item:
                    img_url = item["image"]
                    with urllib.request.urlopen(img_url, timeout=120) as r:
                        return Image.open(BytesIO(r.read())).convert("RGBA")
                if "image_url" in item:
                    img_url = item["image_url"]
                    with urllib.request.urlopen(img_url, timeout=120) as r:
                        return Image.open(BytesIO(r.read())).convert("RGBA")
    except Exception as e:
        raise RuntimeError(f"DashScope 解析失败: {result}") from e
    raise RuntimeError(f"DashScope 未返回图片: {result}")


def generate_image(prompt: str, negative: str) -> Image.Image:
    provider = os.environ.get("IMAGE_API_PROVIDER", "siliconflow").lower()
    api_key = os.environ.get("IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未设置 IMAGE_API_KEY。请复制 .env.example 为 .env 并填入密钥。\n"
            "可选 IMAGE_API_PROVIDER=siliconflow|openai|dashscope"
        )

    if provider == "openai":
        base = os.environ.get("IMAGE_API_BASE", "https://api.openai.com/v1")
        model = os.environ.get("IMAGE_API_MODEL", "dall-e-3")
        return generate_openai_compatible(prompt, negative, api_key, base, model)

    if provider == "siliconflow":
        base = os.environ.get("IMAGE_API_BASE", "https://api.siliconflow.cn/v1")
        model = os.environ.get("IMAGE_API_MODEL", "black-forest-labs/FLUX.1-schnell")
        return generate_openai_compatible(prompt, negative, api_key, base, model)

    if provider == "dashscope":
        model = os.environ.get("IMAGE_API_MODEL", "qwen-image-plus")
        return generate_dashscope(prompt, negative, api_key, model)

    raise RuntimeError(f"未知 IMAGE_API_PROVIDER: {provider}")


# ---------- rembg / 标准化 ----------

def rembg_remove(img: Image.Image) -> Image.Image:
    """优先调用 rembg_env 中的 rembg，保证与你原先环境一致。"""
    RAW_DIR.mkdir(exist_ok=True)
    tmp_in = RAW_DIR / "_tmp_in.png"
    tmp_out = RAW_DIR / "_tmp_out.png"
    img.convert("RGBA").save(tmp_in)

    if REMBG_PYTHON.exists():
        code = (
            "from rembg import remove; from PIL import Image; "
            f"im=Image.open(r'{tmp_in}'); "
            f"remove(im).save(r'{tmp_out}')"
        )
        subprocess.run([str(REMBG_PYTHON), "-c", code], check=True)
        return Image.open(tmp_out).convert("RGBA")

    # 回退：当前环境若已装 rembg
    from rembg import remove
    return remove(img.convert("RGBA"))


def content_bbox(img: Image.Image):
    alpha = img.getchannel("A").point(lambda p: 255 if p > 10 else 0)
    return alpha.getbbox()


def place_on_canvas(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bbox = content_bbox(img)
    if not bbox:
        return Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    person = img.crop(bbox)
    pw, ph = person.size
    scale = TARGET_HEIGHT / ph
    nw, nh = max(1, int(round(pw * scale))), TARGET_HEIGHT
    max_w, max_h = CANVAS[0] - 4, CANVAS[1] - BOTTOM_MARGIN - 4
    if nw > max_w or nh > max_h:
        fit = min(max_w / nw, max_h / nh)
        nw, nh = max(1, int(round(nw * fit))), max(1, int(round(nh * fit)))
    person = person.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    x = (CANVAS[0] - nw) // 2
    y = CANVAS[1] - nh - BOTTOM_MARGIN
    canvas.paste(person, (x, y), person)
    return canvas


def save_action_frame(action: str, index: int, img: Image.Image, prefix: str | None = None):
    action_dir = OUT_SKIN / action
    action_dir.mkdir(parents=True, exist_ok=True)
    name = f"{prefix or action}_{index:02d}.png"
    path = action_dir / name
    place_on_canvas(img).save(path)
    print(f"[OK] {path.relative_to(ROOT)}")


def process_and_save(action: str, index: int, img: Image.Image, prefix: str | None = None):
    cut = rembg_remove(img)
    save_action_frame(action, index, cut, prefix=prefix)


def gen_walk_left(char: dict):
    negative = build_negative(char)
    frames = char["walk_cycle_left"]
    RAW_DIR.mkdir(exist_ok=True)
    for i, frame in enumerate(frames, 1):
        prompt = build_prompt(char, frame["pose"])
        print(f"\n生成 {frame['id']} ({frame['phase']}) ...")
        print(f"  prompt预览: {prompt[:160]}...")
        img = generate_image(prompt, negative)
        raw_path = RAW_DIR / f"{frame['id']}_raw.png"
        img.save(raw_path)
        process_and_save("walk_left", i, img, prefix="walk_left")

    # 右走 = 左走朝向镜像（不改姿态，只改方向）
    right_dir = OUT_SKIN / "walk_right"
    right_dir.mkdir(parents=True, exist_ok=True)
    for f in right_dir.glob("*.png"):
        f.unlink()
    for i, left in enumerate(sorted((OUT_SKIN / "walk_left").glob("walk_left_*.png")), 1):
        img = Image.open(left).convert("RGBA")
        img.transpose(Image.FLIP_LEFT_RIGHT).save(right_dir / f"walk_right_{i:02d}.png")
        print(f"[OK] walk_right/walk_right_{i:02d}.png (镜像)")


def gen_simple(char: dict, action: str, poses: list[str]):
    negative = build_negative(char)
    for i, pose in enumerate(poses, 1):
        prompt = build_prompt(char, pose)
        print(f"\n生成 {action}_{i:02d} ...")
        img = generate_image(prompt, negative)
        (RAW_DIR).mkdir(exist_ok=True)
        img.save(RAW_DIR / f"{action}_{i:02d}_raw.png")
        process_and_save(action, i, img, prefix=action)


def rembg_only(input_dir: Path):
    files = sorted(input_dir.glob("*.png")) + sorted(input_dir.glob("*.jpg"))
    for f in files:
        print(f"抠图 {f.name}")
        img = Image.open(f).convert("RGBA")
        cut = rembg_remove(img)
        # 文件名约定: walk_left_01.png / idle_01.png
        stem = f.stem.replace("_raw", "").replace("_nobg", "")
        if stem.startswith("walk_left"):
            idx = int(stem.split("_")[-1])
            save_action_frame("walk_left", idx, cut, prefix="walk_left")
        elif stem.startswith("idle"):
            idx = int(stem.split("_")[-1])
            save_action_frame("idle", idx, cut, prefix="idle")
        elif stem.startswith("happy"):
            idx = int(stem.split("_")[-1])
            save_action_frame("happy", idx, cut, prefix="happy")
        else:
            out = RAW_DIR / f"{stem}_nobg.png"
            place_on_canvas(cut).save(out)
            print(f"  -> {out}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Q版在线作图流水线")
    parser.add_argument("--action", default="walk_left",
                        choices=["walk_left", "idle", "happy", "all", "none"])
    parser.add_argument("--rembg-only", action="store_true")
    parser.add_argument("--input-dir", type=Path, default=RAW_DIR)
    args = parser.parse_args()

    char = load_character()

    if args.rembg_only:
        rembg_only(args.input_dir)
        return

    if args.action in ("walk_left", "all"):
        gen_walk_left(char)
    if args.action in ("idle", "all"):
        gen_simple(char, "idle", char["actions"]["idle"] * 4)
    if args.action in ("happy", "all"):
        gen_simple(char, "happy", char["actions"]["happy"] * 2)

    print("\n完成。仅写入 Q版卡通 皮肤目录。")


if __name__ == "__main__":
    main()
