#!/usr/bin/env python3
"""
Terminal GIF Player — 在终端中播放 GIF 动画

核心技术：
  - ANSI 24-bit TrueColor (RGB) 颜色转义码
  - 半块字符 ▀ (U+2580) 使垂直分辨率翻倍
    上半部 = 前景色, 下半部 = 背景色
  - \033[H 重置光标位置，避免清屏闪烁
  - 读取 GIF 每帧自带的 duration 控制播放速度
  - 正确处理 GIF disposal method，保证帧叠加正确

兼容：现代终端 (Windows Terminal / iTerm2 / gnome-terminal / Konsole 等)
"""

import sys
import time
import argparse
import shutil
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("错误：需要安装 Pillow 库。请运行: pip install Pillow")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════
# ANSI 工具函数
# ═══════════════════════════════════════════════════════════

def rgb_fg(r: int, g: int, b: int) -> str:
    """24-bit TrueColor 前景色转义码"""
    return f"\033[38;2;{r};{g};{b}m"


def rgb_bg(r: int, g: int, b: int) -> str:
    """24-bit TrueColor 背景色转义码"""
    return f"\033[48;2;{r};{g};{b}m"


ANSI_RESET = "\033[0m"
CURSOR_HOME = "\033[H"
CURSOR_HIDE = "\033[?25l"
CURSOR_SHOW = "\033[?25h"
CLEAR_SCREEN = "\033[2J"


def get_term_size() -> tuple[int, int]:
    """获取终端尺寸 (列数, 行数)"""
    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(size.columns, 10), max(size.lines, 5)


# ═══════════════════════════════════════════════════════════
# 帧 → ANSI 字符串转换（半块字符技术）
# ═══════════════════════════════════════════════════════════

def frame_to_ansi(frame: Image.Image, char_width: int | None = None) -> str:
    """
    将一帧 PIL Image 转换为 ANSI 字符串。

    半块字符原理:
      每个终端字符位置 = 2 个垂直像素
      - 上半像素 → 前景色 (fg)
      - 下半像素 → 背景色 (bg)
      因此图片高度 = 2 × 终端字符行数
    """
    img = frame.convert("RGB")

    if char_width is not None:
        # 计算目标字符高度: 保持宽高比，垂直方向每个字符承载 2 像素
        aspect = img.height / img.width
        char_height = max(1, int(char_width * aspect / 2))
        target_w = char_width
        target_h = char_height * 2  # 像素高度 = 字符行数 × 2
        img = img.resize((target_w, target_h), Image.LANCZOS)

    w, h = img.size
    pixels = img.load()

    lines: list[str] = []

    # 每两行像素编为一个字符行
    for y in range(0, h - 1, 2):
        chars: list[str] = []
        for x in range(w):
            r_top, g_top, b_top = pixels[x, y]
            r_bot, g_bot, b_bot = pixels[x, y + 1]
            chars.append(f"{rgb_fg(r_top, g_top, b_top)}"
                         f"{rgb_bg(r_bot, g_bot, b_bot)}▀")
        chars.append(ANSI_RESET)
        lines.append("".join(chars))

    # 处理奇数高度：最后一行只用上半块
    if h % 2 == 1:
        y = h - 1
        chars = []
        for x in range(w):
            r_top, g_top, b_top = pixels[x, y]
            chars.append(f"{rgb_fg(r_top, g_top, b_top)}"
                         f"{rgb_bg(0, 0, 0)}▀")
        chars.append(ANSI_RESET)
        lines.append("".join(chars))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# GIF 加载与预处理
# ═══════════════════════════════════════════════════════════

