#!/usr/bin/env python3
"""
tts.py - 腾讯云语音合成: 文本 -> 语音 (mp3/wav), 支持指定音色

依赖:
    tencentcloud-sdk-python-tts  (腾讯云语音合成 SDK)
    ffmpeg                      (拼接长文本的分段结果)

配置:
    复用 asr_config.toml 的 [credentials](SecretId/SecretKey/region);
    [tts] 段可设默认音色等, 见 asr_config.example.toml。

用法:
    python tts.py "你好,欢迎光临" -o hello.mp3
    python tts.py 讲稿.txt --voice 1004 --speed 1        # 男声, 稍快
    python tts.py 讲稿.txt --voice 1015                  # 童声
    python tts.py --list-voices                          # 列出内置标准音色
    python tts.py 讲稿.txt --model-type 2 --voice 200001 # 大模型音色(需开通)
"""
import argparse
import os
import re
import subprocess
import sys
import uuid

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.9/3.10

# 内置标准音色 (腾讯云文档公开): 1001-1016
STANDARD_VOICES = {
    1001: "智瑜 (女声 · 中文普通话 · 情感/电话外呼客服)",
    1002: "智聆 (女声 · 中文普通话 · 通用)",
    1003: "智美 (女声 · 中文普通话 · 英语混读)",
    1004: "智云 (男声 · 中文普通话 · 通用)",
    1005: "智莉 (女声 · 中文普通话 · 英文混读)",
    1006: "智言 (男声 · 中文普通话 · 英文混读)",
    1007: "智娜 (女声 · 中文普通话 · 英文混读)",
    1008: "智琪 (女声 · 中文普通话 · 英文混读)",
    1009: "智芸 (女声 · 中文普通话 · 英文混读)",
    1010: "智华 (男声 · 中文普通话 · 英文混读)",
    1011: "智燕 (女声 · 中文普通话 · 英文混读)",
    1012: "智丹 (女声 · 中文普通话 · 英文混读)",
    1013: "智辉 (男声 · 中文普通话 · 英文混读)",
    1014: "智宁 (男声 · 中文普通话 · 英文混读)",
    1015: "智萌 (女声 · 中文普通话 · 童声)",
    1016: "智皓 (男声 · 中文普通话 · 童声)",
}
# 提示: 精品音色 (101001-101052 等) 与大模型音色 (200001+, 需购买资源包)
# 完整音色表见腾讯云文档「音色列表」或控制台实时查询。

# 实时合成单次文本上限(UTF-8 字节), 分段时保守取值
MAX_SEG_CHARS = 450


def log(msg):
    print(f"[tts] {msg}")


def load_config(path):
    """加载配置(复用 asr_config.toml), 缺失键用默认值补齐并校验必填项"""
    if not os.path.isfile(path):
        sys.exit(
            f"找不到配置文件: {path}\n"
            f"请复制 asr_config.example.toml 为 asr_config.toml 并填写腾讯云 SecretId/SecretKey。"
        )
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("credentials", {}).setdefault("region", "ap-guangzhou")
    cfg.setdefault("tts", {}).setdefault("voice_type", 1001)
    cfg.setdefault("tts", {}).setdefault("model_type", 1)
    cfg.setdefault("tts", {}).setdefault("speed", 0)
    cfg.setdefault("tts", {}).setdefault("volume", 0)
    cfg.setdefault("tts", {}).setdefault("sample_rate", 24000)
    cfg.setdefault("tts", {}).setdefault("codec", "mp3")
    cred = cfg["credentials"]
    for key in ("secret_id", "secret_key"):
        val = str(cred.get(key, ""))
        if not val or "xxx" in val.lower():
            sys.exit(f"配置文件缺少必填项 [credentials].{key}, 请在 {path} 中填写。")
    return cfg


def load_text(text_arg, file_arg):
    """取文本: 命令行直传或从文件读取, 两者必填其一"""
    if text_arg and file_arg:
        sys.exit("文本与 --file 只能二选一")
    if file_arg:
        if not os.path.isfile(file_arg):
            sys.exit(f"找不到文本文件: {file_arg}")
        with open(file_arg, encoding="utf-8") as f:
            return f.read().strip()
    if text_arg:
        return text_arg
    sys.exit("缺少输入: 请直接传文本或使用 --file 指定文本文件")


def split_text(text, max_chars=MAX_SEG_CHARS):
    """按句子边界把长文本切成不超过 max_chars 的段落; 无标点的超长串按硬长度切块"""
    sentences = re.split(r"(?<=[。！？!?；;…\n])", text)
    segments, buf = [], ""
    for s in sentences:
        if not s:
            continue
        # 超长句 (如无标点的长串) 按硬长度切块
        while len(s) > max_chars:
            if buf:
                segments.append(buf)
                buf = ""
            segments.append(s[:max_chars])
            s = s[max_chars:]
        if len(buf) + len(s) > max_chars and buf:
            segments.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        segments.append(buf)
    return segments


def synthesize(client, models, text, tts_cfg, seg_no, total):
    """合成一段文本, 返回音频 bytes"""
    req = models.TextToVoiceRequest()
    req.Text = text
    req.SessionId = str(uuid.uuid4())
    req.VoiceType = tts_cfg["voice_type"]
    req.ModelType = tts_cfg["model_type"]
    req.Speed = tts_cfg["speed"]
    req.Volume = tts_cfg["volume"]
    req.SampleRate = tts_cfg["sample_rate"]
    req.Codec = tts_cfg["codec"]
    req.PrimaryLanguage = 1  # 中文
    if total > 1:
        print(f"  合成第 {seg_no}/{total} 段...", flush=True)
    resp = client.TextToVoice(req)
    return resp.Audio


