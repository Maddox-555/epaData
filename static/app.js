const views = {
  home: document.getElementById("view-home"),
  retrieval: document.getElementById("view-retrieval"),
  upload: document.getElementById("view-upload"),
  explorer: document.getElementById("view-explorer"),
  analysis: document.getElementById("view-analysis"),
  prediction: document.getElementById("view-prediction"),
  datasets: document.getElementById("view-datasets"),
  downloads: document.getElementById("view-downloads")
};

function showView(name) {
  Object.values(views).forEach(v => v.classList.remove("active"));
  views[name].classList.add("active");
  document.querySelectorAll(".nav button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
}

document.querySelectorAll(".nav button").forEach(btn => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

document.querySelectorAll("[data-view-target]").forEach(btn => {
  btn.addEventListener("click", () => showView(btn.getAttribute("data-view-target")));
});

const searchForm = document.getElementById("search-form");
const resultsBody = document.getElementById("results-body");
const resultsCount = document.getElementById("results-count");
const clearFiltersBtn = document.getElementById("clear-filters");
const downloadCsvBtn = document.getElementById("download-csv");
const pagination = document.getElementById("pagination");
const pageInfo = document.getElementById("page-info");
const prevPageBtn = document.getElementById("prev-page");
const nextPageBtn = document.getElementById("next-page");
const descriptionSearchBtn = document.getElementById("description-search-button");

const RESULT_COLUMNS = [
  row => `${row.facility_name} (${row.facility_id})`,
  row => row.unit_id,
  row => row.state,
  row => row.reporting_year,
  row => row.primary_fuel,
  row => row.unit_type,
  row => formatNumber(row.operating_time),
  row => formatNumber(row.gross_load),
  row => formatNumber(row.heat_input),
  row => formatNumber(row.co2_mass),
  row => formatNumber(row.so2_mass),
  row => formatNumber(row.nox_mass)
];
const NUMERIC_COLUMN_START = 6;

let lastResponse = null;
let lastSearchParams = null;

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function searchParams() {
  const params = new URLSearchParams();
  new FormData(searchForm).forEach((value, key) => {
    const text = String(value).trim();
    if (text !== "") params.append(key, text);
  });
  return params;
}

function renderResults(data) {
  lastResponse = data;
  resultsBody.replaceChildren();
  if (data.records.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = RESULT_COLUMNS.length;
    td.className = "empty-state";
    td.textContent = "No records match these filters.";
    tr.appendChild(td);
    resultsBody.appendChild(tr);
  }
  data.records.forEach(row => {
    const tr = document.createElement("tr");
    RESULT_COLUMNS.forEach((column, index) => {
      const td = document.createElement("td");
      const value = column(row);
      if (index === 0) {
        const link = document.createElement("a");
        link.href = `/unit/${encodeURIComponent(row.facility_id)}/${encodeURIComponent(row.unit_id)}`;
        link.textContent = value;
        td.appendChild(link);
      } else {
        td.textContent = value === null || value === undefined || value === "" ? "—" : value;
      }
      if (index >= NUMERIC_COLUMN_START) td.className = "text-right";
      tr.appendChild(td);
    });
    resultsBody.appendChild(tr);
  });

  const { total, offset, limit, count } = data;
  resultsCount.textContent = total === 0
    ? "0 results"
    : `Showing ${(offset + 1).toLocaleString()}–${(offset + count).toLocaleString()} of ${total.toLocaleString()} results`;
  const pages = Math.max(1, Math.ceil(total / limit));
  pageInfo.textContent = `Page ${Math.floor(offset / limit) + 1} of ${pages.toLocaleString()}`;
  prevPageBtn.disabled = offset === 0;
  nextPageBtn.disabled = offset + limit >= total;
  pagination.hidden = total === 0;
}

function runSearch(offset = 0, baseParams = null) {
  const params = baseParams ? new URLSearchParams(baseParams) : searchParams();
  params.delete("offset");
  lastSearchParams = new URLSearchParams(params);
  if (offset > 0) params.set("offset", offset);
  history.replaceState(null, "", "?" + params.toString());
  resultsCount.textContent = "Searching…";
  fetch("/api/annual-records?" + params.toString())
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok) throw new Error(data.error || "Search failed.");
      renderResults(data);
    })
    .catch(error => {
      lastResponse = null;
      resultsBody.replaceChildren();
      pagination.hidden = true;
      resultsCount.textContent = error.message;
    });
}

searchForm.addEventListener("submit", event => {
  event.preventDefault();
  runSearch(0);
});

