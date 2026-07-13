const auditState = {
  extracts: [],
  audit: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refresh() {
  auditState.extracts = await api("/api/info-price/extracts");
  const currentValue = $("#extractSelect").value;
  renderExtractOptions();
  const selected = auditState.extracts.some((item) => item.processing_id === currentValue)
    ? currentValue
    : auditState.extracts[0]?.processing_id;
  if (selected) {
    $("#extractSelect").value = selected;
    await loadAudit(selected);
  } else {
    renderEmpty();
  }
}

function renderExtractOptions() {
  const select = $("#extractSelect");
  select.innerHTML = auditState.extracts
    .map(
      (item) =>
        `<option value="${escapeHtml(item.processing_id)}">${escapeHtml(item.file_name)} · ${escapeHtml(item.row_count)} 行</option>`,
    )
    .join("");
}

async function loadAudit(processingId) {
  auditState.audit = await api(`/api/info-price/extracts/${encodeURIComponent(processingId)}/audit`);
  renderAudit();
}

function renderEmpty() {
  $("#metricPriceRows").textContent = "0";
  $("#metricNeedsReview").textContent = "0";
  $("#anomalyProfile").innerHTML = '<div class="empty-list">暂无可审核解析结果。</div>';
  $("#sampleRows").innerHTML = '<div class="empty-list">暂无抽查样本。</div>';
}

function renderAudit() {
  const audit = auditState.audit;
  $("#metricPriceRows").textContent = String(audit.metrics.price_rows);
  $("#metricNeedsReview").textContent = String(audit.metrics.needs_review);
  $("#metricPreviousRate").textContent = audit.metrics.previous_period_match_rate ?? "缺";
  $("#metricProfileStatus").textContent = audit.can_release ? "正常" : "阻断";
  $("#releaseStatus").textContent = audit.can_release ? "可放行" : "禁止放行";
  renderAnomalyProfile(audit);
  renderSampleRows(audit.sample_rows);
  $("#auditDetail").textContent = JSON.stringify(
    {
      previous_period_compare: audit.previous_period_compare,
      release_signature: audit.release_signature,
      return_to_review: audit.return_to_review,
    },
    null,
    2,
  );
}

function renderAnomalyProfile(audit) {
  const profile = audit.anomaly_profile;
  const items = [
    {
      title: "与上期对比",
      meta: profile.period_compare.message,
      status: profile.period_compare.status,
    },
    ...profile.peer_outliers.map((item) => ({
      title: item.raw_material_name,
      meta: `${item.signal} · ${item.message} · ${item.source_location}`,
      status: "peer_outlier",
    })),
    ...profile.missing_or_out_of_range.map((item) => ({
      title: item.raw_material_name,
      meta: `${item.signal} · ${item.field} · ${item.message} · ${item.source_location}`,
      status: "missing_or_out_of_range",
    })),
  ];
  $("#anomalyProfile").innerHTML = items
    .map(
      (item) => `
        <div class="exception-row">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <div class="row-meta">${escapeHtml(item.meta)}</div>
          </div>
          <span class="status-pill">${escapeHtml(item.status)}</span>
        </div>
      `,
    )
    .join("");
}

function renderSampleRows(items) {
  const list = $("#sampleRows");
  if (!items.length) {
    list.innerHTML = '<div class="empty-list">暂无抽查样本。</div>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
        <div class="source-row">
          <div>
            <strong>${escapeHtml(item.raw_material_name)}</strong>
            <div class="row-meta">${escapeHtml(item.unit)} · ${escapeHtml(item.price)} · ${escapeHtml(item.source_location)}</div>
            <div class="form-actions">
              <button type="button" disabled>一致</button>
              <button type="button" disabled>存疑</button>
            </div>
          </div>
          <span class="status-pill">抽查</span>
        </div>
      `,
    )
    .join("");
}

$("#extractSelect").addEventListener("change", (event) => {
  loadAudit(event.target.value).catch((error) => {
    $("#auditDetail").textContent = error.message;
  });
});

$("#refreshButton").addEventListener("click", () => {
  refresh().catch((error) => {
    $("#auditDetail").textContent = error.message;
  });
});

refresh().catch((error) => {
  $("#auditDetail").textContent = error.message;
});
