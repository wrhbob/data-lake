const PDFJS_VERSION = "5.7.284";
const PDFJS_ROOT = `${window.__uiAssetBase || "/ui-assets/"}vendor/pdfjs/`;

let pdfjsPromise;

function loadPdfJs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(`${PDFJS_ROOT}pdf.min.mjs?v=${PDFJS_VERSION}`).then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = `${PDFJS_ROOT}pdf.worker.min.mjs?v=${PDFJS_VERSION}`;
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

function preferredRangeChunkSize(fileSize) {
  const bytes = Number(fileSize) || 0;
  if (bytes >= 32 * 1024 * 1024) return 1024 * 1024;
  return 256 * 1024;
}

function viewerMarkup(fileName, rangeChunkSize) {
  return `
    <article class="pdfjs-viewer" data-pdf-viewer="pdfjs" data-range-loading="enabled" data-range-chunk-size="${rangeChunkSize}" aria-label="PDF.js 文档预览器">
      <header class="pdfjs-toolbar">
        <div class="pdfjs-toolbar-group">
          <button type="button" class="pdfjs-nav-toggle" data-pdf-action="toggle-sidebar" title="收起页面导航" aria-label="收起页面导航" aria-pressed="true">☰ <span>导航</span></button>
          <button type="button" class="pdfjs-tool-button" data-pdf-action="previous" title="上一页" aria-label="上一页">‹</button>
          <label class="pdfjs-page-control">
            <span class="sr-only">页码</span>
            <input type="number" min="1" value="1" inputmode="numeric" data-pdf-page aria-label="当前页码" />
            <span>/ <strong data-pdf-pages>—</strong></span>
          </label>
          <button type="button" class="pdfjs-tool-button" data-pdf-action="next" title="下一页" aria-label="下一页">›</button>
        </div>
        <div class="pdfjs-toolbar-group">
          <button type="button" class="pdfjs-tool-button" data-pdf-action="zoom-out" title="缩小" aria-label="缩小">−</button>
          <button type="button" class="pdfjs-zoom-label" data-pdf-action="fit-width" title="适合宽度">适合宽度</button>
          <button type="button" class="pdfjs-tool-button" data-pdf-action="zoom-in" title="放大" aria-label="放大">＋</button>
        </div>
        <div class="pdfjs-range-state" title="PDF.js 使用 HTTP Range 按需读取 NAS 原件">
          <span class="pdfjs-range-dot" aria-hidden="true"></span>
          <span>PDF.js · Range 按需加载</span>
        </div>
      </header>
      <div class="pdfjs-workspace">
        <aside class="pdfjs-sidebar" data-pdf-sidebar aria-label="页面导航">
          <header class="pdfjs-sidebar-header">
            <strong>页面导航</strong>
            <span data-pdf-sidebar-count>正在读取</span>
          </header>
          <p class="pdfjs-sidebar-hint">点击缩略图可跳转页面</p>
          <div class="pdfjs-thumbnails" data-pdf-thumbnails aria-label="PDF 页面缩略图"></div>
        </aside>
        <div class="pdfjs-stage" data-pdf-stage>
          <div class="pdfjs-loading" data-pdf-loading role="status">
            <span class="pdfjs-spinner" aria-hidden="true"></span>
            <strong>正在打开 PDF</strong>
            <span data-pdf-progress>${fileName || "正在读取文档索引"}</span>
          </div>
          <canvas class="pdfjs-canvas" data-pdf-canvas aria-label="PDF 当前页" hidden></canvas>
          <div class="pdfjs-error" data-pdf-error role="alert" hidden>
            <strong>PDF 预览失败</strong>
            <span data-pdf-error-message>请下载原件查看。</span>
          </div>
        </div>
      </div>
    </article>
  `;
}

