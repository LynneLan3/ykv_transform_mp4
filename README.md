# 优酷 YKV 转点唱机 MP4

将优酷客户端下载的 `.ykv` / `.kux` 容器解包并合并为标准 MP4，便于在点唱机等通用设备上播放。

## Windows 图形版（推荐）

1. 从 GitHub Release 或 Actions 构建产物下载 `YKVTransform-Setup-x64.exe`
2. 双击安装，从开始菜单或桌面打开 **YKV Transform**
3. 将 `.ykv` / `.kux` 文件或文件夹**拖入窗口**，或点击「添加文件 / 添加文件夹」
4. 点击 **开始转换**，MP4 会生成在**源文件同目录**

默认使用 **点唱机模式**（H.264 + AAC 重编码），兼容性更好。若更追求速度，可在界面切换为「快速模式」。

### Windows 界面功能

- 拖拽文件或文件夹批量添加
- 转换模式：点唱机 / 快速
- 可选「覆盖已存在的 MP4 文件」
- 日志区显示 VIP 警告、成功/失败原因

安装包已内置 FFmpeg，无需单独安装。

## 命令行版

### 依赖

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/)（需包含 `ffmpeg` 与 `ffprobe`）

macOS 安装示例：

```bash
brew install ffmpeg
```

### 用法

```bash
# 单文件（默认同目录输出同名 .mp4）
python main.py convert input.ykv

# 指定输出路径
python main.py convert input.ykv -o output.mp4

# 点唱机兼容模式（H.264 + AAC 重编码）
python main.py convert input.ykv -o output.mp4 --mode karaoke

# 批量转换（递归扫描子目录）
python main.py batch ./ykv_files -o ./mp4_out

# 覆盖已存在的输出
python main.py batch ./ykv_files -o ./mp4_out --force
```

### 启动 GUI（开发环境）

```bash
pip install -r requirements-build.txt
python gui_main.py
```

## 输出模式

| 模式 | 说明 |
|------|------|
| `copy` | 无损封装，速度最快，适合内嵌已是 H.264/AAC 的文件 |
| `karaoke` | 重编码为 H.264 Main + AAC，兼容性更好，适合老点唱机 |

## 退出码

| 码值 | 含义 |
|------|------|
| 0 | 成功 |
| 1 | 参数错误或输入无效 |
| 2 | YKV 解包失败 |
| 3 | FFmpeg 合并/校验失败 |

## Windows 安装包构建

在 **Windows** 机器或 CI 上执行：

```powershell
./packaging/build_windows.ps1
```

脚本会：

1. 下载 BtbN FFmpeg win64 到 `resources/ffmpeg/win64/`
2. 用 PyInstaller 打包 GUI 到 `dist/YKVTransform/`
3. 用 Inno Setup 生成 `dist/installer/YKVTransform-Setup-x64.exe`

依赖：

- Python 3.10+
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)（可选，用于生成安装包）

也可推送代码到 `main` 分支，由 GitHub Actions（`.github/workflows/build-windows.yml`）自动构建并上传产物。

### 推荐发布前流程（先本地预检，再推送构建）

```bash
# 一条命令跑完关键回归（失败即退出）
python3 scripts/preflight_local.py
```

通过后再推送分支并触发 Windows 构建，可显著减少“推送后才发现不能转换”的往返。

### FFmpeg 许可证

Windows 安装包内置的 FFmpeg 来自 [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)（GPL 构建）。分发时需遵守 GPL 要求；构建脚本会尝试复制 `LICENSE` 到 `resources/ffmpeg/win64/FFmpeg_LICENSE.txt`。

## 故障排查

### 解包失败：无法解析 YKV 尾部索引

- 确认文件来自优酷客户端，扩展名为 `.ykv` 或 `.kux`
- 文件可能已损坏或未下载完整，请重新下载

### 转换失败：FFmpeg 合并失败

- CLI：确认本机 `ffmpeg -version` 可用
- GUI：重新安装最新版（内置 FFmpeg）
- VIP/DRM 内容可能无法转换；工具会提示「检测到 VIP/付费内容标记」
- 尝试 `--mode karaoke` 或界面中的点唱机模式

### 输出 MP4 在电脑上能播，点唱机不能播

- 使用点唱机模式 / `--mode karaoke` 重新转换
- 确认点唱机支持的分辨率（如 720p/1080p）

### 批量转换跳过某些文件

- CLI 默认跳过已存在的同名 `.mp4`，加 `--force` 可覆盖
- GUI 勾选「覆盖已存在的 MP4 文件」

### 杀毒软件误报

PyInstaller 打包的 exe 可能被误报，可将安装目录加入信任列表。

### 调试中间分片

```bash
python main.py convert input.ykv --keep-temp
```

## 限制

- 仅处理优酷 YKV/KUX 容器解包，不能破解 DRM
- 部分付费/VIP 视频解包后仍可能无法播放

## 本地验证

```bash
python3 scripts/create_test_ykv.py /tmp/sample.ykv
python3 scripts/verify_service.py
python3 main.py convert /tmp/sample.ykv -o /tmp/out_copy.mp4 --mode copy
python3 main.py convert /tmp/sample.ykv -o /tmp/out_karaoke.mp4 --mode karaoke
ffprobe -show_streams /tmp/out_copy.mp4
python3 scripts/preflight_local.py
```

GUI 开发环境验证：

```bash
pip install -r requirements-build.txt
python gui_main.py
```

Windows 安装包需在 Windows 或 GitHub Actions 上构建后，在目标机器上安装并拖入 YKV 文件验证。

## 原理简述

1. 读取 YKV 文件尾部 16 字节，得到 JSON 索引长度
2. 解析索引中各分片的 `offset` / `size`
3. 提取分片；若以 `YK` 开头则去掉前 34 字节
4. 用 FFmpeg concat 合并为 MP4
