// The chart data page's smoke test (music_data.py check, gate "page"): the page's own pure modules (ournotes-player
// examples/songs: catalog.js, ranking.js) over a music-data.json in Node.js, the way the page reads it. Every chart
// must get a row, every playable chart its figures in every play scenario (Gekisou Live at several ranks and Just
// rates, Free Live, a Great share), finite and positive, and the rankings must work on them.
//
//   node music_data_smoke.mjs <examples/songs directory> <music-data.json>
//
// Prints one line per problem (at most 40) and exits 1, or a one-line summary.
import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [dir, file] = process.argv.slice(2);
if (!dir || !file) {
  console.error("usage: node music_data_smoke.mjs <examples/songs directory> <music-data.json>");
  process.exit(2);
}
const catalog = await import(pathToFileURL(path.join(dir, "catalog.js")).href);
const ranking = await import(pathToFileURL(path.join(dir, "ranking.js")).href);
const data = JSON.parse(readFileSync(file, "utf8"));

const problems = [];
const problem = (m) => problems.push(m);
const finite = (v) => typeof v === "number" && Number.isFinite(v);

const charts = (data.songs || []).flatMap((s) => (s.charts || []).map((c) => ({ song: s, chart: c })));
const rows = catalog.chartRows(data);
if (rows.length !== charts.length) problem(`chartRows: ${rows.length} rows for ${charts.length} charts`);
const kind = ranking.plainKind(data);
if (kind === null) problem("plainKind: the file has no plain score-up kind (effect 2000, 5 s, no targets)");

// Whether the data has what a scenario needs on a chart (a null rangeWeights or kind leaves a chart without figures in
// the rank and Just scenarios: music_data.py reports those as warnings).
const computable = (deck, scenario) => {
  if (!deck) return false;
  const has = (s) => s && Array.isArray((s.weights || [])[kind]);
  if (scenario && scenario.mode === "free") return (deck.offSeeds || []).length > 0 && deck.offSeeds.every(has);
  if (deck.unplayable || !(deck.seeds || []).length) return false;
  const rank1 = !scenario || ((scenario.ranks || []).every((r) => r === 1) && (scenario.just ?? 1) >= 1);
  return deck.seeds.every((s) => has(s) && (rank1 || Array.isArray((s.rangeWeights || [])[kind])));
};
const has = ranking.scenarioData(data);
for (const k of ["free", "ranks", "just"]) if (!has[k]) problem(`scenarioData: no data for the ${k} scenario`);

const SCENARIOS = [
  ["Gekisou Live, rank 1", null],
  ["Gekisou Live, rank 5", { mode: "battle", ranks: [5, 5, 5], just: 1, great: 0 }],
  ["Gekisou Live, ranks 2 3 4", { mode: "battle", ranks: [2, 3, 4], just: 1, great: 0 }],
  ["Gekisou Live, Just 0", { mode: "battle", ranks: [1, 1, 1], just: 0, great: 0 }],
  ["Gekisou Live, rank 3, Just 0.5, Great 0.2", { mode: "battle", ranks: [3, 3, 3], just: 0.5, great: 0.2 }],
  ["Free Live", { mode: "free", ranks: [1, 1, 1], just: 1, great: 0 }],
  ["Free Live, Great 0.5", { mode: "free", ranks: [1, 1, 1], just: 1, great: 0.5 }],
];
const SKILLS = [1, 1, 1, 1, 1];
let figures = 0;
for (const [name, scenario] of SCENARIOS) {
  const joined = ranking.joinCharts(data, scenario);
  const expected = charts.filter(({ chart }) => computable(chart.deck, scenario)).length;
  if (joined.length !== expected) problem(`${name}: ${joined.length} charts with figures, expected ${expected}`);
  for (const r of joined) {
    figures++;
    if (!finite(r.base) || r.base <= 0) problem(`${name}: chart ${r.scoreId}: base ${r.base}`);
    if (!Array.isArray(r.weights) || !r.weights.length || !r.weights.every(finite)) {
      problem(`${name}: chart ${r.scoreId}: weights are not finite numbers`);
    }
  }
  if (!joined.length) continue;
  const ranked = ranking.rank(joined, { skills: SKILLS, source: "bgm", overheadMs: 30000 });
  if (!ranked.some((r) => r.frontier)) problem(`${name}: no chart on the frontier`);
  for (const r of ranked) {
    if (r.lengthMs === null) problem(`${name}: chart ${r.scoreId}: no play length`);
    else if (!finite(r.perMinute) || !finite(r.rate)) problem(`${name}: chart ${r.scoreId}: rate ${r.rate}`);
  }
  for (const room of [0, 5]) {
    ranking.eventDominance(ranked, "bgm", ranking.X_MAX, room);
    for (const r of ranked) {
      const p = ranking.requiredPower(r, SKILLS, "S", 1, room);
      if (p !== null && !(finite(p) && p >= 0)) problem(`${name}: chart ${r.scoreId}: required power ${p}`);
    }
  }
  const one = ranked[0];
  const chance = ranking.reachChance(one, SKILLS, 300000, "S", 1, 0);
  if (chance !== null && !(chance >= 0 && chance <= 1)) problem(`${name}: reach chance ${chance}`);
  catalog.refigure(rows, data, scenario);
}
catalog.histogram(rows, (r) => r.level);

if (problems.length) {
  for (const m of problems.slice(0, 40)) console.log(m);
  if (problems.length > 40) console.log(`${problems.length - 40} more problems`);
  process.exit(1);
}
console.log(`page smoke test: ${rows.length} charts, ${SCENARIOS.length} scenarios, ${figures} chart figures; `
  + `plain kind ${kind}, scenarios free/ranks/just`);
