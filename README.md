# 长素材选句工作台

面向 Windows 本机单人使用的长视频选句工具：导入素材后自动转写音频，通过带时间定位的文稿挑选内容，人工分组、排序、命名，再导出 MP4 短视频。

当前定位是可运行的本机验证版，主要适用于单人口播。云端转写使用阿里云百炼录音模型 `paraformer-v2`；视频预览、项目保存和成片导出在本地完成。

已增加 Windows x64 免安装测试包的构建支持，内置 Python、FFmpeg、FFprobe 和界面资源；用户各自填写自己的百炼 Key。源码部署与免安装包使用独立的默认数据目录。

```text
导入原视频 → 提取音频并自动转写 → 从文稿选句
                                  ↓
                    人工分组、拖动排序、命名
                                  ↓
                            MP4

完整转写文稿也可单独导出为 TXT。
```

## 目录

- [当前功能](#当前功能)
- [Windows 免安装测试包](#windows-免安装测试包)
- [环境与安装](#环境与安装)
- [启动工作台](#启动工作台)
- [使用流程](#使用流程)
- [导出规则](#导出规则)
- [数据与密钥](#数据与密钥)
- [开发与测试](#开发与测试)
- [常见问题与限制](#常见问题与限制)
- [代码结构与接口](#代码结构与接口)

## 当前功能

| 模块 | 已实现能力 |
| --- | --- |
| 素材 | 系统文件窗口导入、一次选择多个视频、切换本机项目、相同原文件复用原项目 |
| 转写 | 导入后自动排队转写完整音频；已有文稿直接复用，保留人工修正 |
| 文稿 | 按句显示原文序号与时间、搜索、点击定位播放、修正显示文字 |
| 选择 | 单句勾选、Shift 连续选择、按序号批量选择，如 `1-5,8,12-15` |
| 分组 | 人工创建短片分组、导出前命名、组内拖动重排、移除片段、会话内撤销 |
| 边界 | 按原视频秒数手动调整片段起止位置，支持按组预览 |
| 导出 | 当前组或全部非空分组仅导出 MP4；完整文稿通过“导出文稿”单独导出 TXT |
| 位置 | 视频、文稿各自设置文件夹；点击“更改”打开系统选择窗口，选定后自动保存 |
| 任务 | 顶部“后台任务”按需展示队列、进度、错误、刷新与取消操作 |

未实现自动剪除气口、口误、语气词和冗余语句。跨原视频混剪、自动选精彩片段、智能裁切和后续精剪也不在当前范围内。

## Windows 免安装测试包

当前构建版本为 `0.2.0-test.3`，视频导出仅生成 MP4，并包含 C 盘缓存、D 盘导出等跨盘场景下的 `[WinError 17]` 修复。完整解压 `LongVideoEditor-0.2.0-test.3-windows-x64.zip` 后，双击其中的 `启动工作台.cmd`；浏览器会自动打开工作台。在“设置”中填写自己的百炼 Key，选择输出目录后即可导入素材。无需安装 Python 或 Git。完整说明见 [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md)。

已发布的 Windows x64 测试包、SHA-256 校验文件与版本说明见 [GitHub Releases](https://github.com/J0n-Pan/longvideo-editor/releases)。请以发布页实际提供的附件版本为准；仓库中的版本号不表示对应程序包已发布。

ZIP 是程序文件夹的压缩包，不是单文件安装向导；请完整解压并保留 `_internal`。程序资源随包提供，转写仍需要网络和百炼服务。安装包单独构建，不包含在仓库自动生成的源码 ZIP 中。

免安装包将数据库、缓存和设置放在 `%LOCALAPPDATA%/LongVideoEditor`，默认视频输出在当前用户的 `Videos/LongVideoEditor`，文稿在 `Documents/LongVideoEditor`。更新前退出服务，再完整解压新版，用户数据可继续使用。包内不会附带作者的密钥、素材、项目或导出结果。

免安装版使用独立的 Windows 凭据名称，不会自动继承当前源码测试环境的密钥或项目。需要迁移自己的旧项目时，另行备份并迁移数据，不要把自己的数据库放入分发包。

## 环境与安装

以下步骤面向源码运行；免安装包用户直接按上面的说明操作。

### 1. 环境要求

- Windows 桌面会话，当前已验证 Python 3.12。
- Python 安装包含 `tkinter`，用于系统文件和文件夹选择窗口。
- FFmpeg 与 FFprobe。当前导出使用 CPU 上的 `libx264`、AAC；音频预处理使用 `libmp3lame`。
- 浏览器可访问本机 `http://127.0.0.1:8765/`。
- 自动转写需要可用的百炼 API Key 和相应的服务权限。

Python 依赖固定在 [requirements-lock.txt](requirements-lock.txt)。FFmpeg 二进制、Python 环境和运行数据不包含在 Git 仓库中。

### 2. 按当前目录约定克隆

当前程序使用“工作区根目录 / `outputs/longvideo-editor`”的布局。请按以下结构放置，启动脚本、工具配置和默认数据目录都依赖此约定：

```text
LongVideoWorkspace/
├─ outputs/
│  ├─ longvideo-editor/        # 本 Git 仓库
│  ├─ short-clips/            # 默认 MP4 输出，不入库
│  └─ transcripts/            # 默认 TXT 文稿输出，不入库
└─ work/
   ├─ longvideo-venv/          # Python 虚拟环境，不入库
   ├─ longvideo-tools/
   │  └─ paths.json           # 本机 FFmpeg / FFprobe 路径，不入库
   └─ longvideo-editor-data/   # SQLite、音频缓存、云任务记录、临时文件，不入库
```

以下 PowerShell 示例使用 `D:\LongVideoWorkspace`，可替换为自己的工作区：

```powershell
$workspaceRoot = 'D:\LongVideoWorkspace'
New-Item -ItemType Directory -Force -Path "$workspaceRoot\outputs", "$workspaceRoot\work" | Out-Null
git clone https://github.com/J0n-Pan/longvideo-editor.git "$workspaceRoot\outputs\longvideo-editor"

py -3.12 -m venv "$workspaceRoot\work\longvideo-venv"
& "$workspaceRoot\work\longvideo-venv\Scripts\python.exe" -m pip install -r "$workspaceRoot\outputs\longvideo-editor\requirements-lock.txt"
```

源码运行仍依赖上述工作区布局。直接克隆到任意层级后使用源码启动器，可能使默认数据目录和启动脚本指向错误位置；免安装包不依赖这个布局。

### 3. 配置 FFmpeg 与 FFprobe

准备包含这两个可执行文件的 FFmpeg 构建，例如从 [Gyan Windows 构建分发站](https://www.gyan.dev/ffmpeg/builds/)获取。将实际路径写入 `work/longvideo-tools/paths.json`：

```json
{
  "ffmpeg": "D:/ffmpeg/bin/ffmpeg.exe",
  "ffprobe": "D:/ffmpeg/bin/ffprobe.exe"
}
```

也可继续在 PowerShell 中创建配置。下面示例使用无 BOM 的 UTF-8，避免 JSON 读取失败：

```powershell
$toolsFolder = Join-Path $workspaceRoot 'work\longvideo-tools'
New-Item -ItemType Directory -Force -Path $toolsFolder | Out-Null
$toolConfig = @{
    ffmpeg = 'D:\ffmpeg\bin\ffmpeg.exe'
    ffprobe = 'D:\ffmpeg\bin\ffprobe.exe'
} | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $toolsFolder 'paths.json'), $toolConfig, $utf8NoBom)
```

修改示例路径后，可执行 `ffmpeg.exe -version`、`ffprobe.exe -version` 检查二进制是否可用。程序有 `imageio-ffmpeg` 回退路径，但完整安装应显式配置 FFprobe，以正确读取方向、时长和音视频轨信息。

## 启动工作台

1. 在 **Windows 资源管理器**中进入仓库目录。
2. 双击 `start.cmd`。
3. 启动器打开浏览器，访问 `http://127.0.0.1:8765/`。

启动窗口关闭后服务停止，运行日志显示在该窗口中。重复双击会复用已运行的当前版本；发现同一工作区的旧版本时，只会在确认服务身份、端口及后台任务空闲后切换新版。有排队或运行中的任务时会停止切换并给出提示。

已有本机部署可能还提供上一级的“启动工作台.cmd”便捷入口；它不属于本仓库。新克隆的代码直接使用仓库中的 `start.cmd` 即可。

文件和文件夹窗口需要服务运行在自己的 Windows 桌面中。从隔离桌面或无交互会话启动时，页面会提示重新从资源管理器启动，避免等待不可见窗口。

## 使用流程

### 1. 设置密钥和导出位置

点击右上角“设置”：

- 在百炼 API Key 框中填写自己的密钥并保存。设置页显示实际存储方式，密钥不会回显。
- “检查连接”验证临时音频上传授权是否可用，不代表完整转写质量已经验证。
- “视频导出位置”和“文稿导出位置”分别点击“更改”，通过系统文件夹窗口选择目录。选定后检查可写并自动保存；取消选择保留原设置。

两个输出位置独立保存到本机数据库，服务重启后继续生效。改变位置不会移动原目录中的文件。

### 2. 导入素材并等待文稿

点击顶部“导入素材”，选择一个或多个原视频。导入不会复制或修改原视频；顶部下拉框可切换已导入的项目。

- 没有文稿的新素材：自动提取音频并进入转写队列。
- 尚未配置密钥：项目等待密钥；保存密钥后自动继续已等待的转写。
- 已有文稿的素材：直接复用文稿、人工修正和原分组。
- 同一素材已在队列中或正在转写：复用当前任务，不重复提交。

当前预处理格式为 16kHz、单声道、64kbps MP3。原视频保留本地，识别音频上传到百炼临时存储。后台转写完成后，当前素材的文稿会自动更新。页面没有手动整条转写或短样转写按钮。

### 3. 从文稿选句

文稿中的数字是原文序号，搜索筛选和分组排序都不会重新编号：

1. 点击句子文字，原片跳转到该句附近播放。
2. 勾选需要的整句；Shift 勾选可连续选择。
3. 也可打开“批量选择”，输入 `1-5,8,12-15` 这样的连续或间隔序号。
4. 点击“加入当前组”。已经加入当前组的同一句子会去重。

批量选择保留已经勾选的句子，重复序号自动合并，错误范围会给出提示。“清除选择”用于重新开始；当前没有“全选”或“反选”入口。

句子右侧的修正按钮修改显示文字和导出文稿的文字，原声、句子定位及字词时间戳不随文字修改而变化。

### 4. 整理短片分组

- 点击“＋ 新建”创建短片分组。
- 在“导出名称”中命名；该名称用于输出文件名。
- 拖动组内片段调整成片顺序，原句序号继续保留。
- 点击移除按钮删除组内片段；“撤销”恢复当前页面会话内的结构修改。
- “调整边界”按原片秒数修改起止时间。
- “预览当前组”按组内顺序跳播原视频，用于确认内容和顺序。

分组名称、选择、排序和边界自动保存到本机 SQLite；撤销历史只保留在当前页面会话中。组预览不是生成好的成片，最终切点应以正式导出结果为准。

### 5. 导出与查看任务

“导出当前组”生成一条短片；“导出全部组”处理全部非空分组。“查看导出清单”可预览实际文件名和计划时长。

顶部“后台任务”打开独立弹窗，展示状态、进度、错误、刷新和取消操作。关闭弹窗不会停止任务；运行任务数显示在按钮旁。后台视频导出期间可继续整理分组。

转写和视频导出共用本机串行队列。取消已经提交的云转写只停止本地等待，云端任务仍可能继续并计费。

## 导出规则

### 视频

每个非空分组仅输出一个 MP4 文件：

| 文件 | 内容 |
| --- | --- |
| `.mp4` | H.264 视频、AAC 音频，保留原显示方向与画幅，不烧录字幕 |

视频导出不会额外生成 SRT 字幕或 JSON 文件。旧版本已经生成的配套文件保留原样，不会自动清理；项目、文稿和分组仍保存在本机数据库中。需要完整文稿时，单独点击“导出文稿”。

组内顺序决定成片顺序。相邻且保持原顺序的原句合并为连续区段，保留中间停顿。为降低切掉字头字尾的风险，边界默认前后预留约 80/120ms，并贴近视频帧边界；这不等于自动精剪。

非法文件名字符会替换，Windows 保留名会处理，遇到同名结果自动追加编号。已经排队的导出固定使用提交任务时的目录；之后更改视频导出位置只影响新任务。“已导出”列表显示当前设置目录中的 MP4。

### 完整文稿

文稿栏右上角“导出文稿”输出 UTF-8（带 BOM）的 TXT，包含标题、句子数量、原句序号、源视频时间范围和已修正的文字。例如：

```text
示例口播
原视频：示例口播.mp4
共 2 句

1. [00:00:01.100 --> 00:00:04.300] 这是第一句话。

2. [00:00:05.000 --> 00:00:08.200] 这是修正后的第二句话。
```

文稿按原文顺序完整导出，搜索过滤、当前勾选和短片分组不会缩小导出范围。无文稿时按钮不可用。重复导出自动追加编号，保留已有文件。

## 数据与密钥

Git 仓库只保存源码、测试代码、依赖清单、README 和必要的界面资源；不包含原视频、处理结果或运行环境。

| 数据 | 存放位置或处理方式 | 提交到 GitHub |
| --- | --- | --- |
| API Key | 优先 Windows 凭据管理器；其次用户级 DPAPI；均不可用时仅当前服务内存 | 否 |
| 原视频 | 保留导入时的原路径，不复制进仓库 | 否 |
| 音频与识别缓存 | `work/longvideo-editor-data/audio/`、`asr/` | 否 |
| 项目、文字修正、分组与设置 | `work/longvideo-editor-data/editor.sqlite3` | 否 |
| 渲染临时文件 | `work/longvideo-editor-data/jobs/` | 否 |
| MP4 成片 | 用户选择的视频输出目录 | 否 |
| TXT 文稿 | 用户选择的文稿输出目录 | 否 |
| 截图、日志与验证报告 | 本机任务目录或启动控制台 | 否 |

DPAPI 回退文件为本机数据目录中的 `bailian-key.dpapi`。内存方式在服务重启后需要重新填写。程序也支持 `DASHSCOPE_API_KEY` 进程环境变量，但不会自动读取 `.env` 文件。

备份项目时需要同时保存本机数据目录和原视频路径关系。数据库、TXT 文稿及旧版导出的 JSON 可能包含原文件路径或文稿内容，应作为自己的业务数据管理。

### 云端调用与预算

转写使用百炼临时音频存储，当前实现按 48 小时临时存储流程接入。相同音频和配置优先使用本地结果缓存，已提交任务保留任务记录并优先恢复查询。

代码中的本轮估算上限为 `BUDGET = 10.0`，估算费率固定为每秒 `0.00008` 元。这是本程序的防误调用计算规则，不是实时价格查询，也不是云端硬性消费限额；实际账单以百炼为准。记录跨服务重启保留，不按日期自动清零。

## 开发与测试

### 自动化检查

在上述工作区根目录执行：

```powershell
$testTemp = Join-Path $workspaceRoot ('work\pytest-' + [guid]::NewGuid().ToString('N'))
& "$workspaceRoot\work\longvideo-venv\Scripts\python.exe" -X utf8 -m pytest "$workspaceRoot\outputs\longvideo-editor\tests" -q -p no:cacheprovider --basetemp $testTemp
```

自动化检查覆盖分组顺序、边界、文件命名、凭据回退、文件选择、自动转写流程、预算保护、视频与文稿位置保存、TXT 编码与并发防覆盖，以及免安装包资源路径、用户数据隔离和打包后的文件选择命令等。视频导出回归检查包括仅生成 MP4、同盘导出、模拟 Windows 跨盘重命名错误、已有文件防覆盖、历史配套文件保留，以及合并失败和取消时清理临时文件。云端响应在自动化测试中模拟，不发起付费识别。

### 本地音视频集成检查

```powershell
& "$workspaceRoot\work\longvideo-venv\Scripts\python.exe" -X utf8 "$workspaceRoot\outputs\longvideo-editor\tests\verify_local_integration.py"
```

该检查使用合成色块、测试音和模拟文稿，验证真实 FFmpeg 仅导出 MP4、片段重排后的画面与音频频率、实际时长、自定义输出目录和文件读取。每次使用独立测试目录，数据写入 `work/integration-data/`、`work/integration-exports/`，不调用云转写，也不使用业务视频。

### 构建 Windows 免安装包

在 Windows x64 的开发 Python 环境中安装运行依赖和 `requirements-build.in` 中固定的构建工具，然后执行：

```powershell
python -m pip install -r requirements-lock.txt -r requirements-build.in
python tools/build_windows.py --ffmpeg 'D:\ffmpeg\bin\ffmpeg.exe' --ffprobe 'D:\ffmpeg\bin\ffprobe.exe'
```

构建器沿用本仓库的工作区布局，将临时文件放入 `work/portable-build/`，每次生成新的程序目录；ZIP 和 SHA-256 校验文件输出到工作区 `outputs/`。当前 FFmpeg 资源采用 Gyan 构建，构建器要求其 `bin/` 上一级保留原始 `LICENSE`、`README.txt`，并将这些材料随包提供。

构建器使用明确列出的界面资源和 FFmpeg/FFprobe，不收集工作区数据，不打包 `imageio-ffmpeg` 的重复二进制或测试工具。压缩前执行 EXE 自检、检查业务素材和数据库类型文件，并生成程序文件清单。自检不读取密钥、不请求转写；实际分发前仍需其他 Windows 电脑验证系统选择窗口和完整操作流程。

### 配置入口

| 配置 | 作用 |
| --- | --- |
| `LONGVIDEO_DATA` | 覆盖本机数据库和缓存目录 |
| `LONGVIDEO_EXPORTS` | 设置默认视频输出目录 |
| `LONGVIDEO_TRANSCRIPTS` | 设置默认文稿输出目录 |
| `LONGVIDEO_PORT` | 桌面入口使用的本机端口，默认 `8765` |
| `work/longvideo-tools/paths.json` | 指定 FFmpeg、FFprobe 路径 |
| `version.py` | 桌面启动器识别本机运行版本 |

界面中已经保存的视频、文稿输出位置优先于对应环境变量的默认值。现有服务修改 Python 后端后需要重新加载；在后台任务空闲时双击 `start.cmd`，启动器按版本检查是否切换。界面静态文件修改后刷新浏览器。

## 常见问题与限制

| 现象 | 处理与说明 |
| --- | --- |
| 双击提示找不到 Python 环境 | 检查仓库是否处于约定层级，以及 `work/longvideo-venv/Scripts/python.exe` 是否存在 |
| 导入或“更改”后没有系统窗口 | 确保从自己的 Windows 桌面启动服务，检查页面是否提示隔离桌面、已有选择窗口或选择超时 |
| 导入后仍无文稿 | 查看“后台任务”和密钥配置；没有密钥时保存后自动继续；失败或取消后重新导入尚无文稿的原素材可重试 |
| 重试仍返回原云任务失败 | 任务记录会复用；需要先排查百炼返回的错误，当前没有界面一键清理失败云任务记录的入口 |
| 文稿文字不准确 | 使用句子修正功能；ASR 对专名、中英混说、低声字尾和连读可能识别不准，修正文字不会改变原声 |
| 视频无法在浏览器预览 | 可导入的容器不等于浏览器支持其全部编码；当前主要验证 H.264 口播素材，其他编码需另测 |
| 导出提示源文件变化 | 原片路径、大小和修改时间参与校验；移动或替换后请重新导入，避免使用旧时间轴 |
| 导出报 `[WinError 17]` 无法移到不同磁盘 | 更新到 `0.2.0-test.2` 或后续版本后重新导出已有分组，无需重新转写；旧版可临时把导出目录改到缓存所在磁盘 |
| 更改输出位置后原成片不见了 | 原文件仍在旧目录，列表仅展示当前设置目录；应用不会自动搬迁文件 |
| 选句时长与成片不完全一致 | 自动边界余量、视频帧对齐和原句间停顿会影响实际时长 |

当前还存在这些边界：

- 一个项目对应一个原视频，不能跨文件混剪。
- HDR 素材会明确拒绝导出，需要后续单独配置色彩处理。
- 字词时间戳用于定位，不构成逐帧准确保证，成片边界需要人工听看。
- 部分转码阶段取消不会立即生效，已完成文件会保留。
- 本机串行作业需要足够的临时磁盘空间，音频和识别缓存没有自动清理界面。
- 文件夹窗口和凭据存储依赖 Windows 桌面环境；当前没有容器部署、开机自启或多人协作能力。
- 服务只绑定本机回环地址，没有面向多用户的账号与权限系统。

## 代码结构与接口

```text
app.py                    FastAPI 路由、导入、自动转写编排、后台队列、导出设置
asr.py                    百炼上传、提交、轮询、结果缓存与云任务恢复
credentials.py            Windows 凭据管理器 / 用户级 DPAPI / 内存回退
storage.py                SQLite、数据目录和独立输出位置设置
runtime_paths.py          源码与免安装包的资源、用户数据和 Tcl/Tk 路径
domain.py                 时间轴、句子身份、片段组合和安全文件名
media.py                  FFprobe、音频提取、FFmpeg 渲染与源文件校验
transcript_export.py      完整 TXT 文稿与同名防覆盖
file_picker.py            桌面检测、视频和文件夹选择窗口
desktop_start.py          桌面启动、版本与端口检查、空闲旧服务切换
static/                   HTML、CSS、原生 JavaScript、个人 logo
tests/                    自动化检查与本地音视频集成脚本
tools/build_windows.py    独立运行包构建、资源筛选、自检和 ZIP 压缩
WINDOWS-QUICKSTART.md      免安装包用户说明
```

主要本机接口：

| 方法与路径 | 功能 |
| --- | --- |
| `GET /api/info` | 当前版本、密钥是否配置、桌面窗口能力、输出位置；不返回密钥 |
| `GET /api/projects` | 已导入项目列表 |
| `POST /api/import/pick` | 打开原视频选择窗口并导入，随后自动转写 |
| `PUT /api/projects/{id}/groups` | 保存分组 |
| `PUT /api/projects/{id}/sentence/{sid}` | 修正显示文字 |
| `POST /api/projects/{id}/export-plan` | 预览短片导出计划 |
| `POST /api/projects/{id}/export` | 排队导出短片 |
| `POST /api/projects/{id}/transcript/export` | 保存完整 TXT 文稿 |
| `POST /api/settings/folder/pick` | 选择视频或文稿输出目录，参数 `kind` 为 `video` 或 `transcript` |
| `GET /api/jobs` | 后台任务列表 |
| `POST /api/jobs/{id}/cancel` | 请求取消任务 |

写操作要求 `X-Longvideo-Client: 1`；提供 Origin 时必须与本机服务地址一致。前端已处理这些要求。旧的手动转写 API 已移除；路径导入和视频目录写入接口仅作为现有脚本兼容入口保留。

### 依赖、来源与许可证

主要依赖及来源见 [dependency-manifest.json](dependency-manifest.json)，精确 Python 版本见 [requirements-lock.txt](requirements-lock.txt)。FFmpeg 构建应保留随发行包附带的许可信息；本仓库不分发 FFmpeg 二进制。

项目自身的开源许可证尚待作者确定。个人 logo 为项目界面资源。

接口参考：[百炼录音 REST API](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-restful-api)、[临时音频上传](https://help.aliyun.com/zh/model-studio/get-temporary-file-url)、[Windows 用户级 DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。
