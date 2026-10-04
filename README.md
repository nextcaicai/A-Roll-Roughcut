# 口播粗剪

把口播里的重说和卡顿剪掉，留下句间该留的气口，再由人听一遍定稿。定稿后可以导出按剪后时间对齐的字幕，以及一条给剪映用的成片。

你准备源视频和口播稿。在 Cursor、Claude Code 或 Codex 里打开这个仓库，Agent 按 [koubo-roughcut/SKILL.md](koubo-roughcut/SKILL.md) 做转写、对照和取舍。切口写在 `cut_decisions.json` 里，人在浏览器里改。

## 能完成什么

- 划掉失败开头和整段重说，句间换气留下
- 浏览器里直接听剪后效果，恢复误删或再删一句
- 导出按剪后时间对齐的 SRT，以及一条固定帧率 mp4

这一轮停在粗剪定稿。B-roll、音效、剪映草稿留到后面。

## 安装

命令都在**仓库根目录**执行。Agent 要打开的是这个仓库：目录、命名和密钥约定看这份 README，做粗剪时读 `koubo-roughcut/SKILL.md`。脚本从仓库根目录找 `koubo-roughcut/.venv` 和 `.env`。

装好之后，用检查命令确认。终端打印 `ok` 就可以开工。

| 依赖 | 做什么 | 怎么确认 |
|---|---|---|
| ffmpeg、ffprobe | 抽音频、渲染成片 | `ffmpeg -version` 和 `ffprobe -version` 能打出版本 |
| Python 3 | 跑 `koubo-roughcut/scripts/` | `python3 --version`。本仓库在 Python 3.13 上用过 |
| 百炼 API Key | 把口播转成带时间的逐字稿 | 检查命令不再报缺少 `DASHSCOPE_API_KEY` |

### 方法一：让 Agent 装环境

Key 自己写进仓库根目录的 `.env`，不要贴进对话。在[阿里云百炼的 API Key 说明](https://help.aliyun.com/zh/model-studio/get-api-key)里创建或复制，Key 以 `sk-` 开头。中国大陆的 Key 对应默认接入点 `https://dashscope.aliyuncs.com`。`.env` 已在 `.gitignore` 里：

```bash
DASHSCOPE_API_KEY=sk-你的密钥
```

写好后，把下面这段发给已经打开本仓库的 Agent：

```text
把这个仓库的口播粗剪环境装好，装完停下，不要开始剪视频。缺什么就装什么，不要停下来问我要不要装。

- ffmpeg 或 ffprobe 不在 PATH 里就直接安装。macOS 用 Homebrew：brew install ffmpeg。这台机器还没有 Homebrew 时，先装 Homebrew，再装 ffmpeg
- 用 python3 在 koubo-roughcut/.venv 创建虚拟环境
- 只用这个 venv 的 python 安装 koubo-roughcut/scripts/requirements.txt
- 跑 koubo-roughcut/.venv/bin/python koubo-roughcut/scripts/setup_check.py，把结果告诉我
- 不要读取、打印或提交 .env 和任何密钥
- 不要修改源视频、脚本、成片目录，也不要删除无关文件
```

Agent 回复里出现 `ok` 后即可开工。若它停在缺少 `DASHSCOPE_API_KEY`，把上面的 `.env` 写好，再让它只重跑检查命令。

### 方法二：自己装

**1. 安装 ffmpeg**

macOS（Homebrew）：

```bash
brew install ffmpeg
```

其他系统装好后，保证终端里能直接运行 `ffmpeg` 和 `ffprobe`。

**2. 写入百炼 API Key**

写法与方法一相同：仓库根目录 `.env` 里一行 `DASHSCOPE_API_KEY=sk-你的密钥`。也可以只给当前终端：

```bash
export DASHSCOPE_API_KEY=sk-你的密钥
```

只有你明确要改用豆包录音文件识别 2.0 时，才再加 `DOUBAO_SPEECH_API_KEY`。默认转写用百炼 `paraformer-v2`。

**3. 建立 Python 环境并安装依赖**

```bash
python3 -m venv koubo-roughcut/.venv
koubo-roughcut/.venv/bin/python -m pip install -r koubo-roughcut/scripts/requirements.txt
```

`.venv` 不进 git，每台机器自己建一份。

**4. 检查**

```bash
koubo-roughcut/.venv/bin/python koubo-roughcut/scripts/setup_check.py
```

打印 `ok` 即完成。缺工具、缺包或缺 Key 时，会列出缺的名字。

## 剪一条口播

1. 在仓库里建一个成片目录，用你认得出的题目命名，例如 `如何学习AI？/`。
2. 把源视频放进去，文件名保持原样。
3. 在同一目录写 `脚本.md`。
4. 对 Agent 说：按口播粗剪，给「如何学习AI？」做第一轮粗剪。
5. 浏览器会打开审片页，地址是 `http://127.0.0.1:8765/`。打开就是剪后播放，倍速可以选 1.0、1.1、1.2、1.3。听完在页面上改切口，再导出字幕或成片。导出前先保存。

暂时没有口播稿也可以开工。Agent 会按口播本身去掉重复，并说明这次没有对过稿。

每次粗剪的结果在 `<成片名>/runs/<日期>/`。这个目录可以删，用源片、`脚本.md` 和 skill 能重做。

## 数据会送到哪里

审片只监听本机 `127.0.0.1`，页面不出这台电脑。

转写会离开本机，也只有这一步：

- 默认把本地音频交给阿里云百炼做录音文件识别。脚本访问 `https://dashscope.aliyuncs.com/api/v1` 申请上传、提交任务、取回逐字稿；音频文件传到百炼返回的临时地址。
- 只有你明确指定豆包时，才会请求 `https://openspeech.bytedance.com`。这一路要求音频本身已经是豆包服务能下载的地址。

密钥只放在环境变量或仓库根目录 `.env`。不要写进代码、skill、提交或审片记录。源视频、`.venv`、临时 wav、粗剪 mp4 不进 git。

仓库里没有单独的统计上报，也没有作者自己的服务器。换百炼地域时，可以设置 `DASHSCOPE_HTTP_BASE_URL`，未设置则使用上面的中国大陆接入点。

## 目录

| 路径 | 放什么 |
|---|---|
| `koubo-roughcut/` | 粗剪 skill |
| `koubo-roughcut/scripts/` | 转写、校验、审片、导出 |
| `<成片名>/脚本.md` | 口播稿 |
| `<成片名>/` 里的源视频 | 原文件，不改名 |
| `<成片名>/runs/<日期>/` | 这一次粗剪的产出 |

## 文档以哪份为准

安装和开工看这份 README。剪的步骤、取舍和交付物以 [koubo-roughcut/SKILL.md](koubo-roughcut/SKILL.md) 为准。规则为什么改，记在 [koubo-roughcut/note.md](koubo-roughcut/note.md)。

## 许可

[MIT](LICENSE)
