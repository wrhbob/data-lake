const state = {
  sources: [],
  tasks: [],
  selectedSourceId: "",
  search: "",
  loading: false,
  report: null,
  parseManifest: {
    status: null,
    issues: null,
    loading: false,
    error: null,
  },
  coverage: {
    rows: [],
    loading: false,
    error: null,
    regionCode: "110000",
    year: "2026",
    startPeriod: "2026-01",
    endPeriod: "2026-12",
  },
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function requestJson(url, options = {}) {
  return fetch(url, options).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    return response.json();
  });
}

function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function setApiState(text, mode = "neutral") {
  const el = $("#crawlerApiState");
  el.textContent = text;
  el.dataset.mode = mode;
}

function formatDateTime(value) {
  if (!value) return "-";
  const normalized = String(value).replace("T", " ").replace(/\.\d+.*$/, "");
  return normalized.replace("+00:00", "");
}

function formatPercent(value) {
  if (value === undefined || value === null || !Number.isFinite(Number(value))) return "-";
  return `${Math.round(Number(value) * 1000) / 10}%`;
}

function missingFieldLabel(field) {
  return {
    archive_ids: "档案",
    original_names: "文件名",
    period: "期次",
    region: "地区",
  }[field] || field || "-";
}

function formatMissingFields(value) {
  const fields = String(value || "")
    .split("|")
    .map((item) => item.trim())
    .filter(Boolean);
  return fields.length ? fields.map(missingFieldLabel).join("/") : "-";
}

function coverageStatusLabel(row) {
  if (!row) return "-";
  if (row.business_coverage_status === "covered") return "已覆盖";
  if (row.source_completeness_status === "source_blocked") return "源受阻";
  if (row.business_coverage_status === "pending_verify") return "待核";
  if (row.business_coverage_status === "missing") return "缺失";
  return row.business_coverage_status || "-";
}

function coverageCellKind(row) {
  if (!row) return "empty";
  if (row.business_coverage_status === "covered") return row.primary_download_url ? "file" : "covered";
  if (row.source_completeness_status === "source_blocked") return "blocked";
  if (row.business_coverage_status === "pending_verify") return "pending";
  if (row.business_coverage_status === "missing") return "missing";
  return "empty";
}

function periodParts(value) {
  const match = String(value || "").match(/^(20\d{2})-(0[1-9]|1[0-2])$/);
  return match ? { year: Number(match[1]), month: Number(match[2]) } : null;
}

function periodRange(start, end) {
  const startParts = periodParts(start);
  const endParts = periodParts(end);
  if (!startParts || !endParts) return [];
  const rows = [];
  let year = startParts.year;
  let month = startParts.month;
  while (year < endParts.year || (year === endParts.year && month <= endParts.month)) {
    rows.push(`${year}-${String(month).padStart(2, "0")}`);
    month += 1;
    if (month === 13) {
      year += 1;
      month = 1;
    }
  }
  return rows;
}

function provinceCodeForRegion(regionCode) {
  const text = String(regionCode || "").trim();
  return text.length >= 2 ? `${text.slice(0, 2)}0000` : "";
}

function coverageMatrixUrl() {
  const params = new URLSearchParams({
    start_period: state.coverage.startPeriod,
    end_period: state.coverage.endPeriod,
    province_code: provinceCodeForRegion(state.coverage.regionCode),
  });
  return `/api/info-price/coverage-matrix?${params}`;
}

function statusLabel(status) {
  return {
    active: "active",
    pending_verify: "待验证",
    disabled: "disabled",
    manual_recovery: "人工恢复",
    pending: "pending",
    running: "running",
    done: "done",
    failed: "failed",
    dry_run: "dry-run",
  }[status] || status || "-";
}

function scheduleCell(source) {
  if (!source.schedule_enabled) return '<span class="status-badge status-quarantined">disabled</span>';
  const due = source.is_due
    ? '<span class="status-badge status-pending_tag">due</span>'
    : '<span class="status-badge status-archived">idle</span>';
  return `${due}<small>${escapeHtml(source.frequency || "-")}</small>`;
}

