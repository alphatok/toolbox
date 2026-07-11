#!/usr/bin/env python3
"""
split_video.py - 把视频按指定时长上限拆分成多个片段
依赖: ffmpeg / ffprobe (需加入 PATH)
用法:
    python split_video.py 视频文件.mp4
    python split_video.py 视频文件.mp4 --max-minutes 30 --output-dir out
    python split_video.py 视频文件.mp4 --max-minutes 20
默认: 每段最长 30 分钟, 输出到 视频名_parts/ 目录
"""
import argparse
import os
import subprocess
import sys


def run(cmd):
    """执行命令, 失败直接报错退出"""
    print("> " + " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"命令失败 (exit {r.returncode}): {' '.join(cmd)}")


def get_duration(path):
    """用 ffprobe 读取视频总时长(秒)"""
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    return float(out.decode().strip())


def split_video(path, max_minutes, buffer_seconds, output_dir):
    duration = get_duration(path)
    print(f"视频总时长: {duration:.1f} 秒 ({duration/60:.2f} 分钟)")

    max_seconds = max_minutes * 60
    # 实际切分点略小于上限, 因为 copy 模式会在关键帧处切割,
    # 留出 buffer 确保每段都不超过上限
    seg_seconds = max_seconds - buffer_seconds
    if seg_seconds <= 0:
        sys.exit("buffer 过大, 已超过单段时长上限")

    n_parts = max(1, -(-int(duration) // int(seg_seconds)))  # 向上取整
    print(f"将拆分为 {n_parts} 段 (每段目标 ~{seg_seconds} 秒)")

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out_pattern = os.path.join(output_dir, f"{base}_part%03d.mp4")

    run([
        "ffmpeg", "-y", "-i", path,
        "-f", "segment",
        "-segment_time", str(seg_seconds),
        "-reset_timestamps", "1",
        "-c", "copy",
        out_pattern,
    ])
    return output_dir


def verify(output_dir, max_seconds):
    """校验每段时长是否都在上限内"""
    print("\n拆分结果:")
    ok = True
    for f in sorted(os.listdir(output_dir)):
        if not f.endswith(".mp4"):
            continue
        p = os.path.join(output_dir, f)
        try:
            d = get_duration(p)
        except Exception:
            continue
        flag = "OK" if d <= max_seconds else "超长!"
        if d > max_seconds:
            ok = False
        print(f"  {f}: {d/60:.2f} 分钟  [{flag}]")
    if not ok:
        print("警告: 存在超过上限的片段 (关键帧间隔过大), 可减小 --max-minutes 重试")
    else:
        print("全部片段均在时长上限内。")


def main():
    ap = argparse.ArgumentParser(description="按时长上限拆分视频")
    ap.add_argument("input", help="输入视频文件")
    ap.add_argument("--max-minutes", type=float, default=30, help="每段最长分钟数 (默认 30)")
    ap.add_argument("--buffer", type=float, default=30, help="安全余量秒数 (默认 30)")
    ap.add_argument("--output-dir", default=None, help="输出目录 (默认 视频名_parts)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"找不到输入文件: {args.input}")

    out_dir = args.output_dir or (os.path.splitext(args.input)[0] + "_parts")
    split_video(args.input, args.max_minutes, args.buffer, out_dir)
    verify(out_dir, args.max_minutes * 60)


if __name__ == "__main__":
    main()
