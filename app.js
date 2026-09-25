const views = {
  home: document.getElementById("view-home"),
  retrieval: document.getElementById("view-retrieval"),
  upload: document.getElementById("view-upload"),
  explorer: document.getElementById("view-explorer"),
  datasets: document.getElementById("view-datasets")
};

document.querySelectorAll(".main-nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    Object.values(views).forEach(v => v.classList.remove("active"));
    views[btn.dataset.view].classList.add("active");
  });
});

document.querySelectorAll("[data-view-target]").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.getAttribute("data-view-target");
    Object.values(views).forEach(v => v.classList.remove("active"));
    views[target].classList.add("active");
  });
});

const searchForm = document.getElementById("search-form");
const resultsBody = document.getElementById("results-body");
const resultsCount = document.getElementById("results-count");
const clearFiltersBtn = document.getElementById("clear-filters");
const downloadCsvBtn = document.getElementById("download-csv");

searchForm.addEventListener("submit", event => {
  event.preventDefault();
  const params = new URLSearchParams();
  Array.from(searchForm.elements).forEach(el => {
    if (el.name && el.value) params.append(el.name, el.value);
  });
  fetch("/api/annual-records?" + params.toString())
    .then(r => r.json())
    .then(data => {
      resultsBody.innerHTML = "";
      data.records.forEach(row => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${row.facility_name}</td>
          <td>${row.unit_id}</td>
          <td>${row.state}</td>
          <td>${row.reporting_year}</td>
          <td>${row.co2_mass}</td>
          <td>${row.so2_mass}</td>
          <td>${row.nox_mass}</td>
        `;
        resultsBody.appendChild(tr);
      });
      resultsCount.textContent = data.count + " results";
    });
});

clearFiltersBtn.addEventListener("click", () => {
  Array.from(searchForm.elements).forEach(el => {
    if (el.tagName === "INPUT") el.value = "";
  });
  resultsBody.innerHTML = "";
  resultsCount.textContent = "0 results";
});

downloadCsvBtn.addEventListener("click", () => {
  const params = new URLSearchParams();
  Array.from(searchForm.elements).forEach(el => {
    if (el.name && el.value) params.append(el.name, el.value);
  });
  window.location.href = "/api/annual-records.csv?" + params.toString();
});

const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");

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
    })
    .catch(() => {
      uploadStatus.textContent = "Upload failed.";
    });
});

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

loadDatasets();

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