function taskCountCell(source) {
  const legacyPending = Number(source.legacy_pending_count || 0);
  const legacyLabel = legacyPending ? `<small>legacy pending ${legacyPending}</small>` : "";
  return `
    <span class="crawler-count pending">${source.worker_pending_count ?? source.pending_count ?? 0}</span>
    <span class="crawler-count running">${source.running_count || 0}</span>
    <span class="crawler-count done">${source.done_count || 0}</span>
    <span class="crawler-count failed">${source.failed_count || 0}</span>
    ${legacyLabel}
  `;
}

function sourceMatches(row) {
  const keyword = state.search.trim().toLowerCase();
  if (!keyword) return true;
  return [
    row.site_id,
    row.name,
    row.province,
    row.city,
    row.region_code,
    row.adapter_kind,
    row.status,
  ]
    .join(" ")
    .toLowerCase()
    .includes(keyword);
}

function filteredSources() {
  return state.sources.filter(sourceMatches);
}

function renderMetrics() {
  const rows = state.sources;
  $("#metricSources").textContent = String(rows.length);
  $("#metricDue").textContent = String(rows.filter((row) => row.is_due).length);
  $("#metricPending").textContent = String(
    rows.reduce((sum, row) => sum + Number(row.worker_pending_count ?? row.pending_count ?? 0), 0)
  );
  $("#metricRunning").textContent = String(rows.reduce((sum, row) => sum + Number(row.running_count || 0), 0));
  $("#metricFailed").textContent = String(rows.reduce((sum, row) => sum + Number(row.failed_count || 0), 0));
  $("#sourceCount").textContent = String(filteredSources().length);
}

