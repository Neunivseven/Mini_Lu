"""
批量重新抠图：给 skins 下素材重新 rembg（仅建议在源图有问题时空跑）

注意：可能再次把手部抠坏；之后请运行 fix_q_appearance.py。

用法：
  D:\\Anaconda\\envs\\rembg_env\\python.exe batch_fix_bg.py
"""
from pathlib import Path
from rembg import remove
from PIL import Image

ASSETS_DIR = Path(__file__).parent / "assets" / "skins"

def process_image(input_path: Path, output_path: Path):
    """处理单张图片"""
    try:
        with open(input_path, "rb") as f:
            input_data = f.read()
        output_data = remove(input_data)
        with open(output_path, "wb") as f:
            f.write(output_data)
        print(f"✓ {input_path.name}")
    except Exception as e:
        print(f"✗ {input_path.name}: {e}")

def main():
    count = 0
    # 遍历所有皮肤目录
    for skin_dir in ASSETS_DIR.iterdir():
        if not skin_dir.is_dir():
            continue
        print(f"\n=== 处理皮肤: {skin_dir.name} ===")
        
        # 遍历每个动作目录
        for action_dir in skin_dir.iterdir():
            if not action_dir.is_dir():
                continue
            
            # 处理每个png文件
            png_files = list(action_dir.glob("*.png"))
            for png_file in png_files:
                # 临时文件，处理完覆盖
                temp_file = png_file.with_suffix(".tmp.png")
                process_image(png_file, temp_file)
                
                # 覆盖原文件
                if temp_file.exists():
                    temp_file.replace(png_file)
                    count += 1
    
    print(f"\n完成！共处理 {count} 张图片")

if __name__ == "__main__":
    main()
