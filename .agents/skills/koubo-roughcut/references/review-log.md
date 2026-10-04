# review_log.json

审片面板保存（`PUT /api/decisions`，导出前也会先保存）时写进 run 目录。记的是「流水线交出来的」和「人定稿的」差在哪，供以后学偏好。**不读 FCPXML**（XML 丢了 drop 的原因）。

## 基准

- 第一次对这个 run 打开面板时，当时的 `cut_decisions.json` 存成 `cut_decisions.pipeline.json`。
- 之后每次打开面板都读这份，不再用「这次打开时的 JSON」。所以关掉再开、分几次审，diff 都是累计的。
- 重跑 `pipeline.py`（第 5 步）会删掉旧基准；下次打开面板重新存。
- 第 6 步模型复查改的东西，在第一次开面板之前，算流水线产出。

## 怎么比

按时间比「剪掉了什么」，不按 drop 条目比：

- `dropsRestored`：基准里剪掉、定稿里留下的时间段 → 人把流水线删的又留回。
- `dropsAdded`：基准里留下、定稿里剪掉的时间段 → 人新删的。
- 短于 0.1 秒的差异不记（入点回退的抖动）。
- 一条 drop 被拆成两段、或只改了 reason，不算变化。

每条含 `sourceStart`、`sourceEnd`、`text`（这段时间里中点落进来的修字稿词）、`reason` 和 `id`（恢复的取基准里盖住它最多的那条 drop，新删的取定稿里的），`keptInstead`（若有），以及有转写时的 `context`（前后各约 24 字）。

## 其他字段

| 字段 | 含义 |
|---|---|
| `generatedAt` | UTC ISO8601 |
| `runDir` | 本次 run 绝对路径 |
| `script` / `source` | 来自 decisions |
| `session` | 本次面板的 `startedAt`、`savedAt`、`port` |
| `baseline` / `final` | keep / drop / flag 条数、剪后总长 |
| `flags` | `total`、`heardCount`、`unheard`（没点开过的建议听：`id`、`sourceStart`、`note`） |
| `stats` | 剪后总长变化、恢复与新删条数 |

保存时如果 run 里已有 `run_summary.json`，会把 `stats` 和没听的 flag 数写进它的 `review` 段。没有这份文件也会新建一份，只含 review。

## 学偏好（第 8 步）

用户说「学一下」时跑 `learn_preferences.py`。它只汇总 `review_log.json`，写出 `preference_candidates.md`，不改 `references/`。

- `--run`：这一次的日志，写到该 run。
- `--repo`：仓库里所有 `runs/*/review_log.json`（跳过 `.tmp*`），打印到终端。

没有差异就停。旧日志如果是基准被重置后的空 diff，也学不到东西。模型读报告后列候选，**确认后再写**：删词判例进 `semantic-deletion.md`（四行格式），专名进 `proper-nouns.txt`。只提重复出现的，或一条会改成片结构的。
