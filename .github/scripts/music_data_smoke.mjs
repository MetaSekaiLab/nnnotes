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

// Aptitude is a separate view, never folded into the default song ranking. Older pinned pages may lack this API;
// report that explicitly until PLAYER_REF is deliberately moved to a page with the aptitude UI.
let aptitudeFigures = 0;
const aptitudeApi = ["aptitudeShapes", "chartVariants", "aptitudeFigures", "aptitudeRate", "aptitudeSe",
  "masterSkillFactor"].every((k) => typeof ranking[k] === "function")
  && ["gekisouSkill", "shapeSkills", "shapeBands"].every((k) => typeof catalog[k] === "function");
const near = (a, b) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b));
if (aptitudeApi && data.deck?.gekisouAptitude) {
  const shapes = ranking.aptitudeShapes(data);
  if (shapes.size !== data.deck.gekisouAptitude.shapes.length) problem("aptitudeShapes: missing shapes");
  for (const shape of shapes.values()) {
    const skills = catalog.shapeSkills(data, shape, "zh-Hant");
    if (skills.length !== new Set(shape.skills.map((s) => `${s.id}:${s.level}`)).size) {
      problem(`shape ${shape.id}: shapeSkills count`);
    }
    const table = shape.source === "support" ? "supportSkills" : "skills";
    for (const skill of skills) {
      const named = catalog.gekisouSkill(data, table, skill.id, "zh-Hant");
      if (!named.name || named.name !== skill.name) problem(`shape ${shape.id}: skill name lookup`);
    }
    const bands = catalog.shapeBands(data, shape, "zh-Hant");
    if (bands.length !== new Set(shape.skills.flatMap((s) => s.bandIds || [])).size || bands.some((s) => !s)) {
      problem(`shape ${shape.id}: shapeBands lookup`);
    }
  }
  const power = data.deck.model.power;
  for (const { chart } of charts) {
    const d = chart.deck;
    if (!d) continue;
    const variants = ranking.chartVariants(d);
    if (variants.length !== (d.unplayable ? 0 : d.gekisouAptitude?.variants.length || 0)) {
      problem(`chart ${chart.scoreId}: chartVariants count`);
    }
    // Removing aptitude must not alter any default figure.
    const baseline = ranking.chartFigures(d, kind, power);
    const without = ranking.chartFigures({ ...d, gekisouAptitude: null }, kind, power);
    if (JSON.stringify(baseline) !== JSON.stringify(without)) problem(`chart ${chart.scoreId}: aptitude changes default`);
    for (const variant of variants) {
      for (const [name, scenario] of SCENARIOS) {
        const tag = `aptitude chart ${chart.scoreId} shape ${variant.shape}, ${name}`;
        const f = ranking.aptitudeFigures(variant, d.ranges, power, scenario);
        if (scenario?.mode === "free") {
          if (f !== null) problem(`${tag}: Free Live has aptitude`);
          continue;
        }
        aptitudeFigures++;
        if (!f || !finite(f.base)) { problem(`${tag}: missing or nonfinite base`); continue; }
        if (f.weights !== null && (f.weights.length !== d.positions || !f.weights.every(finite))) {
          problem(`${tag}: invalid weights`);
        }
        const zero = Array(d.positions).fill(0);
        if (!near(ranking.aptitudeRate(f, zero), f.base)) problem(`${tag}: no-skill rate`);
        const rate = ranking.aptitudeRate(f, SKILLS);
        if (f.weights === null ? rate !== null : !finite(rate)) problem(`${tag}: missing cross-term handling`);
        if (ranking.aptitudeSe(f, SKILLS) !== null) problem(`${tag}: SE assigned without covariance`);
        if (scenario === null) {
          if (!near(f.base, variant.score[0] / power)
            || !near(ranking.aptitudeSe(f, zero), variant.score[1] / power)) problem(`${tag}: raw mean/SE`);
        } else if (ranking.aptitudeSe(f, zero) !== null) problem(`${tag}: transformed SE is not null`);
      }
      // Only deterministic deltas describe the individual check seed. Never reconstruct a stochastic check
      // from sampled means. Ordinary check cards are positional, unlike the UI's random-order expectation.
      if (!variant.deterministic || kind === null) continue;
      const c = variant.check;
      const seed = d.seeds.find((s) => s.seed === c.seed);
      const sc = { mode: "battle", ranks: c.ranks, just: 1, great: 0 };
      const base = ranking.scenarioSeed(seed, d.ranges, kind, sc);
      const gain = ranking.aptitudeFigures(variant, d.ranges, power, sc);
      if (!base || !gain?.weights) continue;
      const predicted = data.deck.model.checkPower * ((base.score / power) + gain.base
        + c.deck.reduce((sum, card, k) => sum + (card ? ranking.masterSkillFactor(card[1])
          * (base.weights[k] + gain.weights[k]) : 0), 0));
      // Exported weight rounding adds a small reconstruction error on top of the engine's bound.
      const rounding = data.deck.model.checkPower * 1e-7 * (1 + d.ranges.length) * d.positions;
      if (!finite(predicted) || Math.abs(predicted - c.exact) > c.bound + rounding) {
        problem(`aptitude chart ${chart.scoreId} shape ${variant.shape}: deterministic check reconstruction`);
      }
    }
  }
}

if (problems.length) {
  for (const m of problems.slice(0, 40)) console.log(m);
  if (problems.length > 40) console.log(`${problems.length - 40} more problems`);
  process.exit(1);
}
console.log(`page smoke test: ${rows.length} charts, ${SCENARIOS.length} scenarios, ${figures} chart figures; `
  + `plain kind ${kind}, scenarios free/ranks/just; aptitude ${aptitudeApi ? aptitudeFigures + " figures" : "API unavailable (skipped)"}`);
