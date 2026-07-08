"""
生成模拟草莓温室 ROS 地图 (PGM + YAML)
包含：温室外墙、种植畦道、通道、设备区、入口
"""
import numpy as np
from PIL import Image
import os

# 地图参数
WIDTH = 800       # 像素 (40m / 0.05)
HEIGHT = 600      # 像素 (30m / 0.05)
RESOLUTION = 0.05 # 米/像素
ORIGIN_X = -5.0
ORIGIN_Y = -5.0

# 颜色: 254=空闲(白), 0=障碍(黑), 205=未知(灰)
FREE = 254
WALL = 0
UNKNOWN = 205

def draw_rect(grid, x1, y1, x2, y2, val):
    grid[y1:y2, x1:x2] = val

def draw_hline(grid, x1, x2, y, thickness=2, val=WALL):
    grid[y:y+thickness, x1:x2] = val

def draw_vline(grid, x, y1, y2, thickness=2, val=WALL):
    grid[y1:y2, x:x+thickness] = val

def main():
    grid = np.full((HEIGHT, WIDTH), UNKNOWN, dtype=np.uint8)

    # 温室内部空间 (留边距)
    margin = 30
    draw_rect(grid, margin, margin, WIDTH-margin, HEIGHT-margin, FREE)

    # ── 外墙 (厚度4px) ──
    t = 4
    draw_hline(grid, margin, WIDTH-margin, margin, t)          # 上墙
    draw_hline(grid, margin, WIDTH-margin, HEIGHT-margin-t, t)  # 下墙
    draw_vline(grid, margin, margin, HEIGHT-margin, t)          # 左墙
    draw_vline(grid, WIDTH-margin-t, margin, HEIGHT-margin, t)  # 右墙

    # ── 入口 (下墙中间开口) ──
    entrance_x = WIDTH // 2 - 25
    draw_hline(grid, entrance_x, entrance_x + 50, HEIGHT-margin-t, t, FREE)

    # ── 6条种植畦 (竖向长条，模拟草莓种植行) ──
    bed_top = margin + 60
    bed_bottom = HEIGHT - margin - 80
    bed_width = 40
    bed_gap = 90  # 畦间通道
    start_x = margin + 70

    for i in range(6):
        bx = start_x + i * bed_gap
        if bx + bed_width > WIDTH - margin - 30:
            break
        # 种植畦用深灰色表示（可通过但有植物）
        draw_rect(grid, bx, bed_top, bx + bed_width, bed_bottom, 180)
        # 畦两侧边框
        draw_vline(grid, bx, bed_top, bed_bottom, 2)
        draw_vline(grid, bx + bed_width - 2, bed_top, bed_bottom, 2)

    # ── 横向主通道 (中间) ──
    corridor_y = HEIGHT // 2 - 15
    draw_rect(grid, margin + t, corridor_y, WIDTH - margin - t, corridor_y + 30, FREE)

    # ── 设备间 (左上角) ──
    eq_x2 = margin + 55
    eq_y2 = margin + 50
    draw_rect(grid, margin + t, margin + t, eq_x2, eq_y2, FREE)
    draw_vline(grid, eq_x2, margin + t, eq_y2, 3)
    draw_hline(grid, margin + t, eq_x2 + 3, eq_y2, 3)
    # 设备间入口
    draw_vline(grid, eq_x2, eq_y2 - 20, eq_y2 - 5, 3, FREE)

    # ── 水泵/配电箱 (右上角小障碍) ──
    draw_rect(grid, WIDTH - margin - 50, margin + 15, WIDTH - margin - 20, margin + 40, WALL)

    # ── 工具架 (左下角) ──
    draw_rect(grid, margin + 15, HEIGHT - margin - 50, margin + 45, HEIGHT - margin - 20, WALL)

    # ── 柱子 (4根支撑柱) ──
    pillars = [
        (200, 150), (200, 400),
        (550, 150), (550, 400),
    ]
    for px, py in pillars:
        if px < WIDTH and py < HEIGHT:
            draw_rect(grid, px-4, py-4, px+4, py+4, WALL)

    # ── 保存 PGM ──
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "maps")
    os.makedirs(out_dir, exist_ok=True)

    # 也保存到桌面方便上传
    desktop = os.path.expanduser("~/Desktop")

    img = Image.fromarray(grid, mode='L')

    for save_dir in [out_dir, desktop]:
        pgm_path = os.path.join(save_dir, "greenhouse_map.pgm")
        png_path = os.path.join(save_dir, "greenhouse_map.png")
        yaml_path = os.path.join(save_dir, "greenhouse_map.yaml")

        img.save(pgm_path)
        img.save(png_path)

        yaml_content = f"""image: greenhouse_map.pgm
resolution: {RESOLUTION}
origin: [{ORIGIN_X}, {ORIGIN_Y}, 0.000000]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
"""
        with open(yaml_path, "w") as f:
            f.write(yaml_content)

        print(f"已保存到: {save_dir}")
        print(f"  - {os.path.basename(pgm_path)}")
        print(f"  - {os.path.basename(png_path)}")
        print(f"  - {os.path.basename(yaml_path)}")

    print(f"\n地图尺寸: {WIDTH}x{HEIGHT} px")
    print(f"物理尺寸: {WIDTH*RESOLUTION}x{HEIGHT*RESOLUTION} m")
    print(f"分辨率: {RESOLUTION} m/px")
    print(f"原点: ({ORIGIN_X}, {ORIGIN_Y})")

if __name__ == "__main__":
    main()
