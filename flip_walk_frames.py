"""
用左走图片水平翻转生成右走图片（仅 Q版卡通）

优先建议用独立右走视频 + video_to_walk.py；
本脚本仅在缺少右走素材时作应急镜像。

用法：
  python flip_walk_frames.py
"""
from pathlib import Path
from PIL import Image

SKIN_DIRS = [
    Path(__file__).parent / "assets" / "skins" / "Q版卡通",
]


def main():
    for skin_dir in SKIN_DIRS:
        left_dir = skin_dir / "walk_left"
        right_dir = skin_dir / "walk_right"

        if not left_dir.exists():
            print(f"跳过（无 walk_left）: {skin_dir.name}")
            continue

        right_dir.mkdir(exist_ok=True)

        for f in right_dir.glob("*.png"):
            f.unlink()

        left_frames = sorted(left_dir.glob("*.png"))
        if not left_frames:
            print(f"跳过（walk_left 为空）: {skin_dir.name}")
            continue

        for i, left_img in enumerate(left_frames, 1):
            img = Image.open(left_img).convert("RGBA")
            flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
            right_path = right_dir / f"walk_right_{i:02d}.png"
            flipped.save(right_path)
            print(f"[OK] {skin_dir.name}: {left_img.name} -> {right_path.name}")

        print(f"  -> {skin_dir.name}: generated {len(left_frames)} walk_right frames")


if __name__ == "__main__":
    main()
