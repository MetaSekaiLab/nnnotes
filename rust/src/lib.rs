//! `nnnotes._deck`: the chart statistics of the deck model ournotes-sim (`ournotes-deck.chart-stats/2`) on a deck
//! data document held in memory, the charts measured in parallel.

use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use ournotes_sim::chartstats::{self, AptitudeOptions, ChartStats, GEKISOU_SEEDS, Options};
use ournotes_sim::data::DeckData;
use ournotes_sim::error::Error;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

fn value_error(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// The statistics of every chart, in chart order, on `workers` threads.
fn measure(data: &DeckData, options: &Options, workers: usize) -> Result<Vec<ChartStats>, Error> {
    let kinds = chartstats::kinds(&data.master);
    let n = data.charts.len();
    let next = AtomicUsize::new(0);
    let out: Mutex<Vec<Option<Result<ChartStats, Error>>>> = Mutex::new((0..n).map(|_| None).collect());
    std::thread::scope(|s| {
        for _ in 0..workers.clamp(1, n.max(1)) {
            s.spawn(|| {
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= n {
                        break;
                    }
                    let c = &data.charts[i];
                    // as chartstats::document names a chart's domain errors
                    let r = chartstats::chart_stats_with(&data.master, c, &kinds, options).map_err(|e| match e {
                        Error::Domain(m) => Error::Domain(format!("chart {}: {m}", c.score_id)),
                        e => e,
                    });
                    let failed = r.is_err();
                    out.lock().expect("results")[i] = Some(r);
                    if failed {
                        next.store(n, Ordering::Relaxed);
                    }
                }
            });
        }
    });
    // charts are taken in order and a taken chart always completes, so every chart before the first failure has
    // its result: the error returned is the first in chart order, as the command's
    let mut stats = Vec::with_capacity(n);
    for r in out.into_inner().expect("results") {
        stats.push(r.expect("a chart before the first failure has run")?);
    }
    Ok(stats)
}

/// chart_stats(data, seeds=None, workers=None, aptitude=True, aptitude_max_seeds=None,
///             aptitude_cross_seeds=None) -> str
///
/// The `ournotes-deck.chart-stats/2` document (JSON text) of a deck data document (`nnnotes.deck-data/1` JSON text),
/// as `ournotes-deck chart-stats` writes it. `seeds`: the size of the seed set of charts with a luck range (default
/// 8); `workers`: threads measuring charts (default: the available parallelism). Raises ValueError for data the
/// deck model cannot read or a chart whose check deck fails. `aptitude`: measure single Gekisou skill shapes
/// (default true); `aptitude_max_seeds` and `aptitude_cross_seeds`: sample caps (defaults 65536 and 64).
#[pyfunction]
#[pyo3(signature = (data, seeds=None, workers=None, aptitude=true, aptitude_max_seeds=None, aptitude_cross_seeds=None))]
fn chart_stats(
    py: Python<'_>,
    data: &str,
    seeds: Option<usize>,
    workers: Option<usize>,
    aptitude: bool,
    aptitude_max_seeds: Option<usize>,
    aptitude_cross_seeds: Option<usize>,
) -> PyResult<String> {
    let seeds = seeds.unwrap_or(GEKISOU_SEEDS);
    let defaults = AptitudeOptions::default();
    let options = Options {
        seeds,
        aptitude: aptitude.then_some(AptitudeOptions {
            max_seeds: aptitude_max_seeds.unwrap_or(defaults.max_seeds),
            cross_seeds: aptitude_cross_seeds.unwrap_or(defaults.cross_seeds),
        }),
    };
    let workers = workers.unwrap_or_else(|| std::thread::available_parallelism().map_or(1, |n| n.get()));
    py.detach(|| {
        let mut data = DeckData::from_json(data).map_err(value_error)?;
        let charts = std::mem::take(&mut data.charts);
        // the document of no chart: the format, source, model and kinds, exactly as the command writes them
        let mut doc = chartstats::document_with(&data, &options).map_err(value_error)?;
        data.charts = charts;
        let stats = measure(&data, &options, workers).map_err(value_error)?;
        doc["charts"] = serde_json::to_value(stats).map_err(value_error)?;
        serde_json::to_string(&doc).map_err(value_error)
    })
}

/// info() -> dict: the deck model of this module (name: the ournotes-deck repository; version, source and commit:
/// its crate ournotes-sim as built; sourceSha256: the SHA-256 of that crate's sources, equal for builds that run the
/// same model) and the formats it reads and writes.
#[pyfunction]
fn info(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", "ournotes-deck")?;
    d.set_item("version", env!("DECK_VERSION"))?;
    d.set_item("source", env!("DECK_SOURCE"))?;
    d.set_item("commit", env!("DECK_COMMIT"))?;
    d.set_item("sourceSha256", ournotes_sim::SOURCE_SHA256)?;
    d.set_item("dataFormat", ournotes_sim::data::FORMAT)?;
    d.set_item("format", chartstats::FORMAT)?;
    Ok(d)
}

#[pymodule]
fn _deck(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chart_stats, m)?)?;
    m.add_function(wrap_pyfunction!(info, m)?)?;
    Ok(())
}
