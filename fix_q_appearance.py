"""
修复 Q版素材：
1. 填补手部被误抠透明的空洞（轮廓洞 + 手部条带闭运算）
2. 统一肤色到行走帧暖肤色 TARGET_SKIN

用法：
  D:\\Anaconda\\envs\\rembg_env\\python.exe fix_q_appearance.py --restore
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
SKIN_Q = ROOT / "assets" / "skins" / "Q版卡通"
BACKUP = ROOT / "assets" / "_backups" / "Q版卡通_before_skin_fix"

TARGET_SKIN = np.array([222, 181, 158], dtype=np.float32)
ALPHA_T = 10


def is_skin(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return (
        (alpha > ALPHA_T)
        & (r > 150)
        & (g > 110)
        & (b > 90)
        & (r >= g - 5)
        & (r > b)
        & ((r - b) > 12)
        & ((r - g) < 80)
    )


def character_bbox(opaque: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(opaque)
    return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())


def detect_contour_holes(arr: np.ndarray) -> np.ndarray:
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    opaque = alpha > ALPHA_T
    # 实心黑线才算描边；半透明脏色（rembg 残渣）不算
    outline = (alpha >= 200) & (rgb.mean(axis=2) < 40)
    y_top, y_bot, x_left, x_right = character_bbox(opaque)
    char_h = max(y_bot - y_top, 1)
    char_w = max(x_right - x_left, 1)
    mid_x = (x_left + x_right) / 2

    outline_d = cv2.dilate(
        (outline.astype(np.uint8) * 255),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        1,
    )
    holes = np.zeros(alpha.shape, dtype=bool)

    for k in (7, 9, 11, 15, 21):
        sealed = cv2.morphologyEx(
            outline_d,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
        )
        cnts, hier = cv2.findContours(sealed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hier is None:
            continue
        hier = hier[0]
        for i, c in enumerate(cnts):
            if hier[i][3] < 0:
                continue
            area = cv2.contourArea(c)
            if area < 100 or area > 6000:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            cx, cy = x + bw / 2, y + bh / 2
            if cy > y_top + char_h * 0.90:
                continue
            rel_y = (cy - y_top) / char_h
            rel_x = abs(cx - mid_x) / (char_w / 2)
            side_hand = (0.55 <= rel_y <= 0.88) and (rel_x >= 0.35)
            raised_hand = (0.05 <= rel_y <= 0.45) and (rel_x >= 0.40)
            if not (side_hand or raised_hand):
                continue
            mask = np.zeros(alpha.shape, np.uint8)
            cv2.drawContours(mask, [c], -1, 255, -1)
            # 洞内：全透明，或半透明脏色
            region = (mask > 0) & ((alpha < 200) | ((alpha > ALPHA_T) & (rgb.mean(axis=2) < 80) & (alpha < 250)))
            # 排除已是正常肤色
            region &= ~is_skin(arr[:, :, :3], alpha)
            if region.sum() >= 40:
                holes |= region
    return holes


def detect_band_holes(arr: np.ndarray, *, raised: bool = False) -> np.ndarray:
    """在侧手高度条带内做 alpha 闭运算；happy 才启用举手条带。"""
    alpha = arr[:, :, 3]
    opaque_u8 = (alpha > ALPHA_T).astype(np.uint8) * 255
    y_top, y_bot, x_left, x_right = character_bbox(alpha > ALPHA_T)
    char_h = max(y_bot - y_top, 1)
    char_w = max(x_right - x_left, 1)
    mid_x = (x_left + x_right) // 2

    holes = np.zeros(alpha.shape, dtype=bool)
    bands = [(int(y_top + 0.66 * char_h), int(y_top + 0.86 * char_h))]
    if raised:
        bands.append((int(y_top + 0.08 * char_h), int(y_top + 0.42 * char_h)))
    half_gap = int(char_w * 0.12)

    for band0, band1 in bands:
        band0 = max(0, band0)
        band1 = min(alpha.shape[0], band1)
        if band1 <= band0:
            continue
        for x0, x1 in (
            (0, max(0, mid_x - half_gap)),
            (min(alpha.shape[1], mid_x + half_gap), alpha.shape[1]),
        ):
            if x1 <= x0:
                continue
            roi = opaque_u8[band0:band1, x0:x1]
            closed = cv2.morphologyEx(
                roi,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)),
            )
            holes[band0:band1, x0:x1] |= (closed > 0) & (roi == 0)

    return holes


def detect_hull_hand_holes(arr: np.ndarray, *, raised: bool = False) -> np.ndarray:
    """
    对左右侧手（及可选举手）区域：取描边点做凸包，
    把包内「半透明 / 非肤色非描边非衣服」像素一律视为需修复。
    rembg 常把手心抠成 alpha 很低的脏色，看起来像透明黑洞。
    """
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    opaque = alpha > ALPHA_T
    # 实心黑线才算描边；半透明脏色（rembg 残渣）不算
    outline = (alpha >= 200) & (rgb.mean(axis=2) < 40)
    # 衬衫蓝白条（避免袖口被涂成肤色）
    shirt = opaque & (
        ((b > r + 5) & (b > 130) & (g > 120))
        | ((r > 200) & (g > 200) & (b > 200) & (np.abs(r - b) < 25))  # 白条
    )
    pants = (alpha >= 200) & (rgb.mean(axis=2) < 90) & (rgb.mean(axis=2) > 40) & ~outline

    y_top, y_bot, x_left, x_right = character_bbox(opaque)
    char_h = max(y_bot - y_top, 1)
    char_w = max(x_right - x_left, 1)
    mid_x = (x_left + x_right) // 2
    half_gap = int(char_w * 0.15)
    holes = np.zeros(alpha.shape, dtype=bool)

    bands = [(int(y_top + 0.68 * char_h), int(y_top + 0.85 * char_h))]
    if raised:
        bands.append((int(y_top + 0.05 * char_h), int(y_top + 0.40 * char_h)))

    skin_ok = is_skin(arr[:, :, :3], alpha) & (alpha >= 240)

    for band0, band1 in bands:
        band0 = max(0, band0)
        band1 = min(alpha.shape[0], band1)
        if band1 - band0 < 10:
            continue
        for x0, x1 in (
            (x_left, max(x_left + 1, mid_x - half_gap)),
            (min(x_right, mid_x + half_gap), x_right + 1),
        ):
            if x1 - x0 < 10:
                continue
            roi_outline = outline[band0:band1, x0:x1]
            ys, xs = np.where(roi_outline)
            if len(ys) < 20:
                continue
            pts = np.stack([xs, ys], axis=1).astype(np.float32)
            hull = cv2.convexHull(pts)
            mask = np.zeros((band1 - band0, x1 - x0), np.uint8)
            cv2.fillConvexPoly(mask, hull.astype(np.int32), 255)
            inside = mask > 0

            # 距离描边太远的外部背景不要
            dist = cv2.distanceTransform(
                (~roi_outline).astype(np.uint8) * 255, cv2.DIST_L2, 3
            )
            inside &= dist < 36

            sub_a = alpha[band0:band1, x0:x1]
            sub_skin = skin_ok[band0:band1, x0:x1]
            sub_out = outline[band0:band1, x0:x1]
            sub_shirt = shirt[band0:band1, x0:x1]
            sub_pants = pants[band0:band1, x0:x1]

            is_raised_band = band0 < y_top + 0.5 * char_h
            if is_raised_band:
                # 举手区域凸包易包进头发：只修半透明/残缺像素
                bad = inside & ~sub_out & ~sub_shirt & (sub_a < 220)
            else:
                bad = inside & ~sub_out & ~sub_shirt & ~sub_pants & (
                    (sub_a < 240) | (~sub_skin)
                )
            holes[band0:band1, x0:x1] |= bad

    return holes


def fill_holes(arr: np.ndarray, holes: np.ndarray, color: np.ndarray) -> np.ndarray:
    if not holes.any():
        return arr
    out = arr.copy()
    c = np.clip(color, 0, 255).astype(np.uint8)
    out[holes, :3] = c
    out[holes, 3] = 255
    return out


def unify_skin(arr: np.ndarray, target: np.ndarray) -> np.ndarray:
    """把肤色均值对齐到目标，保留轻微明暗差。"""
    rgb = arr[:, :, :3].astype(np.float32)
    alpha = arr[:, :, 3]
    skin = is_skin(arr[:, :, :3], alpha)
    if skin.sum() < 50:
        return arr

    current = rgb[skin].mean(axis=0)
    centered = rgb[skin] - current
    new_vals = np.clip(target + centered * 0.35, 0, 255)

    out = arr.copy()
    new_rgb = rgb.copy()
    new_rgb[skin] = new_vals
    out[:, :, :3] = new_rgb.astype(np.uint8)
    return out


def process_file(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    bak = backup_dir / path.name
    if not bak.exists():
        shutil.copy2(path, bak)

    arr = np.array(Image.open(bak).convert("RGBA"))
    # 行走帧手部完整；happy 拳头在源图已是实心，只统一肤色
    # idle 侧手存在半透明残渣，需要填洞
    do_holes = path.parent.name.startswith("idle")
    all_holes = np.zeros(arr.shape[:2], dtype=bool)
    filled = arr
    if do_holes:
        holes = (
            detect_contour_holes(arr)
            | detect_band_holes(arr, raised=False)
            | detect_hull_hand_holes(arr, raised=False)
        )
        filled = fill_holes(arr, holes, TARGET_SKIN)
        holes2 = (
            detect_contour_holes(filled)
            | detect_band_holes(filled, raised=False)
            | detect_hull_hand_holes(filled, raised=False)
        )
        filled = fill_holes(filled, holes2, TARGET_SKIN)
        all_holes = holes | holes2

    unified = unify_skin(filled, TARGET_SKIN)
    if all_holes.any():
        unified[all_holes, :3] = TARGET_SKIN.astype(np.uint8)
        unified[all_holes, 3] = 255

    Image.fromarray(unified, "RGBA").save(path)
    skin2 = is_skin(unified[:, :, :3], unified[:, :, 3])
    mean = unified[:, :, :3][skin2].mean(axis=0) if skin2.any() else (0, 0, 0)
    remain = 0
    if do_holes:
        remain = int(
            (
                detect_contour_holes(unified)
                | detect_band_holes(unified, raised=False)
                | detect_hull_hand_holes(unified, raised=False)
            ).sum()
        )
    print(
        f"[OK] {path.parent.name}/{path.name}  "
        f"filled={int(all_holes.sum())}  "
        f"skin_mean=RGB{tuple(int(x) for x in mean)}  "
        f"remain={remain}"
    )


def restore_from_backup() -> None:
    if not BACKUP.exists():
        print("无备份，跳过还原")
        return
    for action_dir in BACKUP.iterdir():
        if not action_dir.is_dir():
            continue
        dest = SKIN_Q / action_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in action_dir.glob("*.png"):
            shutil.copy2(f, dest / f.name)
            print(f"[RESTORE] {action_dir.name}/{f.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    if args.restore:
        restore_from_backup()

    count = 0
    for action_dir in sorted(SKIN_Q.iterdir()):
        if not action_dir.is_dir() or action_dir.name.startswith("_"):
            continue
        for f in sorted(action_dir.glob("*.png")):
            process_file(f, BACKUP / action_dir.name)
            count += 1
    print(f"\n完成，共处理 {count} 张。备份在 {BACKUP}")


if __name__ == "__main__":
    main()
