import re
from pathlib import Path

from test_api import build_client


UI_DIR = Path(__file__).resolve().parents[1] / "app" / "ui"


def test_pdf_preview_uses_local_pdfjs_range_viewer_instead_of_browser_embed():
    app_source = (UI_DIR / "app.js").read_text(encoding="utf-8")
    viewer_source = (UI_DIR / "pdf-viewer.js").read_text(encoding="utf-8")

    assert "<embed class=\"pdf-embed\"" not in app_source
    assert "renderAdaptivePdfViewer(file)" in app_source
    assert "LARGE_PDF_FAST_PREVIEW_THRESHOLD = 64 * 1024 * 1024" in app_source
    assert 'Range: "bytes=0-4095"' in app_source
    assert "/\\/Linearized\\b/" in app_source
    assert 'data-pdf-viewer="native-fast"' in app_source
    assert "fetchFirstPdfJpeg" in app_source
    assert "const chunkSize = 1024 * 1024" in app_source
    assert 'data-native-poster-image' in app_source
    assert 'data-show-full' in app_source
    assert "renderPdfJsViewer(file)" in app_source
    assert 'data-pdf-viewer="pdfjs"' in viewer_source
    assert "const rangeChunkSize = preferredRangeChunkSize(fileSize)" in viewer_source
    assert "bytes >= 32 * 1024 * 1024" in viewer_source
    assert "return 1024 * 1024" in viewer_source
    assert 'data-range-chunk-size="${rangeChunkSize}"' in viewer_source
    assert "disableRange: false" in viewer_source
    assert "disableStream: false" in viewer_source
    assert "disableAutoFetch: false" in viewer_source
    assert "pdfDocument.getPage(pageNumber)" in viewer_source
    assert 'data-pdf-sidebar' in viewer_source
    assert 'data-pdf-thumbnails' in viewer_source
    assert 'data-pdf-thumbnail-page' in viewer_source
    assert "IntersectionObserver" in viewer_source


def test_pdfjs_runtime_worker_and_support_assets_are_served_locally():
    client, _ = build_client()

    for path in (
        "/ui-assets/pdf-viewer.js",
        "/ui-assets/vendor/pdfjs/pdf.min.mjs",
        "/ui-assets/vendor/pdfjs/pdf.worker.min.mjs",
        "/ui-assets/vendor/pdfjs/cmaps/Adobe-GB1-UCS2.bcmap",
        "/ui-assets/vendor/pdfjs/standard_fonts/LiberationSans-Regular.ttf",
        "/ui-assets/vendor/pdfjs/wasm/openjpeg.wasm",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content, path


def test_ui_cache_version_marks_pdfjs_thumbnail_navigation_release():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")

    version = re.search(r'window\.__uiAssetVersion = "([^"]+)"', html)

    assert version is not None
    assert version.group(1).startswith("20260715-")