def load_gif_frames(
    gif_path: Path,
    char_width: int,
) -> tuple[list[str], list[float]]:
    """
    加载 GIF 所有帧，正确处理 disposal method，
    返回 (ANSI 字符串列表, 帧间隔秒数列表)

    GIF disposal method:
      0 - 未指定 / 替换整个画面
      1 - 不丢弃，叠加到画布上
      2 - 恢复为背景色
      3 - 恢复为前一帧
    """
    img = Image.open(gif_path)
    n_frames = img.n_frames

    ansi_frames: list[str] = []
    durations: list[float] = []

    # 累积画布（处理 disposal 叠加）
    canvas: Image.Image | None = None
    # 用于 disposal=3 (restore to previous) 的前一帧备份
    prev_canvas: Image.Image | None = None

    for idx in range(n_frames):
        img.seek(idx)

        # 获取 disposal method（兼容旧版 Pillow）
        try:
            disposal = img.disposal_method
        except AttributeError:
            disposal = 0

        # 当前帧 (RGBA)
        frame = img.copy().convert("RGBA")

        # 根据 disposal 更新画布
        if canvas is None or disposal in (0, 2):
            # 新画布 / 恢复背景 → 黑色底 + 当前帧
            canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)
        elif disposal == 1:
            # 不丢弃：叠加到现有画布上
            canvas.paste(frame, (0, 0), frame)
        elif disposal == 3:
            # 恢复为前一帧：先恢复再叠加
            if prev_canvas is not None:
                canvas = prev_canvas.copy()
            else:
                canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)
        else:
            # 未知 disposal，覆盖处理
            canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)

        # 备份当前画布（供下一帧 disposal=3 使用）
        prev_canvas = canvas.copy()

        # 合成为 RGB（黑色底色）
        rgb_frame = Image.new("RGB", canvas.size, (0, 0, 0))
        rgb_frame.paste(canvas, (0, 0), canvas)

        # 转换为 ANSI
        ansi_str = frame_to_ansi(rgb_frame, char_width)
        ansi_frames.append(ansi_str)

        # 读取帧间隔 (ms → 秒)
        duration = img.info.get("duration", 100)
        if duration <= 0:
            duration = 100
        durations.append(duration / 1000.0)

    img.close()
    return ansi_frames, durations


# ═══════════════════════════════════════════════════════════
# 播放引擎
# ═══════════════════════════════════════════════════════════

def play_gif(
    gif_path: Path,
    char_width: int | None = None,
    loop: bool = True,
) -> None:
    """
    播放 GIF 动画的主循环。

    - 使用 \033[H 重置光标而非清屏，消除闪烁
    - 按帧自带的 duration 控制帧率
    - 支持无限循环 / 单次播放
    """
    # 确定输出宽度
    term_w, term_h = get_term_size()
    if char_width is None:
        char_width = term_w
    char_width = max(10, min(char_width, term_w))

    print(f"加载 GIF: {gif_path.name}")
    print(f"帧尺寸: {char_width}×? 字符  |  终端: {term_w}×{term_h}")

    try:
        ansi_frames, durations = load_gif_frames(gif_path, char_width)
    except Exception as e:
        print(f"加载 GIF 失败: {e}")
        return

    if not ansi_frames:
        print("GIF 无可用帧。")
        return

    # 检查是否会超出终端高度
    first_frame_lines = ansi_frames[0].count("\n") + 1
    if first_frame_lines > term_h - 1:
        # 自动缩小
        scale = (term_h - 1) / first_frame_lines
        new_width = max(10, int(char_width * scale))
        print(f"画面过高，自动缩放到宽度 {new_width} 字符...")
        ansi_frames, durations = load_gif_frames(gif_path, new_width)

    total_lines = ansi_frames[0].count("\n") + 1
    print(f"共 {len(ansi_frames)} 帧, "
          f"每帧 {total_lines} 行字符, "
          f"总时长约 {sum(durations):.1f}s")
    print("按 Ctrl+C 退出\n")

    # 隐藏光标，清屏
    sys.stdout.write(CURSOR_HIDE + CLEAR_SCREEN)
    sys.stdout.flush()

    interrupted = False
    try:
        while True:
            for ansi_str, duration in zip(ansi_frames, durations):
                frame_start = time.perf_counter()

                # 重置光标到左上角（不清屏，无闪烁）
                sys.stdout.write(CURSOR_HOME)
                sys.stdout.write(ansi_str)
                sys.stdout.flush()

                # 精确帧计时：扣除渲染耗时
                elapsed = time.perf_counter() - frame_start
                sleep_time = max(0, duration - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            if not loop:
                break
    except KeyboardInterrupt:
        interrupted = True
    finally:
        # 恢复光标 + 清屏（退出时清理）
        sys.stdout.write(CURSOR_SHOW + ANSI_RESET + CLEAR_SCREEN)
        sys.stdout.flush()
        if interrupted:
            print("播放已中断。")


# ═══════════════════════════════════════════════════════════
# 网格平铺播放引擎
# ═══════════════════════════════════════════════════════════

def _gif_native_size(gif_path: Path) -> tuple[int, int]:
    """获取 GIF 原始尺寸（像素）"""
    img = Image.open(gif_path)
    w, h = img.size
    img.close()
    return w, h


def _cell_size_from_width(
    gif_path: Path,
    cell_char_w: int,
) -> tuple[int, int]:
    """
    根据单元格字符宽度计算单元格的 (字符宽, 字符高)。
    保持 GIF 宽高比，垂直方向每字符承载 2 像素。
    """
    pw, ph = _gif_native_size(gif_path)
    aspect = ph / pw
    cell_char_h = max(1, int(cell_char_w * aspect / 2))
    return cell_char_w, cell_char_h


def _render_cell_frames(
    gif_path: Path,
    cell_char_w: int,
) -> tuple[list[list[str]], list[float], int]:
    """
    加载 GIF 所有帧，每帧渲染为单元格尺寸的 ANSI 行列表。

    返回:
      frame_lines_list:  list[list[str]] — 每帧的 ANSI 行列表（长度 = cell_char_h）
      durations:         list[float]   — 每帧间隔（秒）
      cell_char_h:       int           — 单元格字符高度
    """
    img = Image.open(gif_path)
    n_frames = img.n_frames
    pw, ph = img.size
    aspect = ph / pw

    cell_char_h = max(1, int(cell_char_w * aspect / 2))
    cell_pixel_w = cell_char_w
    cell_pixel_h = cell_char_h * 2  # 半块字符：每字符 2 像素高

    frame_lines_list: list[list[str]] = []
    durations: list[float] = []

    canvas: Image.Image | None = None
    prev_canvas: Image.Image | None = None

    for idx in range(n_frames):
        img.seek(idx)

        try:
            disposal = img.disposal_method
        except AttributeError:
            disposal = 0

        frame = img.copy().convert("RGBA")

        # 处理 disposal
        if canvas is None or disposal in (0, 2):
            canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)
        elif disposal == 1:
            canvas.paste(frame, (0, 0), frame)
        elif disposal == 3:
            if prev_canvas is not None:
                canvas = prev_canvas.copy()
            else:
                canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)
        else:
            canvas = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            canvas.paste(frame, (0, 0), frame)

        prev_canvas = canvas.copy()

        # 合成为 RGB，缩放到单元格像素尺寸
        rgb_frame = Image.new("RGB", canvas.size, (0, 0, 0))
        rgb_frame.paste(canvas, (0, 0), canvas)
        rgb_frame = rgb_frame.resize((cell_pixel_w, cell_pixel_h), Image.LANCZOS)

        # 转换为 ANSI 行（不再二次缩放）
        ansi_str = frame_to_ansi(rgb_frame, char_width=None)
        frame_lines_list.append(ansi_str.split("\n"))

        # 帧间隔
        duration = img.info.get("duration", 100)
        if duration <= 0:
            duration = 100
        durations.append(duration / 1000.0)

    img.close()
    return frame_lines_list, durations, cell_char_h


