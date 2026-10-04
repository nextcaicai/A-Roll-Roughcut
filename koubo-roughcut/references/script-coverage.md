# 脚本覆盖校验

`validate_script_coverage.py`：删后 **keep** 是否仍含脚本里应出现的专名。只读，不改 `cut_decisions.json`。

## 检查什么（v1）

1. 读 `脚本.md`，按非空行编号 `L1`、`L2`…
2. 读 `references/proper-nouns.txt`（可选 `--include-vocabulary` 加上 `vocabulary-common.txt`）。
3. 若某专名出现在某脚本行（归一化 / 英文大小写不敏感），则要求该专名出现在 **全片 keep 的词级转写** 拼接语料中。
4. 缺失项列出；若该词出现在某 `drop.text` 里，报告里附 drop id 提示。

## 不检查什么

- 整句与脚本逐字一致
- 口播 ad-lib、脚本未写但 keep 有的内容
- 无 `scriptRef` 时不做「按时间轴段落」对齐（整稿 keep 一条 pass）

## 命令

```bash
$PY $SCRIPTS/validate_script_coverage.py \
  --script <成片名>/脚本.md \
  --transcript $OUT/transcript.json \
  --decisions $OUT/cut_decisions.json \
  --output $OUT/script_coverage.md
```

`--strict`：有 missing 时 exit 1（给 CI / 自动化用）。

## 维护专名表

新成片列举、产品名先写入 `proper-nouns.txt`（一行一条）。ASR 常错写可在稿侧保留正确写法；若 keep 只有错写仍会报 missing，用审片或 `flags` 处理。
