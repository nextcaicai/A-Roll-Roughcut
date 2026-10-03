const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { planCutAction, silenceCuts, silenceMarks, detectSilences, silenceLabel, pauseDeleteIntervals } = require("./review_cut.js");

test("200ms into a deleted sentence still jumps to the next keep", () => {
  const keeps = [
    { sourceStart: 45.46, sourceEnd: 57.7 },
    { sourceStart: 169.84, sourceEnd: 243.4 },
  ];
  assert.deepEqual(planCutAction(keeps, 57.9), { op: "jump", at: 169.84 });
});

test("deleted speech after the last keep stops instead of playing out", () => {
  const keeps = [{ sourceStart: 504.04, sourceEnd: 513.3 }];
  assert.deepEqual(planCutAction(keeps, 516.5), { op: "end" });
  assert.equal(planCutAction(keeps, 513.29).op, "play");
  assert.deepEqual(planCutAction(keeps, 513.31), { op: "end" });
});

test("early mute uses only the quiet tail, not a fixed 40ms", () => {
  const keeps = [
    { sourceStart: 0, sourceEnd: 1 },
    { sourceStart: 2, sourceEnd: 3 },
  ];
  const loudUntil = (quietFrom) => {
    const peaks = Array(100).fill(0.2);
    for (let i = quietFrom; i < 100; i++) peaks[i] = 0;
    return { peaks, duration: 1 };
  };
  assert.equal(planCutAction(keeps, 0.95, null, loudUntil(96)).op, "play");
  assert.equal(planCutAction(keeps, 0.98, null, loudUntil(96)).op, "mute");
  assert.equal(planCutAction(keeps, 0.97, null, loudUntil(100)).op, "play");
});

test("without a measured silence the last frames of a keep still play", () => {
  const keeps = [
    { sourceStart: 45.46, sourceEnd: 57.7 },
    { sourceStart: 169.84, sourceEnd: 243.4 },
  ];
  assert.equal(planCutAction(keeps, 57.69).op, "play");
  assert.deepEqual(planCutAction(keeps, 57.9), { op: "jump", at: 169.84 });
});

test("abutting keeps are not treated as a cut", () => {
  const keeps = [
    { sourceStart: 45.06, sourceEnd: 45.46 },
    { sourceStart: 45.46, sourceEnd: 57.7 },
  ];
  assert.equal(planCutAction(keeps, 45.45).op, "play");
});

test("playhead before the first keep jumps to it", () => {
  const keeps = [{ sourceStart: 1.9, sourceEnd: 42.4 }];
  assert.deepEqual(planCutAction(keeps, 0.2), { op: "jump", at: 1.9 });
});

test("a trimmed pause between kept lines becomes a breath marker", () => {
  const words = [
    { s: 41.9, e: 42.4 },
    { s: 45.46, e: 45.9 },
  ];
  const keeps = [
    { sourceStart: 1.9, sourceEnd: 42.4 },
    { sourceStart: 45.06, sourceEnd: 45.46 },
    { sourceStart: 45.46, sourceEnd: 57.7 },
  ];
  const cuts = silenceCuts(words, keeps);
  assert.equal(cuts.length, 1);
  assert.equal(cuts[0].kind, "breath");
  assert.equal(cuts[0].afterWord, 0);
  assert.ok(Math.abs(cuts[0].removed - 2.66) < 0.021);
  assert.ok(Math.abs(cuts[0].kept - 0.4) < 0.021);
  assert.equal(silenceLabel({ seconds: cuts[0].removed, role: "cut" }), "[...2.7s]");
});

test("a fully removed pause is a silence marker", () => {
  const words = [
    { s: 261.0, e: 261.52 },
    { s: 264.92, e: 265.4 },
  ];
  const keeps = [
    { sourceStart: 256.17, sourceEnd: 261.52 },
    { sourceStart: 266.6, sourceEnd: 290.8 },
  ];
  const cuts = silenceCuts(words, keeps);
  assert.equal(cuts.length, 1);
  assert.equal(cuts[0].kind, "silence");
  assert.ok(cuts[0].removed > 3.3);
  assert.equal(silenceLabel({ seconds: cuts[0].removed, role: "cut" }), "[...3.4s]");
});

test("a pause that stays inside a keep is not shown as deleted", () => {
  const words = [
    { s: 10, e: 10.4 },
    { s: 10.8, e: 11.2 },
  ];
  const keeps = [{ sourceStart: 9, sourceEnd: 12 }];
  assert.deepEqual(silenceCuts(words, keeps), []);
});

test("opening silence before the first word is marked when it was cut", () => {
  const words = [{ s: 2.02, e: 2.34 }];
  const keeps = [{ sourceStart: 1.9, sourceEnd: 42.4 }];
  const cuts = silenceCuts(words, keeps, { from: 0 });
  assert.equal(cuts.length, 1);
  assert.equal(cuts[0].afterWord, -1);
  assert.ok(cuts[0].removed > 1.5);
});

