/* Cut-playback decisions and silence markers for the review panel. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReviewCut = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const EPS = 0.001;
  const MUTE_LEAD = 0.04;
  const MIN_CUT_GAP = 0.02;
  const BREATH_LEFT = 0.08;
  const SPEECH_PEAK = 0.02;

  // How much quiet audio sits inside the keep, immediately before it ends.
  // The early mute may use only this stretch, and never more than MUTE_LEAD.
  function trailingSilence(keepEnd, peaks, duration) {
    if (!peaks || !peaks.length || !(duration > 0) || !(keepEnd > 0)) return 0;
    const dt = duration / peaks.length;
    let silent = 0;
    for (let t = keepEnd - dt * 0.5; t >= 0 && silent < MUTE_LEAD - 1e-4; t -= dt) {
      const i = Math.min(peaks.length - 1, Math.max(0, Math.floor(t / dt)));
      if (peaks[i] >= SPEECH_PEAK) break;
      silent += dt;
    }
    return Math.min(MUTE_LEAD, silent);
  }

  function planCutAction(keeps, t, muteLead, audio) {
    for (let i = 0; i < keeps.length; i++) {
      const k = keeps[i];
      if (t < k.sourceStart - EPS) return { op: "jump", at: k.sourceStart };
      if (t < k.sourceEnd - EPS) {
        const next = keeps[i + 1];
        const gap = next ? next.sourceStart - k.sourceEnd : Infinity;
        let lead = muteLead == null ? 0 : muteLead;
        if (audio && audio.peaks && muteLead == null) {
          lead = trailingSilence(k.sourceEnd, audio.peaks, audio.duration);
        }
        if (lead > 0 && k.sourceEnd - t <= lead && (!next || gap > MIN_CUT_GAP)) {
          return next ? { op: "mute", at: next.sourceStart } : { op: "end" };
        }
        return { op: "play" };
      }
    }
    return keeps.length ? { op: "end" } : { op: "play" };
  }

  function keptOverlap(start, end, keeps) {
    let total = 0;
    for (let i = 0; i < keeps.length; i++) {
      const k = keeps[i];
      if (k.sourceEnd <= start) continue;
      if (k.sourceStart >= end) break;
      const a = Math.max(start, k.sourceStart);
      const b = Math.min(end, k.sourceEnd);
      if (b > a) total += b - a;
    }
    return total;
  }

  function silenceCuts(words, keeps, opts) {
    const options = opts || {};
    const minGap = options.minGap == null ? 0.12 : options.minGap;
    const minRemoved = options.minRemoved == null ? 0.15 : options.minRemoved;
    const sortedKeeps = keeps.slice().sort((a, b) => a.sourceStart - b.sourceStart);
    const spans = [];
    if (options.from != null && words.length && words[0].s - options.from >= minGap) {
      spans.push({ start: options.from, end: words[0].s, afterWord: -1 });
    }
    for (let i = 0; i < words.length - 1; i++) {
      const start = words[i].e;
      const end = words[i + 1].s;
      if (end - start >= minGap) spans.push({ start, end, afterWord: i });
    }
    if (options.to != null && words.length) {
      const last = words[words.length - 1];
      if (options.to - last.e >= minGap) {
        spans.push({ start: last.e, end: options.to, afterWord: words.length - 1 });
      }
    }
    const cuts = [];
    for (const span of spans) {
      const kept = keptOverlap(span.start, span.end, sortedKeeps);
      const removed = span.end - span.start - kept;
      if (removed < minRemoved) continue;
      cuts.push({
        start: span.start,
        end: span.end,
        removed,
        kept,
        afterWord: span.afterWord,
        kind: kept >= BREATH_LEFT ? "breath" : "silence",
      });
    }
    return cuts;
  }

  function detectSilences(peaks, duration, opts) {
    const options = opts || {};
    const threshold = options.threshold == null ? 0.02 : options.threshold;
    const minDuration = options.minDuration == null ? 0.2 : options.minDuration;
    const mergeGap = options.mergeGap == null ? 0.08 : options.mergeGap;
    if (!peaks || !peaks.length || !(duration > 0)) return [];
    const dt = duration / peaks.length;
    const raw = [];
    let run = null;
    for (let i = 0; i <= peaks.length; i++) {
      const low = i < peaks.length && peaks[i] < threshold;
      if (low) {
        if (run == null) run = i;
      } else if (run != null) {
        const start = run * dt;
        const end = i * dt;
        if (end - start >= minDuration) raw.push({ start: start, end: end });
        run = null;
      }
    }
    const merged = [];
    for (let i = 0; i < raw.length; i++) {
      const prev = merged[merged.length - 1];
      if (prev && raw[i].start - prev.end <= mergeGap) prev.end = raw[i].end;
      else merged.push({ start: raw[i].start, end: raw[i].end });
    }
    return merged.filter(function (s) { return s.end - s.start >= minDuration; });
  }

  function attachAfterWord(mid, words) {
    if (!words.length) return -1;
    for (let i = 0; i < words.length - 1; i++) {
      if (words[i].e - 0.001 <= mid && mid <= words[i + 1].s + 0.001) return i;
    }
    for (let i = 0; i < words.length; i++) {
      if (mid < words[i].s || mid > words[i].e) continue;
      return words[i].e - mid <= mid - words[i].s ? i : i - 1;
    }
    if (mid < words[0].s) return -1;
    return words.length - 1;
  }

  function silencePieces(start, end, keeps, drops) {
    const bounds = [start, end];
    function add(t) {
      if (t > start + EPS && t < end - EPS) bounds.push(t);
    }
    for (let i = 0; i < keeps.length; i++) {
      add(keeps[i].sourceStart);
      add(keeps[i].sourceEnd);
    }
    for (let i = 0; i < drops.length; i++) {
      add(drops[i].sourceStart);
      add(drops[i].sourceEnd);
    }
    bounds.sort(function (a, b) { return a - b; });
    const pts = [];
    for (let i = 0; i < bounds.length; i++) {
      if (!pts.length || bounds[i] - pts[pts.length - 1] > 0.001) pts.push(bounds[i]);
    }
    const pieces = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      const mid = (a + b) / 2;
      let inKeep = false;
      for (let k = 0; k < keeps.length; k++) {
        if (mid >= keeps[k].sourceStart && mid < keeps[k].sourceEnd) { inKeep = true; break; }
      }
      let dropId = null;
      if (!inKeep) {
        for (let d = 0; d < drops.length; d++) {
          if (mid >= drops[d].sourceStart && mid < drops[d].sourceEnd) { dropId = drops[d].id || "drop"; break; }
        }
      }
      const prev = pieces[pieces.length - 1];
      if (prev && prev.inKeep === inKeep && prev.dropId === dropId) prev.end = b;
      else pieces.push({ start: a, end: b, inKeep: inKeep, dropId: dropId });
    }
    return pieces.filter(function (p) {
      const dur = p.end - p.start;
      if (p.inKeep) return dur >= 0.12;
      if (p.dropId) return dur >= 0.04;
      return dur >= 0.12;
    });
  }

  function silenceMarks(words, keeps, peaks, duration, opts) {
    const options = opts || {};
    const sortedKeeps = keeps.slice().sort(function (a, b) { return a.sourceStart - b.sourceStart; });
    const drops = (options.drops || []).filter(function (d) {
      return d && (d.reason === "breath" || d.reason === "long-pause");
    });
    const marks = [];
    const silences = detectSilences(peaks, duration, opts);
    for (let i = 0; i < silences.length; i++) {
      const sil = silences[i];
      const pieces = silencePieces(sil.start, sil.end, sortedKeeps, drops);
      const kept = keptOverlap(sil.start, sil.end, sortedKeeps);
      const removed = Math.max(0, sil.end - sil.start - kept);
      const kind = removed >= 0.8 && kept < 0.2 ? "silence" : "breath";
      for (let p = 0; p < pieces.length; p++) {
        const piece = pieces[p];
        const seconds = piece.end - piece.start;
        const afterWord = attachAfterWord((piece.start + piece.end) / 2, words);
        marks.push({
          start: piece.start,
          end: piece.end,
          afterWord: afterWord,
          role: piece.inKeep ? "kept" : "cut",
          seconds: seconds,
          removed: piece.inKeep ? 0 : seconds,
          kept: piece.inKeep ? seconds : 0,
          kind: piece.inKeep ? "breath" : kind,
        });
      }
    }
    return marks;
  }

  function fmtSec(s) {
    if (s >= 1) return (Math.round(s * 10) / 10).toFixed(1);
    return (Math.round(s * 100) / 100).toFixed(2).replace(/0$/, "");
  }

  // Quiet runs sit inside word timestamps. Punching the whole run marks those
  // words deleted. Leave each overlapped word at least 40ms of keep, and only
  // return the slices that should actually be removed.
  function pauseDeleteIntervals(start, end, words, keeps) {
    const minLeft = 0.04;
    const sorted = (keeps || []).slice().sort(function (a, b) { return a.sourceStart - b.sourceStart; });
    let slices = [];
    for (let i = 0; i < sorted.length; i++) {
      const k = sorted[i];
      if (k.sourceEnd <= start + EPS) continue;
      if (k.sourceStart >= end - EPS) break;
      const a = Math.max(start, k.sourceStart);
      const b = Math.min(end, k.sourceEnd);
      if (b - a >= 0.02) slices.push([a, b]);
    }
    const list = words || [];
    for (let i = 0; i < list.length; i++) {
      const w = list[i];
      if (w.e <= start + EPS || w.s >= end - EPS) continue;
      const span = Math.min(w.e, end) - Math.max(w.s, start);
      const outside = keptOverlap(w.s, w.e, sorted) - keptOverlap(Math.max(w.s, start), Math.min(w.e, end), sorted);
      const need = minLeft - outside;
      if (need <= 0.001 || span <= 0.001) continue;
      const ws = Math.max(w.s, start);
      const we = Math.min(w.e, end);
      const protectStart = Math.max(ws, we - need);
      slices = subtractSlice(slices, protectStart, we);
    }
    return slices;
  }

  function subtractSlice(slices, cutStart, cutEnd) {
    const out = [];
    for (let i = 0; i < slices.length; i++) {
      const s = slices[i][0];
      const e = slices[i][1];
      if (e <= cutStart + EPS || s >= cutEnd - EPS) { out.push([s, e]); continue; }
      if (s < cutStart - EPS) out.push([s, Math.min(e, cutStart)]);
      if (e > cutEnd + EPS) out.push([Math.max(s, cutEnd), e]);
    }
    return out.filter(function (iv) { return iv[1] - iv[0] >= 0.02; });
  }

  function silenceLabel(cut) {
    const seconds = cut.seconds != null ? cut.seconds : cut.removed;
    return "[..." + fmtSec(seconds) + "s]";
  }

  function silenceTitle(cut) {
    const name = cut.kind === "silence" ? "静音" : "气口";
    const seconds = cut.seconds != null ? cut.seconds : cut.removed;
    if (cut.role === "kept") return name + "，留下 " + seconds.toFixed(2) + " 秒";
    return name + "，划掉 " + seconds.toFixed(2) + " 秒";
  }

  return {
    planCutAction,
    trailingSilence,
    silenceCuts,
    detectSilences,
    silenceMarks,
    pauseDeleteIntervals,
    silenceLabel,
    silenceTitle,
  };
});