def merge_segments(part_paths, output, codec):
    """用 ffmpeg 拼接分段音频"""
    if len(part_paths) == 1:
        os.replace(part_paths[0], output)
        return
    list_path = os.path.splitext(output)[0] + ".parts.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in part_paths:
            f.write(f"file '{p}'\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", output]
    r = subprocess.run(cmd)
    os.remove(list_path)
    for p in part_paths:
        if os.path.exists(p):
            os.remove(p)
    if r.returncode != 0:
        sys.exit(f"拼接失败 (exit {r.returncode}): {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser(description="腾讯云语音合成: 文本 -> 语音 (支持指定音色)")
    ap.add_argument("text", nargs="?", default=None, help="要合成的文本 (与 --file 二选一)")
    ap.add_argument("--file", "-f", default=None, help="文本文件路径 (UTF-8)")
    ap.add_argument("--output", "-o", default=None, help="输出音频路径 (默认 <文本首字或文件名>.mp3)")
    ap.add_argument("--voice", type=int, default=None,
                    help="音色 ID (标准音色 1001-1016, 精品/大模型音色见文档, 默认配置值)")
    ap.add_argument("--model-type", type=int, default=None,
                    help="模型类型: 1=标准/精品音色 (默认) | 2=大模型音色 (需购买资源包)")
    ap.add_argument("--speed", type=float, default=None, help="语速, 范围 -2~6 (默认 0)")
    ap.add_argument("--volume", type=float, default=None, help="音量, 范围 -5~5 (默认 0)")
    ap.add_argument("--sample-rate", type=int, choices=[16000, 24000, 48000], default=None,
                    help="采样率 (默认 24000)")
    ap.add_argument("--codec", choices=["mp3", "wav"], default=None, help="编码 (默认 mp3)")
    ap.add_argument("--list-voices", action="store_true", help="列出内置标准音色后退出")
    ap.add_argument("--config", default="asr_config.toml", help="配置文件路径 (默认 asr_config.toml)")
    args = ap.parse_args()

    if args.list_voices:
        print("内置标准音色 (VoiceType):")
        for vid, desc in STANDARD_VOICES.items():
            print(f"  {vid}  {desc}")
        print("提示: 精品音色 (101001-101052 等) 与大模型音色 (200001+, 需购买资源包)")
        print("      完整音色表见腾讯云文档「音色列表」或控制台。")
        return

    cfg = load_config(args.config)
    tts_cfg = dict(cfg["tts"])
    if args.voice is not None:
        tts_cfg["voice_type"] = args.voice
    if args.model_type is not None:
        tts_cfg["model_type"] = args.model_type
    if args.speed is not None:
        tts_cfg["speed"] = args.speed
    if args.volume is not None:
        tts_cfg["volume"] = args.volume
    if args.sample_rate is not None:
        tts_cfg["sample_rate"] = args.sample_rate
    if args.codec is not None:
        tts_cfg["codec"] = args.codec
    if tts_cfg["voice_type"] in STANDARD_VOICES:
        log(f"音色: {tts_cfg['voice_type']} {STANDARD_VOICES[tts_cfg['voice_type']]}")
    else:
        log(f"音色: {tts_cfg['voice_type']} (非内置标准音色, 未校验是否开通)")

    text = load_text(args.text, args.file)
    if not text:
        sys.exit("文本为空")
    segments = split_text(text)
    log(f"文本 {len(text)} 字, 将分 {len(segments)} 段合成 (音色 {tts_cfg['voice_type']})")

    try:
        import warnings
        warnings.filterwarnings("ignore", category=SyntaxWarning)
        from tencentcloud.common import credential as tc_cred
        from tencentcloud.common.exception import TencentCloudSDKException
        from tencentcloud.tts.v20190823 import tts_client, models
    except ImportError:
        sys.exit("缺少腾讯云 TTS SDK, 请先安装: uv add tencentcloud-sdk-python-tts")

    cred = tc_cred.Credential(cfg["credentials"]["secret_id"],
                              cfg["credentials"]["secret_key"])
    client = tts_client.TtsClient(cred, cfg["credentials"]["region"])

    out = args.output or (os.path.splitext(args.file)[0] if args.file
                          else text[:8]) + "." + tts_cfg["codec"]
    part_paths = []
    try:
        for i, seg in enumerate(segments, 1):
            audio = synthesize(client, models, seg, tts_cfg, i, len(segments))
            if not audio:
                sys.exit(f"第 {i} 段合成结果为空")
            part = f"{out}.part{i}"
            with open(part, "wb") as f:
                f.write(__import__("base64").b64decode(audio))
            part_paths.append(part)
    except TencentCloudSDKException as e:
        for p in part_paths:
            if os.path.exists(p):
                os.remove(p)
        hint = ""
        if "ServerNotOpen" in str(e):
            hint = (" (语音合成服务未开通, 请先在控制台开通:"
                    " https://console.cloud.tencent.com/tts)")
        elif "VoiceType" in str(e) or "voice" in str(e).lower():
            hint = (" (音色可能未开通或编号无效, 用 --list-voices 查看标准音色,"
                    " 精品/大模型音色需在控制台确认)")
        sys.exit(f"腾讯云接口调用失败: [{e.code}] {e.message}{hint}")

    merge_segments(part_paths, out, tts_cfg["codec"])
    log(f"语音已生成: {out}")


if __name__ == "__main__":
    main()
