/*
 * quota-ui.js · 清单定额档案台 (domain_type=quota) 主页面 · 状态与域分派
 * SPEC-QA-001 · P0-5A
 *
 * 职责：
 * - 接管 #quotaShell 主区域（其他数据域零改动，由 app.js 在 domain 切换时调用 activate/deactivate）。
 * - 专属 Header（统计未知显示 — 而非 0，禁止写死 8）。
 * - 四页签（档案列表/版本体系/覆盖矩阵/待归档），无能力时禁用并给单一原因，不各处重复"建设中"。
 * - 筛选骨架 + 列表骨架 + 四项新增菜单。
 * - 补录弹窗渲染与交互（表单逻辑/校验/草稿委托 quota-compose.js）。
 * - Feature gate：能力五态 + 生产禁 URL flag（委托 quota-api.js）。
 */
(function (global) {
  "use strict";

  const Api = global.QuotaApi;
  const Compose = global.QuotaCompose;

  const TAB_CAPABILITY = Object.freeze({
    archives: "archives",
    versionSystem: "publicationSets",
    coverage: "coverage",
    pending: "reconciliation",
  });

  const TABS = Object.freeze([
    { view: "archives", label: "档案列表", icon: "list" },
    { view: "versionSystem", label: "版本体系", icon: "layers" },
    { view: "coverage", label: "覆盖矩阵", icon: "table-2" },
    { view: "pending", label: "待归档", icon: "inbox" },
  ]);

  // 第一层筛选（一级分类 → 二级筛选映射）
  const PRIMARY_FILTERS = Object.freeze([
    { value: "all", label: "全部", secondary: null },
    { value: "boq_standard", label: "清单规范", secondary: "scope" },
    { value: "construction_regional", label: "建筑工程定额", secondary: "jurisdiction" },
    { value: "industry_specialty", label: "专业工程定额", secondary: "industry" },
  ]);

  function primaryMeta(primaryValue) {
    for (var i = 0; i < PRIMARY_FILTERS.length; i++) {
      if (PRIMARY_FILTERS[i].value === primaryValue) return PRIMARY_FILTERS[i];
    }
    return PRIMARY_FILTERS[0];
  }

  // 二级筛选标签（不含"全部"，由渲染函数自动添加）
  const SECONDARY_LABELS = Object.freeze({
    scope: "适用范围",
    jurisdiction: "地区",
    industry: "行业分类",
  });

  const LIST_COLUMNS = Object.freeze([
    "档案标题",
    "资料分类",
    "文件数",
    "状态",
    "操作",
  ]);

  // 分类标签映射（仅展示用，与 file_role 无关）
  const CATEGORY_LABELS = {
    construction_quota: "建筑工程定额",
    industry_quota: "专业工程定额",
    boq_standard: "清单规范",
  };

  const CAP = (Api && Api.CAP) || {
    UNKNOWN: "unknown",
    READY: "ready",
    UNAVAILABLE: "unavailable",
    UNAUTHORIZED: "unauthorized",
    ERROR: "error",
  };

  const state = {
    active: false,
    env: "production",
    initialized: false,
    api: null,
    capabilities: Api ? Api.allStatus(CAP.UNKNOWN) : {},
    flags: {},
    view: "archives",
    stats: { status: CAP.UNKNOWN, data: null },
    facets: { status: CAP.UNKNOWN, data: null },
    reconciliation: { status: CAP.UNKNOWN, data: null },
    archives: { status: CAP.UNKNOWN, data: null, error: "" },
    archiveDetail: { status: CAP.UNKNOWN, data: null, archiveId: null, error: "" },
    filters: {
      q: "",
      primary: "all",
      secondary: "all",
      editionYear: "all",
      edition: "all",
      // 高级筛选（收起）
      discipline: "all",
      materialType: "all",
      city: "all",
      issuer: "all",
      metadataStatus: "all",
      archiveStatus: "all",
      sourceChannel: "all",
      fileFormat: "all",
      advancedOpen: false,
      // "展开更多" 控制
      secondaryExpanded: false,
    },
    addMenuOpen: false,
    compose: null,
    composeWarning: "",
    composeAdvancedOpen: false,
    upload: { open: false, files: [], category: "", province: "", year: "", submitting: false },
    toast: "",
  };

  // ── 工具 ─────────────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function refreshIcons() {
    if (global.lucide) global.lucide.createIcons();
  }

  function setByPath(target, path, value) {
    const keys = String(path).split(".");
    let node = target;
    for (let i = 0; i < keys.length - 1; i += 1) {
      if (node[keys[i]] == null || typeof node[keys[i]] !== "object") node[keys[i]] = {};
      node = node[keys[i]];
    }
    node[keys[keys.length - 1]] = value;
  }

  // ── 适用地区静态列表 (GB/T 2260, 31 省级行政区 + 计划单列市 + 兵团) ──
  // 不依赖后端字典接口；值存区划代码, 标签仅作展示。
  const JURISDICTION_REGIONS = Object.freeze([
    {code:"110000",label:"北京市",py:"beijing"},
    {code:"120000",label:"天津市",py:"tianjin"},
    {code:"130000",label:"河北省",py:"hebei"},
    {code:"140000",label:"山西省",py:"shanxi"},
    {code:"150000",label:"内蒙古自治区",py:"neimenggu"},
    {code:"210000",label:"辽宁省",py:"liaoning"},
    {code:"210200",label:"大连市(计划单列市)",py:"dalian"},
    {code:"220000",label:"吉林省",py:"jilin"},
    {code:"230000",label:"黑龙江省",py:"heilongjiang"},
    {code:"310000",label:"上海市",py:"shanghai"},
    {code:"320000",label:"江苏省",py:"jiangsu"},
    {code:"330000",label:"浙江省",py:"zhejiang"},
    {code:"330200",label:"宁波市(计划单列市)",py:"ningbo"},
    {code:"340000",label:"安徽省",py:"anhui"},
    {code:"350000",label:"福建省",py:"fujian"},
    {code:"350200",label:"厦门市(计划单列市)",py:"xiamen"},
    {code:"360000",label:"江西省",py:"jiangxi"},
    {code:"370000",label:"山东省",py:"shandong"},
    {code:"370200",label:"青岛市(计划单列市)",py:"qingdao"},
    {code:"410000",label:"河南省",py:"henan"},
    {code:"420000",label:"湖北省",py:"hubei"},
    {code:"430000",label:"湖南省",py:"hunan"},
    {code:"440000",label:"广东省",py:"guangdong"},
    {code:"440300",label:"深圳市(计划单列市)",py:"shenzhen"},
    {code:"450000",label:"广西壮族自治区",py:"guangxi"},
    {code:"460000",label:"海南省",py:"hainan"},
    {code:"500000",label:"重庆市",py:"chongqing"},
    {code:"510000",label:"四川省",py:"sichuan"},
    {code:"520000",label:"贵州省",py:"guizhou"},
    {code:"530000",label:"云南省",py:"yunnan"},
    {code:"540000",label:"西藏自治区",py:"xizang"},
    {code:"610000",label:"陕西省",py:"shaanxi"},
    {code:"620000",label:"甘肃省",py:"gansu"},
    {code:"630000",label:"青海省",py:"qinghai"},
    {code:"640000",label:"宁夏回族自治区",py:"ningxia"},
    {code:"650000",label:"新疆维吾尔自治区",py:"xinjiang"},
    {code:"660000",label:"新疆生产建设兵团",py:"bingtuan"},
  ]);

  // ── 省级单位 + 深圳市（筛选条 chip fallback 用）────────────────
  // 31 省级行政区 + 深圳市（440300）= 32 条；
  // 兵团（660000）不列出，4 个其他计划单列市也不列出。
  // short 字段用作 chip 显示文本，紧凑型 label，避免行宽爆炸。
  // 后端 /facets 真正接好后自动让位给接口数据。
  const PROVINCE_REGIONS = Object.freeze([
    {code:"110000",label:"北京市",py:"beijing",short:"北京"},
    {code:"120000",label:"天津市",py:"tianjin",short:"天津"},
    {code:"130000",label:"河北省",py:"hebei",short:"河北"},
    {code:"140000",label:"山西省",py:"shanxi",short:"山西"},
    {code:"150000",label:"内蒙古自治区",py:"neimenggu",short:"内蒙古"},
    {code:"210000",label:"辽宁省",py:"liaoning",short:"辽宁"},
    {code:"220000",label:"吉林省",py:"jilin",short:"吉林"},
    {code:"230000",label:"黑龙江省",py:"heilongjiang",short:"黑龙江"},
    {code:"310000",label:"上海市",py:"shanghai",short:"上海"},
    {code:"320000",label:"江苏省",py:"jiangsu",short:"江苏"},
    {code:"330000",label:"浙江省",py:"zhejiang",short:"浙江"},
    {code:"340000",label:"安徽省",py:"anhui",short:"安徽"},
    {code:"350000",label:"福建省",py:"fujian",short:"福建"},
    {code:"360000",label:"江西省",py:"jiangxi",short:"江西"},
    {code:"370000",label:"山东省",py:"shandong",short:"山东"},
    {code:"410000",label:"河南省",py:"henan",short:"河南"},
    {code:"420000",label:"湖北省",py:"hubei",short:"湖北"},
    {code:"430000",label:"湖南省",py:"hunan",short:"湖南"},
    {code:"440000",label:"广东省",py:"guangdong",short:"广东"},
    {code:"450000",label:"广西壮族自治区",py:"guangxi",short:"广西"},
    {code:"460000",label:"海南省",py:"hainan",short:"海南"},
    {code:"500000",label:"重庆市",py:"chongqing",short:"重庆"},
    {code:"510000",label:"四川省",py:"sichuan",short:"四川"},
    {code:"520000",label:"贵州省",py:"guizhou",short:"贵州"},
    {code:"530000",label:"云南省",py:"yunnan",short:"云南"},
    {code:"540000",label:"西藏自治区",py:"xizang",short:"西藏"},
    {code:"610000",label:"陕西省",py:"shaanxi",short:"陕西"},
    {code:"620000",label:"甘肃省",py:"gansu",short:"甘肃"},
    {code:"630000",label:"青海省",py:"qinghai",short:"青海"},
    {code:"640000",label:"宁夏回族自治区",py:"ningxia",short:"宁夏"},
    {code:"650000",label:"新疆维吾尔自治区",py:"xinjiang",short:"新疆"},
    {code:"440300",label:"深圳市",py:"shenzhen",short:"深圳"},
  ]);

  // 适应地区下拉选项 HTML (带拼音 data 属性, 供搜索过滤)
  function renderJurisdictionOptions(selectedCode) {
    var html = '<option value="">请选择适用地区</option>';
    for (var i = 0; i < JURISDICTION_REGIONS.length; i++) {
      var r = JURISDICTION_REGIONS[i];
      var sel = (r.code === selectedCode) ? " selected" : "";
      html += '<option value="' + escapeHtml(r.code) + '" data-label="' + escapeHtml(r.label) + '" data-py="' + r.py + '"' + sel + '>' + escapeHtml(r.label) + '</option>';
    }
    return html;
  }

  // ── 上传弹窗省份白名单（与后端 file_asset_service/app/quota_api.py:_UPLOAD_PROVINCE_MAP 同步）──
  // 31 省级单位 + 深圳 = 32 条；value=省简称短码（与后端 _VALID_PROVINCE_CODES 一致）
  const QUOTA_UPLOAD_PROVINCES = Object.freeze([
    { code: "sc",  label: "四川省" },
    { code: "cq",  label: "重庆市" },
    { code: "bj",  label: "北京市" },
    { code: "tj",  label: "天津市" },
    { code: "hb",  label: "河北省" },
    { code: "sx",  label: "山西省" },
    { code: "nm",  label: "内蒙古自治区" },
    { code: "ln",  label: "辽宁省" },
    { code: "jl",  label: "吉林省" },
    { code: "hl",  label: "黑龙江省" },
    { code: "sh",  label: "上海市" },
    { code: "js",  label: "江苏省" },
    { code: "zj",  label: "浙江省" },
    { code: "ah",  label: "安徽省" },
    { code: "fj",  label: "福建省" },
    { code: "jx",  label: "江西省" },
    { code: "sd",  label: "山东省" },
    { code: "yu",  label: "河南省" },
    { code: "hu",  label: "湖北省" },
    { code: "xi",  label: "湖南省" },
    { code: "gd",  label: "广东省" },
    { code: "gx",  label: "广西壮族自治区" },
    { code: "hi",  label: "海南省" },
    { code: "gz",  label: "贵州省" },
    { code: "yn",  label: "云南省" },
    { code: "xz",  label: "西藏自治区" },
    { code: "snx", label: "陕西省" },
    { code: "gs",  label: "甘肃省" },
    { code: "qh",  label: "青海省" },
    { code: "nx",  label: "宁夏回族自治区" },
    { code: "xj",  label: "新疆维吾尔自治区" },
    { code: "sz",  label: "深圳市" },
  ]);

  function renderUploadProvinceOptions(selectedCode) {
    var html = '<option value="">请选择省份</option>';
    for (var i = 0; i < QUOTA_UPLOAD_PROVINCES.length; i++) {
      var r = QUOTA_UPLOAD_PROVINCES[i];
      var sel = (r.code === selectedCode) ? " selected" : "";
      html += '<option value="' + escapeHtml(r.code) + '"' + sel + '>' + escapeHtml(r.label) + '</option>';
    }
    return html;
  }

  // 统计展示：未知/未就绪一律显示 —（禁止 0、禁止写死 8）
  function statVal(key) {
    if (state.stats.status === CAP.READY && state.stats.data && state.stats.data[key] != null) {
      return String(state.stats.data[key]);
    }
    return "—";
  }

  function pendingCount() {
    if (state.reconciliation.status === CAP.READY && state.reconciliation.data) {
      const v = state.reconciliation.data.pending;
      if (v != null) return Number(v);
    }
    if (state.stats.status === CAP.READY && state.stats.data && state.stats.data.pendingRaw != null) {
      return Number(state.stats.data.pendingRaw);
    }
    return null;
  }

  function tabEnabled(view) {
    return state.flags[TAB_CAPABILITY[view]] === true;
  }

  function capReason(capabilityKey) {
    const status = state.capabilities[capabilityKey] || CAP.UNKNOWN;
    return Api ? Api.reasonText(status) : "能力建设中。";
  }

  // ── 渲染：Header + 统计条 ────────────────────────────────────────────
  function renderHeader() {
    const debug =
      Api && Api.isDev(state.env)
        ? `<span class="quota-debug-pill" title="调试信息，仅开发环境显示">Archive API / domain_type: quota</span>`
        : "";
    return `
      <header class="quota-header">
        <div class="quota-header-titles">
          <p class="eyebrow">Layer 0 · 清单定额档案台</p>
          <h1>清单定额档案台</h1>
          <span class="quota-summary">
            ${statVal("systems")} 套资料体系 · ${statVal("archived")} 份已归档 · ${statVal("pendingRaw")} 份原件待归档
          </span>
        </div>
        <div class="quota-header-actions">
          <label class="search-box">
            <i data-lucide="search"></i>
            <input id="quotaSearch" type="search" placeholder="搜索资料体系、分册、标准/定额编号" autocomplete="off" />
          </label>
          <button class="icon-button" type="button" title="刷新" data-quota-action="refresh">
            <i data-lucide="rotate-ccw"></i>
          </button>
          <div class="quota-add">
            <button class="primary-button" type="button" data-quota-action="add-menu-toggle" aria-haspopup="true" aria-expanded="${state.addMenuOpen}">
              <i data-lucide="plus"></i><span>新增档案</span>
            </button>
            ${state.addMenuOpen ? renderAddMenu() : ""}
          </div>
          ${debug}
        </div>
      </header>
      ${renderStatsBar()}
    `;
  }

  function renderStatsBar() {
    const cells = [
      { label: "原件", key: "rawTotal" },
      { label: "可访问", key: "accessible" },
      { label: "待归档", key: "pendingRaw" },
      { label: "重复", key: "duplicate" },
      { label: "异常", key: "invalid" },
    ];
    const note =
      state.stats.status === CAP.READY
        ? ""
        : `<span class="quota-statsbar-note">${escapeHtml(capReason("stats"))}</span>`;
    return `
      <section class="quota-statsbar" aria-label="quota 原件统计">
        <span class="section-marker" aria-hidden="true"></span>
        ${cells
          .map(
            (c) => `<span class="quota-stat"><strong>${statVal(c.key)}</strong><small>${c.label}</small></span>`
          )
          .join("")}
        ${note}
      </section>
    `;
  }

  function renderAddMenu() {
    const items = [
      { action: "new_boq", label: "新增清单规范" },
      { action: "new_set", label: "新建定额体系" },
      { action: "add_volume", label: "向已有体系新增分册" },
      { action: "supplement", label: "向已有档案补充文件" },
    ];
    return `
      <div class="quota-add-menu" role="menu">
        ${items
          .map(
            (it) =>
              `<button class="quota-add-item" type="button" role="menuitem" data-quota-action="add:${it.action}">${escapeHtml(
                it.label
              )}</button>`
          )
          .join("")}
      </div>
    `;
  }

  // ── 渲染：页签 ───────────────────────────────────────────────────────
  function renderTabs() {
    const badge = pendingCount();
    return `
      <section class="quota-tabs" aria-label="清单定额工作视图">
        ${TABS.map((tab) => {
          const enabled = tabEnabled(tab.view);
          const isActive = state.view === tab.view;
          const reason = enabled ? "" : capReason(TAB_CAPABILITY[tab.view]);
          const badgeHtml =
            tab.view === "pending" && badge != null && badge > 0
              ? `<span class="quota-tab-badge">${badge}</span>`
              : "";
          return `
            <button class="quota-tab ${isActive ? "active" : ""}" type="button"
              data-quota-action="set-view:${tab.view}"
              ${enabled ? "" : "disabled"}
              ${reason ? `title="${escapeHtml(reason)}"` : ""}>
              <i data-lucide="${tab.icon}"></i>
              <span>${escapeHtml(tab.label)}</span>
              ${badgeHtml}
            </button>`;
        }).join("")}
      </section>
    `;
  }

  // ── 渲染：筛选区（两级标签 + 条件版次 + 高级筛选）──────────────────
  function renderFilters() {
    var facetsReady = state.flags.facets === true;
    var meta = primaryMeta(state.filters.primary);
    var secondaryKey = meta.secondary;
    var secondaryLabel = secondaryKey ? (SECONDARY_LABELS[secondaryKey] || "") : "";

    // 一级分类 chips
    var primaryChips = PRIMARY_FILTERS.map(function (opt) {
      var active = state.filters.primary === opt.value ? " active" : "";
      return '<button class="filter-chip' + active + '" type="button" data-quota-action="set-primary:' + opt.value + '">' + escapeHtml(opt.label) + '</button>';
    }).join("");

    // API 未就绪：不渲染任何 chip，统一提示
    if (!facetsReady) {
      return '<section class="quota-filter-panel" aria-label="多维检索">' +
        '<div class="quota-filter-primary">' + primaryChips + '</div>' +
        '<p class="quota-filter-placeholder">分类与年份筛选待接入数据接口。</p>' +
        renderAdvancedFilterToggle() +
        (state.filters.advancedOpen ? renderAdvancedFilterBody() : "") +
        '</section>';
    }

    // ── facets ready 路径（二级标签组）───────────────────────────────
    return '<section class="quota-filter-panel" aria-label="多维检索">' +
      '<div class="quota-filter-primary">' + primaryChips + '</div>' +
      renderSecondaryChips(secondaryKey, secondaryLabel) +
      renderYearChips() +
      renderEditionChips() +
      '<div class="quota-filter-row">' +
        renderAdvancedFilterToggle() +
      '</div>' +
      (state.filters.advancedOpen ? renderAdvancedFilterBody() : "") +
      '</section>';
  }

  // ── 二级筛选标签组（地区 / 行业分类 / 适用范围）─────────────────────
  function renderSecondaryChips(key, label) {
    if (!key) return "";
    // 数据来自 facets.data[key + "s"]（如 jurisdictions / industries / scopes）
    var items = getFacetItems(key);
    var chips = items.length
      ? renderChipGroup(key, "secondary", label, items, state.filters.secondary, state.filters.secondaryExpanded)
      : '<span class="quota-filter-note">' + escapeHtml(label) + '维度待接入</span>';
    return '<div class="quota-filter-row"><span class="quota-filter-row-label">' + escapeHtml(label) + '</span>' + chips + '</div>';
  }

  // ── 年份标签组 ──────────────────────────────────────────────────────
  // 年份是「与省份正交」的维度：选全部省份也要能按年份筛（如"全国所有 2025 年定额"）。
  // 后端 /facets 在 jurisdiction_code 缺省时返回所有省份的 years 并集，前端 fallback
  // 也是最近 6 年，所以不区分 secondary 是否具体选。
  function renderYearChips() {
    if (state.filters.primary === "all") return "";
    var items = getFacetItems("years");
    var chips = items.length
      ? renderChipGroup("year", "editionYear", "年份", items, state.filters.editionYear)
      : '<span class="quota-filter-note">年份维度待接入</span>';
    return '<div class="quota-filter-row"><span class="quota-filter-row-label">年份</span>' + chips + '</div>';
  }

  // ── 版次标签组（条件出现）────────────────────────────────────────────
  function renderEditionChips() {
    if (state.filters.primary === "all") return "";
    // 仅在选择具体年份后显示
    if (state.filters.editionYear === "all") return "";
    var editions = getEditionItems(state.filters.editionYear);
    // 单版次不显示
    if (!editions.length || editions.length <= 1) return "";
    var chips = renderChipGroup("edition", "edition", "版次", editions, state.filters.edition);
    return '<div class="quota-filter-row"><span class="quota-filter-row-label">版次</span>' + chips + '</div>';
  }

  // ── 通用标签组渲染 ──────────────────────────────────────────────────
  function renderChipGroup(field, stateKey, label, items, selected, expanded) {
    // 主筛选维度（地区/行业/适用范围/年份/版次）行宽能容下，不强制折叠；
    // 高级筛选字段保留 8 折叠上限，避免一屏过宽。
    var MAIN_FIELDS = ["jurisdiction", "industry", "scope", "year", "edition"];
    var maxDefault = MAIN_FIELDS.indexOf(field) !== -1 ? 32 : 8;
    if (typeof expanded === "undefined") expanded = false;
    var allChip = '<button class="filter-chip' + (selected === "all" ? " active" : "") +
      '" type="button" data-quota-action="set-' + stateKey + ':all">全部</button>';
    var itemChips = items.map(function (item, idx) {
      var active = selected === item.value ? " active" : "";
      var hiddenClass = (!expanded && idx >= maxDefault) ? " filter-chip-overflow" : "";
      return '<button class="filter-chip' + active + hiddenClass + '" type="button" data-quota-action="set-' + stateKey + ':' + item.value + '">' + escapeHtml(item.label) + '</button>';
    });
    var html = '<div class="filter-chip-group">' + allChip + itemChips.join("");
    if (items.length > maxDefault) {
      html += '<button class="filter-chip filter-chip-more" type="button" data-quota-action="toggle-' + field + '-expand">' +
        (expanded ? '收起' : '展开更多（' + (items.length - maxDefault) + '）') + '</button>';
    }
    html += '</div>';
    return html;
  }

  // ── 高级筛选折叠切换 ────────────────────────────────────────────────
  function renderAdvancedFilterToggle() {
    return '<button class="filter-chip filter-toggle' + (state.filters.advancedOpen ? " active" : "") + '" type="button" data-quota-action="toggle-advanced">' +
      (state.filters.advancedOpen ? "收起高级筛选" : "高级筛选") + '</button>';
  }

  // ── 高级筛选内容（折叠区）────────────────────────────────────────────
  function renderAdvancedFilterBody() {
    var ready = state.flags.facets === true;
    if (!ready) {
      return '<div class="quota-filter-advanced">' +
        '<span class="quota-filter-note">分册专业 / 资料性质 / 地市 / 发布单位 / 元数据状态 / 档案状态 / 入湖通道 / 文件格式待接入 facets 接口。</span>' +
        '</div>';
    }
    var fields = [
      { key: "discipline", label: "分册专业", items: getFacetItems("disciplines") },
      { key: "materialType", label: "资料性质", items: getFacetItems("materialTypes") },
      { key: "city", label: "地市", items: getFacetItems("cities") },
      { key: "issuer", label: "发布单位", items: getFacetItems("issuers") },
      { key: "metadataStatus", label: "元数据状态", items: getFacetItems("metadataStatuses") },
      { key: "archiveStatus", label: "档案状态", items: getFacetItems("archiveStatuses") },
      { key: "sourceChannel", label: "入湖通道", items: getFacetItems("sourceChannels") },
      { key: "fileFormat", label: "文件格式", items: getFacetItems("fileFormats") },
    ];
    var rows = fields.map(function (f) {
      if (!f.items || !f.items.length) {
        return '<div class="quota-advanced-field"><span class="quota-advanced-label">' + escapeHtml(f.label) + '</span>' +
          '<span class="quota-filter-note">待接入</span></div>';
      }
      return '<div class="quota-advanced-field"><span class="quota-advanced-label">' + escapeHtml(f.label) + '</span>' +
        renderChipGroup(f.key, "adv_" + f.key, f.label, f.items, state.filters[f.key] || "all") + '</div>';
    }).join("");
    return '<div class="quota-filter-advanced">' + rows + '</div>';
  }

  // ── facets 数据访问（占位：数据来自 state.facets.data）─────────────
  // 构造 GET /facets 查询参数：
  //   - primary 始终传（决定 jurisdictions/industries/scopes/years 的语义）
  //   - 建筑工程定额 + 选了具体省份 → 透传 jurisdiction_code 让 years 按省过滤
  function _facetsParams() {
    var params = { primary: state.filters.primary };
    if (
      state.filters.primary === "construction_regional" &&
      state.filters.secondary &&
      state.filters.secondary !== "all"
    ) {
      params.jurisdiction_code = state.filters.secondary;
    }
    return params;
  }

  function getFacetItems(key) {
    // 后端 facets 数据为空 / 未就绪时仍允许静默兜底，避免"地区维度待接入"这种空位
    var fx = (state.facets && state.facets.data) || {};
    switch (key) {
      case "scope": case "scopes":
        return fx.scopes || [];
      case "jurisdiction": case "jurisdictions": {
        // HOTFIX-QA-CHIP-001 · 2026-07-29：地区 chip 一直显示 32 个省级单位（31 省 + 深圳市），
        // 不读后端 fx.jurisdictions。原因：之前 /facets 500 时静态兜底一直显示，
        // 修好 500 后 DB 里只有四川有档案 → chip 坍缩到 1 个，影响其他省份可见性。
        // 静态 chip 与"是否有数据"解耦 — 后端 facets 数据驱动的筛选命中数另在他处展示。
        return PROVINCE_REGIONS.map(function (r) {
          return { value: r.code, label: r.short || r.label };
        });
      }
      case "industry": case "industries":
        return fx.industries || [];
      case "years": {
        var fromYears = fx.years || [];
        if (fromYears.length) {
          return fromYears.map(function (y) {
            if (typeof y === "string") return { value: y, label: String(y), editions: [] };
            return { value: y.value, label: String(y.label || y.value), editions: y.editions || [] };
          });
        }
        // 兜底：最近 6 年倒序（仅在选了具体省份后才显示）
        return [2026, 2025, 2024, 2023, 2022, 2021].map(function (y) {
          return { value: String(y), label: String(y), editions: [] };
        });
      }
      case "disciplines":
        return fx.disciplines || [];
      case "materialTypes":
        return fx.materialTypes || [];
      case "cities":
        return fx.cities || [];
      case "issuers":
        return fx.issuers || [];
      case "metadataStatuses":
        return fx.metadataStatuses || [];
      case "archiveStatuses":
        return fx.archiveStatuses || [];
      case "sourceChannels":
        return fx.sourceChannels || [];
      case "fileFormats":
        return fx.fileFormats || [];
      default:
        return [];
    }
  }

  function getEditionItems(yearValue) {
    var years = getFacetItems("years");
    for (var i = 0; i < years.length; i++) {
      if (String(years[i].value) === String(yearValue)) {
        return years[i].editions || [];
      }
    }
    return [];
  }

  // ── 渲染：视图主体（按页签）──────────────────────────────────────────
  function renderBody() {
    switch (state.view) {
      case "archives":
        return renderArchivesView();
      case "archive-detail":
        return renderArchiveDetailView();
      case "pending":
        return renderPendingView();
      case "versionSystem":
        return renderCapabilityPlaceholder("publicationSets", "版本体系");
      case "coverage":
        return renderCapabilityPlaceholder("coverage", "覆盖矩阵");
      default:
        return "";
    }
  }

  function renderCapabilityPlaceholder(capabilityKey, title) {
    return `
      <section class="quota-view-card">
        <div class="quota-empty">
          <i data-lucide="construction"></i>
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(capReason(capabilityKey))}真实数据待 P0-4 接线。</span>
        </div>
      </section>
    `;
  }

  function renderArchivesView() {
    const head = LIST_COLUMNS.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
    let body;
    if (state.flags.archives !== true) {
      // 无能力：单一原因，不重复"建设中"
      body = `
        <div class="quota-empty">
          <i data-lucide="folder-search"></i>
          <strong>档案列表待接入</strong>
          <span>${escapeHtml(capReason("archives"))}</span>
          ${renderGoPendingButton()}
        </div>`;
    } else if (state.archives.status === CAP.READY && (state.archives.data || []).length) {
      const rows = state.archives.data.map((row) => renderArchiveRow(row)).join("");
      body = `<table class="archive-table quota-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
    } else if (state.archives.status === CAP.READY) {
      // 能力就绪但无数据
      body = `
        <div class="quota-empty">
          <i data-lucide="folder-search"></i>
          <strong>暂无已归档档案</strong>
          <span>当前筛选条件下无档案。点击右上角「新增档案」开始入库。</span>
          ${renderGoPendingButton()}
        </div>`;
    } else if (state.archives.status === CAP.ERROR) {
      const msg = state.archives.error || capReason("archives");
      body = `
        <div class="quota-empty">
          <i data-lucide="alert-circle"></i>
          <strong>档案列表加载失败</strong>
          <span>${escapeHtml(msg)}</span>
        </div>`;
    } else {
      // UNKNOWN / 加载中
      body = `
        <div class="quota-empty">
          <i data-lucide="folder-search"></i>
          <strong>档案列表加载中…</strong>
        </div>`;
    }
    return `<section class="quota-view-card">${body}</section>`;
  }

  function renderArchiveRow(row) {
    const cell = (v) => (v === null || v === undefined || v === "") ? "—" : escapeHtml(String(v));
    // category 存在 Archive.metadata_payload.category.value（由 upload 端点写入）
    var meta = row.metadata || {};
    var categoryCell = meta.category || {};
    var categoryVal = (categoryCell.value && CATEGORY_LABELS[categoryCell.value]) || categoryCell.value || "—";
    var archiveId = encodeURIComponent(row.archive_id || "");
    return `
      <tr data-quota-action="preview-archive" data-archive-id="${archiveId}" style="cursor:pointer" title="点击预览">
        <td class="quota-col-title">${cell(row.title)}</td>
        <td>${cell(categoryVal)}</td>
        <td class="quota-col-count">${cell(row.file_count)}</td>
        <td><span class="quota-status-pill">${cell(row.status)}</span></td>
        <td class="quota-col-actions">
          <button class="quota-preview-btn" type="button" data-quota-action="preview-archive" data-archive-id="${archiveId}" title="预览 PDF">
            <i data-lucide="eye"></i><span>预览</span>
          </button>
        </td>
      </tr>`;
  }

  function renderGoPendingButton() {
    const enabled = tabEnabled("pending");
    return `<button class="primary-button" type="button" data-quota-action="set-view:pending" ${
      enabled ? "" : "disabled title=\"" + escapeHtml(capReason("reconciliation")) + "\""
    }><i data-lucide="inbox"></i><span>前往待归档</span></button>`;
  }

  function renderPendingView() {
    if (state.flags.reconciliation !== true) {
      return `
        <section class="quota-view-card">
          <div class="quota-empty">
            <i data-lucide="inbox"></i>
            <strong>待归档工作台待接入</strong>
            <span>${escapeHtml(capReason("reconciliation"))}逐条处理需 P0-4 reconciliation 接口。</span>
          </div>
        </section>`;
    }
    const rows = (state.reconciliation.data && state.reconciliation.data.items) || [];
    if (!rows.length) {
      return `<section class="quota-view-card"><div class="quota-empty"><i data-lucide="check-circle"></i><strong>无待归档候选</strong><span>所有原件已处置。</span></div></section>`;
    }
    return `<section class="quota-view-card"><p class="quota-filter-note">待归档候选 ${rows.length} 条（逐条处理工作台）。</p></section>`;
  }

  // ── 档案详情视图（最小版:元数据回显 + 分册 + 来源 PDF + 当前状态）─────
  function formatFileSize(bytes) {
    if (bytes === null || bytes === undefined || isNaN(Number(bytes))) return "—";
    const n = Number(bytes);
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function renderArchiveDetailView() {
    const detail = state.archiveDetail || {};
    if (detail.status === CAP.UNKNOWN || (detail.status !== CAP.READY && !detail.data)) {
      return `<section class="quota-view-card"><div class="quota-empty"><i data-lucide="loader"></i><strong>加载档案详情…</strong></div></section>`;
    }
    if (detail.status === CAP.ERROR || (detail.status === CAP.UNAVAILABLE && !detail.data)) {
      const msg = detail.error || capReason("archives");
      return `<section class="quota-view-card">
        <button class="secondary-button" type="button" data-quota-action="back-to-archives"><i data-lucide="arrow-left"></i><span>返回档案列表</span></button>
        <div class="quota-empty"><i data-lucide="alert-circle"></i><strong>档案详情加载失败</strong><span>${escapeHtml(msg)}</span></div>
      </section>`;
    }

    const d = detail.data || {};
    const archive = d.archive || {};
    const pubset = d.publication_set || {};
    const volumes = d.volumes || [];
    const files = d.files || [];

    const cell = (x) => (x === null || x === undefined || x === "") ? "—" : escapeHtml(String(x));

    const meta = [
      { label: "省份/管辖", value: archive.jurisdiction_code || pubset.jurisdiction_code },
      { label: "定额体系", value: pubset.quota_system_type },
      { label: "资料类型", value: pubset.material_type },
      { label: "版本年", value: pubset.edition_year },
      { label: "版次", value: pubset.edition_label },
      { label: "专业", value: archive.discipline_code },
      { label: "文档角色", value: archive.document_role },
      { label: "元数据状态", value: archive.metadata_status },
      { label: "发布单位", value: pubset.issuer_name },
      { label: "标准/定额编号", value: pubset.standard_or_quota_code },
      { label: "法律状态", value: pubset.legal_status },
      { label: "业务键", value: archive.business_key, mono: true },
    ];
    const metaRows = meta.map((m) => {
      const monoAttr = m.mono ? ' class="quota-mono"' : "";
      return `<dt>${escapeHtml(m.label)}</dt><dd${monoAttr}>${cell(m.value)}</dd>`;
    }).join("");

    let volumesHtml;
    if (!volumes.length) {
      volumesHtml = `<div class="quota-empty"><i data-lucide="book"></i><strong>暂无分册记录</strong></div>`;
    } else {
      const vRows = volumes.map((v) => {
        const currentMark = v.is_current ? '<span class="quota-status-pill">当前</span>' : "";
        const volLabel = [v.volume_code, v.volume_title].filter(Boolean).map((s) => escapeHtml(String(s))).join(" · ");
        const action = "open-archive:" + encodeURIComponent(v.archive_id);
        const clickable = v.is_current ? "" : `data-quota-action="${escapeHtml(action)}" style="cursor:pointer" title="切换到该分册"`;
        return `<tr ${clickable}>
          <td>${cell(volLabel || v.title)}</td>
          <td>${cell(v.discipline_code)}</td>
          <td>${cell(v.document_role)}</td>
          <td>${cell(v.file_count)}</td>
          <td><span class="quota-status-pill">${cell(v.status)}</span> ${currentMark}</td>
        </tr>`;
      }).join("");
      volumesHtml = `<table class="archive-table quota-table">
        <thead><tr><th>分册</th><th>专业</th><th>文档角色</th><th>文件数</th><th>状态</th></tr></thead>
        <tbody>${vRows}</tbody>
      </table>`;
    }

    let filesHtml;
    if (!files.length) {
      filesHtml = `<div class="quota-empty"><i data-lucide="file-x"></i><strong>暂无来源 PDF</strong><span>文件上传能力（POST /archives/{id}/files）尚未实现。</span></div>`;
    } else {
      const fRows = files.map((f) => {
        const primaryMark = f.is_primary ? '<span class="quota-status-pill">主文件</span>' : "";
        const roleBadge = f.file_role ? `<span class="quota-file-role-badge">${cell(f.file_role)}</span>` : "";
        const name = f.display_name || f.file_name || "—";
        return `<tr>
          <td>${cell(name)}</td>
          <td>${roleBadge}</td>
          <td>${cell(formatFileSize(f.size_bytes))}</td>
          <td>${cell(f.mime_type)}</td>
          <td>${cell(f.fetch_status)} ${primaryMark}</td>
        </tr>`;
      }).join("");
      filesHtml = `<table class="archive-table quota-table">
        <thead><tr><th>文件名</th><th>角色</th><th>大小</th><th>类型</th><th>状态</th></tr></thead>
        <tbody>${fRows}</tbody>
      </table>`;
    }

    const statusText = archive.status || "—";
    const stateMachineNote = "状态机: uploaded → registered → parsing → parsed → qa_passed → usable（完整推进 UI 待解析管线就绪）";

    return `<section class="quota-view-card quota-archive-detail">
      <button class="secondary-button" type="button" data-quota-action="back-to-archives"><i data-lucide="arrow-left"></i><span>返回档案列表</span></button>
      <header class="quota-archive-detail-header">
        <div>
          <p class="eyebrow">档案详情</p>
          <h2>${escapeHtml(archive.title || "（未命名档案）")}</h2>
          <p class="quota-archive-meta-line">
            <span class="quota-status-pill">${escapeHtml(statusText)}</span>
            <span class="quota-filter-note">文件数 ${escapeHtml(String(archive.file_count || 0))} · 分册 ${volumes.length} · 来源 PDF ${files.length}</span>
          </p>
        </div>
      </header>
      <section class="quota-archive-detail-section">
        <h3>必填元数据</h3>
        <dl class="quota-meta-grid">${metaRows}</dl>
      </section>
      <section class="quota-archive-detail-section">
        <h3>分册（${volumes.length}）</h3>
        ${volumesHtml}
      </section>
      <section class="quota-archive-detail-section">
        <h3>来源 PDF（${files.length}）</h3>
        ${filesHtml}
      </section>
      <section class="quota-archive-detail-section">
        <h3>状态机</h3>
        <p>当前状态: <strong>${escapeHtml(statusText)}</strong></p>
        <p class="quota-filter-note">${escapeHtml(stateMachineNote)}</p>
      </section>
    </section>`;
  }

  // ── 渲染：整个 shell ────────────────────────────────────────────────
  function render() {
    const shell = $("#quotaShell");
    if (!shell) return;
    // 预计算校验（用于 compose modal 内 field 级 inline 错误）
    try {
      if (state.compose && state.compose.open) {
        state._lastValidation = Compose.validateCompose(state.compose);
      } else {
        state._lastValidation = null;
      }
    } catch (e) {
      state._lastValidation = null;
      if (typeof console !== "undefined" && console.error) console.error("validateCompose error:", e);
    }
    shell.innerHTML = `
      ${renderHeader()}
      ${renderTabs()}
      ${state.view === "archives" ? renderFilters() : ""}
      ${renderBody()}
    `;
    renderComposeModal();
    renderUploadModal();
    refreshIcons();
  }

  // ── 补录弹窗 ─────────────────────────────────────────────────────────
  function renderComposeModal() {
    const modal = $("#quotaComposeModal");
    if (!modal) return;
    if (!state.compose || !state.compose.open) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      modal.innerHTML = "";
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    const c = state.compose;
    const meta = Compose.ACTION_META[c.action] || {};
    const actions = Compose.resolveComposeActions(c, { apiReady: state.flags.compose === true });
    const v = actions.validation;

    const headerTitle = c.action === Compose.ACTIONS.NEW_SET ? "新增定额" : (meta.label || "补录");
    const headerSub = c.action === Compose.ACTIONS.NEW_SET ? "" :
      `<p class="eyebrow">Quota Compose · ${escapeHtml(c.action)}</p>`;

    var bodyHtml = "";
    bodyHtml += state.composeWarning ? `<div class="quota-warning">${escapeHtml(state.composeWarning)}</div>` : "";
    bodyHtml += renderComposeTarget(c);
    bodyHtml += renderDropZone(c);
    bodyHtml += renderComposeSetForm(c);
    bodyHtml += renderComposeVolumes(c);
    bodyHtml += renderUnassignedFiles(c);
    bodyHtml += renderComposeRelation(c);
    bodyHtml += renderComposeSupplement(c);
    bodyHtml += renderAdvancedInfo(c);

    // 校验汇总（置于 footer 上方）
    var footerHint = renderComposeFooterHint(v);

    modal.innerHTML = `
      <form class="quota-compose-dialog" data-quota-form="compose">
        <header class="manual-upload-header">
          <div>
            ${headerSub}
            <h2>${escapeHtml(headerTitle)}</h2>
          </div>
          <button class="icon-button" type="button" title="关闭" data-quota-action="close-compose"><i data-lucide="x"></i></button>
        </header>
        <div class="quota-compose-body">${bodyHtml}</div>
        ${footerHint ? `<div class="quota-compose-validation">${footerHint}</div>` : ""}
        <footer class="quota-compose-footer">
          <button class="secondary-button" type="button" data-quota-action="close-compose">取消</button>
          <button class="secondary-button" type="button" data-quota-action="save-draft">${escapeHtml(actions.saveDraftLabel)}</button>
          <button class="primary-button" type="button" data-quota-action="submit"
            ${actions.canSubmit && !c.submitting ? "" : "disabled"}
            ${actions.submitDisabledReason && !c.submitting ? `title="${escapeHtml(actions.submitDisabledReason)}"` : ""}>
            ${c.submitting ? '<i class="spinner"></i><span>提交中...</span>' : '<i data-lucide="check"></i><span>保存并提交核验</span>'}
          </button>
        </footer>
      </form>
    `;
    refreshIcons();
  }

  function renderComposeTarget(c) {
    if (!c.targetKind) return "";
    var label = c.targetKind === "archives" ? "目标档案" : "目标资料体系";
    return `
      <fieldset class="quota-compose-section">
        <legend>${escapeHtml(label)}</legend>
        <div class="quota-target-placeholder">
          <i data-lucide="link-2-off"></i>
          <span>待接入 API，暂无法选择目标（不提供静态选项）。</span>
        </div>
      </fieldset>`;
  }

  function optionList(list, selected) {
    return list
      .map(function (o) { return `<option value="${escapeHtml(o.value)}" ${o.value === selected ? "selected" : ""}>${escapeHtml(o.label)}</option>`; })
      .join("");
  }

  function inlineError(fieldErrors, key) {
    if (!fieldErrors || !fieldErrors[key]) return "";
    return `<span class="quota-inline-error">${escapeHtml(fieldErrors[key])}</span>`;
  }

  // ── 拖拽上传区（新建定额体系专用）─────────────────────────────────────
  function renderDropZone(c) {
    if (c.action !== Compose.ACTIONS.NEW_SET) return "";
    var volCount = (c.volumes || []).length;
    var unCount = (c.unassignedFiles || []).length;
    var total = volCount + unCount;
    var label = total ? "已选择 " + total + " 个文件（" + volCount + " 个分册 + " + unCount + " 个待归属）"
      : "拖入或选择多个 PDF（支持 .pdf，单次最多 50 个）";
    return `
      <div class="quota-dropzone">
        <label class="quota-dropzone-label" data-quota-action="dropzone-click">
          <input type="file" multiple accept=".pdf" data-qfile-input="1" data-vol="__dropzone__" />
          <span><i data-lucide="upload-cloud"></i> ${escapeHtml(label)}</span>
        </label>
      </div>`;
  }

  // ── 简化体系表单（新建定额体系：仅 4 字段）────────────────────────────
  function renderComposeSetForm(c) {
    if (c.action === Compose.ACTIONS.NEW_SET) return renderNewSetForm(c);
    if (c.action === Compose.ACTIONS.NEW_BOQ) return renderNewBoqForm(c);
    return "";
  }

  function renderNewSetForm(c) {
    var isIndustry = c.systemType === "industry_specialty";
    var isRegional = c.systemType === "construction_regional";
    var v = (state._lastValidation && state._lastValidation.fields) || {};

    var jurisLabel = isRegional ? "适用地区" : isIndustry ? "行业" : "适用地区/行业";
    var jurisInput = "";
    if (isRegional) {
      jurisInput = '<select class="quota-field-input" data-qfield="path.jurisdiction_code" ' +
        'data-qlabel="path.jurisdiction_label">' +
        renderJurisdictionOptions(c.path.jurisdiction_code) +
      '</select>';
    } else if (isIndustry) {
      jurisInput = `<input type="text" class="quota-field-input" data-qfield="path.industry_sector_label"
        value="${escapeHtml(c.path.industry_sector_label || c.path.industry_sector_code)}"
        placeholder="如 电网工程（字典接入后改为下拉）" />`;
    } else {
      jurisInput = `<input type="text" class="quota-field-input" disabled placeholder="请先选择定额体系" />`;
    }

    var titleDisplay = c.set.title || "";
    if (!titleDisplay && !c.set.titleUserEdited) {
      titleDisplay = Compose.autoGenerateTitle(c);
    }

    return `
      <fieldset class="quota-compose-section quota-set-form-simple">
        <div class="quota-simple-form">
          <div class="quota-simple-row">
            <div class="quota-simple-field ${v.systemType ? "has-error" : ""}">
              <label>定额体系</label>
              <select data-qfield="systemType" class="quota-field-input">
                <option value="">请选择</option>
                ${optionList(Compose.SYSTEM_TYPES, c.systemType)}
              </select>
              ${inlineError(v, "systemType")}
            </div>
            <div class="quota-simple-field ${v.jurisdiction || v.industry ? "has-error" : ""}">
              <label>${escapeHtml(jurisLabel)}</label>
              ${jurisInput}
              ${inlineError(v, "jurisdiction")}
              ${inlineError(v, "industry")}
            </div>
          </div>
          <div class="quota-simple-row">
            <div class="quota-simple-field ${v.edition_year ? "has-error" : ""}">
              <label>年份</label>
              <input type="text" class="quota-field-input" data-qfield="set.edition_year"
                value="${escapeHtml(c.set.edition_year)}" placeholder="如 2025"
                inputmode="numeric" maxlength="4" />
              ${inlineError(v, "edition_year")}
            </div>
            <div class="quota-simple-field">
              <label>体系名称 <small>自动生成，可修改</small></label>
              <input type="text" class="quota-field-input" data-qfield="set.title"
                value="${escapeHtml(titleDisplay)}" placeholder="如 四川省2025建设工程计价定额" />
            </div>
          </div>
        </div>
      </fieldset>`;
  }

  function renderNewBoqForm(c) {
    var v = (state._lastValidation && state._lastValidation.fields) || {};
    return `
      <fieldset class="quota-compose-section quota-set-form-simple">
        <div class="quota-simple-form">
          <div class="quota-simple-row">
            <div class="quota-simple-field ${v.title ? "has-error" : ""}">
              <label>适用范围</label>
              <input type="text" class="quota-field-input" data-qfield="set.title"
                value="${escapeHtml(c.set.title)}" placeholder="如 建设工程工程量清单计价规范" />
              ${inlineError(v, "title")}
            </div>
            <div class="quota-simple-field ${v.edition_year ? "has-error" : ""}">
              <label>年份 / 版本</label>
              <input type="text" class="quota-field-input" data-qfield="set.edition_year"
                value="${escapeHtml(c.set.edition_year)}" placeholder="如 2025" />
              ${inlineError(v, "edition_year")}
            </div>
          </div>
        </div>
      </fieldset>`;
  }

  // ── 分册列表（简化：每分册一行，角色下拉）─────────────────────────────
  function renderComposeVolumes(c) {
    var usesVolumes = c.action === Compose.ACTIONS.NEW_BOQ || c.action === Compose.ACTIONS.NEW_SET || c.action === Compose.ACTIONS.ADD_VOLUME;
    if (!usesVolumes) return "";
    var vols = c.volumes || [];
    if (!vols.length && c.action === Compose.ACTIONS.NEW_SET) return "";
    var v = (state._lastValidation && state._lastValidation.warnings) || [];
    var label = vols.length ? "已识别 " + vols.length + " 个分册" : "分册";
    return `
      <fieldset class="quota-compose-section">
        <legend>${escapeHtml(label)}</legend>
        <div class="quota-volume-list-simple">
          ${vols.map(function (vol) {
            var volWarns = v.filter(function (w) { return w.indexOf(vol.volume_title || "分册") === 0; });
            var mains = Compose.volumeMainCount(vol);
            var roleClass = mains === 0 ? "role-warn" : mains > 1 ? "role-warn" : "";
            return `
              <div class="quota-volume-row" data-vol="${vol.tempId}">
                <div class="quota-volume-row-main">
                  <div class="quota-volume-name">
                    <input type="text" class="quota-field-input" data-qvol="volume_title" data-vol="${vol.tempId}"
                      value="${escapeHtml(vol.volume_title)}" placeholder="分册名称" />
                  </div>
                  <div class="quota-volume-files">
                    ${(vol.files || []).map(function (f) {
                      return renderFileRow(vol.tempId, f);
                    }).join("")}
                  </div>
                </div>
                ${vols.length > 1 ? `<button class="icon-button danger-action" type="button" title="删除分册" data-quota-action="remove-volume" data-vol="${vol.tempId}"><i data-lucide="trash-2"></i></button>` : ""}
                ${volWarns.length ? `<span class="quota-volume-warn-inline">${escapeHtml(volWarns.join("; "))}</span>` : ""}
              </div>`;
          }).join("")}
        </div>
        <button class="secondary-button" type="button" data-quota-action="add-volume"><i data-lucide="plus"></i><span>手动新增分册</span></button>
      </fieldset>`;
  }

  // ── 待归属文件池（封面/目录/附录/公告）────────────────────────────────
  function renderUnassignedFiles(c) {
    if (c.action !== Compose.ACTIONS.NEW_SET) return "";
    var files = c.unassignedFiles || [];
    if (!files.length) return "";
    return `
      <fieldset class="quota-compose-section quota-unassigned">
        <legend>待归属文件（${files.length} 个 — 请分配到对应分册）</legend>
        <div class="quota-unassigned-list">
          ${files.map(function (f) {
            return `
              <div class="quota-unassigned-row">
                ${renderFileRow("__unassigned__", f)}
                <select class="quota-assign-select" data-quota-action="assign-file" data-file="${f.tempId}">
                  <option value="">分配到分册…</option>
                  ${(c.volumes || []).map(function (vol) {
                    return `<option value="${vol.tempId}">${escapeHtml(vol.volume_title || "分册")}</option>`;
                  }).join("")}
                </select>
              </div>`;
          }).join("")}
        </div>
      </fieldset>`;
  }

  function renderFileRow(volTemp, f) {
    var missing = f.missingContent
      ? `<span class="quota-file-missing" title="${escapeHtml(Compose.DRAFT_FILE_WARNING)}">需重新选择</span>`
      : "";
    var roleLabel = (Compose.FILE_ROLES.find(function (r) { return r.value === f.role; }) || {}).label || f.role;
    return `
      <div class="quota-file-row" data-file="${f.tempId}">
        <span class="quota-file-name">${escapeHtml(f.name || "(未选择)")}${missing}</span>
        <span class="quota-file-role-badge" title="文件角色">${escapeHtml(roleLabel)}</span>
        <button class="icon-button danger-action" type="button" title="移除" data-quota-action="remove-file" data-vol="${volTemp}" data-file="${f.tempId}"><i data-lucide="x"></i></button>
      </div>`;
  }

  // ── 高级信息（折叠，默认不展开）────────────────────────────────────────
  function renderAdvancedInfo(c) {
    if (c.action !== Compose.ACTIONS.NEW_SET) return "";
    var open = state.composeAdvancedOpen;
    var legal = Compose.computeLegalStatus(c.set);
    return `
      <details class="quota-advanced-info" ${open ? "open" : ""}>
        <summary data-quota-action="toggle-compose-advanced">
          <span>高级信息（资料性质 / 发布单位 / 日期 / 编号）</span>
          <i data-lucide="${open ? "chevron-up" : "chevron-down"}"></i>
        </summary>
        <div class="quota-advanced-body">
          <div class="quota-form-grid">
            <label>资料性质
              <select data-qfield="set.material_type">${optionList(Compose.MATERIAL_TYPES, c.set.material_type)}</select>
            </label>
            <label>版次
              <input type="text" data-qfield="set.edition_label" value="${escapeHtml(c.set.edition_label)}" placeholder="如 2025版" />
            </label>
            <label>发布单位
              <input type="text" data-qfield="set.issuer_name" value="${escapeHtml(c.set.issuer_name)}" placeholder="如 四川省住建厅" />
            </label>
            <label>发布日期
              <input type="date" data-qfield="set.publish_date" value="${escapeHtml(c.set.publish_date)}" />
            </label>
            <label>实施日期
              <input type="date" data-qfield="set.effective_date" value="${escapeHtml(c.set.effective_date)}" />
            </label>
            <label>标准/定额编号
              <input type="text" data-qfield="set.standard_or_quota_code" value="${escapeHtml(c.set.standard_or_quota_code)}" />
            </label>
            <label>法律状态 <small>（自动计算）</small>
              <span class="quota-legal-status">${escapeHtml(legal === "effective" ? "现行有效" : legal === "pending" ? "待生效" : "未知")}</span>
            </label>
          </div>
        </div>
      </details>`;
  }

  function renderComposeRelation(c) {
    var buildsSet = c.action === Compose.ACTIONS.NEW_BOQ || c.action === Compose.ACTIONS.NEW_SET;
    if (!buildsSet || !Compose.requiresRelation(c.set.material_type || c.material_type)) return "";
    return `
      <fieldset class="quota-compose-section quota-relation">
        <legend>关联主体系</legend>
        <div class="quota-form-grid">
          <label>关联体系
            <input type="text" data-qfield="relation.related_publication_set_id" value="${escapeHtml(c.relation.related_publication_set_id)}" placeholder="待接入体系查询后可选择" />
          </label>
          <label>关系类型
            <select data-qfield="relation.relation_type"><option value="">请选择</option>${optionList(Compose.RELATION_TYPES, c.relation.relation_type)}</select>
          </label>
        </div>
      </fieldset>`;
  }

  function renderComposeSupplement(c) {
    if (c.action !== "supplement") return "";
    return `
      <fieldset class="quota-compose-section">
        <legend>补充文件</legend>
        <div class="quota-file-section">
          ${(c.supplementFiles || []).map(function (f) { return renderFileRow("__supplement__", f); }).join("")}
          <label class="quota-file-add">
            <input type="file" multiple data-qfile-input="1" data-vol="__supplement__" />
            <span><i data-lucide="upload-cloud"></i> 添加文件</span>
          </label>
        </div>
      </fieldset>`;
  }

  function _scrollToFirstError() {
    var modal = $("#quotaComposeModal");
    if (!modal) return;
    var firstErr = modal.querySelector(".has-error .quota-field-input, .quota-inline-error");
    if (firstErr) {
      firstErr.scrollIntoView({ behavior: "smooth", block: "center" });
      if (firstErr.focus) firstErr.focus();
    }
  }

  function renderComposeFooterHint(v) {
    if (!v) return "";
    if (v.ok) return "";
    var count = v.blockingCount || 0;
    if (count === 0) return "";
    return `<div class="quota-footer-hint">还有 <strong>${count}</strong> 项必填信息未完成</div>`;
  }

  // ── 列表数据源：通用 /api/archives?domain_type=quota（裸数组，含 primary_file）──
  // 不走 state.api.getArchives()（那是 /api/data-lake/quota/archives，响应是 {items:[...]}
  // 且无 primary_file 字段——不适合预览）。
  //
  // 2026-07-29 修复 chip 筛选：原本硬编码 /api/archives?domain_type=quota&limit=500 不带任何
  // 筛选条件 → 选了 primary="专业工程定额" / jurisdiction="云南" 也不影响列表。后端现在
  // 已扩展接受 6 个 quota 域参数（primary / jurisdiction_code / industry_sector_code /
  // edition_year / edition_label / discipline_code），前端这里把 currentArchiveFilters()
  // 的产出作为 query string 拼上去。
  //
  // 2026-07-29 v0.3.3 Plan C：加严格输入校验器 `_sanitizeArchiveFilters`。
  // 根因：当前端任何 chip 状态漏过 currentArchiveFilters()（例如版本遗留路径），
  // 字符串 "all" 会作为 edition_year 值发到后端，触发 FastAPI 422 int_parsing。
  // 校验器统一拦截：白名单 keys + 类型强转 + "all"/空值丢弃。
  // 这层防御独立于 currentArchiveFilters()，无论上游怎么传都安全。
  const _ARCHIVE_FILTER_SCHEMA = Object.freeze({
    search: { kind: "string" },
    primary: { kind: "string" },
    jurisdiction_code: { kind: "string" },
    industry_sector_code: { kind: "string" },
    edition_year: { kind: "int" },        // 后端 int | None，字符串 → 422
    edition_label: { kind: "string" },
    discipline_code: { kind: "string" },
  });
  function _sanitizeArchiveFilters(filters) {
    const out = {};
    if (!filters || typeof filters !== "object") return out;
    Object.keys(filters).forEach((k) => {
      const spec = _ARCHIVE_FILTER_SCHEMA[k];
      if (!spec) return;  // 未知 key 直接丢弃（防御 typo / 调试残留字段）
      let v = filters[k];
      // 哨兵 + 空值一律丢弃（"all" / undefined / null / 空串）
      if (v === undefined || v === null) return;
      if (typeof v === "string") {
        const trimmed = v.trim();
        if (trimmed === "" || trimmed === "all") return;
        v = trimmed;
      }
      if (spec.kind === "int") {
        // 必须是数字（含 "2026" 字符串）。非数字 → 丢，绝不让 422 出门
        const n = typeof v === "number" ? v : Number(v);
        if (!Number.isFinite(n) || !Number.isInteger(n)) return;
        out[k] = n;
      } else {
        out[k] = String(v);
      }
    });
    return out;
  }

  async function loadQuotaArchivesGeneric(filters) {
    if (typeof global.fetch !== "function") {
      return { status: CAP.ERROR, data: [], error: "fetch 不可用" };
    }
    const safe = _sanitizeArchiveFilters(filters);
    const params = new URLSearchParams();
    params.set("domain_type", "quota");
    Object.keys(safe).forEach((k) => {
      params.set(k, String(safe[k]));
    });
    params.set("limit", "500");
    try {
      const response = await global.fetch("/api/archives?" + params.toString(), {
        headers: { Accept: "application/json" },
      });
      if (response.status === 401 || response.status === 403) {
        return { status: CAP.UNAUTHORIZED, data: [], error: Api.reasonText(CAP.UNAUTHORIZED) };
      }
      if (response.status === 404) {
        return { status: CAP.UNAVAILABLE, data: [], error: Api.reasonText(CAP.UNAVAILABLE) };
      }
      if (!response.ok) {
        return { status: CAP.ERROR, data: [], error: "HTTP " + response.status };
      }
      // 响应是裸数组 list[ArchiveSummaryResponse]，不是 {items:[...]}
      const data = await response.json();
      const items = Array.isArray(data) ? data : ((data && data.items) || []);
      return { status: CAP.READY, data: items, error: "" };
    } catch (_e) {
      return { status: CAP.ERROR, data: [], error: "网络错误，无法连接档案接口。" };
    }
  }

  // ── 数据加载（能力探测 + 各能力就绪时拉取真实数据）──────────────────
  async function load() {
    if (!state.api) return;
    state.capabilities = await state.api.probeCapabilities();
    state.flags = Api.resolveFlags({
      capabilities: state.capabilities,
      env: state.env,
      search: typeof global.location !== "undefined" ? global.location.search : "",
    });
    render();
    const tasks = [];
    if (state.flags.stats) {
      tasks.push(
        state.api.getStats().then((r) => {
          state.stats = { status: r.status, data: r.data };
        })
      );
    }
    if (state.flags.facets) {
      tasks.push(
        state.api.getFacets(_facetsParams()).then((r) => {
          state.facets = { status: r.status, data: r.data };
        })
      );
    }
    if (state.flags.reconciliation) {
      tasks.push(
        state.api.getReconciliation().then((r) => {
          state.reconciliation = { status: r.status, data: r.data };
        })
      );
    }
    if (state.flags.archives) {
      // 切换到通用 /api/archives?domain_type=quota：响应是裸数组 list[ArchiveSummaryResponse]，
      // 含 primary_file{file_id,file_name,file_role}（预览所需），旧 /api/data-lake/quota/archives 无此字段。
      // 2026-07-29 修复：把 URL 上挂上当前 chip 筛选，与 reloadArchives 一致。
      tasks.push(
        loadQuotaArchivesGeneric(currentArchiveFilters()).then((result) => {
          state.archives = result;
        })
      );
    }
    await Promise.all(tasks);
    render();
  }

  // ── 列表筛选参数构造 + 仅列表的轻量重载 ───────────────────────────────
  function currentArchiveFilters() {
    const f = state.filters || {};
    // 2026-07-29 修复：键名对齐后端 /api/archives 接受的参数。
    // 之前 q 是孤儿字段（后端用 search），chip 切换从不生效。现在传 search。
    const params = {
      search: f.q || undefined,
      primary: f.primary && f.primary !== "all" ? f.primary : undefined,
      edition_year: f.editionYear && f.editionYear !== "all" ? f.editionYear : undefined,
      edition_label: f.edition && f.edition !== "all" ? f.edition : undefined,
      discipline_code: f.discipline && f.discipline !== "all" ? f.discipline : undefined,
    };
    if (f.secondary && f.secondary !== "all") {
      if (f.primary === "construction_regional") params.jurisdiction_code = f.secondary;
      else if (f.primary === "industry_specialty") params.industry_sector_code = f.secondary;
    }
    // 去掉所有 undefined，避免 URL 里出现 ?key=
    Object.keys(params).forEach((k) => {
      if (params[k] === undefined) delete params[k];
    });
    return params;
  }

  function reloadArchives() {
    if (!state.api || !state.flags.archives) return;
    state.archives = { status: CAP.UNKNOWN, data: null, error: "" };
    render();
    // 2026-07-29：补上传 chip 当前筛选条件，跨端点 query string 一致；
    // 后端 /api/archives 已支持 primary / jurisdiction_code / edition_year /
    // edition_label / discipline_code / industry_sector_code 6 个 quota 域参数。
    loadQuotaArchivesGeneric(currentArchiveFilters()).then((result) => {
      state.archives = result;
      render();
    });
  }

  // ── 交互 ─────────────────────────────────────────────────────────────
  function draftCtx() {
    return { tenant: "platform_public", user: (global.__quotaUser && global.__quotaUser.id) || "anonymous" };
  }

  function openCompose(action) {
    state.addMenuOpen = false;
    state.composeWarning = "";
    const ctx = draftCtx();
    state.compose = Compose.createComposeState(action, ctx);
    // 尝试恢复已存在草稿
    const existing = Compose.loadDraft(state.compose, global.localStorage);
    if (existing.ok) {
      state.compose = existing.state;
      state.compose.open = true;
      state.composeWarning = existing.warning || "";
    }
    render();
  }

  function closeCompose() {
    state.compose = null;
    state.composeWarning = "";
    render();
  }

  // ── 极简上传弹窗 ─────────────────────────────────────────────────────
  function openUploadDialog() {
    state.addMenuOpen = false;
    state.upload = { open: true, files: [], category: "", province: "", year: "", submitting: false };
    render();
  }

  function closeUploadDialog() {
    state.upload.open = false;
    state.upload.files = [];
    state.upload.submitting = false;
    render();
  }

  // 轻量：只刷新文件列表（不重建表单，避免 input 失焦）
  function renderUploadFileList() {
    var wrap = document.querySelector("[data-upload-filelist='1']");
    if (!wrap) return;
    var u = state.upload || { files: [] };
    wrap.innerHTML = (u.files || []).map(function (f, idx) {
      return '<div class="manual-upload-fileitem">' +
        '<span class="manual-upload-filename">' + escapeHtml(f.name) + '</span>' +
        '<button class="icon-button danger-action" type="button" title="移除" data-quota-action="remove-upload-file" data-upload-idx="' + idx + '">' +
        '<i data-lucide="x"></i></button>' +
        '</div>';
    }).join("");
    refreshIcons();
  }

  // 轻量：只刷新提交按钮 disabled（不重建表单，避免 input 失焦）
  // 注意: 2026-07-29 我们去掉了 disabled 属性,
  // 因为 disabled 会吞掉 click 事件, 用户点了没反应又看不到原因。
  // 现在 submit 按钮永远可点击, 缺字段由 submitUpload 内 setToast 告知。
  function renderUploadSubmitButton() {
    var btn = document.querySelector("[data-upload-submit='1']");
    if (!btn) return;
    var u = state.upload || {};
    var yearNum = parseInt(u.year, 10);
    var yearValid = u.year && !isNaN(yearNum) && yearNum >= 1900 && yearNum <= 2100;
    var canSubmit = (u.files || []).length > 0 && u.category && u.province && yearValid && !u.submitting;
    // 仅做视觉提示（按钮变色/边框），不动 disabled 属性
    if (canSubmit) btn.classList && btn.classList.remove("is-disabled-soft");
    else btn.classList && btn.classList.add("is-disabled-soft");
  }

  function renderUploadModal() {
    const modal = $("#quotaUploadModal");
    if (!modal) return;
    if (!state.upload.open) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      modal.innerHTML = "";
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");

    const u = state.upload;
    // 校验：files + category + province + year 都必填；year 范围 1900-2100
    var yearNum = parseInt(u.year, 10);
    var yearValid = u.year && !isNaN(yearNum) && yearNum >= 1900 && yearNum <= 2100;
    var canSubmit = (u.files || []).length > 0 && u.category && u.province && yearValid && !u.submitting;

    const categoryOptions = [
      { value: "", label: "请选择资料分类" },
      { value: "construction_quota", label: "建筑工程定额" },
      { value: "industry_quota", label: "专业工程定额" },
      { value: "boq_standard", label: "清单规范" },
    ];

    const fileListHtml = (u.files || []).map(function (f, idx) {
      return '<div class="manual-upload-fileitem">' +
        '<span class="manual-upload-filename">' + escapeHtml(f.name) + '</span>' +
        '<button class="icon-button danger-action" type="button" title="移除" data-quota-action="remove-upload-file" data-upload-idx="' + idx + '">' +
        '<i data-lucide="x"></i></button>' +
        '</div>';
    }).join("");

    modal.innerHTML = `
      <form class="manual-upload-dialog quota-compose-dialog" data-quota-form="upload">
        <header class="manual-upload-header">
          <div>
            <p class="eyebrow">Quota Upload · 极简上传</p>
            <h2>新增定额档案</h2>
          </div>
          <button class="icon-button" type="button" title="关闭" data-quota-action="close-upload"><i data-lucide="x"></i></button>
        </header>
        <div class="quota-compose-body">
          <label class="file-picker" id="quotaUploadDropzone">
            <input id="quotaUploadFile" type="file" multiple accept=".pdf" data-qufile-input="upload" />
            <span>
              <strong>选择 PDF 文件</strong>
              <small>可多选 .pdf，单次最多 50 个</small>
            </span>
          </label>
          <div class="manual-upload-filelist" data-upload-filelist="1">${fileListHtml}</div>
          <div class="quota-simple-form">
            <div class="quota-simple-row">
              <div class="quota-simple-field">
                <label>省份 <span class="quota-required-marker" aria-label="必填">*</span></label>
                <select class="quota-field-input" data-qfield="upload.province">
                  ${renderUploadProvinceOptions(u.province)}
                </select>
              </div>
              <div class="quota-simple-field">
                <label>年份 <span class="quota-required-marker" aria-label="必填">*</span></label>
                <input type="number" class="quota-field-input" data-qfield="upload.year"
                  value="${escapeHtml(u.year || "")}"
                  placeholder="如 2026" min="1900" max="2100" step="1" />
              </div>
            </div>
            <div class="quota-simple-row">
              <div class="quota-simple-field">
                <label>资料分类 <span class="quota-required-marker" aria-label="必填">*</span></label>
                <select class="quota-field-input" data-qfield="upload.category">
                  ${categoryOptions.map(function (o) {
                    return '<option value="' + escapeHtml(o.value) + '"' + (o.value === u.category ? ' selected' : '') + '>' + escapeHtml(o.label) + '</option>';
                  }).join("")}
                </select>
              </div>
            </div>
          </div>
        </div>
        <footer class="quota-compose-footer">
          <button class="secondary-button" type="button" data-quota-action="close-upload">取消</button>
          <button class="primary-button" type="button" data-quota-action="submit-upload"
            data-upload-submit="1"
            ${canSubmit ? "" : 'aria-disabled="true"'}
            ${u.submitting ? '<i class="spinner"></i>' : ""}>
            ${u.submitting ? '<span>上传中...</span>' : '<i data-lucide="upload-cloud"></i><span>上传并保存</span>'}
          </button>
        </footer>
      </form>
    `;
    refreshIcons();
  }

  async function submitUpload() {
    const u = state.upload;
    // 2026-07-29 v2: 之前仅看 state.upload.files —— 但我们已经发现 state
    // 可能与 DOM 不同步（input 有 files，state 是空）。现在以 DOM <input type="file">
    // 为准 + state 作 fallback, 同时把多个缺失字段合并成一条 toast。
    if (!u) { setToast("上传状态丢失，请重新打开弹窗"); return; }
    if (u.submitting) { setToast("正在上传中，请稍候"); return; }

    // 真相源：DOM 中当前的 file input
    var liveFiles = [];
    var fileInput = document.getElementById("quotaUploadFile");
    if (fileInput && fileInput.files && fileInput.files.length > 0) {
      liveFiles = Array.from(fileInput.files);
    }
    // 回退：如果 user 已经选了文件被 close/reopen 流程吃掉，至少 state 还能补救
    var effectiveFiles = liveFiles.length > 0 ? liveFiles : (u.files || []);
    // 同步回 state（保守起见）
    u.files = effectiveFiles;

    var yearNum = parseInt(u.year, 10);
    var yearValid = u.year && !isNaN(yearNum) && yearNum >= 1900 && yearNum <= 2100;

    // 一次性收集所有缺失字段
    var missing = [];
    if (effectiveFiles.length === 0) missing.push("文件");
    if (!u.category) missing.push("分类");
    if (!u.province) missing.push("省份");
    if (!yearValid) missing.push("合法年份(1900-2100)");
    if (typeof global.fetch !== "function") missing.push("fetch 不可用");
    if (missing.length > 0) {
      setToast("请补齐：" + missing.join("、"));
      return;
    }

    u.submitting = true;
    renderUploadModal(); // 立刻显示「上传中...」状态

    const formData = new FormData();
    effectiveFiles.forEach(function (f) { formData.append("files", f, f.name); });
    formData.append("category", u.category);
    formData.append("province", u.province);
    formData.append("year", String(yearNum));

    try {
      const response = await global.fetch("/api/data-lake/quota/upload", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        setToast("上传失败：" + ((data && data.detail) || "HTTP " + response.status));
        return;
      }
      const succeeded = data.succeeded || 0;
      const failed = data.failed || 0;
      const duplicates = (data.items || []).filter(function (it) { return it.status === "duplicate"; }).length;
      const created = succeeded - duplicates;
      const parts = [];
      if (created > 0) parts.push("已创建 " + created + " 份档案");
      if (duplicates > 0) parts.push(duplicates + " 份重复跳过");
      if (failed > 0) parts.push(failed + " 份失败");
      setToast(parts.join("，") || "未处理任何文件");
      if (failed === 0) {
        closeUploadDialog(); // 内部已 reset submitting + render
        load();
      }
    } catch (err) {
      setToast("上传失败：" + (err.message || "网络错误"));
    } finally {
      // 确保按钮永远解禁（除非弹窗已被 closeUploadDialog 关闭）
      if (u.open) {
        u.submitting = false;
        renderUploadModal();
      }
    }
  }

  function findVolume(volTemp) {
    return (state.compose.volumes || []).find((v) => v.tempId === volTemp);
  }

  function addFiles(volTemp, fileList) {
    if (!state.compose) return;
    // 拖拽区（新建定额体系）：正文→创建分册，非正文→待归属
    if (volTemp === "__dropzone__") {
      var result = Compose.processDroppedFiles(state.compose, fileList);
      if (result.volumes.length) {
        state.compose.volumes = (state.compose.volumes || []).concat(result.volumes);
      }
      if (result.unassigned.length) {
        state.compose.unassignedFiles = (state.compose.unassignedFiles || []).concat(result.unassigned);
      }
      render();
      return;
    }
    var entries = Array.from(fileList || []).map(function (file) {
      return Compose.newFileEntry({ file: file, name: file.name, size: file.size, type: file.type });
    });
    if (volTemp === "__supplement__") {
      entries.forEach(function (e) { e.role = "other"; });
      state.compose.supplementFiles = state.compose.supplementFiles.concat(entries);
    } else {
      var vol = findVolume(volTemp);
      if (vol) vol.files = vol.files.concat(entries);
    }
    render();
  }

  function removeFile(volTemp, fileTemp) {
    if (!state.compose) return;
    if (volTemp === "__supplement__") {
      state.compose.supplementFiles = state.compose.supplementFiles.filter(function (f) { return f.tempId !== fileTemp; });
    } else if (volTemp === "__unassigned__") {
      state.compose.unassignedFiles = (state.compose.unassignedFiles || []).filter(function (f) { return f.tempId !== fileTemp; });
    } else {
      const vol = findVolume(volTemp);
      if (vol) vol.files = vol.files.filter(function (f) { return f.tempId !== fileTemp; });
    }
    render();
  }

  function setToast(msg) {
    state.toast = msg;
    const el = $("#quotaToast");
    if (el) {
      el.textContent = msg;
      el.hidden = !msg;
      if (msg) {
        clearTimeout(setToast._t);
        setToast._t = setTimeout(() => {
          el.hidden = true;
        }, 2600);
      }
    }
  }

  // ── dev flag：仅 dev + 显式 URL flag 时走旧 compose 菜单；默认走上传弹窗 ──
  function shouldUseComposeMenu() {
    if (!(Api && Api.isDev(state.env))) return false;
    var search = (typeof global.location !== "undefined" && global.location.search) || "";
    return search.indexOf("quotaFlags=compose:on") !== -1;
  }

  function handleAction(action, el) {
    if (action === "add-menu-toggle") {
      if (shouldUseComposeMenu()) {
        state.addMenuOpen = !state.addMenuOpen;
      } else {
        openUploadDialog();
      }
      render();
      return;
    }
    if (action === "preview-archive") {
      var archiveId = el && el.dataset ? el.dataset.archiveId : "";
      if (archiveId) {
        document.dispatchEvent(new CustomEvent("quota:preview-archive", { detail: { archiveId: decodeURIComponent(archiveId) } }));
      }
      return;
    }
    if (action === "close-upload") {
      closeUploadDialog();
      return;
    }
    if (action === "submit-upload") {
      submitUpload();
      return;
    }
    if (action === "remove-upload-file") {
      var idx = el && el.dataset ? Number(el.dataset.uploadIdx) : -1;
      if (Array.isArray(state.upload.files) && idx >= 0 && idx < state.upload.files.length) {
        state.upload.files.splice(idx, 1);
        renderUploadFileList();
        renderUploadSubmitButton();
      }
      return;
    }
    if (action.indexOf("add:") === 0) {
      openCompose(action.slice(4));
      return;
    }
    if (action.indexOf("set-view:") === 0) {
      const view = action.slice(9);
      if (tabEnabled(view) || view === "archives") state.view = view;
      state.addMenuOpen = false;
      render();
      return;
    }
    if (action === "back-to-archives") {
      state.view = "archives";
      state.archiveDetail = { status: CAP.UNKNOWN, data: null, archiveId: null, error: "" };
      state.addMenuOpen = false;
      render();
      return;
    }
    if (action.indexOf("open-archive:") === 0) {
      const id = action.slice("open-archive:".length);
      if (!id) return;
      state.view = "archive-detail";
      state.archiveDetail = { status: CAP.UNKNOWN, data: null, archiveId: id, error: "" };
      state.addMenuOpen = false;
      render();
      if (state.api && state.api.getArchiveDetail) {
        state.api.getArchiveDetail(id).then((r) => {
          state.archiveDetail = {
            status: r.status,
            data: r.data,
            archiveId: id,
            error: r.error || "",
          };
          render();
        });
      }
      return;
    }
    if (action.indexOf("set-primary:") === 0) {
      var newPrimary = action.slice(12);
      if (newPrimary !== state.filters.primary) {
        state.filters.primary = newPrimary;
        // 切换一级：清空二级、年份、版次，以及失效的地市和分册专业
        state.filters.secondary = "all";
        state.filters.editionYear = "all";
        state.filters.edition = "all";
        state.filters.city = "all";
        state.filters.discipline = "all";
        // 一级切到建筑工程定额时，facets 二次元数据（jurisdictions/years）按 primary 重置
        if (state.flags.facets) {
          state.api.getFacets(_facetsParams()).then((r) => {
            state.facets = { status: r.status, data: r.data };
            render();
          });
        }
      }
      render();
      reloadArchives();
      return;
    }
    if (action.indexOf("set-secondary:") === 0) {
      state.filters.secondary = action.slice(14);
      // 建筑工程定额：切省份后重刷 facets，让 years 按省份过滤
      if (state.filters.primary === "construction_regional" && state.flags.facets) {
        state.api.getFacets(_facetsParams()).then((r) => {
          state.facets = { status: r.status, data: r.data };
          render();
        });
      }
      render();
      reloadArchives();
      return;
    }
    if (action.indexOf("set-editionYear:") === 0) {
      var newYear = action.slice(17);
      if (newYear !== state.filters.editionYear) {
        state.filters.editionYear = newYear;
        // 年份切换：清空版次
        state.filters.edition = "all";
      }
      render();
      reloadArchives();
      return;
    }
    if (action.indexOf("set-edition:") === 0) {
      state.filters.edition = action.slice(12);
      render();
      reloadArchives();
      return;
    }
    // 高级筛选维度（data-quota-action="set-adv_{key}:value" → set any filter field)
    // These are handled by setByPath pattern via data-quota-action="set-filter:{field}:{value}"
    // Reserved for future use when facets API returns data
    if (action.indexOf("toggle-secondary-expand") === 0) {
      state.filters.secondaryExpanded = !state.filters.secondaryExpanded;
      render();
      return;
    }
    if (action.indexOf("toggle-year-expand") === 0) {
      state.filters.secondaryExpanded = !state.filters.secondaryExpanded;
      render();
      return;
    }
    // 高级筛选维度：set-adv_{field}:value
    if (action.indexOf("set-adv_") === 0) {
      var remainder = action.slice(8); // e.g. "discipline:general"
      var colonIdx = remainder.indexOf(":");
      if (colonIdx !== -1) {
        var field = remainder.slice(0, colonIdx);
        var val = remainder.slice(colonIdx + 1);
        state.filters[field] = val;
        render();
        // 仅 discipline 影响 /archives 查询参数;其他高级筛选待 facets 接口就绪后再挂钩
        if (field === "discipline") reloadArchives();
      }
      return;
    }
    if (action === "toggle-advanced") {
      state.filters.advancedOpen = !state.filters.advancedOpen;
      render();
      return;
    }
    if (action === "toggle-compose-advanced") {
      state.composeAdvancedOpen = !state.composeAdvancedOpen;
      render();
      return;
    }
    if (action === "refresh") {
      load();
      return;
    }
    if (action === "close-compose") {
      closeCompose();
      return;
    }
    if (action === "add-volume") {
      state.compose.volumes.push(Compose.newVolume());
      render();
      return;
    }
    if (action === "remove-volume") {
      const volTemp = el.dataset.vol;
      state.compose.volumes = state.compose.volumes.filter((v) => v.tempId !== volTemp);
      if (!state.compose.volumes.length) state.compose.volumes = [Compose.newVolume()];
      render();
      return;
    }
    if (action === "remove-file") {
      removeFile(el.dataset.vol, el.dataset.file);
      return;
    }
    if (action === "assign-file") {
      // 将待归属文件分配到指定分册
      if (!state.compose) return;
      var fileTemp = el.dataset.file;
      var targetVol = el.value;
      if (!targetVol) return;
      var fileIdx = -1;
      for (var i = 0; i < (state.compose.unassignedFiles || []).length; i++) {
        if (state.compose.unassignedFiles[i].tempId === fileTemp) { fileIdx = i; break; }
      }
      if (fileIdx === -1) return;
      var fileEntry = state.compose.unassignedFiles[fileIdx];
      state.compose.unassignedFiles.splice(fileIdx, 1);
      var vol = findVolume(targetVol);
      if (vol) vol.files.push(fileEntry);
      render();
      return;
    }
    if (action === "save-draft") {
      var res = Compose.saveDraft(state.compose, global.localStorage);
      setToast(res.ok ? "已保存本地草稿。" + (hasComposeFiles() ? "（文件内容不入草稿，需重新选择）" : "") : (res.error || "草稿保存失败"));
      return;
    }
    if (action === "submit") {
      // 重新校验（使用最新表单数据）
      var vResult = Compose.validateCompose(state.compose);
      state._lastValidation = vResult;
      if (!vResult.ok) {
        _scrollToFirstError();
        setToast(vResult.blockingCount + " 项未完成，请修正后重试", "error");
        render();
        return;
      }
      var actions = Compose.resolveComposeActions(state.compose, { apiReady: state.flags.compose === true });
      if (!actions.canSubmit) {
        setToast(actions.submitDisabledReason);
        return;
      }
      if (!state.api || !state.api.compose) { setToast("API 未初始化"); return; }
      // 将 compose state 中 File 对象替换为元数据（文件上传待后续接线）
      var payload = JSON.parse(JSON.stringify(state.compose));
      payload.volumes = (payload.volumes || []).map(function (v) {
        v.files = (v.files || []).map(function (f) { return { name: f.name, size: f.size, type: f.type, role: f.role }; });
        return v;
      });
      payload.unassignedFiles = (payload.unassignedFiles || []).map(function (f) {
        return { name: f.name, size: f.size, type: f.type, role: f.role };
      });
      payload.supplementFiles = (payload.supplementFiles || []).map(function (f) {
        return { name: f.name, size: f.size, type: f.type, role: f.role };
      });

      state.compose.submitting = true;
      render();
      state.api.compose(payload).then(function (res) {
        state.compose.submitting = false;
        var data = (res && res.data) || {};
        if (res && res.status === "ready" && data.ok) {
          var n = (data.archives || []).length;
          setToast("已创建 " + n + " 份档案" + (n > 0 ? "（文件上传请前往档案详情页）" : ""));
          closeCompose();
          if (Api && state.api) load();
        } else {
          var errMsg = res.error || data.error || data.detail || "未知错误";
          if (res.status) errMsg = "[HTTP " + res.status + "] " + errMsg;
          setToast("提交失败：" + errMsg, "error");
          render();
        }
      }).catch(function (err) {
        state.compose.submitting = false;
        var msg = err.message || "网络错误";
        if (err.status) msg = "[HTTP " + err.status + "] " + msg;
        setToast("提交失败：" + msg, "error");
        render();
      });
      return;
    }
  }

  function hasComposeFiles() {
    if (!state.compose) return false;
    return (
      (state.compose.supplementFiles || []).length > 0 ||
      (state.compose.unassignedFiles || []).length > 0 ||
      (state.compose.volumes || []).some(function (v) { return (v.files || []).length > 0; })
    );
  }

  // 表单字段变更
  function handleInput(event) {
    if (!state.active) return;
    const t = event.target;
    if (!t) return;

    // ── 上传弹窗的输入处理（不依赖 state.compose）──────────────────────
    if (state.upload && state.upload.open) {
      if (t.dataset && t.dataset.qfileInput === "upload") {
        var newFiles = Array.from(t.files || []);
        state.upload.files = (state.upload.files || []).concat(newFiles);
        t.value = "";
        renderUploadFileList();
        renderUploadSubmitButton();
        return;
      }
      if (t.dataset && t.dataset.qfield === "upload.category") {
        state.upload.category = t.value;
        renderUploadSubmitButton();
        return;
      }
      if (t.dataset && t.dataset.qfield === "upload.province") {
        state.upload.province = t.value;
        renderUploadSubmitButton();
        return;
      }
      if (t.dataset && t.dataset.qfield === "upload.year") {
        state.upload.year = t.value;
        renderUploadSubmitButton();
        return;
      }
    }

    if (!state.compose) return;
    if (t.dataset && t.dataset.qfield) {
      const path = t.dataset.qfield;
      const prevSystem = state.compose.systemType;
      setByPath(state.compose, path, t.value);
      // 切换定额体系：清空另一路径，重新生成名称
      if (path === "systemType" && t.value !== prevSystem) {
        state.compose.path = { jurisdiction_level: "", jurisdiction_code: "", jurisdiction_label: "", industry_sector_code: "", industry_sector_label: "" };
        state.compose.set.titleUserEdited = false;
      }
      // 年份变更：重新生成名称（如果未手动编辑）
      if (path === "set.edition_year" && !state.compose.set.titleUserEdited) {
        var gen = Compose.autoGenerateTitle(state.compose);
        if (gen) state.compose.set.title = gen;
      }
      // 地区下拉变更：value=区划代码, 从 option data-label 取显示名
      if (path === "path.jurisdiction_code") {
        var selOpt = t.options && t.options[t.selectedIndex];
        var label = selOpt ? (selOpt.getAttribute("data-label") || selOpt.textContent) : "";
        state.compose.path.jurisdiction_label = label;
        state.compose.path.jurisdiction_code = t.value;
        if (!state.compose.set.titleUserEdited) {
          var gen2 = Compose.autoGenerateTitle(state.compose);
          if (gen2) state.compose.set.title = gen2;
        }
      }
      // 兼容旧版文本输入 (path.jurisdiction_label 仍可触发)
      if (path === "path.jurisdiction_label") {
        state.compose.path.jurisdiction_code = t.value;
        if (!state.compose.set.titleUserEdited) {
          var gen2b = Compose.autoGenerateTitle(state.compose);
          if (gen2b) state.compose.set.title = gen2b;
        }
      }
      if (path === "path.industry_sector_label") {
        state.compose.path.industry_sector_code = t.value;
        if (!state.compose.set.titleUserEdited) {
          var gen3 = Compose.autoGenerateTitle(state.compose);
          if (gen3) state.compose.set.title = gen3;
        }
      }
      // 用户手动编辑标题：标记已编辑
      if (path === "set.title") {
        state.compose.set.titleUserEdited = true;
      }
      // 实施日期变更：重新计算法律状态
      if (path === "set.effective_date") {
        state.compose.set.legal_status = Compose.computeLegalStatus(state.compose.set);
      }
      render();
      return;
    }
    if (t.dataset && t.dataset.qvol) {
      const vol = findVolume(t.dataset.vol);
      if (vol) vol[t.dataset.qvol] = t.value;
      renderComposeModal();
      return;
    }
    if (t.dataset && t.dataset.qfileRole) {
      const target = t.dataset.vol === "__supplement__" ? null : t.dataset.vol === "__unassigned__" ? null : findVolume(t.dataset.vol);
      var list;
      if (t.dataset.vol === "__supplement__") list = state.compose.supplementFiles;
      else if (t.dataset.vol === "__unassigned__") list = state.compose.unassignedFiles;
      else list = target ? target.files : [];
      const f = (list || []).find(function (x) { return x.tempId === t.dataset.file; });
      if (f) f.role = t.value;
      render();
      return;
    }
    if (t.dataset && t.dataset.qfileInput) {
      addFiles(t.dataset.vol, t.files);
      t.value = "";
      return;
    }
  }

  function bindEvents() {
    document.addEventListener("click", (event) => {
      if (!state.active) return;
      var target = event.target;
      if (!target || typeof target.closest !== "function") return;
      const el = target.closest("[data-quota-action]");
      if (!el) {
        // 点击空白关闭新增菜单
        if (state.addMenuOpen && !target.closest(".quota-add")) {
          state.addMenuOpen = false;
          render();
        }
        return;
      }
      // 阻止默认行为（防止表单提交 / 复选框切换等副作用）
      if (typeof event.preventDefault === "function") event.preventDefault();
      handleAction(el.dataset.quotaAction, el);
    });
    document.addEventListener("input", handleInput);
    document.addEventListener("change", handleInput);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !state.active) return;
      if (state.upload && state.upload.open) closeUploadDialog();
      else if (state.compose) closeCompose();
    });
  }

  // ── 对外接口（由 app.js 注入调用）─────────────────────────────────────
  function ensureInit() {
    if (state.initialized) return;
    state.env = Api
      ? Api.detectEnv({
          explicitEnv: global.__quotaEnv,
          hostname: typeof global.location !== "undefined" ? global.location.hostname : "",
        })
      : "production";
    state.api = Api ? Api.createQuotaApi({}) : null;
    bindEvents();
    state.initialized = true;
  }

  function activate() {
    ensureInit();
    state.active = true;
    const shell = $("#quotaShell");
    if (shell) shell.hidden = false;
    render();
    load();
  }

  function deactivate() {
    state.active = false;
    state.addMenuOpen = false;
    state.compose = null;
    state.upload = { open: false, files: [], category: "", province: "", year: "", submitting: false };
    const shell = $("#quotaShell");
    if (shell) shell.hidden = true;
    const modal = $("#quotaComposeModal");
    if (modal) {
      modal.hidden = true;
      modal.innerHTML = "";
    }
    const uploadModal = $("#quotaUploadModal");
    if (uploadModal) {
      uploadModal.hidden = true;
      uploadModal.innerHTML = "";
    }
  }

  // ── 地区下拉搜索 ──────────────────────────────────────────────────────
  function _filterRegionDropdown(searchInput) {
    var wrapper = searchInput.parentNode;
    var select = wrapper && wrapper.querySelector("select");
    if (!select) return;
    var q = (searchInput.value || "").toLowerCase().replace(/\s+/g, "");
    var opts = select.options;
    // 跳过占位 option (index 0)
    for (var i = 1; i < opts.length; i++) {
      var opt = opts[i];
      var label = (opt.getAttribute("data-label") || opt.textContent).toLowerCase();
      var py = (opt.getAttribute("data-py") || "");
      var visible = !q || label.indexOf(q) !== -1 || py.indexOf(q) !== -1;
      opt.hidden = !visible;
    }
    // 如果当前选中项被隐藏则重置
    if (select.selectedIndex >= 1 && opts[select.selectedIndex].hidden) {
      select.selectedIndex = 0;
    }
  }

  function _onRegionSelect(selectEl) {
    // 选中后回填搜索框
    var wrapper = selectEl.parentNode;
    var searchInput = wrapper && wrapper.querySelector(".quota-region-search");
    var selOpt = selectEl.options[selectEl.selectedIndex];
    if (searchInput && selOpt && selOpt.value) {
      searchInput.value = selOpt.getAttribute("data-label") || selOpt.textContent;
    }
  }

  const QuotaUI = {
    // 生命周期
    activate,
    deactivate,
    render,
    load,
    openUploadDialog,
    closeUploadDialog,
    submitUpload,
    // 测试可见的纯函数/常量
    _state: state,
    _filterRegionDropdown,
    _onRegionSelect,
    _internals: {
      setByPath,
      statVal,
      pendingCount,
      tabEnabled,
      shouldUseComposeMenu,
      renderArchiveRow,
      renderUploadModal,
      handleAction,
      TAB_CAPABILITY,
      TABS,
      LIST_COLUMNS,
      PRIMARY_FILTERS,
    },
  };

  global.QuotaUI = QuotaUI;
  if (typeof module !== "undefined" && module.exports) module.exports = QuotaUI;
})(typeof window !== "undefined" ? window : globalThis);
