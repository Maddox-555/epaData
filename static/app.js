const views = {
  home: document.getElementById("view-home"),
  retrieval: document.getElementById("view-retrieval"),
  upload: document.getElementById("view-upload"),
  explorer: document.getElementById("view-explorer"),
  analysis: document.getElementById("view-analysis"),
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
      renderChart(data.records); // NEW: Draw chart on success
    })
    .catch(error => {
      lastResponse = null;
      resultsBody.replaceChildren();
      pagination.hidden = true;
      resultsCount.textContent = error.message;
      renderChart([]); // NEW: Clear chart on error
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
  renderChart([]); // NEW: Clear chart when filters are cleared
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
  const layout = plotLayout(`${metric} visualization`, groupBy, "Value");
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
  }], plotLayout(`${metric} trend`, "Reporting year", "Mean value"), {responsive: true, displaylogo: false});
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
  ["state", "reporting_year_min", "reporting_year_max", "primary_fuel"].forEach(key => {
    if (analysisFilters.has(key)) impactParams.set(key, analysisFilters.get(key));
  });
  const response = await fetch(`/api/analysis/impact?${impactParams}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Impact request failed.");
  const units = data.factor_units && data.factor_units.length ? data.factor_units.join("; ") : "No verified matching factor";
  const normalization = data.normalize_by ? `; normalized by ${data.normalize_by}` : "";
  document.getElementById("impact-total").textContent = `Total potential impact: ${formatNumber(data.total_impact)} ${units}${normalization} (TRACI ${data.traci_version})`;
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
  new FormData(comparisonForm).forEach((value, key) => params.set(key, String(value).trim()));
  comparisonResult.textContent = "Comparing…";
  try {
    const response = await fetch(`/api/analysis/compare?${params}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Comparison failed.");
    comparisonResult.textContent = `${result.group_a.group_name} vs ${result.group_b.group_name}: absolute difference ${formatNumber(result.absolute_difference)}; percentage difference ${result.percentage_difference === null ? "undefined" : formatNumber(result.percentage_difference) + "%"}.`;
  } catch (error) {
    comparisonResult.textContent = error.message;
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