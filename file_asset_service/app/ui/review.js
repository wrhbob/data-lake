const reviewState = {
  extracts: [],
  review: null,
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

function actionButtons(actions) {
  return actions
    .map((action) => `<button type="button" data-review-edit-action="${escapeHtml(action)}" disabled>${escapeHtml(action)}</button>`)
    .join("");
}

async function refresh() {
  reviewState.extracts = await api("/api/info-price/extracts");
  const currentValue = $("#extractSelect").value;
  renderExtractOptions();
  const selected = reviewState.extracts.some((item) => item.processing_id === currentValue)
    ? currentValue
    : reviewState.extracts[0]?.processing_id;
  if (selected) {
    $("#extractSelect").value = selected;
    await loadReview(selected);
  } else {
    renderEmpty();
  }
}

function renderExtractOptions() {
  const select = $("#extractSelect");
  select.innerHTML = reviewState.extracts
    .map(
      (item) =>
        `<option value="${escapeHtml(item.processing_id)}">${escapeHtml(item.file_name)} · ${escapeHtml(item.row_count)} 行</option>`,
    )
    .join("");
}

async function loadReview(processingId) {
  reviewState.review = await api(`/api/info-price/extracts/${encodeURIComponent(processingId)}/review`);
  renderReview();
}

function renderEmpty() {
  $("#metricTotalRows").textContent = "0";
  $("#metricManualRows").textContent = "0";
  $("#metricAutoRows").textContent = "0";
  $("#materialMatchList").innerHTML = '<div class="empty-list">暂无已解析信息价。</div>';
  $("#lowConfidenceList").innerHTML = '<div class="empty-list">暂无低置信项。</div>';
}

function renderReview() {
  const review = reviewState.review;
  $("#metricTotalRows").textContent = String(review.total_rows);
  $("#metricManualRows").textContent = String(review.manual_count);
  $("#metricAutoRows").textContent = String(review.auto_confirmed_count);
  $("#qualityGateNote").textContent = review.quality_gate.note;
  renderMaterialMatches(review.pending_material_matches);
  renderLowConfidence(review.low_confidence_items);
}

function renderMaterialMatches(items) {
  const list = $("#materialMatchList");
  if (!items.length) {
    list.innerHTML = '<div class="empty-list">暂无待匹配材料。v0.2 接入材料标准化结果后，这里只显示 raw_material_name 挂不到 material_id 的少数项。</div>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
        <div class="source-row" data-source-location="${escapeHtml(item.source_location)}">
          <div>
            <strong>${escapeHtml(item.raw_material_name)}</strong>
            <div class="row-meta">建议：${escapeHtml(item.suggested_material_name || "无")} · 置信度 ${escapeHtml(item.confidence)}% · ${escapeHtml(item.source_location)}</div>
            <div class="form-actions">${actionButtons(item.actions)}</div>
          </div>
          <span class="status-pill">待匹配</span>
        </div>
      `,
    )
    .join("");
}

function renderLowConfidence(items) {
  const list = $("#lowConfidenceList");
  if (!items.length) {
    list.innerHTML = '<div class="empty-list">暂无低置信抽取项。v0.1 使用临时规则派生，正式置信度由解析器输出。</div>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
        <div class="source-row" data-source-location="${escapeHtml(item.source_location)}" data-source-preview="${escapeHtml(
          JSON.stringify(item, null, 2),
        )}">
          <div>
            <strong>${escapeHtml(item.raw_material_name)}</strong>
            <div class="row-meta">${escapeHtml(item.field)}：${escapeHtml(item.suggested_value || "空")} · ${escapeHtml(item.reason)} · ${escapeHtml(item.source_location)}</div>
            <div class="form-actions">${actionButtons(item.actions)}</div>
          </div>
          <span class="status-pill">低置信</span>
        </div>
      `,
    )
    .join("");
}

document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-source-location]");
  if (!row) return;
  $("#sourceLocationMeta").textContent = row.dataset.sourceLocation;
  $("#sourcePreview").textContent = row.dataset.sourcePreview || row.innerText;
});

$("#extractSelect").addEventListener("change", (event) => {
  loadReview(event.target.value).catch((error) => {
    $("#sourcePreview").textContent = error.message;
  });
});

$("#refreshButton").addEventListener("click", () => {
  refresh().catch((error) => {
    $("#sourcePreview").textContent = error.message;
  });
});

refresh().catch((error) => {
  $("#sourcePreview").textContent = error.message;
});
