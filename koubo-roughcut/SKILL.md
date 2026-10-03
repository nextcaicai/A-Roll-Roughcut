---
name: koubo-roughcut
description: Use when the user wants 口播粗剪, 第一轮粗剪, 对照脚本粗剪, talking-head rough cut, 重说留最后一遍, 气口, or FCPXML for 达芬奇/剪映. Not for ChatCut-native editing, auto B-roll, or SFX.
version: 0.12.7
---

# 口播粗剪

把口播剪顺。决策写进 JSON。默认开浏览器审片面板给人把取舍定稿，打开就是剪后播放。要进达芬奇精剪，用命令行导出 FCPXML；要字幕，在面板导出按剪后时间对齐的 SRT。定稿后可以再导出一条固定帧率成片，给剪映直接配这份 SRT。改切口仍改 JSON。不要为了调气口重跑转写。

## 何时用

有口播源片，要去掉重说/卡顿、收气口，再交精剪。通常还有 `脚本.md`。

## 何时不用

画面已在达芬奇 / 剪映 / ChatCut 里开着要当场改；只要自动 B-roll 或配音效；源片不是口播。

## 输入

开工前三样都有着落再转写：

1. 源视频路径
2. `脚本.md`（没有就说一声，按口播自身去重，不假装对过稿）
3. 输出目录：`<成片名>/runs/<日期>/`

片内目录约定见仓库根目录 `README.md`。

机械步骤必须跑 `koubo-roughcut/scripts/`，不要现场拼 ffmpeg 或手写 XML。模型做修字、对照、取舍、残留复查。修字落盘用脚本，不许手改时间戳。

脚本一律用 `koubo-roughcut/.venv/bin/python`（不要用系统 python）。本机第一次：

```bash
koubo-roughcut/.venv/bin/python koubo-roughcut/scripts/setup_check.py
# 缺包：koubo-roughcut/.venv/bin/python -m pip install -r koubo-roughcut/scripts/requirements.txt
```

转写要 `DASHSCOPE_API_KEY`（环境变量或仓库根目录 `.env`，不要提交）。缺 ffmpeg 或缺 key 先补，不要改脚本去绕开。`paraformer-v2` 暂时用不了就停下来告诉用户。不要改用 realtime 模型，也不要启用本地 Whisper。

## 粗剪规则

对每一段口播只做这三件事：

1. **卡顿 / 半句重说**：划掉失败开头，留后说完整的那句。隔了一口气又把开头重说一遍，也删前面那截。
2. **整段重说**：同一内容又录一遍 → 删前面整段，留最后一遍，并在 `flags` 写明让人听。后遍个别句子更残，不因此改留前遍。只有后遍整段明显更残、信息更少，才留前遍。
3. **气口**：句间自然换气留下；词与词之间、以及一段 keep 头尾多出来的停顿，长于 `breath.maxKeep`（默认 `0.4s`）的压掉。不要把所有缝都压成 `0.4s`。keep **入点**默认在空隙内回退 `breath.leadIn`（`0.12s`），避免切在吸气峰上；不伸进已删的 drop。整段已经划掉的区间里，中间的气口、静音、卡顿一并去掉，只留还挨着留下的字的那口气。

不确定就进 `flags`，不删。不改音频。口播用词和脚本不同的，按口播留，不贴脚本。ASR 听错的字在删词之前修，只改判断用的文字。判据见 `references/semantic-deletion.md`。

## 流水线原则

**顺序固定：** 先修字 → 再少误删 → 机械收缝、清词级卡顿 → 通读复查 → 人定稿。

不要为了重说判定去调 Jev 或要 `TYPESAFE_API_KEY`。第 4 步取舍、词级卡顿、残留扫描和审片已经覆盖这类判断。

删词判断、词级卡顿、覆盖、残留都读 `transcript.corrected.json`。`transcript.json` 留原文。

## 步骤

`PY=koubo-roughcut/.venv/bin/python`，`SCRIPTS=koubo-roughcut/scripts`，`OUT=<成片名>/runs/<日期>`，`TR=$OUT/transcript.corrected.json`。