def _compose_grid_frame(
    cell_lines: list[str],
    grid_cols: int,
    grid_rows: int,
) -> str:
    """
    将单个单元格的 ANSI 行平铺为 grid_cols × grid_rows 的完整屏幕帧。
    """
    output_lines: list[str] = []
    for _grid_row in range(grid_rows):
        for line in cell_lines:
            # 同一行水平拼接 grid_cols 份（每份自带 ANSI reset，不会颜色串扰）
            output_lines.append(line * grid_cols)
    return "\n".join(output_lines)


def play_gif_grid(
    gif_path: Path,
    grid_cols: int,
    grid_rows: int,
    loop: bool = True,
    char_width: int | None = None,
) -> None:
    """
    以 grid_cols × grid_rows 网格平铺播放同一个 GIF。

    参数:
      char_width: 每个单元格的字符宽度，None = 自适应终端

    自动缩放逻辑：
      1. 若指定 -w，以之为初始单元格宽度；否则按终端宽度 ÷ 列数计算
      2. 如果整个网格超出终端，逐步缩小单元格宽度（每次 ×0.85）
      3. 最小单元格宽度 6 字符，若仍放不下则维持最小尺寸
    """
    term_w, term_h = get_term_size()

    # 初始单元格宽度
    if char_width is not None:
        cell_w = char_width
    else:
        cell_w = term_w // grid_cols
    cell_w = max(6, cell_w)

    print(f"网格模式: {grid_cols}×{grid_rows}  |  终端: {term_w}×{term_h}")
    print(f"加载 GIF: {gif_path.name}")

    # 自动缩放循环：如果超出终端则缩小
    frame_lines_list = None
    durations = None
    cell_h = 0

    while cell_w >= 6:
        cell_w, cell_h = _cell_size_from_width(gif_path, cell_w)
        # 确保 cell_w 不超过终端列宽限制
        if cell_w * grid_cols > term_w:
            cell_w = term_w // grid_cols
            cell_w, cell_h = _cell_size_from_width(gif_path, cell_w)

        try:
            frame_lines_list, durations, cell_h = _render_cell_frames(
                gif_path, cell_w
            )
        except Exception as e:
            print(f"渲染失败: {e}")
            return

        total_h = cell_h * grid_rows

        if total_h <= term_h:
            # 放得下
            break

        # 放不下，缩小一层
        old_w = cell_w
        cell_w = max(6, int(cell_w * 0.85))
        print(f"  ⚠ 网格 {total_h} 行超出终端 {term_h} 行，"
              f"单元格宽度 {old_w} → {cell_w}")

    if frame_lines_list is None:
        print("无法渲染 GIF 帧。")
        return

    total_h = cell_h * grid_rows
    print(f"单元格: {cell_w}×{cell_h} 字符  "
          f"({cell_w}×{cell_h * 2} 像素)  |  "
          f"网格总尺寸: {cell_w * grid_cols}×{total_h} 字符")
    print(f"共 {len(frame_lines_list)} 帧, "
          f"总时长约 {sum(durations):.1f}s")
    print("按 Ctrl+C 退出\n")

    # 预合成所有帧的网格画面
    grid_frames: list[str] = []
    for cell_lines in frame_lines_list:
        grid_frames.append(
            _compose_grid_frame(cell_lines, grid_cols, grid_rows)
        )

    # 播放
    sys.stdout.write(CURSOR_HIDE + CLEAR_SCREEN)
    sys.stdout.flush()

    interrupted = False
    try:
        while True:
            for grid_str, duration in zip(grid_frames, durations):
                frame_start = time.perf_counter()

                sys.stdout.write(CURSOR_HOME)
                sys.stdout.write(grid_str)
                sys.stdout.flush()

                elapsed = time.perf_counter() - frame_start
                sleep_time = max(0, duration - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            if not loop:
                break
    except KeyboardInterrupt:
        interrupted = True
    finally:
        sys.stdout.write(CURSOR_SHOW + ANSI_RESET + CLEAR_SCREEN)
        sys.stdout.flush()
        if interrupted:
            print("播放已中断。")


# ═══════════════════════════════════════════════════════════
# 交互式文件选择器
# ═══════════════════════════════════════════════════════════

def select_gif_file() -> Path | None:
    """
    简易交互式 GIF 文件选择器。
    在当前目录及子目录中搜索 .gif 文件，提供菜单选择。
    支持：
      - 数字选择
      - 输入自定义路径
      - 浏览子目录
    """
    cwd = Path.cwd()

    # 搜索 GIF 文件（当前目录 + 一级子目录）
    gif_files: list[Path] = []
    gif_files.extend(sorted(cwd.glob("*.gif"), key=lambda p: p.name.lower()))
    for subdir in sorted(cwd.iterdir()):
        if subdir.is_dir() and not subdir.name.startswith("."):
            gif_files.extend(sorted(subdir.glob("*.gif"),
                                    key=lambda p: p.name.lower()))

    # 同时也从常见目录搜索
    home_gifs = list(Path.home().glob("*.gif"))
    downloads_gif_dir = Path.home() / "Downloads"
    if downloads_gif_dir.exists():
        home_gifs.extend(downloads_gif_dir.glob("*.gif"))
    # 去重
    seen = set(str(p) for p in gif_files)
    for p in home_gifs:
        if str(p) not in seen:
            gif_files.append(p)

    if not gif_files:
        print("\n当前目录及常用位置未找到 GIF 文件。")
        return _custom_path_input()

    # 分页显示
    page_size = 20
    total_pages = (len(gif_files) + page_size - 1) // page_size
    page = 0

    while True:
        start = page * page_size
        end = min(start + page_size, len(gif_files))

        print(f"\n{'─' * 55}")
        print(f"  GIF 终端播放器 — 选择文件")
        print(f"{'─' * 55}")
        print(f"  当前目录: {cwd}")
        print(f"  找到 {len(gif_files)} 个 GIF 文件")
        if total_pages > 1:
            print(f"  第 {page + 1}/{total_pages} 页")
        print(f"{'─' * 55}")

        for i, f in enumerate(gif_files[start:end], start=start):
            idx = i + 1
            size_kb = f.stat().st_size / 1024
            # 截断过长的路径显示
            display = str(f)
            if len(display) > 65:
                display = "..." + display[-62:]
            print(f"  [{idx:>3}] {display}  ({size_kb:.0f} KB)")

        print(f"{'─' * 55}")
        print(f"  [c] 输入自定义路径")
        if total_pages > 1:
            print(f"  [n] 下一页  [p] 上一页")
        print(f"  [q] 退出")
        print(f"{'─' * 55}")

        choice = input("  → ").strip().lower()

        if choice == "q":
            return None
        elif choice == "c":
            return _custom_path_input()
        elif choice == "n" and total_pages > 1:
            page = (page + 1) % total_pages
        elif choice == "p" and total_pages > 1:
            page = (page - 1) % total_pages
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(gif_files):
                    return gif_files[idx]
            except ValueError:
                pass
            print(f"  ⚠ 无效选择，请输入 1-{len(gif_files)} 或命令字母。")


def _custom_path_input() -> Path | None:
    """输入自定义 GIF 路径"""
    print()
    while True:
        path_str = input("  输入 GIF 文件路径 (q 返回): ").strip()
        if path_str.lower() == "q":
            return None
        if not path_str:
            continue
        p = Path(path_str).expanduser().resolve()
        if p.exists() and p.suffix.lower() == ".gif":
            return p
        if p.exists() and p.suffix.lower() != ".gif":
            print(f"  ⚠ 不是 .gif 文件: {p}")
        else:
            print(f"  ⚠ 文件不存在: {p}")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="在终端中用 TrueColor + 半块字符播放 GIF 动画",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python gif_player.py                             # 交互式选择文件
  python gif_player.py demo.gif                    # 播放指定文件
  python gif_player.py demo.gif -w 60              # 指定宽度 60 字符
  python gif_player.py demo.gif -g 2x1             # 2 列 1 行，并排两个
  python gif_player.py demo.gif -g 2x2             # 2×2 网格，四个小猫
  python gif_player.py demo.gif -g 2x2 -w 40       # 2×2 网格，每格宽 40 字符
  python gif_player.py demo.gif -g 3x2 --once      # 3×2 网格，只播一次
        """,
    )
    parser.add_argument(
        "gif", nargs="?", default=None,
        help="GIF 文件路径（省略则进入交互式选择）"
    )
    parser.add_argument(
        "-w", "--width", type=int, default=None,
        help="输出宽度（字符数）。单播放模式 = 画面宽度；"
             "网格模式 = 每个单元格宽度。超出终端时自动缩小。"
    )
    parser.add_argument(
        "-g", "--grid", type=str, default=None,
        metavar="WxH",
        help="网格平铺模式，例如 2x1（并排两个）、2x2（四个）。"
             "配合 -w 可指定单元格宽度。超出终端时自动缩小。"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="只播放一次，不循环"
    )
    args = parser.parse_args()

    # 解析网格参数
    grid_cols = grid_rows = 0
    if args.grid:
        try:
            parts = args.grid.lower().split("x")
            if len(parts) != 2:
                raise ValueError
            grid_cols = int(parts[0])
            grid_rows = int(parts[1])
            if grid_cols < 1 or grid_rows < 1 or grid_cols > 20 or grid_rows > 20:
                raise ValueError
        except (ValueError, IndexError):
            print(f"错误: 无效的网格格式 '{args.grid}'，应为 WxH（如 2x1, 2x2, 3x2），"
                  f"每维 1-20。")
            sys.exit(1)

    # 确定 GIF 文件
    gif_path = args.gif
    if gif_path:
        gif_path = Path(gif_path).expanduser().resolve()
        if not gif_path.exists():
            print(f"文件不存在: {gif_path}")
            sys.exit(1)
    else:
        gif_path = select_gif_file()
        if gif_path is None:
            print("未选择文件，退出。")
            sys.exit(0)

    # 播放
    if grid_cols > 0 and grid_rows > 0:
        play_gif_grid(
            gif_path=gif_path,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
            loop=not args.once,
            char_width=args.width,
        )
    else:
        play_gif(
            gif_path=gif_path,
            char_width=args.width,
            loop=not args.once,
        )


if __name__ == "__main__":
    main()
