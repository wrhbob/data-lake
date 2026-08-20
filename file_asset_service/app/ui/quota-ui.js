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
    compare: "compare",
  });

  const TABS = Object.freeze([
    { view: "archives", label: "档案列表", icon: "list" },
    { view: "versionSystem", label: "版本体系", icon: "layers" },
    { view: "coverage", label: "覆盖矩阵", icon: "table-2" },
    { view: "pending", label: "待归档", icon: "inbox" },
    { view: "compare", label: "定额对比", icon: "git-compare" },
  ]);

  // 第一层筛选（一级分类 → 二级筛选映射）
  // v0.9 (2026-08-18): value 改用 book_category 单字段 (与 DB quota_publication_set.book_category 对齐)。
  //   历史用 quota_system_type 双层命名空间 (construction_regional / industry_specialty) 与 DB book_category 不一致,
  //   后端 /archives?primary=... 已迁到 book_category, 此处同步, 不再双层映射。
  // v0.9.3 (2026-08-18): 清单规范 secondary 从 "scope" (按 pubset title group, 1 选项, 等于无筛选) 改为
  //   "jurisdiction" (按省份下拉, 与建筑工程定额同形)。前端 jurisdiction chip 走静态 PROVINCE_REGIONS,
  //   不读后端 fx.jurisdictions (见 getFacetItems HOTFIX-QA-CHIP-001), 改 secondary 即可。
  const PRIMARY_FILTERS = Object.freeze([
    { value: "all", label: "全部", secondary: null },
    { value: "boq_standard", label: "清单规范", secondary: "jurisdiction" },
    { value: "construction_quota", label: "建筑工程定额", secondary: "jurisdiction" },
    { value: "industry_quota", label: "专业工程定额", secondary: "industry" },
  ]);

  // v0.9.4 (2026-08-19): 覆盖矩阵 sub-tab — 每类一张矩阵, 行轴在「省份」/「专业」间切换.
  //   - construction_quota / boq_standard → row_label="省份" (province axis)
  //   - industry_quota                     → row_label="专业" (industry axis)
  // 默认激活 construction_quota (数据最多, 19 条省份档案).
  const COVERAGE_BOOK_CATEGORIES = Object.freeze([
    { value: "construction_quota", label: "建筑工程定额", rowLabel: "省份" },
    { value: "boq_standard",       label: "清单规范",     rowLabel: "省份" },
    { value: "industry_quota",     label: "专业工程定额", rowLabel: "专业" },
  ]);
  const COVERAGE_DEFAULT_BOOK_CATEGORY = "construction_quota";

  function primaryMeta(primaryValue) {
    for (var i = 0; i < PRIMARY_FILTERS.length; i++) {
      if (PRIMARY_FILTERS[i].value === primaryValue) return PRIMARY_FILTERS[i];
    }
    return PRIMARY_FILTERS[0];
  }

  // 二级筛选标签（不含"全部"，由渲染函数自动添加）
  // v0.9.3 (2026-08-18): industry "行业分类" → "专业", 与 quotaUploadModal 的"专业"下拉对齐 (upload 时
  //   category=industry_quota 用的 label 就是 "专业")。前端语义一致, 后端不受影响。
  const SECONDARY_LABELS = Object.freeze({
    scope: "适用范围",
    jurisdiction: "地区",
    industry: "专业",
  });

  const LIST_COLUMNS = Object.freeze([
    "档案标题",
    "资料分类",
    "文件数",
    "状态",
    "操作",
  ]);

  // ── 5 状态徽章（v0.4 web-frontend/SPEC.md §3.2.3 H） ──
  // 把 archive.status + parse.error_code 推导出 5 个 UI 状态之一
  //   pending / parsing / review / done / failed
  // 注意：useless 中走 done 渲染（合并 qa_passed + usable）
  const PARSE_STATUS_VARIANT = Object.freeze({
    pending:  "pending",
    parsing:  "parsing",
    parsed:   "review",
    // v0.5 pipeline 新状态：candidate.xlsx 已落 MinIO，等用户上传 reviewed.xlsx
    candidate_ready: "review",
    qa_passed: "done",
    usable:   "done",
    failed_user: "failed",
    failed_permanent: "failed",
    // v0.8: worker has_parser_for(province) 失败 → 档案入库成功但不会自动解析。
    // 列表徽章视觉上等同 pending（"未解析"），不污染 5 态分布；
    // 提示信息走右下角 toast（点击"开始解析"时弹）+ 详情页 renderParseStatusSection。
  });

  function resolveUiStatus(row) {
    // v0.4 Bug#1 修：5 态徽章由 parse_status 驱动（不是 row.status，row.status 是
    // archive 生命周期 status='pending_tag'，不在 PARSE_STATUS_VARIANT 表里）。
    // 后端 /archives 已透出 parse_status（list 端点修复见 quota_api.py list_quota_archives）。
    // parse_status 为空（从未触发解析）→ 落 pending，UI 显示"未解析"。
    var st = (row && (row.parse_status || row.status)) || "pending";
    // transient 在 parsing 上叠加，但不替换主徽章——留后续 banner 处理
    return PARSE_STATUS_VARIANT[st] || "pending";
  }

  function renderStatusBadge(row) {
    var variant = resolveUiStatus(row);
    var labelMap = {
      pending:  "未解析",
      parsing:  "解析中",
      review:   "待审核",
      done:     "已完成",
      failed:   "解析失败",
    };
    var iconMap = {
      pending:  null,
      parsing:  "loader",
      review:   null,
      done:     "check",
      failed:   "alert-triangle",
    };
    var label = labelMap[variant] || "—";
    var icon = iconMap[variant];
    var extraSpin = variant === "parsing" ? " status-badge--spin" : "";
    var inner = icon
      ? '<i data-lucide="' + icon + '"></i><span>' + escapeHtml(label) + '</span>'
      : '<span>' + escapeHtml(label) + '</span>';
    return '<span class="status-badge status-badge--' + variant + extraSpin + '">' + inner + '</span>';
  }

  // 分类标签映射（仅展示用，与 file_role 无关）
  const CATEGORY_LABELS = {
    construction_quota: "建筑工程定额",
    industry_quota: "专业工程定额",
    boq_standard: "清单规范",
  };

  // ── 智能动作矩阵（v0.4 SPEC §3.2.3 I） ──
  // 5 UI 状态 × 2 智能图标（lucide 名 + tooltip + action 名）
  const SMART_ACTIONS = Object.freeze({
    pending: [
      { icon: "eye",  tip: "预览 PDF",         action: "preview-archive" },
      { icon: "play", tip: "开始解析",         action: "parse-trigger" },
    ],
    parsing: [
      { icon: "eye",          tip: "预览 PDF",  action: "preview-archive" },
      { icon: "loader-circle", tip: "解析中",   action: "__disabled__" },
    ],
    review: [
      { icon: "download", tip: "下载 candidate.xlsx", action: "parse-download-candidate" },
      { icon: "upload",   tip: "上传 reviewed.xlsx",   action: "parse-upload-reviewed" },
    ],
    done: [
      { icon: "download", tip: "下载 final.xlsx", action: "parse-download-final" },
      { icon: "eye",      tip: "预览 PDF",         action: "preview-archive" },
    ],
    failed: [
      { icon: "refresh-cw", tip: "重新解析", action: "parse-trigger" },
      { icon: "eye",       tip: "预览 PDF",  action: "preview-archive" },
    ],
  });

  // ⋯ 下拉项（按状态返回，普通项 + 危险项）
  // 普通项 icon / label / action；danger: true 渲染分隔条 + 红色
  // 删除档案 (#10) 在所有状态下都放 ⋯ 下拉危险区；删除解析结果 (#9) 仅 parsed/qa_passed/usable/failed_*
  // v0.5 双语义：「撤回审核」(parse-revert-reviewed, scope=reviewed_only) 仅 done 态有,
  //               「删除全部解析结果」(parse-delete-all, scope=all) 所有有解析结果的态都有
  const DROPDOWN_ITEMS = Object.freeze({
    pending: [
      { divider: true },
      { icon: "trash-2", label: "删除档案", action: "archive-delete", danger: true },
    ],
    parsing: [
      { icon: "file-text", label: "查看 Manifest", action: "parse-show-manifest" },
      { divider: true },
      { icon: "trash-2",   label: "删除档案",      action: "archive-delete", danger: true },
    ],
    review: [
      { icon: "file-text",      label: "查看 Manifest", action: "parse-show-manifest" },
      { icon: "refresh-cw",     label: "重新解析",       action: "parse-trigger" },
      { divider: true },
      { icon: "trash-2",        label: "删除全部解析结果", action: "parse-delete-all", danger: true },
      { icon: "trash-2",        label: "删除档案",          action: "archive-delete", danger: true },
    ],
    done: [
      { icon: "file-text",   label: "查看 Manifest", action: "parse-show-manifest" },
      { icon: "check-circle", label: "查看 QA 报告", action: "parse-show-qa" },
      { divider: true },
      // v0.5 新增：「撤回审核」(只删 reviewed xlsx,保留 candidate) — 走 scope=reviewed_only
      { icon: "rotate-ccw",  label: "撤回审核",     action: "parse-revert-reviewed", danger: false },
      // 「删除全部解析结果」 — 走 scope=all (默认)
      { icon: "trash-2",     label: "删除全部解析结果", action: "parse-delete-all", danger: true },
      { icon: "trash-2",     label: "删除档案",         action: "archive-delete", danger: true },
    ],
    failed: [
      { icon: "file-text",   label: "查看 Manifest", action: "parse-show-manifest" },
      { icon: "check-circle", label: "查看 QA 报告", action: "parse-show-qa" },
      { divider: true },
      { icon: "trash-2",     label: "删除全部解析结果", action: "parse-delete-all", danger: true },
      { icon: "trash-2",     label: "删除档案",          action: "archive-delete", danger: true },
    ],
  });

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
    upload: { open: false, files: [], category: "", province: "", industry_sector_code: "", year: "", submitting: false },
    // 列表行 ⋯ 下拉：open=false 时下拉隐藏
    dropdown: { open: false, archiveId: "", variant: "pending" },
    // 删除解析结果 modal：open=false 时隐藏
    deleteParse: {
      open: false,
      archiveId: "",
      title: "",
      submitting: false,
      // 后端报错信息（422 等），modal 不关闭以保留反馈
      error: "",
    },
    // 删除档案 modal（#10；与 #9 严格区分，按 SPEC §3.1.4）
    deleteArchive: {
      open: false,
      archiveId: "",
      title: "",
      submitting: false,
      error: "",
    },
    // 跨省定额对比 modal（v0.12 2026-08-17）：keyword 必填，任意词 / 排除词 选填
    compare: {
      open: false,
      keyword: "",
      any_terms: "",
      exclude_terms: "",
      submitting: false,
      error: "",
    },
    // v0.9.4 (2026-08-19): 覆盖矩阵按 book_category 拆分; cache 缓存 3 类矩阵各自的
    // {status,data,error}, bookCategory 记录当前激活 sub-tab.
    coverage: {
      bookCategory: COVERAGE_DEFAULT_BOOK_CATEGORY,
      cache: Object.create(null),
      status: CAP.UNKNOWN,
      data: null,
      error: "",
    },
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

  // ── 上传弹窗省份全开（v0.8: 入库 ≠ 解析, 32 省都能入, 无 extractor 由 worker 兜底 skip）──
  // 后端 quota_api.py:_UPLOAD_PROVINCE_MAP 已全员预填 province 短码 + jurisdiction_code + profile,
  // 不再有 _EXTRACTABLE_PROVINCES 白名单. 档案能不能入库只看 province 是否在 _VALID_PROVINCE_CODES,
  // 能不能解析由 quota_parser_worker.has_parser_for(province) 守卫 (无则 skipped_no_parser 终态).
  // 完整语义见 quota/README.md §8.
  // 注意: code 用 province 短码 (sc/cq/bj/...) 不是 6 位 jurisdiction_code,
  //       因为 upload API 期望短码 (与 worker job.metadata_payload.province 对齐).
  // v0.9 (2026-08-18): 加 nat = 全国, 用于 boq_standard (全国清单规范) 与
  //   construction_quota (某些省份可能没有省级定额, 但全国通用可入档) 两种场景。
  //   注意 nat 与 12 项专业工程 (industry_quota) 短码无任何冲突:
  //   - province 短码是 2 字母拼音 (bj/tj/sc/cq/nat/...)
  //   - industry_sector 短码是下划线连接的 2-3 词英文 (water_conservancy/electric_power/...)
  //   后端 _UPLOAD_PROVINCE_MAP 同样首位插入 nat=("000000", "全国", None, "national")。
  const UPLOAD_PROVINCE_OPTIONS = Object.freeze([
    { code: "nat", label: "全国" },
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
    { code: "cq",  label: "重庆市" },
    { code: "sc",  label: "四川省" },
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
    for (var i = 0; i < UPLOAD_PROVINCE_OPTIONS.length; i++) {
      var r = UPLOAD_PROVINCE_OPTIONS[i];
      var sel = (r.code === selectedCode) ? " selected" : "";
      html += '<option value="' + escapeHtml(r.code) + '"' + sel + '>' + escapeHtml(r.label) + '</option>';
    }
    return html;
  }

  // v0.9 (2026-08-18): 12 项专业工程 v1 受控词表, 与后端 quota_api.py:INDUSTRY_SECTOR_LABELS
  //   必须保持完全一致 (短码 → 中文名一一对应)。后端校验 industry_sector_code
  //   必须落在该词表内 (HTTP 422 INVALID_INDUSTRY)。词表维护: 走 quota_taxonomy.py 同步。
  //   排序: 按拼音 a-z (前端可读性; 后端 _VALID_INDUSTRY_SECTOR_CODES 是 set, 顺序无所谓)。
  const INDUSTRY_SECTOR_OPTIONS = Object.freeze([
    { code: "coal",               label: "煤炭工程" },
    { code: "electric_power",     label: "电力工程" },
    { code: "highway",            label: "公路工程" },
    { code: "ict",                label: "信息通信工程" },
    { code: "non_ferrous_metal",  label: "有色金属工业" },
    { code: "petrochemical",      label: "石化工程" },
    { code: "petroleum",          label: "石油工程" },
    { code: "power_grid",         label: "电网工程" },
    { code: "railway",            label: "铁路工程" },
    { code: "solar_power",        label: "光伏发电工程" },
    { code: "water_conservancy",  label: "水利工程" },
    { code: "waterway_port",      label: "水运港口工程" },
  ]);
  const VALID_INDUSTRY_SECTOR_CODES = new Set(INDUSTRY_SECTOR_OPTIONS.map(function (o) { return o.code; }));

  function renderUploadIndustryOptions(selectedCode) {
    var html = '<option value="">请选择专业</option>';
    for (var i = 0; i < INDUSTRY_SECTOR_OPTIONS.length; i++) {
      var r = INDUSTRY_SECTOR_OPTIONS[i];
      var sel = (r.code === selectedCode) ? " selected" : "";
      html += '<option value="' + escapeHtml(r.code) + '"' + sel + '>' + escapeHtml(r.label) + '</option>';
    }
    return html;
  }

  // v0.9 (2026-08-18): 资料分类 → 必填字段映射。
  //   - boq_standard         : 需要 province (省份 或 全国)
  //   - construction_quota   : 需要 province (省份 或 全国)
  //   - industry_quota       : 需要 industry_sector_code (专业), province 占位
  // UI 字段可见性/必填校验都走这张表。
  const UPLOAD_CATEGORY_FIELDS = Object.freeze({
    boq_standard:       { needsProvince: true,  needsIndustry: false, provinceLabel: "适用省份" },
    construction_quota: { needsProvince: true,  needsIndustry: false, provinceLabel: "省份"        },
    industry_quota:     { needsProvince: false, needsIndustry: true,  provinceLabel: null           },
  });

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
          <p class="eyebrow">Layer 0 · Projection Archive</p>
          <h1>清单定额档案台</h1>
          <span class="quota-summary">
            ${statVal("systems")} 套资料体系 · ${statVal("archived")} 份已归档 · ${statVal("pendingRaw")} 份原件待归档
          </span>
        </div>
        <div class="quota-header-actions">
          ${debug}
          <label class="search-box">
            <i data-lucide="search"></i>
            <input id="quotaSearch" type="search" placeholder="搜索资料体系、分册、标准/定额编号" autocomplete="off" value="${escapeHtml(state.filters.q || "")}" />
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
  //   - v0.9.3: 清单规范 secondary 也是 "jurisdiction", 同样要透传 jurisdiction_code
  function _facetsParams() {
    var params = { primary: state.filters.primary };
    if (
      (state.filters.primary === "construction_quota" ||
       state.filters.primary === "boq_standard") &&
      state.filters.secondary &&
      state.filters.secondary !== "all"
    ) {
      params.jurisdiction_code = state.filters.secondary;
    }
    return params;
  }

  // 把后端 `{code, label, count}` 形态的 facet 条目统一映射到前端 `renderChipGroup`
  // 期望的 `{value, label}` 形态。后端 2026-08-18 起统一用 `code` 字段（见 quota_api.py
  // /facets），industries / jurisdictions / disciplines / 等都走这个形态。
  // 之前 industries 直接 `return fx.industries || []` 没映射 → item.value 是 undefined
  // → chip 的 data-quota-action 变成 "set-secondary:undefined"
  // → selected === item.value 永远 false → 没有 active class 不显示蓝框
  // → state.filters.secondary 被设成 "undefined" 字符串传给后端
  // → /api/archives?industry_sector_code=undefined 返回 0 条
  // 2026-08-19 修复：所有走 code 形态的 facet 都先经过这个映射。
  function _mapCodeToValue(items) {
    return (items || []).map(function (i) {
      return {
        value: i.code != null ? String(i.code) : i.value,
        label: i.label != null ? String(i.label) : String(i.code || i.value || ""),
        count: i.count,
      };
    });
  }

  function getFacetItems(key) {
    // 后端 facets 数据为空 / 未就绪时仍允许静默兜底，避免"地区维度待接入"这种空位
    var fx = (state.facets && state.facets.data) || {};
    switch (key) {
      case "scope": case "scopes":
        return _mapCodeToValue(fx.scopes);
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
        return _mapCodeToValue(fx.industries);
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
        return _mapCodeToValue(fx.disciplines);
      case "materialTypes":
        return _mapCodeToValue(fx.materialTypes);
      case "cities":
        return _mapCodeToValue(fx.cities);
      case "issuers":
        return _mapCodeToValue(fx.issuers);
      case "metadataStatuses":
        return _mapCodeToValue(fx.metadataStatuses);
      case "archiveStatuses":
        return _mapCodeToValue(fx.archiveStatuses);
      case "sourceChannels":
        return _mapCodeToValue(fx.sourceChannels);
      case "fileFormats":
        return _mapCodeToValue(fx.fileFormats);
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
        return renderCoverageMatrixView();
      case "compare":
        return renderCompareView();
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
    var archiveIdEnc = encodeURIComponent(row.archive_id || "");
    var archiveId = row.archive_id || "";
    var variant = resolveUiStatus(row);
    var smartIcons = renderSmartActions(archiveIdEnc, variant);
    var dropdownHtml = renderDropdown(archiveIdEnc, variant);
    return `
      <tr data-quota-action="preview-archive" data-archive-id="${archiveIdEnc}" style="cursor:pointer" title="点击预览">
        <td class="quota-col-title">${cell(row.title)}</td>
        <td>${cell(categoryVal)}</td>
        <td class="quota-col-count">${cell(row.file_count)}</td>
        <td class="quota-col-status">${renderStatusBadge(row)}</td>
        <td class="quota-col-actions">
          <span class="quota-action-cell">${smartIcons}${dropdownHtml}</span>
        </td>
      </tr>`;
  }

  // 智能图标（2 个 icon-only 按钮）
  function renderSmartActions(archiveIdEnc, variant) {
    var arr = SMART_ACTIONS[variant] || [];
    return arr.map(function (it) {
      var disabled = it.action === "__disabled__";
      var dataAttr = disabled
        ? ' disabled aria-disabled="true" data-tooltip="' + escapeHtml(it.tip) + '"'
        : ' data-quota-action="' + it.action + '" data-archive-id="' + archiveIdEnc + '"' +
          ' data-tooltip="' + escapeHtml(it.tip) + '"';
      return '<button class="quota-action-cell__icon" type="button"' + dataAttr +
        ' title="' + escapeHtml(it.tip) + '"><i data-lucide="' + it.icon + '"></i></button>';
    }).join("");
  }

  // ⋯ 下拉触发按钮 + 菜单（菜单默认 hidden，由 dropdown.open 控制）
  // 当前行 dropdown.open 命中 → 菜单去掉 hidden、aria-expanded=true
  function renderDropdown(archiveIdEnc, variant) {
    var items = DROPDOWN_ITEMS[variant] || [];
    var listHtml = items.length
      ? items.map(function (it) {
          if (it.divider) return '<div class="quota-dropdown__divider"></div>';
          var cls = "quota-dropdown__item" + (it.danger ? " quota-dropdown__item--danger" : "");
          return '<button class="' + cls + '" type="button" data-quota-action="' + it.action +
            '" data-archive-id="' + archiveIdEnc + '"><i data-lucide="' + it.icon +
            '"></i><span>' + escapeHtml(it.label) + '</span></button>';
        }).join("")
      : '<div class="quota-dropdown__empty">无更多操作</div>';
    var isOpen = state.dropdown && state.dropdown.open && state.dropdown.archiveId === archiveIdEnc;
    var menuAttrs = isOpen ? ' role="menu"' : ' role="menu" hidden';
    var triggerAttrs = isOpen
      ? ' data-quota-action="dropdown-toggle" data-archive-id="' + archiveIdEnc + '"' +
        ' title="更多操作" aria-haspopup="true" aria-expanded="true"'
      : ' data-quota-action="dropdown-toggle" data-archive-id="' + archiveIdEnc + '"' +
        ' title="更多操作" aria-haspopup="true" aria-expanded="false"';
    return '<span class="quota-dropdown' + (isOpen ? ' is-open' : '') + '">' +
      '<button class="quota-action-cell__icon quota-action-cell__more" type="button"' +
        triggerAttrs + ' data-tooltip="更多操作"><i data-lucide="more-horizontal"></i></button>' +
      '<div class="quota-dropdown__menu"' + menuAttrs + '>' + listHtml + '</div>' +
    '</span>';
  }

  // ── 删除解析结果 modal（v0.4 SPEC §3.2.3 K） ──
  // 二次确认：输入档案标题才解锁确认按钮；提交后端 §5.8 端点
  // modal 不挂 quotalModal 样式名——样式独享 class 集合（styles.css 中定义）
  function renderDeleteParseModal() {
    var modal = document.getElementById("quotaDeleteParseModal");
    if (!modal) return;
    var d = state.deleteParse || {};
    if (!d.open) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      modal.innerHTML = "";
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    var errorHtml = d.error
      ? '<div class="quota-delete-error" role="alert"><i data-lucide="alert-circle"></i><span>' +
        escapeHtml(d.error) + '</span></div>'
      : "";
    // v0.5 双语义文案：scope='reviewed_only' → 「撤回审核」；scope='all' → 「删除全部解析结果」
    var scope = d.scope || "all";
    var isRevert = (scope === "reviewed_only");
    var eyebrow = isRevert ? "可逆操作" : "危险操作";
    var title = isRevert ? "撤回审核" : "删除全部解析结果";
    var subtitle = isRevert
      ? '此操作将删除 MinIO 上的 <code>final.xlsx</code> + <code>manifest.json</code>、' +
        '<code>archive_file.parse_final_xlsx</code> 行，并把 <code>parse_status</code> 退回 ' +
        '<code>candidate_ready</code>。<strong>candidate / markdown / html 保留</strong>，你可以重新上传 ' +
        'reviewed.xlsx 重新走阶段 B。'
      : '此操作将删除 MinIO 上的 markdown / html / candidate.xlsx / final.xlsx / manifest.json' +
        ' 与 Archive <code>parse_*</code> 字段、<code>archive_file</code> 4 类 parse_* 行、关联 ' +
        '<code>parse_job</code> 记录；<strong>原始 PDF 与档案元数据保留</strong>，回到未解析态可重新解析。';
    var confirmIcon = isRevert ? "rotate-ccw" : "trash-2";
    var confirmLabel = d.submitting
      ? (isRevert ? "撤回中…" : "删除中…")
      : (isRevert ? "确认撤回审核" : "确认删除全部解析结果");
    modal.innerHTML = `
      <form class="quota-delete-confirm-modal" data-quota-form="delete-parse">
        <header class="quota-delete-header">
          <div>
            <p class="eyebrow">${escapeHtml(eyebrow)}</p>
            <h2>${escapeHtml(title)}</h2>
          </div>
          <button class="icon-button" type="button" data-quota-action="close-delete-parse" title="关闭">
            <i data-lucide="x"></i>
          </button>
        </header>
        <div class="quota-delete-body">
          <p class="quota-delete-subtitle">${subtitle}</p>
          <p class="quota-delete-archive-title">档案：<strong>${escapeHtml(d.title || "—")}</strong></p>
          ${errorHtml}
        </div>
        <footer class="quota-compose-footer">
          <button class="secondary-button" type="button" data-quota-action="close-delete-parse">取消</button>
          <button class="primary-button quota-delete-confirm-btn" type="button"
            data-quota-action="submit-delete-parse">
            <i data-lucide="${confirmIcon}"></i>
            <span>${escapeHtml(confirmLabel)}</span>
          </button>
        </footer>
      </form>
    `;
  }

  // 二次确认仅靠弹窗文案承载，弹窗内无输入项，点击确认按钮即提交后端

  // scope: 'all'(默认) | 'reviewed_only'
  function openDeleteParseModal(archiveId, title, scope) {
    state.deleteParse = {
      open: true,
      archiveId: archiveId || "",
      title: title || "",
      scope: scope || "all",
      submitting: false,
      error: "",
    };
    renderDeleteParseModal();
    refreshIcons();
  }

  function closeDeleteParseModal() {
    state.deleteParse = {
      open: false,
      archiveId: "",
      title: "",
      scope: "all",
      submitting: false,
      error: "",
    };
    renderDeleteParseModal();
  }

  // ── 删除档案 modal（#10；按 SPEC §3.1.4 与 #9 严格区分）──────────────
  // 副标题更重（涉及资料体系可能级联删）；按钮 label 改"确认删除档案"；audit action='quota_archive_deleted'
  function renderDeleteArchiveModal() {
    var modal = document.getElementById("quotaDeleteArchiveModal");
    if (!modal) return;
    var d = state.deleteArchive || {};
    if (!d.open) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      modal.innerHTML = "";
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    var errorHtml = d.error
      ? '<div class="quota-delete-error" role="alert"><i data-lucide="alert-circle"></i><span>' +
        escapeHtml(d.error) + '</span></div>'
      : "";
    modal.innerHTML = `
      <form class="quota-delete-confirm-modal" data-quota-form="delete-archive">
        <header class="quota-delete-header">
          <div>
            <p class="eyebrow">危险操作 · 不可恢复</p>
            <h2>删除档案</h2>
          </div>
          <button class="icon-button" type="button" data-quota-action="close-delete-archive" title="关闭">
            <i data-lucide="x"></i>
          </button>
        </header>
        <div class="quota-delete-body">
          <p class="quota-delete-subtitle">
            此操作将<strong>删除档案本身及其全部关联文件</strong>（原始 PDF + 4 类解析产物 +
            <code>archive_file</code> + <code>quota_archive_profile</code> + <code>parse_job</code>）；
            若该档案是所属 <code>quota_publication_set</code> 的唯一分册，<strong>资料体系本身也会被删</strong>。
            <br/><strong>此操作不可恢复。</strong>
          </p>
          <p class="quota-delete-archive-title">档案：<strong>${escapeHtml(d.title || "—")}</strong></p>
          ${errorHtml}
        </div>
        <footer class="quota-compose-footer">
          <button class="secondary-button" type="button" data-quota-action="close-delete-archive">取消</button>
          <button class="primary-button quota-delete-confirm-btn" type="button"
            data-quota-action="submit-delete-archive">
            <i data-lucide="trash-2"></i>
            <span>${d.submitting ? "删除中…" : "确认删除档案"}</span>
          </button>
        </footer>
      </form>
    `;
  }

  function openDeleteArchiveModal(archiveId, title) {
    state.deleteArchive = {
      open: true,
      archiveId: archiveId || "",
      title: title || "",
      submitting: false,
      error: "",
    };
    renderDeleteArchiveModal();
    refreshIcons();
  }

  function closeDeleteArchiveModal() {
    state.deleteArchive = {
      open: false,
      archiveId: "",
      title: "",
      submitting: false,
      error: "",
    };
    renderDeleteArchiveModal();
  }

  // ── 跨省定额对比（v0.12 2026-08-17） ─────────────────────────
  // 来源：quota-compare/extract.py 已 commit（commit 659fa3a）
  // 数据层：4 省已审核 final.xlsx → collect_hits → xlsx 字节
  // 入口：定额对比 tab → 打开对比工具 → 弹窗 3 输入（keyword 必填）
  function renderCompareView() {
    const ready = state.flags.compare === true;
    if (!ready) {
      return `
        <section class="quota-view-card">
          <div class="quota-empty">
            <i data-lucide="git-compare"></i>
            <strong>定额对比待接入</strong>
            <span>${escapeHtml(capReason("compare"))}</span>
          </div>
        </section>`;
    }
    return `
      <section class="quota-view-card">
        <div class="quota-compare-toolbar">
          <i data-lucide="git-compare"></i>
          <div class="quota-compare-toolbar__text">
            <strong>跨省定额对比</strong>
            <span>从所有已审核的 final.xlsx 抽取主题定额，按省份分组，输出单 sheet 跨省对比 xlsx。实际可对比的省份由当前已审核档案决定，无硬编码省份列表。</span>
          </div>
          <button class="primary-button" type="button" data-quota-action="open-compare-modal">
            <i data-lucide="play"></i>
            <span>打开对比工具</span>
          </button>
        </div>
        <div class="quota-compare-tips">
          <p><strong>使用提示</strong>：</p>
          <ul>
            <li><code>keyword</code> 必填，定额名称必须包含该词</li>
            <li><code>any_terms</code> 选填（空格分隔），与主词 <strong>AND 关系</strong>：定额名必须同时含主词与任一补充词；常用于主类下钻子类，如「挖」+「土 淤泥 沟槽」</li>
            <li><code>exclude_terms</code> 选填（空格分隔），任一词出现即排除；常用于「挖/挖土」防误伤「挖掘机」</li>
            <li>结果直接下载；不写入数据库，不污染原 final.xlsx</li>
          </ul>
        </div>
      </section>
    `;
  }

  function renderCompareModal() {
    const modal = document.getElementById("quotaCompareModal");
    if (!modal) return;
    var c = state.compare || {};
    if (!c.open) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      modal.innerHTML = "";
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    const errorHtml = c.error
      ? '<div class="quota-delete-error" role="alert"><i data-lucide="alert-circle"></i><span>' +
        escapeHtml(c.error) + '</span></div>'
      : "";
    const submitLabel = c.submitting
      ? "下载中…"
      : '<i data-lucide="download"></i><span>运行并下载</span>';
    modal.innerHTML = `
      <form class="quota-delete-confirm-modal" data-quota-form="compare" onsubmit="return false;">
        <header class="quota-delete-header">
          <div>
            <p class="eyebrow">跨省对比</p>
            <h2>定额跨省对比</h2>
          </div>
          <button class="icon-button" type="button" data-quota-action="close-compare-modal" title="关闭">
            <i data-lucide="x"></i>
          </button>
        </header>
        <div class="quota-delete-body">
          <p class="quota-delete-subtitle">输入关键词（必填）与可选的扩展/排除词，浏览器自动下载跨省对比 xlsx。</p>
          <div class="quota-compare-form">
            <label class="quota-compare-field">
              <span>关键词（必填）</span>
              <input id="compareKeyword" type="text" placeholder="例如：踢脚、扶手、挖" autocomplete="off" />
              <small>定额「名称」必须包含该词才会被命中。</small>
            </label>
            <label class="quota-compare-field">
              <span>补充词（选填）</span>
              <input id="compareAnyTerms" type="text" placeholder="例如：踢脚线 踢脚板" autocomplete="off" />
              <small>定额名同时含主词与任一补充词才命中；补充词起到「细化主类」的作用（例：挖 + 土/淤泥/沟槽 = 人工挖土方类）。</small>
            </label>
            <label class="quota-compare-field">
              <span>排除词（选填，空格分隔）</span>
              <input id="compareExcludeTerms" type="text" placeholder="例如：挖掘机 挖孔 钻" autocomplete="off" />
              <small>任一词出现即排除；常用于防误伤。</small>
            </label>
          </div>
          ${errorHtml}
        </div>
        <footer class="quota-compose-footer">
          <button class="secondary-button" type="button" data-quota-action="close-compare-modal">取消</button>
          <button class="primary-button" type="button" data-quota-action="submit-compare" ${c.submitting ? "disabled" : ""}>
            ${submitLabel}
          </button>
        </footer>
      </form>
    `;
    // 把 modal 内 input 同步回 state（用户输入不丢）
    syncCompareInputsFromDom();
  }

  function syncCompareInputsFromDom() {
    const k = document.getElementById("compareKeyword");
    const a = document.getElementById("compareAnyTerms");
    const e = document.getElementById("compareExcludeTerms");
    if (k) k.value = state.compare.keyword || "";
    if (a) a.value = state.compare.any_terms || "";
    if (e) e.value = state.compare.exclude_terms || "";
  }

  function syncCompareStateFromDom() {
    const k = document.getElementById("compareKeyword");
    const a = document.getElementById("compareAnyTerms");
    const e = document.getElementById("compareExcludeTerms");
    if (k) state.compare.keyword = k.value;
    if (a) state.compare.any_terms = a.value;
    if (e) state.compare.exclude_terms = e.value;
  }

  function openCompareModal() {
    state.compare = {
      open: true,
      keyword: state.compare.keyword || "",
      any_terms: state.compare.any_terms || "",
      exclude_terms: state.compare.exclude_terms || "",
      submitting: false,
      error: "",
    };
    renderCompareModal();
    refreshIcons();
    // 自动聚焦第一个输入
    setTimeout(() => {
      const k = document.getElementById("compareKeyword");
      if (k) k.focus();
    }, 0);
  }

  function closeCompareModal() {
    state.compare = {
      open: false,
      keyword: state.compare.keyword || "",
      any_terms: state.compare.any_terms || "",
      exclude_terms: state.compare.exclude_terms || "",
      submitting: false,
      error: "",
    };
    renderCompareModal();
  }

  async function submitCompare() {
    syncCompareStateFromDom();
    const keyword = (state.compare.keyword || "").trim();
    if (!keyword) {
      state.compare.error = "请输入关键词（必填）";
      renderCompareModal();
      refreshIcons();
      return;
    }
    state.compare.error = "";
    state.compare.submitting = true;
    renderCompareModal();
    refreshIcons();

    try {
      const body = new URLSearchParams();
      body.set("keyword", keyword);
      if (state.compare.any_terms) body.set("any_terms", state.compare.any_terms);
      if (state.compare.exclude_terms) body.set("exclude_terms", state.compare.exclude_terms);
      const resp = await fetch("/api/data-lake/quota/compare", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded; charset=utf-8" },
        body: body.toString(),
      });
      if (!resp.ok) {
        // 422 等带 {detail: ...} 的错误
        let detail = "HTTP " + resp.status;
        try {
          const j = await resp.json();
          if (j && j.detail) {
            if (typeof j.detail === "string") {
              detail = j.detail;
            } else if (j.detail.message) {
              detail = j.detail.message;
              if (j.detail.code) detail = `[${j.detail.code}] ${detail}`;
            } else {
              detail = JSON.stringify(j.detail);
            }
          }
        } catch (_) {}
        state.compare.error = detail;
        state.compare.submitting = false;
        renderCompareModal();
        refreshIcons();
        return;
      }
      // 200 OK → xlsx 字节流
      const blob = await resp.blob();
      const total = resp.headers.get("X-Compare-Total") || "?";
      const filename = parseContentDispositionFilename(resp.headers.get("Content-Disposition"))
        || `${keyword}_跨省对比.xlsx`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // 给浏览器一点时间触发下载再回收
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      state.compare.submitting = false;
      state.compare.error = "";
      // 关闭 modal + toast
      closeCompareModal();
      setToast(`对比完成：${total} 条命中 → ${filename}`);
    } catch (e) {
      state.compare.error = (e && e.message) || "网络错误";
      state.compare.submitting = false;
      renderCompareModal();
      refreshIcons();
    }
  }

  function parseContentDispositionFilename(header) {
    if (!header) return null;
    // RFC 5987: filename*=UTF-8''...
    const rfc5987 = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(header);
    if (rfc5987) {
      try {
        return decodeURIComponent(rfc5987[1]);
      } catch (_) {}
    }
    // fallback: filename="..."
    const ascii = /filename\s*=\s*"([^"]+)"/i.exec(header);
    if (ascii) return ascii[1];
    return null;
  }

  // 后端契约见 web-frontend/SPEC.md §5.10：DELETE /api/data-lake/quota/archives/{id}
  // 点击即提交；归档态不可恢复的提示由弹窗承担，UI 不再要求输入标题
  function submitDeleteArchive() {
    var d = state.deleteArchive || {};
    if (!d.open || d.submitting) return;
    if (!d.archiveId) {
      closeDeleteArchiveModal();
      return;
    }
    state.deleteArchive.submitting = true;
    state.deleteArchive.error = "";
    renderDeleteArchiveModal();
    refreshIcons();
    var archiveId = d.archiveId;
    var p = (state.api && state.api.deleteArchive)
      ? state.api.deleteArchive(archiveId)
      : Promise.resolve({ status: "error", error: "deleteArchive API 未配置" });
    Promise.resolve(p).then(function (resp) {
      if (resp && resp.status === "ready" && !resp.error) {
        closeDeleteArchiveModal();
        // 删完跳回档案列表
        state.view = "archives";
        reloadArchives();
      } else {
        state.deleteArchive.submitting = false;
        state.deleteArchive.error = (resp && resp.error) || "删除失败，请重试";
        renderDeleteArchiveModal();
        refreshIcons();
      }
    }).catch(function (e) {
      state.deleteArchive.submitting = false;
      state.deleteArchive.error = (e && e.message) || "删除请求异常";
      renderDeleteArchiveModal();
      refreshIcons();
    });
  }

  // 提交删除（POST /api/data-lake/quota/archives/{id}/parse/delete）
  // 后端契约见 web-frontend/SPEC.md §5.8（v0.4 强化版）
  // 点击即提交，弹窗内无输入项
  function submitDeleteParse() {
    var d = state.deleteParse || {};
    if (!d.open || d.submitting) return;
    if (!d.archiveId) {
      closeDeleteParseModal();
      return;
    }
    state.deleteParse.submitting = true;
    state.deleteParse.error = "";
    renderDeleteParseModal();
    refreshIcons();
    var archiveId = d.archiveId;
    var scope = d.scope || "all";
    var p = (state.api && state.api.deleteParseResult)
      ? state.api.deleteParseResult(archiveId, scope)
      : Promise.resolve({ status: "error", error: "deleteParseResult API 未配置" });
    Promise.resolve(p).then(function (resp) {
      // 成功：resp.status === "ready" && !resp.error
      if (resp && resp.status === "ready" && !resp.error) {
        closeDeleteParseModal();
        // 刷新列表
        reloadArchives();
      } else {
        state.deleteParse.submitting = false;
        state.deleteParse.error = (resp && resp.error) || "删除失败，请重试";
        renderDeleteParseModal();
        refreshIcons();
      }
    }).catch(function (e) {
      state.deleteParse.submitting = false;
      state.deleteParse.error = (e && e.message) || "删除请求异常";
      renderDeleteParseModal();
      refreshIcons();
    });
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

  // v0.8 解析状态区块：依据 archive.parse_status 渲染提示条
  // - skipped_no_parser → 黄色警告条：未配置解析脚本，需联系管理员接入或手工上传 reviewed.xlsx
  // - failed_user / failed_permanent → 红色错误条：附 parse_error_message
  // - parsing → loading 条
  // - parsed / qa_passed / usable → 绿色完成条
  // - pending (或空) → 不渲染（与已存在的「未解析」徽章重复）
  function renderParseStatusSection(archive) {
    var st = archive && archive.parse_status;
    if (!st) return "";
    var warnings = Array.isArray(archive.parse_warnings) ? archive.parse_warnings : [];

    var block = "";
    if (st === "skipped_no_parser") {
      var npWarn = warnings.find(function (w) { return w && w.code === "no_parser_for_province"; });
      var detail = npWarn ? npWarn.detail : "未在 extractors/{province}/ 找到对应解析脚本";
      block = '<div class="quota-warning quota-warning--no-parser">' +
        '<strong><i data-lucide="alert-triangle"></i>未配置解析脚本</strong>' +
        '<p>档案已入库（'+ escapeHtml(archive.title || "（未命名）") +'），但 worker 找不到 extractors/'+ escapeHtml(npWarn ? npWarn.province : "?") +'/ 下的解析脚本，不会自动解析。</p>' +
        '<p class="quota-filter-note">'+ escapeHtml(detail) +'</p>' +
        '<p class="quota-filter-note">可选：联系管理员接入 extractor；或手工上传 reviewed.xlsx 走审核流程。</p>' +
        '</div>';
    } else if (st === "failed_user" || st === "failed_permanent") {
      var msg = archive.parse_error_message || archive.parse_error_code || "未提供错误详情";
      block = '<div class="quota-warning quota-warning--error">' +
        '<strong><i data-lucide="alert-circle"></i>解析失败</strong>' +
        '<p>'+ escapeHtml(msg) +'</p>' +
        '</div>';
    } else if (st === "parsing") {
      block = '<div class="quota-warning quota-warning--info">' +
        '<strong><i data-lucide="loader"></i>正在解析</strong>' +
        '<p>worker 已领取任务，结果将写入 candidate.xlsx。</p>' +
        '</div>';
    } else if (st === "parsed" || st === "candidate_ready" || st === "qa_passed" || st === "usable") {
      var finishedAt = archive.parse_finished_at || "";
      var ver = archive.parse_parser_version || "";
      block = '<div class="quota-warning quota-warning--success">' +
        '<strong><i data-lucide="check-circle"></i>解析完成</strong>' +
        '<p>状态: '+ escapeHtml(st) + (finishedAt ? ' · 完成于 '+ escapeHtml(finishedAt) : "") +'</p>' +
        (ver ? '<p class="quota-filter-note">parser_version: '+ escapeHtml(ver) +'</p>' : "") +
        '</div>';
    }
    if (!block) return "";
    return '<section class="quota-archive-detail-section"><h3>解析状态</h3>'+ block +'</section>';
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
      ${renderParseStatusSection(archive)}
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
    // 2026-08-20: focus 保留 — render() 用 shell.innerHTML 重建整 shell，原本 focused
    // 的输入框（典型：顶部 #quotaSearch）会被新元素替换 → 丢光标，用户必须重新点击
    // 搜索栏才能继续输入「2024」之类的连续字符。render 前捕获 activeElement.id +
    // selectionStart/End，render 后按同 id 找回来 refocus。不在 shell 内的 modal 输入
    // 不被这次 innerHTML 销毁，focus 自然保留。
    const prevActive = document.activeElement;
    const prevId = prevActive && prevActive.id ? prevActive.id : null;
    const prevSelStart =
      prevActive && typeof prevActive.selectionStart === "number" ? prevActive.selectionStart : null;
    const prevSelEnd =
      prevActive && typeof prevActive.selectionEnd === "number" ? prevActive.selectionEnd : null;

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
    renderDeleteParseModal();
    renderDeleteArchiveModal();
    renderCompareModal();
    refreshIcons();
    // 2026-08-17: 覆盖矩阵每次重渲染后都要重新绑 tooltip（事件委托已经挂在外层，但
    // matrix DOM 整体替换了。Tooltip 容器的创建只需一次，由 ensureCoverageTooltipNode 完成。
    bindCoverageTooltipEvents();

    // 恢复 focus（仅在 focused 元素是带 id 的输入框时生效；普通 button / body 跳过）
    if (prevId) {
      const newActive = document.getElementById(prevId);
      if (newActive && typeof newActive.focus === "function") {
        try {
          newActive.focus({ preventScroll: true });
          if (prevSelStart !== null) newActive.selectionStart = prevSelStart;
          if (prevSelEnd !== null) newActive.selectionEnd = prevSelEnd;
        } catch (_e) {
          // readonly / 不支持 selection 的元素（如 contenteditable 边界），吞掉异常
        }
      }
    }
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
    q: { kind: "string" },                 // 顶部搜索框：内部 state 用，URL 出口走 search_all
    search_all: { kind: "string-list" },   // 2026-08-20: AND-tokenized（四川 园林 → [四川, 园林]）
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

      // 2026-08-20: string-list 类型（search_all）—— 逐元素 trim + 哨兵丢弃
      if (spec.kind === "string-list") {
        if (!Array.isArray(v)) return;
        const arr = [];
        v.forEach((item) => {
          if (typeof item !== "string") return;
          const trimmed = item.trim();
          if (trimmed === "" || trimmed === "all") return;
          arr.push(trimmed);
        });
        if (arr.length > 0) out[k] = arr;
        return;
      }

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
      const value = safe[k];
      // 2026-08-20: search_all 是 string-list，URL 上要重复发 ?search_all=t1&search_all=t2
      // —— set() 会拼成 "t1,t2" 单参，后端 FastAPI 把它当成一个 token 字符串，
      // 不是预期的 list[str]。append() 才会发出多个同名参数。
      if (Array.isArray(value)) {
        value.forEach((item) => params.append(k, String(item)));
      } else {
        params.set(k, String(value));
      }
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

  // 2026-08-17: 覆盖矩阵懒加载。coverage 矩阵是派生数据，仅在用户切到该 tab 时拉。
  // 失败时由 renderCoverageMatrixView 展示 fallback（不要把整个页面挡住）。
  //
  // v0.9.4 (2026-08-19): 加 bookCategory 参数 + 多类缓存.
  //   - 切换 bookCategory 时复用 cache[bookCategory], 避免重复请求.
  //   - 同一 bookCategory 已 READY 则直接 hydrate, 跳过网络.
  async function loadCoverageMatrix(bookCategory) {
    if (!state.api || !state.flags.coverage) return;
    var cat = bookCategory || state.coverage.bookCategory || COVERAGE_DEFAULT_BOOK_CATEGORY;
    // 同步当前激活 sub-tab (前端以 state.coverage.bookCategory 为准)
    state.coverage.bookCategory = cat;
    // 已有缓存 → 直接 hydrate 到顶层 + render
    var cached = state.coverage.cache[cat];
    if (cached) {
      state.coverage.status = cached.status;
      state.coverage.data = cached.data;
      state.coverage.error = cached.error || "";
      if (state.active) render();
      return;
    }
    // 5 态没法原生表达"加载中",复用 UNKNOWN 标记 + 重新 render 触发空骨架
    state.coverage.status = CAP.UNKNOWN;
    state.coverage.data = null;
    state.coverage.error = "";
    if (state.active) render();
    const r = await state.api.getCoverageMatrix(cat);
    var entry = { status: r.status, data: r.data, error: r.error || "" };
    state.coverage.cache[cat] = entry;
    // 只在当前激活 sub-tab 还是 cat 时回写顶层 (避免 stale 回填)
    if (state.coverage.bookCategory === cat) {
      state.coverage.status = entry.status;
      state.coverage.data = entry.data;
      state.coverage.error = entry.error || "";
    }
    if (state.active) render();
  }

  // v0.9.4: 切换覆盖矩阵 sub-tab (3 类资料各一张). 委托给 quota-ui.js 的 action dispatcher.
  function selectCoverageBookCategory(bookCategory) {
    if (!bookCategory || bookCategory === state.coverage.bookCategory) return;
    state.coverage.bookCategory = bookCategory;
    loadCoverageMatrix(bookCategory);
  }

  // 2026-08-17: 覆盖矩阵视图（行=年份，列=省份，cell=档案数+hover 详情）。
  // 复用 .coverage-workbench / .coverage-year-month-table styles.css 已有类
  // (信息价覆盖矩阵同款)，避免重复定义。
  //
  // v0.9.4 (2026-08-19): 按 book_category 拆分, 头部加 3 个 sub-tab; 行轴 axis-agnostic
  //   (省份 vs 专业), cell 上的 data-province → data-row, 内部 row_name 替代 province_name.
  function renderCoverageMatrixView() {
    // 能力未就绪（features.coverage=unavailable / 401 / 404）走占位
    if (!state.flags.coverage) {
      return `
        <section class="quota-view-card">
          <div class="quota-empty">
            <i data-lucide="table-2"></i>
            <strong>覆盖矩阵待接入</strong>
            <span>${escapeHtml(capReason("coverage"))}</span>
          </div>
        </section>`;
    }

    // v0.9.4: sub-tab 行 (3 类资料各一张矩阵).
    var activeCat = state.coverage.bookCategory || COVERAGE_DEFAULT_BOOK_CATEGORY;
    var subTabsHtml = `
      <div class="filter-chip-group coverage-subtabs" role="tablist" aria-label="覆盖矩阵资料分类">
        ${COVERAGE_BOOK_CATEGORIES.map(function (opt) {
          var active = opt.value === activeCat ? " active" : "";
          return `<button class="filter-chip${active}" type="button" role="tab"
            aria-selected="${opt.value === activeCat ? "true" : "false"}"
            data-quota-action="coverage-tab:${opt.value}">${escapeHtml(opt.label)}</button>`;
        }).join("")}
      </div>`;

    // 加载中 / 失败 / 就绪 分别走不同视图
    if (state.coverage.status === CAP.UNKNOWN) {
      return `
        <section class="coverage-matrix-card" aria-label="定额覆盖矩阵">
          <div class="coverage-workbench">
            ${subTabsHtml}
            <div class="quota-empty">
              <i data-lucide="loader-circle"></i>
              <strong>正在加载覆盖矩阵</strong>
              <span>汇总当前定额的覆盖信息…</span>
            </div>
          </div>
        </section>`;
    }
    if (state.coverage.status !== CAP.READY || !state.coverage.data) {
      const msg = state.coverage.error || capReason("coverage");
      return `
        <section class="coverage-matrix-card" aria-label="定额覆盖矩阵">
          <div class="coverage-workbench">
            ${subTabsHtml}
            <div class="quota-empty">
              <i data-lucide="alert-triangle"></i>
              <strong>覆盖矩阵加载失败</strong>
              <span>${escapeHtml(msg)}</span>
              <button class="secondary-button" type="button" data-quota-action="coverage-retry">重试</button>
            </div>
          </div>
        </section>`;
    }

    const data = state.coverage.data;
    const rowLabel = data.row_label || "省份";
    const axis = data.axis || "province";
    const years = data.years || [];
    const rows = data.rows || [];
    const cells = data.cells || {};
    const summary = data.summary || {};
    const totalArchives = summary.total_archives || 0;
    const unknownYearCount = summary.unknown_year_count || 0;

    // axis-agnostic 表头：第一列 rowLabel, 其后为年份列表
    const headHtml = `
      <thead>
        <tr>
          <th scope="col" class="coverage-province-head">${escapeHtml(rowLabel)} \\ 年份</th>
          ${years.map((y) => `<th scope="col">${escapeHtml(y)}</th>`).join("")}
        </tr>
      </thead>`;

    // 空数据 (例如 boq_standard 暂无档案) → 显示 empty hint
    if (!rows.length) {
      return `
        <section class="coverage-matrix-card" aria-label="定额覆盖矩阵">
          <div class="coverage-workbench">
            ${subTabsHtml}
            <header class="coverage-workbench-header">
              <div class="coverage-workbench-title">
                <span class="section-marker"></span>
                <strong>${escapeHtml(rowLabel)} × 年份 覆盖矩阵</strong>
              </div>
            </header>
            <div class="quota-empty">
              <i data-lucide="table-2"></i>
              <strong>该分类暂无档案</strong>
              <span>当前 book_category 暂无入湖档案, 矩阵为空。</span>
            </div>
          </div>
        </section>`;
    }

    // 每行: 一个 row (province 或 industry)
    const bodyRows = rows.map((p) => {
      const cellsHtml = years.map((year) => {
        const cell = cells[`${year}|${p.code}`] || cells[`${year}|${p.code || ""}`] || null;
        if (!cell) {
          return `<td class="coverage-cell coverage-cell--empty" aria-label="无档案"></td>`;
        }
        // 解析状态 → 5 态变体颜色（复用 PARSE_STATUS_VARIANT）。多状态时取"最差"（按解析深度）
        const variant = statusVariantForCells(cell.parse_statuses || {});
        return `
          <td class="coverage-cell"
              data-has-data="1"
              data-year="${escapeHtml(year)}"
              data-row="${escapeHtml(p.code || "")}"
              data-variant="${variant}"
              tabindex="0"
              aria-label="${escapeHtml(p.name || "未知")} ${escapeHtml(year)} 共 ${cell.archive_count || 0} 份档案">
            <span class="coverage-cell-count">${cell.archive_count || 0}</span>
          </td>`;
      }).join("");
      return `
        <tr>
          <th scope="row" title="${escapeHtml(p.code)}">${escapeHtml(p.name)}</th>
          ${cellsHtml}
        </tr>`;
    }).join("");

    return `
      <section class="coverage-matrix-card" aria-label="定额覆盖矩阵">
        <div class="coverage-workbench">
          <header class="coverage-workbench-header">
            <div class="coverage-workbench-title">
              <span class="section-marker"></span>
              <strong>${escapeHtml(rowLabel)} × 年份 覆盖矩阵</strong>
            </div>
          </header>
          ${subTabsHtml}
          <div class="coverage-workbench-summary">
            <span>${escapeHtml(rowLabel)} <strong>${rows.length}</strong></span>
            <span aria-hidden="true">·</span>
            <span>年份跨度 <strong>${years.length - (unknownYearCount > 0 ? 1 : 0)}</strong></span>
            <span aria-hidden="true">·</span>
            <span>无年份 <strong>${unknownYearCount}</strong></span>
            <span aria-hidden="true">·</span>
            <span>档案总数 <strong>${totalArchives}</strong></span>
          </div>
          <div class="coverage-region-meta">
            <span>悬停单元格查看该 (${escapeHtml(rowLabel)} × 年份) 下的档案清单；按 <kbd>Tab</kbd> 可逐格聚焦。</span>
          </div>
          <table class="coverage-year-month-table">
            ${headHtml}
            <tbody>
              ${bodyRows}
            </tbody>
          </table>
        </div>
      </section>`;
  }

  // 2026-08-17: 单元格颜色 — 多种 parse_status 取「最差」变体。
  // 解析进度等序（差→好）: pending < parsing < review < done < failed (异常用 red)
  // 实际大多数 cell 不会有 mixed 状态，但有也不会乱。
  const _STATUS_VARIANT_PRIORITY = ["pending", "parsing", "review", "done", "failed"];
  function statusVariantForCells(statuses) {
    const keys = Object.keys(statuses || {});
    if (!keys.length) return "pending";
    // 命中：选最低优先度（解析最浅）。未命中 → pending
    let best = "pending";
    for (const sv of _STATUS_VARIANT_PRIORITY) {
      for (const k of keys) {
        const v = PARSE_STATUS_VARIANT[k] || "pending";
        if (v === sv) { best = sv; break; }
      }
      if (best !== "pending") break;
    }
    return best;
  }

  // 2026-08-17: 自定义 hover tooltip（替代 native title — 后者延迟 1-2s + 多行换行浏览器不一致）。
  // 在 body 末尾创建一个 portal 节点；事件委托在覆盖矩阵容器上，hover 进入 data-has-data="1"
  // 的 cell 即弹出，列出 archive_titles + 状态分布 + 计数。
  function ensureCoverageTooltipNode() {
    let node = document.querySelector(".quota-coverage-tooltip");
    if (node) return node;
    node = document.createElement("div");
    node.className = "quota-coverage-tooltip";
    node.setAttribute("role", "tooltip");
    node.hidden = true;
    if (typeof document.body !== "undefined") document.body.appendChild(node);
    return node;
  }

  function renderCoverageTooltipInner(cellData) {
    // cellData: { year, row_name, archive_count, archive_titles, archive_statuses, parse_statuses }
    //   v0.9.4: row_name 替代 province_name (axis-agnostic; "湖北" / "煤炭工程" / "未知")
    //   v0.9.5: archive_statuses = [{title, parse_status}, ...]，与 archive_titles 一一对应，按 title 排序
    const titles = (cellData && cellData.archive_titles) || [];
    const archiveEntries = (cellData && cellData.archive_statuses) || [];
    const statuses = (cellData && cellData.parse_statuses) || {};
    const rowName = (cellData && (cellData.row_name != null ? cellData.row_name : cellData.province_name)) || "未知";

    // 2026-08-18: parse_status 分布按 5 态变体聚合，与档案列表徽章对齐（同时供小圆点 + 底部汇总共用）。
    const variantLabelMap = Object.freeze({
      pending: "未解析",
      parsing: "解析中",
      review:  "待审核",
      done:    "已完成",
      failed:  "解析失败",
    });

    // 2026-08-18 (v0.9.5): 每条档案前的状态小圆点 — 把 archive_statuses 按 title 建索引，
    //   每条 title 旁边渲染一个 data-variant 颜色点（不带文字，色图例看底部汇总）。
    //   没有 archive_statuses 的旧 cell（防御性兜底）就只渲染 title 不带点。
    const statusByTitle = new Map();
    archiveEntries.forEach((entry) => {
      if (entry && entry.title) statusByTitle.set(entry.title, entry.parse_status || "unknown");
    });
    const titleHtml = titles.length
      ? titles.map((t) => {
          const rawStatus = statusByTitle.has(t) ? statusByTitle.get(t) : null;
          const variant = rawStatus ? (PARSE_STATUS_VARIANT[rawStatus] || "pending") : null;
          const dot = variant
            ? `<span class="quota-tooltip-archive-dot" data-variant="${escapeHtml(variant)}" title="${escapeHtml(variantLabelMap[variant] || variant)}" aria-label="${escapeHtml(variantLabelMap[variant] || variant)}"></span>`
            : "";
          return `<li>${dot}<span class="quota-tooltip-archive-name">${escapeHtml(t)}</span></li>`;
        }).join("")
      : `<li class="quota-coverage-tooltip-empty">无档案标题</li>`;

    const variantCounts = { pending: 0, parsing: 0, review: 0, done: 0, failed: 0 };
    Object.keys(statuses).forEach((rawKey) => {
      const variant = PARSE_STATUS_VARIANT[rawKey] || "pending";
      variantCounts[variant] = (variantCounts[variant] || 0) + (statuses[rawKey] | 0);
    });
    // 5 态顺序固定（按解析进度浅→深+失败）
    const statusHtml = ["pending", "parsing", "review", "done", "failed"]
      .filter((v) => variantCounts[v] > 0)
      .map((v) => `<span class="quota-coverage-tooltip-pill">${escapeHtml(variantLabelMap[v])} <em>${escapeHtml(String(variantCounts[v]))}</em></span>`)
      .join("");
    return `
      <header class="quota-coverage-tooltip-head">
        <strong>${escapeHtml(cellData.year)} · ${escapeHtml(rowName)}</strong>
        <span class="quota-coverage-tooltip-count">${escapeHtml(String(cellData.archive_count || 0))} 份档案</span>
      </header>
      <ul class="quota-coverage-tooltip-list">${titleHtml}</ul>
      ${statusHtml ? `<footer class="quota-coverage-tooltip-foot">${statusHtml}</footer>` : ""}
    `;
  }

  function showCoverageTooltip(cellEl, cellData) {
    const node = ensureCoverageTooltipNode();
    if (!node) return;
    node.innerHTML = renderCoverageTooltipInner(cellData);
    node.hidden = false;
    // 默认位置：单元格右侧。溢出视口时翻转；底部超出往上。
    const rect = cellEl.getBoundingClientRect();
    const tipRect = node.getBoundingClientRect();
    const margin = 8;
    let left = rect.right + window.scrollX + margin;
    let top = rect.top + window.scrollY;
    if (rect.right + tipRect.width + margin > window.innerWidth) {
      left = rect.left + window.scrollX - tipRect.width - margin;
    }
    if (left < margin) left = margin;
    if (rect.top + tipRect.height + margin > window.innerHeight) {
      top = window.scrollY + window.innerHeight - tipRect.height - margin;
    }
    if (top < window.scrollY + margin) top = window.scrollY + margin;
    node.style.left = left + "px";
    node.style.top = top + "px";
  }

  function hideCoverageTooltip() {
    const node = document.querySelector(".quota-coverage-tooltip");
    if (node) node.hidden = true;
  }

  // 事件委托到覆盖矩阵容器：mouseover / focus 进入带数据的 cell 即弹；mouseout / blur 隐藏。
  // 用 [_bound] 标记避免 render() 每次重新渲染后重复挂监听。
  //
  // 2026-08-18 修复：原来用 document.querySelector(".coverage-matrix-card")，但 index.html
  // 里同时存在信息价覆盖矩阵的 <section id="coverageMatrixCard" class="coverage-matrix-card">，
  // DOM 顺序在 #quotaShell 之前。quota 域切换时只是给信息价加 hidden 属性（不删元素），
  // querySelector 返回第一个匹配 → 信息价的（隐藏的）卡片。结果 quota 覆盖矩阵的 hover
  // 事件其实没绑上去，CSS hover 样式还能触发（box-shadow + 蓝色背景），但 JS tooltip
  // 永远不弹。修复：限定到 #quotaShell .coverage-matrix-card。
  function bindCoverageTooltipEvents() {
    const matrix = document.querySelector("#quotaShell .coverage-matrix-card");
    if (!matrix) return;
    if (matrix._tooltipBound) return;
    matrix._tooltipBound = true;
    // v0.9.4: cell 上挂的是 data-row (axis-agnostic), 不是 data-province.
    matrix.addEventListener("mouseover", function (event) {
      const cell = event.target.closest('td.coverage-cell[data-has-data="1"]');
      if (!cell || !state.coverage.data || !state.coverage.data.cells) return;
      const key = cell.dataset.year + "|" + cell.dataset.row;
      const cellData = state.coverage.data.cells[key];
      if (cellData) showCoverageTooltip(cell, cellData);
    });
    matrix.addEventListener("mouseout", function (event) {
      if (event.target.closest('td.coverage-cell[data-has-data="1"]')) hideCoverageTooltip();
    });
    matrix.addEventListener("focusin", function (event) {
      const cell = event.target.closest('td.coverage-cell[data-has-data="1"]');
      if (!cell || !state.coverage.data || !state.coverage.data.cells) return;
      const key = cell.dataset.year + "|" + cell.dataset.row;
      const cellData = state.coverage.data.cells[key];
      if (cellData) showCoverageTooltip(cell, cellData);
    });
    matrix.addEventListener("focusout", function (event) {
      if (event.target.closest('td.coverage-cell[data-has-data="1"]')) hideCoverageTooltip();
    });
  }

  // ── 列表筛选参数构造 + 仅列表的轻量重载 ───────────────────────────────
  function currentArchiveFilters() {
    const f = state.filters || {};
    // 注意：loadQuotaArchivesGeneric 实际请求的是 /api/archives?domain_type=quota&...
    // （line 2156 直接 global.fetch，绕过 quota-api.js 的 QUOTA_API_BASE 前缀），
    // 该端点（main.py:1217）签名是 search= + search_all=（2026-08-20 新增）。
    //
    // 顶部搜索框发 search_all=（AND-tokenized）：「四川 园林」→ [四川, 园林] →
    // ?search_all=四川&search_all=园林 → 后端对每个 token 做
    // (title LIKE OR business_key LIKE)，token 之间 AND，等价于「同时包含」。
    // 单 token 行为不退化（只发一个同名参数也合法）。其他域继续走 search 单 substring，
    // 不影响 app.js:8217 的 globalSearch。
    const params = {
      primary: f.primary && f.primary !== "all" ? f.primary : undefined,
      edition_year: f.editionYear && f.editionYear !== "all" ? f.editionYear : undefined,
      edition_label: f.edition && f.edition !== "all" ? f.edition : undefined,
      discipline_code: f.discipline && f.discipline !== "all" ? f.discipline : undefined,
    };
    // AND-tokenized search：按任意空白拆（split(/\s+/) 自动去前后空 + 合并连续空）
    const q = (f.q || "").trim();
    if (q) {
      const tokens = q.split(/\s+/).filter(Boolean);
      if (tokens.length > 0) params.search_all = tokens;
    }
    if (f.secondary && f.secondary !== "all") {
      // v0.9.3: 清单规范 secondary 也用 "jurisdiction", 与建筑工程定额同走 jurisdiction_code
      if (f.primary === "construction_quota" || f.primary === "boq_standard") params.jurisdiction_code = f.secondary;
      else if (f.primary === "industry_quota") params.industry_sector_code = f.secondary;
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
    state.upload = { open: true, files: [], category: "", province: "", industry_sector_code: "", year: "", submitting: false };
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

  // v0.9 (2026-08-18): 校验完整性函数。3 类资料 (boq/construction/industry) 必填字段不同。
  //   - boq_standard         : province required
  //   - construction_quota   : province required
  //   - industry_quota       : industry_sector_code required, province 占位
  function isUploadReady(u) {
    if (!u) return false;
    if (!(u.files || []).length) return false;
    if (!u.category) return false;
    var yearNum = parseInt(u.year, 10);
    if (!u.year || isNaN(yearNum) || yearNum < 1900 || yearNum > 2100) return false;
    if (u.submitting) return false;
    var cfg = UPLOAD_CATEGORY_FIELDS[u.category];
    if (!cfg) return false;
    if (cfg.needsProvince && !u.province) return false;
    if (cfg.needsIndustry && !u.industry_sector_code) return false;
    // 互斥: 非 industry 类型不能带 industry_sector_code (后端会 422 UNEXPECTED_INDUSTRY)
    if (!cfg.needsIndustry && u.industry_sector_code) return false;
    return true;
  }

  // 轻量：只刷新提交按钮 disabled（不重建表单，避免 input 失焦）
  // 注意: 2026-07-29 我们去掉了 disabled 属性,
  // 因为 disabled 会吞掉 click 事件, 用户点了没反应又看不到原因。
  // 现在 submit 按钮永远可点击, 缺字段由 submitUpload 内 setToast 告知。
  function renderUploadSubmitButton() {
    var btn = document.querySelector("[data-upload-submit='1']");
    if (!btn) return;
    var canSubmit = isUploadReady(state.upload || {});
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
    // v0.9: category 决定 province/industry 字段可见性。未选 category → 不渲染 province/industry。
    //   - boq_standard + construction_quota → 显示 province (含 nat=全国), industry 不显示
    //   - industry_quota                  → 显示 industry_sector_code, province 不显示
    var catCfg = u.category ? UPLOAD_CATEGORY_FIELDS[u.category] : null;
    var canSubmit = isUploadReady(u);

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

    // 动态块: 哪个 category 适用哪个, 互斥。
    var provinceBlock = "";
    var industryBlock = "";
    if (catCfg && catCfg.needsProvince) {
      provinceBlock =
        '<div class="quota-simple-field">' +
          '<label>' + escapeHtml(catCfg.provinceLabel) + ' <span class="quota-required-marker" aria-label="必填">*</span></label>' +
          '<select class="quota-field-input" data-qfield="upload.province">' +
            renderUploadProvinceOptions(u.province) +
          '</select>' +
        '</div>';
    }
    if (catCfg && catCfg.needsIndustry) {
      industryBlock =
        '<div class="quota-simple-field">' +
          '<label>专业 <span class="quota-required-marker" aria-label="必填">*</span></label>' +
          '<select class="quota-field-input" data-qfield="upload.industry_sector_code">' +
            renderUploadIndustryOptions(u.industry_sector_code) +
          '</select>' +
        '</div>';
    }

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
                <label>资料分类 <span class="quota-required-marker" aria-label="必填">*</span></label>
                <select class="quota-field-input" data-qfield="upload.category">
                  ${categoryOptions.map(function (o) {
                    return '<option value="' + escapeHtml(o.value) + '"' + (o.value === u.category ? ' selected' : '') + '>' + escapeHtml(o.label) + '</option>';
                  }).join("")}
                </select>
              </div>
              <div class="quota-simple-field">
                <label>年份 <span class="quota-required-marker" aria-label="必填">*</span></label>
                <input type="number" class="quota-field-input" data-qfield="upload.year"
                  value="${escapeHtml(u.year || "")}"
                  placeholder="如 2026" min="1900" max="2100" step="1" />
              </div>
            </div>
            ${(provinceBlock || industryBlock) ? `
            <div class="quota-simple-row">
              ${provinceBlock}
              ${industryBlock}
            </div>
            ` : ""}
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

    // v0.9: 按 category 分流必填字段 (boq/construction → province, industry → industry_sector_code)
    var catCfg = u.category ? UPLOAD_CATEGORY_FIELDS[u.category] : null;
    // 一次性收集所有缺失字段
    var missing = [];
    if (effectiveFiles.length === 0) missing.push("文件");
    if (!u.category) missing.push("分类");
    if (catCfg && catCfg.needsProvince && !u.province) missing.push("省份");
    if (catCfg && catCfg.needsIndustry && !u.industry_sector_code) missing.push("专业");
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
    // industry_quota: province 占位, 后端允许不传; 非 industry: 必传 province。
    // 一律 append (空字符串也行): 后端 _UPLOAD_PROVINCE_MAP 校验只看 sent value。
    formData.append("province", u.province || "");
    formData.append("year", String(yearNum));
    if (catCfg && catCfg.needsIndustry && u.industry_sector_code) {
      formData.append("industry_sector_code", u.industry_sector_code);
    }

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
      // 2026-08-17: 覆盖矩阵懒加载。切到 coverage tab 时如果还没拉过，触发加载
      if (state.view === "coverage" && state.flags.coverage && state.coverage.status !== CAP.READY) {
        loadCoverageMatrix();
      }
      return;
    }
    if (action === "back-to-archives") {
      state.view = "archives";
      state.archiveDetail = { status: CAP.UNKNOWN, data: null, archiveId: null, error: "" };
      state.addMenuOpen = false;
      render();
      return;
    }
    // 2026-08-17: 覆盖矩阵加载失败时，按钮触发重试
    if (action === "coverage-retry") {
      loadCoverageMatrix();
      return;
    }
    // v0.9.4 (2026-08-19): 覆盖矩阵 sub-tab 切换 (3 类资料各一张)
    if (action.indexOf("coverage-tab:") === 0) {
      selectCoverageBookCategory(action.slice("coverage-tab:".length));
      return;
    }
    // ── ⋯ 下拉：点击触发按钮展开/收起 ──
    if (action === "dropdown-toggle") {
      var ddArchiveId = (el && el.dataset && el.dataset.archiveId) || "";
      var wasOpen = state.dropdown.open && state.dropdown.archiveId === ddArchiveId;
      // 切换：点同一行收起；点不同行直接跳
      if (wasOpen) {
        state.dropdown = { open: false, archiveId: "", variant: "pending" };
      } else {
        var ddRow = (state.archives.data || []).find(function (r) {
          return encodeURIComponent(r.archive_id || "") === ddArchiveId;
        });
        state.dropdown = {
          open: true,
          archiveId: ddArchiveId,
          variant: ddRow ? resolveUiStatus(ddRow) : "pending",
        };
      }
      render();
      return;
    }
    // ── 解析动作（行级 / 下拉共用）───────────────────────────────────
    if (action === "parse-trigger") {
      var ptId = (el && el.dataset && el.dataset.archiveId) || "";
      if (ptId) {
        document.dispatchEvent(new CustomEvent("quota:parse-trigger", {
          detail: { archiveId: decodeURIComponent(ptId) }
        }));
      }
      // 点完关下拉
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      render();
      return;
    }
    if (action === "parse-download-candidate" || action === "parse-download-final" ||
        action === "parse-show-manifest" || action === "parse-show-qa") {
      var pdId = (el && el.dataset && el.dataset.archiveId) || "";
      if (pdId) {
        document.dispatchEvent(new CustomEvent("quota:parse-action", {
          detail: { action: action, archiveId: decodeURIComponent(pdId) }
        }));
      }
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      render();
      return;
    }
    if (action === "parse-upload-reviewed") {
      var puId = (el && el.dataset && el.dataset.archiveId) || "";
      if (puId) {
        document.dispatchEvent(new CustomEvent("quota:parse-upload-reviewed", {
          detail: { archiveId: decodeURIComponent(puId) }
        }));
      }
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      render();
      return;
    }
    // ── 危险操作：撤回审核 (done-only, scope=reviewed_only) ──────────
    if (action === "parse-revert-reviewed") {
      var pdRevId = (el && el.dataset && el.dataset.archiveId) || "";
      var pdRevRow = (state.archives.data || []).find(function (r) {
        return encodeURIComponent(r.archive_id || "") === pdRevId;
      });
      var pdRevTitle = pdRevRow ? (pdRevRow.title || "") : "";
      var pdRevRealId = pdRevId ? decodeURIComponent(pdRevId) : "";
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      openDeleteParseModal(pdRevRealId, pdRevTitle, "reviewed_only");
      return;
    }
    // ── 危险操作：删除全部解析结果（所有有产物的状态都支持, scope=all）────
    if (action === "parse-delete-all") {
      var pdDelId = (el && el.dataset && el.dataset.archiveId) || "";
      var pdDelRow = (state.archives.data || []).find(function (r) {
        return encodeURIComponent(r.archive_id || "") === pdDelId;
      });
      var pdDelTitle = pdDelRow ? (pdDelRow.title || "") : "";
      var pdDelRealId = pdDelId ? decodeURIComponent(pdDelId) : "";
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      openDeleteParseModal(pdDelRealId, pdDelTitle, "all");
      return;
    }
    // ── 危险操作：删除档案（#10，行 ⋯ 下拉入口；按 SPEC §3.1.4 与 #9 严格区分）──
    if (action === "archive-delete") {
      var adDelId = (el && el.dataset && el.dataset.archiveId) || "";
      var adDelRow = (state.archives.data || []).find(function (r) {
        return encodeURIComponent(r.archive_id || "") === adDelId;
      });
      var adDelTitle = adDelRow ? (adDelRow.title || "") : "";
      var adDelRealId = adDelId ? decodeURIComponent(adDelId) : "";
      state.dropdown = { open: false, archiveId: "", variant: "pending" };
      openDeleteArchiveModal(adDelRealId, adDelTitle);
      return;
    }
    if (action === "close-delete-parse") {
      closeDeleteParseModal();
      return;
    }
    if (action === "submit-delete-parse") {
      submitDeleteParse();
      return;
    }
    if (action === "close-delete-archive") {
      closeDeleteArchiveModal();
      return;
    }
    if (action === "submit-delete-archive") {
      submitDeleteArchive();
      return;
    }
    if (action === "open-compare-modal") {
      openCompareModal();
      return;
    }
    if (action === "close-compare-modal") {
      closeCompareModal();
      return;
    }
    if (action === "submit-compare") {
      submitCompare();
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
      if (state.filters.primary === "construction_quota" && state.flags.facets) {
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
      // v0.3.4 修复：slice(17) 是 off-by-one。"set-editionYear:" 是 16 字符，
      // slice(17) 会切掉首字符 → "2018" 变 "018" → Number("018")=18 → 后端查 edition_year=18 返 0 条，
      // 同时 selected==="018" 与 chip value "2018" 不匹配 → 无蓝色高亮。一个 bug，两个症状。
      var newYear = action.slice(16);
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
  // ── 顶部 search 框：IME composition 感知 + 后台 fetch ───────────────────
  // IME 合成期间如果 250ms 内停手（看候选词），debounce 触发 fetch，fetch 完成后的
  // render() 会销毁 <input> 元素 → IME composition 直接断（候选词消失、合成的
  // 拼音丢失）。所以加 composition 监听：合成期间只更新 state.filters.q、不触发
  // fetch；fetch 完成后若用户正在合成也不 render()。这样 IME 全程不被打断。
  let quotaSearchDebounceTimer = null;
  let quotaSearchIsComposing = false;

  function commitQuotaSearch() {
    if (quotaSearchDebounceTimer) {
      clearTimeout(quotaSearchDebounceTimer);
      quotaSearchDebounceTimer = null;
    }
    quotaSearchDebounceTimer = setTimeout(() => {
      quotaSearchDebounceTimer = null;
      if (quotaSearchIsComposing) return; // 又开新一轮合成，跳过本次 fetch
      if (!state.api || !state.flags.archives) return;
      const params = currentArchiveFilters();
      loadQuotaArchivesGeneric(params).then((result) => {
        state.archives = result;
        if (quotaSearchIsComposing) return; // fetch 期间用户又开始合成，跳过 render
        render();
      });
    }, 250);
  }

  // composition 事件冒泡，挂到 document 上即可。
  document.addEventListener("compositionstart", (e) => {
    if (e.target && e.target.id === "quotaSearch") {
      quotaSearchIsComposing = true;
      // 进入合成：取消尚未触发的 debounce（合成期间由 compositionend 触发）
      if (quotaSearchDebounceTimer) {
        clearTimeout(quotaSearchDebounceTimer);
        quotaSearchDebounceTimer = null;
      }
    }
  });

  document.addEventListener("compositionend", (e) => {
    if (e.target && e.target.id === "quotaSearch") {
      quotaSearchIsComposing = false;
      state.filters.q = e.target.value;
      commitQuotaSearch();
    }
  });

  function handleInput(event) {
    if (!state.active) return;
    const t = event.target;
    if (!t) return;

    // ── 顶部 search 框（IME 感知 + debounce，不走 reloadArchives）──────────
    if (t.id === "quotaSearch") {
      state.filters.q = t.value;
      if (quotaSearchIsComposing) return; // 等 compositionend 再触发
      commitQuotaSearch();
      return;
    }

    // 删除 modal 内已无输入项；input/change 事件只在上传弹窗与 compose 弹窗触发

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
        var prevCat = state.upload.category;
        state.upload.category = t.value;
        // v0.9: 切 category 时清空不再适用的字段, 避免遗留 province/industry_sector_code
        //   撞后端 422 校验 (UNEXPECTED_INDUSTRY / MISSING_PROVINCE)。
        var newCatCfg = UPLOAD_CATEGORY_FIELDS[state.upload.category];
        if (prevCat !== state.upload.category) {
          if (newCatCfg && !newCatCfg.needsProvince) state.upload.province = "";
          if (newCatCfg && !newCatCfg.needsIndustry) state.upload.industry_sector_code = "";
        }
        // 切到不同 category → 字段可见性变化 → 必须重渲染整个 modal
        renderUploadModal();
        return;
      }
      if (t.dataset && t.dataset.qfield === "upload.province") {
        state.upload.province = t.value;
        renderUploadSubmitButton();
        return;
      }
      if (t.dataset && t.dataset.qfield === "upload.industry_sector_code") {
        state.upload.industry_sector_code = t.value;
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
        // 点击空白关闭 ⋯ 下拉（点击 dropdown 内部任何子项都会带 data-quota-action 上走 handleAction）
        if (state.dropdown && state.dropdown.open && !target.closest(".quota-dropdown")) {
          state.dropdown = { open: false, archiveId: "", variant: "pending" };
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
      // 优先级：上传 → compose → 删除解析结果 → ⋯ 下拉
      if (state.upload && state.upload.open) closeUploadDialog();
      else if (state.compose && state.compose.open) closeCompose();
      else if (state.deleteParse && state.deleteParse.open) closeDeleteParseModal();
      else if (state.deleteArchive && state.deleteArchive.open) closeDeleteArchiveModal();
      else if (state.compare && state.compare.open) closeCompareModal();
      else if (state.dropdown && state.dropdown.open) {
        state.dropdown = { open: false, archiveId: "", variant: "pending" };
        render();
      }
    });

    // ── Phase C: 解析 7 端点事件 ─────────────────────────────────
    // handleAction 里 dispatchEvent 的 3 个事件原本只发声,这里接住 + 走真实后端
    document.addEventListener("quota:parse-trigger", function (ev) {
      var detail = (ev && ev.detail) || {};
      handleParseTrigger(detail.archiveId);
    });
    document.addEventListener("quota:parse-action", function (ev) {
      var detail = (ev && ev.detail) || {};
      handleParseAction(detail.action, detail.archiveId);
    });
    document.addEventListener("quota:parse-upload-reviewed", function (ev) {
      var detail = (ev && ev.detail) || {};
      handleUploadReviewed(detail.archiveId);
    });
  }

  // ── Phase C: 解析 handler 们 ─────────────────────────────────────────
  // 状态机五态到前端提示语(对齐 quota-api.js §3 CAP)
  var PARSE_ERR_MSG = {
    unauthorized: "未授权:请检查登录态 / QUOTA_PARSE_MOCK 是否启用",
    unavailable: "解析端点不可用(404):确认 backend 已部署该路由",
    error: "解析请求失败:查看后端日志",
  };

  function _parseApiOrWarn() {
    if (!state.parseApi) {
      setToast("解析客户端未加载(quota-parse-api.js 没注册)");
      return null;
    }
    return state.parseApi;
  }

  function handleParseTrigger(archiveId) {
    var api = _parseApiOrWarn();
    if (!api) return;
    if (!archiveId) { setToast("缺少 archiveId"); return; }
    // v0.8: 点「开始解析」时若档案 parse_status='skipped_no_parser' (即入库成功但
    // 该省未配置 extractor), 不发请求直接弹 toast 提示, 不污染失败统计.
    // 列表徽章视觉等同「未解析」(等同 pending 变体), 区别只在用户主动触发解析时才弹出.
    var archiveRow = null;
    if (state.archives && state.archives.status === CAP.READY && state.archives.data) {
      for (var i = 0; i < state.archives.data.length; i++) {
        if (state.archives.data[i].archive_id === archiveId) {
          archiveRow = state.archives.data[i];
          break;
        }
      }
    }
    if (archiveRow && archiveRow.parse_status === "skipped_no_parser") {
      var provinceHint = "";
      // province 短码 → label 映射 (取自 _UPLOAD_PROVINCE_MAP 同源数据)
      var code = (archiveRow.metadata && archiveRow.metadata.province) || "";
      for (var j = 0; j < UPLOAD_PROVINCE_OPTIONS.length; j++) {
        if (UPLOAD_PROVINCE_OPTIONS[j].code === code) {
          provinceHint = UPLOAD_PROVINCE_OPTIONS[j].label;
          break;
        }
      }
      var provincePart = provinceHint ? "（" + provinceHint + "）" : "";
      setToast("未配置解析脚本" + provincePart + "，请联系管理员接入 extractor");
      return;
    }
    setToast("已提交阶段 A,稍候查询状态…");
    api.triggerParse(archiveId).then(function (res) {
      if (res.status === CAP.READY) {
        setToast("阶段 A 已启动,5-10s 后查产物");
        // 阶段 A mock 默认 5-10s — 等 8s 后自动 reload
        setTimeout(function () {
          if (state.api && state.flags.archives) reloadArchives();
        }, 8000);
      } else {
        setToast("触发失败: " + (PARSE_ERR_MSG[res.status] || res.error || res.status));
      }
    });
  }

  function handleParseAction(action, archiveId) {
    var api = _parseApiOrWarn();
    if (!api) return;
    if (!archiveId) { setToast("缺少 archiveId"); return; }
    if (action === "parse-download-candidate") {
      api.downloadCandidate(archiveId).then(function (res) {
        if (res.status === CAP.READY && res.blob) {
          api.saveBlob(res.blob, res.filename || ("candidate-" + archiveId.slice(0, 8) + ".xlsx"));
          setToast("candidate.xlsx 已下载");
        } else {
          setToast("下载失败: " + (res.error || res.status));
        }
      });
      return;
    }
    if (action === "parse-download-final") {
      api.downloadFinal(archiveId).then(function (res) {
        if (res.status === CAP.READY && res.blob) {
          api.saveBlob(res.blob, res.filename || ("final-" + archiveId.slice(0, 8) + ".xlsx"));
          setToast("final.xlsx 已下载");
        } else {
          setToast("下载失败: " + (res.error || res.status));
        }
      });
      return;
    }
    if (action === "parse-show-manifest") {
      api.getManifest(archiveId).then(function (res) {
        if (res.status === CAP.READY && res.data) {
          openJsonModal("Manifest", res.data);
        } else {
          setToast("读 manifest 失败: " + (res.error || res.status));
        }
      });
      return;
    }
    if (action === "parse-show-qa") {
      api.getQaReport(archiveId).then(function (res) {
        if (res.status === CAP.READY && res.data) {
          openJsonModal("QA Report", res.data);
        } else {
          setToast("读 qa 报告失败: " + (res.error || res.status));
        }
      });
      return;
    }
  }

  function handleUploadReviewed(archiveId) {
    var api = _parseApiOrWarn();
    if (!api) return;
    if (!archiveId) { setToast("缺少 archiveId"); return; }
    // 动态创建 file input,选完即 POST
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".xlsx";
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      setToast("已上传 reviewed.xlsx,稍候查询 final…");
      api.uploadReviewed(archiveId, file).then(function (res) {
        if (res.status === CAP.READY) {
          setToast("reviewed.xlsx 已接受,2-3s 后查 final");
          setTimeout(function () {
            if (state.api && state.flags.archives) reloadArchives();
          }, 4500);
        } else {
          // 422 携带结构错误详情,这里直接显示后端 detail
          var detail = (res.data && res.data.detail) || res.error || res.status;
          setToast("上传失败: " + detail);
        }
      });
    });
    input.click();
  }

  // 极简 JSON / Markdown 弹窗(复用现有 quotaModal 容器风格)
  function openJsonModal(title, payload) {
    var modalId = "quotaParseViewerModal";
    var existing = document.getElementById(modalId);
    if (existing) { existing.remove(); }
    var isMd = (typeof payload === "string");
    var body = isMd
      ? escapeHtml(payload)
      : JSON.stringify(payload, null, 2);
    var html =
      '<div class="quota-modal-dialog">' +
        '<header class="manual-upload-header">' +
          '<div><p class="eyebrow">Parse Viewer</p><h2>' + escapeHtml(title) + '</h2></div>' +
          '<button class="icon-button" type="button" data-action="close-parse-viewer" title="关闭"><span style="font-size:18px;">×</span></button>' +
        '</header>' +
        '<div class="manual-upload-body"><pre style="white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.5;max-height:60vh;overflow:auto;background:#fafafa;padding:12px;border-radius:6px;">' + body + '</pre></div>' +
      '</div>';
    var sec = document.createElement("section");
    sec.id = modalId;
    sec.className = "modal-backdrop quota-modal";
    sec.setAttribute("aria-hidden", "false");
    sec.innerHTML = html;
    sec.addEventListener("click", function (ev) {
      var t = ev.target;
      if (t && t.closest && t.closest("[data-action=\"close-parse-viewer\"]")) sec.remove();
      else if (t === sec) sec.remove();
    });
    document.body.appendChild(sec);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>\"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
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
    // Phase C：解析 7 端点 HTTP 客户端（独立于 quota-api.js,自己包 fetch wrapper）
    state.parseApi = (typeof QuotaParseApi !== "undefined" && QuotaParseApi.createQuotaParseApi)
      ? QuotaParseApi.createQuotaParseApi({})
      : null;
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
    state.upload = { open: false, files: [], category: "", province: "", industry_sector_code: "", year: "", submitting: false };
    state.dropdown = { open: false, archiveId: "", variant: "pending" };
    state.deleteParse = {
      open: false, archiveId: "", title: "", submitting: false, error: "",
    };
    state.deleteArchive = {
      open: false, archiveId: "", title: "", submitting: false, error: "",
    };
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
    const deleteModal = document.getElementById("quotaDeleteParseModal");
    if (deleteModal) {
      deleteModal.hidden = true;
      deleteModal.innerHTML = "";
    }
    const deleteArchModal = document.getElementById("quotaDeleteArchiveModal");
    if (deleteArchModal) {
      deleteArchModal.hidden = true;
      deleteArchModal.innerHTML = "";
    }
    const compareModal = document.getElementById("quotaCompareModal");
    if (compareModal) {
      compareModal.hidden = true;
      compareModal.innerHTML = "";
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
      renderStatusBadge,
      renderSmartActions,
      renderDropdown,
      resolveUiStatus,
      openDeleteParseModal,
      closeDeleteParseModal,
      submitDeleteParse,
      openDeleteArchiveModal,
      closeDeleteArchiveModal,
      submitDeleteArchive,
      openCompareModal,
      closeCompareModal,
      submitCompare,
      renderCompareView,
      renderCompareModal,
      renderUploadModal,
      handleAction,
      TAB_CAPABILITY,
      TABS,
      LIST_COLUMNS,
      PRIMARY_FILTERS,
      SMART_ACTIONS,
      DROPDOWN_ITEMS,
    },
  };

  global.QuotaUI = QuotaUI;
  if (typeof module !== "undefined" && module.exports) module.exports = QuotaUI;
})(typeof window !== "undefined" ? window : globalThis);
