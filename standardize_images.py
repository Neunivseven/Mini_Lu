"""
标准化各皮肤素材的人物大小
统一所有帧中人物的高度，保持底部对齐、水平居中

用法：
  python standardize_images.py
"""
from pathlib import Path
from PIL import Image

SKINS_DIR = Path(__file__).parent / "assets" / "skins"
# 画布高度约 1331，留出上下边距；略小于多数帧内容高度，避免贴边裁切
TARGET_HEIGHT = 1100
BOTTOM_MARGIN = 10
ALPHA_THRESHOLD = 10


def get_content_bbox(img: Image.Image) -> tuple | None:
    """获取图片中非透明区域的边界框"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha = img.getchannel("A")
    # 忽略极低透明度噪点，避免 bbox 被虚边拉大
    mask = alpha.point(lambda p: 255 if p > ALPHA_THRESHOLD else 0)
    return mask.getbbox()


def standardize_image(img_path: Path, target_height: int, canvas_size: tuple[int, int] | None = None):
    """标准化单张图片：统一人物高度，底部对齐"""
    img = Image.open(img_path).convert("RGBA")
    bbox = get_content_bbox(img)

    if not bbox:
        return False

    left, top, right, bottom = bbox
    person_height = bottom - top
    person_width = right - left

    if person_height <= 0 or person_width <= 0:
        return False

    scale = target_height / person_height
    new_width = max(1, int(round(person_width * scale)))
    new_height = target_height

    person = img.crop(bbox)
    person_resized = person.resize((new_width, new_height), Image.LANCZOS)

    width, height = canvas_size or img.size
    # 若缩放后超出画布，按比例再缩小以完整放入
    max_w = width - 4
    max_h = height - BOTTOM_MARGIN - 4
    if new_width > max_w or new_height > max_h:
        fit = min(max_w / new_width, max_h / new_height)
        new_width = max(1, int(round(new_width * fit)))
        new_height = max(1, int(round(new_height * fit)))
        person_resized = person.resize((new_width, new_height), Image.LANCZOS)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = (width - new_width) // 2
    y = height - new_height - BOTTOM_MARGIN
    canvas.paste(person_resized, (x, y), person_resized)

    canvas.save(img_path)
    print(f"[OK] {img_path.parent.parent.name}/{img_path.parent.name}/{img_path.name} "
          f"({person_height}px -> {new_height}px)")
    return True


def collect_pngs(skin_dir: Path) -> list[Path]:
    files = []
    for action_dir in sorted(skin_dir.iterdir()):
        if action_dir.is_dir():
            files.extend(sorted(action_dir.glob("*.png")))
    return files


def main():
    if not SKINS_DIR.exists():
        print("未找到 skins 目录")
        return

    count = 0
    for skin_dir in sorted(SKINS_DIR.iterdir()):
        if not skin_dir.is_dir():
            continue
        png_files = collect_pngs(skin_dir)
        if not png_files:
            continue

        # 以该皮肤第一张图的画布尺寸为准，保持一致
        sample = Image.open(png_files[0])
        canvas_size = sample.size
        sample.close()

        print(f"\n== {skin_dir.name} (canvas={canvas_size}, target_h={TARGET_HEIGHT}) ==")
        for f in png_files:
            if standardize_image(f, TARGET_HEIGHT, canvas_size):
                count += 1

    print(f"\n完成！共标准化 {count} 张图片")


if __name__ == "__main__":
    main()
