#!/usr/bin/env python3
"""
transcribe.py - 腾讯云录音文件识别: 音频 -> 逐字稿 (markdown, 区分发言人)

流程:
    本地音频(mp3/m4a/wav 等) -> 上传 COS 生成临时 URL (或直接提供 URL)
    -> CreateRecTask 提交异步任务 -> 轮询 DescribeTaskStatus
    -> 解析结果(说话人+时间戳) -> 输出 .md 逐字稿

依赖:
    tencentcloud-sdk-python-asr  (腾讯云语音识别 SDK)
    cos-python-sdk-v5           (本地文件上传 COS 时需要)

配置:
    复制 asr_config.example.toml 为 asr_config.toml 并填写 SecretId/SecretKey 等。

用法:
    python transcribe.py 会议录音.mp3
    python transcribe.py 会议录音.mp3 --output 纪要.md
    python transcribe.py https://example.com/meeting.mp3 --speakers 3
    python transcribe.py 会议录音.mp3 --config 其他配置.toml
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.9/3.10

# 配置默认值: 仅作为兜底, 可配置项都在 asr_config.toml
DEFAULT_CONFIG = {
    "credentials": {"region": "ap-guangzhou"},
    "cos": {"upload_prefix": "asr-upload/", "url_expire_seconds": 7200},
    "asr": {
        "engine_model_type": "16k_zh",
        "channel_num": 1,
        "res_text_format": 0,
        "speaker_diarization": 1,
        "speaker_number": 0,
        "filter_dirty": 0,
        "filter_punc": 0,
        "filter_modal": 0,
        "poll_interval": 5,
        "timeout": 1800,
    },
}


def log(msg):
    print(f"[transcribe] {msg}")


def load_config(path):
    """加载 asr_config.toml, 缺失的键用默认值补齐, 并校验必填项"""
    if not os.path.isfile(path):
        sys.exit(
            f"找不到配置文件: {path}\n"
            f"请复制 asr_config.example.toml 为 asr_config.toml 并填写腾讯云 SecretId/SecretKey。"
        )
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    # 两层深合并默认值 (credentials / cos / asr)
    for section, defaults in DEFAULT_CONFIG.items():
        cfg.setdefault(section, {})
        for k, v in defaults.items():
            cfg[section].setdefault(k, v)

    cred = cfg["credentials"]
    for key in ("secret_id", "secret_key"):
        val = str(cred.get(key, ""))
        if not val or "xxx" in val.lower():  # 空值或模板占位符都视为未填写
            sys.exit(f"配置文件缺少必填项 [credentials].{key}, 请在 {path} 中填写。")
    if not cfg["cos"].get("bucket"):
        log("提示: 未配置 [cos].bucket, 本地文件将无法自动上传, 仅支持直接传 URL。")
    return cfg


def upload_to_cos(cfg, path):
    """上传本地音频到 COS, 返回带签名的临时 GET URL"""
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        sys.exit("缺少 cos-python-sdk-v5, 请先安装: uv add cos-python-sdk-v5")

    cred = cfg["credentials"]
    cos = cfg["cos"]
    client = CosS3Client(CosConfig(
        Region=cred["region"], SecretId=cred["secret_id"], SecretKey=cred["secret_key"],
    ))
    key = cos["upload_prefix"] + os.path.basename(path)
    with open(path, "rb") as f:
        client.put_object(Bucket=cos["bucket"], Body=f, Key=key, ContentType="audio/mpeg")
    url = client.get_presigned_url(
        Method="GET", Bucket=cos["bucket"], Key=key, Expired=cos["url_expire_seconds"],
    )
    log(f"已上传 {path} -> cos://{cos['bucket']}/{key}, 临时 URL 有效期 {cos['url_expire_seconds']} 秒")
    return url


def is_url(s):
    return urllib.parse.urlparse(s).scheme in ("http", "https")


def submit_task(client, models, url, asr_cfg):
    """提交录音文件识别任务, 返回 TaskId"""
    req = models.CreateRecTaskRequest()
    req.EngineModelType = asr_cfg["engine_model_type"]
    req.ChannelNum = asr_cfg["channel_num"]
    req.ResTextFormat = asr_cfg["res_text_format"]
    req.SourceType = 0
    req.Url = url
    req.SpeakerDiarization = asr_cfg["speaker_diarization"]
    req.SpeakerNumber = asr_cfg["speaker_number"]
    req.FilterDirty = asr_cfg["filter_dirty"]
    req.FilterPunc = asr_cfg["filter_punc"]
    req.FilterModal = asr_cfg["filter_modal"]
    resp = client.CreateRecTask(req)
    log(f"识别任务已提交 (TaskId={resp.Data.TaskId})")
    return resp.Data.TaskId


def poll_task(client, models, task_id, asr_cfg):
    """轮询识别结果, 返回 (成功与否, ResultDetail, Result, 错误信息)"""
    req = models.DescribeTaskStatusRequest()
    req.TaskId = task_id
    interval = asr_cfg["poll_interval"]
    deadline = time.time() + asr_cfg["timeout"]
    while time.time() < deadline:
        resp = client.DescribeTaskStatus(req)
        data = resp.Data
        status = data.Status
        if status == 2:  # 成功
            return True, data.ResultDetail, data.Result, ""
        if status == 3:  # 失败
            return False, None, None, data.ErrorMsg
        # 0=等待 1=执行中
        print(f"  识别中... (status={status}, 每 {interval}s 查询)", flush=True)
        time.sleep(interval)
    return False, None, None, "轮询超时"


# 旧版文本格式一行: [开始秒:毫秒,结束秒:毫秒,说话人] 文本
_LEGACY_LINE = re.compile(r"\[(\d+):([\d.]+),(\d+):([\d.]+),(\d+)\]\s*(.*)")


def parse_legacy_result(text):
    """解析旧版文本格式 Result, 返回与 parse_result_detail 相同结构的句子列表"""
    sentences = []
    for line in (text or "").splitlines():
        m = _LEGACY_LINE.match(line.strip())
        if not m:
            continue
        s_start, s_start_frac, s_end, s_end_frac, speaker, text = m.groups()
        if not text:
            continue
        start = int(s_start) * 1000 + int(float(s_start_frac) * 1000)
        end = int(s_end) * 1000 + int(float(s_end_frac) * 1000)
        sentences.append((int(speaker), start, end, text.strip()))
    return sentences


def parse_result_detail(result_detail):
    """
    解析 ResultDetail JSON, 返回句子列表: [(speaker_id, start_ms, end_ms, text), ...]
    兼容各 ResTextFormat 的段落/句子结构, 解析失败抛异常由调用方处理。
    """
    data = json.loads(result_detail)
    results = data.get("Result", data if isinstance(data, list) else [])
    sentences = []
    for slice_ in results:
        got = False
        for para in slice_.get("Paragraphs", []):
            speaker = para.get("SpeakerId", 0)
            for sent in para.get("Sentences", []):
                sentences.append((
                    speaker,
                    sent.get("StartTime", 0),
                    sent.get("EndTime", 0),
                    sent.get("Text", ""),
                ))
                got = True
        # 无段落结构时退化为直接取 Text
        if not got and slice_.get("Text"):
            sentences.append((0, slice_.get("StartTime", 0),
                              slice_.get("EndTime", 0), slice_["Text"]))
    return sentences


def fmt_ts(ms):
    """毫秒 -> HH:MM:SS"""
    s = int(ms // 1000)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_markdown(sentences, meta):
    """按发言人分组生成 markdown 逐字稿, meta: {音频, 时长, 引擎, 说话人, 时间}"""
    lines = ["# 逐字稿", ""]
    lines.append("| 项目 | 内容 |")
    lines.append("| --- | --- |")
    lines.append(f"| 音频 | {meta['音频']} |")
    lines.append(f"| 时长 | {meta['时长']} |")
    lines.append(f"| 识别引擎 | {meta['引擎']} |")
    lines.append(f"| 说话人分离 | {meta['说话人']} |")
    lines.append(f"| 识别时间 | {meta['时间']} |")
    lines.append("")

    if not sentences:
        lines.append("_未识别到有效语音内容。_")
        return "\n".join(lines)

    # 连续同发言人合并为一段
    blocks = []
    for speaker, start, end, text in sentences:
        if blocks and blocks[-1]["speaker"] == speaker:
            b = blocks[-1]
            b["end"] = max(b["end"], end)
            b["texts"].append(text)
        else:
            blocks.append({"speaker": speaker, "start": start,
                           "end": end, "texts": [text]})

    for i, b in enumerate(blocks, 1):
        lines.append(f"## 发言人 {i} (SpeakerId={b['speaker']})")
        lines.append("")
        lines.append(f"> {fmt_ts(b['start'])} - {fmt_ts(b['end'])}")
        lines.append("")
        lines.append("".join(b["texts"]))
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="腾讯云录音文件识别: 音频 -> 逐字稿 (markdown)")
    ap.add_argument("input", help="音频文件 (自动上传 COS) 或 http(s) URL")
    ap.add_argument("--output", "-o", default=None, help="输出 .md 路径 (默认 <音频名>.md)")
    ap.add_argument("--config", default="asr_config.toml", help="配置文件路径 (默认 asr_config.toml)")
    ap.add_argument("--speakers", type=int, default=None,
                    help="指定说话人数 1-10 (默认 0=自动分离)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    asr_cfg = dict(cfg["asr"])
    if args.speakers is not None:
        asr_cfg["speaker_number"] = args.speakers

    if is_url(args.input):
        url = args.input
        log(f"直接使用音频 URL: {url}")
    else:
        if not os.path.isfile(args.input):
            sys.exit(f"找不到输入文件: {args.input}")
        if not cfg["cos"].get("bucket"):
            sys.exit("本地文件需要上传 COS, 请在配置中填写 [cos].bucket (或直接传音频 URL)。")
        url = upload_to_cos(cfg, args.input)

    # 腾讯云 SDK (导入放这里, 便于不装 SDK 时也能看帮助/错误)
    try:
        import warnings
        # 忽略 SDK 在 Python 3.14 下的无害 SyntaxWarning
        warnings.filterwarnings("ignore", category=SyntaxWarning)
        from tencentcloud.common import credential as tc_cred
        from tencentcloud.common.exception import TencentCloudSDKException
        from tencentcloud.asr.v20190614 import asr_client, models
    except ImportError:
        sys.exit("缺少腾讯云 SDK, 请先安装: uv add tencentcloud-sdk-python-asr")

    cred = tc_cred.Credential(cfg["credentials"]["secret_id"],
                              cfg["credentials"]["secret_key"])
    client = asr_client.AsrClient(cred, cfg["credentials"]["region"])

    try:
        task_id = submit_task(client, models, url, asr_cfg)
        ok, result_detail, legacy_result, err = poll_task(client, models, task_id, asr_cfg)
    except TencentCloudSDKException as e:
        sys.exit(f"腾讯云接口调用失败: [{e.code}] {e.message}")
    if not ok:
        sys.exit(f"识别失败: {err}")

    # 原始结果存一份, 便于排查 (新版 JSON 或旧版文本格式)
    out_md = args.output or (os.path.splitext(args.input)[0] + ".md")
    if result_detail:
        raw_path = os.path.splitext(out_md)[0] + ".raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(json.loads(result_detail), f, ensure_ascii=False, indent=2)
    else:
        raw_path = os.path.splitext(out_md)[0] + ".raw.txt"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(legacy_result or "")
    log(f"原始结果已保存: {raw_path}")

    # 新版 JSON 或旧版文本格式, 统一解析成句子列表
    sentences = (parse_result_detail(result_detail) if result_detail
                 else parse_legacy_result(legacy_result))
    meta = {
        "音频": args.input,
        "时长": fmt_ts(max(s[2] for s in sentences)) if sentences else "-",
        "引擎": asr_cfg["engine_model_type"],
        "说话人": ("自动分离" if asr_cfg["speaker_number"] == 0
                   else f"{asr_cfg['speaker_number']} 人"),
        "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    md = to_markdown(sentences, meta)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"逐字稿已生成: {out_md} ({len(sentences)} 句)")


if __name__ == "__main__":
    main()
