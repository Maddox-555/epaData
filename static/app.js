const views = {
  home: document.getElementById("view-home"),
  retrieval: document.getElementById("view-retrieval"),
  upload: document.getElementById("view-upload"),
  explorer: document.getElementById("view-explorer"),
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

function runSearch(offset = 0) {
  const params = searchParams();
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
  if (lastResponse) runSearch(Math.max(0, lastResponse.offset - lastResponse.limit));
});

nextPageBtn.addEventListener("click", () => {
  if (lastResponse) runSearch(lastResponse.offset + lastResponse.limit);
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
