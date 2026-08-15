# toolbox

个人音视频处理工具箱。

## split_video — 视频按时长拆分

某些视频处理软件不支持 30 分钟以上的视频。本工具可把长视频按指定时长上限拆分为多段（默认 30 分钟），基于 `ffmpeg` 的流复制，**不重新编码**，速度快、画质零损失。

### 特点

- 🚀 **零画质损失**：`-c copy` 流复制，不重新编码，处理 1 小时视频只需几秒
- ✅ **自动校验**：拆分后自动检查每段时长，超长的片段会明确告警
- 🎯 **拆分点精确**：落在关键帧（keyframe）处，保证每段可正常播放
- 📦 **零 Python 依赖**：只依赖系统 `ffmpeg` / `ffprobe`

### 环境要求

- Python 3.9+
- `ffmpeg` / `ffprobe`（需位于 `PATH` 中）

安装 ffmpeg：

```bash
# macOS (Homebrew)
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (winget 或 choco)
winget install ffmpeg
```

### 使用 uv 管理依赖与运行

```bash
# 初始化/同步虚拟环境（由 pyproject.toml + uv.lock 锁定）
uv sync

# 运行（脚本已注册为命令行入口 split-video）
uv run split-video 视频.mp4
uv run split-video 视频.mp4 --max-minutes 25
uv run split-video 视频.mp4 --output-dir out
```

不借助 uv 也可直接运行：

```bash
python split_video.py 视频.mp4
```

### 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `input` | 输入视频文件（必填） | — |
| `--max-minutes` | 每段最长分钟数 | `30` |
| `--buffer` | 安全余量秒数（保证每段不超上限，因拆分发生在关键帧处） | `30` |
| `--output-dir` | 输出目录 | `<视频名>_parts` |

### 运行示例

```text
$ python split_video.py demo.mp4 --max-minutes 1
视频总时长: 185.3 秒 (3.09 分钟)
将拆分为 4 段 (每段目标 ~30 秒)
> ffmpeg -y -i demo.mp4 -f segment -segment_time 30 -reset_timestamps 1 -c copy demo_part%03d.mp4

拆分结果:
  demo_part000.mp4: 0.50 分钟  [OK]
  demo_part001.mp4: 0.50 分钟  [OK]
  demo_part002.mp4: 0.50 分钟  [OK]
  demo_part003.mp4: 0.35 分钟  [OK]
全部片段均在时长上限内。
```

### 说明

- 拆分点落在关键帧（keyframe）处，因此实际时长会略短于上限；脚本结束会自动校验每段是否都在上限内。
- 若个别视频关键帧间隔过大导致某段超长，调小 `--max-minutes` 重试即可。
- 处理结果输出到 `<视频名>_parts/` 目录，文件名形如 `..._part000.mp4`、`..._part001.mp4`。

### 常见问题

| 问题 | 解决 |
| --- | --- |
| `ffprobe: command not found` | 未安装 ffmpeg 或未加入 `PATH`，见上文「环境要求」 |
| `找不到输入文件: xxx.mp4` | 检查路径与文件名（含中文/空格建议加引号） |
| 某段出现 `超长!` 警告 | 视频关键帧间隔过大，减小 `--max-minutes` 重试 |
| `buffer 过大` 报错 | `--buffer` 大于 `--max-minutes` 对应秒数，调小 buffer 或调大 max-minutes |

## convert_audio — 音频格式转换 (m4a 转 mp3)

把 m4a 音频转换为 mp3（使用 `libmp3lame` 编码），保留原文件元数据（标题、艺术家等），方便在不支持 m4a 的软件/设备上播放。

### 使用 uv 运行

```bash
uv run convert-audio 音频.m4a
uv run convert-audio 音频.m4a --bitrate 320k
uv run convert-audio *.m4a --output-dir mp3/
uv run convert-audio 音乐目录/          # 转换目录下所有 .m4a
```

不借助 uv 也可直接运行：

```bash
python convert_audio.py 音频.m4a
```

### 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `inputs` | 输入文件或目录（可多个，目录会转换其中所有 `.m4a`） | 必填 |
| `--bitrate` | mp3 比特率 | `192k` |
| `--output-dir` | 输出目录 | 与源文件同目录 |

### 运行示例

```text
$ python convert_audio.py demo.m4a --bitrate 128k
> ffmpeg -y -i demo.m4a -vn -c:a libmp3lame -b:a 128k -map_metadata 0 -id3v2_version 3 demo.mp3
转换完成: demo.m4a -> demo.mp3
```

### 说明

- 输出文件与原文件共存（同名不同扩展名），**不删除原文件**。
- 转换保留原文件元数据（标题/艺术家/专辑等），并写入兼容性好的 ID3v2.3 标签。
- 默认忽略封面图（`-vn`），只保留音轨。
- 除 m4a 外，也可直接指定任意 ffmpeg 支持的音频/视频文件（如 `.wav`/`.flac`/`.mp4`），只取其音轨转成 mp3。

### 项目结构

```
split_video.py    # 视频按时长拆分
convert_audio.py  # 音频格式转换 (m4a -> mp3)
pyproject.toml    # uv 工程配置（含 split-video / convert-audio 命令行入口）
uv.lock           # 锁定的依赖
README.md         # 本说明
```
