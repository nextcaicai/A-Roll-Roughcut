# cut_decisions.json

粗剪的真源。FCPXML、时间表都从这份生成。改切点只改这里，再导出 XML，不渲拼接 mp4。

`breath.maxKeep`：keep 内词间停顿超过该值时，压到该长度（见 `compress_breath.py`）。整段已经划掉之后，不挨着留下的字的气口也写成 `long-pause`（见 `prune_orphaned_empty_keeps`）。

`breath.leadIn`（默认 `0.12`）：keep 起点在**前面空隙内**回退，保留换气余量；不伸进 `drop` 或上一段 keep（见 `apply_lead_in.py`）。

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

keep / drop 的 `text` 是这段时间里中点落进来的修字，不是整句。第 5 步和审片保存时按词时间重写。气口 keep 的 `text` 为空。

`flags[].heard`：审片面板里点开听过就写 `true`。没有这个字段就是还没听。

## 校验

`validate_decisions.py`、`pipeline.py` 和面板保存时共用 `decisions_common.decisions_problems`：

- 报错，不许写：keep 时间倒置、超出源片、互相重叠、时间线对不上；reason 不在上表；**同一段时间既在 keep 里又在 drop 里**。
- 只提示：两条 drop 互相重叠。老面板留下过这种数据，不影响成片，但会让审片记录难读。

keep 和 drop 都没盖到的时间，也是剪掉的（例如第 4 步只写了 keep、没给句间空隙写 drop），不算错。
