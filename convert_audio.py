#!/usr/bin/env python3
"""
convert_audio.py - 音频格式转换 (m4a -> mp3)
依赖: ffmpeg (需加入 PATH)
用法:
    python convert_audio.py 音频文件.m4a
    python convert_audio.py 音频文件.m4a --bitrate 320k
    python convert_audio.py *.m4a --output-dir mp3
    python convert_audio.py m4a目录            # 转换目录下所有 .m4a
默认: mp3 比特率 192k, 输出到源文件同目录, 保留元数据
"""
import argparse
import glob
import os
import subprocess
import sys


def run(cmd):
    """执行命令, 失败直接报错退出"""
    print("> " + " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"命令失败 (exit {r.returncode}): {' '.join(cmd)}")


def find_inputs(paths):
    """把参数展开成文件列表: 支持文件、目录、通配符"""
    files = []
    for p in paths:
        if os.path.isdir(p):
            # 目录: 转换其中所有 m4a (兼容 .M4A 大写后缀)
            for pat in ("*.m4a", "*.M4A"):
                files.extend(sorted(glob.glob(os.path.join(p, pat))))
        elif os.path.isfile(p):
            files.append(p)
        else:
            sys.exit(f"找不到输入: {p}")
    if not files:
        sys.exit("没有找到可转换的 m4a 文件")
    return files


def convert(path, bitrate, output_dir):
    """把单个音频文件转成 mp3, 返回输出路径"""
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(output_dir, base + ".mp3")
    run([
        "ffmpeg", "-y", "-i", path,
        "-vn",                    # 忽略封面等视频流, 只转音频
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
        "-map_metadata", "0",     # 保留原文件元数据 (标题/艺术家等)
        "-id3v2_version", "3",    # 兼容性好的 ID3v2.3 标签
        out,
    ])
    return out


def main():
    ap = argparse.ArgumentParser(description="音频格式转换 (m4a -> mp3)")
    ap.add_argument("inputs", nargs="+", help="输入文件或目录 (可多个, 目录会转换其中所有 m4a)")
    ap.add_argument("--bitrate", default="192k", help="mp3 比特率 (默认 192k)")
    ap.add_argument("--output-dir", default=None, help="输出目录 (默认与源文件同目录)")
    args = ap.parse_args()

    for path in find_inputs(args.inputs):
        out_dir = args.output_dir or os.path.dirname(os.path.abspath(path))
        os.makedirs(out_dir, exist_ok=True)
        out = convert(path, args.bitrate, out_dir)
        print(f"转换完成: {path} -> {out}")


if __name__ == "__main__":
    main()