function renderSourceRows() {
  const rows = filteredSources();
  $("#sourceRows").innerHTML = rows.length
    ? rows
        .map((source) => {
          const selected = source.source_id === state.selectedSourceId ? " selected-row" : "";
          const workerDisabled = source.can_run_worker ? "" : "disabled";
          const adapterBadge = source.adapter_kind
            ? `<span class="domain-pill mono">${escapeHtml(source.adapter_kind)}</span>`
            : '<span class="status-badge status-collect_failed">缺 adapter</span>';
          return `
            <tr class="${selected}" data-source-id="${escapeHtml(source.source_id)}">
              <td>
                <button class="crawler-source-button" type="button" data-action="select-source" data-source-id="${escapeHtml(source.source_id)}">
                  <strong>${escapeHtml(source.site_id)}</strong>
                  <small>${escapeHtml(source.name)}</small>
                </button>
              </td>
              <td><span>${escapeHtml(source.province || "-")}</span><small>${escapeHtml(source.city || source.region_code || "-")}</small></td>
              <td>${adapterBadge}</td>
              <td>${scheduleCell(source)}</td>
              <td class="mono">${taskCountCell(source)}</td>
              <td><span class="status-badge task-${escapeHtml(source.last_task_status || "none")}">${escapeHtml(statusLabel(source.last_task_status))}</span><small>${escapeHtml(source.last_error_code || source.last_task_type || "-")}</small></td>
              <td>
                <div class="row-actions crawler-actions">
                  <button class="icon-button crawler-tool-button" type="button" title="调度预检" data-action="scheduler-source-dry-run" data-site-id="${escapeHtml(source.site_id)}">
                    <i data-lucide="scan-search"></i>
                  </button>
                  <button class="icon-button crawler-tool-button" type="button" title="生成任务" data-action="scheduler-source-run" data-site-id="${escapeHtml(source.site_id)}">
                    <i data-lucide="calendar-plus"></i>
                  </button>
                  <button class="icon-button crawler-tool-button" type="button" title="Worker 预检" data-action="worker-source-dry-run" data-source-id="${escapeHtml(source.source_id)}" ${workerDisabled}>
                    <i data-lucide="file-search"></i>
                  </button>
                  <button class="icon-button crawler-tool-button" type="button" title="Worker 运行" data-action="worker-source-run" data-source-id="${escapeHtml(source.source_id)}" ${workerDisabled}>
                    <i data-lucide="play"></i>
                  </button>
                </div>
              </td>
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="7"><div class="empty-state"><strong>没有匹配的站点</strong><span>调整搜索条件。</span></div></td></tr>`;
}

function renderTaskRows() {
  $("#taskScopeLabel").textContent = state.selectedSourceId ? "选中站点" : "全部站点";
  $("#selectedSourcePill").textContent = state.selectedSourceId
    ? state.sources.find((source) => source.source_id === state.selectedSourceId)?.site_id || "选中站点"
    : "全部站点";
  $("#taskRows").innerHTML = state.tasks.length
    ? state.tasks
        .map((task) => `
          <tr>
            <td><strong>${escapeHtml(task.task_type)}</strong><small>${escapeHtml(task.task_id)}</small></td>
            <td><span>${escapeHtml(task.site_id || "-")}</span><small>${escapeHtml(task.source_name || "-")}</small></td>
            <td><span class="status-badge task-${escapeHtml(task.status)}">${escapeHtml(statusLabel(task.status))}</span><small>${escapeHtml(task.trigger_type || "-")}</small></td>
            <td class="mono">new ${task.new_file_count || 0} / dup ${task.duplicate_file_count || 0} / fail ${task.failed_count || 0}<small>found ${task.discovered_count || 0}</small></td>
            <td class="mono">${task.attempt || 0}/${task.max_attempts || 0}<small>${escapeHtml(task.adapter_kind || "-")}</small></td>
            <td><span>${escapeHtml(formatDateTime(task.scheduled_at))}</span><small>${escapeHtml(formatDateTime(task.finished_at || task.started_at))}</small></td>
            <td><span>${escapeHtml(task.error_code || "-")}</span></td>
          </tr>
        `)
        .join("")
    : `<tr><td colspan="7"><div class="empty-state"><strong>暂无任务</strong><span>先运行 scheduler 生成 pending 任务。</span></div></td></tr>`;
}

function renderReport() {
  $("#runReport").textContent = state.report ? JSON.stringify(state.report, null, 2) : "等待操作";
}

function renderParseManifestStatus() {
  const target = $("#parseManifestStatus");
  if (!target) return;
  const manifest = state.parseManifest;
  if (manifest.loading) {
    target.innerHTML = `
      <div class="storage-audit-content storage-audit-loading parse-manifest-content">
        <strong>解析 Manifest</strong>
        <span>读取中</span>
      </div>
    `;
    return;
  }
  if (manifest.error) {
    target.innerHTML = `
      <div class="storage-audit-content storage-audit-error parse-manifest-content">
        <strong>解析 Manifest</strong>
        <span>${escapeHtml(manifest.error)}</span>
      </div>
    `;
    return;
  }
  const status = manifest.status;
  if (!status) {
    target.innerHTML = `
      <div class="storage-audit-content storage-audit-empty parse-manifest-content">
        <strong>解析 Manifest</strong>
        <span>未加载</span>
      </div>
    `;
    return;
  }
  if (!status.configured) {
    target.innerHTML = `
      <div class="storage-audit-content storage-audit-empty parse-manifest-content">
        <strong>解析 Manifest</strong>
        <span>未配置 FILE_ASSET_PARSE_MANIFEST_PATH</span>
      </div>
    `;
    return;
  }
  if (!status.exists) {
    target.innerHTML = `
      <div class="storage-audit-content storage-audit-degraded parse-manifest-content">
        <strong>解析 Manifest</strong>
        <span>文件不存在</span>
        <span class="parse-manifest-path">${escapeHtml(status.path || "-")}</span>
      </div>
    `;
    return;
  }

  const notReady = Number(status.not_ready_count || 0);
  const health = notReady ? "degraded" : "healthy";
  const missingCounts = Object.entries(status.missing_field_counts || {})
    .map(([field, count]) => `${missingFieldLabel(field)} ${count}`)
    .join(" / ");
  target.innerHTML = `
    <div class="storage-audit-content storage-audit-${health} parse-manifest-content">
      <strong>解析 Manifest</strong>
      <span class="mono">${escapeHtml(status.ready_count || 0)}/${escapeHtml(status.row_count || 0)}</span>
      <span>可解析率 ${escapeHtml(formatPercent(status.ready_rate))}</span>
      <span>未就绪 ${escapeHtml(notReady)}</span>
      ${missingCounts ? `<span>${escapeHtml(missingCounts)}</span>` : ""}
      <span>${escapeHtml(formatDateTime(status.updated_at))}</span>
      <span class="parse-manifest-path">${escapeHtml(status.path || "-")}</span>
    </div>
  `;
}

function renderParseManifestRows() {
  const target = $("#parseManifestRows");
  if (!target) return;
  const manifest = state.parseManifest;
  if (manifest.loading) {
    target.innerHTML = `<tr><td colspan="5"><div class="empty-state"><strong>Manifest 读取中</strong><span>正在读取数据湖字段清单。</span></div></td></tr>`;
    return;
  }
  if (manifest.error) {
    target.innerHTML = `<tr><td colspan="5"><div class="empty-state"><strong>Manifest 不可用</strong><span>${escapeHtml(manifest.error)}</span></div></td></tr>`;
    return;
  }
  const status = manifest.status;
  if (!status?.configured) {
    target.innerHTML = `<tr><td colspan="5"><div class="empty-state"><strong>未配置 Manifest</strong><span>设置 FILE_ASSET_PARSE_MANIFEST_PATH 指向 NAS 固定目录。</span></div></td></tr>`;
    return;
  }
  if (!status.exists) {
    target.innerHTML = `<tr><td colspan="5"><div class="empty-state"><strong>Manifest 文件不存在</strong><span>${escapeHtml(status.path || "-")}</span></div></td></tr>`;
    return;
  }
  const issues = manifest.issues?.issues || [];
  if (!issues.length) {
    target.innerHTML = `<tr><td colspan="5"><div class="empty-state"><strong>暂无阻断项</strong><span>当前 Manifest 行具备解析前置字段。</span></div></td></tr>`;
    return;
  }
  target.innerHTML = issues
    .map((issue) => {
      const fileName = issue.original_names || issue.file_names || "-";
      const region = issue.resolved_regions || issue.region_codes || "-";
      const period = issue.period_starts || issue.period_raws || "-";
      return `
        <tr>
          <td><strong class="mono">${escapeHtml(issue.object_key || "-")}</strong><small>${escapeHtml(issue.sha256 || "-")}</small></td>
          <td><span>${escapeHtml(fileName)}</span><small>${escapeHtml(issue.file_names || issue.original_names || "-")}</small></td>
          <td><span>${escapeHtml(region)}</span><small>${escapeHtml(period)}</small></td>
          <td><span class="status-badge status-collect_failed">${escapeHtml(formatMissingFields(issue.missing_fields))}</span></td>
          <td><span>${escapeHtml(issue.source_urls || "-")}</span></td>
        </tr>
      `;
    })
    .join("");
}

function renderCoverageMatrixSummary() {
  const target = $("#coverageMatrixSummary");
  if (!target) return;
  const coverage = state.coverage;
  if (coverage.loading) {
    target.innerHTML = `<span>覆盖矩阵加载中</span>`;
    return;
  }
  if (coverage.error) {
    target.innerHTML = `<span class="coverage-console-error">${escapeHtml(coverage.error)}</span>`;
    return;
  }
  const rows = coverage.rows.filter((row) => String(row.coverage_region_code || "") === String(coverage.regionCode));
  const regionName = rows.find((row) => row.coverage_region_name)?.coverage_region_name || "-";
  const covered = rows.filter((row) => row.business_coverage_status === "covered").length;
  const pending = rows.filter((row) => row.business_coverage_status === "pending_verify").length;
  const missing = rows.filter((row) => row.business_coverage_status === "missing").length;
  const sourceIds = [...new Set(rows.flatMap((row) => row.source_ids || []))];
  target.innerHTML = `
    <span>地区 <strong>${escapeHtml(regionName)}</strong> <strong class="mono">${escapeHtml(coverage.regionCode)}</strong></span>
    <span>范围 <strong class="mono">${escapeHtml(coverage.startPeriod)} ~ ${escapeHtml(coverage.endPeriod)}</strong></span>
    <span>已覆盖 <strong class="mono">${escapeHtml(covered)}/${escapeHtml(rows.length)}</strong></span>
    <span>待核 <strong class="mono">${escapeHtml(pending)}</strong></span>
    <span>缺失 <strong class="mono">${escapeHtml(missing)}</strong></span>
    <span>源 <strong class="mono">${escapeHtml(sourceIds.length)}</strong></span>
  `;
}

function renderCoverageRegionOptions() {
  const target = $("#coverageRegionOptions");
  if (!target) return;
  const options = new Map();
  for (const source of state.sources) {
    const code = source.region_code || "";
    if (!code) continue;
    const label = [source.province, source.city, source.name, source.site_id].filter(Boolean).join(" · ");
    options.set(code, label);
  }
  for (const row of state.coverage.rows) {
    const code = row.coverage_region_code || "";
    if (!code || options.has(code)) continue;
    options.set(code, [row.coverage_region_name, row.province_code].filter(Boolean).join(" · "));
  }
  target.innerHTML = [...options.entries()]
    .sort((a, b) => a[0].localeCompare(b[0], "zh-CN"))
    .map(([code, label]) => `<option value="${escapeHtml(code)}" label="${escapeHtml(label || code)}"></option>`)
    .join("");
}

function renderCoverageMatrixRows() {
  const target = $("#coverageMatrixRows");
  if (!target) return;
  if (state.coverage.loading) {
    target.innerHTML = `<div class="empty-state"><strong>覆盖矩阵加载中</strong><span>正在读取地区和期次覆盖状态。</span></div>`;
    return;
  }
  if (state.coverage.error) {
    target.innerHTML = `<div class="empty-state"><strong>覆盖矩阵不可用</strong><span>${escapeHtml(state.coverage.error)}</span></div>`;
    return;
  }
  const rows = state.coverage.rows.filter((row) => String(row.coverage_region_code || "") === String(state.coverage.regionCode));
  if (!rows.length) {
    target.innerHTML = `<div class="empty-state"><strong>没有覆盖单元</strong><span>检查地区编码和时间范围。</span></div>`;
    return;
  }
  const byPeriod = new Map(rows.map((row) => [row.period, row]));
  const periods = periodRange(state.coverage.startPeriod, state.coverage.endPeriod);
  const years = [...new Set(periods.map((period) => period.slice(0, 4)))].sort((a, b) => b.localeCompare(a));
  const months = Array.from({ length: 12 }, (_, index) => index + 1);
  target.innerHTML = `
    <table class="coverage-year-month-table crawler-coverage-table">
      <thead>
        <tr>
          <th>年份</th>
          ${months.map((month) => `<th>${escapeHtml(month)}月</th>`).join("")}
        </tr>
      </thead>
      <tbody>
        ${years
          .map((year) => `
            <tr>
              <th>${escapeHtml(year)}年</th>
              ${months.map((month) => renderCrawlerCoverageCell(byPeriod.get(`${year}-${String(month).padStart(2, "0")}`), `${year}-${String(month).padStart(2, "0")}`)).join("")}
            </tr>
          `)
          .join("")}
      </tbody>
    </table>
  `;
}

function renderCrawlerCoverageCell(row, period) {
  const parts = periodParts(period);
  if (!parts || period < state.coverage.startPeriod || period > state.coverage.endPeriod) {
    return `<td><span class="coverage-period-cell cell-empty">-</span></td>`;
  }
  if (!row) {
    return `<td><button class="coverage-period-cell cell-missing coverage-action-cell" type="button" data-action="coverage-cell-backfill" data-period="${escapeHtml(period)}">补采</button></td>`;
  }
  const kind = coverageCellKind(row);
  const title = [
    row.period,
    coverageStatusLabel(row),
    ...(Array.isArray(row.evidence_titles) ? row.evidence_titles : []),
    row.coverage_note,
    row.source_audit_note,
  ].filter(Boolean).join("；");
  if (row.business_coverage_status === "covered" && row.primary_download_url) {
    return `
      <td>
        <a class="coverage-period-cell cell-file" href="${escapeHtml(row.primary_download_url)}" title="${escapeHtml(title)}">
          下载
        </a>
      </td>
    `;
  }
  if (row.business_coverage_status === "covered") {
    return `<td><span class="coverage-period-cell cell-covered" title="${escapeHtml(title)}">已覆盖</span></td>`;
  }
  return `
    <td>
      <button class="coverage-period-cell cell-${escapeHtml(kind)} coverage-action-cell" type="button" data-action="coverage-cell-backfill" data-period="${escapeHtml(row.period || period)}" title="${escapeHtml(title)}">
        ${escapeHtml(kind === "blocked" ? "受阻" : "补采")}
      </button>
    </td>
  `;
}

function renderAll() {
  renderMetrics();
  renderSourceRows();
  renderTaskRows();
  renderReport();
  renderParseManifestStatus();
  renderParseManifestRows();
  renderCoverageMatrixSummary();
  renderCoverageMatrixRows();
  renderCoverageRegionOptions();
  if (window.lucide) window.lucide.createIcons();
}

async function loadSources() {
  state.sources = await requestJson("/api/crawler/sources");
}

async function loadTasks() {
  const params = new URLSearchParams({ limit: "80" });
  if (state.selectedSourceId) params.set("source_id", state.selectedSourceId);
  state.tasks = await requestJson(`/api/crawler/tasks?${params}`);
}

async function loadParseManifest() {
  state.parseManifest.loading = true;
  state.parseManifest.error = null;
  try {
    const [status, issues] = await Promise.all([
      requestJson("/api/crawler/parse-manifest"),
      requestJson("/api/crawler/parse-manifest/issues?limit=20"),
    ]);
    state.parseManifest.status = status;
    state.parseManifest.issues = issues;
  } catch (error) {
    state.parseManifest.error = String(error.message || error);
  } finally {
    state.parseManifest.loading = false;
  }
}

async function loadCoverageMatrix() {
  state.coverage.loading = true;
  state.coverage.error = null;
  try {
    state.coverage.rows = await requestJson(coverageMatrixUrl());
  } catch (error) {
    state.coverage.rows = [];
    state.coverage.error = String(error.message || error);
  } finally {
    state.coverage.loading = false;
  }
}

async function loadAll() {
  state.loading = true;
  setApiState("加载中", "loading");
  try {
    await loadSources();
    await loadTasks();
    await loadParseManifest();
    await loadCoverageMatrix();
    setApiState("Crawler API", "ok");
  } catch (error) {
    setApiState("加载失败", "error");
    state.report = { error: String(error.message || error) };
  } finally {
    state.loading = false;
    renderAll();
  }
}

async function runScheduler({ dryRun, siteId = null, force = false }) {
  setApiState(dryRun ? "调度预检中" : "生成任务中", "loading");
  const report = await postJson("/api/crawler/scheduler/run", {
    dry_run: dryRun,
    force,
    site_id: siteId,
    trigger: dryRun ? "ui_dry_run" : "ui",
  });
  state.report = report;
  await loadAll();
}

async function runWorker({ dryRun, sourceId }) {
  if (!dryRun && !window.confirm("将对选中站点执行 Worker，可能访问原站并下载文件。确认运行？")) return;
  setApiState(dryRun ? "Worker 预检中" : "Worker 运行中", "loading");
  const report = await postJson("/api/crawler/worker/run", {
    dry_run: dryRun,
    source_id: sourceId,
    limit: 1,
    trigger: dryRun ? "ui_dry_run" : "ui",
  });
  state.report = report;
  await loadAll();
}

async function runCoverageBackfill({ startPeriod, endPeriod, dryRun = false }) {
  setApiState(dryRun ? "补采预检中" : "补采下载中", "loading");
  const backfill = await postJson("/api/crawler/coverage-backfill", {
    region_code: state.coverage.regionCode,
    start_period: startPeriod,
    end_period: endPeriod,
    dry_run: dryRun,
  });
  if (dryRun) {
    state.report = { backfill };
    setApiState("Crawler API", "ok");
    renderAll();
    return;
  }
  const workers = [];
  const createdTasks = (backfill.tasks || []).filter((task) => task.source_id && task.batch_id);
  for (const task of createdTasks) {
    workers.push(await postJson("/api/crawler/worker/run", {
      dry_run: false,
      source_id: task.source_id,
      batch_id: task.batch_id,
      limit: 1,
      trigger: "coverage_backfill_ui",
    }));
  }
  state.report = { backfill, workers };
  await loadSources();
  await loadTasks();
  await loadCoverageMatrix();
  setApiState("Crawler API", "ok");
  renderAll();
}

async function handleAction(event) {
  const target = event.target.closest("[data-action]");
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  try {
    if (action === "refresh") await loadAll();
    if (action === "reload-tasks") {
      await loadTasks();
      renderAll();
    }
    if (action === "reload-manifest") {
      await loadParseManifest();
      renderAll();
    }
    if (action === "load-coverage") {
      syncCoverageInputs();
      await loadCoverageMatrix();
      renderAll();
    }
    if (action === "coverage-range-backfill") {
      syncCoverageInputs();
      await runCoverageBackfill({
        startPeriod: state.coverage.startPeriod,
        endPeriod: state.coverage.endPeriod,
        dryRun: false,
      });
    }
    if (action === "coverage-range-dry-run") {
      syncCoverageInputs();
      await runCoverageBackfill({
        startPeriod: state.coverage.startPeriod,
        endPeriod: state.coverage.endPeriod,
        dryRun: true,
      });
    }
    if (action === "coverage-cell-backfill") {
      syncCoverageInputs();
      const period = target.dataset.period;
      await runCoverageBackfill({ startPeriod: period, endPeriod: period, dryRun: false });
    }
    if (action === "clear-report") {
      state.report = null;
      renderAll();
    }
    if (action === "clear-selection") {
      state.selectedSourceId = "";
      await loadTasks();
      renderAll();
    }
    if (action === "select-source") {
      state.selectedSourceId = target.dataset.sourceId || "";
      await loadTasks();
      renderAll();
    }
    if (action === "scheduler-dry-run") await runScheduler({ dryRun: true });
    if (action === "scheduler-run") await runScheduler({ dryRun: false });
    if (action === "scheduler-source-dry-run") await runScheduler({ dryRun: true, siteId: target.dataset.siteId });
    if (action === "scheduler-source-run") await runScheduler({ dryRun: false, siteId: target.dataset.siteId, force: true });
    if (action === "worker-source-dry-run") await runWorker({ dryRun: true, sourceId: target.dataset.sourceId });
    if (action === "worker-source-run") await runWorker({ dryRun: false, sourceId: target.dataset.sourceId });
  } catch (error) {
    state.report = { error: String(error.message || error) };
    setApiState("操作失败", "error");
    renderAll();
  }
}

document.addEventListener("click", handleAction);
$("#sourceSearch").addEventListener("input", (event) => {
  state.search = event.target.value || "";
  renderAll();
});

function populateCoverageYearSelect() {
  const select = $("#coverageYear");
  if (!select) return;
  const currentYear = new Date().getFullYear();
  const years = [];
  for (let y = currentYear; y >= currentYear - 10; y--) {
    years.push(y);
  }
  const selectedYear = state.coverage.year || String(currentYear);
  select.innerHTML = years
    .map((y) => `<option value="${y}" ${String(y) === selectedYear ? "selected" : ""}>${y}年</option>`)
    .join("");
}

function coverageYearFromPeriods() {
  const startParts = periodParts(state.coverage.startPeriod);
  const endParts = periodParts(state.coverage.endPeriod);
  if (startParts && endParts && startParts.year === endParts.year) {
    return String(startParts.year);
  }
  return state.coverage.year || String(new Date().getFullYear());
}

function syncCoverageInputs() {
  state.coverage.regionCode = ($("#coverageRegionCode")?.value || "110000").trim();
  state.coverage.startPeriod = $("#coverageStartPeriod")?.value || state.coverage.startPeriod;
  state.coverage.endPeriod = $("#coverageEndPeriod")?.value || state.coverage.endPeriod;
  state.coverage.year = coverageYearFromPeriods();
  populateCoverageYearSelect();
}

$("#coverageYear")?.addEventListener("change", () => {
  const year = $("#coverageYear").value;
  state.coverage.year = year;
  state.coverage.startPeriod = `${year}-01`;
  state.coverage.endPeriod = `${year}-12`;
  $("#coverageStartPeriod").value = state.coverage.startPeriod;
  $("#coverageEndPeriod").value = state.coverage.endPeriod;
});

["#coverageRegionCode", "#coverageStartPeriod", "#coverageEndPeriod"].forEach((selector) => {
  const input = $(selector);
  if (!input) return;
  input.addEventListener("change", () => {
    syncCoverageInputs();
  });
});

populateCoverageYearSelect();
loadAll();