export function mountPdfViewer(container, { url, fileName = "PDF 原件", fileSize = 0 } = {}) {
  let destroyed = false;
  let loadingTask;
  let pdfDocument;
  let renderTask;
  let resizeObserver;
  let resizeTimer;
  let pageNumber = 1;
  let pageCount = 0;
  let scaleMode = "fit-width";
  let customScale = 1;
  let currentScale = 1;
  let renderSerial = 0;
  let thumbnailObserver;
  const thumbnailRenderTasks = new Map();
  const thumbnailButtons = new Map();
  const events = new AbortController();
  const rangeChunkSize = preferredRangeChunkSize(fileSize);

  container.innerHTML = viewerMarkup(fileName, rangeChunkSize);
  const viewer = container.querySelector("[data-pdf-viewer]");
  const stage = viewer.querySelector("[data-pdf-stage]");
  const canvas = viewer.querySelector("[data-pdf-canvas]");
  const loading = viewer.querySelector("[data-pdf-loading]");
  const progress = viewer.querySelector("[data-pdf-progress]");
  const errorPanel = viewer.querySelector("[data-pdf-error]");
  const errorMessage = viewer.querySelector("[data-pdf-error-message]");
  const pageInput = viewer.querySelector("[data-pdf-page]");
  const pagesLabel = viewer.querySelector("[data-pdf-pages]");
  const previousButton = viewer.querySelector('[data-pdf-action="previous"]');
  const nextButton = viewer.querySelector('[data-pdf-action="next"]');
  const zoomLabel = viewer.querySelector('[data-pdf-action="fit-width"]');
  const navigationButton = viewer.querySelector('[data-pdf-action="toggle-sidebar"]');
  const sidebar = viewer.querySelector("[data-pdf-sidebar]");
  const sidebarCount = viewer.querySelector("[data-pdf-sidebar-count]");
  const thumbnails = viewer.querySelector("[data-pdf-thumbnails]");

  function setLoading(message) {
    if (destroyed) return;
    loading.hidden = false;
    canvas.hidden = true;
    errorPanel.hidden = true;
    progress.textContent = message;
  }

  function showError(error) {
    if (destroyed) return;
    loading.hidden = true;
    canvas.hidden = true;
    errorPanel.hidden = false;
    errorMessage.textContent = error?.message || "该 PDF 暂不能在线预览，请下载原件查看。";
  }

  function updateControls(displayScale) {
    pageInput.value = String(pageNumber);
    pageInput.max = String(pageCount || 1);
    pagesLabel.textContent = pageCount ? String(pageCount) : "—";
    previousButton.disabled = pageNumber <= 1;
    nextButton.disabled = pageNumber >= pageCount;
    zoomLabel.textContent = scaleMode === "fit-width" ? "适合宽度" : `${Math.round(displayScale * 100)}%`;
    updateActiveThumbnail();
  }

  function updateActiveThumbnail() {
    thumbnailButtons.forEach((button, number) => {
      const active = number === pageNumber;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    const activeButton = thumbnailButtons.get(pageNumber);
    if (activeButton && thumbnails) activeButton.scrollIntoView({ block: "nearest" });
  }

  function toggleSidebar() {
    const collapsed = viewer.classList.toggle("sidebar-collapsed");
    navigationButton.setAttribute("aria-pressed", String(!collapsed));
    navigationButton.setAttribute("aria-label", collapsed ? "展开页面导航" : "收起页面导航");
    navigationButton.title = collapsed ? "展开页面导航" : "收起页面导航";
    if (!collapsed && thumbnailButtons.get(pageNumber)) updateActiveThumbnail();
  }

  async function renderThumbnail(button) {
    if (destroyed || !pdfDocument || button.dataset.thumbnailState) return;
    const thumbnailPage = Number(button.dataset.pdfThumbnailPage);
    if (!Number.isInteger(thumbnailPage) || thumbnailPage < 1 || thumbnailPage > pageCount) return;
    button.dataset.thumbnailState = "rendering";

    try {
      const page = await pdfDocument.getPage(thumbnailPage);
      if (destroyed) return;
      const baseViewport = page.getViewport({ scale: 1 });
      const scale = Math.max(0.08, Math.min(0.22, 116 / baseViewport.width, 156 / baseViewport.height));
      const viewport = page.getViewport({ scale });
      const canvas = button.querySelector("canvas");
      const placeholder = button.querySelector("[data-pdf-thumbnail-placeholder]");
      if (!canvas || !placeholder || destroyed) return;
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      const task = page.render({ canvasContext: canvas.getContext("2d", { alpha: false }), viewport });
      thumbnailRenderTasks.set(thumbnailPage, task);
      await task.promise;
      if (destroyed) return;
      canvas.hidden = false;
      placeholder.hidden = true;
      button.dataset.thumbnailState = "ready";
    } catch (error) {
      if (error?.name !== "RenderingCancelledException" && !destroyed) button.dataset.thumbnailState = "unavailable";
    } finally {
      thumbnailRenderTasks.delete(thumbnailPage);
    }
  }

  function buildThumbnails() {
    if (destroyed || !thumbnails) return;
    thumbnailObserver?.disconnect();
    thumbnailButtons.clear();
    thumbnails.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (let number = 1; number <= pageCount; number += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pdfjs-thumbnail";
      button.dataset.pdfThumbnailPage = String(number);
      button.setAttribute("aria-label", `跳转到第 ${number} 页`);
      button.innerHTML = `
        <span class="pdfjs-thumbnail-paper">
          <span class="pdfjs-thumbnail-placeholder" data-pdf-thumbnail-placeholder>${number}</span>
          <canvas hidden aria-hidden="true"></canvas>
        </span>
        <span class="pdfjs-thumbnail-number">${number}</span>
      `;
      thumbnailButtons.set(number, button);
      fragment.append(button);
    }
    thumbnails.append(fragment);
    sidebarCount.textContent = `${pageCount} 页`;

    if (window.IntersectionObserver) {
      thumbnailObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) renderThumbnail(entry.target);
          });
        },
        { root: thumbnails, rootMargin: "480px 0px" }
      );
      thumbnailButtons.forEach((button) => thumbnailObserver.observe(button));
    } else {
      [...thumbnailButtons.values()].slice(0, 16).forEach(renderThumbnail);
    }
    updateActiveThumbnail();
  }

  async function renderPage(nextPageNumber) {
    if (destroyed || !pdfDocument) return;
    const serial = ++renderSerial;
    pageNumber = Math.max(1, Math.min(Number(nextPageNumber) || 1, pageCount));
    renderTask?.cancel();
    setLoading(`正在按需读取第 ${pageNumber} 页`);

    try {
      const page = await pdfDocument.getPage(pageNumber);
      if (destroyed || serial !== renderSerial) return;
      const baseViewport = page.getViewport({ scale: 1 });
      const availableWidth = Math.max(stage.clientWidth - 40, 240);
      const fitWidthScale = Math.max(0.25, Math.min(4, availableWidth / baseViewport.width));
      const displayScale = scaleMode === "fit-width" ? fitWidthScale : customScale;
      currentScale = displayScale;
      const viewport = page.getViewport({ scale: displayScale });
      const outputScale = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
      const context = canvas.getContext("2d", { alpha: false });

      canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
      canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      canvas.hidden = false;
      updateControls(displayScale);

      renderTask = page.render({
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
      });
      await renderTask.promise;
      if (destroyed || serial !== renderSerial) return;
      loading.hidden = true;
      canvas.hidden = false;
    } catch (error) {
      if (error?.name === "RenderingCancelledException" || destroyed || serial !== renderSerial) return;
      showError(error);
    }
  }

  function navigateToInputPage() {
    const requestedPage = Number(pageInput.value);
    renderPage(Number.isFinite(requestedPage) ? requestedPage : pageNumber);
  }

  viewer.addEventListener(
    "click",
    (event) => {
      const thumbnail = event.target.closest("[data-pdf-thumbnail-page]");
      if (thumbnail && pdfDocument) {
        renderPage(Number(thumbnail.dataset.pdfThumbnailPage));
        return;
      }
      const action = event.target.closest("[data-pdf-action]")?.dataset.pdfAction;
      if (!action || !pdfDocument) return;
      if (action === "toggle-sidebar") {
        toggleSidebar();
        return;
      }
      if (action === "previous") renderPage(pageNumber - 1);
      if (action === "next") renderPage(pageNumber + 1);
      if (action === "zoom-out") {
        customScale = Math.max(0.25, currentScale - 0.15);
        scaleMode = "custom";
        renderPage(pageNumber);
      }
      if (action === "zoom-in") {
        customScale = Math.min(4, currentScale + 0.15);
        scaleMode = "custom";
        renderPage(pageNumber);
      }
      if (action === "fit-width") {
        scaleMode = "fit-width";
        renderPage(pageNumber);
      }
    },
    { signal: events.signal }
  );

  pageInput.addEventListener("change", navigateToInputPage, { signal: events.signal });
  pageInput.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        navigateToInputPage();
      }
    },
    { signal: events.signal }
  );

  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(() => {
      if (destroyed || !pdfDocument || scaleMode !== "fit-width") return;
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => renderPage(pageNumber), 100);
    });
    resizeObserver.observe(stage);
  }

  (async () => {
    try {
      const pdfjs = await loadPdfJs();
      if (destroyed) return;
      loadingTask = pdfjs.getDocument({
        url,
        cMapUrl: `${PDFJS_ROOT}cmaps/`,
        cMapPacked: true,
        standardFontDataUrl: `${PDFJS_ROOT}standard_fonts/`,
        wasmUrl: `${PDFJS_ROOT}wasm/`,
        rangeChunkSize,
        // Keep the sequential response flowing while Range requests jump to
        // xref/page objects. Range-only mode is much slower for large,
        // non-linearized PDFs stored on a remote NAS.
        disableRange: false,
        disableStream: false,
        disableAutoFetch: false,
      });
      loadingTask.onProgress = ({ loaded, total }) => {
        if (destroyed) return;
        progress.textContent = total > 0
          ? `正在读取文档索引 ${Math.min(100, Math.round((loaded / total) * 100))}%`
          : "正在读取文档索引";
      };
      pdfDocument = await loadingTask.promise;
      if (destroyed) return;
      pageCount = pdfDocument.numPages;
      buildThumbnails();
      await renderPage(1);
    } catch (error) {
      showError(error);
    }
  })();

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      renderSerial += 1;
      window.clearTimeout(resizeTimer);
      resizeObserver?.disconnect();
      thumbnailObserver?.disconnect();
      events.abort();
      renderTask?.cancel();
      thumbnailRenderTasks.forEach((task) => task.cancel());
      if (pdfDocument) pdfDocument.destroy();
      else loadingTask?.destroy();
    },
  };
}

export { PDFJS_VERSION, preferredRangeChunkSize };