prevPageBtn.addEventListener("click", () => {
  if (lastResponse) runSearch(Math.max(0, lastResponse.offset - lastResponse.limit), lastSearchParams);
});

nextPageBtn.addEventListener("click", () => {
  if (lastResponse) runSearch(lastResponse.offset + lastResponse.limit, lastSearchParams);
});

descriptionSearchBtn.addEventListener("click", () => {
  const description = searchForm.elements.namedItem("description").value.trim();
  if (!description) {
    resultsCount.textContent = "Enter a description first, such as: coal units in Kentucky above 500k tons of CO2.";
    return;
  }
  const params = new URLSearchParams();
  params.set("description", description);
  runSearch(0, params);
});

document.querySelectorAll("#results-table th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const sortSelect = searchForm.elements.namedItem("sort");
    const orderSelect = searchForm.elements.namedItem("order");
    if (sortSelect.value === th.dataset.sort) {
      orderSelect.value = orderSelect.value === "desc" ? "asc" : "desc";
    } else {
      sortSelect.value = th.dataset.sort;
      orderSelect.value = th.dataset.defaultOrder || "desc";
    }
    runSearch(0);
  });
});

clearFiltersBtn.addEventListener("click", () => {
  searchForm.reset();
  lastResponse = null;
  resultsBody.replaceChildren();
  pagination.hidden = true;
  resultsCount.textContent = "Run a search to see results.";
  history.replaceState(null, "", window.location.pathname);
});

downloadCsvBtn.addEventListener("click", () => {
  window.location.href = "/api/annual-records.csv?" + searchParams().toString();
});

function restoreSearchFromUrl() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("analysis") === "1") return;
  if ([...params.keys()].length === 0) return;
  params.forEach((value, key) => {
    const field = searchForm.elements.namedItem(key);
    if (field) field.value = value;
  });
  showView("explorer");
  runSearch(Number(params.get("offset")) || 0);
}

restoreSearchFromUrl();

const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");
const uploadReport = document.getElementById("upload-report");
const uploadActions = document.getElementById("upload-actions");
const approveUploadBtn = document.getElementById("approve-upload");
const cancelUploadBtn = document.getElementById("cancel-upload");
let pendingDatasetId = null;

uploadForm.addEventListener("submit", event => {
  event.preventDefault();
  const formData = new FormData(uploadForm);
  uploadStatus.textContent = "Uploading and validating…";
  fetch("/api/data/upload", {
    method: "POST",
    body: formData
  })
    .then(r => r.json())
    .then(data => {
      uploadStatus.textContent = data.status || data.error || "Upload completed.";
      uploadReport.textContent = data.validation ? JSON.stringify({validation: data.validation, errors: data.errors || []}, null, 2) : "";
      pendingDatasetId = data.dataset_id && data.status === "pending" ? data.dataset_id : null;
      uploadActions.hidden = !pendingDatasetId;
      if (data.errors && data.errors.length) uploadStatus.textContent += ` ${data.errors.length} data-quality issue(s) must be resolved before approval.`;
    })
    .catch(() => {
      uploadStatus.textContent = "Upload failed.";
    });
});

function finishUploadAction(url, message) {
  fetch(url, {method: "POST"})
    .then(r => r.json().then(data => ({ok: r.ok, data})))
    .then(({ok, data}) => {
      uploadStatus.textContent = ok ? (data.status || message) : (data.error || "Upload action failed.");
      if (ok) {
        pendingDatasetId = null;
        uploadActions.hidden = true;
        loadDatasets();
        loadDownloads();
      }
    })
    .catch(() => { uploadStatus.textContent = "Upload action failed."; });
}

approveUploadBtn.addEventListener("click", () => finishUploadAction(`/api/data/upload/${pendingDatasetId}/approve`, "Upload approved."));
cancelUploadBtn.addEventListener("click", () => finishUploadAction(`/api/data/upload/${pendingDatasetId}/cancel`, "Upload cancelled."));

const datasetsBody = document.getElementById("datasets-body");

