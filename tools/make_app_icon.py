"""生成 Windows 可用的多尺寸 PNG-in-ICO（避免只剩糊的 16x16）。"""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"
SRC = OUT / "app_icon_source.png"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _near_white(r: int, g: int, b: int, a: int, thresh: int = 248) -> bool:
    return a > 0 and r >= thresh and g >= thresh and b >= thresh


def flood_clear_bg(im: Image.Image, thresh: int = 248) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    visited = [[False] * w for _ in range(h)]
    stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    stack = [xy for xy in stack if _near_white(*px[xy], thresh=thresh)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h or visited[y][x]:
            continue
        r, g, b, a = px[x, y]
        if not _near_white(r, g, b, a, thresh=thresh):
            continue
        visited[y][x] = True
        px[x, y] = (r, g, b, 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def scrub_watermark(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    x0, y0 = int(w * 0.72), int(h * 0.90)
    for y in range(y0, h):
        for x in range(x0, w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if min(r, g, b) >= 200 and max(r, g, b) - min(r, g, b) < 40:
                px[x, y] = (r, g, b, 0)
    return im


def to_square(im: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    bbox = im.split()[-1].getbbox()
    if bbox:
        im = im.crop(bbox)
    pad = max(8, int(max(im.size) * pad_ratio))
    side = max(im.size) + pad * 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2), im)
    return canvas


def write_png_ico(master: Image.Image, path: Path, sizes=SIZES) -> None:
    """每个尺寸嵌入 PNG（Vista+），资源管理器会选合适档，不会死盯 16x16。"""
    blobs: list[tuple[int, bytes]] = []
    for s in sizes:
        im = master.resize((s, s), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        blobs.append((s, buf.getvalue()))

    count = len(blobs)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = bytearray()
    data = bytearray()
    for s, png in blobs:
        # 宽高：256 记为 0
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries += struct.pack(
            "<BBBBHHII",
            w,
            h,
            0,  # color count
            0,  # reserved
            1,  # planes
            32,  # bit count
            len(png),
            offset,
        )
        data += png
        offset += len(png)

    path.write_bytes(header + entries + data)


def inspect_ico(path: Path) -> None:
    raw = path.read_bytes()
    _r, typ, count = struct.unpack_from("<HHH", raw, 0)
    print(f"{path.name}: type={typ} count={count} bytes={len(raw)}")
    off = 6
    for i in range(count):
        w, h, _c, _res, planes, bpp, nbytes, img_off = struct.unpack_from(
            "<BBBBHHII", raw, off
        )
        ww, hh = (w or 256), (h or 256)
        kind = "PNG" if raw[img_off : img_off + 8] == b"\x89PNG\r\n\x1a\n" else "BMP"
        print(f"  #{i}: {ww}x{hh} {nbytes}B {kind}")
        off += 16


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"missing {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)

    raw = Image.open(SRC).convert("RGBA")
    im = to_square(scrub_watermark(flood_clear_bg(raw)))
    master = im.resize((1024, 1024), Image.Resampling.LANCZOS)

    master.save(OUT / "app_icon.png", optimize=True)
    master.resize((512, 512), Image.Resampling.LANCZOS).save(
        OUT / "app_icon_512.png", optimize=True
    )
    master.resize((256, 256), Image.Resampling.LANCZOS).save(
        OUT / "app_icon_256.png", optimize=True
    )

    ico_path = OUT / "app_icon.ico"
    write_png_ico(master, ico_path)
    inspect_ico(ico_path)

    # 同步到已打包目录（若存在）
    dist_icons = ROOT / "dist" / "Mini_Lu" / "assets" / "icons"
    if dist_icons.is_dir():
        for name in (
            "app_icon.png",
            "app_icon_512.png",
            "app_icon_256.png",
            "app_icon.ico",
        ):
            src = OUT / name
            if src.is_file():
                (dist_icons / name).write_bytes(src.read_bytes())
        print("synced assets to dist/Mini_Lu/assets/icons")


if __name__ == "__main__":
    main()
