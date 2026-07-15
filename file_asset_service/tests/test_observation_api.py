import json
from pathlib import Path
import re
import subprocess
import textwrap

from test_api import build_client
from test_info_price_schema import minimal_xlsx


UI_DIR = Path(__file__).resolve().parents[1] / "app" / "ui"


def run_ui_probe(probe_js: str) -> None:
    script = f"""
        const fs = require("fs");
        const vm = require("vm");
        const appJs = fs.readFileSync({json.dumps(str(UI_DIR / "app.js"))}, "utf8");
        const elements = new Map();
        const metadataInputs = [];
        function baseElement(value = "") {{
          return {{
            value,
            dataset: {{}},
            textContent: "",
            innerHTML: "",
            hidden: false,
            addEventListener() {{}},
            setAttribute() {{}},
            classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }},
          }};
        }}
        const document = {{
          querySelector(selector) {{ return elements.get(selector) || baseElement(); }},
          querySelectorAll(selector) {{ return selector === "[data-metadata-field]" ? metadataInputs : []; }},
          addEventListener() {{}},
        }};
        const window = {{ addEventListener() {{}}, lucide: {{ createIcons() {{}} }} }};
        const context = {{ console, document, window, Date, Intl, URL, Map, Object, String, Number, Array }};
        vm.createContext(context);
        vm.runInContext(appJs + "\\nglobalThis.__probe = {{ state, domainConfigs, metadata, policyEffectiveStatus, collectPatch, filterOptions, cityOptions, regionLabel, filteredArchives, downloadUrl, previewUrl, renderViewerCanvas, resetViewerScroll, coverageMatrixUrl, filteredCoverageRows, renderCoverageMatrixTable, renderStorageAuditBar, renderAttachmentList, renderViewerShell, manualUploadDefaults, buildManualUploadArchivePayload, manualEditDefaults, buildManualEditPatch, submitManualUpload, loadArchives, loadCoverageMatrix, loadCurrentView, latestCostInfoPeriodLabel, costInfoOverviewModel }};", context);
        const __probePromise = (async () => {{
        {probe_js}
        }})();
        if (__probePromise && typeof __probePromise.catch === "function") {{
          __probePromise.catch((error) => {{
            console.error(error && error.stack ? error.stack : error);
            process.exitCode = 1;
          }});
        }}
    """
    result = subprocess.run(["node", "-e", textwrap.dedent(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout


def css_rule(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}", css, re.DOTALL)
    assert match, f"missing CSS rule: {selector}"
    return match.group("body")


def test_observation_api_lists_assets_processing_and_output():
    client, storage = build_client()
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "tenant_obs",
            "source_type": "info_price_governance",
            "batch_id": "batch-observe",
            "source_item_key": "乌鲁木齐2026-04",
        },
        files={
            "file": (
                "乌鲁木齐信息价.xlsx",
                minimal_xlsx(
                    [
                        ["乌鲁木齐市2026年4月份建设工程综合价格信息"],
                        ["序号", "材料名称及规格型号", "单位", "除税综合\n信息价", "含税综合\n信息价"],
                        [1, "低碳热轧盘条（高线）HPB300 Φ6", "t", 3200.99, 3609.12],
                    ]
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()
    processing_response = client.get("/api/file-processing")
    assert processing_response.status_code == 200
    parse_processing_id = next(
        item["processing_id"]
        for item in processing_response.json()
        if item["file_id"] == ingest["file_id"] and item["processor"] == "info_price_parse"
    )

    run = client.post(f"/api/file-processing/{parse_processing_id}/run")

    assert run.status_code == 200
    assets = client.get("/api/file-assets", params={"tenant_code": "tenant_obs"}).json()
    assert assets[0]["file_id"] == ingest["file_id"]
    assert assets[0]["file_name"] == "乌鲁木齐信息价.xlsx"
    assert assets[0]["source_type"] == "info_price_governance"
    assert assets[0]["batch_id"] == "batch-observe"
    assert assets[0]["source_item_key"] == "乌鲁木齐2026-04"

    processing = client.get("/api/file-processing", params={"file_id": ingest["file_id"]}).json()
    assert {item["processor"] for item in processing} == {"xls_parse", "info_price_parse"}
    parsed = next(item for item in processing if item["processor"] == "info_price_parse")
    assert parsed["status"] == "succeeded"
    assert parsed["file_name"] == "乌鲁木齐信息价.xlsx"
    assert parsed["output_bucket"] == "cost-extract"

    output = client.get(f"/api/file-processing/{parse_processing_id}/output")

    assert output.status_code == 200
    assert output.json()["schema"] == "info_price_extract.v1"
    assert output.json()["row_count"] == 1
    assert json.loads(storage.get_object(parsed["output_bucket"], parsed["output_key"]))["row_count"] == 1


def test_ui_shell_exposes_domain_archive_shell_and_trading_hooks():
    client, _ = build_client()

    response = client.get("/ui")

    assert response.status_code == 200
    html = response.text
    assert "招投标公告档案台" in html
    assert 'data-domain="cost_info"' in html
    assert 'data-domain="trading"' in html
    assert 'data-domain="policy_regulation"' in html
    assert 'data-domain="standard_atlas"' in html
    assert 'data-domain="quota"' in html
    assert 'data-domain-state="placeholder"' in html
    assert "该域待接入真实数据" in html
    assert 'data-domain-state="disabled"' not in html
    assert 'id="archiveRows"' in html
    assert 'data-view-mode="archives"' in html
    assert 'data-view-mode="coverage"' in html
    assert 'id="coverageMatrixCard"' in html
    assert 'id="coverageMatrixRows"' in html
    assert 'id="costInfoOverview"' in html
    assert 'id="attachmentList"' in html
    assert 'id="metadataFields"' in html
    assert 'id="viewerBody"' in html
    assert 'id="viewerInfoToggle"' in html
    assert 'data-action="toggle-viewer-info"' in html
    assert 'id="manualUploadModal"' in html
    assert 'id="manualUploadForm"' in html
    assert 'data-action="submit-upload"' in html
    assert 'id="manualUploadToast"' in html
    assert 'title="人工上传(爬虫够不着的源)"' in html
    assert 'id="apiState"' in html
    assert "app.js?v=" in html
    assert "项目大盘" not in html
    assert "中标金额" not in html


def test_cost_info_overview_uses_real_archive_metrics():
    run_ui_probe(
        """
        const cell = (value) => ({ value });
        context.__probe.state.domain = "cost_info";
        context.__probe.state.viewMode = "archives";
        context.__probe.state.archiveTotalCount = 3;
        context.__probe.state.archives = [
          { region_code: "510100", channel_type: "auto_crawl", metadata: { period: cell("2026-05") } },
          { region_code: "510500", channel_type: "manual_upload", metadata: { period: cell("2026-06") } },
          { region_code: "510500", channel_type: "auto_crawl", metadata: { period: cell("2025-12") } },
        ];
        const model = context.__probe.costInfoOverviewModel();
        const values = Object.fromEntries(model.metrics.map((item) => [item.label, item.value]));
        if (values["档案总量"] !== "3") throw new Error("unexpected total " + values["档案总量"]);
        if (values["覆盖地区"] !== "2") throw new Error("unexpected regions " + values["覆盖地区"]);
        if (values["最新期次"] !== "2026年6月") throw new Error("unexpected period " + values["最新期次"]);
        if (values["人工补录"] !== "1") throw new Error("unexpected manual count " + values["人工补录"]);
        """
    )


def test_ui_script_uses_archive_api_and_does_not_define_project_fields():
    client, _ = build_client()

    response = client.get("/ui-assets/app.js")

    assert response.status_code == 200
    script = response.text
    assert "domainConfigs" in script
    assert "/api/archives" in script
    assert "domain_type" in script
    assert "priced_source" in script
    assert "quota_code_raw" in script
    assert "policy_topic_raw" in script
    assert "construction_topic_raw" in script
    assert "field_sources" in script
    assert "source_level" in script
    assert "project_group_key" not in script
    assert "winning_amount" not in script
    assert "winning_bidder" not in script
    assert "屋面" not in script
    assert "防水" not in script
    assert "费率参数表" not in script


def test_ui_renders_nas_directory_mirror_status_and_export_action():
    run_ui_probe(
        """
        const attachmentSummary = baseElement();
        const attachmentList = baseElement();
        const viewerBody = baseElement();
        const viewerSidebar = baseElement();
        const viewerTools = baseElement();
        const viewerInfoToggle = baseElement();
        elements.set("#attachmentSummary", attachmentSummary);
        elements.set("#attachmentList", attachmentList);
        elements.set("#viewerBody", viewerBody);
        elements.set("#viewerSidebar", viewerSidebar);
        elements.set("#viewerTools", viewerTools);
        elements.set("#viewerInfoToggle", viewerInfoToggle);

        const item = {
          archive_id: "archive-001",
          files: [
            {
              file_id: "file-001",
              file_name: "《泉州工程造价管理》2026年第1期.pdf",
              file_ext: "pdf",
              file_role: "main_document",
              mirror_status: "missing",
              mirror_relative_path: "信息价/福建省/泉州市/2026/《泉州工程造价管理》2026年第1期.pdf",
            },
          ],
        };
        context.__probe.state.selectedFileId = "file-001";
        context.__probe.renderAttachmentList(item);
        context.__probe.renderViewerShell(item);

        if (!attachmentList.innerHTML.includes("未镜像")) throw new Error(attachmentList.innerHTML);
        if (!attachmentList.innerHTML.includes("信息价/福建省/泉州市/2026")) throw new Error(attachmentList.innerHTML);
        if (!viewerTools.innerHTML.includes("data-action=\\"mirror-archive\\"")) throw new Error(viewerTools.innerHTML);
        if (!viewerTools.innerHTML.includes("导出NAS")) throw new Error(viewerTools.innerHTML);
        """
    )


def test_ui_domain_filters_match_archive_specs():
    run_ui_probe(
        """
        const expected = {
          cost_info: ["publisher_org", "category_raw", "region_code", "period_year", "period_month", "channel_type"],
          trading: ["notice_type_raw", "region_code", "publish_year", "publish_month", "publish_day", "channel_type"],
          quota: ["region_code", "specialty_raw", "file_role", "version_year", "channel_type"],
          policy_regulation: ["issuing_authority_raw", "policy_type_raw", "region_code", "policy_effective_status", "publish_year", "channel_type"],
          standard_atlas: ["standard_type_raw", "discipline_raw", "construction_topic_raw", "version_year", "channel_type"],
        };
        for (const [domain, keys] of Object.entries(expected)) {
          const actual = context.__probe.domainConfigs[domain].filters.map((filter) => filter.key);
          for (const key of keys) {
            if (!actual.includes(key)) throw new Error(`${domain} missing filter ${key}; got ${actual.join(",")}`);
          }
        }
        const costInfoFilters = context.__probe.domainConfigs.cost_info.filters.map((filter) => filter.key);
        if (costInfoFilters.includes("publisher")) {
          throw new Error(`cost_info publisher must not be a filter; got ${costInfoFilters.join(",")}`);
        }
        if (costInfoFilters.includes("period_issue")) {
          throw new Error(`cost_info issue number should be a row label, not a filter; got ${costInfoFilters.join(",")}`);
        }
        const costInfoColumns = context.__probe.domainConfigs.cost_info.columns.map((column) => column.key);
        if (!costInfoColumns.includes("publisher")) {
          throw new Error(`publisher should still be visible in archive list columns; got ${costInfoColumns.join(",")}`);
        }
        const costInfoMetadataFields = context.__probe.domainConfigs.cost_info.metadataFields.map((field) => field.key);
        if (!costInfoMetadataFields.includes("publisher")) {
          throw new Error(`publisher should still be visible in detail metadata fields; got ${costInfoMetadataFields.join(",")}`);
        }
        const tradingFilters = context.__probe.domainConfigs.trading.filters.map((filter) => filter.key);
        if (tradingFilters.includes("exchange_name")) {
          throw new Error(`trading exchange center should not be a filter; got ${tradingFilters.join(",")}`);
        }
        if (tradingFilters.includes("priced_source")) {
          throw new Error(`trading priced_source filter should stay hidden until role filters expand; got ${tradingFilters.join(",")}`);
        }
        """
    )


def test_cost_info_filters_keep_core_rows_visible_and_low_frequency_collapsed():
    run_ui_probe(
        """
        const panel = baseElement();
        elements.set("#filterPanel", panel);
        context.__probe.state.domain = "cost_info";
        context.__probe.state.viewMode = "archives";
        context.__probe.state.filterAdvancedOpen = false;
        context.__probe.state.filters = context.defaultFilters("cost_info");
        context.__probe.state.archives = [
          { region_code: "110000", period_kind: "monthly", metadata: { period: { value: "2026-01" } } },
        ];

        context.renderFilters();

        if (!panel.innerHTML.includes("地区")) throw new Error(panel.innerHTML);
        if (!panel.innerHTML.includes("地市")) throw new Error(panel.innerHTML);
        if (!panel.innerHTML.includes("年份")) throw new Error(panel.innerHTML);
        if (!panel.innerHTML.includes("月份")) throw new Error(panel.innerHTML);
        if (panel.innerHTML.includes("资料分类")) throw new Error("资料分类 should be collapsed by default");
        if (panel.innerHTML.includes("入湖通道")) throw new Error("入湖通道 should be collapsed by default");
        if (!panel.innerHTML.includes("更多筛选")) throw new Error(panel.innerHTML);
        if (!panel.innerHTML.includes('data-action="toggle-advanced-filters"')) throw new Error(panel.innerHTML);

        context.__probe.state.filterAdvancedOpen = true;
        context.renderFilters();
        if (!panel.innerHTML.includes("资料分类") || !panel.innerHTML.includes("入湖通道")) {
          throw new Error(`advanced filters not rendered: ${panel.innerHTML}`);
        }
        """
    )


def test_trading_filters_include_local_enums_and_filter_rows():
    run_ui_probe(
        """
        const cell = (value) => ({ value, source_level: "crawler", tagged_by: "test", tagged_at: "2026-06-20T10:00:00+08:00" });
        context.__probe.state.domain = "trading";
        context.__probe.state.filters = {
          notice_type_raw: "招标公告",
          region_code: "33",
          exchange_name: "杭州市公共资源交易中心",
          publish_year: "2026",
          publish_month: "06",
          publish_day: "20",
          channel_type: "crawler",
        };
        context.__probe.state.archives = [
          {
            archive_id: "a1",
            domain_type: "trading",
            channel_type: "crawler",
            business_key: "trading:src:1",
            title: "学校施工招标公告",
            region_code: "330100",
            publish_date: "2026-06-20",
            status: "pending_tag",
            metadata: { notice_type_raw: cell("招标公告"), exchange_name: cell("杭州市公共资源交易中心") },
            priced_source_count: 1,
            primary_file: { file_id: "f1", file_name: "notice.html", file_role: "web_snapshot" },
          },
          {
            archive_id: "a2",
            domain_type: "trading",
            channel_type: "crawler",
            business_key: "trading:src:2",
            title: "学校施工中标结果",
            region_code: "330100",
            publish_date: "2026-06-20",
            status: "pending_tag",
            metadata: { notice_type_raw: cell("中标结果"), exchange_name: cell("杭州市公共资源交易中心") },
            priced_source_count: 1,
          },
          {
            archive_id: "a3",
            domain_type: "trading",
            channel_type: "crawler",
            business_key: "trading:src:3",
            title: "学校施工招标公告",
            region_code: "330100",
            publish_date: "2026-06-21",
            status: "pending_tag",
            metadata: { notice_type_raw: cell("招标公告"), exchange_name: cell("杭州市公共资源交易中心") },
            priced_source_count: 1,
          },
        ];

        const noticeFilter = context.__probe.domainConfigs.trading.filters.find((filter) => filter.key === "notice_type_raw");
        const noticeOptions = context.__probe.filterOptions(noticeFilter).map((option) => option.value);
        if (!noticeOptions.includes("招标公告") || !noticeOptions.includes("中标结果")) {
          throw new Error(`notice enum options missing: ${noticeOptions.join(",")}`);
        }

        const rows = context.__probe.filteredArchives();
        if (rows.length !== 1 || rows[0].archive_id !== "a1") {
          throw new Error(`expected only a1 after trading filters, got ${rows.map((row) => row.archive_id).join(",")}`);
        }
        """
    )


def test_trading_filters_hide_attachment_type_and_reveal_day_after_year_month():
    run_ui_probe(
        """
        const panel = baseElement();
        elements.set("#filterPanel", panel);
        context.__probe.state.domain = "trading";
        context.__probe.state.viewMode = "archives";
        context.__probe.state.filterAdvancedOpen = false;
        context.__probe.state.filters = context.defaultFilters("trading");

        context.renderFilters();

        if (!panel.innerHTML.includes("年份")) throw new Error(panel.innerHTML);
        if (!panel.innerHTML.includes("月份")) throw new Error(panel.innerHTML);
        if (panel.innerHTML.includes("公告类型")) throw new Error("公告类型 should be advanced by default");
        if (panel.innerHTML.includes("附件类型")) throw new Error("附件类型 should be hidden");
        if (panel.innerHTML.includes("入湖通道")) throw new Error("入湖通道 should be advanced by default");
        if (panel.innerHTML.includes("日期")) throw new Error("日期 should wait for selected year and month");
        if (!panel.innerHTML.includes("更多筛选")) throw new Error(panel.innerHTML);

        context.__probe.state.filters.publish_year = "2026";
        context.__probe.state.filters.publish_month = "06";
        context.renderFilters();
        if (!panel.innerHTML.includes("日期")) throw new Error(`日期 filter missing after year/month: ${panel.innerHTML}`);

        context.__probe.state.filterAdvancedOpen = true;
        context.renderFilters();
        if (!panel.innerHTML.includes("公告类型")) throw new Error(`advanced notice type missing: ${panel.innerHTML}`);
        if (!panel.innerHTML.includes("入湖通道")) throw new Error(`advanced channel missing: ${panel.innerHTML}`);
        if (panel.innerHTML.includes("附件类型")) throw new Error("附件类型 should remain hidden in advanced filters");
        """
    )


def test_region_filter_expands_city_options_from_static_dictionary():
    run_ui_probe(
        """
        const cities = context.__probe.cityOptions("51").map((option) => option.value);
        if (!cities.includes("510100") || !cities.includes("510700")) {
          throw new Error(`四川地市选项缺失: ${cities.join(",")}`);
        }
        if (context.__probe.regionLabel("510100") !== "成都市") {
          throw new Error(`510100 应显示成都市, got ${context.__probe.regionLabel("510100")}`);
        }
        """
    )


def test_manual_upload_defaults_prefill_from_cost_info_filters():
    run_ui_probe(
        """
        context.__probe.state.domain = "cost_info";
        context.__probe.state.viewMode = "archives";
        context.__probe.state.filters = {
          category_raw: "all",
          region_code: "51",
          region_code_city: "511400",
          period_year: "2026",
          period_month: "06",
          channel_type: "all",
        };

        const defaults = context.__probe.manualUploadDefaults();

        if (defaults.region_code !== "511400") throw new Error(`expected 511400, got ${defaults.region_code}`);
        if (defaults.period !== "2026-06") throw new Error(`expected 2026-06, got ${defaults.period}`);
        if (defaults.tax_type !== "") throw new Error(`tax_type should default blank/null, got ${defaults.tax_type}`);
        if (defaults.publisher !== "") throw new Error(`publisher should stay manual, got ${defaults.publisher}`);
        if (defaults.title !== "") throw new Error(`title should remain blank for multi-file uploads, got ${defaults.title}`);
        """
    )


def test_manual_upload_archive_payload_keeps_manual_layer0_boundary():
    run_ui_probe(
        """
        const payload = context.__probe.buildManualUploadArchivePayload(
          { ingest_event_id: "evt_manual", source_id: "src_manual" },
          {
            region_code: "511400",
            period: "2026-06",
            price_source_type: "info_price",
            publisher: "眉山市建设工程造价管理站",
            tax_type: "",
            title: "眉山市2026年6月信息价.pdf",
            file_name: "眉山市2026年6月信息价.pdf",
            file_ext: "pdf",
          }
        );

        if (payload.event_id !== "evt_manual") throw new Error(`wrong event_id ${payload.event_id}`);
        if (payload.domain_type !== "cost_info") throw new Error(`wrong domain ${payload.domain_type}`);
        if (payload.channel_type !== "manual_upload") throw new Error(`wrong channel ${payload.channel_type}`);
        if (payload.collection_method !== "manual_denovo") throw new Error(`wrong method ${payload.collection_method}`);
        if (payload.status !== "collected") throw new Error(`wrong status ${payload.status}`);
        if (payload.business_key !== "cost_info:src_manual:511400:2026-06:眉山市2026年6月信息价.pdf") {
          throw new Error(`wrong business_key ${payload.business_key}`);
        }
        if (payload.metadata.tax_type.value !== null) throw new Error("blank tax_type must become null");
        if (payload.metadata.period.source_level !== "manual") throw new Error("period source must be manual");
        if (payload.metadata.coverage_region_code.value !== "511400") throw new Error("coverage region missing");
        if (payload.metadata.price_source_type.value !== "info_price") throw new Error("price source missing");
        if (payload.metadata.parsability.value !== "image_based") throw new Error(`pdf manual parsability should stay preview-only image_based, got ${payload.metadata.parsability.value}`);
        for (const field of ["domain_type", "channel_type", "collection_method", "business_key", "title", "region_code", "publish_date"]) {
          if (payload.field_sources[field].source_level !== "manual") throw new Error(`${field} source not manual`);
        }
        """
    )


def test_manual_edit_patch_recomputes_business_key_and_keeps_manual_layer0_boundary():
    run_ui_probe(
        """
        const archive = {
          archive_id: "archive_manual",
          source_id: "src_manual",
          title: "2026年第6期眉山市建设工程造价信息.pdf",
          region_code: "511400",
          channel_type: "manual_upload",
          primary_file: { file_id: "file_manual", file_name: "2026年第6期眉山市建设工程造价信息.pdf", file_role: "main_document" },
          metadata: {
            period: { value: "2026-05", source_level: "manual" },
            price_source_type: { value: "info_price", source_level: "manual" },
            tax_type: { value: null, source_level: "manual" },
            publisher: { value: "眉山市建设工程造价管理站", source_level: "manual" },
          },
        };
        const defaults = context.__probe.manualEditDefaults(archive);
        if (defaults.period !== "2026-05") throw new Error(`wrong period default ${defaults.period}`);
        if (defaults.file_name !== "2026年第6期眉山市建设工程造价信息.pdf") throw new Error(`wrong file ${defaults.file_name}`);

        const patch = context.__probe.buildManualEditPatch(archive, {
          ...defaults,
          period: "2026-06",
          title: "2026年第6期眉山市建设工程造价信息.pdf",
          publisher: "眉山市建设工程造价管理站",
        });

        if (patch.business_key !== "cost_info:src_manual:511400:2026-06:2026年第6期眉山市建设工程造价信息.pdf") {
          throw new Error(`wrong business_key ${patch.business_key}`);
        }
        if (patch.channel_type || patch.collection_method || patch.file_id) throw new Error(JSON.stringify(patch));
        if (patch.metadata.period.value !== "2026-06") throw new Error(JSON.stringify(patch.metadata.period));
        if (patch.metadata.period.source_level !== "manual") throw new Error("period must stay manual");
        if (patch.metadata.tax_type.value !== null) throw new Error("blank tax_type must stay null");
        for (const field of ["business_key", "title", "region_code", "publish_date"]) {
          if (patch.field_sources[field].source_level !== "manual") throw new Error(`${field} source not manual`);
        }
        """
    )


def test_submit_manual_upload_calls_existing_backend_chain():
    run_ui_probe(
        """
        const calls = [];
        context.FormData = class {
          constructor() { this.fields = []; }
          append(key, value) { this.fields.push([key, value]); }
        };
        context.fetch = async (url, options = {}) => {
          calls.push({ url, options });
          if (url.startsWith("/api/data-sources") && !options.method) {
            return { ok: true, json: async () => [], text: async () => "" };
          }
          if (url === "/api/data-sources") {
            return { ok: true, json: async () => ({ source_id: "src_manual", connector_type: "manual_upload", region_code: "511400", config: { stable: { site_id: "cost_info.manual.511400" } } }), text: async () => "" };
          }
          if (url === "/api/file-assets/ingest") {
            return { ok: true, json: async () => ({ file_id: "file_manual", ingest_event_id: "evt_manual", duplicated: false, processing_ids: [] }), text: async () => "" };
          }
          if (url === "/api/archives/from-ingest-event") {
            return { ok: true, json: async () => ({ archive_id: "archive_manual", channel_type: "manual_upload" }), text: async () => "" };
          }
          throw new Error(`unexpected url ${url}`);
        };

        await context.__probe.submitManualUpload(
          [{ file: { name: "眉山市2026年6月信息价.pdf", size: 2048 }, role: "main_document" }],
          {
            region_code: "511400",
            period: "2026-06",
            price_source_type: "info_price",
            publisher: "眉山市建设工程造价管理站",
            tax_type: "",
            title: "眉山市2026年6月信息价.pdf",
            file_name: "眉山市2026年6月信息价.pdf",
            file_ext: "pdf",
          }
        );

        if (calls.length !== 4) throw new Error(`expected 4 calls, got ${calls.length}`);
        if (!calls[0].url.startsWith("/api/data-sources?source_type=info_price")) throw new Error(calls[0].url);
        const sourcePayload = JSON.parse(calls[1].options.body);
        if (sourcePayload.connector_type !== "manual_upload") throw new Error(calls[1].options.body);
        if (sourcePayload.config.stable.coverage_region_code !== "511400") throw new Error(calls[1].options.body);
        const formFields = Object.fromEntries(calls[2].options.body.fields);
        if (formFields.tenant_code !== "platform_public") throw new Error(JSON.stringify(formFields));
        if (formFields.source_type !== "info_price") throw new Error(JSON.stringify(formFields));
        if (formFields.source_id !== "src_manual") throw new Error(JSON.stringify(formFields));
        if (formFields.derive_tasks !== "false") throw new Error(JSON.stringify(formFields));
        if (!String(formFields.source_item_key).includes("manual:511400:2026-06")) throw new Error(JSON.stringify(formFields));
        const archivePayload = JSON.parse(calls[3].options.body);
        if (archivePayload.channel_type !== "manual_upload") throw new Error(calls[3].options.body);
        if (archivePayload.collection_method !== "manual_denovo") throw new Error(calls[3].options.body);
        if (archivePayload.metadata.tax_type.value !== null) throw new Error(calls[3].options.body);
        """
    )


def test_ui_load_archives_pages_until_total_count_is_loaded():
    run_ui_probe(
        """
        const calls = [];
        const pageRows = {
          0: [{ archive_id: "a1", status: "pending_tag" }, { archive_id: "a2", status: "archived" }],
          2: [{ archive_id: "a3", status: "collect_failed" }],
        };
        context.fetch = async (url) => {
          const parsed = new URL(url, "http://local.test");
          const offset = Number(parsed.searchParams.get("offset") || "0");
          calls.push({ limit: parsed.searchParams.get("limit"), offset });
          return {
            ok: true,
            headers: { get: (name) => name.toLowerCase() === "x-total-count" ? "3" : null },
            json: async () => pageRows[offset] || [],
            text: async () => "",
          };
        };

        await context.__probe.loadArchives();
        for (let index = 0; index < 10 && context.__probe.state.archives.length < 3; index += 1) {
          await Promise.resolve();
        }

        if (context.__probe.state.archiveTotalCount !== 3) {
          throw new Error(`expected total count 3, got ${context.__probe.state.archiveTotalCount}`);
        }
        if (context.__probe.state.archives.length !== 3) {
          throw new Error(`expected 3 loaded archives, got ${context.__probe.state.archives.length}`);
        }
        if (calls.length !== 2 || calls[0].offset !== 0 || calls[1].offset !== 2) {
          throw new Error(`expected two paged calls, got ${JSON.stringify(calls)}`);
        }
        """
    )


def test_region_filter_matches_province_then_city_code():
    run_ui_probe(
        """
        const cell = (value) => ({ value, source_level: "crawler", tagged_by: "test", tagged_at: "2026-06-20T10:00:00+08:00" });
        context.__probe.state.domain = "cost_info";
        context.__probe.state.archives = [
          {
            archive_id: "chengdu",
            domain_type: "cost_info",
            channel_type: "crawler",
            business_key: "cost:chengdu",
            title: "成都市2026年6月信息价",
            region_code: "510100",
            status: "pending_tag",
            metadata: { city_raw: cell("成都市"), publisher: cell("成都市住建局") },
          },
          {
            archive_id: "mianyang",
            domain_type: "cost_info",
            channel_type: "crawler",
            business_key: "cost:mianyang",
            title: "绵阳市2026年6月信息价",
            region_code: "510700",
            status: "pending_tag",
            metadata: { city_raw: cell("绵阳市"), publisher: cell("绵阳市住建局") },
          },
          {
            archive_id: "hangzhou",
            domain_type: "cost_info",
            channel_type: "crawler",
            business_key: "cost:hangzhou",
            title: "杭州市2026年6月信息价",
            region_code: "330100",
            status: "pending_tag",
            metadata: { city_raw: cell("杭州市"), publisher: cell("杭州市住建局") },
          },
        ];

        context.__probe.state.filters = {
          category_raw: "all",
          publisher: "all",
          region_code: "51",
          region_code_city: "all",
          period_year: "all",
          period_month: "all",
          channel_type: "all",
        };
        const provinceRows = context.__probe.filteredArchives().map((row) => row.archive_id);
        if (provinceRows.join(",") !== "chengdu,mianyang") {
          throw new Error(`四川省筛选应命中成都和绵阳, got ${provinceRows.join(",")}`);
        }

        context.__probe.state.filters.region_code_city = "510100";
        const cityRows = context.__probe.filteredArchives().map((row) => row.archive_id);
        if (cityRows.join(",") !== "chengdu") {
          throw new Error(`成都市筛选应只命中成都, got ${cityRows.join(",")}`);
        }
        """
    )


def test_ui_download_links_target_layer0_file_asset_endpoint():
    run_ui_probe(
        """
        if (typeof context.downloadUrl !== "function") throw new Error("downloadUrl missing");
        if (context.downloadUrl({ file_id: "file id/中文" }) !== "/api/file-assets/file%20id%2F%E4%B8%AD%E6%96%87/download") {
          throw new Error(`unexpected download URL: ${context.downloadUrl({ file_id: "file id/中文" })}`);
        }

        const rowHtml = context.renderActions({
          archive_id: "a1",
          primary_file: { file_id: "f1", file_name: "重庆工程造价.pdf", file_role: "main_document" },
        });
        if (!rowHtml.includes('/api/file-assets/f1/download')) throw new Error(rowHtml);
        if (!rowHtml.includes('download')) throw new Error(rowHtml);
        """
    )


def test_manual_upload_rows_expose_edit_and_delete_actions():
    run_ui_probe(
        """
        const rowHtml = context.renderActions({
          archive_id: "manual1",
          channel_type: "manual_upload",
          primary_file: { file_id: "f1", file_name: "2026年第6期眉山信息价.pdf", file_role: "main_document" },
        });
        if (!rowHtml.includes('data-action="edit-archive"')) throw new Error(rowHtml);
        if (!rowHtml.includes('data-action="delete-archive"')) throw new Error(rowHtml);
        if (!rowHtml.includes('pencil')) throw new Error(rowHtml);
        if (!rowHtml.includes('trash-2')) throw new Error(rowHtml);

        const crawlerHtml = context.renderActions({
          archive_id: "crawler1",
          channel_type: "crawler",
          primary_file: { file_id: "f2", file_name: "绵阳信息价.pdf", file_role: "main_document" },
        });
        if (crawlerHtml.includes('data-action="delete-archive"')) throw new Error(crawlerHtml);
        if (!crawlerHtml.includes('data-action="withdraw-archive"')) throw new Error(crawlerHtml);
        if (!crawlerHtml.includes('不删除 NAS 原件')) throw new Error(crawlerHtml);
        """
    )


def test_ui_preview_links_use_layer0_preview_endpoint():
    run_ui_probe(
        """
        if (typeof context.previewUrl !== "function") throw new Error("previewUrl missing");
        if (context.previewUrl({ file_id: "file id/中文" }) !== "/api/file-assets/file%20id%2F%E4%B8%AD%E6%96%87/preview") {
          throw new Error(`unexpected preview URL: ${context.previewUrl({ file_id: "file id/中文" })}`);
        }
        """
    )


def test_storage_audit_bar_renders_missing_nas_object_state():
    run_ui_probe(
        """
        if (typeof context.__probe.renderStorageAuditBar !== "function") throw new Error("renderStorageAuditBar missing");
        context.__probe.state.storageAudit = {
          loading: false,
          error: "",
          data: {
            health_status: "degraded",
            checked_count: 849,
            ok_count: 848,
            missing_count: 1,
            size_mismatch_count: 0,
            orphan_reference_count: 7,
            error_count: 0,
            availability_rate: 848 / 849,
            issues: [
              { status: "missing", file_name: "德阳市2026年2月信息价.pdf", region_code: "510600" },
            ],
          },
        };
        const html = context.__probe.renderStorageAuditBar();
        if (!html.includes("NAS 原件")) throw new Error(html);
        if (!html.includes("848/849")) throw new Error(html);
        if (!html.includes("缺失 1")) throw new Error(html);
        if (!html.includes("孤儿引用 7")) throw new Error(html);
        if (!html.includes("德阳市2026年2月信息价.pdf")) throw new Error(html);
        if (!html.includes("storage-audit-degraded")) throw new Error(html);
        """
    )


def test_viewer_layout_defaults_to_pdf_first_and_hides_single_file_rail():
    run_ui_probe(
        """
        if (typeof context.viewerLayoutClass !== "function") throw new Error("viewerLayoutClass missing");
        if (typeof context.viewerShowsAttachmentRail !== "function") throw new Error("viewerShowsAttachmentRail missing");
        const single = {
          files: [{ file_id: "pdf1", file_name: "成都市2026年4月信息价.pdf", file_ext: "pdf", sort_order: 1 }],
        };
        context.__probe.state.viewerInfoOpen = false;
        const collapsed = context.viewerLayoutClass(single);
        if (!collapsed.includes("viewer-body")) throw new Error(collapsed);
        if (!collapsed.includes("single-file")) throw new Error(collapsed);
        if (!collapsed.includes("info-closed")) throw new Error(collapsed);
        if (collapsed.includes("has-attachments")) throw new Error(collapsed);
        if (context.viewerShowsAttachmentRail(single)) throw new Error("single file should not show attachment rail");

        context.__probe.state.viewerInfoOpen = true;
        const open = context.viewerLayoutClass(single);
        if (!open.includes("info-open")) throw new Error(open);
        """
    )


def test_viewer_reset_scroll_returns_excel_preview_to_top_left():
    run_ui_probe(
        """
        if (typeof context.__probe.resetViewerScroll !== "function") throw new Error("resetViewerScroll missing");
        const wrap = { scrollTop: 88, scrollLeft: 99 };
        const canvas = {
          scrollTop: 1370,
          scrollLeft: 320,
          querySelector(selector) {
            if (selector === ".excel-table-wrap") return wrap;
            return null;
          },
        };
        context.document.querySelector = (selector) => selector === "#viewerCanvas" ? canvas : null;

        context.__probe.resetViewerScroll();

        if (canvas.scrollTop !== 0 || canvas.scrollLeft !== 0) throw new Error(JSON.stringify(canvas));
        if (wrap.scrollTop !== 0 || wrap.scrollLeft !== 0) throw new Error(JSON.stringify(wrap));
        """
    )


def test_viewer_layout_keeps_multi_file_attachment_rail_available():
    run_ui_probe(
        """
        const multi = {
          files: [
            { file_id: "snap", file_name: "公告网页快照.html", file_ext: "html", sort_order: 1 },
            { file_id: "cdz", file_name: "控制价文件.cdz", file_ext: "cdz", sort_order: 2 },
            { file_id: "rar", file_name: "清单附件.rar", file_ext: "rar", sort_order: 3 },
            { file_id: "xlsx", file_name: "工程量清单.xlsx", file_ext: "xlsx", sort_order: 4 },
          ],
        };
        context.__probe.state.viewerInfoOpen = false;
        const collapsed = context.viewerLayoutClass(multi);
        if (!collapsed.includes("has-attachments")) throw new Error(collapsed);
        if (!collapsed.includes("info-closed")) throw new Error(collapsed);
        if (!context.viewerShowsAttachmentRail(multi)) throw new Error("multi file should show attachment rail");
        """
    )


def test_viewer_allows_image_based_cost_info_zip_preview_but_keeps_priced_zip_opaque():
    run_ui_probe(
        """
        if (typeof context.canPreviewZipInline !== "function") throw new Error("canPreviewZipInline missing");
        const imageZipArchive = {
          domain_type: "cost_info",
          metadata: {
            parsability: { value: "image_based" },
            source_attachment_mode: { value: "zip_package" },
          },
        };
        const imageZipFile = { file_id: "zip1", file_name: "自贡信息价图片包.zip", file_ext: "zip", file_role: "zip_package" };
        if (!context.canPreviewZipInline(imageZipArchive, imageZipFile)) throw new Error("image zip should preview");

        const tradingArchive = {
          domain_type: "trading",
          metadata: {
            parsability: { value: "image_based" },
            source_attachment_mode: { value: "zip_package" },
          },
        };
        const pricedZip = { file_id: "zip2", file_name: "控制价.CDZ", file_ext: "cdz", file_role: "priced_source" };
        if (context.canPreviewZipInline(tradingArchive, pricedZip)) throw new Error("priced source zip must remain opaque");
        """
    )


def test_viewer_css_prioritizes_canvas_and_collapses_auxiliary_panels():
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".viewer-body.single-file.info-closed" in css
    assert ".viewer-body.has-attachments.info-closed" in css
    assert ".viewer-body.info-closed .archive-form" in css
    assert ".viewer-body.single-file .viewer-sidebar" in css


def test_coverage_matrix_url_defaults_to_sichuan_city_level():
    run_ui_probe(
        """
        context.__probe.state.coverageFilters = {
          region_code: "51",
          region_code_city: "all",
          period_year: "2026",
          period_month: "all",
          target_level: "city",
          business_coverage_status: "all",
          source_completeness_status: "all",
        };
        const url = context.coverageMatrixUrl();
        if (!url.includes("/api/info-price/coverage-matrix")) throw new Error(url);
        if (!url.includes("province_code=510000")) throw new Error(url);
        if (!url.includes("start_period=2020-01")) throw new Error(url);
        if (!url.includes("end_period=2026-12")) throw new Error(url);
        if (!url.includes("target_level=city")) throw new Error(url);
        """
    )


def test_coverage_matrix_loads_without_waiting_for_source_dimensions_and_ignores_stale_results():
    run_ui_probe(
        """
        const pending = [];
        context.AbortController = class {
          constructor() { this.signal = {}; }
          abort() {}
        };
        context.setTimeout = () => 1;
        context.clearTimeout = () => {};
        context.fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }));
        context.__probe.state.domain = "cost_info";
        context.__probe.state.viewMode = "coverage";

        const initial = context.__probe.loadCurrentView();
        await Promise.resolve();
        const sourceRequest = pending.find((request) => request.url.includes("/api/data-sources"));
        const matrixRequest = pending.find((request) => request.url.includes("/api/info-price/coverage-matrix"));
        if (!sourceRequest || !matrixRequest) throw new Error("coverage and source requests should start together");

        matrixRequest.resolve({ ok: true, json: async () => [{ coverage_region_name: "北京市" }] });
        for (let index = 0; index < 10 && !context.__probe.state.coverageRows.length; index += 1) {
          await Promise.resolve();
        }
        if (context.__probe.state.coverageRows[0]?.coverage_region_name !== "北京市") throw new Error("matrix result was delayed by source dimensions");

        sourceRequest.resolve({ ok: true, json: async () => [] });
        await initial;

        const older = context.__probe.loadCoverageMatrix();
        await Promise.resolve();
        const olderRequest = pending[pending.length - 1];
        const newer = context.__probe.loadCoverageMatrix();
        await Promise.resolve();
        const newerRequest = pending[pending.length - 1];
        newerRequest.resolve({ ok: true, json: async () => [{ coverage_region_name: "上海市" }] });
        await newer;
        olderRequest.resolve({ ok: true, json: async () => [{ coverage_region_name: "北京市" }] });
        await older;
        if (context.__probe.state.coverageRows[0]?.coverage_region_name !== "上海市") throw new Error("stale coverage response replaced current filters");
        """
    )


def test_coverage_matrix_filter_panel_keeps_region_and_year_selector():
    run_ui_probe(
        """
        context.__probe.state.viewMode = "coverage";
        context.__probe.state.coverageFilters = {
          region_code: "35",
          region_code_city: "350400",
          period_year: "2026",
          period_month: "all",
          target_level: "city",
          business_coverage_status: "all",
          source_completeness_status: "all",
        };
        const keys = context.visibleFilterKeys();
        if (JSON.stringify(keys) !== JSON.stringify(["region_code", "period_year"])) throw new Error(keys.join(","));
        """
    )


def test_coverage_matrix_renders_period_cells_and_mianyang_paradox():
    run_ui_probe(
        """
        context.__probe.state.viewMode = "coverage";
        context.__probe.state.coverageFilters = {
          region_code: "51",
          region_code_city: "510700",
          period_year: "2026",
          period_month: "04",
          target_level: "subregion",
          business_coverage_status: "all",
          source_completeness_status: "all",
        };
        context.__probe.state.coverageRows = [
          {
            coverage_region_code: "510700-市区",
            coverage_region_name: "绵阳市区",
            target_level: "subregion",
            period: "2026-04",
            business_coverage_status: "covered",
            source_completeness_status: "dual_source",
            province_source_count: 1,
            city_source_count: 1,
            source_audit_status: "auto_crawl_verified",
          },
          {
            coverage_region_code: "510725",
            coverage_region_name: "梓潼县",
            target_level: "subregion",
            period: "2026-04",
            business_coverage_status: "covered",
            source_completeness_status: "city_source_missing",
            province_source_count: 1,
            city_source_count: 0,
            source_audit_status: "auto_crawl_verified",
          },
        ];
        const rows = context.filteredCoverageRows();
        if (rows.length !== 2) throw new Error(`expected 2 rows, got ${rows.length}`);
        const html = context.renderCoverageMatrixTable(rows);
        if (!html.includes("绵阳市区")) throw new Error(html);
        if (html.includes("梓潼县")) throw new Error(html);
        if (!html.includes("四川 · 区县信息价")) throw new Error(html);
        if (!html.includes("2026年")) throw new Error(html);
        if (!html.includes("4月")) throw new Error(html);
        """
    )


def test_coverage_matrix_renders_selected_city_year_month_grid_and_issue_label():
    run_ui_probe(
        """
        context.__probe.state.viewMode = "coverage";
        context.__probe.state.coverageFilters = {
          region_code: "35",
          region_code_city: "350400",
          period_year: "2026",
          period_month: "all",
          target_level: "city",
          business_coverage_status: "all",
          source_completeness_status: "all",
        };
        const rows = [
          {
            province_code: "350000",
            coverage_region_code: "350400",
            coverage_region_name: "三明市",
            target_level: "city",
            period: "2026-05",
            period_label: "第5期",
            evidence_titles: ["三明市建设工程主要材料综合价格2026年第5期"],
            business_coverage_status: "covered",
            source_completeness_status: "city_source_present",
            province_source_count: 0,
            city_source_count: 1,
            source_audit_status: "auto_crawl_verified",
          },
          {
            province_code: "350000",
            coverage_region_code: "350500",
            coverage_region_name: "泉州市",
            target_level: "city",
            period: "2026-05",
            business_coverage_status: "covered",
            source_completeness_status: "city_source_present",
            province_source_count: 0,
            city_source_count: 1,
            source_audit_status: "auto_crawl_verified",
          },
        ];
        const html = context.renderCoverageMatrixTable(rows);
        if (!html.includes("福建 · 各地市信息价")) throw new Error(html);
        if (!html.includes("三明市")) throw new Error(html);
        if (html.includes("泉州市")) throw new Error(html);
        if (!html.includes("年份") || !html.includes("5月")) throw new Error(html);
        if (!html.includes("5月/第5期")) throw new Error(html);
        if (!html.includes("三明市建设工程主要材料综合价格2026年第5期")) throw new Error(html);
        if (html.includes("coverage-city-tabs")) throw new Error(html);
        """
    )


def test_coverage_matrix_renders_original_site_link_for_online_sources():
    run_ui_probe(
        """
        const html = context.renderCoverageMatrixTable([
          {
            coverage_region_code: "510500",
            coverage_region_name: "泸州市",
            target_level: "city",
            period: "2026-04",
            business_coverage_status: "covered",
            source_completeness_status: "city_source_missing",
            province_source_count: 1,
            city_source_count: 0,
            source_audit_status: "pending_source_audit",
            source_visit_url: "https://zjj.luzhou.gov.cn/gczjxx/?period=2026-04",
          },
          {
            coverage_region_code: "510727",
            coverage_region_name: "平武县",
            target_level: "subregion",
            period: "2026-04",
            business_coverage_status: "covered",
            source_completeness_status: "city_source_missing",
            province_source_count: 1,
            city_source_count: 0,
            source_audit_status: "auto_crawl_verified",
          },
        ]);

        if (!html.includes("查看原站")) throw new Error(html);
        if (!html.includes("https://zjj.luzhou.gov.cn/gczjxx/?period=2026-04")) throw new Error(html);
        if (!html.includes('target="_blank"')) throw new Error(html);
        if (!html.includes('rel="noreferrer"')) throw new Error(html);
        if ((html.match(/href=/g) || []).length !== 1) throw new Error(html);
        """
    )


def test_coverage_matrix_cells_show_demo_status_modes_for_guangdong_distribution():
    run_ui_probe(
        """
        const fileHtml = context.renderCoverageCell({
          business_coverage_status: "covered",
          source_completeness_status: "city_source_present",
          source_audit_status: "auto_crawl_verified",
          province_source_count: 0,
          city_source_count: 1,
        });
        if (!fileHtml.includes("文件源") || !fileHtml.includes("mode-file")) throw new Error(fileHtml);

        const declarationHtml = context.renderCoverageCell({
          business_coverage_status: "covered",
          source_completeness_status: "city_source_present",
          source_audit_status: "online_table_declaration",
          province_source_count: 0,
          city_source_count: 1,
        });
        if (!declarationHtml.includes("覆盖声明") || !declarationHtml.includes("mode-declaration")) throw new Error(declarationHtml);

        const blockedHtml = context.renderCoverageCell({
          business_coverage_status: "pending_verify",
          source_completeness_status: "source_blocked",
          source_audit_status: "source_blocked",
          province_source_count: 0,
          city_source_count: 0,
        });
        if (!blockedHtml.includes("源受阻") || !blockedHtml.includes("mode-blocked")) throw new Error(blockedHtml);

        const pendingHtml = context.renderCoverageCell({
          business_coverage_status: "pending_verify",
          source_completeness_status: "city_source_present",
          source_audit_status: "auto_crawl_verified",
          province_source_count: 0,
          city_source_count: 1,
        });
        if (!pendingHtml.includes("待核") || !pendingHtml.includes("mode-pending")) throw new Error(pendingHtml);
        """
    )


def test_ui_patch_preserves_existing_provenance_for_unchanged_values():
    run_ui_probe(
        """
        const existingSource = { source_level: "crawler", tagged_by: "crawler:quota", tagged_at: "2026-06-20T08:00:00+08:00" };
        const existingCell = { value: "川建价发〔2026〕12号", ...existingSource };
        const existingNumericCell = { value: 2020, ...existingSource };
        elements.set("#fieldTitle", baseElement("四川省建设工程定额库"));
        elements.set("#fieldRegionCode", baseElement("510000"));
        elements.set("#fieldPublishDate", baseElement("2026-06-20"));
        metadataInputs.push({ dataset: { metadataField: "document_no_raw" }, value: "川建价发〔2026〕12号" });
        metadataInputs.push({ dataset: { metadataField: "version" }, value: "2020" });
        context.__probe.state.selectedArchive = {
          title: "四川省建设工程定额库",
          region_code: "510000",
          publish_date: "2026-06-20",
          domain_type: "policy_regulation",
          channel_type: "crawler",
          business_key: "policy_regulation:src:doc",
          metadata: { document_no_raw: existingCell, version: existingNumericCell },
          field_sources: {
            title: existingSource,
            region_code: existingSource,
            publish_date: existingSource,
            domain_type: existingSource,
            channel_type: existingSource,
            business_key: existingSource,
          },
        };

        const patch = context.__probe.collectPatch("ready_for_governance");
        if (patch.metadata.document_no_raw.source_level !== "crawler") throw new Error("metadata provenance was rewritten");
        if (patch.metadata.version.source_level !== "crawler") throw new Error("numeric metadata provenance was rewritten");
        if (patch.metadata.version.value !== 2020) throw new Error("numeric metadata value was not preserved");
        if (patch.field_sources.title.source_level !== "crawler") throw new Error("title provenance was rewritten");
        if (patch.field_sources.region_code.source_level !== "crawler") throw new Error("region provenance was rewritten");
        if (patch.field_sources.publish_date.source_level !== "crawler") throw new Error("publish provenance was rewritten");
        """
    )


def test_policy_effective_status_uses_china_local_day():
    run_ui_probe(
        """
        const RealDate = Date;
        context.Date = class extends RealDate {
          constructor(...args) {
            if (args.length) return new RealDate(...args);
            return new RealDate("2026-06-19T16:30:00.000Z");
          }
          static now() { return new RealDate("2026-06-19T16:30:00.000Z").getTime(); }
          static parse(value) { return RealDate.parse(value); }
          static UTC(...args) { return RealDate.UTC(...args); }
        };
        const status = context.__probe.policyEffectiveStatus({ metadata: { effective_date: { value: "2026-06-20", source_level: "crawler" } } });
        if (status !== "生效中") throw new Error(`expected China-local effective status, got ${status}`);
        """
    )


def test_ui_css_keeps_archive_table_controls_within_djt_density():
    css = (UI_DIR / "styles.css").read_text()

    rail_button = css_rule(css, ".rail-button")
    icon_button = css_rule(css, ".icon-button")
    table_cell = css_rule(css, ".archive-table td")
    file_secondary = css_rule(css, ".file-cell small")
    priced_source_mini = css_rule(css, ".priced-source-mini")

    assert "width: var(--control-height);" in rail_button
    assert "height: var(--control-height);" in rail_button
    assert "width: var(--control-height);" in icon_button
    assert "height: var(--control-height);" in icon_button
    assert "height: 30px;" in table_cell
    assert "padding: 1px 10px;" in table_cell
    assert "display: none;" in file_secondary
    assert "display: none;" in priced_source_mini