test("a breath hidden inside word timings still shows up", () => {
  const words = [
    { s: 8.2, e: 8.49 },
    { s: 8.492, e: 8.94 },
    { s: 8.94, e: 9.4 },
  ];
  const keeps = [{ sourceStart: 0, sourceEnd: 20 }];
  const peaks = Array(100).fill(0.2);
  for (let i = 86; i < 91; i++) peaks[i] = 0;
  const marks = silenceMarks(words, keeps, peaks, 10, { threshold: 0.02, minDuration: 0.2 });
  assert.equal(marks.length, 1);
  assert.equal(marks[0].role, "kept");
  assert.equal(marks[0].kind, "breath");
  assert.ok(marks[0].seconds >= 0.4);
  assert.equal(silenceLabel(marks[0]), "[...0.5s]");
});

test("a cut pause is struck as the removed seconds, and the leftover breath stays", () => {
  const words = [
    { s: 1, e: 1.2 },
    { s: 4.2, e: 4.5 },
  ];
  const keeps = [
    { sourceStart: 0, sourceEnd: 1.2 },
    { sourceStart: 3.8, sourceEnd: 4.2 },
    { sourceStart: 4.2, sourceEnd: 6 },
  ];
  const peaks = Array(80).fill(0.2);
  for (let i = 12; i < 42; i++) peaks[i] = 0;
  const marks = silenceMarks(words, keeps, peaks, 8, { threshold: 0.02, minDuration: 0.2 });
  const cut = marks.find((m) => m.role === "cut");
  const kept = marks.find((m) => m.role === "kept");
  assert.ok(cut);
  assert.ok(kept);
  assert.match(silenceLabel(cut), /^\[\.\.\./);
  assert.ok(cut.seconds > kept.seconds);
  assert.ok(cut.end <= kept.start + 0.001);
});

test("a struck pause stays beside the older cut instead of merging away", () => {
  const words = [{ s: 2.02, e: 2.34 }];
  const keeps = [{ sourceStart: 2.28, sourceEnd: 10 }];
  const drops = [{ id: "d9", sourceStart: 1.9, sourceEnd: 2.28, reason: "breath" }];
  const peaks = Array(100).fill(0.2);
  for (let i = 0; i < 24; i++) peaks[i] = 0;
  const marks = silenceMarks(words, keeps, peaks, 10, { drops, minDuration: 0.2, threshold: 0.02 });
  const cuts = marks.filter((m) => m.role === "cut");
  assert.equal(cuts.length, 2);
  assert.ok(cuts.some((c) => Math.abs((c.end - c.start) - 0.38) < 0.05));
});

function overlap(start, end, keeps) {
  let total = 0;
  for (const k of keeps) {
    const a = Math.max(start, k.sourceStart);
    const b = Math.min(end, k.sourceEnd);
    if (b > a) total += b - a;
  }
  return total;
}

function punch(keeps, intervals) {
  let out = keeps.map((k) => ({ ...k }));
  for (const [start, end] of intervals) {
    const next = [];
    for (const k of out) {
      if (k.sourceEnd <= start + 0.001 || k.sourceStart >= end - 0.001) { next.push(k); continue; }
      if (k.sourceStart < start - 0.001 && start - k.sourceStart >= 0.02) next.push({ ...k, sourceEnd: start });
      if (k.sourceEnd > end + 0.001 && k.sourceEnd - end >= 0.02) next.push({ ...k, sourceStart: end });
    }
    out = next;
  }
  return out;
}

test("deleting a pause leaves the overlapped word in place", () => {
  const words = [{ s: 2.02, e: 2.34 }];
  const keeps = [{ sourceStart: 1.9, sourceEnd: 42.4 }];
  const ivs = pauseDeleteIntervals(0, 2.32, words, keeps);
  const left = punch(keeps, ivs);
  assert.ok(overlap(2.02, 2.34, left) >= 0.04 - 0.001);
  assert.ok(overlap(1.9, 2.32, keeps) - overlap(1.9, 2.32, left) > 0.3);
});

test("a pause that sits between words is removed in full", () => {
  const words = [{ s: 1, e: 1.2 }, { s: 2, e: 2.2 }];
  const keeps = [{ sourceStart: 0, sourceEnd: 3 }];
  assert.deepEqual(pauseDeleteIntervals(1.2, 2, words, keeps), [[1.2, 2]]);
});

test("a word that already keeps its tail is not shielded inside the pause", () => {
  const words = [{ s: 2, e: 2.5 }];
  const keeps = [{ sourceStart: 1.5, sourceEnd: 3 }];
  assert.deepEqual(pauseDeleteIntervals(1.5, 2.1, words, keeps), [[1.5, 2.1]]);
});

test("review panel applies the cut plan every frame and shows silence cuts", () => {
  const html = fs.readFileSync(path.join(__dirname, "review.html"), "utf8");
  assert.match(html, /planCutAction\(/);
  assert.match(html, /requestAnimationFrame/);
  assert.match(html, /createMediaElementSource/);
  assert.match(html, /silenceMarks\(/);
  assert.match(html, /silenceCuts\(/);
  assert.match(html, /pauseDeleteIntervals\(/);
  assert.match(html, /applyMixedSelection\(/);
  assert.doesNotMatch(html, /hit\.inDrop && hit\.next/);
});
