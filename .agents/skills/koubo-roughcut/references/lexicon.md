# 词表与白名单（references）

机械脚本从这里读配置；**不要**把长列表写进 `SKILL.md`。改词表只改本目录文件。

| 文件 | 用途 | 消费者 |
|---|---|---|
| `word-stutter-redup.txt` | 合法 **叠词**（归一化后 2 字 AA，如 `看看`） | `apply_word_stutter.py` |
| `word-stutter-repeat-words.txt` | 相邻重复 **整词**也保留（如口语） | `apply_word_stutter.py` |
| `proper-nouns.txt` | 专名、产品名、模型名 | `validate_script_coverage.py` |
| `vocabulary-common.txt` | 领域常用词 / 固定搭配 | 覆盖校验（`--include-vocabulary`） |

## 格式

- 纯文本，**一行一条**，UTF-8。
- `#` 开头为注释，空行忽略。
- 写入条目时请用 **归一化形式**（无标点、与 `decisions_common.normalize_zh` 一致），或接受脚本自动 normalize。

## word-stutter

由 `SKILL.md` 第 5 步的 `pipeline.py` 调用 `apply_word_stutter.py`。相邻相同 token、间隔 ≤ 0.45s 时，若 norm 命中叠词白名单 → 不 drop 前一 token。

## proper-nouns / vocabulary

规则见 `references/script-coverage.md`。口播里 **不应因「半遍+全遍」被整段删掉** 的名称，写进 `proper-nouns.txt`；成片领域词写进 `vocabulary-common.txt`。
