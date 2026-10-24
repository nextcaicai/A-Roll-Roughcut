# 脚本覆盖校验

`validate_script_coverage.py`：删后 **keep** 是否仍含脚本里应出现的专名与小节结构。只读，不改 `cut_decisions.json`。

## 检查什么

### 全局专名（v1）

1. 读 `脚本.md`，按非空行编号 `L1`、`L2`…
2. 读 `references/proper-nouns.txt`（可选 `--include-vocabulary` 加上 `vocabulary-common.txt`）。
3. 若某专名出现在某脚本行（归一化 / 英文大小写不敏感），则要求该专名出现在 **全片 keep 的词级转写** 拼接语料中。
4. 缺失项列出；若该词出现在某 `drop.text` 里，报告里附 drop id 提示。

### 小节标题锚点（v1.1）

5. 按 `## 标题` 切脚本小节（开篇无 `##` 的段落为 `S0`）。
6. 每个 `##` 小节取标题归一化后的 **锚点**（去 `#`、去空格标点），长度 ≥ 4 时，要求锚点字符串出现在 keep 语料中（顺序保留的拼接串）。
7. 目的：避免「全片还有一个 WeLink」但 **`## WeLink 的使用说明` 标题段被误删** 仍显示 OK。不做口播时间轴对齐。

## 不检查什么

- 整句与脚本逐字一致
- 口播 ad-lib、脚本未写但 keep 有的内容
- 按源片时间把 keep 映射到脚本行（未做对齐）

## 残留扫描

`scan_retake_residue.py` 在 keep 里找重复短语时，**整词命中 `proper-nouns.txt` 的重复不算残留重说**（专名跨句、跨小节再次出现是常态）。真正的 retake 重复见 `semantic-deletion.md` R9。

## 命令

```bash
$PY $SCRIPTS/validate_script_coverage.py \
  --script <成片名>/脚本.md \
  --transcript $OUT/transcript.corrected.json \
  --decisions $OUT/cut_decisions.json \
  --output $OUT/script_coverage.md
```

`--strict`：有全局或小节缺失时 exit 1（给 CI / 自动化用）。

## 维护专名表

新成片列举、产品名先写入 `proper-nouns.txt`（一行一条）。ASR 常错写可在稿侧保留正确写法；若 keep 只有错写仍会报 missing，用审片或 `flags` 处理。口播里 **不应因「半遍+全遍」被整段删掉** 的名称，写进 `proper-nouns.txt`。
