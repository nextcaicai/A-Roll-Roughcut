# 词表与白名单（references）

机械脚本从这里读配置；**不要**把长列表写进 `SKILL.md`。改词表只改本目录文件。

| 文件 | 用途 | 消费者 |
|---|---|---|
| `asr-corrections.txt` | 常见听写错误，左右词数相同才替换 | `apply_asr_lexicon.py` |
| `word-stutter-function.txt` | 单字重叠仍算口吃的虚词、代词 | `apply_word_stutter.py` |
| `word-stutter-repeat-words.txt` | 相邻重复 **整词**也保留（如口语「为什么」） | `apply_word_stutter.py` |
| `word-stutter-redup.txt` | 已停用。叠词不再逐个登记 | — |
| `proper-nouns.txt` | 专名、产品名、模型名 | `validate_script_coverage.py` |
| `vocabulary-common.txt` | 领域常用词 / 固定搭配 | 覆盖校验（`--include-vocabulary`） |

## 格式

- 纯文本，**一行一条**，UTF-8。
- `#` 开头为注释，空行忽略。
- 叠词、专名、常用词写入时用归一化形式（无标点、与 `decisions_common.normalize_zh` 一致）。错词表用 `听写 => 写法`，见下。

## asr-corrections

由 `SKILL.md` 第 2 步的 `apply_asr_lexicon.py` 在模型修字之前跑。一行一条，`听写 => 写法`，空格是词界，左右词数必须一样。单个字和本身能当正常词的不要写进来。

## word-stutter

由 `SKILL.md` 第 4 步的 `pipeline.py` 调用 `apply_word_stutter.py`。相邻相同 token、间隔 ≤ 0.45s 时：单字是实词（看看、找找、迟迟、空荡荡）就留下；单字在 `word-stutter-function.txt` 里，或重复的是两个字以上的整词，才删前一个。不要为新出现的叠词往表里加词。见 `semantic-deletion.md` R10。

## proper-nouns / vocabulary

规则见 `references/script-coverage.md`。口播里 **不应因「半遍+全遍」被整段删掉** 的名称，写进 `proper-nouns.txt`；成片领域词写进 `vocabulary-common.txt`。
