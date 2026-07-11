# toolbox

个人视频处理工具箱。

## split_video — 视频按时长拆分

某些视频处理软件不支持 30 分钟以上的视频。本工具可把长视频按指定时长上限拆分为多段（默认 30 分钟），基于 `ffmpeg` 的流复制，**不重新编码**，速度快、画质零损失。

### 环境要求

- Python 3.9+
- `ffmpeg` / `ffprobe`（需位于 `PATH` 中）

### 使用 uv 管理依赖与运行

```powershell
# 初始化/同步虚拟环境（由 pyproject.toml + uv.lock 锁定）
uv sync

# 运行（脚本已注册为命令行入口 split-video）
uv run split-video 视频.mp4
uv run split-video 视频.mp4 --max-minutes 25
uv run split-video 视频.mp4 --output-dir out
```

不借助 uv 也可直接运行：

```powershell
python split_video.py 视频.mp4
```

### 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `input` | 输入视频文件（必填） | — |
| `--max-minutes` | 每段最长分钟数 | `30` |
| `--buffer` | 安全余量秒数（保证每段不超上限，因拆分发生在关键帧处） | `30` |
| `--output-dir` | 输出目录 | `<视频名>_parts` |

### 说明

- 拆分点落在关键帧（keyframe）处，因此实际时长会略短于上限；脚本结束会自动校验每段是否都在上限内。
- 若个别视频关键帧间隔过大导致某段超长，调小 `--max-minutes` 重试即可。
- 处理结果输出到 `<视频名>_parts/` 目录，文件名形如 `..._part000.mp4`、`..._part001.mp4`。

### 项目结构

```
split_video.py    # 拆分脚本
pyproject.toml    # uv 工程配置（含 split-video 命令行入口）
uv.lock           # 锁定的依赖
```
