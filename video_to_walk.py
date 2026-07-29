"""
从 raw_video 抽帧 → rembg 抠人物 → 标准化 → 写入 Q版 walk_left / walk_right

使用 rembg_env（需已安装 opencv-python-headless、rembg）。

用法：
  # 向左走
  D:\\Anaconda\\envs\\rembg_env\\python.exe video_to_walk.py ^
    --action walk_left --video raw_video/walk_left_source.mp4 --frames 8 --start 12

  # 向右走（独立视频，不镜像）
  D:\\Anaconda\\envs\\rembg_env\\python.exe video_to_walk.py ^
    --action walk_right --video raw_video/walk_right_source.mp4 --frames 8 --start 10
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import remove

ROOT = Path(__file__).parent
VIDEO_DIR = ROOT / "raw_video"
SKIN_Q = ROOT / "assets" / "skins" / "Q版卡通"
CANVAS = (1024, 1331)
TARGET_HEIGHT = 1100
BOTTOM_MARGIN = 10
ALPHA_T = 10


def find_video(path: Path | None) -> Path:
    if path and path.exists():
        return path
    videos = sorted(VIDEO_DIR.glob("*.mp4")) + sorted(VIDEO_DIR.glob("*.webm")) + sorted(VIDEO_DIR.glob("*.mov"))
    if not videos:
        raise FileNotFoundError(f"未在 {VIDEO_DIR} 找到视频")
    return videos[0]


def content_bbox(img: Image.Image):
    alpha = img.getchannel("A").point(lambda p: 255 if p > ALPHA_T else 0)
    return alpha.getbbox()


def place_on_canvas(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bbox = content_bbox(img)
    if not bbox:
        return Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    person = img.crop(bbox)
    pw, ph = person.size
    scale = TARGET_HEIGHT / max(ph, 1)
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


def extract_indices(total: int, start: int, end: int, count: int) -> list[int]:
    start = max(0, start)
    end = min(total - 1, end if end >= 0 else total - 1)
    if end <= start:
        raise ValueError("end 必须大于 start")
    if count == 1:
        return [start]
    return [int(round(start + i * (end - start) / (count - 1))) for i in range(count)]


def cleanup_shadow(img: Image.Image) -> Image.Image:
    """弱化地面阴影与半透明雾边，保留人物实体。"""
    arr = np.array(img.convert("RGBA"))
    rgb = arr[:, :, :3].astype(np.float32)
    a = arr[:, :, 3].astype(np.float32)
    gray = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    shadow = (a < 180) & (chroma < 25) & (gray > 80) & (gray < 220)
    soft = (a < 40)
    a[shadow | soft] = 0
    arr[:, :, 3] = a.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def process_video(
    video_path: Path,
    action: str = "walk_left",
    frame_count: int = 8,
    start: int = 12,
    end: int = -1,
):
    if action not in ("walk_left", "walk_right"):
        raise ValueError("action 只能是 walk_left 或 walk_right")

    prefix = action
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    print(f"视频: {video_path.name}  frames={total}  fps={fps:.2f}  -> {action}")

    indices = extract_indices(total, start, end, frame_count)
    print(f"抽取帧号: {indices}")

    work = VIDEO_DIR / f"_extracted_{action}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    out_dir = SKIN_Q / action
    out_dir.mkdir(parents=True, exist_ok=True)
    bak = out_dir / "_backup_before_video"
    bak.mkdir(exist_ok=True)
    for f in out_dir.glob("*.png"):
        dest = bak / f.name
        if not dest.exists():
            shutil.copy2(f, dest)
    for f in out_dir.glob("*.png"):
        f.unlink()

    session = None
    try:
        from rembg.session_factory import new_session
        session = new_session("u2net")
    except Exception:
        session = None

    saved = 0
    for i, idx in enumerate(indices, 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            print(f"[跳过] 读帧失败 frame={idx}")
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pil.save(work / f"raw_{i:02d}_f{idx:03d}.png")

        print(f"[{i}/{len(indices)}] rembg frame {idx} ...")
        cut = remove(pil, session=session) if session is not None else remove(pil)
        cut = cleanup_shadow(cut)
        placed = place_on_canvas(cut)
        out_path = out_dir / f"{prefix}_{i:02d}.png"
        placed.save(out_path)
        cut.save(work / f"nobg_{i:02d}.png")
        print(f"  -> {out_path.relative_to(ROOT)}")
        saved += 1

    cap.release()
    print(f"完成：已写入 Q版卡通/{action} 共 {saved} 帧")


def main():
    parser = argparse.ArgumentParser(description="视频抠人 → Q版 walk_left / walk_right")
    parser.add_argument("--video", type=Path, default=None, help="视频路径，默认 raw_video 下第一个")
    parser.add_argument("--action", choices=["walk_left", "walk_right"], default="walk_left")
    parser.add_argument("--frames", type=int, default=8, help="抽取帧数")
    parser.add_argument("--start", type=int, default=4, help="起始帧（跳过片头）")
    parser.add_argument("--end", type=int, default=-1, help="结束帧，-1 表示到末尾")
    args = parser.parse_args()

    video = find_video(args.video)
    process_video(
        video,
        action=args.action,
        frame_count=args.frames,
        start=args.start,
        end=args.end,
    )


if __name__ == "__main__":
    main()