| 步 | 谁做 | 产出 |
|---|---|---|
| 1 转写 | 脚本 | `transcript.json` / `.md` |
| 2 修字 | 模型出表，脚本落字 | `corrections.*`、`transcript.corrected.*` |
| 3 脚本对照 | 模型 | `review.md` |
| 4 保守取舍 | 模型 | `cut_decisions.json` |
| 5 收缝 | 脚本（`pipeline.py`） | 更新 `cut_decisions.json` + 各报告 |
| 6 残留复查 | 模型 | 修订 `cut_decisions.json` |
| 7 审片定稿 | 人 | 定稿 `cut_decisions.json`、`review_log.json` |
| 8 学偏好 | 可选，人触发 | `preference_candidates.md` → 确认后写入 `references/` |

### 1. 转写

默认百炼录音文件识别 `paraformer-v2`。不要用 `paraformer-realtime-v2` 或其他 realtime 模型，词时间会均分，切口对不上声音。

本地 wav 由脚本上传成百炼临时地址再提交。缺 key、提交失败、或没有词时间：停下来告诉用户「paraformer-v2 暂时用不了」。不要改走 realtime，不要启用本地 Whisper，也不要未经用户选择改走豆包。

用户明确要换模型时，才用豆包录音文件识别 2.0（`volc.seedasr.auc`）。这是可选项，不是失败后的替补。key：`DOUBAO_SPEECH_API_KEY`。音频必须是豆包服务能下载的 wav / mp3 / ogg，用 `--audio-url`。这一路关掉语义顺滑，重复和语气词留给后面的删词。没有地址或没有 key，同样停下来说明。

```bash
$PY $SCRIPTS/transcribe.py <源视频> --output $OUT --script <成片名>/脚本.md

# 用户指定豆包时才加这两项
$PY $SCRIPTS/transcribe.py <源视频> --output $OUT --script <成片名>/脚本.md \
  --engine doubao --audio-url <豆包能下载的音频地址>
```

`transcript.json` 是听写原文，后面不改它。

### 2. 修字

对照 `脚本.md`、`references/proper-nouns.txt` 和 `references/semantic-deletion.md`。只换听错的写法，不换意思，不改时间、词数、音频。口播自己的用词（脚本写成别的词）不改。拿不准的不猜，写进 `unresolved`。

模型写 `corrections.json`，再落字：

```bash
$PY $SCRIPTS/apply_text_corrections.py --transcript $OUT/transcript.json \
  --corrections $OUT/corrections.json --output $TR
```

`replacements` 每条必须对上一个词的 `start`、`end` 和原文 `from`。对不上就停，不要放宽匹配。先看 `corrections.md`，再往下做。没有修字稿之前，不要写 `cut_decisions.json`。

### 3. 脚本对照

读 `$TR` 的字。意思对齐、重说成组、不确定的写进 `review.md` 草稿。这一步**不写** `cut_decisions.json`。

### 4. 保守取舍

写 `cut_decisions.json`（字段见 `references/cut-decisions.md`）。**优先 flag、少 drop**；列举/专名/半句+全句各有独有内容时不自动删前遍；删后须仍能覆盖脚本该句要点。`review.md` 补上听点和高风险删除。

### 5. 收缝

一条命令跑完：气口压缝 → 入点回退 → 词级卡顿 → 清掉删段中间的孤儿气口 → 脚本覆盖 → 残留扫描。每改一次 JSON 都校验。

```bash
$PY $SCRIPTS/pipeline.py --run $OUT
```

逐字稿默认按 `cut_decisions.json` 的 `script` 或 `脚本.md` 找，找不到就跳过覆盖检查并说明；另有位置用 `--script`。某一步失败会停下，报出哪一步、为什么，修好后按提示 `--from <步>` 接着跑。

词级卡顿的白名单见 `references/lexicon.md`，覆盖规则见 `references/script-coverage.md`。

产出：`word_stutter.md`、`script_coverage.md`、`retake_residue.md`、`run_summary.json`（成片时长、压缩率、各类删除条数、残留条数）。keep / drop 的 `text` 会按词时间改成这一段真正留下的字。

### 6. 残留复查

读 `retake_residue.md`、`script_coverage.md`（有 missing 时核对），修订 `cut_decisions.json`，再跑 `validate_decisions.py`。拿不准的进 `flags`，不删。`review.md` 里写过「先不删、要听」的，JSON 里必须有对应的 flag，否则审片时看不到。

```bash
$PY $SCRIPTS/validate_decisions.py $OUT/cut_decisions.json
```

