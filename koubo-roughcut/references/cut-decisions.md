# cut_decisions.json

粗剪的真源。FCPXML、时间表都从这份生成。改切点只改这里，再导出 XML，不渲拼接 mp4。

`breath.maxKeep`：留下的字之间，缝长于该值时只保留下一词前的这一段。段内和段间用同一长度（见 `compress_breath.py`）。整段已经划掉之后，不挨着留下的字的气口也写成 `long-pause`（见 `prune_orphaned_empty_keeps`）。

`breath.leadIn`（默认 `0.12`）：片子开头，以及内容删除之后的词头，把这一段垫进 keep。可以从 `long-pause` 收回，不伸进 `retake` / `stutter` / `manual`。

没字、且不在 `retake` / `stutter` / `manual` / `uncertain` 里的时间：波形峰值达到 **0.08**、持续至少 **0.12** 秒的，留成 keep。中间低于 **0.10** 秒的起伏仍算同一段。`text` 为空，`reason` 为 `breath`，并带 `untranscribed: true`。同时加一条 flag，`note` 为「这里有人声，但没有字」。低于 0.08 的才写成 `long-pause`。紧挨着内容删除结束的人声（删掉那遍的字尾）不拉回来。清孤儿气口时，`untranscribed` 的 keep 留下。

时间单位：秒，浮点，保留 3 位。时间线时间从 0 起，按 keep 段顺序累加。

```json
{
  "version": 1,
  "source": {
    "path": "action_….mp4",
    "duration": 1508.724,
    "fps": 30
  },
  "script": "脚本.md",
  "breath": { "maxKeep": 0.4, "leadIn": 0.12 },
  "keep": [
    {
      "id": "k001",
      "sourceStart": 12.400,
      "sourceEnd": 18.120,
      "timelineStart": 0.0,
      "timelineEnd": 5.720,
      "text": "为什么单独开这个栏目？两个原因。",
      "scriptRef": "L1",
      "reason": "speech"
    }
  ],
  "drop": [
    {
      "id": "d014",
      "sourceStart": 88.000,
      "sourceEnd": 94.200,
      "text": "下用一段大白话把需求描述清楚。",
      "reason": "retake",
      "keptInstead": "k024"
    }
  ],
  "flags": [
    {
      "id": "f003",
      "sourceStart": 512.0,
      "reason": "uncertain",
      "note": "工具名口糊，ASR 乱，只删了失败遍"
    }
  ]
}
```

`reason` 只允许：`speech` · `breath` · `retake` · `stutter` · `long-pause` · `uncertain` · `manual`

- `uncertain`：流水线拿不准。按规则应该进 `flags` 而不是 drop；drop 里出现它，说明有人决定删但没写原因。
- `manual`：人在审片面板里删的字。面板里删气口仍记 `breath` / `long-pause`，是谁删的看 `review_log.json`。

`scriptRef` 指向 `脚本.md` 的行号或小节标题，对得上就写，对不上就省。

keep / drop 的 `text` 是这段时间里中点落进来的修字，不是整句。第 4 步和审片保存时按词时间重写。气口 keep 的 `text` 为空。

第 3 步不写 `keep`。下面是收缝生成 keep、压完气口之后的形状。drop 的起止按词的中点判断：中点落在区间里，这个词就删。

`flags[].heard`：审片面板里点开听过就写 `true`。没有这个字段就是还没听。

## 校验

`validate_decisions.py`、`pipeline.py` 和面板保存时共用 `decisions_common.decisions_problems`：

- 报错，不许写：keep 时间倒置、超出源片、互相重叠、时间线对不上；reason 不在上表；**同一段时间既在 keep 里又在 drop 里**。
- 只提示：两条 drop 互相重叠。老面板留下过这种数据，不影响成片，但会让审片记录难读。

keep 和 drop 都没盖到的时间，也是剪掉的。第 3 步只写 drop，没被 drop 盖住的词由第 4 步收成 keep。
