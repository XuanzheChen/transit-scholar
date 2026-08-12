/* Stage 7 acceptance panel — vanilla JS, no build, no network assets.
 * Talks to the FastAPI backend at /api/*. Maintenance actions are preview-only:
 * the only maintenance mutation is POST .../preview. */

(function () {
  "use strict";

  const SEL = {
    dataRootBadge: document.getElementById("data-root-badge"),
    healthBadge: document.getElementById("health-badge"),
    footerMsg: document.getElementById("footer-msg"),
    importForm: document.getElementById("import-form"),
    importFile: document.getElementById("import-file"),
    importRun: document.getElementById("import-run"),
    lastResult: document.getElementById("last-result"),
    papersStatus: document.getElementById("papers-status"),
    papersIncludeDeleted: document.getElementById("papers-include-deleted"),
    papersRefresh: document.getElementById("papers-refresh"),
    papersList: document.getElementById("papers-list"),
    paperDetail: document.getElementById("paper-detail"),
    enrichment: document.getElementById("enrichment"),
    enrichmentRefresh: document.getElementById("enrichment-refresh"),
    gate: document.getElementById("gate"),
    metadataCandidates: document.getElementById("metadata-candidates"),
    citations: document.getElementById("citations"),
    trace: document.getElementById("trace"),
    maintenanceRefresh: document.getElementById("maintenance-refresh"),
    maintenanceList: document.getElementById("maintenance-list"),
    maintenancePreview: document.getElementById("maintenance-preview"),
  };

  // Track the most recent selections so panels stay in sync.
  let lastImportResult = null;
  let selectedPaperId = null;
  let selectedItemId = null;
  const maintenanceItemCache = {}; // item_id -> item (holds recommended_actions)

  function msg(text) {
    SEL.footerMsg.textContent = text;
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function api(path, options) {
    const resp = await fetch(path, options);
    const contentType = resp.headers.get("content-type") || "";
    let body = null;
    if (contentType.includes("application/json")) {
      body = await resp.json();
    } else if (contentType.includes("text/html") || contentType.includes("text/plain")) {
      body = await resp.text();
    } else {
      body = await resp.text();
    }
    if (!resp.ok) {
      const detail = (body && body.detail) || resp.statusText || "request failed";
      throw new Error(`${resp.status}: ${detail}`);
    }
    return body;
  }

  function pill(text, cls) {
    const extra = cls ? ` ${cls}` : "";
    return `<span class="pill${extra}">${escapeHtml(text)}</span>`;
  }

  function renderEmpty(el, text) {
    el.innerHTML = `<span class="empty">${escapeHtml(text)}</span>`;
  }

  // --- Health ---------------------------------------------------------------

  async function refreshHealth() {
    try {
      const body = await api("/api/health");
      SEL.dataRootBadge.textContent = "data root: " + body.data_root;
      SEL.dataRootBadge.title = body.data_root;
      SEL.healthBadge.textContent = "health: " + body.status;
      SEL.healthBadge.classList.add("badge-ok");
      msg(`Connected. database=${body.database_url}`);
    } catch (err) {
      SEL.healthBadge.textContent = "health: error";
      SEL.healthBadge.classList.add("badge-err");
      msg("Health check failed: " + err.message);
    }
  }

  // --- Import ---------------------------------------------------------------

  function renderLastResult(result) {
    if (!result) {
      renderEmpty(SEL.lastResult, "No import run yet in this session.");
      return;
    }
    const rows = [
      ["status", result.status],
      ["job_id", result.job_id],
      ["paper_id", result.paper_id],
      ["file_id", result.file_id],
      ["import_status", result.import_status],
      ["metadata_status", result.metadata_status],
      ["duplicate_status", result.duplicate_status],
      ["current_stage", result.current_stage],
      ["is_exact_duplicate", result.is_exact_duplicate],
      ["relations_created", result.relations_created],
      ["relations_existing", result.relations_existing],
      ["second_layer_ready", result.second_layer_ready],
      ["second_layer_blockers", (result.second_layer_blockers || []).join(", ")],
      ["error_code", result.error_code],
      ["error_message", result.error_message],
      ["warnings", (result.warnings || []).join("; ")],
    ];
    const html = rows
      .map(
        ([k, v]) =>
          `<div class="row"><span class="label">${escapeHtml(k)}</span>` +
          `<span class="value">${escapeHtml(v === null || v === undefined || v === "" ? "—" : v)}</span></div>`
      )
      .join("");
    SEL.lastResult.innerHTML =
      `<div class="result-block">` +
      `<div class="row"><span class="label">status</span><span class="value">` +
      pill(result.status, "pill-" + result.status) +
      `</span></div>` +
      html +
      `</div>`;
  }

  async function runImport(event) {
    event.preventDefault();
    const file = SEL.importFile.files[0];
    if (!file) {
      msg("Choose a PDF file first.");
      return;
    }
    SEL.importRun.disabled = true;
    msg("Uploading and running pipeline…");
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api("/api/import", { method: "POST", body: form });
      lastImportResult = result;
      renderLastResult(result);
      msg(`Import finished: status=${result.status} job=${result.job_id}`);
      await refreshPapers();
      await refreshMaintenance();
      if (result.paper_id) {
        await selectPaper(result.paper_id);
      }
    } catch (err) {
      msg("Import failed: " + err.message);
      SEL.lastResult.innerHTML =
        `<span class="pill pill-failed">import error</span> ` + escapeHtml(err.message);
    } finally {
      SEL.importRun.disabled = false;
    }
  }

  // --- Papers ---------------------------------------------------------------

  async function refreshPapers() {
    const params = new URLSearchParams();
    if (SEL.papersStatus.value) params.set("status", SEL.papersStatus.value);
    if (SEL.papersIncludeDeleted.checked) params.set("include_deleted", "true");
    try {
      const rows = await api("/api/papers?" + params.toString());
      renderPapers(rows);
      msg(`Papers: ${rows.length} shown.`);
    } catch (err) {
      renderEmpty(SEL.papersList, "Failed to list papers: " + err.message);
    }
  }

  function renderPapers(rows) {
    if (!rows.length) {
      renderEmpty(SEL.papersList, "No papers loaded.");
      return;
    }
    const html =
      `<table><thead><tr>` +
      `<th>title</th><th>year</th><th>status</th><th>primary file</th>` +
      `</tr></thead><tbody>` +
      rows
        .map((p) => {
          const title = p.title || p.paper_id;
          const cls = selectedPaperId === p.paper_id ? "selected" : "";
          return (
            `<tr class="clickable ${cls}" data-paper-id="${escapeHtml(p.paper_id)}">` +
            `<td>${escapeHtml(title)}</td>` +
            `<td class="nowrap">${escapeHtml(p.publication_year ?? "—")}</td>` +
            `<td class="nowrap">${pill(p.status, "pill-" + p.status)}</td>` +
            `<td class="mono">${escapeHtml(p.primary_file_id || "—")}</td>` +
            `</tr>`
          );
        })
        .join("") +
      `</tbody></table>`;
    SEL.papersList.innerHTML = html;
    SEL.papersList.querySelectorAll("tr[data-paper-id]").forEach((tr) => {
      tr.addEventListener("click", () => selectPaper(tr.getAttribute("data-paper-id")));
    });
  }

  // --- Paper detail + gate + metadata + citations --------------------------

  async function selectPaper(paperId) {
    selectedPaperId = paperId;
    // Re-render the list so the selected row is highlighted.
    const params = new URLSearchParams();
    if (SEL.papersStatus.value) params.set("status", SEL.papersStatus.value);
    if (SEL.papersIncludeDeleted.checked) params.set("include_deleted", "true");
    let rows = [];
    try {
      rows = await api("/api/papers?" + params.toString());
    } catch (_e) {
      /* ignore */
    }
    renderPapers(rows);

    msg(`Loading paper ${paperId}…`);
    const requests = await Promise.allSettled([
      api(`/api/papers/${paperId}`),
      api(`/api/papers/${paperId}/enrichment`),
      api(`/api/papers/${paperId}/second-layer-input`),
      api(`/api/papers/${paperId}/metadata-candidates`),
      api(`/api/papers/${paperId}/citations`),
      api(`/api/papers/${paperId}/trace`),
    ]);
    const [detail, enrich, gate, meta, cites, trace] = requests;

    if (detail.status === "fulfilled") {
      renderDetail(detail.value);
    } else {
      renderEmpty(SEL.paperDetail, "Failed to load paper detail: " + detail.reason.message);
    }

    // Enrichment loads only after a paper is selected; the paper list never
    // triggers provider reads. The refresh command is the only writer.
    SEL.enrichmentRefresh.disabled = false;
    if (enrich.status === "fulfilled") {
      renderEnrichment(enrich.value);
    } else {
      renderEmpty(SEL.enrichment, "Failed to load enrichment: " + enrich.reason.message);
    }

    if (gate.status === "fulfilled") {
      renderGate(gate.value);
    } else {
      renderEmpty(SEL.gate, "Failed to load second-layer gate: " + gate.reason.message);
    }

    if (meta.status === "fulfilled") {
      renderMetadata(meta.value);
    } else {
      renderEmpty(SEL.metadataCandidates, "Failed to load metadata candidates: " + meta.reason.message);
    }

    if (cites.status === "fulfilled") {
      renderCitations(cites.value);
    } else {
      renderEmpty(SEL.citations, "Failed to load citations: " + cites.reason.message);
    }

    if (trace.status === "fulfilled") {
      renderTrace(trace.value);
    } else {
      renderEmpty(SEL.trace, "Failed to load trace: " + trace.reason.message);
    }

    const failed = requests.filter((r) => r.status === "rejected").length;
    msg(failed ? `Selected paper: ${paperId}; ${failed} panel(s) failed.` : `Selected paper: ${paperId}`);
  }

  function renderDetail(detail) {
    const kv = [
      ["paper_id", detail.paper_id],
      ["title", detail.title],
      ["normalized_title", detail.normalized_title],
      ["publication_year", detail.publication_year],
      ["venue", detail.venue],
      ["doi", detail.doi],
      ["normalized_doi", detail.normalized_doi],
      ["arxiv_id", detail.arxiv_id],
      ["status", detail.status],
      ["created_at", detail.created_at],
      ["updated_at", detail.updated_at],
      ["deleted_at", detail.deleted_at],
      ["abstract", detail.abstract],
    ];
    let html = `<div class="kv">` +
      kv
        .map(
          ([k, v]) =>
            `<span class="k">${escapeHtml(k)}</span>` +
            `<span class="v">${escapeHtml(v === null || v === undefined || v === "" ? "—" : v)}</span>`
        )
        .join("") +
      `</div>`;

    html += `<h2 style="margin-top:12px">Authors</h2>`;
    if (detail.authors && detail.authors.length) {
      html += `<table><thead><tr><th>order</th><th>name</th><th>affiliation</th><th>orcid</th></tr></thead><tbody>`;
      for (const a of detail.authors) {
        html += `<tr><td class="nowrap">${escapeHtml(a.author_order ?? "—")}</td>` +
          `<td>${escapeHtml(a.full_name || "—")}</td>` +
          `<td>${escapeHtml(a.affiliation || "—")}</td>` +
          `<td class="mono">${escapeHtml(a.orcid || "—")}</td></tr>`;
      }
      html += `</tbody></table>`;
    } else {
      html += `<span class="empty">No authors.</span>`;
    }

    html += `<h2 style="margin-top:12px">Files</h2>`;
    if (detail.files && detail.files.length) {
      html += `<table><thead><tr><th>file_id</th><th>filename</th><th>primary</th><th>relative_path</th><th>sha256</th></tr></thead><tbody>`;
      for (const f of detail.files) {
        html += `<tr><td class="mono">${escapeHtml(f.file_id)}</td>` +
          `<td>${escapeHtml(f.original_filename || "—")}</td>` +
          `<td class="nowrap">${f.is_primary ? "yes" : ""}</td>` +
          `<td class="mono">${escapeHtml(f.relative_path || "—")}</td>` +
          `<td class="mono">${escapeHtml(f.sha256 || "—")}</td></tr>`;
      }
      html += `</tbody></table>`;
    } else {
      html += `<span class="empty">No files.</span>`;
    }

    html += `<h2 style="margin-top:12px">Duplicate Relations</h2>`;
    if (detail.duplicate_relations && detail.duplicate_relations.length) {
      html += `<table><thead><tr><th>relation_id</th><th>source</th><th>target</th><th>type</th><th>confidence</th><th>status</th></tr></thead><tbody>`;
      for (const r of detail.duplicate_relations) {
        html += `<tr><td class="mono">${escapeHtml(r.relation_id)}</td>` +
          `<td class="mono">${escapeHtml(r.source_paper_id)}</td>` +
          `<td class="mono">${escapeHtml(r.target_paper_id)}</td>` +
          `<td class="nowrap">${escapeHtml(r.relation_type)}</td>` +
          `<td class="nowrap">${escapeHtml(r.confidence ?? "—")}</td>` +
          `<td class="nowrap">${pill(r.status, "pill-" + r.status)}</td></tr>`;
      }
      html += `</tbody></table>`;
    } else {
      html += `<span class="empty">No duplicate relations.</span>`;
    }

    SEL.paperDetail.innerHTML = html;
  }

  // --- Provider enrichment (persisted-read + controlled refresh) -----------

  function enrichmentRetryState(p) {
    if (p.status === "retry_scheduled" && p.next_retry_at) return "retry scheduled";
    if (p.error_code === "rate_limited") return "rate limited";
    if (p.error_code === "network_disabled") return "network disabled";
    if (p.error_code === "missing_api_key") return "missing api key";
    if (p.error_code === "not_found") return "not found";
    if (p.error_code === "doi_mismatch") return "doi mismatch";
    return "—";
  }

  function renderEnrichment(data) {
    const kv = [
      ["paper_id", data.paper_id],
      ["doi", data.doi],
      ["error_code", data.error_code],
      ["error_message", data.error_message],
    ];
    let html = `<div class="kv">` +
      `<span class="k">status</span><span class="v">` +
      pill(data.metadata_enrichment_status, "pill-" + data.metadata_enrichment_status) +
      `</span>` +
      kv
        .map(
          ([k, v]) =>
            `<span class="k">${escapeHtml(k)}</span>` +
            `<span class="v">${escapeHtml(v === null || v === undefined || v === "" ? "—" : v)}</span>`
        )
        .join("") +
      `</div>`;

    const providers = data.providers || [];
    if (!providers.length) {
      html += `<div class="empty">No provider results recorded.</div>`;
    } else {
      html += `<h2 style="margin-top:12px">Providers</h2>`;
      for (const p of providers) {
        const facts = [
          ["provider", p.provider],
          ["status", pill(p.status, "pill-" + p.status)],
          ["http_status", p.http_status],
          ["attempts", p.attempt_count],
          ["retry_state", enrichmentRetryState(p)],
          ["fetched_at", p.fetched_at],
          ["next_retry_at", p.next_retry_at],
          ["error_code", p.error_code],
          ["error_message", p.error_message],
          ["fields", (p.fields || []).join(", ")],
        ];
        html += `<div class="provider-fact"><div class="kv">` +
          facts
            .map(
              ([k, v]) =>
                `<span class="k">${escapeHtml(k)}</span>` +
                `<span class="v">${escapeHtml(v === null || v === undefined || v === "" ? "—" : v)}</span>`
            )
            .join("") +
          `</div></div>`;
      }
    }
    SEL.enrichment.innerHTML = html;
  }

  async function refreshEnrichment() {
    if (!selectedPaperId) {
      msg("Select a paper first.");
      return;
    }
    SEL.enrichmentRefresh.disabled = true;
    msg("Refreshing enrichment…");
    try {
      const data = await api(`/api/papers/${selectedPaperId}/enrichment/refresh`, {
        method: "POST",
      });
      renderEnrichment(data);
      msg("Enrichment refresh finished.");
    } catch (err) {
      msg("Enrichment refresh failed: " + err.message);
      renderEmpty(SEL.enrichment, "Refresh failed: " + err.message);
    } finally {
      SEL.enrichmentRefresh.disabled = false;
    }
  }

  function renderGate(gate) {
    const rows = [
      ["status", gate.status],
      ["paper_id", gate.paper_id],
      ["primary_file_id", gate.primary_file_id],
      ["source_pdf_path", gate.source_pdf_path],
      ["relative_path", gate.relative_path],
      ["title", gate.title],
      ["authors", (gate.authors || []).join(", ")],
      ["year", gate.year],
      ["doi", gate.doi],
      ["arxiv_id", gate.arxiv_id],
      ["page_count", gate.page_count],
      ["identity_status", gate.identity_status],
      ["duplicate_status", gate.duplicate_status],
      ["blockers", (gate.blockers || []).join("; ")],
      ["error_code", gate.error_code],
      ["error_message", gate.error_message],
    ];
    SEL.gate.innerHTML =
      `<div class="kv">` +
      rows
        .map(([k, v]) => {
          let value;
          if (k === "status") {
            value = pill(v, "pill-" + v);
          } else if (k === "blockers" && v) {
            value = `<ul class="blocker-list">${(gate.blockers || [])
              .map((b) => `<li>${escapeHtml(b)}</li>`)
              .join("")}</ul>`;
          } else {
            value = escapeHtml(v === null || v === undefined || v === "" ? "—" : v);
          }
          return `<span class="k">${escapeHtml(k)}</span><span class="v">${value}</span>`;
        })
        .join("") +
      `</div>`;
  }

  function renderMetadata(rows) {
    if (!rows.length) {
      renderEmpty(SEL.metadataCandidates, "No metadata candidates.");
      return;
    }
    SEL.metadataCandidates.innerHTML =
      `<table><thead><tr><th>id</th><th>field</th><th>value</th><th>source</th><th>location</th><th>confidence</th><th>selected</th></tr></thead><tbody>` +
      rows
        .map(
          (r) =>
            `<tr><td class="mono">${escapeHtml(r.id)}</td>` +
            `<td class="nowrap">${escapeHtml(r.field_name)}</td>` +
            `<td>${escapeHtml(r.value_text || "—")}</td>` +
            `<td class="nowrap">${escapeHtml(r.source_type)}</td>` +
            `<td class="nowrap">${escapeHtml(r.source_location || "—")}</td>` +
            `<td class="nowrap">${escapeHtml(r.confidence ?? "—")}</td>` +
            `<td class="nowrap">${r.is_selected ? "yes" : ""}</td></tr>`
        )
        .join("") +
      `</tbody></table>`;
  }

  function renderCitations(rows) {
    if (!rows.length) {
      renderEmpty(SEL.citations, "No citation records.");
      return;
    }
    SEL.citations.innerHTML =
      `<table><thead><tr><th>id</th><th>format</th><th>raw_text</th><th>parse_status</th><th>selected</th></tr></thead><tbody>` +
      rows
        .map(
          (r) =>
            `<tr><td class="mono">${escapeHtml(r.id)}</td>` +
            `<td class="nowrap">${escapeHtml(r.source_format)}</td>` +
            `<td style="max-width:30ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(r.raw_text || "—")}</td>` +
            `<td class="nowrap">${pill(r.parse_status, "pill-" + r.parse_status)}</td>` +
            `<td class="nowrap">${r.is_selected ? "yes" : ""}</td></tr>`
        )
        .join("") +
      `</tbody></table>`;
  }

  // --- Data Flow Trace (read-only) ------------------------------------------

  function renderTrace(trace) {
    if (!trace || !trace.steps || !trace.steps.length) {
      renderEmpty(SEL.trace, "No trace data for this paper.");
      return;
    }

    const identity = [
      ["paper_id", trace.paper_id],
      ["paper_status", trace.paper_status],
      ["primary_file_id", trace.primary_file_id],
      ["original_filename", trace.original_filename],
      ["sha256", trace.sha256],
      ["stored_path", trace.stored_relative_path],
      ["file_exists", trace.file_exists ? "yes" : "no"],
    ];
    let html = `<div class="trace-identity kv">` +
      identity
        .map(
          ([k, v]) =>
            `<span class="k">${escapeHtml(k)}</span>` +
            `<span class="v">${escapeHtml(v === null || v === undefined || v === "" ? "—" : v)}</span>`
        )
        .join("") +
      `</div>`;

    html += `<h2 style="margin-top:12px">Data Flow Steps</h2>`;
    html += `<ol class="trace-steps">`;
    for (const s of trace.steps) {
      html += `<li class="trace-step">` +
        `<div class="trace-step-head">` +
        `<span class="trace-step-name">${escapeHtml(s.step)}</span>` +
        pill(s.status, "pill-" + s.status) +
        `</div>`;
      if (s.details) {
        html += `<div class="trace-step-details">${escapeHtml(s.details)}</div>`;
      }
      if (s.records && s.records.length) {
        html += `<ul class="trace-step-list">${s.records
          .map((r) => `<li>${escapeHtml(r)}</li>`)
          .join("")}</ul>`;
      }
      if (s.paths && s.paths.length) {
        html += `<ul class="trace-step-list">${s.paths
          .map((p) => `<li>${escapeHtml(p)}</li>`)
          .join("")}</ul>`;
      }
      if (s.blockers && s.blockers.length) {
        html += `<ul class="trace-step-blockers">${s.blockers
          .map((b) => `<li>${escapeHtml(b)}</li>`)
          .join("")}</ul>`;
      }
      html += `</li>`;
    }
    html += `</ol>`;

    if (trace.ingestion_jobs && trace.ingestion_jobs.length) {
      html += `<h2 style="margin-top:12px">Ingestion Jobs</h2>`;
      html += `<table><thead><tr><th>job_id</th><th>status</th><th>stage</th><th>exact dup</th></tr></thead><tbody>`;
      for (const j of trace.ingestion_jobs) {
        html += `<tr><td class="mono">${escapeHtml(j.job_id)}</td>` +
          `<td class="nowrap">${pill(j.status, "pill-" + j.status)}</td>` +
          `<td class="nowrap">${escapeHtml(j.current_stage || "—")}</td>` +
          `<td class="nowrap">${j.is_exact_duplicate ? "yes" : ""}</td></tr>`;
      }
      html += `</tbody></table>`;
    }

    const summary = trace.metadata_summary;
    if (summary && summary.fields && Object.keys(summary.fields).length) {
      html += `<h2 style="margin-top:12px">Metadata Summary</h2>`;
      html += `<div class="trace-meta-summary-line muted" style="font-size:12px">` +
        `total candidates: ${escapeHtml(summary.total_candidates)} &middot; ` +
        `selected: ${escapeHtml(summary.selected_count)}</div>`;
      html += `<table><thead><tr>` +
        `<th>field</th><th>candidates</th><th>selected</th>` +
        `<th>synced</th><th>top confidence</th></tr></thead><tbody>`;
      for (const f of Object.values(summary.fields)) {
        const synced = f.synced_to_paper === null ? "n/a" : (f.synced_to_paper ? "yes" : "no");
        html += `<tr><td class="nowrap">${escapeHtml(f.field_name)}</td>` +
          `<td class="nowrap">${escapeHtml(f.candidate_count)}</td>` +
          `<td class="nowrap">${f.selected ? "yes" : ""}</td>` +
          `<td class="nowrap">${escapeHtml(synced)}</td>` +
          `<td class="nowrap">${escapeHtml(f.top_confidence ?? "—")}</td></tr>`;
      }
      html += `</tbody></table>`;
    }

    if (trace.metadata_candidates && trace.metadata_candidates.length) {
      html += `<h2 style="margin-top:12px">Candidate Sources</h2>`;
      html += `<table><thead><tr>` +
        `<th>field</th><th>value</th><th>source</th><th>location</th>` +
        `<th>confidence</th><th>selected</th></tr></thead><tbody>`;
      for (const c of trace.metadata_candidates) {
        html += `<tr><td class="nowrap">${escapeHtml(c.field_name)}</td>` +
          `<td>${escapeHtml(c.value_text || "—")}</td>` +
          `<td class="nowrap">${escapeHtml(c.source_type)}</td>` +
          `<td class="nowrap">${escapeHtml(c.source_location || "—")}</td>` +
          `<td class="nowrap">${escapeHtml(c.confidence ?? "—")}</td>` +
          `<td class="nowrap">${c.is_selected ? "yes" : ""}</td></tr>`;
      }
      html += `</tbody></table>`;
    }

    const gate = trace.second_layer_gate;
    if (gate) {
      html += `<h2 style="margin-top:12px">Second-Layer Gate</h2>`;
      html += `<div class="kv">` +
        `<span class="k">status</span><span class="v">` +
        pill(gate.status, "pill-" + gate.status) +
        `</span></div>`;
      if (gate.blockers && gate.blockers.length) {
        html += `<ul class="trace-step-blockers">` +
          gate.blockers.map((b) => `<li>${escapeHtml(b)}</li>`).join("") +
          `</ul>`;
      }
    }

    SEL.trace.innerHTML = html;
  }

  // --- Maintenance ----------------------------------------------------------

  async function refreshMaintenance() {
    try {
      const rows = await api("/api/maintenance/items");
      renderMaintenance(rows);
      msg(`Maintenance: ${rows.length} items.`);
    } catch (err) {
      renderEmpty(SEL.maintenanceList, "Failed to list maintenance: " + err.message);
    }
  }

  function renderMaintenance(rows) {
    if (!rows.length) {
      renderEmpty(SEL.maintenanceList, "No maintenance items.");
      return;
    }
    SEL.maintenanceList.innerHTML =
      `<table><thead><tr><th>item_id</th><th>type</th><th>severity</th><th>risk</th><th>title</th><th>actions</th></tr></thead><tbody>` +
      rows
        .map((it) => {
          const cls = selectedItemId === it.item_id ? "selected" : "";
          const actions = (it.recommended_actions || [])
            .map((a) => `<span class="pill pill-info">${escapeHtml(a)}</span>`)
            .join(" ");
          maintenanceItemCache[it.item_id] = it;
          return (
            `<tr class="clickable ${cls}" data-item-id="${escapeHtml(it.item_id)}">` +
            `<td class="mono">${escapeHtml(it.item_id)}</td>` +
            `<td class="nowrap">${escapeHtml(it.item_type)}</td>` +
            `<td class="nowrap">${pill(it.severity, "pill-" + it.severity)}</td>` +
            `<td class="nowrap">${escapeHtml(it.risk_level)}</td>` +
            `<td>${escapeHtml(it.title)}</td>` +
            `<td class="nowrap">${actions}</td>` +
            `</tr>`
          );
        })
        .join("") +
      `</tbody></table>`;
    SEL.maintenanceList.querySelectorAll("tr[data-item-id]").forEach((tr) => {
      tr.addEventListener("click", () => selectMaintenance(tr.getAttribute("data-item-id")));
    });
  }

  async function selectMaintenance(itemId) {
    selectedItemId = itemId;
    // Re-render to highlight the selection.
    try {
      const rows = await api("/api/maintenance/items");
      renderMaintenance(rows);
    } catch (_e) {
      /* ignore */
    }
    msg(`Selected maintenance item ${itemId}. Previewing…`);
    await previewMaintenance(itemId, null);
  }

  async function previewMaintenance(itemId, action) {
    if (!itemId) {
      return;
    }
    // Resolve the item (from cache or API) to learn its recommended actions.
    let item = maintenanceItemCache[itemId];
    if (!item) {
      try {
        item = await api(`/api/maintenance/items/${itemId}`);
      } catch (_e) {
        item = null;
      }
    }
    let bodyAction = action;
    if (!bodyAction) {
      bodyAction = (item && item.recommended_actions && item.recommended_actions[0]) || null;
    }
    if (!bodyAction) {
      renderEmpty(SEL.maintenancePreview, "No recommended action available to preview.");
      return;
    }
    try {
      const prev = await api(`/api/maintenance/items/${itemId}/preview`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: bodyAction }),
      });
      // Attach recommended actions so the preview can offer them as buttons.
      prev._recommended_actions = (item && item.recommended_actions) || [];
      renderPreview(prev);
    } catch (err) {
      renderEmpty(SEL.maintenancePreview, "Preview failed: " + err.message);
    }
  }

  function renderPreview(prev) {
    const rows = [
      ["item_id", prev.item_id],
      ["action", prev.action],
      ["allowed", prev.allowed],
      ["risk_level", prev.risk_level],
      ["requires_confirmation", prev.requires_confirmation],
      ["requires_user_input", prev.requires_user_input],
      ["message", prev.message],
    ];
    let html = `<div class="kv">` +
      rows
        .map(([k, v]) => {
          let value;
          if (k === "allowed") {
            value = v ? pill("true", "pill-ready") : pill("false", "pill-failed");
          } else {
            value = escapeHtml(v === null || v === undefined || v === "" ? "—" : v);
          }
          return `<span class="k">${escapeHtml(k)}</span><span class="v">${value}</span>`;
        })
        .join("") +
      `</div>`;

    const renderList = (title, arr) => {
      if (!arr || !arr.length) return "";
      return `<h2 style="margin-top:12px">${escapeHtml(title)}</h2>` +
        `<ul class="path-list">${arr.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`;
    };
    html += renderList("affected_paths", prev.affected_paths);
    html += renderList("affected_db_records", prev.affected_db_records);
    html += renderList("will_delete_paths", prev.will_delete_paths);
    html += renderList("will_update_records", prev.will_update_records);
    html += renderList("will_create_records", prev.will_create_records);
    html += renderList("blockers", prev.blockers);

    // Action buttons so the user can switch between recommended actions.
    const actions = prev._recommended_actions || [];
    if (actions.length) {
      html += `<h2 style="margin-top:12px">Preview another action</h2>` +
        `<div class="preview-action-row">` +
        actions
          .map(
            (a) =>
              `<button type="button" class="secondary" data-action="${escapeHtml(a)}">${escapeHtml(a)}</button>`
          )
          .join("") +
        `</div>`;
    }
    SEL.maintenancePreview.innerHTML = html;
    SEL.maintenancePreview.querySelectorAll("button[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (selectedItemId) previewMaintenance(selectedItemId, btn.getAttribute("data-action"));
      });
    });
  }

  // --- Wiring ---------------------------------------------------------------

  function init() {
    SEL.importForm.addEventListener("submit", runImport);
    SEL.papersRefresh.addEventListener("click", refreshPapers);
    SEL.papersStatus.addEventListener("change", refreshPapers);
    SEL.papersIncludeDeleted.addEventListener("change", refreshPapers);
    SEL.enrichmentRefresh.addEventListener("click", refreshEnrichment);
    SEL.maintenanceRefresh.addEventListener("click", refreshMaintenance);

    refreshHealth();
    refreshPapers();
    refreshMaintenance();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