### 7. 审片定稿

```bash
$PY $SCRIPTS/review_server.py --run $OUT --source <源视频>
```

面板优先读 `transcript.corrected.json`。没有修字稿才读 `transcript.json`。打开就是剪后播放。播放倍速可选 1.0、1.1、1.2、1.3。

- **审片记录**：第一次打开面板时，当时的 JSON 存成 `cut_decisions.pipeline.json`，之后每次打开都拿它当基准。每次保存写 `review_log.json`，按时间记下人恢复了哪些删除、新删了哪些，跨多次打开累计。重跑第 5 步会删掉旧基准，下次打开重新存。字段见 `references/review-log.md`。
- **手删**：人在面板里删字，reason 记为 `manual`，和流水线自己拿不准的 `uncertain` 分开。
- **建议听**：右侧列出 `flags`。点一条就跳过去播放，并记为听过。顶部显示还剩几处没听；导出时如果还有没听的，状态栏会提醒。

右上角两个导出按钮：字幕、成片。点下去打开系统的存储对话框，文件名可以改，默认打开上次保存的文件夹；还没有记录时，打开本次 run 目录。同名文件由系统询问是否替换。取消则不写文件。记住的路径在仓库根目录 `.export-dir`，不进 git。建议的文件名是 `<成片名>-<run>.srt` / `.mp4`。导出前先保存。FCPXML 用下面的命令行，不在面板上。

- **导出字幕**：按剪后时间对齐的 SRT，规则见 `references/subtitles.md`。
- **导出成片**：按当前切口渲一条 mp4，帧率和 XML 同一套整数帧率。切口焊进这个文件，改一句要重新导出。字幕仍用单独的 SRT。渲染时页脚显示进度和大约还要多久。

命令行导出（写到 `--output`，不弹对话框）：

```bash
$PY $SCRIPTS/export_fcpxml.py --source <源视频> --decisions $OUT/cut_decisions.json --output $OUT/roughcut.fcpxml
$PY $SCRIPTS/export_srt.py --transcript $TR --decisions $OUT/cut_decisions.json --source <源视频> --output $OUT/roughcut.srt
```

### 8. 学偏好（人触发）

用户说「学一下」时再做。先汇总审片差异，再抽象成规则，**用户点头之后**才写入 `references/`。

```bash
$PY $SCRIPTS/learn_preferences.py --run $OUT
# 多片一起看
$PY $SCRIPTS/learn_preferences.py --repo .
```

读 `preference_candidates.md`（多片时看命令打印）。对照 `references/semantic-deletion.md` 已有判例。只把出现至少 2 次、或一条会改成片结构的（例如收尾只剩一遍）列为候选。列给用户确认后，按四行格式追加到 `semantic-deletion.md`，专名进 `proper-nouns.txt`。没有差异就停，不编规则。

## 交付

| 文件 | 给谁 |
|---|---|
| `cut_decisions.json` | 真源 |
| 审片面板 | 人定稿 |
| `roughcut.fcpxml` | 可选，达芬奇 |
| `roughcut.srt` | 可选，剪后字幕。词来自修字稿，时间在剪后轴 |
| `transcript.json` | 听写原文 |
| `corrections.md` | 第 2 步修字表 |
| `transcript.corrected.json` | 删词判断用的字，时间与原文相同 |
| `review.md` | 对照、听点 |
| `script_coverage.md` | 第 5 步专名覆盖 |
| `retake_residue.md` | 第 5 步扫描，第 6 步复查 |
| `cut_decisions.pipeline.json` | 第 7 步审片基准，第一次开面板时存 |
| `review_log.json` | 第 7 步审片 diff（学偏好原料） |
| `run_summary.json` | 第 5 步指标；审片保存后补上人改了几处 |

## 禁止

为调气口重跑转写或重让模型取舍。把拼接 mp4 当粗剪真源。写剪映草稿 JSON。现场手写 FCPXML。把字幕焊进 FCPXML。用 `transcript.json` 出字幕。把 ChatCut 当粗剪主场。把源片、venv、密钥拷进 skill。用 realtime 模型转写。`paraformer-v2` 失败时改走本地 Whisper、realtime，或未经用户选择改走豆包。未经用户要求不要跑 Jev，也不要为了重说判定去要 `TYPESAFE_API_KEY`。