function loadDatasets() {
  fetch("/api/datasets")
    .then(r => r.json())
    .then(rows => {
      datasetsBody.innerHTML = "";
      rows.forEach(row => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${row.dataset_id}</td>
          <td>${row.dataset_name}</td>
          <td>${row.data_source}</td>
          <td>${row.reporting_year}</td>
          <td>${row.status}</td>
          <td>${row.raw_records}</td>
          <td>${row.accepted_records}</td>
          <td>${row.rejected_records}</td>
        `;
        datasetsBody.appendChild(tr);
      });
    });
}

const downloadList = document.getElementById("download-list");

function loadDownloads() {
  fetch("/api/datasets")
    .then(r => r.json())
    .then(rows => {
      downloadList.replaceChildren();
      rows.forEach(row => {
        const section = document.createElement("section");
        section.className = "download-item";
        const title = document.createElement("h2");
        title.textContent = `${row.dataset_name} (${row.status})`;
        section.appendChild(title);
        [[`/api/datasets/${row.dataset_id}.csv`, "Complete records"], [`/api/uploads/${row.dataset_id}/errors.csv`, "Validation errors"], [`/api/datasets/${row.dataset_id}/provenance.csv`, "Provenance"]].forEach(([href, label]) => {
          const link = document.createElement("a");
          link.className = "secondary download-link";
          link.href = href;
          link.textContent = label;
          section.appendChild(link);
        });
        downloadList.appendChild(section);
      });
    });
}

loadDatasets();
loadDownloads();

const retrievalForm = document.getElementById("retrieval-form");
const retrievalStatus = document.getElementById("retrieval-status");

retrievalForm.addEventListener("submit", event => {
  event.preventDefault();
  const payload = { params: {} };
  Array.from(retrievalForm.elements).forEach(el => {
    if (el.name && el.value) payload.params[el.name] = el.value;
  });
  payload.approve = true;
  retrievalStatus.textContent = "Retrieving from CAMPD…";
  fetch("/api/data/retrieve/epa-campd", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(r => r.json())
    .then(data => {
      retrievalStatus.textContent = data.status || data.error || "Retrieval completed.";
    })
    .catch(() => {
      retrievalStatus.textContent = "Retrieval failed.";
    });
});

// --- Analysis dashboard ---
const analysisForm = document.getElementById("analysis-form");
const impactForm = document.getElementById("impact-form");
const analysisStatus = document.getElementById("analysis-status");
const analysisCards = document.getElementById("analysis-cards");
const comparisonForm = document.getElementById("comparison-form");
const comparisonResult = document.getElementById("comparison-result");
const predictionForm = document.getElementById("prediction-form");
const predictionStatus = document.getElementById("prediction-status");
const predictionSplits = document.getElementById("prediction-splits");
const predictionResults = document.getElementById("prediction-results");
const predictionSummary = document.getElementById("prediction-summary");
const downloadPrediction = document.getElementById("download-prediction");
const sensitivityForm = document.getElementById("sensitivity-form");
const predictionAnalysisStatus = document.getElementById("prediction-analysis-status");
const predictionAnalysisWarning = document.getElementById("prediction-analysis-warning");
const downloadSensitivity = document.getElementById("download-sensitivity");
let currentAnalysisParams = new URLSearchParams();
let currentAnalysisDataset = null;
let currentAnalysisSummary = null;

function analysisParams(form, includeMetric = true) {
  const params = new URLSearchParams();
  new FormData(form).forEach((value, key) => {
    if (String(value).trim() !== "" && (includeMetric || !["metric", "group_by"].includes(key))) {
      params.set(key, String(value).trim());
    }
  });
  return params;
}

function renderStatCards(summary) {
  const values = [
    ["Count", summary.count],
    ["Missing", summary.missing],
    ["Minimum", summary.min],
    ["Maximum", summary.max],
    ["Mean", summary.mean],
    ["Median", summary.median],
    ["Std. deviation", summary.stddev],
    ["Q1", summary.q1],
    ["Q3", summary.q3],
    ["IQR", summary.iqr],
    ["P90", summary.percentiles && summary.percentiles["0.9"]],
    ["P95", summary.percentiles && summary.percentiles["0.95"]]
  ];
  analysisCards.replaceChildren(...values.map(([label, value]) => {
    const card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = `<span>${label}</span><strong>${value === null || value === undefined ? "—" : formatNumber(value)}</strong>`;
    return card;
  }));
}

function plotLayout(title, xTitle, yTitle) {
  return {
    title: {text: title, font: {size: 17}},
    paper_bgcolor: "white",
    plot_bgcolor: "white",
    margin: {t: 50, r: 25, b: 65, l: 70},
    xaxis: {title: xTitle},
    yaxis: {title: yTitle, gridcolor: "#e5e7eb"},
    font: {color: "#1f2937"}
  };
}

const metricLabels = {
  co2_mass: "CO₂ emissions (short tons)",
  so2_mass: "SO₂ emissions (short tons)",
  nox_mass: "NOx emissions (short tons)",
  gross_load: "Gross load (MWh)",
  heat_input: "Heat input (mmBtu)",
  co2_intensity: "CO₂ intensity (short tons/MWh)",
  so2_intensity: "SO₂ intensity (short tons/MWh)",
  nox_intensity: "NOx intensity (short tons/MWh)",
  heat_rate: "Heat rate (mmBtu/MWh)"
};

function renderSummary(summary, metric, groupBy, chartType, records) {
  renderStatCards(summary.summary);
  const rows = summary.results.slice().sort((a, b) => (b.mean ?? 0) - (a.mean ?? 0)).slice(0, 25);
  const values = records.map(row => row[metric]).filter(value => value !== null && value !== undefined).map(Number);
  let traces;
  if (chartType === "histogram") {
    traces = [{x: values, type: "histogram", marker: {color: "#10b981"}, hovertemplate: "%{x:,.3f}<extra></extra>"}];
  } else if (chartType === "box") {
    traces = [{y: values, type: "box", name: metric, marker: {color: "#10b981"}, boxmean: true}];
  } else if (chartType === "scatter") {
    const xMetric = metric === "gross_load" ? "heat_input" : "gross_load";
    traces = [{x: records.map(row => row[xMetric]), y: records.map(row => row[metric]), mode: "markers", type: "scatter", text: records.map(row => row.facility_name), marker: {color: "#10b981", opacity: 0.7}}];
  } else if (chartType === "correlation") {
    const metricNames = ["operating_time", "gross_load", "heat_input", "co2_mass", "so2_mass", "nox_mass"];
    const matrix = metricNames.map(first => metricNames.map(second => {
      const pairs = records.filter(row => row[first] !== null && row[second] !== null);
      if (pairs.length < 2) return 0;
      const a = pairs.map(row => Number(row[first]));
      const b = pairs.map(row => Number(row[second]));
      const ma = a.reduce((s, v) => s + v, 0) / a.length;
      const mb = b.reduce((s, v) => s + v, 0) / b.length;
      const numerator = a.reduce((s, v, i) => s + (v - ma) * (b[i] - mb), 0);
      const denominator = Math.sqrt(a.reduce((s, v) => s + (v - ma) ** 2, 0) * b.reduce((s, v) => s + (v - mb) ** 2, 0));
      return denominator ? numerator / denominator : 0;
    }));
    traces = [{z: matrix, x: metricNames, y: metricNames, type: "heatmap", colorscale: "RdBu", zmin: -1, zmax: 1}];
  } else if (chartType === "map") {
    traces = [{
      type: "scattergeo", mode: "markers",
      lat: records.map(row => row.latitude).filter(value => value !== null),
      lon: records.map(row => row.longitude).filter(value => value !== null),
      text: records.filter(row => row.latitude !== null && row.longitude !== null).map(row => `${row.facility_name}: ${row[metric] ?? "missing"}`),
      marker: {size: 8, color: records.filter(row => row.latitude !== null && row.longitude !== null).map(row => Number(row[metric] || 0)), colorscale: "Viridis", showscale: true}
    }];
  } else {
    traces = [{
      x: rows.map(row => row.group_name), y: rows.map(row => chartType === "line" ? row.mean : row.mean),
      type: chartType === "line" ? "scatter" : "bar", mode: chartType === "line" ? "lines+markers" : undefined,
      marker: {color: "#10b981"}, line: {color: "#10b981"},
      hovertemplate: "%{x}<br>Mean: %{y:,.3f}<extra></extra>"
    }];
  }
  const layout = plotLayout(`${metricLabels[metric] || metric} visualization`, groupBy, metricLabels[metric] || "Value");
  if (chartType === "map") {
    delete layout.xaxis;
    delete layout.yaxis;
    layout.geo = {scope: "usa", projection: {type: "albers usa"}, showland: true, landcolor: "#f3f4f6", showcountries: true};
  }
  Plotly.newPlot("summary-chart", traces, layout, {responsive: true, displaylogo: false});
}

function renderTrend(records, metric) {
  const byYear = {};
  records.forEach(row => {
    const value = row[metric];
    if (value !== null && value !== undefined) {
      byYear[row.reporting_year] = byYear[row.reporting_year] || [];
      byYear[row.reporting_year].push(Number(value));
    }
  });
  const years = Object.keys(byYear).sort();
  const means = years.map(year => byYear[year].reduce((sum, value) => sum + value, 0) / byYear[year].length);
  Plotly.newPlot("trend-chart", [{
    x: years, y: means, type: "scatter", mode: "lines+markers",
    line: {color: "#2563eb"}, hovertemplate: "%{x}<br>Mean: %{y:,.3f}<extra></extra>"
  }], plotLayout(`${metricLabels[metric] || metric} trend`, "Reporting year", metricLabels[metric] || "Mean value"), {responsive: true, displaylogo: false});
}

async function loadAnalysis() {
  const params = analysisParams(analysisForm);
  const chartType = analysisForm.elements.namedItem("chart_type").value;
  params.delete("chart_type");
  currentAnalysisParams = params;
  const urlParams = new URLSearchParams(params);
  urlParams.set("chart_type", chartType);
  history.replaceState(null, "", `?analysis=1&${urlParams.toString()}`);
  analysisStatus.textContent = "Loading analysis…";
  try {
    const [summaryResponse, datasetResponse] = await Promise.all([
      fetch(`/api/analysis/summary?${params}`),
      fetch(`/api/analysis/dataset?${params}&limit=1000`)
    ]);
    const summary = await summaryResponse.json();
    const dataset = await datasetResponse.json();
    if (!summaryResponse.ok) throw new Error(summary.error || "Summary request failed.");
    if (!datasetResponse.ok) throw new Error(dataset.error || "Dataset request failed.");
    currentAnalysisDataset = dataset;
    currentAnalysisSummary = summary;
    renderSummary(summary, params.get("metric"), params.get("group_by"), chartType, dataset.records);
    renderTrend(dataset.records, params.get("metric"));
    document.getElementById("analysis-record-count").textContent = dataset.selected_record_count.toLocaleString();
    document.getElementById("analysis-facility-count").textContent = dataset.selected_facility_count.toLocaleString();
    document.getElementById("analysis-unit-count").textContent = dataset.selected_unit_count.toLocaleString();
    document.getElementById("analysis-year-count").textContent = dataset.selected_years.length.toLocaleString();
    document.getElementById("analysis-variables").textContent = dataset.available_variables.join(", ");
    const missing = Object.entries(dataset.missing_values).filter(([, value]) => value > 0);
    document.getElementById("analysis-missing").textContent = missing.length ? missing.map(([name, value]) => `${name}: ${value}`).join(" • ") : "No missing values in the selected variables.";
    analysisStatus.textContent = `${dataset.selected_record_count.toLocaleString()} records (${dataset.returned_record_count.toLocaleString()} plotted) across ${dataset.selected_facility_count.toLocaleString()} facilities and ${dataset.selected_years.length} years.`;
  } catch (error) {
    analysisStatus.textContent = error.message;
    analysisCards.replaceChildren();
  }
}

async function loadImpact() {
  const analysisFilters = analysisParams(analysisForm, false);
  const impactParams = analysisParams(impactForm);
  [
    "facility_id", "unit_id", "state", "reporting_year", "reporting_year_min",
    "reporting_year_max", "primary_fuel", "unit_type", "operating_time_min",
    "operating_time_max", "gross_load_min", "gross_load_max", "heat_input_min",
    "heat_input_max", "co2_mass_min", "co2_mass_max", "so2_mass_min",
    "so2_mass_max", "nox_mass_min", "nox_mass_max", "so2_control_information",
    "nox_control_information", "pm_control_information", "program_code"
  ].forEach(key => {
    if (analysisFilters.has(key)) impactParams.set(key, analysisFilters.get(key));
  });
  const response = await fetch(`/api/analysis/impact?${impactParams}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Impact request failed.");
  const units = data.factor_units && data.factor_units.length ? data.factor_units.join("; ") : "No verified matching factor";
  const normalization = data.normalize_by ? `; normalized by ${data.normalize_by}` : "";
  const impactTotal = document.getElementById("impact-total");
  if (!data.matched_factor_count || !data.results.length) {
    impactTotal.textContent = `No matching TRACI 2.2 air-emission factor was found for ${data.impact_category} and the selected pollutant(s).`;
    Plotly.purge("impact-chart");
    document.getElementById("impact-chart").textContent = "No impact data is available for this selection.";
    return;
  }

  const traciVersion = data.traci_version || "2.2";
  impactTotal.textContent = `Total potential impact: ${formatNumber(data.total_impact)} ${units}${normalization} (TRACI ${traciVersion})`;
  const rows = data.results.slice().sort((a, b) => b.impact - a.impact).slice(0, 25);
  Plotly.newPlot("impact-chart", [{
    x: rows.map(row => row.group_key), y: rows.map(row => row.impact), type: "bar",
    marker: {color: "#f59e0b"}, hovertemplate: "%{x}<br>Impact: %{y:,.3f}<extra></extra>"
  }], plotLayout(`${data.impact_category} impact by ${data.group_by}`, data.group_by, "Potential impact"), {responsive: true, displaylogo: false});
}

analysisForm.addEventListener("submit", event => {
  event.preventDefault();
  loadAnalysis();
});

impactForm.addEventListener("submit", event => {
  event.preventDefault();
  loadImpact().catch(error => { document.getElementById("impact-total").textContent = error.message; });
});

predictionForm.addEventListener("submit", async event => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(predictionForm));
  predictionStatus.textContent = "Training chronological prediction models…";
  predictionSplits.replaceChildren();
  predictionResults.replaceChildren();
  predictionSummary.textContent = "";
  try {
    const response = await fetch(`/api/prediction?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Prediction request failed.");
    const predictionExportParams = new URLSearchParams();
    if (params.get("state")) predictionExportParams.set("state", params.get("state"));
    downloadPrediction.href = `/api/prediction.csv?${predictionExportParams}`;
    downloadPrediction.hidden = false;
    const test = data.splits.test;
    predictionStatus.textContent = `${data.objective} Selected model: ${data.selected_model.replace("_", " ")}.`;
    [
      ["Training", `${data.splits.train.target_years[0]}–${data.splits.train.target_years[1]}`, data.splits.train.records],
      ["Validation", data.splits.validation.target_year, data.splits.validation.records],
      ["Final test", test.target_year, test.records]
    ].forEach(([label, years, records]) => {
      const card = document.createElement("div");
      card.className = "prediction-split";
      card.innerHTML = `<span>${label}</span><strong>${years}</strong><small>${Number(records).toLocaleString()} records</small>`;
      predictionSplits.appendChild(card);
    });
    const labels = {
      mean_baseline: "Mean baseline",
      ridge: "Ridge regression",
      random_forest: "Random forest"
    };
    Object.entries(data.models).forEach(([name, scores]) => {
      const row = document.createElement("tr");
      const values = [
        labels[name] || name,
        formatNumber(scores.validation.mae),
        formatNumber(scores.test.mae),
        formatNumber(scores.test.rmse),
        formatNumber(scores.test.r2),
        scores.test.mape === null ? "—" : `${formatNumber(scores.test.mape)}%`
      ];
      values.forEach(value => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
      if (name === data.selected_model) row.className = "selected-model";
      predictionResults.appendChild(row);
    });
    predictionSummary.textContent = `Features: ${data.features.join(", ")}. ${data.feature_policy}`;
    const names = Object.keys(data.models);
    Plotly.newPlot("prediction-chart", [
      {x: names, y: names.map(name => data.models[name].test.mae), type: "bar", name: "MAE", marker: {color: "#2563eb"}},
      {x: names, y: names.map(name => data.models[name].test.rmse), type: "bar", name: "RMSE", marker: {color: "#f59e0b"}}
    ], plotLayout("Test-set prediction error", "Model", "Error (short tons)"), {responsive: true, displaylogo: false, barmode: "group"});
    const importance = data.feature_importance;
    const importanceLayout = plotLayout(`${labels[data.selected_model] || data.selected_model} feature importance`, "Importance", "Feature");
    importanceLayout.margin = {...importanceLayout.margin, l: 240};
    importanceLayout.yaxis = {...importanceLayout.yaxis, automargin: true, title: {text: "Feature", standoff: 45}};
    Plotly.newPlot("prediction-importance-chart", [{
      x: importance.map(item => item.importance).reverse(),
      y: importance.map(item => item.feature).reverse(),
      type: "bar",
      orientation: "h",
      marker: {color: "#10b981"},
      hovertemplate: "%{y}<br>Importance: %{x:.4f}<extra></extra>"
    }], importanceLayout, {responsive: true, displaylogo: false});
    const trend = data.co2_trend;
    Plotly.newPlot("prediction-trend-chart", [
      {x: trend.map(item => item.year), y: trend.map(item => item.actual_co2), type: "scatter", mode: "lines+markers", name: "Actual CO₂", line: {color: "#2563eb"}, connectgaps: false},
      {x: trend.map(item => item.year), y: trend.map(item => item.predicted_co2), type: "scatter", mode: "lines+markers", name: "Predicted CO₂", line: {color: "#f59e0b", dash: "dash"}, connectgaps: false}
    ], plotLayout("Historical CO₂ and out-of-sample prediction", "Reporting year", "CO₂ mass (short tons)"), {responsive: true, displaylogo: false});
  } catch (error) {
    predictionStatus.textContent = error.message;
    predictionSummary.textContent = "";
    Plotly.purge("prediction-chart");
    Plotly.purge("prediction-importance-chart");
    Plotly.purge("prediction-trend-chart");
  }
});

function setPredictionDownload(rows, filename) {
  const headers = Object.keys(rows[0] || {});
  const csv = [headers.join(","), ...rows.map(row => headers.map(header => JSON.stringify(row[header] ?? "")).join(","))].join("\n");
  downloadSensitivity.href = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
  downloadSensitivity.download = filename;
  downloadSensitivity.hidden = false;
}

sensitivityForm.addEventListener("submit", async event => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(sensitivityForm));
  const state = predictionForm.elements.namedItem("state").value.trim();
  if (state) params.set("state", state);
  predictionAnalysisStatus.textContent = "Running sensitivity analysis…";
  predictionAnalysisWarning.textContent = "";
  try {
    const response = await fetch(`/api/prediction/sensitivity?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Sensitivity request failed.");
    predictionAnalysisStatus.textContent = `Model: ${data.model.replace("_", " ")}. Baseline prediction: ${formatNumber(data.baseline.prediction)} short tons.`;
    predictionAnalysisWarning.textContent = data.warning || "";
    const sensitivityLayout = plotLayout(`${data.feature} sensitivity`, data.feature, "Predicted CO₂ (short tons)");
    sensitivityLayout.shapes = [
      {type: "line", x0: data.baseline.input_value, x1: data.baseline.input_value, y0: 0, y1: 1, yref: "paper", line: {color: "#f59e0b", dash: "dot"}},
      {type: "line", x0: 0, x1: 1, xref: "paper", y0: data.baseline.prediction, y1: data.baseline.prediction, line: {color: "#f59e0b", dash: "dot"}}
    ];
    if (data.training_range.minimum !== null && data.training_range.maximum !== null) {
      const inputs = data.points.map(point => point.input_value);
      if (Math.min(...inputs) < data.training_range.minimum) {
        sensitivityLayout.shapes.push({type: "rect", x0: Math.min(...inputs), x1: data.training_range.minimum, y0: 0, y1: 1, yref: "paper", fillcolor: "#fca5a5", opacity: 0.25, line: {width: 0}});
      }
      if (Math.max(...inputs) > data.training_range.maximum) {
        sensitivityLayout.shapes.push({type: "rect", x0: data.training_range.maximum, x1: Math.max(...inputs), y0: 0, y1: 1, yref: "paper", fillcolor: "#fca5a5", opacity: 0.25, line: {width: 0}});
      }
    }
    Plotly.newPlot("sensitivity-chart", [{
      x: data.points.map(point => point.input_value),
      y: data.points.map(point => point.prediction),
      type: "scatter", mode: "lines+markers", name: "Prediction",
      marker: {color: "#2563eb"},
      customdata: data.points.map(point => [point.absolute_change, point.percentage_change, point.outside_training_range ? "Yes" : "No"]),
      hovertemplate: "Input: %{x:,.3f}<br>Prediction: %{y:,.3f}<br>Change: %{customdata[0]:,.3f}<br>Change: %{customdata[1]:.2f}%<br>Outside training range: %{customdata[2]}<extra></extra>"
    }, {
      x: [data.baseline.input_value], y: [data.baseline.prediction],
      type: "scatter", mode: "markers", name: "Baseline", marker: {color: "#f59e0b", size: 12}
    }], sensitivityLayout, {responsive: true, displaylogo: false});
    setPredictionDownload(data.points.map(point => ({feature: data.feature, ...point})), "prediction-sensitivity.csv");
  } catch (error) {
    predictionAnalysisStatus.textContent = error.message;
    predictionAnalysisWarning.textContent = "";
    Plotly.purge("sensitivity-chart");
  }
});

document.getElementById("run-default-scenarios").addEventListener("click", async () => {
  const state = predictionForm.elements.namedItem("state").value.trim();
  predictionAnalysisStatus.textContent = "Running scenarios…";
  predictionAnalysisWarning.textContent = "";
  try {
    const response = await fetch("/api/prediction/scenarios", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        state,
        facility_id: sensitivityForm.elements.namedItem("facility_id").value.trim(),
        unit_id: sensitivityForm.elements.namedItem("unit_id").value.trim(),
        model: sensitivityForm.elements.namedItem("model").value,
        scenarios: [
          {name: "Gross load +10%", changes: {gross_load: {percent: 10}}},
          {name: "Heat input -5%", changes: {heat_input: {percent: -5}}},
          {name: "High-efficiency combination", changes: {gross_load: {percent: 10}, heat_input: {percent: -5}}}
        ]
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Scenario request failed.");
    predictionAnalysisStatus.textContent = `Model: ${data.model.replace("_", " ")}. Baseline prediction: ${formatNumber(data.baseline.prediction)} short tons.`;
    predictionAnalysisWarning.textContent = data.warning || "";
    Plotly.newPlot("scenario-chart", [{
      x: data.scenarios.map(scenario => scenario.name),
      y: data.scenarios.map(scenario => scenario.prediction),
      type: "bar", marker: {color: ["#2563eb", "#10b981", "#f59e0b", "#7c3aed"]},
      customdata: data.scenarios.map(scenario => [scenario.absolute_change, scenario.percentage_change, scenario.outside_training_range ? "Yes" : "No"]),
      hovertemplate: "%{x}<br>Prediction: %{y:,.3f}<br>Change: %{customdata[0]:,.3f}<br>Change: %{customdata[1]:.2f}%<br>Outside training range: %{customdata[2]}<extra></extra>"
    }], plotLayout("Scenario prediction comparison", "Scenario", "Predicted CO₂ (short tons)"), {responsive: true, displaylogo: false});
    setPredictionDownload(data.scenarios, "prediction-scenarios.csv");
  } catch (error) {
    predictionAnalysisStatus.textContent = error.message;
    Plotly.purge("scenario-chart");
  }
});

document.getElementById("clear-analysis").addEventListener("click", () => {
  analysisForm.reset();
  history.replaceState(null, "", window.location.pathname);
  loadAnalysis();
});

analysisForm.elements.namedItem("chart_type").addEventListener("change", () => {
  if (currentAnalysisSummary && currentAnalysisDataset) {
    renderSummary(currentAnalysisSummary, analysisForm.elements.namedItem("metric").value, analysisForm.elements.namedItem("group_by").value, analysisForm.elements.namedItem("chart_type").value, currentAnalysisDataset.records);
  }
});

function analysisDownload(endpoint) {
  const params = new URLSearchParams(currentAnalysisParams);
  params.set("metric", analysisForm.elements.namedItem("metric").value);
  params.set("group_by", analysisForm.elements.namedItem("group_by").value);
  window.location.href = `${endpoint}?${params}`;
}

document.getElementById("download-analysis-data").addEventListener("click", () => analysisDownload("/api/analysis/dataset.csv"));
document.getElementById("download-analysis-summary").addEventListener("click", () => analysisDownload("/api/analysis/summary.csv"));

comparisonForm.addEventListener("submit", async event => {
  event.preventDefault();
  const params = new URLSearchParams(currentAnalysisParams);
  params.set("metric", analysisForm.elements.namedItem("metric").value);
  params.set("group_by", analysisForm.elements.namedItem("group_by").value);
  params.set("normalize_by", comparisonForm.elements.namedItem("normalize_by").value);
  new FormData(comparisonForm).forEach((value, key) => params.set(key, String(value).trim()));
  comparisonResult.textContent = "Comparing…";
  try {
    const response = await fetch(`/api/analysis/compare?${params}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Comparison failed.");
    const normalization = result.normalize_by ? ` normalized by ${result.normalize_by}` : "";
    comparisonResult.textContent = `${result.group_a.group_name} vs ${result.group_b.group_name}${normalization}: ${result.metric} means ${formatNumber(result.group_a.mean)} vs ${formatNumber(result.group_b.mean)}; absolute difference ${formatNumber(result.absolute_difference)}; percentage difference ${result.percentage_difference === null ? "undefined" : formatNumber(result.percentage_difference) + "%"}.`;
    Plotly.newPlot("comparison-chart", [{
      x: [result.group_a.group_name, result.group_b.group_name],
      y: [result.group_a.mean, result.group_b.mean],
      type: "bar",
      marker: {color: ["#2563eb", "#f59e0b"]},
      hovertemplate: "%{x}<br>Mean: %{y:,.3f}<extra></extra>"
    }], plotLayout(`${result.metric} comparison${normalization}`, result.group_by, "Mean value"), {responsive: true, displaylogo: false});
  } catch (error) {
    comparisonResult.textContent = error.message;
    Plotly.purge("comparison-chart");
  }
});

document.querySelector('[data-view="analysis"]').addEventListener("click", () => {
  if (!analysisCards.children.length) loadAnalysis();
});

function restoreAnalysisFromUrl() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("analysis") !== "1") return;
  params.delete("analysis");
  params.forEach((value, key) => {
    const field = analysisForm.elements.namedItem(key);
    if (field) field.value = value;
  });
  showView("analysis");
  loadAnalysis();
}

restoreAnalysisFromUrl();