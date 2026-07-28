const domainConfigs = {
  cost_info: {
    title: "信息价档案台",
    resultUnit: "份信息价档案",
    searchPlaceholder: "搜索地区、期次、来源、文件名",
    filters: [
      { key: "publisher_org", label: "发布机构", kind: "publisherOrg", hideWhenNoOptions: true, advanced: true },
      { key: "region_code", label: "地区", kind: "region", metadataKeys: ["province_raw", "city_raw"] },
      { key: "period_year", label: "年份", kind: "year", metadataKeys: ["period_raw", "period_start", "publish_date_raw"] },
      { key: "period_month", label: "月份", kind: "month", metadataKeys: ["period_raw", "period_start", "publish_date_raw"] },
      { key: "category_raw", label: "资料分类", kind: "metadataEnum", metadataKey: "category_raw", advanced: true },
      { key: "channel_type", label: "入湖通道", options: channelOptions(), advanced: true },
    ],
    columns: [
      { key: "title", label: "档案标题", type: "title" },
      { key: "publisher", label: "发布主体", type: "metadata", metadataKey: "publisher" },
      { key: "region_code", label: "识别区域", type: "region" },
      { key: "period", label: "期次", type: "metadata", metadataKey: "period_raw" },
      { key: "channel_type", label: "通道", type: "channel" },
      { key: "uploaded_by", label: "上传人", type: "metadata", metadataKey: "uploaded_by" },
      { key: "status", label: "状态", type: "status" },
      { key: "actions", label: "操作", type: "actions" },
    ],
    metadataFields: [
      { key: "period_raw", label: "期次原文" },
      { key: "publisher_type", label: "发布主体类型" },
      { key: "publisher", label: "发布主体" },
      { key: "province_raw", label: "省份原文" },
      { key: "city_raw", label: "城市原文" },
      { key: "uploaded_by", label: "上传人" },
    ],
  },
  trading: {
    title: "招投标公告档案台",
    resultUnit: "条公告档案",
    searchPlaceholder: "搜索公告标题、项目代码原文、来源条目",
    filters: [
      { key: "region_code", label: "地区", kind: "region", metadataKeys: ["province_raw", "city_raw"] },
      { key: "publish_year", label: "年份", kind: "year", fields: ["publish_date"], metadataKeys: ["publish_date_raw"] },
      { key: "publish_month", label: "月份", kind: "month", fields: ["publish_date"], metadataKeys: ["publish_date_raw"] },
      { key: "publish_day", label: "日期", kind: "day", fields: ["publish_date"], metadataKeys: ["publish_date_raw"], dependsOn: ["publish_month"] },
      { key: "notice_type_raw", label: "公告类型", kind: "metadataEnum", metadataKey: "notice_type_raw", advanced: true },
      { key: "channel_type", label: "入湖通道", options: channelOptions(), advanced: true },
    ],
    columns: [
      { key: "title", label: "公告", type: "title" },
      { key: "notice_type_raw", label: "公告类型", type: "metadata", metadataKey: "notice_type_raw" },
      { key: "exchange_name", label: "交易中心", type: "metadata", metadataKey: "exchange_name" },
      { key: "region_code", label: "识别区域", type: "region" },
      { key: "publish_date", label: "发布时间", type: "date" },
      { key: "attachments", label: "附件", type: "attachments" },
      { key: "status", label: "状态", type: "status" },
      { key: "actions", label: "操作", type: "actions" },
    ],
    metadataFields: [
      { key: "project_code_raw", label: "项目代码原文" },
      { key: "notice_type_raw", label: "公告类型原文" },
      { key: "exchange_name", label: "交易中心" },
      { key: "column_path_raw", label: "来源栏目路径" },
      { key: "project_name_raw", label: "项目名称原文" },
      { key: "engineering_type_raw", label: "工程类型原文" },
      { key: "publish_date_raw", label: "发布日期原文" },
      { key: "attachment_count_raw", label: "附件数原文" },
    ],
  },
  quota: {
    title: "清单定额档案台",
    resultUnit: "份清单定额档案",
    searchPlaceholder: "搜索定额库、清单规范、专业、版本、来源",
    emptyHint: "导入真实定额库后，这里展示 quota 档案。",
    filters: [
      { key: "region_code", label: "地区", kind: "region", metadataKeys: ["region_raw"] },
      { key: "specialty_raw", label: "专业", kind: "metadataEnum", metadataKey: "specialty_raw" },
      { key: "file_role", label: "文件类型", kind: "fileRole", options: quotaFileRoleOptions() },
      { key: "version_year", label: "版本年份", kind: "year", metadataKeys: ["version", "effective_date"] },
      { key: "channel_type", label: "入湖通道", options: channelOptions() },
    ],
    columns: [
      { key: "title", label: "档案标题", type: "title" },
      { key: "quota_code_raw", label: "定额编码", type: "metadata", metadataKey: "quota_code_raw" },
      { key: "specialty_raw", label: "专业", type: "metadata", metadataKey: "specialty_raw" },
      { key: "version", label: "版本", type: "metadata", metadataKey: "version" },
      { key: "effective_date", label: "实施日期", type: "metadata", metadataKey: "effective_date" },
      { key: "status", label: "状态", type: "status" },
      { key: "actions", label: "操作", type: "actions" },
    ],
    metadataFields: [
      { key: "quota_code_raw", label: "定额编码原文" },
      { key: "region_raw", label: "适用地区原文" },
      { key: "version", label: "版本" },
      { key: "effective_date", label: "实施日期" },
      { key: "specialty_raw", label: "专业原文" },
      { key: "standard_no", label: "标准号" },
      { key: "parent_quota_ref", label: "主定额线索" },
    ],
  },
  policy_regulation: {
    title: "政策法规档案台",
    resultUnit: "份政策法规档案",
    searchPlaceholder: "搜索政策标题、文号、发文机关、适用地区",
    emptyHint: "该域待接入真实数据。政策主题先保留 raw 文本，不提供固定枚举。",
    filters: [
      { key: "issuing_authority_raw", label: "发文机关", kind: "metadataEnum", metadataKey: "issuing_authority_raw" },
      { key: "policy_type_raw", label: "政策类型", kind: "metadataEnum", metadataKey: "policy_type_raw" },
      { key: "region_code", label: "适用地区", kind: "region", metadataKeys: ["applicable_region_raw"] },
      { key: "policy_effective_status", label: "生效状态", kind: "policyEffectiveStatus", options: policyEffectiveStatusOptions() },
      { key: "publish_year", label: "年份", kind: "year", fields: ["publish_date"], metadataKeys: ["publish_date_raw", "effective_date"] },
      { key: "channel_type", label: "入湖通道", options: channelOptions() },
    ],
    columns: [
      { key: "title", label: "档案标题", type: "title" },
      { key: "document_no_raw", label: "文号", type: "metadata", metadataKey: "document_no_raw" },
      { key: "issuing_authority_raw", label: "发文机关", type: "metadata", metadataKey: "issuing_authority_raw" },
      { key: "policy_effective_status", label: "生效状态", type: "policyEffectiveStatus" },
      { key: "status", label: "状态", type: "status" },
      { key: "actions", label: "操作", type: "actions" },
    ],
    metadataFields: [
      { key: "issuing_authority_raw", label: "发文机关原文" },
      { key: "document_no_raw", label: "发文号原文" },
      { key: "policy_type_raw", label: "政策类型原文" },
      { key: "publish_date_raw", label: "发布日期原文" },
      { key: "effective_date", label: "生效日期" },
      { key: "expiry_date", label: "失效日期" },
      { key: "supersedes_raw", label: "替代线索原文" },
      { key: "applicable_region_raw", label: "适用地区原文" },
      { key: "policy_topic_raw", label: "政策主题原文" },
    ],
  },
  standard_atlas: {
    title: "规范图集档案台",
    resultUnit: "份规范图集档案",
    searchPlaceholder: "搜索图集号、标准号、专业、标题",
    emptyHint: "该域待接入真实数据。构造主题先做 raw 文本搜索，不提供固定枚举。",
    filters: [
      { key: "standard_type_raw", label: "标准类型", kind: "metadataEnum", metadataKey: "standard_type_raw" },
      { key: "discipline_raw", label: "专业", kind: "metadataEnum", metadataKey: "discipline_raw" },
      { key: "construction_topic_raw", label: "构造主题", kind: "metadataEnum", metadataKey: "construction_topic_raw" },
      { key: "version_year", label: "版本年份", kind: "year", metadataKeys: ["version", "effective_date"] },
      { key: "channel_type", label: "入湖通道", options: channelOptions() },
    ],
    columns: [
      { key: "title", label: "档案标题", type: "title" },
      { key: "standard_no", label: "标准号", type: "metadata", metadataKey: "standard_no" },
      { key: "standard_type_raw", label: "标准类型", type: "metadata", metadataKey: "standard_type_raw" },
      { key: "discipline_raw", label: "专业", type: "metadata", metadataKey: "discipline_raw" },
      { key: "status", label: "状态", type: "status" },
      { key: "actions", label: "操作", type: "actions" },
    ],
    metadataFields: [
      { key: "standard_no", label: "标准号" },
      { key: "standard_type_raw", label: "标准类型原文" },
      { key: "version", label: "版本" },
      { key: "issuing_authority_raw", label: "发布机构原文" },
      { key: "effective_date", label: "实施日期" },
      { key: "discipline_raw", label: "专业领域原文" },
      { key: "superseded_by_raw", label: "替代线索原文" },
      { key: "construction_topic_raw", label: "构造主题原文" },
    ],
  },
};

const coverageFilterConfig = [
  { key: "region_code", label: "地区", kind: "region", coverage: true },
  { key: "period_year", label: "年份", kind: "year", coverage: true },
  { key: "period_month", label: "月份", kind: "month", coverage: true },
  {
    key: "target_level",
    label: "层级",
    coverage: true,
    options: [
      { label: "地市", value: "city" },
      { label: "区县/分区", value: "subregion" },
      { label: "全部层级", value: "all" },
    ],
  },
  {
    key: "business_coverage_status",
    label: "业务覆盖",
    coverage: true,
    options: [
      { label: "全部", value: "all" },
      { label: "已覆盖", value: "covered" },
      { label: "待核", value: "pending_verify" },
      { label: "缺失", value: "missing" },
    ],
  },
  {
    key: "source_completeness_status",
    label: "来源完整度",
    coverage: true,
    options: [
      { label: "全部", value: "all" },
      { label: "双源", value: "dual_source" },
      { label: "地市源", value: "city_source_present" },
      { label: "缺地市", value: "city_source_missing" },
      { label: "仅省站", value: "province_source_only" },
      { label: "源受阻", value: "source_blocked" },
      { label: "待审计", value: "pending_source_audit" },
    ],
  },
];

const businessCoverageLabels = {
  covered: "已覆盖",
  pending_verify: "待核",
  missing: "缺失",
};

const sourceCompletenessLabels = {
  dual_source: "双源",
  city_source_present: "地市源",
  city_source_missing: "缺地市",
  province_source_only: "仅省站",
  source_blocked: "源受阻",
  pending_source_audit: "待审计",
};

const sourceAuditLabels = {
  auto_crawl_verified: "已接地市源",
  online_table_declaration: "覆盖声明",
  spa_api_file_source: "SPA文件源",
  source_blocked: "源受阻",
  manual_upload_required: "人工待补",
  pending_source_audit: "待找地市源",
};

const state = {
  domain: "cost_info",
  viewMode: "archives",
  archives: [],
  archiveTotalCount: 0,
  archiveHydrating: false,
  archiveHydrationError: "",
  archiveLoadToken: 0,
  costInfoSources: [],
  coverageRows: [],
  coverageLoadToken: 0,
  storageAudit: { loading: false, error: "", data: null },
  crawlBusy: false,
  coverageBackfill: {
    open: false,
    row: null,
    sourceId: "",
    submitting: false,
    error: "",
  },
  selectedArchive: null,
  selectedFileId: null,
  mirrorExporting: false,
  viewerInfoOpen: false,
  filterAdvancedOpen: false,
  zipPreview: { fileId: null, manifest: null, selectedIndex: null },
  filters: defaultFilters("cost_info"),
  coverageFilters: defaultCoverageFilters(),
  status: "all",
  search: "",
  loading: false,
  error: "",
  toast: { message: "", kind: "success" },
  manualUpload: {
    open: false,
    mode: "create",
    archive: null,
    submitting: false,
    error: "",
    files: [],
    regionProvince: "",
    regionSearch: "",
    values: {},
    periodPicker: { open: false, view: "month", year: null, month: null },
  },
};

const costInfoDimensionIndexCache = { archives: null, index: null };
const ARCHIVE_PAGE_SIZE = 500;
const ARCHIVE_INITIAL_PAGE_SIZE = 100;
const COVERAGE_HISTORY_YEAR_COUNT = 7;
const DEFAULT_COVERAGE_END_YEAR = 2026;

const statusLabels = {
  discovered: "已发现",
  collecting: "入湖中",
  collected: "已入湖",
  pending_tag: "需确认",
  archived: "已归档",
  ready_for_governance: "待移交",
  collect_failed: "采集失败",
  quarantined: "隔离",
};

const channelLabels = {
  crawler: "爬虫",
  manual_upload: "个人上传",
  netdisk: "网盘",
  system_api: "系统对接",
  batch_import: "批量导入",
  email_import: "邮件导入",
};

const typeConfig = {
  pdf: { icon: "file-text", label: "PDF", className: "type-pdf" },
  xls: { icon: "file-spreadsheet", label: "Excel", className: "type-excel" },
  xlsx: { icon: "file-spreadsheet", label: "Excel", className: "type-excel" },
  doc: { icon: "file-text", label: "Word", className: "type-word" },
  docx: { icon: "file-text", label: "Word", className: "type-word" },
  html: { icon: "globe", label: "HTML", className: "type-html" },
  htm: { icon: "globe", label: "HTML", className: "type-html" },
  jpg: { icon: "image", label: "Image", className: "type-image" },
  jpeg: { icon: "image", label: "Image", className: "type-image" },
  png: { icon: "image", label: "Image", className: "type-image" },
  zbx: { icon: "file-code-2", label: "计价源", className: "type-priced" },
  cjz: { icon: "file-code-2", label: "计价源", className: "type-priced" },
  cos: { icon: "file-code-2", label: "计价源", className: "type-priced" },
  qtfx: { icon: "file-code-2", label: "计价源", className: "type-priced" },
};

const regionTree = [
  { value: "11", label: "北京", cities: [{ value: "110100", label: "北京市" }] },
  { value: "12", label: "天津", cities: [{ value: "120100", label: "天津市" }] },
  { value: "13", label: "河北", cities: [
    { value: "130100", label: "石家庄市" }, { value: "130200", label: "唐山市" }, { value: "130300", label: "秦皇岛市" },
    { value: "130400", label: "邯郸市" }, { value: "130500", label: "邢台市" }, { value: "130600", label: "保定市" },
    { value: "130700", label: "张家口市" }, { value: "130800", label: "承德市" }, { value: "130900", label: "沧州市" },
    { value: "131000", label: "廊坊市" }, { value: "131100", label: "衡水市" },
  ] },
  { value: "14", label: "山西", cities: [
    { value: "140100", label: "太原市" }, { value: "140200", label: "大同市" }, { value: "140300", label: "阳泉市" },
    { value: "140400", label: "长治市" }, { value: "140500", label: "晋城市" }, { value: "140600", label: "朔州市" },
    { value: "140700", label: "晋中市" }, { value: "140800", label: "运城市" }, { value: "140900", label: "忻州市" },
    { value: "141000", label: "临汾市" }, { value: "141100", label: "吕梁市" },
  ] },
  { value: "15", label: "内蒙古", cities: [
    { value: "150100", label: "呼和浩特市" }, { value: "150200", label: "包头市" }, { value: "150300", label: "乌海市" },
    { value: "150400", label: "赤峰市" }, { value: "150500", label: "通辽市" }, { value: "150600", label: "鄂尔多斯市" },
    { value: "150700", label: "呼伦贝尔市" }, { value: "150800", label: "巴彦淖尔市" }, { value: "150900", label: "乌兰察布市" },
    { value: "152200", label: "兴安盟" }, { value: "152500", label: "锡林郭勒盟" }, { value: "152900", label: "阿拉善盟" },
  ] },
  { value: "21", label: "辽宁", cities: [
    { value: "210100", label: "沈阳市" }, { value: "210200", label: "大连市" }, { value: "210300", label: "鞍山市" },
    { value: "210400", label: "抚顺市" }, { value: "210500", label: "本溪市" }, { value: "210600", label: "丹东市" },
    { value: "210700", label: "锦州市" }, { value: "210800", label: "营口市" }, { value: "210900", label: "阜新市" },
    { value: "211000", label: "辽阳市" }, { value: "211100", label: "盘锦市" }, { value: "211200", label: "铁岭市" },
    { value: "211300", label: "朝阳市" }, { value: "211400", label: "葫芦岛市" },
  ] },
  { value: "22", label: "吉林", cities: [
    { value: "220100", label: "长春市" }, { value: "220200", label: "吉林市" }, { value: "220300", label: "四平市" },
    { value: "220400", label: "辽源市" }, { value: "220500", label: "通化市" }, { value: "220600", label: "白山市" },
    { value: "220700", label: "松原市" }, { value: "220800", label: "白城市" }, { value: "222400", label: "延边朝鲜族自治州" },
  ] },
  { value: "23", label: "黑龙江", cities: [
    { value: "230100", label: "哈尔滨市" }, { value: "230200", label: "齐齐哈尔市" }, { value: "230300", label: "鸡西市" },
    { value: "230400", label: "鹤岗市" }, { value: "230500", label: "双鸭山市" }, { value: "230600", label: "大庆市" },
    { value: "230700", label: "伊春市" }, { value: "230800", label: "佳木斯市" }, { value: "230900", label: "七台河市" },
    { value: "231000", label: "牡丹江市" }, { value: "231100", label: "黑河市" }, { value: "231200", label: "绥化市" },
    { value: "232700", label: "大兴安岭地区" },
  ] },
  { value: "31", label: "上海", cities: [{ value: "310100", label: "上海市" }] },
  { value: "32", label: "江苏", cities: [
    { value: "320100", label: "南京市" }, { value: "320200", label: "无锡市" }, { value: "320300", label: "徐州市" },
    { value: "320400", label: "常州市" }, { value: "320500", label: "苏州市" }, { value: "320600", label: "南通市" },
    { value: "320700", label: "连云港市" }, { value: "320800", label: "淮安市" }, { value: "320900", label: "盐城市" },
    { value: "321000", label: "扬州市" }, { value: "321100", label: "镇江市" }, { value: "321200", label: "泰州市" },
    { value: "321300", label: "宿迁市" },
  ] },
  { value: "33", label: "浙江", cities: [
    { value: "330100", label: "杭州市" }, { value: "330200", label: "宁波市" }, { value: "330300", label: "温州市" },
    { value: "330400", label: "嘉兴市" }, { value: "330500", label: "湖州市" }, { value: "330600", label: "绍兴市" },
    { value: "330700", label: "金华市" }, { value: "330800", label: "衢州市" }, { value: "330900", label: "舟山市" },
    { value: "331000", label: "台州市" }, { value: "331100", label: "丽水市" },
  ] },
  { value: "34", label: "安徽", cities: [
    { value: "340100", label: "合肥市" }, { value: "340200", label: "芜湖市" }, { value: "340300", label: "蚌埠市" },
    { value: "340400", label: "淮南市" }, { value: "340500", label: "马鞍山市" }, { value: "340600", label: "淮北市" },
    { value: "340700", label: "铜陵市" }, { value: "340800", label: "安庆市" }, { value: "341000", label: "黄山市" },
    { value: "341100", label: "滁州市" }, { value: "341200", label: "阜阳市" }, { value: "341300", label: "宿州市" },
    { value: "341500", label: "六安市" }, { value: "341600", label: "亳州市" }, { value: "341700", label: "池州市" },
    { value: "341800", label: "宣城市" },
  ] },
  { value: "35", label: "福建", cities: [
    { value: "350100", label: "福州市" }, { value: "350200", label: "厦门市" }, { value: "350300", label: "莆田市" },
    { value: "350400", label: "三明市" }, { value: "350500", label: "泉州市" }, { value: "350600", label: "漳州市" },
    { value: "350700", label: "南平市" }, { value: "350800", label: "龙岩市" }, { value: "350900", label: "宁德市" },
  ] },
  { value: "36", label: "江西", cities: [
    { value: "360100", label: "南昌市" }, { value: "360200", label: "景德镇市" }, { value: "360300", label: "萍乡市" },
    { value: "360400", label: "九江市" }, { value: "360500", label: "新余市" }, { value: "360600", label: "鹰潭市" },
    { value: "360700", label: "赣州市" }, { value: "360800", label: "吉安市" }, { value: "360900", label: "宜春市" },
    { value: "361000", label: "抚州市" }, { value: "361100", label: "上饶市" },
  ] },
  { value: "37", label: "山东", cities: [
    { value: "370100", label: "济南市" }, { value: "370200", label: "青岛市" }, { value: "370300", label: "淄博市" },
    { value: "370400", label: "枣庄市" }, { value: "370500", label: "东营市" }, { value: "370600", label: "烟台市" },
    { value: "370700", label: "潍坊市" }, { value: "370800", label: "济宁市" }, { value: "370900", label: "泰安市" },
    { value: "371000", label: "威海市" }, { value: "371100", label: "日照市" }, { value: "371300", label: "临沂市" },
    { value: "371400", label: "德州市" }, { value: "371500", label: "聊城市" }, { value: "371600", label: "滨州市" },
    { value: "371700", label: "菏泽市" },
  ] },
  { value: "41", label: "河南", cities: [
    { value: "410100", label: "郑州市" }, { value: "410200", label: "开封市" }, { value: "410300", label: "洛阳市" },
    { value: "410400", label: "平顶山市" }, { value: "410500", label: "安阳市" }, { value: "410600", label: "鹤壁市" },
    { value: "410700", label: "新乡市" }, { value: "410800", label: "焦作市" }, { value: "410900", label: "濮阳市" },
    { value: "411000", label: "许昌市" }, { value: "411100", label: "漯河市" }, { value: "411200", label: "三门峡市" },
    { value: "411300", label: "南阳市" }, { value: "411400", label: "商丘市" }, { value: "411500", label: "信阳市" },
    { value: "411600", label: "周口市" }, { value: "411700", label: "驻马店市" },
  ] },
  { value: "42", label: "湖北", cities: [
    { value: "420100", label: "武汉市" }, { value: "420200", label: "黄石市" }, { value: "420300", label: "十堰市" },
    { value: "420500", label: "宜昌市" }, { value: "420600", label: "襄阳市" }, { value: "420700", label: "鄂州市" },
    { value: "420800", label: "荆门市" }, { value: "420900", label: "孝感市" }, { value: "421000", label: "荆州市" },
    { value: "421100", label: "黄冈市" }, { value: "421200", label: "咸宁市" }, { value: "421300", label: "随州市" },
    { value: "422800", label: "恩施土家族苗族自治州" },
  ] },
  { value: "43", label: "湖南", cities: [
    { value: "430100", label: "长沙市" }, { value: "430200", label: "株洲市" }, { value: "430300", label: "湘潭市" },
    { value: "430400", label: "衡阳市" }, { value: "430500", label: "邵阳市" }, { value: "430600", label: "岳阳市" },
    { value: "430700", label: "常德市" }, { value: "430800", label: "张家界市" }, { value: "430900", label: "益阳市" },
    { value: "431000", label: "郴州市" }, { value: "431100", label: "永州市" }, { value: "431200", label: "怀化市" },
    { value: "431300", label: "娄底市" }, { value: "433100", label: "湘西土家族苗族自治州" },
  ] },
  { value: "44", label: "广东", cities: [
    { value: "440100", label: "广州市" }, { value: "440200", label: "韶关市" }, { value: "440300", label: "深圳市" },
    { value: "440400", label: "珠海市" }, { value: "440500", label: "汕头市" }, { value: "440600", label: "佛山市" },
    { value: "440700", label: "江门市" }, { value: "440800", label: "湛江市" }, { value: "440900", label: "茂名市" },
    { value: "441200", label: "肇庆市" }, { value: "441300", label: "惠州市" }, { value: "441400", label: "梅州市" },
    { value: "441500", label: "汕尾市" }, { value: "441600", label: "河源市" }, { value: "441700", label: "阳江市" },
    { value: "441800", label: "清远市" }, { value: "441900", label: "东莞市" }, { value: "442000", label: "中山市" },
    { value: "445100", label: "潮州市" }, { value: "445200", label: "揭阳市" }, { value: "445300", label: "云浮市" },
  ] },
  { value: "45", label: "广西", cities: [
    { value: "450100", label: "南宁市" }, { value: "450200", label: "柳州市" }, { value: "450300", label: "桂林市" },
    { value: "450400", label: "梧州市" }, { value: "450500", label: "北海市" }, { value: "450600", label: "防城港市" },
    { value: "450700", label: "钦州市" }, { value: "450800", label: "贵港市" }, { value: "450900", label: "玉林市" },
    { value: "451000", label: "百色市" }, { value: "451100", label: "贺州市" }, { value: "451200", label: "河池市" },
    { value: "451300", label: "来宾市" }, { value: "451400", label: "崇左市" },
  ] },
  { value: "46", label: "海南", cities: [
    { value: "460100", label: "海口市" }, { value: "460200", label: "三亚市" }, { value: "460300", label: "三沙市" },
    { value: "460400", label: "儋州市" },
  ] },
  { value: "50", label: "重庆", cities: [{ value: "500100", label: "重庆市" }] },
  { value: "51", label: "四川", cities: [
    { value: "510100", label: "成都市" }, { value: "510300", label: "自贡市" }, { value: "510400", label: "攀枝花市" },
    { value: "510500", label: "泸州市" }, { value: "510600", label: "德阳市" }, { value: "510700", label: "绵阳市" },
    { value: "510800", label: "广元市" }, { value: "510900", label: "遂宁市" }, { value: "511000", label: "内江市" },
    { value: "511100", label: "乐山市" }, { value: "511300", label: "南充市" }, { value: "511400", label: "眉山市" },
    { value: "511500", label: "宜宾市" }, { value: "511600", label: "广安市" }, { value: "511700", label: "达州市" },
    { value: "511800", label: "雅安市" }, { value: "511900", label: "巴中市" }, { value: "512000", label: "资阳市" },
    { value: "513200", label: "阿坝藏族羌族自治州" }, { value: "513300", label: "甘孜藏族自治州" }, { value: "513400", label: "凉山彝族自治州" },
  ] },
  { value: "52", label: "贵州", cities: [
    { value: "520100", label: "贵阳市" }, { value: "520200", label: "六盘水市" }, { value: "520300", label: "遵义市" },
    { value: "520400", label: "安顺市" }, { value: "520500", label: "毕节市" }, { value: "520600", label: "铜仁市" },
    { value: "522300", label: "黔西南布依族苗族自治州" }, { value: "522600", label: "黔东南苗族侗族自治州" }, { value: "522700", label: "黔南布依族苗族自治州" },
  ] },
  { value: "53", label: "云南", cities: [
    { value: "530100", label: "昆明市" }, { value: "530300", label: "曲靖市" }, { value: "530400", label: "玉溪市" },
    { value: "530500", label: "保山市" }, { value: "530600", label: "昭通市" }, { value: "530700", label: "丽江市" },
    { value: "530800", label: "普洱市" }, { value: "530900", label: "临沧市" }, { value: "532300", label: "楚雄彝族自治州" },
    { value: "532500", label: "红河哈尼族彝族自治州" }, { value: "532600", label: "文山壮族苗族自治州" }, { value: "532800", label: "西双版纳傣族自治州" },
    { value: "532900", label: "大理白族自治州" }, { value: "533100", label: "德宏傣族景颇族自治州" }, { value: "533300", label: "怒江傈僳族自治州" },
    { value: "533400", label: "迪庆藏族自治州" },
  ] },
  { value: "54", label: "西藏", cities: [
    { value: "540100", label: "拉萨市" }, { value: "540200", label: "日喀则市" }, { value: "540300", label: "昌都市" },
    { value: "540400", label: "林芝市" }, { value: "540500", label: "山南市" }, { value: "540600", label: "那曲市" },
    { value: "542500", label: "阿里地区" },
  ] },
  { value: "61", label: "陕西", cities: [
    { value: "610100", label: "西安市" }, { value: "610200", label: "铜川市" }, { value: "610300", label: "宝鸡市" },
    { value: "610400", label: "咸阳市" }, { value: "610500", label: "渭南市" }, { value: "610600", label: "延安市" },
    { value: "610700", label: "汉中市" }, { value: "610800", label: "榆林市" }, { value: "610900", label: "安康市" },
    { value: "611000", label: "商洛市" },
  ] },
  { value: "62", label: "甘肃", cities: [
    { value: "620100", label: "兰州市" }, { value: "620200", label: "嘉峪关市" }, { value: "620300", label: "金昌市" },
    { value: "620400", label: "白银市" }, { value: "620500", label: "天水市" }, { value: "620600", label: "武威市" },
    { value: "620700", label: "张掖市" }, { value: "620800", label: "平凉市" }, { value: "620900", label: "酒泉市" },
    { value: "621000", label: "庆阳市" }, { value: "621100", label: "定西市" }, { value: "621200", label: "陇南市" },
    { value: "622900", label: "临夏回族自治州" }, { value: "623000", label: "甘南藏族自治州" },
  ] },
  { value: "63", label: "青海", cities: [
    { value: "630100", label: "西宁市" }, { value: "630200", label: "海东市" }, { value: "632200", label: "海北藏族自治州" },
    { value: "632300", label: "黄南藏族自治州" }, { value: "632500", label: "海南藏族自治州" }, { value: "632600", label: "果洛藏族自治州" },
    { value: "632700", label: "玉树藏族自治州" }, { value: "632800", label: "海西蒙古族藏族自治州" },
  ] },
  { value: "64", label: "宁夏", cities: [
    { value: "640100", label: "银川市" }, { value: "640200", label: "石嘴山市" }, { value: "640300", label: "吴忠市" },
    { value: "640400", label: "固原市" }, { value: "640500", label: "中卫市" },
  ] },
  { value: "65", label: "新疆", cities: [
    { value: "650100", label: "乌鲁木齐市" }, { value: "650200", label: "克拉玛依市" }, { value: "650400", label: "吐鲁番市" },
    { value: "650500", label: "哈密市" }, { value: "652300", label: "昌吉回族自治州" }, { value: "652700", label: "博尔塔拉蒙古自治州" },
    { value: "652800", label: "巴音郭楞蒙古自治州" }, { value: "652900", label: "阿克苏地区" }, { value: "653000", label: "克孜勒苏柯尔克孜自治州" },
    { value: "653100", label: "喀什地区" }, { value: "653200", label: "和田地区" }, { value: "654000", label: "伊犁哈萨克自治州" },
    { value: "654200", label: "塔城地区" }, { value: "654300", label: "阿勒泰地区" },
  ] },
];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function channelOptions() {
  return [
    { label: "全部", value: "all" },
    { label: "爬虫", value: "crawler" },
    { label: "个人上传", value: "manual_upload" },
    { label: "网盘", value: "netdisk" },
    { label: "系统对接", value: "system_api" },
    { label: "批量导入", value: "batch_import" },
  ];
}

function quotaFileRoleOptions() {
  return [
    { label: "全部", value: "all" },
    { label: "定额库", value: "quota_db" },
    { label: "清单规范", value: "bill_standard" },
    { label: "补充定额", value: "quota_supplement" },
    { label: "定额解释", value: "quota_interpretation" },
    { label: "附件", value: "attachment" },
  ];
}

function policyEffectiveStatusOptions() {
  return [
    { label: "全部", value: "all" },
    { label: "未知", value: "未知" },
    { label: "未生效", value: "未生效" },
    { label: "生效中", value: "生效中" },
    { label: "已失效", value: "已失效" },
  ];
}

function regionOptions() {
  return [{ label: "全部", value: "all" }, ...regionTree.map(({ label, value }) => ({ label, value }))];
}

function costInfoSourceRegionCode(source) {
  return String(
    source?.region_code ||
      source?.config?.stable?.coverage_region_code ||
      source?.config?.stable?.region_code ||
      ""
  ).trim();
}

function costInfoSourceCityCodes(province) {
  const codes = new Set();
  (state.costInfoSources || []).forEach((source) => {
    if (source?.data_domain && source.data_domain !== "cost_info") return;
    if (source?.source_type && source.source_type !== "info_price") return;
    const regionCode = costInfoSourceRegionCode(source);
    const sourceCity = String(source?.city || source?.config?.stable?.city || "").trim();
    province.cities.forEach((city) => {
      if (regionCode === city.value || (regionCode.length > 6 && regionCode.startsWith(city.value.slice(0, 4)))) {
        codes.add(city.value);
        return;
      }
      if (sourceCity && (sourceCity === city.label || sourceCity.includes(city.label) || city.label.includes(sourceCity))) {
        codes.add(city.value);
      }
    });
  });
  return codes;
}

function mergeCostInfoCityOptions(province, archiveCities) {
  const mergedCodes = new Set(archiveCities.map((city) => city.value));
  const sourceCityCodes = costInfoSourceCityCodes(province);
  sourceCityCodes.forEach((code) => mergedCodes.add(code));
  return province.cities.filter((city) => mergedCodes.has(city.value));
}

function cityOptions(provinceCode, filter = {}) {
  const province = regionTree.find((item) => item.value === String(provinceCode));
  if (!province) return [{ label: "全部", value: "all" }];
  if (usesArchiveBackedCostInfoOptions(filter)) {
    const provinceIndex = costInfoDimensionScope({
      publisherOrg: effectiveCostInfoPublisherOrg(),
      province: province.value,
    });
    const archiveCities = province.cities.filter((city) => provinceIndex?.cities.has(city.value));
    const cities = mergeCostInfoCityOptions(province, archiveCities);
    if (!cities.length && province.cities.length === 1 && provinceIndex?.archiveCount) {
      return [{ label: "全部", value: "all" }, province.cities[0]];
    }
    return [{ label: "全部", value: "all" }, ...cities];
  }
  return [{ label: "全部", value: "all" }, ...province.cities];
}

function regionLabel(regionCode) {
  const code = String(regionCode ?? "").trim();
  if (!code) return "";
  for (const province of regionTree) {
    if (code === province.value || code === `${province.value}0000`) return province.label;
    for (const city of province.cities) {
      if (code === city.value || (code.length > 6 && code.startsWith(city.value.slice(0, 4)))) return city.label;
    }
  }
  return "";
}

function yearOptions(filter = {}) {
  if (usesArchiveBackedCostInfoOptions(filter)) {
    const scope = selectedCostInfoDimensionScope();
    return [
      { label: "全部", value: "all" },
      ...Array.from(scope?.years || [])
        .sort((a, b) => Number(b) - Number(a))
        .map((year) => ({ label: year, value: year })),
    ];
  }
  const currentYear = chinaToday().slice(0, 4);
  const start = Number(currentYear) || 2026;
  return [
    { label: "全部", value: "all" },
    ...Array.from({ length: 8 }, (_, index) => {
      const year = String(start - index);
      return { label: year, value: year };
    }),
  ];
}

function monthOptions(filter = {}) {
  if (usesArchiveBackedCostInfoOptions(filter)) {
    const selectedYear = state.filters.period_year || "all";
    const months = costInfoMonthsForScope(selectedCostInfoDimensionScope(), selectedYear);
    return [
      { label: "全部", value: "all" },
      ...Array.from(months)
        .sort((a, b) => Number(a) - Number(b))
        .map((month) => ({ label: `${Number(month)}月`, value: month })),
    ];
  }
  return [
    { label: "全部", value: "all" },
    ...Array.from({ length: 12 }, (_, index) => {
      const month = String(index + 1).padStart(2, "0");
      return { label: `${index + 1}月`, value: month };
    }),
  ];
}

function issueOptions(filter = {}) {
  if (!usesArchiveBackedCostInfoOptions(filter)) return [{ label: "全部", value: "all" }];
  const selectedYear = state.filters.period_year || "all";
  const issues = costInfoIssuesForScope(selectedCostInfoDimensionScope(), selectedYear);
  return [
    { label: "全部", value: "all" },
    ...Array.from(issues.values())
      .sort((a, b) => Number(b.year) - Number(a.year) || Number(a.issueNo) - Number(b.issueNo))
      .map((issue) => ({ label: issue.label, value: issue.value })),
  ];
}

function publisherOrgOptions(filter = {}) {
  if (!usesArchiveBackedCostInfoOptions(filter)) return [{ label: "全部", value: "all" }];
  const regionFilter = regionFilterForCostInfo();
  const scope = costInfoDimensionScope({
    province: state.filters.region_code || "all",
    city: state.filters[cityFilterKey(regionFilter)] || "all",
  });
  return [
    { label: "全部", value: "all" },
    ...Array.from(scope.publisherOrgs.values())
      .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, "zh-CN"))
      .map(({ label, value }) => ({ label, value })),
  ];
}

function dayOptions(filter = {}) {
  const filters = filter.coverage ? state.coverageFilters : state.filters;
  const year = filters.publish_year || filters.period_year;
  const month = filters.publish_month || filters.period_month;
  if (!month || month === "all") return [{ label: "全部", value: "all" }];
  const days = new Set();
  state.archives.forEach((item) => {
    [item.publish_date, metadata(item, "publish_date_raw"), ...filterCandidates(item, filter)].forEach((candidate) => {
      const parts = dateParts(candidate);
      if (parts?.month === month && (!year || year === "all" || parts.year === year)) days.add(parts.day);
    });
  });
  return [
    { label: "全部", value: "all" },
    ...Array.from(days)
      .sort((a, b) => Number(a) - Number(b))
      .map((day) => ({ label: `${Number(day)}日`, value: day })),
  ];
}

function dateParts(value) {
  const text = String(value ?? "");
  let match = text.match(/\b(20\d{2})-(\d{1,2})-(\d{1,2})/);
  if (!match) match = text.match(/(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})(?:日|号)?/);
  if (!match) return null;
  return {
    year: match[1],
    month: String(Number(match[2])).padStart(2, "0"),
    day: String(Number(match[3])).padStart(2, "0"),
  };
}

function yearMonthParts(value) {
  const text = String(value ?? "");
  let match = text.match(/\b(20\d{2})-(\d{1,2})(?:-\d{1,2})?/);
  if (!match) match = text.match(/(20\d{2})年\s*(\d{1,2})月/);
  if (!match) return null;
  return {
    year: match[1],
    month: String(Number(match[2])).padStart(2, "0"),
  };
}

function issueParts(value) {
  const text = String(value ?? "");
  const match = text.match(/(20\d{2})年\s*第\s*(\d{1,2})\s*期/);
  if (!match) return null;
  const issueNo = Number(match[2]);
  const label = `${match[1]}年第${issueNo}期`;
  return { year: match[1], issueNo, label, value: label };
}

function defaultFilters(domain) {
  const config = domainConfigs[domain];
  return Object.fromEntries(
    (config?.filters || []).flatMap((filter) => {
      const entries = [[filter.key, "all"]];
      if (filter.kind === "region") entries.push([cityFilterKey(filter), "all"]);
      return entries;
    })
  );
}

function defaultCoverageFilters() {
  return {
    region_code: "51",
    region_code_city: "all",
    period_year: "2026",
    period_month: "all",
    target_level: "city",
    business_coverage_status: "all",
    source_completeness_status: "all",
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function activeConfig() {
  return domainConfigs[state.domain];
}

function metadata(item, key) {
  const value = item.metadata?.[key];
  if (value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "value")) return value.value ?? "";
  return value ?? "";
}

function usesArchiveBackedCostInfoOptions(filter = {}) {
  const costInfoFilterKeys = new Set(domainConfigs.cost_info.filters.map((item) => item.key));
  return state.domain === "cost_info" && state.viewMode === "archives" && !filter.coverage && costInfoFilterKeys.has(filter.key);
}

function filterValue(filter) {
  const filters = filter.coverage ? state.coverageFilters : state.filters;
  return filters[filter.key] || "all";
}

function cityFilterKey(filter) {
  return `${filter.key}_city`;
}

function cityFilterValue(filter) {
  const filters = filter.coverage ? state.coverageFilters : state.filters;
  return filters[cityFilterKey(filter)] || "all";
}

function regionFilterForCostInfo() {
  return domainConfigs.cost_info.filters.find((filter) => filter.kind === "region");
}

function createCostInfoDimensionNode() {
  return {
    archiveCount: 0,
    cities: new Map(),
    publisherOrgs: new Map(),
    years: new Set(),
    monthsByYear: new Map(),
    issuesByYear: new Map(),
  };
}

function addCostInfoPeriod(node, periodInfos) {
  node.archiveCount += 1;
  periodInfos.forEach((info) => {
    if (!info?.year) return;
    node.years.add(info.year);
    if (info.kind === "monthly" && info.month) {
      if (!node.monthsByYear.has(info.year)) node.monthsByYear.set(info.year, new Set());
      node.monthsByYear.get(info.year).add(info.month);
    }
    if (info.kind === "issue_based" && info.issueNo) {
      if (!node.issuesByYear.has(info.year)) node.issuesByYear.set(info.year, new Map());
      node.issuesByYear.get(info.year).set(info.value, info);
    }
  });
}

function costInfoPublisherTypeGroup(item) {
  const raw = String(item.publisher_type || metadata(item, "publisher_type") || "").trim().toLowerCase();
  if (!raw) return "";
  if (raw.includes("association") || raw.includes("协会")) return "association";
  if (
    raw.includes("official") ||
    raw.includes("government") ||
    raw.includes("housing") ||
    raw.includes("cost_station") ||
    raw.includes("住建") ||
    raw.includes("定额")
  ) {
    return "station";
  }
  return "";
}

function costInfoPublisherScope(item) {
  const raw = String(item.publisher_scope || metadata(item, "publisher_scope") || "").trim().toLowerCase();
  if (raw === "province" || raw === "city") return raw;
  return regionParts(item.region_code).publisher_scope;
}

function costInfoPublisherOrgInfo(item) {
  const group = costInfoPublisherTypeGroup(item);
  const scope = costInfoPublisherScope(item);
  if (!group || !scope) return null;
  const value = `${group}:${scope}`;
  const labels = {
    "station:province": "省站",
    "station:city": "市站",
    "association:province": "省协会",
    "association:city": "市协会",
  };
  const order = {
    "station:province": 10,
    "station:city": 20,
    "association:province": 30,
    "association:city": 40,
  };
  return { value, label: labels[value] || value, order: order[value] || 100 };
}

function costInfoScopeKey(publisherOrg = "all", province = "all", city = "all") {
  return `${publisherOrg || "all"}|${province || "all"}|${city || "all"}`;
}

function getCostInfoScopeNode(index, publisherOrg = "all", province = "all", city = "all") {
  const key = costInfoScopeKey(publisherOrg, province, city);
  if (!index.scopes.has(key)) index.scopes.set(key, createCostInfoDimensionNode());
  return index.scopes.get(key);
}

function addCostInfoScope(index, { publisherOrg = "all", province = "all", city = "all", periodInfos, publisherOrgInfo }) {
  const node = getCostInfoScopeNode(index, publisherOrg, province, city);
  addCostInfoPeriod(node, periodInfos);
  if (publisherOrgInfo) node.publisherOrgs.set(publisherOrgInfo.value, publisherOrgInfo);
  return node;
}

function costInfoDimensionIndex() {
  if (costInfoDimensionIndexCache.archives === state.archives && costInfoDimensionIndexCache.index) {
    return costInfoDimensionIndexCache.index;
  }
  const regionFilter = regionFilterForCostInfo();
  const index = { all: createCostInfoDimensionNode(), provinces: new Map(), scopes: new Map() };
  state.archives.forEach((item) => {
    const periodInfos = costInfoPeriodInfos(item, regionFilter);
    const publisherOrgInfo = costInfoPublisherOrgInfo(item);
    const publisherOrgValues = ["all", ...(publisherOrgInfo ? [publisherOrgInfo.value] : [])];
    const matchedRegions = [];
    regionTree.forEach((province) => {
      if (!archiveMatchesProvince(item, province.value, regionFilter)) return;
      matchedRegions.push({
        province,
        cities: province.cities.filter((city) => archiveMatchesCity(item, city.value, regionFilter)),
      });
    });

    publisherOrgValues.forEach((publisherOrg) => {
      addCostInfoScope(index, { publisherOrg, periodInfos, publisherOrgInfo });
      matchedRegions.forEach(({ province, cities }) => {
        const provinceNode = addCostInfoScope(index, {
          publisherOrg,
          province: province.value,
          periodInfos,
          publisherOrgInfo,
        });
        cities.forEach((city) => {
          provinceNode.cities.set(city.value, city);
          addCostInfoScope(index, {
            publisherOrg,
            province: province.value,
            city: city.value,
            periodInfos,
            publisherOrgInfo,
          });
        });
      });
    });
  });
  index.all = getCostInfoScopeNode(index);
  regionTree.forEach((province) => {
    const provinceNode = index.scopes.get(costInfoScopeKey("all", province.value, "all"));
    if (provinceNode) index.provinces.set(province.value, provinceNode);
  });
  costInfoDimensionIndexCache.archives = state.archives;
  costInfoDimensionIndexCache.index = index;
  return index;
}

function costInfoDimensionScope({ publisherOrg = "all", province = "all", city = "all" } = {}) {
  const index = costInfoDimensionIndex();
  return index.scopes.get(costInfoScopeKey(publisherOrg, province, city)) || createCostInfoDimensionNode();
}

function effectiveCostInfoPublisherOrg() {
  const selected = state.filters.publisher_org || "all";
  if (selected === "all" || state.domain !== "cost_info" || state.viewMode !== "archives") return selected;
  const regionFilter = regionFilterForCostInfo();
  const scope = costInfoDimensionScope({
    province: state.filters.region_code || "all",
    city: state.filters[cityFilterKey(regionFilter)] || "all",
  });
  return scope.publisherOrgs.has(selected) ? selected : "all";
}

function normalizeCostInfoPublisherOrgFilter() {
  if (state.domain !== "cost_info" || state.viewMode !== "archives") return;
  const effective = effectiveCostInfoPublisherOrg();
  if ((state.filters.publisher_org || "all") !== effective) state.filters.publisher_org = effective;
}

function selectedCostInfoDimensionScope() {
  const regionFilter = regionFilterForCostInfo();
  const selectedPublisherOrg = effectiveCostInfoPublisherOrg();
  const selectedProvince = state.filters.region_code || "all";
  const selectedCity = state.filters[cityFilterKey(regionFilter)] || "all";
  return costInfoDimensionScope({
    publisherOrg: selectedPublisherOrg,
    province: selectedProvince,
    city: selectedCity,
  });
}

function costInfoMonthsForScope(scope, selectedYear) {
  if (!scope) return new Set();
  if (selectedYear && selectedYear !== "all") return scope.monthsByYear.get(selectedYear) || new Set();
  const months = new Set();
  scope.monthsByYear.forEach((yearMonths) => yearMonths.forEach((month) => months.add(month)));
  return months;
}

function costInfoIssuesForScope(scope, selectedYear) {
  const issues = new Map();
  if (!scope) return issues;
  const addIssues = (yearIssues) => yearIssues.forEach((issue, value) => issues.set(value, issue));
  if (selectedYear && selectedYear !== "all") {
    addIssues(scope.issuesByYear.get(selectedYear) || new Map());
    return issues;
  }
  scope.issuesByYear.forEach(addIssues);
  return issues;
}

function costInfoPeriodCandidates(item, filter = {}) {
  const periodValues = [
    metadata(item, "period"),
    metadata(item, "period_raw"),
    metadata(item, "period_start"),
  ].filter((value) => value !== undefined && value !== null && value !== "");
  if (periodValues.length) return periodValues;
  return [
    item.publish_date,
    metadata(item, "publish_date_raw"),
    ...filterCandidates(item, filter),
  ].filter((value) => value !== undefined && value !== null && value !== "");
}

function costInfoPeriodKind(item) {
  return String(item.period_kind || metadata(item, "period_kind") || "").trim();
}

function costInfoPeriodInfos(item, filter = {}) {
  const configuredKind = costInfoPeriodKind(item);
  const periodYear = String(metadata(item, "period_year") || "").trim();
  const periodIssueNo = String(metadata(item, "period_issue_no") || "").trim();
  const infos = [];
  const addInfo = (info) => {
    if (!info?.year) return;
    const key = `${info.kind}:${info.year}:${info.month || info.issueNo || ""}`;
    if (!infos.some((entry) => `${entry.kind}:${entry.year}:${entry.month || entry.issueNo || ""}` === key)) infos.push(info);
  };

  costInfoPeriodCandidates(item, filter).forEach((candidate) => {
    if (configuredKind === "issue_based") {
      const issue = issueParts(candidate);
      if (issue) addInfo({ kind: "issue_based", ...issue });
      return;
    }
    const monthly = yearMonthParts(candidate);
    if (monthly) addInfo({ kind: "monthly", ...monthly });
  });

  if (configuredKind === "issue_based") {
    const projectedMonth = yearMonthParts(metadata(item, "period_start"));
    if (projectedMonth) {
      addInfo({ kind: "monthly", ...projectedMonth });
    } else {
      const month = Number(metadata(item, "period_month"));
      if (periodYear && month >= 1 && month <= 12) {
        addInfo({ kind: "monthly", year: periodYear, month: String(month).padStart(2, "0") });
      }
    }
  }

  if (configuredKind === "issue_based" && periodYear && periodIssueNo && Number(periodIssueNo)) {
    const issueNo = Number(periodIssueNo);
    addInfo({
      kind: "issue_based",
      year: periodYear,
      issueNo,
      label: `${periodYear}年第${issueNo}期`,
      value: `${periodYear}年第${issueNo}期`,
    });
  } else if (configuredKind === "issue_based" && periodYear) {
    addInfo({ kind: "issue_based", year: periodYear });
  }

  return infos;
}

function archiveRegionText(item, filter = {}) {
  return [metadata(item, "province_raw"), metadata(item, "city_raw"), ...filterCandidates(item, filter)]
    .join(" ")
    .toLowerCase();
}

function archiveMatchesProvince(item, provinceCode, filter = {}) {
  const value = String(provinceCode || "");
  if (!value || value === "all") return true;
  const itemCode = String(item.region_code || "");
  const province = regionTree.find((candidate) => candidate.value === value);
  if (itemCode === value || itemCode === `${value}0000` || itemCode.startsWith(value)) return true;
  return archiveRegionText(item, filter).includes(String(province?.label || value).toLowerCase());
}

function archiveMatchesCity(item, cityCode, filter = {}) {
  const value = String(cityCode || "");
  if (!value || value === "all") return true;
  const itemCode = String(item.region_code || "");
  const city = cityByCode(value);
  if (itemCode === value || (itemCode.length > 6 && itemCode.startsWith(value.slice(0, 4)))) return true;
  if (city && itemCode === `${city.province.value}0000` && city.province.cities.length === 1) return true;
  return archiveRegionText(item, filter).includes(String(city?.label || value).toLowerCase());
}

function cityByCode(cityCode) {
  const value = String(cityCode || "");
  for (const province of regionTree) {
    const city = province.cities.find((candidate) => candidate.value === value);
    if (city) return { ...city, province };
  }
  return null;
}

function uniqueOptions(values) {
  const options = [];
  values
    .map((value) => String(value ?? "").trim())
    .filter(Boolean)
    .forEach((value) => {
      if (!options.some((option) => option.value === value)) options.push({ label: value, value });
    });
  return options.sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
}

function metadataEnumOptions(filter) {
  const values = state.archives.map((item) => metadata(item, filter.metadataKey));
  return [{ label: "全部", value: "all" }, ...uniqueOptions(values)];
}

function filterOptions(filter) {
  if (!filter) return [{ label: "全部", value: "all" }];
  if (filter.options) return filter.options;
  if (filter.kind === "metadataEnum") return metadataEnumOptions(filter);
  if (filter.kind === "publisherOrg") return publisherOrgOptions(filter);
  if (filter.kind === "region") return regionOptions();
  if (filter.kind === "year") return yearOptions(filter);
  if (filter.kind === "month") return monthOptions(filter);
  if (filter.kind === "issue") return issueOptions(filter);
  if (filter.kind === "day") return dayOptions(filter);
  return [{ label: "全部", value: "all" }];
}

function filterCandidates(item, filter) {
  const fields = filter.fields || [];
  const metadataKeys = filter.metadataKeys || [];
  return [
    ...fields.map((field) => item[field]),
    ...metadataKeys.map((key) => metadata(item, key)),
  ].filter((value) => value !== undefined && value !== null && value !== "");
}

function matchesRegion(item, filter, value, option) {
  const itemCode = String(item.region_code || "");
  const selectedCity = cityFilterValue(filter);
  const selectedCityOption = cityOptions(value, filter).find((city) => city.value === selectedCity);
  const rawText = [metadata(item, "province_raw"), metadata(item, "city_raw"), ...filterCandidates(item, filter)]
    .join(" ")
    .toLowerCase();
  if (selectedCity !== "all") {
    const cityPrefix = selectedCity.slice(0, 4);
    if (itemCode === selectedCity || itemCode.startsWith(cityPrefix)) return true;
    return rawText.includes(String(selectedCityOption?.label || selectedCity).toLowerCase());
  }
  if (item.region_code && String(item.region_code).startsWith(value)) return true;
  return rawText.includes(String(option?.label || value).toLowerCase());
}

function matchesYear(item, filter, value) {
  if (usesArchiveBackedCostInfoOptions(filter)) {
    return costInfoPeriodInfos(item, filter).some((info) => info.year === value);
  }
  const candidates = [item.publish_date, metadata(item, "publish_date_raw"), metadata(item, "effective_date"), ...filterCandidates(item, filter)];
  return candidates.some((candidate) => String(candidate ?? "").includes(value));
}

function matchesMonth(item, filter, value) {
  if (usesArchiveBackedCostInfoOptions(filter)) {
    return costInfoPeriodInfos(item, filter).some((info) => info.kind === "monthly" && info.month === value);
  }
  const monthNumber = String(Number(value));
  const candidates = [metadata(item, "period_raw"), metadata(item, "period_start"), item.publish_date, ...filterCandidates(item, filter)];
  return candidates.some((candidate) => {
    const text = String(candidate ?? "");
    if (/^\d{4}-\d{2}/.test(text)) return text.slice(5, 7) === value;
    return new RegExp(`(^|[^0-9])0?${monthNumber}(月|[^0-9]|$)`).test(text);
  });
}

function matchesIssue(item, filter, value) {
  return costInfoPeriodInfos(item, filter).some((info) => info.kind === "issue_based" && info.value === value);
}

function matchesPublisherOrg(item, value) {
  return costInfoPublisherOrgInfo(item)?.value === value;
}

function matchesDay(item, filter, value) {
  const dayNumber = String(Number(value));
  const candidates = [item.publish_date, metadata(item, "publish_date_raw"), ...filterCandidates(item, filter)];
  return candidates.some((candidate) => {
    const text = String(candidate ?? "");
    if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(8, 10) === value;
    return new RegExp(`(^|[^0-9])0?${dayNumber}(日|号|[^0-9]|$)`).test(text);
  });
}

function matchesFileRole(item, value) {
  const roles = [
    primaryFile(item)?.file_role,
    ...(item.files || []).map((file) => file.file_role),
  ].filter(Boolean);
  return roles.includes(value);
}

function matchesFilter(item, filter) {
  const value = filterValue(filter);
  if (value === "all") return true;
  if (filter.key === "channel_type") return item.channel_type === value;
  if (filter.key === "priced_source") return value !== "yes" || hasPricedSource(item);
  if (filter.kind === "metadataEnum") return metadata(item, filter.metadataKey) === value;
  if (filter.kind === "publisherOrg") return matchesPublisherOrg(item, value);
  if (filter.kind === "region") return matchesRegion(item, filter, value, filterOptions(filter).find((option) => option.value === value));
  if (filter.kind === "year") return matchesYear(item, filter, value);
  if (filter.kind === "month") return matchesMonth(item, filter, value);
  if (filter.kind === "issue") return matchesIssue(item, filter, value);
  if (filter.kind === "day") return matchesDay(item, filter, value);
  if (filter.kind === "policyEffectiveStatus") return policyEffectiveStatus(item) === value;
  if (filter.kind === "fileRole") return matchesFileRole(item, value);
  return true;
}

function chinaToday() {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function policyEffectiveStatus(item) {
  const effective = metadata(item, "effective_date");
  const expiry = metadata(item, "expiry_date");
  if (!effective) return "未知";
  const today = chinaToday();
  if (effective > today) return "未生效";
  if (expiry && expiry < today) return "已失效";
  return "生效中";
}

function nowIso() {
  return new Date().toISOString();
}

function manualCell(value) {
  return { value, source_level: "manual", tagged_by: "ui:archive-desk", tagged_at: nowIso() };
}

function manualSource() {
  return { source_level: "manual", tagged_by: "ui:archive-desk", tagged_at: nowIso() };
}

function manualUploadCell(value) {
  return { value, source_level: "manual", tagged_by: "ui:manual-upload", tagged_at: nowIso() };
}

function manualUploadSource() {
  return { source_level: "manual", tagged_by: "ui:manual-upload", tagged_at: nowIso() };
}

function manualEditCell(value) {
  return { value, source_level: "manual", tagged_by: "ui:manual-edit", tagged_at: nowIso() };
}

function manualEditSource() {
  return { source_level: "manual", tagged_by: "ui:manual-edit", tagged_at: nowIso() };
}

function fileNameFromUpload(file) {
  return file?.name || file?.file_name || "";
}

function fileExtFromName(fileName) {
  const ext = String(fileName || "").split(".").pop() || "";
  return ext && ext !== fileName ? ext.toLowerCase() : "";
}

function selectedManualUploadFilters() {
  return state.viewMode === "coverage" ? state.coverageFilters : state.filters;
}

function selectedManualUploadRegionCode() {
  const filters = selectedManualUploadFilters();
  if (filters.region_code_city && filters.region_code_city !== "all") return filters.region_code_city;
  if (filters.region_code && filters.region_code !== "all") return provinceCodeForFilter(filters.region_code);
  return "";
}

function selectedManualUploadPeriod() {
  const filters = selectedManualUploadFilters();
  const year = filters.period_year && filters.period_year !== "all" ? filters.period_year : "";
  const month = filters.period_month && filters.period_month !== "all" ? filters.period_month : "";
  return year && month ? `${year}-${month}` : "";
}

function manualUploadDefaults() {
  return {
    region_code: selectedManualUploadRegionCode(),
    period: selectedManualUploadPeriod(),
    publisher: "",
    tax_type: "",
    title: "",
    uploaded_by: "",
  };
}

function regionParts(regionCode) {
  const code = String(regionCode || "");
  for (const province of regionTree) {
    if (code === province.value || code === `${province.value}0000`) {
      return { province: province.label, city: "", publisher_scope: "province" };
    }
    const city = province.cities.find((item) => item.value === code || (code.length > 6 && code.startsWith(item.value.slice(0, 4))));
    if (city) return { province: province.label, city: city.label, publisher_scope: "city" };
  }
  return { province: "", city: "", publisher_scope: "city" };
}

function parsabilityForManualUpload(ext) {
  if (["xls", "xlsx"].includes(ext)) return "structured";
  return "image_based";
}

function sourceAttachmentModeForManualUpload(ext) {
  if (["xls", "xlsx"].includes(ext)) return "xls_attachment";
  if (["zip", "rar", "7z", "cdz"].includes(ext)) return "zip_package";
  if (["jpg", "jpeg", "png"].includes(ext)) return "image_file";
  return "pdf_only";
}

function manualUploadSourceSiteId(regionCode) {
  return `cost_info.manual.${regionCode || "unknown"}`;
}

function buildManualUploadSourcePayload(values) {
  const parts = regionParts(values.region_code);
  const regionName = regionLabel(values.region_code) || values.region_code || "未选地区";
  const publisher = values.publisher || `${regionName}信息价人工补录`;
  return {
    source_scope: "platform_public",
    managed_by: "platform",
    source_type: "info_price",
    connector_type: "manual_upload",
    name: `${regionName}-人工补录信息价`,
    province: parts.province || null,
    city: parts.city || null,
    region_code: values.region_code || null,
    data_domain: "cost_info",
    format: values.file_ext || null,
    downloadable: true,
    bucket: "人工补录",
    frequency: "manual",
    status: "active",
    config: {
      stable: {
        site_id: manualUploadSourceSiteId(values.region_code),
        domain_type: "cost_info",
        region_code: values.region_code || null,
        coverage_region_code: values.region_code || null,
        publisher_scope: parts.publisher_scope,
        publisher_region_code: values.region_code || null,
        publisher_name: publisher,
      },
      ops: { source_audit_status: "人工补录" },
    },
  };
}

function buildManualUploadArchivePayload(ingest, values) {
  const ext = values.file_ext || fileExtFromName(values.file_name || values.title);
  const taxType = values.tax_type ? values.tax_type : null;
  const publisher = values.publisher || null;
  const businessKey = `cost_info:${ingest.source_id}:${values.region_code}:${values.period}:${values.title}`;
  const metadataPayload = {
    period: manualUploadCell(values.period),
    period_start: manualUploadCell(values.period),
    period_raw: manualUploadCell(values.period),
    coverage_region_code: manualUploadCell(values.region_code),
    price_source_type: manualUploadCell(values.price_source_type || "info_price"),
    tax_type: manualUploadCell(taxType),
    producer: manualUploadCell(publisher),
    publisher: manualUploadCell(publisher),
    publisher_scope: manualUploadCell(regionParts(values.region_code).publisher_scope),
    publisher_region_code: manualUploadCell(values.region_code),
    parsability: manualUploadCell(parsabilityForManualUpload(ext)),
    publication_mode: manualUploadCell("MANUAL_ONLY"),
    source_attachment_mode: manualUploadCell(sourceAttachmentModeForManualUpload(ext)),
    uploaded_by: manualUploadCell(values.uploaded_by),
  };
  return {
    event_id: ingest.ingest_event_id || ingest.event_id,
    domain_type: "cost_info",
    channel_type: "manual_upload",
    collection_method: "manual_denovo",
    business_key: businessKey,
    title: values.title,
    region_code: values.region_code || null,
    publish_date: null,
    visibility_scope: "public",
    status: "collected",
    metadata: metadataPayload,
    field_sources: {
      domain_type: manualUploadSource(),
      channel_type: manualUploadSource(),
      collection_method: manualUploadSource(),
      business_key: manualUploadSource(),
      title: manualUploadSource(),
      region_code: manualUploadSource(),
      publish_date: manualUploadSource(),
    },
    actor_type: "user",
    actor_id: "ui:manual-upload",
  };
}

function manualEditDefaults(item) {
  const file = primaryFile(item);
  const fileName = file?.file_name || item.title || "";
  return {
    region_code: item.region_code || metadata(item, "coverage_region_code") || "",
    period: metadata(item, "period") || metadata(item, "period_start") || metadata(item, "period_raw") || "",
    price_source_type: metadata(item, "price_source_type") || "info_price",
    publisher: metadata(item, "publisher") || metadata(item, "producer") || "",
    tax_type: metadata(item, "tax_type") || "",
    title: item.title || fileName,
    file_name: fileName,
    file_ext: fileExtFromName(fileName),
    uploaded_by: metadata(item, "uploaded_by") || "",
  };
}

function buildManualEditPatch(item, values) {
  if (!item?.source_id) throw new Error("缺少 source_id，无法重算 business_key。");
  const taxType = values.tax_type ? values.tax_type : null;
  const publisher = values.publisher || null;
  const businessKey = `cost_info:${item.source_id}:${values.region_code}:${values.period}:${values.title}`;
  return {
    business_key: businessKey,
    title: values.title,
    region_code: values.region_code || null,
    publish_date: item.publish_date || null,
    metadata: {
      period: manualEditCell(values.period),
      period_start: manualEditCell(values.period),
      period_raw: manualEditCell(values.period),
      coverage_region_code: manualEditCell(values.region_code),
      price_source_type: manualEditCell(values.price_source_type || "info_price"),
      tax_type: manualEditCell(taxType),
      producer: manualEditCell(publisher),
      publisher: manualEditCell(publisher),
      publisher_scope: manualEditCell(regionParts(values.region_code).publisher_scope),
      publisher_region_code: manualEditCell(values.region_code),
      uploaded_by: manualEditCell(values.uploaded_by),
    },
    field_sources: {
      business_key: manualEditSource(),
      title: manualEditSource(),
      region_code: manualEditSource(),
      publish_date: manualEditSource(),
    },
    actor_type: "user",
    actor_id: "ui:manual-edit",
  };
}

function sameFormValue(nextValue, currentValue) {
  return String(nextValue ?? "") === String(currentValue ?? "");
}

function existingMetadataCell(key) {
  const cell = state.selectedArchive?.metadata?.[key];
  return cell && typeof cell === "object" && Object.prototype.hasOwnProperty.call(cell, "source_level") ? cell : null;
}

function metadataCellForPatch(key, value) {
  const existingCell = existingMetadataCell(key);
  if (existingCell && sameFormValue(value, existingCell.value)) return existingCell;
  return manualCell(value);
}

function fieldSourceForPatch(fieldName, nextValue, currentValue) {
  const existingSource = state.selectedArchive?.field_sources?.[fieldName];
  if (existingSource && sameFormValue(nextValue, currentValue)) return existingSource;
  return manualSource();
}

function preservedFieldSource(fieldName) {
  return state.selectedArchive?.field_sources?.[fieldName] || manualSource();
}

function normalizeFile(file) {
  if (!file) return null;
  const fileName = file.file_name || file.display_name || "未命名文件";
  const ext = (file.file_ext || fileName.split(".").pop() || "").replace(".", "").toLowerCase();
  return { ...file, file_name: fileName, file_ext: ext };
}

function primaryFile(item) {
  return normalizeFile(item.primary_file) || normalizeFile(item.files?.find((file) => file.is_primary)) || normalizeFile(item.files?.[0]);
}

function defaultViewerFile(item) {
  const files = archiveFiles(item);
  if (item?.domain_type === "trading") {
    return files.find((file) => file.file_role === "web_snapshot" && ["html", "htm"].includes(file.file_ext)) || primaryFile(item);
  }
  return primaryFile(item);
}

function downloadUrl(file) {
  if (!file?.file_id) return "";
  return `/api/file-assets/${encodeURIComponent(file.file_id)}/download`;
}

function previewUrl(file) {
  if (!file?.file_id) return "";
  return `/api/file-assets/${encodeURIComponent(file.file_id)}/preview`;
}

let activePdfViewer = null;
let pdfViewerLoadToken = 0;
let pdfViewerModulePromise = null;
const LARGE_PDF_FAST_PREVIEW_THRESHOLD = 64 * 1024 * 1024;
const pdfLinearizationCache = new Map();

function destroyActivePdfViewer() {
  pdfViewerLoadToken += 1;
  activePdfViewer?.controller?.destroy?.();
  activePdfViewer = null;
}

function renderPdfJsViewer(file) {
  const viewerCanvas = $("#viewerCanvas");
  if (activePdfViewer?.fileId === file.file_id && viewerCanvas.querySelector?.('[data-pdf-viewer="pdfjs"]')) return;

  destroyActivePdfViewer();
  const loadToken = pdfViewerLoadToken;
  const selectedFileId = file.file_id;
  viewerCanvas.innerHTML = `
    <div class="empty-state pdfjs-bootstrap-state" role="status">
      <strong>正在启动 PDF.js</strong>
      <span>准备 Range 按需预览：${escapeHtml(file.file_name)}</span>
    </div>
  `;
  resetViewerScroll();

  if (!pdfViewerModulePromise) {
    const assetBase = window.__uiAssetBase || "/ui-assets/";
    const assetVersion = window.__uiAssetVersion || "pdfjs";
    pdfViewerModulePromise = import(`${assetBase}pdf-viewer.js?v=${encodeURIComponent(assetVersion)}`);
  }
  pdfViewerModulePromise
    .then(({ mountPdfViewer }) => {
      if (loadToken !== pdfViewerLoadToken || selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      const controller = mountPdfViewer(viewerCanvas, {
        url: previewUrl(file),
        fileName: file.file_name,
        fileSize: file.file_size,
      });
      activePdfViewer = { fileId: selectedFileId, controller };
    })
    .catch((error) => {
      if (loadToken !== pdfViewerLoadToken || selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      viewerCanvas.innerHTML = `
        <article class="web-page source-only">
          <h2>${escapeHtml(file.file_name)}</h2>
          <p>PDF.js 加载失败：${escapeHtml(error?.message || "请下载原件查看")}</p>
        </article>
      `;
      resetViewerScroll();
    });
}

async function isPdfLinearized(file) {
  if (!file?.file_id) return false;
  if (pdfLinearizationCache.has(file.file_id)) return pdfLinearizationCache.get(file.file_id);

  const probe = fetch(previewUrl(file), { headers: { Range: "bytes=0-4095" } })
    .then(async (response) => {
      if (!response.ok) return false;
      const header = new TextDecoder("latin1").decode(await response.arrayBuffer());
      return /\/Linearized\b/.test(header);
    })
    .catch(() => false);
  pdfLinearizationCache.set(file.file_id, probe);
  return probe;
}

function findBytePair(bytes, first, second, fromIndex = 0) {
  for (let index = Math.max(0, fromIndex); index < bytes.length - 1; index += 1) {
    if (bytes[index] === first && bytes[index + 1] === second) return index;
  }
  return -1;
}

function appendBytes(left, right) {
  const joined = new Uint8Array(left.length + right.length);
  joined.set(left);
  joined.set(right, left.length);
  return joined;
}

async function fetchFirstPdfJpeg(url, signal) {
  const chunkSize = 1024 * 1024;
  const maxBytes = 8 * chunkSize;
  let bytes = new Uint8Array(0);
  let jpegStart = -1;

  for (let start = 0; start < maxBytes; start += chunkSize) {
    const response = await fetch(url, {
      headers: { Range: `bytes=${start}-${start + chunkSize - 1}` },
      signal,
    });
    if (response.status !== 206) return null;
    const chunk = new Uint8Array(await response.arrayBuffer());
    bytes = appendBytes(bytes, chunk);
    if (jpegStart < 0) jpegStart = findBytePair(bytes, 0xff, 0xd8);
    if (jpegStart >= 0) {
      const jpegEnd = findBytePair(bytes, 0xff, 0xd9, jpegStart + 2);
      if (jpegEnd >= 0) return new Blob([bytes.slice(jpegStart, jpegEnd + 2)], { type: "image/jpeg" });
    }
    if (chunk.length < chunkSize) break;
  }
  return null;
}

function renderNativePdfFastViewer(file) {
  const viewerCanvas = $("#viewerCanvas");
  destroyActivePdfViewer();
  viewerCanvas.innerHTML = `
    <article class="pdf-native-fast" data-pdf-viewer="native-fast" aria-label="超大 PDF 极速预览器">
      <header class="pdfjs-toolbar pdf-native-fast-toolbar">
        <div class="pdf-native-fast-state">
          <span class="pdfjs-range-dot" aria-hidden="true"></span>
          <strong>首屏极速预览</strong>
          <span>先读取第一页，完整文档随后加载</span>
        </div>
        <div class="pdfjs-toolbar-group pdf-native-actions">
          <button type="button" class="pdfjs-zoom-label pdf-native-open-full" data-show-full>打开完整文档</button>
          <button type="button" class="pdfjs-zoom-label pdf-native-switch" data-use-pdfjs>切换 PDF.js</button>
        </div>
      </header>
      <div class="pdf-native-stage" data-native-stage>
        <div class="pdf-native-poster" data-native-poster>
          <div class="pdfjs-loading" data-native-poster-loading role="status">
            <span class="pdfjs-spinner" aria-hidden="true"></span>
            <strong>正在提取第一页</strong>
            <span>只读取少量原件数据</span>
          </div>
          <img data-native-poster-image alt="${escapeHtml(file.file_name)} 第一页" hidden />
          <span class="pdf-native-poster-badge" hidden data-native-poster-badge>首屏快速预览 · 完整文档后台加载中</span>
        </div>
        <iframe
          class="pdf-native-frame"
          src="about:blank"
          title="${escapeHtml(file.file_name)}"
        ></iframe>
      </div>
    </article>
  `;
  resetViewerScroll();

  const stage = viewerCanvas.querySelector("[data-native-stage]");
  const frame = viewerCanvas.querySelector(".pdf-native-frame");
  const poster = viewerCanvas.querySelector("[data-native-poster]");
  const posterLoading = viewerCanvas.querySelector("[data-native-poster-loading]");
  const posterImage = viewerCanvas.querySelector("[data-native-poster-image]");
  const posterBadge = viewerCanvas.querySelector("[data-native-poster-badge]");
  const showFullButton = viewerCanvas.querySelector("[data-show-full]");
  const switchButton = viewerCanvas.querySelector("[data-use-pdfjs]");
  const events = new AbortController();
  let posterObjectUrl = "";

  function startFullDocument() {
    if (frame?.getAttribute("src") === "about:blank") frame.src = previewUrl(file);
  }

  function showFullDocument() {
    startFullDocument();
    stage?.classList.add("show-full");
  }

  showFullButton?.addEventListener("click", showFullDocument, { signal: events.signal });
  switchButton?.addEventListener("click", () => renderPdfJsViewer(file), { signal: events.signal });

  fetchFirstPdfJpeg(previewUrl(file), events.signal)
    .then((jpeg) => {
      if (events.signal.aborted) return;
      if (!jpeg) {
        showFullDocument();
        return;
      }
      posterObjectUrl = URL.createObjectURL(jpeg);
      posterImage.addEventListener(
        "load",
        () => {
          posterLoading.hidden = true;
          posterImage.hidden = false;
          posterBadge.hidden = false;
          poster?.classList.add("is-ready");
          startFullDocument();
        },
        { once: true, signal: events.signal }
      );
      posterImage.src = posterObjectUrl;
    })
    .catch(() => {
      if (!events.signal.aborted) showFullDocument();
    });

  activePdfViewer = {
    fileId: file.file_id,
    controller: {
      destroy() {
        events.abort();
        if (posterObjectUrl) URL.revokeObjectURL(posterObjectUrl);
        if (frame) frame.src = "about:blank";
      },
    },
  };
}

function renderAdaptivePdfViewer(file) {
  const fileSize = Number(file?.file_size) || 0;
  if (fileSize < LARGE_PDF_FAST_PREVIEW_THRESHOLD) {
    renderPdfJsViewer(file);
    return;
  }

  const viewerCanvas = $("#viewerCanvas");
  destroyActivePdfViewer();
  const loadToken = pdfViewerLoadToken;
  const selectedFileId = file.file_id;
  viewerCanvas.innerHTML = `
    <div class="empty-state pdfjs-bootstrap-state" role="status">
      <strong>正在选择最快预览方式</strong>
      <span>正在检测超大 PDF 是否支持快速 Web 查看</span>
    </div>
  `;
  resetViewerScroll();

  isPdfLinearized(file).then((linearized) => {
    if (loadToken !== pdfViewerLoadToken || selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
    if (linearized) renderPdfJsViewer(file);
    else renderNativePdfFastViewer(file);
  });
}

function zipPreviewUrl(file) {
  if (!file?.file_id) return "";
  return `/api/file-assets/${encodeURIComponent(file.file_id)}/zip-preview`;
}

function zipEntryPreviewUrl(file, entryIndex) {
  if (!file?.file_id) return "";
  return `/api/file-assets/${encodeURIComponent(file.file_id)}/zip-preview/${encodeURIComponent(entryIndex)}`;
}

function fileType(file) {
  const normalized = normalizeFile(file);
  return typeConfig[normalized?.file_ext] || typeConfig.pdf;
}

function archiveFiles(item) {
  return [...(item.files || [])].map(normalizeFile).filter(Boolean).sort((a, b) => (a.sort_order || 100) - (b.sort_order || 100));
}

function archiveSource(item) {
  return item.source_url || item.source_item_key || item.source_id || "";
}

function hasPricedSource(item) {
  return Number(item.priced_source_count || 0) > 0 || archiveFiles(item).some((file) => file.file_role === "priced_source");
}

function selectedCostInfoYearPeriodOrder(item) {
  const selectedYear = String(state.filters.period_year || "all");
  if (state.domain !== "cost_info" || selectedYear === "all") return null;
  const sequences = costInfoPeriodInfos(item, regionFilterForCostInfo())
    .filter((info) => info.year === selectedYear)
    .map((info) => Number(info.kind === "monthly" ? info.month : info.issueNo))
    .filter((value) => Number.isFinite(value) && value > 0);
  return sequences.length ? Math.min(...sequences) : Number.POSITIVE_INFINITY;
}

function publicationDateSortValue(item) {
  const parts = dateParts(item.publish_date || metadata(item, "publish_date_raw"));
  if (!parts) return Number.NEGATIVE_INFINITY;
  return Number(`${parts.year}${parts.month}${parts.day}`);
}

function newestPublicationFirst(left, right) {
  const publicationDifference = publicationDateSortValue(right) - publicationDateSortValue(left);
  if (publicationDifference) return publicationDifference;
  const createdDifference = String(right.created_at || "").localeCompare(String(left.created_at || ""));
  if (createdDifference) return createdDifference;
  return String(right.archive_id || "").localeCompare(String(left.archive_id || ""));
}

function filteredArchives() {
  const query = state.search.trim().toLowerCase();
  const rows = state.archives.filter((item) => {
    if (state.status !== "all" && item.status !== state.status) return false;
    if (!activeVisibleFilters().every((filter) => matchesFilter(item, filter))) return false;
    if (!query) return true;
    const haystack = [
      item.title,
      item.business_key,
      item.source_item_key,
      item.source_url,
      metadata(item, "project_code_raw"),
      metadata(item, "notice_type_raw"),
      metadata(item, "exchange_name"),
      metadata(item, "project_name_raw"),
      metadata(item, "publisher"),
      ...(activeConfig().metadataFields || []).map((field) => metadata(item, field.key)),
      item.region_code,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
  if (state.domain === "trading") {
    // The API applies the same ordering. Keep the browser-side list stable
    // while paged hydration is still receiving older API pages.
    rows.sort(newestPublicationFirst);
  } else if (state.domain === "cost_info" && state.filters.period_year && state.filters.period_year !== "all") {
    rows.sort((left, right) => selectedCostInfoYearPeriodOrder(left) - selectedCostInfoYearPeriodOrder(right));
  }
  return rows;
}

function costInfoArchiveHasIssuePeriod(item) {
  if (state.domain !== "cost_info") return false;
  return costInfoPeriodInfos(item, regionFilterForCostInfo()).some((info) => info.kind === "issue_based");
}

function costInfoMonthIssueFilterHint() {
  if (state.domain !== "cost_info" || state.viewMode !== "archives") return "";
  if (!state.filters.period_month || state.filters.period_month === "all") return "";
  const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
  const filtersWithoutMonth = activeVisibleFilters().filter((filter) => filter.key !== monthFilter?.key);
  const filteredOutIssueCount = state.archives.filter((item) => {
    if (state.status !== "all" && item.status !== state.status) return false;
    if (!costInfoArchiveHasIssuePeriod(item)) return false;
    return filtersWithoutMonth.every((filter) => matchesFilter(item, filter));
  }).length;
  return filteredOutIssueCount ? "期号制源请清除月份按年查看" : "";
}

function renderCostInfoMonthIssueHint() {
  const hint = costInfoMonthIssueFilterHint();
  if (!hint) return "";
  return `
    <div class="filter-row sub-filter">
      <div class="filter-label">提示</div>
      <div class="filter-options">
        <span class="filter-chip">${escapeHtml(hint)}</span>
      </div>
    </div>
  `;
}

function apiUrl(path, params = {}) {
  const url = new URL(path, window.location?.origin || "http://127.0.0.1");
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "" && value !== "all") {
      url.searchParams.set(key, value);
    }
  });
  return url.pathname + url.search;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await responseErrorText(response));
  return response.json();
}

async function responseErrorText(response) {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    return body.detail || text || "请求失败";
  } catch {
    return text || "请求失败";
  }
}

async function ensureManualUploadSource(values) {
  const siteId = manualUploadSourceSiteId(values.region_code);
  const sources = await requestJson(apiUrl("/api/data-sources", { source_type: "info_price" }));
  const existing = sources.find((source) => {
    const stable = source.config?.stable || {};
    return (
      source.data_domain === "cost_info" &&
      source.connector_type === "manual_upload" &&
      (stable.site_id === siteId || String(source.region_code || "") === String(values.region_code || ""))
    );
  });
  if (existing) return existing;
  return requestJson("/api/data-sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildManualUploadSourcePayload(values)),
  });
}

function manualUploadSourceItemKey(values) {
  return `manual:${values.region_code || ""}:${values.period || ""}:${values.title || values.file_name || ""}`;
}

async function submitManualUpload(entries, values) {
  if (!Array.isArray(entries) || !entries.length) throw new Error("请至少选择一个文件。");
  if (!values.region_code) throw new Error("请选择地区。");
  if (!values.period) throw new Error("请填写期次。");
  if (!values.title) throw new Error("请填写标题。");

  const base = { ...values, price_source_type: values.price_source_type || "info_price" };
  const source = await ensureManualUploadSource(base);
  const batchId = `manual-${values.region_code}-${values.period}-${Date.now()}`;

  const ingested = [];
  for (const entry of entries) {
    const perFile = { ...base, file_name: entry.file.name, file_ext: fileExtFromName(entry.file.name) };
    const formData = new FormData();
    formData.append("file", entry.file);
    formData.append("tenant_code", "platform_public");
    formData.append("source_type", "info_price");
    formData.append("batch_id", batchId);
    formData.append("source_id", source.source_id);
    formData.append("source_item_key", manualUploadSourceItemKey(perFile));
    formData.append("derive_tasks", "false");
    formData.append(
      "source_metadata",
      JSON.stringify({
        channel_type: "manual_upload",
        collection_method: "manual_denovo",
        region_code: values.region_code,
        period: values.period,
      })
    );
    const res = await requestJson("/api/file-assets/ingest", { method: "POST", body: formData });
    if (res.duplicated && Array.isArray(res.existing_archives) && res.existing_archives.length) {
      const where = res.existing_archives.map((a) => `《${a.title}》`).join("、");
      throw new Error(`文件「${entry.file.name}」已是重复文件，已挂在 ${where}。请移除该文件，或改用「补充文件」。`);
    }
    ingested.push({ ...res, role: entry.role, file_name: entry.file.name });
  }

  const primaryIndex = Math.max(0, ingested.findIndex((e) => e.role === "main_document"));
  const primary = ingested[primaryIndex];
  const primaryValues = { ...base, file_name: primary.file_name, file_ext: fileExtFromName(primary.file_name) };
  const archive = await requestJson("/api/archives/from-ingest-event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildManualUploadArchivePayload({ ...primary, source_id: source.source_id }, primaryValues)),
  });

  let extraCount = 0;
  for (const entry of ingested) {
    if (entry.file_id === primary.file_id) continue;
    const role = entry.role === "main_document" ? "attachment" : entry.role;
    await requestJson(`/api/archives/${encodeURIComponent(archive.archive_id)}/files`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: entry.file_id,
        file_role: role,
        display_name: entry.file_name,
        actor_type: "user",
        actor_id: "ui:manual-upload",
      }),
    });
    extraCount += 1;
  }

  return { archive, attached_to_existing: Boolean(archive.attached_to_existing), extraCount };
}

async function submitManualSupplement(archive, entries) {
  if (!archive?.archive_id) throw new Error("缺少目标档案。");
  if (!Array.isArray(entries) || !entries.length) throw new Error("请至少选择一个文件。");

  const tenantCode = archive.tenant_code || "platform_public";
  const batchId = `supplement-${archive.archive_id}-${Date.now()}`;
  let attachedCount = 0;
  let skippedCount = 0;
  const skippedFiles = [];
  const attachedFiles = [];

  for (const entry of entries) {
    const formData = new FormData();
    formData.append("file", entry.file);
    formData.append("tenant_code", tenantCode);
    formData.append("source_type", "info_price");
    formData.append("batch_id", batchId);
    formData.append("source_id", archive.source_id);
    formData.append(
      "source_metadata",
      JSON.stringify({
        channel_type: "manual_upload",
        collection_method: "manual_recovery",
        region_code: archive.region_code || null,
      })
    );
    const res = await requestJson("/api/file-assets/ingest", { method: "POST", body: formData });

    const alreadyHere =
      res.duplicated &&
      Array.isArray(res.existing_archives) &&
      res.existing_archives.some((a) => a.archive_id === archive.archive_id);
    if (alreadyHere) {
      skippedCount += 1;
      skippedFiles.push(entry.file.name);
      continue;
    }
    const role = entry.role === "main_document" ? "attachment" : entry.role;
    await requestJson(`/api/archives/${encodeURIComponent(archive.archive_id)}/files`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: res.file_id,
        file_role: role,
        display_name: entry.file.name,
        actor_type: "user",
        actor_id: "ui:manual-upload",
      }),
    });
    attachedCount += 1;
    attachedFiles.push(entry.file.name);
  }

  return { attachedCount, skippedCount, skippedFiles, attachedFiles };
}

async function submitManualEdit(item, values) {
  if (!item?.archive_id) throw new Error("缺少档案记录，无法保存。");
  if (item.channel_type !== "manual_upload") throw new Error("只允许编辑人工上传记录。");
  if (!values.region_code) throw new Error("请选择地区。");
  if (!values.period) throw new Error("请填写期次。");
  if (!values.title) throw new Error("请填写标题。");
  return requestJson(`/api/archives/${encodeURIComponent(item.archive_id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildManualEditPatch(item, values)),
  });
}

async function deleteManualArchive(item) {
  if (!item?.archive_id) throw new Error("缺少档案记录，无法删除。");
  if (item.channel_type !== "manual_upload") throw new Error("只允许删除人工上传记录。");
  const response = await fetch(`/api/archives/${encodeURIComponent(item.archive_id)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await responseErrorText(response));
  return response.json();
}

async function withdrawArchive(item) {
  if (!item?.archive_id) throw new Error("缺少档案记录，无法撤下。");
  return requestJson(`/api/archives/${encodeURIComponent(item.archive_id)}/withdraw`, {
    method: "POST",
  });
}

async function loadArchives() {
  const loadToken = state.archiveLoadToken + 1;
  state.archiveLoadToken = loadToken;
  state.loading = true;
  state.error = "";
  state.archiveHydrating = false;
  state.archiveHydrationError = "";
  state.archives = [];
  state.archiveTotalCount = 0;
  renderApiState();
  try {
    const baseParams = {
      domain_type: state.domain,
      status: state.status === "all" ? undefined : state.status,
      channel_type: state.filters.channel_type,
      search: state.search.trim(),
    };
    const response = await fetch(apiUrl("/api/archives", { ...baseParams, limit: ARCHIVE_INITIAL_PAGE_SIZE, offset: 0 }));
    if (!response.ok) throw new Error(await response.text());
    const rows = await response.json();
    if (loadToken !== state.archiveLoadToken) return;
    const totalHeader = response.headers?.get?.("x-total-count");
    const totalCount = totalHeader === null || totalHeader === undefined || totalHeader === "" ? NaN : Number(totalHeader);
    const hasTotalCount = Number.isFinite(totalCount);
    const needsHydration = hasTotalCount
      ? rows.length < totalCount
      : rows.length === ARCHIVE_INITIAL_PAGE_SIZE;

    state.archives = rows;
    state.archiveTotalCount = hasTotalCount ? totalCount : rows.length;
    state.loading = false;
    state.archiveHydrating = needsHydration;
    renderAll();

    if (needsHydration) {
      void hydrateArchives({ baseParams, offset: rows.length, totalCount: hasTotalCount ? totalCount : null, loadToken });
    }
  } catch (error) {
    if (loadToken !== state.archiveLoadToken) return;
    state.archives = [];
    state.archiveTotalCount = 0;
    state.error = error.message || "档案列表加载失败";
  } finally {
    if (loadToken !== state.archiveLoadToken) return;
    state.loading = false;
    renderAll();
  }
}

async function hydrateArchives({ baseParams, offset, totalCount, loadToken }) {
  let nextOffset = offset;
  try {
    while (true) {
      const response = await fetch(apiUrl("/api/archives", { ...baseParams, limit: ARCHIVE_PAGE_SIZE, offset: nextOffset }));
      if (!response.ok) throw new Error(await response.text());
      const rows = await response.json();
      if (loadToken !== state.archiveLoadToken) return;
      if (!rows.length) break;

      // 每次替换数组，确保地区/期次维度缓存会按新数据重新计算。
      state.archives = [...state.archives, ...rows];
      nextOffset += rows.length;
      renderApiState();

      if (Number.isFinite(totalCount) && state.archives.length >= totalCount) break;
      if (!Number.isFinite(totalCount) && rows.length < ARCHIVE_PAGE_SIZE) break;
    }
    if (loadToken !== state.archiveLoadToken) return;
    state.archiveTotalCount = Number.isFinite(totalCount) ? totalCount : state.archives.length;
    state.archiveHydrating = false;
    renderAll();
  } catch (error) {
    if (loadToken !== state.archiveLoadToken) return;
    state.archiveHydrating = false;
    state.archiveHydrationError = error.message || "完整档案列表补全失败";
    renderAll();
  }
}

async function loadCostInfoSourceDimensions() {
  if (state.domain !== "cost_info") return;
  try {
    const sources = await requestJson(apiUrl("/api/data-sources", { source_type: "info_price" }));
    state.costInfoSources = sources.filter((source) => {
      if (source.data_domain && source.data_domain !== "cost_info") return false;
      return Boolean(costInfoSourceRegionCode(source));
    });
  } catch {
    state.costInfoSources = [];
  }
}

function provinceCodeForFilter(value) {
  if (!value || value === "all") return undefined;
  return `${value}0000`;
}

function coverageEndYear() {
  const parsed = Number(state.coverageFilters.period_year);
  if (Number.isInteger(parsed) && parsed >= 2000) return parsed;
  return DEFAULT_COVERAGE_END_YEAR;
}

function coverageYearRange() {
  const endYear = coverageEndYear();
  return {
    startYear: endYear - COVERAGE_HISTORY_YEAR_COUNT + 1,
    endYear,
  };
}

function coveragePeriodParams() {
  const year = String(coverageEndYear());
  const month = state.coverageFilters.period_month;
  if (month && month !== "all") {
    const period = `${year}-${month}`;
    return { start_period: period, end_period: period };
  }
  const range = coverageYearRange();
  return { start_period: `${range.startYear}-01`, end_period: `${range.endYear}-12` };
}

async function fetchWithRetry(url, options = {}) {
  const timeout = options.timeout || 30000;
  const retries = options.retries ?? 3;
  const baseDelay = options.baseDelay || 1000;
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      if (!response.ok) throw new Error(await response.text());
      return response;
    } catch (error) {
      lastError = error;
      if (attempt < retries && (error.name === "AbortError" || error.name === "TypeError" || error.message.includes("NetworkError"))) {
        await new Promise((resolve) => setTimeout(resolve, baseDelay * Math.pow(2, attempt)));
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

function coverageMatrixUrl() {
  return apiUrl("/api/info-price/coverage-matrix", {
    ...coveragePeriodParams(),
    province_code: provinceCodeForFilter(state.coverageFilters.region_code),
    target_level: state.coverageFilters.target_level,
  });
}

async function loadCoverageMatrix() {
  const loadToken = state.coverageLoadToken + 1;
  state.coverageLoadToken = loadToken;
  state.loading = true;
  state.error = "";
  // 过滤条件已经变化时，不能把上一地区的数据挂在新标题下等待请求完成。
  // 保留明确的加载态，避免“北京标题 + 成都表格”这类短暂错配。
  state.coverageRows = [];
  renderAll();
  try {
    const response = await fetchWithRetry(coverageMatrixUrl(), { timeout: 30000, retries: 3 });
    const rows = await response.json();
    if (loadToken !== state.coverageLoadToken || state.viewMode !== "coverage") return;
    state.coverageRows = rows;
  } catch (error) {
    if (loadToken !== state.coverageLoadToken || state.viewMode !== "coverage") return;
    state.coverageRows = [];
    state.error = error.name === "AbortError" ? "覆盖矩阵加载超时，请刷新重试" : (error.message || "覆盖矩阵加载失败");
  } finally {
    if (loadToken !== state.coverageLoadToken || state.viewMode !== "coverage") return;
    state.loading = false;
    renderAll();
  }
}

async function postJson(path, body) {
  return requestJson(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

async function runIncrementalCrawlAll() {
  if (state.crawlBusy) return;
  if (!window.confirm("将对所有已启用采集的信息价源执行一次增量抓取（抓各地最新期次）。系统会根据文件 SHA-256 指纹自动跳过重复文件。确认执行？")) return;
  state.crawlBusy = true;
  showToast("增量全网：调度中…", "success");
  renderAll();
  try {
    const scheduler = await postJson("/api/crawler/scheduler/run", {
      dry_run: false,
      force: true,
      trigger: "coverage_ui_all",
    });
    const created = Number(scheduler.task_created || 0);
    let workerSummary = {};
    if (created > 0) {
      const workerResult = await postJson("/api/crawler/worker/run", {
        dry_run: false,
        limit: 50,
        trigger: "coverage_ui_all",
      });
      workerSummary = workerResult.summary || {};
    }
    const dupCount = Number(workerSummary.duplicate_count || 0);
    const archiveCount = Number(workerSummary.archive_created_count || 0);
    const failCount = Number(workerSummary.failed_count || 0);
    const parts = [`新建任务 ${created} 个`];
    if (archiveCount) parts.push(`新建档案 ${archiveCount}`);
    if (dupCount) parts.push(`重复跳过 ${dupCount}`);
    if (failCount) parts.push(`异常 ${failCount}`);
    parts.push(`进行中跳过 ${scheduler.task_skipped_pending || 0}`);
    parts.push(`未启用 ${scheduler.task_skipped_disabled || 0}`);
    const toastKind = failCount ? "error" : !archiveCount && dupCount ? "warning" : "success";
    showToast(`增量全网完成：${parts.join(" · ")}`, toastKind);
    await loadCoverageMatrix();
  } catch (error) {
    showToast(`增量全网失败：${error.message || error}`, "error");
  } finally {
    state.crawlBusy = false;
    renderAll();
  }
}

function coverageSourceUrl(source) {
  return String(
    source?.url || source?.base_url || source?.config?.stable?.entry_url || source?.config?.audit?.evidence_url || ""
  ).trim();
}

function safeExternalUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}

function isCoverageDirectorySource(source) {
  return source?.config?.source_shape?.acquisition === "coverage_directory_only";
}

function coverageSourceCandidates(row) {
  const sourceIds = new Set(Array.isArray(row?.source_ids) ? row.source_ids : []);
  const rowRegionCode = String(row?.coverage_region_code || "");
  const candidates = (state.costInfoSources || [])
    .filter((source) => sourceIds.has(source.source_id) && source.status === "active")
    .filter((source) => !isCoverageDirectorySource(source))
    .filter((source) => Boolean(coverageSourceUrl(source)))
    .sort((left, right) => {
      const leftExact = costInfoSourceRegionCode(left) === rowRegionCode ? 0 : 1;
      const rightExact = costInfoSourceRegionCode(right) === rowRegionCode ? 0 : 1;
      if (leftExact !== rightExact) return leftExact - rightExact;
      const leftOfficial = left?.config?.stable?.publisher_type ? 0 : 1;
      const rightOfficial = right?.config?.stable?.publisher_type ? 0 : 1;
      if (leftOfficial !== rightOfficial) return leftOfficial - rightOfficial;
      return String(left.name || "").localeCompare(String(right.name || ""), "zh-CN");
    });

  const seenUrls = new Set();
  return candidates.filter((source) => {
    const key = safeExternalUrl(coverageSourceUrl(source)).replace(/\/$/, "") || source.source_id;
    if (seenUrls.has(key)) return false;
    seenUrls.add(key);
    return true;
  });
}

function coverageBackfillIsAvailable(row) {
  if (!row || row.business_coverage_status === "covered") return false;
  if (isFutureCoveragePeriod(row.period)) return false;
  return coverageSourceCandidates(row).length > 0;
}

function closeCoverageBackfillDialog() {
  state.coverageBackfill = {
    open: false,
    row: null,
    sourceId: "",
    submitting: false,
    error: "",
  };
  renderCoverageBackfillModal();
}

function openCoverageBackfillDialog(regionCode, period) {
  const row = state.coverageRows.find(
    (item) => String(item.coverage_region_code || "") === String(regionCode || "") && item.period === period
  );
  if (!row) {
    showToast("未找到对应的覆盖期次，请刷新后重试。", "error");
    return;
  }
  if (!coverageBackfillIsAvailable(row)) {
    showToast("该期没有可直接下载的官方源；请补充官网源或通过“新增档案”上传原件。", "warning");
    return;
  }
  const sources = coverageSourceCandidates(row);
  state.coverageBackfill = {
    open: true,
    row,
    sourceId: sources[0]?.source_id || "",
    submitting: false,
    error: "",
  };
  renderCoverageBackfillModal();
}

function selectCoverageBackfillSource(sourceId) {
  const row = state.coverageBackfill.row;
  if (!row || !coverageSourceCandidates(row).some((source) => source.source_id === sourceId)) return;
  state.coverageBackfill.sourceId = sourceId;
  state.coverageBackfill.error = "";
  renderCoverageBackfillModal();
}

function renderCoverageBackfillModal() {
  const modal = $("#coverageBackfillModal");
  if (!modal) return;
  const dialog = state.coverageBackfill;
  modal.hidden = !dialog.open;
  modal.setAttribute("aria-hidden", String(!dialog.open));
  if (!dialog.open || !dialog.row) {
    modal.innerHTML = "";
    return;
  }

  const row = dialog.row;
  const sources = coverageSourceCandidates(row);
  const selected = sources.find((source) => source.source_id === dialog.sourceId) || sources[0];
  if (selected && dialog.sourceId !== selected.source_id) dialog.sourceId = selected.source_id;
  const region = row.coverage_region_name || regionLabel(row.coverage_region_code) || row.coverage_region_code;
  const sourceOptions = sources
    .map((source) => {
      const selectedClass = source.source_id === dialog.sourceId ? " is-selected" : "";
      const isSelected = source.source_id === dialog.sourceId;
      const url = safeExternalUrl(coverageSourceUrl(source));
      return `
        <div class="coverage-source-choice${selectedClass}">
          <button
            type="button"
            class="coverage-source-select"
            data-action="select-coverage-backfill-source"
            data-source-id="${escapeHtml(source.source_id)}"
            aria-pressed="${isSelected}"
          >
            <span>
              <strong>${escapeHtml(source.name || "官方信息价源")}</strong>
              <small>${escapeHtml(url || coverageSourceUrl(source))}</small>
            </span>
            <span class="coverage-source-selected">${isSelected ? "已选" : "选择"}</span>
          </button>
          ${url ? `<a class="coverage-source-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer"><i data-lucide="external-link"></i><span>查看官网</span></a>` : ""}
        </div>
      `;
    })
    .join("");

  modal.innerHTML = `
    <section class="coverage-backfill-dialog" role="dialog" aria-modal="true" aria-labelledby="coverageBackfillTitle">
      <header class="manual-upload-header">
        <div>
          <p class="eyebrow">Official Source Download</p>
          <h2 id="coverageBackfillTitle">下载到档案列表</h2>
        </div>
        <button class="icon-button" type="button" title="关闭" data-action="close-coverage-backfill" ${dialog.submitting ? "disabled" : ""}>
          <i data-lucide="x"></i>
        </button>
      </header>
      <div class="coverage-backfill-body">
        <div class="coverage-backfill-target">
          <strong>${escapeHtml(region)}</strong>
          <span>${escapeHtml(row.period)} · ${escapeHtml(businessCoverageLabels[row.business_coverage_status] || row.business_coverage_status || "待回填")}</span>
        </div>
        <p class="coverage-backfill-note">指定一个已登记的官方源后，系统会自动创建下载任务、抓取原件、按 SHA-256 去重，并将新档案带回档案列表。</p>
        <div class="coverage-source-list" role="list" aria-label="可用官方源">
          ${sourceOptions || '<p class="coverage-backfill-empty">没有可下载的官方源。</p>'}
        </div>
        ${row.coverage_note ? `<p class="coverage-backfill-evidence">${escapeHtml(row.coverage_note)}</p>` : ""}
        <div class="form-error" aria-live="polite">${escapeHtml(dialog.error || "")}</div>
      </div>
      <footer class="manual-upload-actions">
        <button class="secondary-button" type="button" data-action="close-coverage-backfill" ${dialog.submitting ? "disabled" : ""}>取消</button>
        <button class="primary-button" type="button" data-action="submit-coverage-backfill" ${!selected || dialog.submitting ? "disabled" : ""}>
          <i data-lucide="${dialog.submitting ? "loader-circle" : "download-cloud"}"></i>
          <span>${dialog.submitting ? "下载入湖中…" : "下载到档案列表"}</span>
        </button>
      </footer>
    </section>
  `;
  refreshIcons();
}

function workerSummaryValue(summary, key) {
  return Number(summary?.[key] || 0);
}

async function submitCoverageBackfill() {
  const dialog = state.coverageBackfill;
  const row = dialog.row;
  if (!dialog.open || !row || dialog.submitting) return;
  const source = coverageSourceCandidates(row).find((item) => item.source_id === dialog.sourceId);
  if (!source) {
    dialog.error = "请选择一个可下载的官方源。";
    renderCoverageBackfillModal();
    return;
  }

  dialog.submitting = true;
  dialog.error = "";
  renderCoverageBackfillModal();
  try {
    const backfill = await postJson("/api/crawler/coverage-backfill", {
      region_code: row.coverage_region_code,
      start_period: row.period,
      end_period: row.period,
      source_id: source.source_id,
      dry_run: false,
    });
    const tasks = (backfill.tasks || []).filter((task) => task.source_id && task.batch_id);
    if (!tasks.length) {
      const reason = backfill.skipped?.[0]?.reason;
      dialog.submitting = false;
      dialog.error = reason === "pending_or_running_exists"
        ? "该期已有下载任务正在执行，请稍后刷新档案列表。"
        : "未创建下载任务；该期可能已覆盖或该官方源暂不可用。";
      renderCoverageBackfillModal();
      await loadCoverageMatrix();
      return;
    }

    const workers = [];
    for (const task of tasks) {
      workers.push(
        await postJson("/api/crawler/worker/run", {
          dry_run: false,
          source_id: task.source_id,
          batch_id: task.batch_id,
          limit: 1,
          trigger: "coverage_matrix_direct_download",
        })
      );
    }
    const summaries = workers.map((worker) => worker.summary || worker);
    const archiveCount = summaries.reduce((count, summary) => count + workerSummaryValue(summary, "archive_created_count"), 0);
    const duplicateCount = summaries.reduce((count, summary) => count + workerSummaryValue(summary, "duplicate_count"), 0);
    const failedCount = summaries.reduce((count, summary) => count + workerSummaryValue(summary, "failed_count"), 0);

    closeCoverageBackfillDialog();
    if (archiveCount > 0) {
      const [year, month] = String(row.period).split("-");
      state.viewMode = "archives";
      state.filters = {
        ...state.filters,
        region_code: row.coverage_region_code || "all",
        period_year: year || "all",
        period_month: month || "all",
      };
      renderDomain();
      await loadArchives();
      showToast(`已下载并入库 ${archiveCount} 份档案。`, "success");
    } else {
      await loadCoverageMatrix();
      const result = failedCount
        ? `下载完成，但有 ${failedCount} 个任务异常。`
        : duplicateCount
          ? "官网文件已在档案列表中，已按指纹跳过重复下载。"
          : "已检查官网，暂未发现该期可下载的新原件。";
      showToast(result, failedCount ? "error" : duplicateCount ? "warning" : "success");
    }
  } catch (error) {
    dialog.submitting = false;
    dialog.error = error.message || "官网下载失败，请稍后重试。";
    renderCoverageBackfillModal();
  }
}

async function loadStorageAudit() {
  if (state.domain === "quota") return; // quota 使用专属统计，不加载全域 NAS 原件条
  state.storageAudit = { ...state.storageAudit, loading: true, error: "" };
  renderStorageAudit();
  try {
    const response = await fetch(apiUrl("/api/storage-audit/file-assets", { limit: 5000, issue_limit: 3 }));
    if (!response.ok) throw new Error(await response.text());
    state.storageAudit = { loading: false, error: "", data: await response.json() };
  } catch (error) {
    state.storageAudit = { loading: false, error: error.message || "NAS 原件校验失败", data: null };
  }
  renderStorageAudit();
}

function scheduleInitialStorageAudit() {
  // 首屏先让档案列表返回；NAS 全量审计在稍后启动，避免与首次列表查询争用连接和带宽。
  window.setTimeout(() => {
    if (state.domain !== "quota") loadStorageAudit();
  }, 2000);
}

async function loadCurrentView() {
  if (state.domain === "quota") return; // quota 走 /api/data-lake/quota，禁止回退旧 /api/archives
  const sourceDimensions = loadCostInfoSourceDimensions();
  if (state.viewMode === "coverage") {
    const coverage = loadCoverageMatrix();
    await sourceDimensions;
    if (state.viewMode === "coverage") renderAll();
    return coverage;
  }
  const archives = loadArchives();
  await sourceDimensions;
  return archives;
}

async function loadArchiveDetail(id) {
  state.loading = true;
  state.error = "";
  renderApiState();
  try {
    const response = await fetch(`/api/archives/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error(await response.text());
    state.selectedArchive = await response.json();
    state.selectedFileId = defaultViewerFile(state.selectedArchive)?.file_id || null;
    state.viewerInfoOpen = false;
    openViewer();
  } catch (error) {
    state.error = error.message || "档案详情加载失败";
    renderApiState();
  } finally {
    state.loading = false;
    renderApiState();
  }
}

async function mirrorSelectedArchive() {
  if (!state.selectedArchive?.archive_id || state.mirrorExporting) return;
  state.mirrorExporting = true;
  renderViewer(state.selectedArchive);
  try {
    const response = await fetch(`/api/archives/${encodeURIComponent(state.selectedArchive.archive_id)}/mirror`, { method: "POST" });
    if (!response.ok) throw new Error(await responseErrorText(response));
    const detail = await requestJson(`/api/archives/${encodeURIComponent(state.selectedArchive.archive_id)}`);
    state.selectedArchive = detail;
    state.selectedFileId = selectedFile(detail)?.file_id || defaultViewerFile(detail)?.file_id || null;
    state.archives = state.archives.map((item) => (item.archive_id === detail.archive_id ? { ...item, ...detail } : item));
    showToast("已导出到 NAS 共享目录。", "success");
    renderViewer(state.selectedArchive);
    renderRows();
  } catch (error) {
    showToast(`导出失败：${error.message || "NAS 目录不可写或未配置"}`, "error");
    renderViewer(state.selectedArchive);
  } finally {
    state.mirrorExporting = false;
    renderViewer(state.selectedArchive);
  }
}

async function patchSelectedArchive(patch) {
  if (!state.selectedArchive) return;
  state.loading = true;
  renderApiState();
  const response = await fetch(`/api/archives/${encodeURIComponent(state.selectedArchive.archive_id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    state.error = await response.text();
    state.loading = false;
    renderApiState();
    return;
  }
  state.selectedArchive = await response.json();
  state.archives = state.archives.map((item) =>
    item.archive_id === state.selectedArchive.archive_id ? { ...item, ...state.selectedArchive } : item
  );
  state.loading = false;
  renderAll();
  renderViewer(state.selectedArchive);
}

const MANUAL_FILE_ROLES = [
  { value: "main_document", label: "原件" },
  { value: "cover", label: "封面" },
  { value: "attachment", label: "附件" },
];

function defaultFileRole(fileName) {
  const name = String(fileName || "").toLowerCase();
  return /\.(jpe?g|png)$/.test(name) && name.includes("封面") ? "cover" : "main_document";
}

function cascaderProvinceCode() {
  const explicit = String(state.manualUpload.regionProvince || "").trim();
  if (explicit) return explicit;
  const regionCode = String((state.manualUpload.values || {}).region_code || "");
  return regionCode.slice(0, 2);
}

function sourceForRegion(regionCode) {
  const sources = state.costInfoSources || [];
  return (
    sources.find((source) => {
      const rc = costInfoSourceRegionCode(source);
      return Boolean(rc) && (rc === regionCode || (rc.length > 6 && regionCode && rc.startsWith(regionCode.slice(0, 4))));
    }) || null
  );
}

function isRegionWithoutSource(regionCode) {
  const sources = state.costInfoSources;
  if (!Array.isArray(sources) || !sources.length) return false;
  return !sourceForRegion(regionCode);
}

function prefillPublisherForRegion(regionCode) {
  const source = sourceForRegion(regionCode);
  const publisher = source?.config?.stable?.publisher_name || source?.config?.stable?.publisher || "";
  const values = state.manualUpload.values || {};
  if (publisher && !values.publisher) {
    state.manualUpload.values = { ...values, publisher };
  }
}

function manualCityOptionsHtml(province, cityCode, search, sourcesLoaded) {
  const cityEntries = province
    ? [
        { value: `${province.value}0000`, label: `【${province.label}】全省（省级发布）`, provinceScope: true },
        ...province.cities.map((c) => ({ ...c, provinceScope: false })),
      ]
    : [];
  const hasSourceCodes = province ? costInfoSourceCityCodes(province) : new Set();
  const visibleCities = cityEntries.filter((c) => {
    if (!search) return true;
    return c.label.toLowerCase().includes(search) || c.value.includes(search);
  });
  if (!visibleCities.length) return `<option value="">无匹配城市</option>`;
  return visibleCities
    .map((c) => {
      const hasSource = c.provinceScope || !sourcesLoaded || hasSourceCodes.has(c.value);
      const marker = hasSource ? "" : "（将新建源）";
      return `<option value="${escapeHtml(c.value)}"${c.value === cityCode ? " selected" : ""}>${escapeHtml(c.label)}${marker}</option>`;
    })
    .join("");
}

function renderRegionCascader() {
  const container = $("#manualRegionCascader");
  if (!container) return;
  const values = state.manualUpload.values || {};
  const provinceCode = cascaderProvinceCode();
  const cityCode = String(values.region_code || "");
  const search = String(state.manualUpload.regionSearch || "").trim().toLowerCase();
  const sourcesLoaded = Array.isArray(state.costInfoSources) && state.costInfoSources.length > 0;
  const province = regionTree.find((item) => item.value === provinceCode);
  const provinceOptions = regionTree
    .map((p) => `<option value="${escapeHtml(p.value)}"${p.value === provinceCode ? " selected" : ""}>${escapeHtml(p.label)}</option>`)
    .join("");
  const codeHint = cityCode && isRegionWithoutSource(cityCode)
    ? `<small class="region-code-hint">无源（将新建）</small>`
    : "";
  container.innerHTML = `
    <select id="manualRegionProvince" data-region="province" aria-label="省"><option value="">省</option>${provinceOptions}</select>
    <select id="manualRegionCity" data-region="city" aria-label="市"><option value="">请选择市</option>${manualCityOptionsHtml(province, cityCode, search, sourcesLoaded)}</select>
    ${codeHint}
  `;
}

function refreshRegionCityOptions() {
  const citySel = $("#manualRegionCity");
  if (!citySel) return;
  const values = state.manualUpload.values || {};
  const provinceCode = cascaderProvinceCode();
  const cityCode = String(values.region_code || "");
  const search = String(state.manualUpload.regionSearch || "").trim().toLowerCase();
  const sourcesLoaded = Array.isArray(state.costInfoSources) && state.costInfoSources.length > 0;
  const province = regionTree.find((item) => item.value === provinceCode);
  citySel.innerHTML = `<option value="">请选择市</option>${manualCityOptionsHtml(province, cityCode, search, sourcesLoaded)}`;
}

function handleRegionCascaderChange(part, value) {
  if (part === "province") {
    state.manualUpload.regionProvince = String(value || "");
    state.manualUpload.values = { ...(state.manualUpload.values || {}), region_code: "" };
    state.manualUpload.regionSearch = "";
    renderManualUploadModal();
    return;
  }
  if (part === "city") {
    const code = String(value || "");
    state.manualUpload.values = { ...(state.manualUpload.values || {}), region_code: code };
    if (code.length >= 2) state.manualUpload.regionProvince = code.slice(0, 2);
    prefillPublisherForRegion(code);
    renderManualUploadModal();
  }
}

function renderManualFileList() {
  const list = $("#manualUploadFileList");
  if (!list) return;
  const entries = state.manualUpload.files || [];
  if (!entries.length) {
    list.innerHTML = "";
    return;
  }
  const roleOptions = (selected) =>
    MANUAL_FILE_ROLES.map((r) => `<option value="${r.value}"${r.value === selected ? " selected" : ""}>${r.label}</option>`).join("");
  list.innerHTML = entries
    .map((entry, idx) => `
      <div class="upload-file-row">
        <span class="upload-file-name" title="${escapeHtml(entry.file.name)}">
          ${escapeHtml(entry.file.name)}
          <small>${formatUploadFileSize(entry.file.size)}</small>
        </span>
        <select data-upload-role="${idx}" aria-label="文件角色">${roleOptions(entry.role)}</select>
        <button class="icon-button danger-action" type="button" title="移除" data-action="remove-upload-file" data-upload-idx="${idx}">
          <i data-lucide="x"></i>
        </button>
      </div>
    `)
    .join("");
}

function formatUploadFileSize(size) {
  if (!Number.isFinite(Number(size))) return "";
  const value = Number(size);
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

function initPeriodPicker() {
  const values = state.manualUpload.values || {};
  const period = values.period || "";
  const parts = period.match(/^(20\d{2})-(0[1-9]|1[0-2])$/);
  const picker = state.manualUpload.periodPicker;
  if (!picker) return;
  picker.open = false;
  picker.view = "month";
  if (parts) {
    picker.year = Number(parts[1]);
    picker.month = Number(parts[2]);
  } else {
    picker.year = new Date().getFullYear();
    picker.month = null;
  }
}

function renderPeriodPicker() {
  const container = $("#manualPeriodPicker");
  if (!container) return;
  const picker = state.manualUpload.periodPicker || {};
  const open = picker.open;
  const view = picker.view || "month";
  const year = picker.year || new Date().getFullYear();
  const month = picker.month || null;
  const displayText = month ? `${year}年${month}月` : "----年--月";
  const triggerHtml = `
    <div class="period-picker-trigger" data-action="period-picker-toggle">
      <span class="period-picker-display${month ? "" : " is-placeholder"}">${escapeHtml(displayText)}</span>
      <i data-lucide="calendar" class="period-picker-icon"></i>
    </div>`;
  const popupHtml = open ? (view === "month" ? renderPeriodMonthView(year, month) : renderPeriodYearView(year)) : "";
  container.innerHTML = triggerHtml + popupHtml;
  if (window.lucide) window.lucide.createIcons();
}

const PERIOD_MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"];

function renderPeriodMonthView(year, selectedMonth) {
  return `
    <div class="period-popup" data-period-popup="true">
      <div class="period-nav">
        <button class="period-nav-arrow" type="button" data-action="period-picker-prev-year">‹</button>
        <button class="period-nav-label" type="button" data-action="period-picker-goto-year">${escapeHtml(String(year))}</button>
        <button class="period-nav-arrow" type="button" data-action="period-picker-next-year">›</button>
      </div>
      <div class="period-grid period-month-grid">
        ${PERIOD_MONTHS.map((name, idx) => {
          const m = idx + 1;
          const sel = m === selectedMonth ? " is-selected" : "";
          return `<button class="period-cell${sel}" type="button" data-action="period-picker-select-month" data-period-month="${m}">${escapeHtml(name)}</button>`;
        }).join("")}
      </div>
      <div class="period-actions">
        <button class="secondary-button period-action-btn" type="button" data-action="period-picker-confirm">确定</button>
        <button class="secondary-button period-action-btn" type="button" data-action="period-picker-clear">清除</button>
      </div>
    </div>`;
}

function renderPeriodYearView(year) {
  const decadeStart = Math.floor(year / 10) * 10;
  const decadeEnd = decadeStart + 9;
  const decadeYears = [];
  for (let y = decadeStart; y <= decadeEnd; y++) decadeYears.push(y);
  return `
    <div class="period-popup" data-period-popup="true">
      <div class="period-nav">
        <button class="period-nav-arrow" type="button" data-action="period-picker-prev-decade">‹</button>
        <span class="period-nav-label is-static">${decadeStart} - ${decadeEnd}</span>
        <button class="period-nav-arrow" type="button" data-action="period-picker-next-decade">›</button>
      </div>
      <div class="period-grid period-year-grid">
        ${decadeYears.map((y) => {
          const sel = y === year ? " is-selected" : "";
          return `<button class="period-cell${sel}" type="button" data-action="period-picker-select-year" data-period-year="${y}">${y}</button>`;
        }).join("")}
      </div>
      <div class="period-actions">
        <button class="secondary-button period-action-btn" type="button" data-action="period-picker-confirm">确定</button>
        <button class="secondary-button period-action-btn" type="button" data-action="period-picker-clear">清除</button>
      </div>
    </div>`;
}

function renderManualUploadModal() {
  const modal = $("#manualUploadModal");
  if (!modal) return;
  modal.hidden = !state.manualUpload.open;
  modal.setAttribute("aria-hidden", String(!state.manualUpload.open));
  if (!state.manualUpload.open) return;

  const mode = state.manualUpload.mode || "create";
  const isEdit = mode === "edit";
  const isSupplement = mode === "supplement";
  const values = state.manualUpload.values || {};
  const archive = state.manualUpload.archive;

  const titles = { create: "新增档案", supplement: "补充文件", edit: "编辑元数据" };
  const eyebrows = { create: "Manual Upload", supplement: "Attach Files", edit: "Manual Metadata" };
  const notes = {
    create: "提交后将入湖并排队解析（状态：待解析）。原件优先解析，可同时上传封面/附件。",
    supplement: "文件作为补充挂入该档案，不改档案元数据；系统会根据文件 SHA-256 指纹自动检测重复，已在档的重复文件自动跳过，不会重复上传。",
    edit: "只修改地区/期次/发布主体/标题，不改文件；保存会重算 business_key 并过去重闸。",
  };
  const submitLabels = { create: "提交入湖", supplement: "提交补充", edit: "保存" };
  const submitIcons = { create: "upload-cloud", supplement: "paperclip", edit: "save" };

  $("#manualUploadEyebrow").textContent = eyebrows[mode];
  $("#manualUploadTitle").textContent = titles[mode];
  $("#manualUploadFileLabel").textContent = isSupplement ? "选择补充文件" : "选择文件";
  $("#manualUploadFileName").textContent = isEdit ? "编辑模式不改文件" : "可多选：PDF / Excel / zip / 图片";
  $("#manualUploadNote").textContent = notes[mode];
  $("#manualUploadFileSection").hidden = isEdit;

  const target = $("#manualUploadTarget");
  if (isSupplement && archive) {
    target.hidden = false;
    const region = regionLabel(archive.region_code) || archive.region_code || "";
    const period = metadata(archive, "period") || metadata(archive, "period_start") || metadata(archive, "period_raw") || "";
    const publisher = metadata(archive, "publisher") || metadata(archive, "producer") || "";
    target.innerHTML = `
      <div class="manual-upload-target-card">
        <strong>${escapeHtml(archive.title || "")}</strong>
        <small>${escapeHtml([region, period, publisher].filter(Boolean).join(" · "))}</small>
      </div>`;
  } else {
    target.hidden = true;
    target.innerHTML = "";
  }

  $("#manualUploadGrid").hidden = isSupplement;
  if (!isSupplement) {
    renderRegionCascader();
    initPeriodPicker();
    renderPeriodPicker();
    $("#manualPublisher").value = values.publisher || "";
    $("#manualTitle").value = values.title || "";
    $("#manualUploader").value = values.uploaded_by || "";
  }

  renderManualFileList();

  $("#manualUploadError").textContent = state.manualUpload.error || "";
  const submitting = state.manualUpload.submitting;
  $("#manualUploadSubmit").disabled = submitting;
  $("#manualUploadSubmit").innerHTML = submitting
    ? `<i data-lucide="loader-circle"></i><span>提交中</span>`
    : `<i data-lucide="${submitIcons[mode]}"></i><span>${submitLabels[mode]}</span>`;
  refreshIcons();
}

function showToast(message, kind = "success", duration = 5000) {
  state.toast = { message, kind, duration };
  renderToast();
}

function renderToast() {
  const toast = $("#manualUploadToast");
  if (!toast) return;
  toast.hidden = !state.toast.message;
  toast.className = `toast toast-${state.toast.kind || "success"}`;
  toast.textContent = state.toast.message || "";
  if (state.toast.message) {
    window.clearTimeout?.(state.toast.timer);
    const dur = state.toast.duration ?? 5000;
    if (dur > 0) {
      state.toast.timer = window.setTimeout?.(() => {
        state.toast = { message: "", kind: "success" };
        renderToast();
      }, dur);
    }
  }
}

function openManualUploadDialog() {
  state.manualUpload.open = true;
  state.manualUpload.mode = "create";
  state.manualUpload.archive = null;
  state.manualUpload.submitting = false;
  state.manualUpload.error = "";
  state.manualUpload.files = [];
  const createDefaults = manualUploadDefaults();
  state.manualUpload.regionSearch = "";
  state.manualUpload.regionProvince = String(createDefaults.region_code || "").slice(0, 2);
  state.manualUpload.values = createDefaults;
  const input = $("#manualUploadFile");
  if (input) input.value = "";
  renderManualUploadModal();
}

function openManualSupplementDialog(id) {
  const archive = archiveById(id);
  if (!archive) {
    showToast("未找到目标档案。", "error");
    return;
  }
  state.manualUpload.open = true;
  state.manualUpload.mode = "supplement";
  state.manualUpload.archive = archive;
  state.manualUpload.submitting = false;
  state.manualUpload.error = "";
  state.manualUpload.files = [];
  state.manualUpload.regionSearch = "";
  state.manualUpload.regionProvince = "";
  state.manualUpload.values = {};
  const input = $("#manualUploadFile");
  if (input) input.value = "";
  renderManualUploadModal();
}

function archiveById(id) {
  if (state.selectedArchive?.archive_id === id) return state.selectedArchive;
  return state.archives.find((item) => item.archive_id === id) || null;
}

async function openArchiveEditDialog(id) {
  let archive = archiveById(id);
  if (!archive) {
    archive = await requestJson(`/api/archives/${encodeURIComponent(id)}`);
  }
  if (archive.channel_type !== "manual_upload") {
    showToast("只允许编辑人工上传记录。", "error");
    return;
  }
  state.manualUpload.open = true;
  state.manualUpload.mode = "edit";
  state.manualUpload.archive = archive;
  state.manualUpload.submitting = false;
  state.manualUpload.error = "";
  state.manualUpload.files = [];
  const editDefaults = manualEditDefaults(archive);
  state.manualUpload.regionSearch = "";
  state.manualUpload.regionProvince = String(editDefaults.region_code || "").slice(0, 2);
  state.manualUpload.values = editDefaults;
  renderManualUploadModal();
}

function closeManualUploadDialog() {
  state.manualUpload.open = false;
  state.manualUpload.mode = "create";
  state.manualUpload.archive = null;
  state.manualUpload.submitting = false;
  state.manualUpload.error = "";
  state.manualUpload.files = [];
  state.manualUpload.regionSearch = "";
  state.manualUpload.regionProvince = "";
  state.manualUpload.values = {};
  state.manualUpload.periodPicker = { open: false, view: "month", year: null, month: null };
  renderManualUploadModal();
}

function manualUploadFormValues() {
  const values = state.manualUpload.values || {};
  const firstEntry = (state.manualUpload.files || [])[0];
  const fileName = firstEntry ? fileNameFromUpload(firstEntry.file) : values.file_name || "";
  const field = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : "";
  };
  return {
    region_code: values.region_code || "",
    period: values.period || "",
    price_source_type: "info_price",
    publisher: field("manualPublisher").trim(),
    tax_type: "",
    title: field("manualTitle").trim() || values.title || fileName || "",
    uploaded_by: field("manualUploader").trim(),
    file_name: fileName,
    file_ext: fileExtFromName(fileName),
  };
}

function updateManualUploadValue(event) {
  const target = event.target;
  if (!target) return;
  const regionPart = target.dataset ? target.dataset.region : null;
  if (regionPart) {
    handleRegionCascaderChange(regionPart, target.value);
    return;
  }
  if (target.id === "manualUploadFile") {
    handleManualUploadFileChange(event);
    return;
  }
  if (target.dataset && target.dataset.uploadRole !== undefined) {
    const idx = Number(target.dataset.uploadRole);
    const files = state.manualUpload.files || [];
    if (files[idx]) files[idx].role = target.value;
    return;
  }
  const fieldById = {
    manualPublisher: "publisher",
    manualTitle: "title",
    manualUploader: "uploaded_by",
  };
  const fieldKey = fieldById[target.id];
  if (!fieldKey) return;
  state.manualUpload.values = { ...(state.manualUpload.values || {}), [fieldKey]: target.value };
}

function addManualUploadFiles(fileList) {
  if (state.manualUpload.mode === "edit") return;
  const picked = Array.from(fileList || []);
  if (!picked.length) return;
  const files = Array.isArray(state.manualUpload.files) ? state.manualUpload.files.slice() : [];
  picked.forEach((file) => files.push({ file, role: defaultFileRole(file.name) }));
  state.manualUpload.files = files;
  renderManualUploadModal();
}

function handleManualUploadFileChange(event) {
  addManualUploadFiles(event.target.files);
  event.target.value = "";
}

async function handleManualUploadSubmit(event) {
  event?.preventDefault?.();
  if (state.manualUpload.submitting) return;
  const mode = state.manualUpload.mode || "create";
  const values = manualUploadFormValues();
  const entries = (state.manualUpload.files || []).map((entry) => ({ file: entry.file, role: entry.role }));
  state.manualUpload.values = values;
  state.manualUpload.submitting = true;
  state.manualUpload.error = "";
  renderManualUploadModal();
  try {
    if (mode === "edit") {
      await submitManualEdit(state.manualUpload.archive, values);
      showToast("元数据已更新。", "success");
    } else if (mode === "supplement") {
      if (!entries.length) throw new Error("请至少选择一个文件。");
      const result = await submitManualSupplement(state.manualUpload.archive, entries);
      const parts = [];
      if (result.attachedCount) parts.push(`挂入 ${result.attachedCount} 个`);
      if (result.skippedCount) {
        const names = result.skippedFiles.slice(0, 3).map((n) => `「${n}」`).join("、");
        const more = result.skippedFiles.length > 3 ? ` 等${result.skippedFiles.length}个` : "";
        parts.push(`跳过重复：${names}${more}`);
      }
      const noNew = result.attachedCount === 0;
      showToast(`补充完成：${parts.join("；")}。`, noNew ? "error" : result.skippedCount ? "warning" : "success");
    } else {
      if (!entries.length) throw new Error("请至少选择一个文件。");
      if (values.region_code && isRegionWithoutSource(values.region_code)) {
        const ok = window.confirm ? window.confirm("该地区暂无信息价源，提交后将自动新建源。是否继续？") : true;
        if (!ok) {
          state.manualUpload.submitting = false;
          renderManualUploadModal();
          return;
        }
      }
      const result = await submitManualUpload(entries, values);
      if (result.archive.attached_to_existing) {
        showToast("该期档案已存在，文件已作为补充挂入。", "success");
      } else {
        showToast(`新增档案成功，已入湖并排队解析。${result.extraCount ? `（另挂 ${result.extraCount} 个文件）` : ""}`, "success");
      }
    }
    state.manualUpload.open = false;
    state.manualUpload.mode = "create";
    state.manualUpload.archive = null;
    state.manualUpload.submitting = false;
    state.manualUpload.files = [];
    state.manualUpload.values = {};
    renderManualUploadModal();
    await loadCurrentView();
  } catch (error) {
    state.manualUpload.submitting = false;
    state.manualUpload.error = error.message || "提交失败";
    showToast(state.manualUpload.error, "error");
    renderManualUploadModal();
  }
}

async function handleDeleteArchive(id) {
  const archive = archiveById(id);
  if (!archive) {
    showToast("未找到要删除的档案。", "error");
    return;
  }
  if (archive.channel_type !== "manual_upload") {
    showToast("只允许删除人工上传记录。", "error");
    return;
  }
  const confirmed = window.confirm
    ? window.confirm(`确认删除“${archive.title}”？删除后可重新补录原件。`)
    : true;
  if (!confirmed) return;
  state.loading = true;
  renderApiState();
  try {
    await deleteManualArchive(archive);
    await loadCurrentView();
    showToast("人工上传记录已删除，可重新补录。", "success");
  } catch (error) {
    state.loading = false;
    state.error = error.message || "删除失败";
    showToast(`删除失败：${state.error}`, "error");
    renderApiState();
  }
}

async function handleWithdrawArchive(id) {
  const archive = archiveById(id);
  if (!archive) {
    showToast("未找到要撤下的档案。", "error");
    return;
  }
  const confirmed = window.confirm
    ? window.confirm(`确认删除“${archive.title}”？\n\n档案将从列表撤下，但不会删除 NAS 原件，可通过审计记录恢复。`)
    : true;
  if (!confirmed) return;
  state.loading = true;
  renderApiState();
  try {
    await withdrawArchive(archive);
    await loadCurrentView();
    showToast("档案已撤下，NAS 原件仍保留。", "success");
  } catch (error) {
    state.loading = false;
    state.error = error.message || "撤下失败";
    showToast(`删除失败：${state.error}`, "error");
    renderApiState();
  }
}

function renderApiState() {
  const apiState = $("#apiState");
  if (!apiState) return;
  const isHydrating = state.archiveHydrating && state.archiveTotalCount > state.archives.length;
  const progress = `${state.archives.length}/${state.archiveTotalCount}`;
  apiState.className = `api-state ${state.error || state.archiveHydrationError ? "error" : state.loading || isHydrating ? "loading" : "ok"}`;
  apiState.textContent = state.error
    ? "接口异常"
    : state.archiveHydrationError
      ? "补全失败"
      : state.loading
        ? "加载中"
        : isHydrating
          ? `档案补全 ${progress}`
          : "Archive API";
  apiState.title = state.error || state.archiveHydrationError || (isHydrating ? `已显示首批数据，正在后台补全 ${progress}` : "");
}

function latestCostInfoPeriodLabel(archives) {
  let latest = null;
  (archives || []).forEach((item) => {
    costInfoPeriodInfos(item, regionFilterForCostInfo()).forEach((info) => {
      const year = Number(info.year || 0);
      const sequence = info.kind === "monthly" ? Number(info.month || 0) : Number(info.issueNo || 0);
      const score = year * 1000 + Math.min(sequence, 999);
      if (!year || (latest && score <= latest.score)) return;
      latest = {
        score,
        label:
          info.kind === "monthly"
            ? `${info.year}年${Number(info.month)}月`
            : info.issueNo
              ? `${info.year}年第${info.issueNo}期`
              : `${info.year}年`,
      };
    });
  });
  return latest?.label || "暂无";
}

function costInfoOverviewModel() {
  const intro = {
    eyebrow: state.viewMode === "coverage" ? "Coverage Intelligence" : "Information Price Lake",
    title: state.viewMode === "coverage" ? "地区与期次覆盖一屏掌握" : "信息价原件与期次统一管理",
    description: "数据来自 NAS 上唯一的共享 PostgreSQL，原件集中留存在 NAS 数据湖。",
  };
  if (state.viewMode === "coverage") {
    const rows = filteredCoverageRows();
    const stats = coverageRegionStats(rows);
    const regions = new Set(rows.map((row) => row.coverage_region_code).filter(Boolean));
    const effectiveTotal = Math.max(0, stats.total - stats.future);
    return {
      ...intro,
      metrics: [
        { icon: "circle-check-big", label: "已覆盖", value: String(stats.covered), note: `共 ${effectiveTotal} 个有效单元` },
        { icon: "clock-3", label: "待核", value: String(stats.pending), note: "等待发布或回填" },
        { icon: "circle-alert", label: "缺失", value: String(stats.missing), note: "需要采集或补录" },
        { icon: "map-pinned", label: "覆盖地区", value: String(regions.size), note: "当前筛选范围" },
      ],
    };
  }

  const archives = state.archives || [];
  const regionCodes = new Set(
    archives
      .map((item) => item.region_code || metadata(item, "coverage_region_code"))
      .filter(Boolean)
  );
  const manualCount = archives.filter((item) => item.channel_type === "manual_upload").length;
  return {
    ...intro,
    metrics: [
      { icon: "archive", label: "档案总量", value: String(state.archiveTotalCount || archives.length), note: "份 Layer 0 原件" },
      { icon: "map-pinned", label: "覆盖地区", value: String(regionCodes.size), note: "按行政区划归集" },
      { icon: "calendar-days", label: "最新期次", value: latestCostInfoPeriodLabel(archives), note: "按档案期次识别" },
      { icon: "upload-cloud", label: "人工补录", value: String(manualCount), note: "爬虫受阻来源" },
    ],
  };
}

function renderCostInfoOverview() {
  const container = $("#costInfoOverview");
  if (!container) return;
  const visible = state.domain === "cost_info";
  container.hidden = !visible;
  if (!visible) return;
  const model = costInfoOverviewModel();
  container.innerHTML = `
    <div class="cost-info-overview-copy">
      <p>${escapeHtml(model.eyebrow)}</p>
      <strong>${escapeHtml(model.title)}</strong>
      <span>${escapeHtml(model.description)}</span>
    </div>
    <div class="cost-info-metrics">
      ${model.metrics
        .map(
          (metric) => `
            <article class="cost-info-metric">
              <div class="cost-info-metric-label"><i data-lucide="${escapeHtml(metric.icon)}"></i><span>${escapeHtml(metric.label)}</span></div>
              <strong>${escapeHtml(metric.value)}</strong>
              <small>${escapeHtml(metric.note)}</small>
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function renderDomain() {
  // quota (domain_type=quota) 由 quota-ui.js 独立接管；其他数据域零改动。
  const isQuota = state.domain === "quota";
  const legacyMain = document.querySelector("main.app-shell");
  const quotaShell = document.querySelector("#quotaShell");
  if (legacyMain) legacyMain.hidden = isQuota;
  if (quotaShell) quotaShell.hidden = !isQuota;
  $$(".rail-button[data-domain]").forEach((button) => button.classList.toggle("active", button.dataset.domain === state.domain));
  if (isQuota) {
    if (window.QuotaUI) window.QuotaUI.render();
    return;
  }
  if (state.domain !== "cost_info" && state.viewMode === "coverage") state.viewMode = "archives";
  const config = activeConfig();
  $("#pageTitle").textContent = state.viewMode === "coverage" ? "信息价覆盖矩阵" : config.title;
  $("#globalSearch").placeholder = config.searchPlaceholder;
  $("#domainPill").textContent = `domain_type: ${state.domain}`;
  $$(".rail-button[data-domain]").forEach((button) => button.classList.toggle("active", button.dataset.domain === state.domain));
  $$(".workspace-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.viewMode === state.viewMode);
    button.disabled = button.dataset.viewMode === "coverage" && state.domain !== "cost_info";
  });
  renderCostInfoOverview();
  renderFilters();
  renderHead();
  renderViewShell();
}

function activeFilters() {
  return state.viewMode === "coverage" ? coverageFilterConfig : activeConfig().filters;
}

function isFilterVisible(filter) {
  if (!filter.dependsOn?.length) return true;
  const filters = state.viewMode === "coverage" ? state.coverageFilters : state.filters;
  return filter.dependsOn.every((key) => filters[key] && filters[key] !== "all");
}

function isSingleValueFilterHidden(filter) {
  if (!filter.autoHideSingleValue) return false;
  const values = filterOptions(filter).filter((option) => option.value !== "all");
  return values.length <= 1;
}

function isNoValueFilterHidden(filter) {
  if (!filter.hideWhenNoOptions) return false;
  return !filterOptions(filter).some((option) => option.value !== "all");
}

function activeVisibleFilters() {
  const filters = state.viewMode === "coverage"
    ? activeFilters().filter((filter) => filter.key === "region_code" || filter.key === "period_year")
    : activeFilters();
  return filters
    .filter(isFilterVisible)
    .filter((filter) => !isNoValueFilterHidden(filter))
    .filter((filter) => !isSingleValueFilterHidden(filter));
}

function visibleFilterKeys() {
  return activeVisibleFilters()
    .filter((filter) => !filter.advanced || state.filterAdvancedOpen)
    .map((filter) => filter.key);
}

function renderFilters() {
  const filters = activeVisibleFilters();
  const coreFilters = filters.filter((filter) => !filter.advanced);
  const advancedFilters = filters.filter((filter) => filter.advanced);
  const advancedRows = state.filterAdvancedOpen ? advancedFilters.map(renderFilterRow).join("") : "";
  const toggleRow = advancedFilters.length ? renderAdvancedFilterToggle(advancedFilters.length) : "";
  $("#filterPanel").innerHTML = coreFilters.map(renderFilterRow).join("") + renderCostInfoMonthIssueHint() + advancedRows + toggleRow;
}

function renderAdvancedFilterToggle(count) {
  return `
    <div class="filter-row filter-row-toggle">
      <div class="filter-label">更多</div>
      <div class="filter-options">
        <button class="filter-chip filter-toggle ${state.filterAdvancedOpen ? "active" : ""}" type="button" data-action="toggle-advanced-filters" aria-expanded="${state.filterAdvancedOpen}">
          ${state.filterAdvancedOpen ? "收起筛选" : "更多筛选"}
          <span>${count}</span>
        </button>
      </div>
    </div>
  `;
}

function renderFilterRow(filter) {
  if (filter.kind === "region") return renderRegionFilter(filter);
  return `
    <div class="filter-row">
      <div class="filter-label">${escapeHtml(filter.label)}</div>
      <div class="filter-options">
        ${filterOptions(filter).map((option) => renderFilterChip(filter.key, option, filterValue(filter))).join("")}
      </div>
    </div>
  `;
}

function renderRegionFilter(filter) {
  const provinceValue = filterValue(filter);
  const provinceRow = `
    <div class="filter-row">
      <div class="filter-label">${escapeHtml(filter.label)}</div>
      <div class="filter-options">
        ${regionOptions().map((option) => renderFilterChip(filter.key, option, provinceValue)).join("")}
      </div>
    </div>
  `;
  const cityRow = `
    <div class="filter-row sub-filter">
      <div class="filter-label">地市</div>
      <div class="filter-options">
        ${cityOptions(provinceValue, filter).map((option) => renderFilterChip(cityFilterKey(filter), option, cityFilterValue(filter))).join("")}
      </div>
    </div>
  `;
  return provinceRow + cityRow;
}

function renderFilterChip(filterKey, option, activeValue) {
  return `
    <button class="filter-chip ${activeValue === option.value ? "active" : ""}" type="button" data-filter="${escapeHtml(filterKey)}" data-value="${escapeHtml(option.value)}">
      ${escapeHtml(option.label)}
    </button>
  `;
}

function renderHead() {
  $("#archiveHead").innerHTML = activeConfig().columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
}

function renderTotalBadge() {
  const el = $("#archiveTotalBadge");
  if (!el) return;
  if (state.viewMode === "coverage") { el.textContent = ""; return; }
  const total = state.archiveTotalCount || state.archives.length;
  el.textContent = `${total} ${activeConfig().resultUnit}`;
}

function renderViewShell() {
  $("#archiveTableCard").hidden = state.viewMode === "coverage";
  $("#coverageMatrixCard").hidden = state.viewMode !== "coverage";
}

function formatAuditPercent(value) {
  if (value === undefined || value === null || !Number.isFinite(Number(value))) return "-";
  return `${Math.round(Number(value) * 1000) / 10}%`;
}

function storageAuditStatusText(audit) {
  if (!audit) return "未校验";
  if (audit.health_status === "healthy") return "正常";
  if (audit.health_status === "empty") return "无对象";
  return "有缺口";
}

function renderStorageAuditBar() {
  const auditState = state.storageAudit || {};
  if (auditState.loading) {
    return `
      <div class="storage-audit-content storage-audit-loading">
        <span class="section-marker" aria-hidden="true"></span>
        <strong>NAS 原件</strong>
        <span>校验中</span>
      </div>
    `;
  }
  if (auditState.error) {
    return `
      <div class="storage-audit-content storage-audit-error">
        <span class="section-marker" aria-hidden="true"></span>
        <strong>NAS 原件</strong>
        <span>${escapeHtml(auditState.error)}</span>
      </div>
    `;
  }
  const audit = auditState.data;
  if (!audit) {
    return `
      <div class="storage-audit-content storage-audit-empty">
        <span class="section-marker" aria-hidden="true"></span>
        <strong>NAS 原件</strong>
        <span>未校验</span>
      </div>
    `;
  }
  const checked = Number(audit.checked_count || 0);
  const ok = Number(audit.ok_count || 0);
  const missing = Number(audit.missing_count || 0);
  const mismatch = Number(audit.size_mismatch_count || 0);
  const errors = Number(audit.error_count || 0);
  const orphanReferences = Number(audit.orphan_reference_count || 0);
  const firstIssue = (audit.issues || [])[0];
  const issueText = firstIssue
    ? `${firstIssue.region_code ? `${firstIssue.region_code} ` : ""}${firstIssue.file_name || firstIssue.object_key || firstIssue.file_id}`
    : "";
  return `
    <div class="storage-audit-content storage-audit-${escapeHtml(audit.health_status || "empty")}">
      <span class="section-marker" aria-hidden="true"></span>
      <strong>NAS 原件</strong>
      <span class="mono">${escapeHtml(ok)}/${escapeHtml(checked)}</span>
      <span>${escapeHtml(storageAuditStatusText(audit))}</span>
      <span>可用率 ${escapeHtml(formatAuditPercent(audit.availability_rate))}</span>
      <span>缺失 ${escapeHtml(missing)}</span>
      <span>大小异常 ${escapeHtml(mismatch)}</span>
      <span>孤儿引用 ${escapeHtml(orphanReferences)}</span>
      <span>错误 ${escapeHtml(errors)}</span>
      ${issueText ? `<span class="storage-audit-issue">${escapeHtml(issueText)}</span>` : ""}
    </div>
  `;
}

function renderStorageAudit() {
  const bar = $("#storageAuditBar");
  if (!bar) return;
  bar.innerHTML = renderStorageAuditBar();
}

function renderRows() {
  const rows = filteredArchives();
  const isHydrating = state.archiveHydrating && state.archiveTotalCount > state.archives.length;
  const hasActiveListFilter = Boolean(state.search.trim()) || state.status !== "all" || activeFilters().some((filter) => filterValue(filter) !== "all");
  const total = state.archiveTotalCount || state.archives.length;
  $("#resultCount").textContent = String(rows.length);
  $("#resultSummary").textContent = isHydrating
    ? `${activeConfig().resultUnit}（已加载 ${state.archives.length}/${state.archiveTotalCount}）`
    : hasActiveListFilter
      ? `${activeConfig().resultUnit}（当前筛选，共 ${total} 份）`
      : activeConfig().resultUnit;
  $("#emptyState").hidden = rows.length > 0 || state.loading;
  $("#emptyHint").textContent = state.error ? "接口暂不可用，请稍后刷新。" : (activeConfig().emptyHint || "调整筛选条件或等待采集落档。");
  $("#emptyUploadButton").hidden = Boolean(state.error) || state.domain !== "cost_info" || rows.length > 0 || state.loading || !selectedManualUploadRegionCode();
  $("#archiveRows").innerHTML = rows
    .map((item) => `<tr>${activeConfig().columns.map((column) => `<td>${renderCell(item, column)}</td>`).join("")}</tr>`)
    .join("");
  refreshIcons();
}

function filteredCoverageRows({ includeCity = true } = {}) {
  const query = state.search.trim().toLowerCase();
  const filters = state.coverageFilters;
  return state.coverageRows.filter((row) => {
    if (includeCity && filters.region_code_city !== "all") {
      const code = String(row.coverage_region_code || "");
      const cityPrefix = filters.region_code_city.slice(0, 4);
      if (code !== filters.region_code_city && !code.startsWith(cityPrefix)) return false;
    }
    if (filters.period_month !== "all") {
      const expectedPeriod = `${coverageEndYear()}-${filters.period_month}`;
      if (String(row.period || "") !== expectedPeriod) return false;
    } else {
      const parts = coveragePeriodParts(row.period);
      const range = coverageYearRange();
      if (parts && (parts.year < range.startYear || parts.year > range.endYear)) return false;
    }
    if (filters.business_coverage_status !== "all" && row.business_coverage_status !== filters.business_coverage_status) return false;
    if (filters.source_completeness_status !== "all" && row.source_completeness_status !== filters.source_completeness_status) return false;
    if (!query) return true;
    return [
      row.coverage_region_code,
      row.coverage_region_name,
      row.period,
      businessCoverageLabels[row.business_coverage_status] || row.business_coverage_status,
      sourceCompletenessLabels[row.source_completeness_status] || row.source_completeness_status,
      sourceAuditLabels[row.source_audit_status] || row.source_audit_status,
      row.coverage_note,
      row.source_audit_note,
    ]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

function coveragePeriods(rows) {
  return [...new Set(rows.map((row) => row.period).filter(Boolean))].sort();
}

function coverageRegions(rows) {
  const regions = new Map();
  rows.forEach((row) => {
    const code = row.coverage_region_code || "";
    if (!regions.has(code)) {
      regions.set(code, {
        code,
        name: row.coverage_region_name || regionLabel(code) || code,
        targetLevel: row.target_level,
        sourceAuditStatus: row.source_audit_status,
        sourceAuditNote: row.source_audit_note,
        citySourceUrl: row.source_visit_url || row.city_source_url,
        periods: new Map(),
      });
    }
    regions.get(code).periods.set(row.period, row);
  });
  return [...regions.values()].sort((a, b) => a.code.localeCompare(b.code, "zh-CN"));
}

function coveragePeriodParts(value) {
  const match = String(value || "").match(/^(20\d{2})-(0[1-9]|1[0-2])$/);
  if (!match) return null;
  return { year: Number(match[1]), month: Number(match[2]), monthText: match[2] };
}

function coverageMonthColumns(rows) {
  if (state.coverageFilters.period_month !== "all") {
    return [Number(state.coverageFilters.period_month)];
  }
  const months = [...new Set(rows.map((row) => coveragePeriodParts(row.period)?.month).filter(Boolean))].sort((a, b) => a - b);
  return months.length ? months : Array.from({ length: 12 }, (_, index) => index + 1);
}

function coverageYearRows(rows) {
  const years = [...new Set(rows.map((row) => coveragePeriodParts(row.period)?.year).filter(Boolean))].sort((a, b) => b - a);
  if (years.length) return years;
  const range = coverageYearRange();
  return Array.from({ length: COVERAGE_HISTORY_YEAR_COUNT }, (_, index) => range.endYear - index);
}

function coverageRegionMatchesCode(rowOrRegion, selectedCode) {
  const code = String(rowOrRegion?.coverage_region_code || rowOrRegion?.code || "");
  if (!selectedCode || selectedCode === "all") return true;
  if (code === selectedCode) return true;
  if (selectedCode.length >= 4 && code.startsWith(selectedCode.slice(0, 4))) return true;
  return false;
}

function selectedCoverageRegion(regions) {
  if (!regions.length) return null;
  const selected = state.coverageFilters.region_code_city;
  if (selected && selected !== "all") {
    return regions.find((region) => region.code === selected) || regions.find((region) => coverageRegionMatchesCode(region, selected)) || regions[0];
  }
  return regions[0];
}

function coverageRowsForRegion(rows, region) {
  if (!region) return [];
  return rows.filter((row) => String(row.coverage_region_code || "") === String(region.code || ""));
}

function coverageProvinceLabel(rows) {
  const selected = state.coverageFilters.region_code;
  if (selected && selected !== "all") return regionLabel(selected) || selected;
  const rowProvince = rows.find((row) => row.province_code)?.province_code;
  return regionLabel(rowProvince) || "全国";
}

function coverageMatrixTitle(rows) {
  const province = coverageProvinceLabel(rows);
  if (state.coverageFilters.target_level === "subregion") return `${province} · 区县信息价`;
  if (state.coverageFilters.target_level === "all") return `${province} · 信息价覆盖`;
  return `${province} · 各地市信息价`;
}

function coverageRegionStats(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.total += 1;
      if (isFutureCoveragePeriod(row.period) && row.business_coverage_status !== "covered") acc.future += 1;
      else if (row.business_coverage_status === "covered") acc.covered += 1;
      else if (row.business_coverage_status === "pending_verify") acc.pending += 1;
      else if (row.business_coverage_status === "missing") acc.missing += 1;
      return acc;
    },
    { total: 0, covered: 0, pending: 0, missing: 0, future: 0 }
  );
}

function issueLabelFromText(value) {
  const match = String(value || "").match(/第\s*([0-9０-９一二三四五六七八九十]+)\s*期/);
  return match ? `第${match[1]}期` : "";
}

function coverageIssueLabel(row) {
  const candidates = [
    row?.period_label,
    row?.issue_label,
    row?.period_issue_label,
    row?.archive_period_label,
    ...(Array.isArray(row?.evidence_titles) ? row.evidence_titles : []),
  ];
  for (const candidate of candidates) {
    const label = issueLabelFromText(candidate);
    if (label) return label;
  }
  return "";
}

function coverageMonthLabel(row, fallbackMonth) {
  const parts = coveragePeriodParts(row?.period);
  return `${parts?.month || fallbackMonth}月`;
}

function coveragePeriodCellLabel(row, month) {
  if (!row) return "-";
  if (isFutureCoveragePeriod(row.period) && row.business_coverage_status !== "covered") return "-";
  if (row.source_completeness_status === "source_blocked") return "受阻";
  if (row.business_coverage_status === "missing") return "-";
  if (row.business_coverage_status === "pending_verify") return "待核";
  const monthLabel = coverageMonthLabel(row, month);
  const issueLabel = coverageIssueLabel(row);
  return issueLabel ? `${monthLabel}/${issueLabel}` : monthLabel;
}

function coveragePeriodValue(year, month) {
  return `${year}-${String(month).padStart(2, "0")}`;
}

function isFutureCoveragePeriod(period) {
  const parts = coveragePeriodParts(period);
  if (!parts) return false;
  const today = new Date();
  const currentYear = today.getFullYear();
  const currentMonth = today.getMonth() + 1;
  return parts.year > currentYear || (parts.year === currentYear && parts.month > currentMonth);
}

function externalSourceUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value));
    if (!["http:", "https:"].includes(url.protocol)) return "";
    return url.toString();
  } catch (_) {
    return "";
  }
}

function renderCoverageAudit(region) {
  const audit = sourceAuditLabels[region.sourceAuditStatus] || region.sourceAuditStatus || "-";
  const className = `coverage-audit audit-${escapeHtml(region.sourceAuditStatus || "unknown")}`;
  const sourceUrl = externalSourceUrl(region.citySourceUrl);
  if (!sourceUrl) return `<span class="${className}">${escapeHtml(audit)}</span>`;
  return `
    <a class="${className}" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer" title="查看原站" aria-label="查看原站：${escapeHtml(region.name)}">
      <span>${escapeHtml(audit)}</span>
      <i data-lucide="external-link"></i>
    </a>
  `;
}

function renderCoverageCell(row) {
  if (!row) return `<span class="coverage-cell empty">-</span>`;
  const mode = coverageCellMode(row);
  const source = sourceCompletenessLabels[row.source_completeness_status] || row.source_completeness_status || "-";
  const title = [
    mode.title,
    row.coverage_note,
    row.source_audit_note,
    `省站源:${row.province_source_count || 0}`,
    `地市源:${row.city_source_count || 0}`,
  ]
    .filter(Boolean)
    .join("；");
  return `
    <span class="coverage-cell mode-${escapeHtml(mode.kind)} business-${escapeHtml(row.business_coverage_status)} source-${escapeHtml(row.source_completeness_status)}" title="${escapeHtml(title)}">
      <span>${escapeHtml(mode.label)}</span>
      <span>${escapeHtml(source)}</span>
    </span>
  `;
}

function coverageCellMode(row) {
  if (row.source_completeness_status === "source_blocked") {
    return { kind: "blocked", label: "源受阻", title: "官方源受阻，待人工补录" };
  }
  if (row.business_coverage_status === "pending_verify") {
    return { kind: "pending", label: "待核", title: "源存在但该期未发布或待回填" };
  }
  if (row.source_audit_status === "online_table_declaration") {
    return { kind: "declaration", label: "覆盖声明", title: "Layer0只做覆盖声明和原站跳转" };
  }
  if (row.source_audit_status === "spa_api_file_source" || row.source_audit_status === "auto_crawl_verified") {
    return { kind: "file", label: "文件源", title: "官方文件源已入湖" };
  }
  return {
    kind: row.business_coverage_status || "unknown",
    label: businessCoverageLabels[row.business_coverage_status] || row.business_coverage_status || "-",
    title: "",
  };
}

function renderCoverageMatrixTable(rows) {
  const regions = coverageRegions(rows);
  const activeRegion = selectedCoverageRegion(regions);
  const activeRows = coverageRowsForRegion(rows, activeRegion);
  const years = coverageYearRows(activeRows);
  const months = coverageMonthColumns(activeRows);
  if (!rows.length || !regions.length || !activeRegion || !years.length || !months.length) return "";
  const byPeriod = new Map(activeRows.map((row) => [row.period, row]));
  const stats = coverageRegionStats(activeRows);
  const activeRegionWithRows = { ...activeRegion, ...(activeRows[0] || {}) };
  return `
    <section class="coverage-workbench" aria-label="${escapeHtml(coverageMatrixTitle(rows))}">
      <header class="coverage-workbench-header">
        <div class="coverage-workbench-title">
          <span class="section-marker" aria-hidden="true"></span>
          <strong>${escapeHtml(coverageMatrixTitle(rows))}</strong>
          <button class="coverage-crawl-all-btn" type="button" data-action="crawl-scheduler-all" ${state.crawlBusy ? "disabled" : ""}>
            <i data-lucide="radar"></i>
            <span>${state.crawlBusy ? "采集中…" : "一键增量全网"}</span>
          </button>
        </div>
        <div class="coverage-workbench-summary">
          <span>覆盖 ${escapeHtml(stats.covered)}/${escapeHtml(stats.total)}</span>
          <span>待核 ${escapeHtml(stats.pending)}</span>
          <span>缺失 ${escapeHtml(stats.missing)}</span>
        </div>
      </header>
      <div class="coverage-region-meta">
        <strong>${escapeHtml(activeRegion.name)}</strong>
        <span>${escapeHtml(activeRegion.code)}</span>
        ${renderCoverageAudit(activeRegionWithRows)}
      </div>
      <table class="coverage-year-month-table">
        <thead>
          <tr>
            <th>年份</th>
            ${months.map((month) => `<th>${escapeHtml(month)}月</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${years
            .map(
              (year) => `
                <tr>
                  <th class="coverage-year-head">
                    <span>${escapeHtml(year)}年</span>
                  </th>
                  ${months.map((month) => renderCoveragePeriodCell(byPeriod.get(coveragePeriodValue(year, month)), month)).join("")}
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </section>
  `;
}

function renderCoveragePeriodCell(row, month) {
  const label = coveragePeriodCellLabel(row, month);
  if (!row) {
    return `<td><span class="coverage-period-cell cell-empty">-</span></td>`;
  }
  const mode = coverageCellMode(row);
  const issueLabel = coverageIssueLabel(row);
  const source = sourceCompletenessLabels[row.source_completeness_status] || row.source_completeness_status || "-";
  const isFuture = isFutureCoveragePeriod(row.period) && row.business_coverage_status !== "covered";
  const title = [
    row.period,
    issueLabel,
    ...(Array.isArray(row.evidence_titles) ? row.evidence_titles : []),
    mode.title,
    businessCoverageLabels[row.business_coverage_status] || row.business_coverage_status,
    source,
    row.coverage_note,
    row.source_audit_note,
    `省站源:${row.province_source_count || 0}`,
    `地市源:${row.city_source_count || 0}`,
  ]
    .filter(Boolean)
    .join("；");
  const className = [
    "coverage-period-cell",
    `cell-${isFuture ? "future" : mode.kind}`,
    `business-${row.business_coverage_status}`,
    `source-${row.source_completeness_status}`,
  ].join(" ");
  if (coverageBackfillIsAvailable(row)) {
    const actionLabel = row.business_coverage_status === "pending_verify" ? "待核·回填" : "回填";
    const busy = state.coverageBackfill.submitting ? "disabled" : "";
    return `
      <td>
        <button
          class="${escapeHtml(className)} coverage-backfill-button"
          type="button"
          data-action="open-coverage-backfill"
          data-region-code="${escapeHtml(row.coverage_region_code)}"
          data-period="${escapeHtml(row.period)}"
          title="${escapeHtml(`${title}；选择官网后直接下载并入库`)}"
          ${busy}
        >${escapeHtml(actionLabel)}</button>
      </td>
    `;
  }
  return `<td><span class="${escapeHtml(className)}" title="${escapeHtml(title)}">${escapeHtml(label)}</span></td>`;
}

function renderCoverageMatrix() {
  const rows = filteredCoverageRows({ includeCity: false });
  const countedRows = filteredCoverageRows();
  $("#resultCount").textContent = String(rows.length);
  $("#resultSummary").textContent = countedRows.length === rows.length ? "个覆盖单元" : `个覆盖单元，当前地市 ${countedRows.length}`;
  $("#coverageEmptyState").hidden = rows.length > 0 || state.loading;
  $("#coverageMatrixRows").innerHTML = state.loading
    ? `<div class="empty-state coverage-loading-state" role="status"><strong>正在加载覆盖矩阵</strong><span>正在汇总当前地区与期次的覆盖信息…</span></div>`
    : renderCoverageMatrixTable(rows);
  refreshIcons();
}

function renderCell(item, column) {
  if (column.type === "title") return renderTitleCell(item);
  if (column.type === "metadata") return escapeHtml(metadata(item, column.metadataKey) || "-");
  if (column.type === "region") {
    return escapeHtml(regionLabel(item.region_code) || metadata(item, "province_raw") || metadata(item, "city_raw") || item.region_code || "-");
  }
  if (column.type === "date") return escapeHtml(item.publish_date || metadata(item, "publish_date_raw") || "-");
  if (column.type === "policyEffectiveStatus") return escapeHtml(policyEffectiveStatus(item));
  if (column.type === "channel") return `<span class="channel-pill">${escapeHtml(channelLabels[item.channel_type] || item.channel_type)}</span>`;
  if (column.type === "attachments") return renderAttachmentCount(item);
  if (column.type === "status") return `<span class="status-badge status-${escapeHtml(item.status)}">${escapeHtml(statusLabels[item.status] || item.status)}</span>`;
  if (column.type === "actions") return renderActions(item);
  return escapeHtml(item[column.key] || "-");
}

function renderTitleCell(item) {
  const file = primaryFile(item);
  const type = fileType(file);
  const secondary = item.business_key || file?.file_name || item.source_item_key || "";
  const hoverTitle = [item.title, secondary].filter(Boolean).join(" · ");
  const issueTag = renderCostInfoIssueTag(item);
  return `
    <button class="file-cell" type="button" data-action="open-viewer" data-id="${escapeHtml(item.archive_id)}" title="${escapeHtml(hoverTitle)}">
      <span class="file-avatar ${type.className}"><i data-lucide="${type.icon}"></i></span>
      <span>
        <strong>${escapeHtml(item.title)}${issueTag}</strong>
        <small>${escapeHtml(secondary)}</small>
      </span>
    </button>
  `;
}

function renderCostInfoIssueTag(item) {
  if (state.domain !== "cost_info" || costInfoPeriodKind(item) !== "issue_based") return "";
  const periodInfo = costInfoPeriodInfos(item, regionFilterForCostInfo()).find((info) => info.kind === "issue_based" && info.issueNo);
  if (!periodInfo?.issueNo) return "";
  const issueEndNo = Number(metadata(item, "period_issue_end_no") || 0);
  const label =
    issueEndNo && issueEndNo !== periodInfo.issueNo
      ? `第${periodInfo.issueNo}—${issueEndNo}期`
      : `第${periodInfo.issueNo}期`;
  return `<span class="channel-pill issue-tag">${escapeHtml(label)}</span>`;
}

function renderAttachmentCount(item) {
  const count = Number(item.file_count || item.files?.length || 0);
  const priced = Number(item.priced_source_count || 0);
  return `
    <span>${count} 个</span>
    ${priced ? `<span class="priced-source-mini">${priced} 个计价源文件</span>` : ""}
  `;
}

function mirrorStatusLabel(status) {
  if (status === "mirrored") return "已镜像";
  if (status === "missing") return "未镜像";
  if (status === "stale") return "需更新";
  if (status === "unconfigured") return "未配置";
  if (status === "missing_asset") return "无原件";
  if (status === "error") return "异常";
  return "未校验";
}

function renderMirrorStatusBadge(file) {
  const status = file?.mirror_status || "unknown";
  return `<span class="mirror-badge mirror-${escapeHtml(status)}">${escapeHtml(mirrorStatusLabel(status))}</span>`;
}

function mirrorSummary(files) {
  const mirrored = files.filter((file) => file.mirror_status === "mirrored").length;
  const missing = files.filter((file) => file.mirror_status === "missing").length;
  const stale = files.filter((file) => file.mirror_status === "stale").length;
  const unconfigured = files.filter((file) => file.mirror_status === "unconfigured").length;
  if (unconfigured && unconfigured === files.length) return "NAS目录未配置";
  if (!files.length) return "";
  if (mirrored === files.length) return `NAS目录 ${mirrored}/${files.length}`;
  const issues = [
    missing ? `未镜像 ${missing}` : "",
    stale ? `需更新 ${stale}` : "",
  ].filter(Boolean);
  return `NAS目录 ${mirrored}/${files.length}${issues.length ? ` · ${issues.join(" · ")}` : ""}`;
}

function renderActions(item) {
  const file = primaryFile(item);
  const href = downloadUrl(file);
  const manualActions =
    item.channel_type === "manual_upload"
      ? `
        <button class="icon-button" type="button" title="编辑元数据" data-action="edit-archive" data-id="${escapeHtml(item.archive_id)}">
          <i data-lucide="pencil"></i>
        </button>
        <button class="icon-button danger-action" type="button" title="删除后重传" data-action="delete-archive" data-id="${escapeHtml(item.archive_id)}">
          <i data-lucide="trash-2"></i>
        </button>
      `
      : "";
  const withdrawAction =
    item.channel_type !== "manual_upload"
      ? `
        <button class="icon-button danger-action" type="button" title="删除（撤下档案，不删除 NAS 原件）" data-action="withdraw-archive" data-id="${escapeHtml(item.archive_id)}">
          <i data-lucide="trash-2"></i>
        </button>
      `
      : "";
  const download = href
    ? `
      <a class="icon-button" href="${escapeHtml(href)}" title="下载原件" download>
        <i data-lucide="download"></i>
      </a>
    `
    : `
      <button class="icon-button" type="button" title="暂无原件可下载" disabled>
        <i data-lucide="download"></i>
      </button>
    `;
  return `
    <div class="row-actions">
      <button class="icon-button" type="button" title="预览归整" data-action="open-viewer" data-id="${escapeHtml(item.archive_id)}">
        <i data-lucide="eye"></i>
      </button>
      ${download}
      <button class="icon-button" type="button" title="补充文件" data-action="supplement-archive" data-id="${escapeHtml(item.archive_id)}">
        <i data-lucide="paperclip"></i>
      </button>
      ${manualActions}
      ${withdrawAction}
    </div>
  `;
}

function renderAll() {
  if (state.domain === "quota") return; // quota 独立渲染，避免写入被隐藏的旧 DOM
  renderApiState();
  renderTotalBadge();
  renderCostInfoOverview();
  renderFilters();
  renderViewShell();
  renderStorageAudit();
  if (state.viewMode === "coverage") renderCoverageMatrix();
  else renderRows();
  renderCoverageBackfillModal();
}

function openViewer() {
  const viewer = $("#viewer");
  renderViewer(state.selectedArchive);
  viewer.classList.add("open");
  viewer.setAttribute("aria-hidden", "false");
}

function closeViewer() {
  const viewer = $("#viewer");
  destroyActivePdfViewer();
  viewer.classList.remove("open");
  viewer.setAttribute("aria-hidden", "true");
  state.viewerInfoOpen = false;
}

function viewerShowsAttachmentRail(item) {
  return archiveFiles(item).length > 1;
}

function viewerLayoutClass(item) {
  const attachmentClass = viewerShowsAttachmentRail(item) ? "has-attachments" : "single-file";
  const infoClass = state.viewerInfoOpen ? "info-open" : "info-closed";
  return `viewer-body ${attachmentClass} ${infoClass}`;
}

function renderViewerShell(item) {
  const files = archiveFiles(item);
  $("#viewerBody").className = viewerLayoutClass(item);
  $("#viewerSidebar").hidden = !viewerShowsAttachmentRail(item);
  const summary = mirrorSummary(files);
  const disabled = state.mirrorExporting ? "disabled" : "";
  $("#viewerTools").innerHTML = `
    ${files.length > 1 ? `<span>${files.length} 个附件</span>` : ""}
    ${summary ? `<span class="viewer-mirror-summary">${escapeHtml(summary)}</span>` : ""}
    <button class="tool-button" type="button" data-action="mirror-archive" ${disabled}>
      <i data-lucide="folder-sync"></i>
      <span>${state.mirrorExporting ? "导出中" : "导出NAS"}</span>
    </button>
  `;

  const toggle = $("#viewerInfoToggle");
  toggle.classList.toggle("active", state.viewerInfoOpen);
  toggle.setAttribute("aria-expanded", String(state.viewerInfoOpen));
  toggle.innerHTML = state.viewerInfoOpen
    ? `<i data-lucide="panel-right-close"></i><span>收起信息</span>`
    : `<i data-lucide="panel-right-open"></i><span>展开信息</span>`;
}

function renderViewer(item) {
  if (!item) return;
  const file = selectedFile(item);
  const type = fileType(file);
  $("#viewerIcon").className = `file-avatar ${type.className}`;
  $("#viewerIcon").innerHTML = `<i data-lucide="${type.icon}"></i>`;
  $("#viewerFileName").textContent = file?.file_name || item.title;
  $("#viewerMeta").textContent = `${type.label} · ${regionLabel(item.region_code) || item.region_code || "-"} · ${item.publish_date || "-"} · ${channelLabels[item.channel_type] || item.channel_type}`;
  $("#viewerDownload").href = downloadUrl(file) || "#";
  $("#formTitle").textContent = activeConfig().title;
  $("#formStatus").className = `status-badge status-${item.status}`;
  $("#formStatus").textContent = statusLabels[item.status] || item.status;
  $("#fieldTitle").value = item.title || "";
  $("#fieldRegionCode").value = item.region_code || "";
  $("#fieldPublishDate").value = item.publish_date || "";
  $("#fieldDomainType").value = item.domain_type || "";
  $("#fieldChannel").value = item.channel_type || "";
  $("#fieldBusinessKey").value = item.business_key || "";
  renderViewerShell(item);
  renderMetadataFields(item);
  renderAttachmentList(item);
  renderViewerCanvas(item, file);
  refreshIcons();
}

function selectedFile(item) {
  return archiveFiles(item).find((file) => file.file_id === state.selectedFileId) || defaultViewerFile(item);
}

function renderMetadataFields(item) {
  $("#metadataFields").innerHTML = activeConfig().metadataFields
    .map(
      (field) => `
        <label>
          ${escapeHtml(field.label)}
          <input data-metadata-field="${escapeHtml(field.key)}" type="text" value="${escapeHtml(metadata(item, field.key))}" />
        </label>
      `
    )
    .join("");
}

function renderAttachmentList(item) {
  const files = archiveFiles(item);
  $("#attachmentSummary").textContent = `${files.length} 个文件`;
  $("#attachmentList").innerHTML =
    files
      .map((file) => {
        const type = fileType(file);
        const active = file.file_id === state.selectedFileId ? "active" : "";
        const priced = file.file_role === "priced_source" ? `<span class="priced-source-badge">计价源文件</span>` : "";
        const label = file.display_name || file.file_name;
        const mirrorPath = file.mirror_relative_path
          ? `<small class="mirror-path" title="${escapeHtml(file.mirror_relative_path)}">${escapeHtml(file.mirror_relative_path)}</small>`
          : "";
        return `
          <button class="attachment-item ${active}" type="button" data-action="select-file" data-file-id="${escapeHtml(file.file_id)}" title="${escapeHtml(label)}">
            <span class="file-avatar ${type.className}"><i data-lucide="${type.icon}"></i></span>
            <span>
              <strong>${escapeHtml(label)}</strong>
              <span class="attachment-meta">
                <small>${escapeHtml(file.file_role || "attachment")} · ${escapeHtml(file.file_ext || "-")}</small>
                ${renderMirrorStatusBadge(file)}
              </span>
              ${mirrorPath}
              ${priced}
            </span>
          </button>
        `;
      })
      .join("") || `<div class="source-card"><strong>尚未挂载原件</strong><span>该 archive 暂无附件。</span></div>`;
}

function resetViewerScroll() {
  const canvas = $("#viewerCanvas");
  if (!canvas) return;
  canvas.scrollTop = 0;
  canvas.scrollLeft = 0;
  const excelWrap = canvas.querySelector?.(".excel-table-wrap");
  if (excelWrap) {
    excelWrap.scrollTop = 0;
    excelWrap.scrollLeft = 0;
  }
}

function renderViewerCanvas(item, file) {
  if (!file) {
    destroyActivePdfViewer();
    $("#viewerCanvas").innerHTML = `<div class="empty-state"><strong>无预览文件</strong><span>该档案尚未挂载原件。</span></div>`;
    resetViewerScroll();
    return;
  }
  if (file.file_role === "priced_source") {
    destroyActivePdfViewer();
    $("#viewerCanvas").innerHTML = `
      <article class="web-page source-only">
        <span class="web-origin">${escapeHtml(file.source_url || item.source_url || "")}</span>
        <h2>${escapeHtml(file.file_name)}</h2>
        <p>计价源文件已作为原件留存。该格式不提供在线解析预览，可下载原件查看。</p>
        <section><h3>文件身份</h3><p>${escapeHtml(file.file_ext || "-")} · ${escapeHtml(file.file_role)}</p></section>
      </article>
    `;
    resetViewerScroll();
    return;
  }
  const ext = file.file_ext || "pdf";
  if (ext === "pdf") {
    renderAdaptivePdfViewer(file);
    return;
  }
  destroyActivePdfViewer();
  if (["xls", "xlsx"].includes(ext)) {
    const selectedFileId = file.file_id;
    $("#viewerCanvas").innerHTML = `<div class="empty-state"><strong>正在加载预览</strong><span>${escapeHtml(file.file_name)}</span></div>`;
    resetViewerScroll();
    fetch(previewUrl(file))
      .then((response) => {
        if (!response.ok) throw new Error("PREVIEW_FAILED");
        return response.text();
      })
      .then((html) => {
        if (state.selectedFileId === selectedFileId) {
          $("#viewerCanvas").innerHTML = html;
          resetViewerScroll();
        }
      })
      .catch(() => {
        if (state.selectedFileId === selectedFileId) {
          $("#viewerCanvas").innerHTML = `
            <article class="web-page source-only">
              <h2>${escapeHtml(file.file_name)}</h2>
              <p>该 Excel 原件暂不能在线预览，可下载原件查看。</p>
            </article>
          `;
          resetViewerScroll();
        }
      });
    return;
  }
  if (canPreviewZipInline(item, file)) {
    renderZipPreview(item, file);
    return;
  }
  if (["zip", "rar", "7z", "cdz"].includes(ext) || file.file_role === "zip_package") {
    $("#viewerCanvas").innerHTML = `
      <article class="web-page source-only">
        <span class="web-origin">${escapeHtml(file.source_url || item.source_url || "")}</span>
        <h2>${escapeHtml(file.file_name)}</h2>
        <p>压缩包原件不在线展开预览，可下载原件查看。</p>
        <section><h3>文件身份</h3><p>${escapeHtml(file.file_ext || "-")} · ${escapeHtml(file.file_role || "attachment")}</p></section>
      </article>
    `;
    resetViewerScroll();
    return;
  }
  if (["html", "htm"].includes(ext)) {
    renderHtmlPreview(item, file);
    resetViewerScroll();
    return;
  }
  if (ext === "docx") {
    renderDocxPreview(item, file);
    resetViewerScroll();
    return;
  }
  $("#viewerCanvas").innerHTML = `
    <article class="pdf-page">
      <h2>${escapeHtml(item.title)}</h2>
      <p>文件：${escapeHtml(file.file_name)}</p>
      <p>来源：${escapeHtml(file.source_url || item.source_url || "-")}</p>
      <div class="fake-lines"><span></span><span></span><span></span><span></span><span></span></div>
    </article>
  `;
  resetViewerScroll();
}

function renderHtmlPreview(item, file) {
  const selectedFileId = file.file_id;
  $("#viewerCanvas").innerHTML = `<div class="empty-state"><strong>正在加载公告正文</strong><span>${escapeHtml(file.file_name)}</span></div>`;
  fetch(previewUrl(file))
    .then((response) => {
      if (!response.ok) throw new Error("HTML_PREVIEW_FAILED");
      return response.text();
    })
    .then((html) => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      $("#viewerCanvas").innerHTML = html;
      resetViewerScroll();
    })
    .catch(() => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      $("#viewerCanvas").innerHTML = `
        <article class="web-page source-only">
          <span class="web-origin">${escapeHtml(file.source_url || item.source_url || "")}</span>
          <h2>${escapeHtml(item.title)}</h2>
          <p>公告网页快照暂不能在线预览，可下载原件查看。</p>
        </article>
      `;
      resetViewerScroll();
    });
}

function renderDocxPreview(item, file) {
  const selectedFileId = file.file_id;
  $("#viewerCanvas").innerHTML = `<div class="empty-state"><strong>正在加载Word正文</strong><span>${escapeHtml(file.file_name)}</span></div>`;
  fetch(previewUrl(file))
    .then((response) => {
      if (!response.ok) throw new Error("DOCX_PREVIEW_FAILED");
      return response.text();
    })
    .then((html) => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      $("#viewerCanvas").innerHTML = html;
      resetViewerScroll();
    })
    .catch(() => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      $("#viewerCanvas").innerHTML = `
        <article class="web-page source-only">
          <span class="web-origin">${escapeHtml(file.source_url || item.source_url || "")}</span>
          <h2>${escapeHtml(file.file_name)}</h2>
          <p>Word原件暂不能在线预览，可下载原件查看。</p>
        </article>
      `;
      resetViewerScroll();
    });
}

function canPreviewZipInline(item, file) {
  const ext = String(file?.file_ext || "").replace(".", "").toLowerCase();
  if (ext !== "zip") return false;
  if (file?.file_role === "priced_source" || item?.domain_type === "trading") return false;
  const parsability = metadata(item || {}, "parsability");
  const attachmentMode = metadata(item || {}, "source_attachment_mode");
  return item?.domain_type === "cost_info" && (parsability === "image_based" || attachmentMode === "zip_package" || file?.file_role === "zip_package");
}

function renderZipPreview(item, file) {
  const selectedFileId = file.file_id;
  state.zipPreview = { fileId: selectedFileId, manifest: null, selectedIndex: null };
  $("#viewerCanvas").innerHTML = `<div class="empty-state"><strong>正在读取图片包</strong><span>${escapeHtml(file.file_name)}</span></div>`;
  fetch(zipPreviewUrl(file))
    .then((response) => {
      if (!response.ok) throw new Error("ZIP_PREVIEW_FAILED");
      return response.json();
    })
    .then((manifest) => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      const selectedIndex = manifest.entries?.[0]?.index ?? 0;
      state.zipPreview = { fileId: selectedFileId, manifest, selectedIndex };
      renderZipPreviewPanel(file, manifest, selectedIndex);
    })
    .catch(() => {
      if (selectedFile(state.selectedArchive)?.file_id !== selectedFileId) return;
      $("#viewerCanvas").innerHTML = `
        <article class="web-page source-only">
          <h2>${escapeHtml(file.file_name)}</h2>
          <p>图片包暂不能在线展开预览，可下载原件查看。</p>
        </article>
      `;
    });
}

function renderZipPreviewPanel(file, manifest, selectedIndex) {
  const entries = Array.isArray(manifest?.entries) ? manifest.entries : [];
  const selected = entries.find((entry) => String(entry.index) === String(selectedIndex)) || entries[0];
  if (!selected) {
    $("#viewerCanvas").innerHTML = `
      <article class="web-page source-only">
        <h2>${escapeHtml(file.file_name)}</h2>
        <p>该压缩包内没有可预览图片。</p>
      </article>
    `;
    return;
  }
  $("#viewerCanvas").innerHTML = `
    <article class="zip-preview" data-preview-mode="ephemeral" data-file-processing="0">
      <header class="zip-preview-header">
        <strong>${escapeHtml(file.file_name)}</strong>
        <span>${escapeHtml(String(manifest.previewable_count || entries.length))} 张可预览图片 · 解压预览不入库</span>
      </header>
      <div class="zip-preview-body">
        <nav class="zip-entry-list" aria-label="图片清单">
          ${entries
            .map(
              (entry) => `
                <button class="zip-entry ${String(entry.index) === String(selected.index) ? "active" : ""}" type="button" data-action="select-zip-entry" data-entry-index="${escapeHtml(entry.index)}">
                  <span>${escapeHtml(entry.name)}</span>
                  <small>${escapeHtml(entry.file_ext)} · ${escapeHtml(formatUploadFileSize(entry.file_size))}</small>
                </button>
              `
            )
            .join("")}
        </nav>
        <div class="zip-image-stage">
          <img class="zip-image-preview" alt="${escapeHtml(selected.name)}" src="${escapeHtml(zipEntryPreviewUrl(file, selected.index))}" />
        </div>
      </div>
    </article>
  `;
  refreshIcons();
}

function collectPatch(status) {
  const metadataPatch = {};
  $$("[data-metadata-field]").forEach((input) => {
    metadataPatch[input.dataset.metadataField] = metadataCellForPatch(input.dataset.metadataField, input.value);
  });
  const fieldSources = {};
  if ($("#fieldTitle").value) fieldSources.title = fieldSourceForPatch("title", $("#fieldTitle").value, state.selectedArchive?.title);
  if ($("#fieldRegionCode").value) {
    fieldSources.region_code = fieldSourceForPatch("region_code", $("#fieldRegionCode").value, state.selectedArchive?.region_code);
  }
  if ($("#fieldPublishDate").value) {
    fieldSources.publish_date = fieldSourceForPatch("publish_date", $("#fieldPublishDate").value, state.selectedArchive?.publish_date);
  }
  if (state.selectedArchive?.domain_type) fieldSources.domain_type = preservedFieldSource("domain_type");
  if (state.selectedArchive?.channel_type) fieldSources.channel_type = preservedFieldSource("channel_type");
  if (state.selectedArchive?.business_key) fieldSources.business_key = preservedFieldSource("business_key");
  return {
    title: $("#fieldTitle").value,
    region_code: $("#fieldRegionCode").value || null,
    publish_date: $("#fieldPublishDate").value || null,
    status,
    metadata: metadataPatch,
    field_sources: fieldSources,
  };
}

function setActiveFilter(button) {
  const filterKey = button.dataset.filter;
  const filters = activeFilters();
  const filter = filters.find((item) => item.key === filterKey);
  const target = state.viewMode === "coverage" ? state.coverageFilters : state.filters;
  const regionFilter = filters.find((item) => item.kind === "region" && item.key === filterKey);
  target[filterKey] = button.dataset.value;
  if (regionFilter) target[cityFilterKey(regionFilter)] = "all";
  normalizeCostInfoPublisherOrgFilter();
  if (filterKey === "publish_year" && target.publish_year === "all") {
    target.publish_month = "all";
    target.publish_day = "all";
  }
  if (filterKey === "publish_month" && target.publish_month === "all") target.publish_day = "all";
  if (state.viewMode === "archives" && filterKey !== "channel_type") {
    renderAll();
    return;
  }
  if (state.viewMode === "coverage" && filter?.key !== "period_year" && filter?.key !== "period_month" && filter?.key !== "target_level" && filter?.key !== "region_code") {
    renderAll();
    return;
  }
  loadCurrentView();
}


function setDomain(domain) {
  if (!domainConfigs[domain] || domain === state.domain) return;
  state.archiveLoadToken += 1;
  state.coverageLoadToken += 1;
  state.archiveHydrating = false;
  state.archiveHydrationError = "";
  state.domain = domain;
  if (domain !== "cost_info") state.viewMode = "archives";
  state.status = "all";
  state.filters = defaultFilters(domain);
  state.filterAdvancedOpen = false;
  state.search = "";
  state.selectedArchive = null;
  $("#globalSearch").value = "";
  renderDomain();
  if (domain === "quota") {
    if (window.QuotaUI) window.QuotaUI.activate();
  } else {
    if (window.QuotaUI) window.QuotaUI.deactivate();
    loadCurrentView();
  }
}

function setViewMode(mode) {
  if (!["archives", "coverage"].includes(mode)) return;
  if (mode === "coverage" && state.domain !== "cost_info") return;
  if (state.viewMode === mode) return;
  if (mode === "coverage") {
    state.archiveLoadToken += 1;
    state.archiveHydrating = false;
    state.archiveHydrationError = "";
  } else {
    state.coverageLoadToken += 1;
  }
  state.viewMode = mode;
  state.filterAdvancedOpen = false;
  renderDomain();
  loadCurrentView();
}

function handlePeriodPickerAction(action) {
  const cmd = String(action.dataset.action).replace("period-picker-", "");
  const picker = state.manualUpload.periodPicker;
  if (!picker) return;
  switch (cmd) {
    case "toggle":
      if (picker.open) { picker.open = false; renderPeriodPicker(); break; }
      initPeriodPicker();
      picker.open = true;
      renderPeriodPicker();
      break;
    case "prev-year":
      picker.year = (picker.year || new Date().getFullYear()) - 1;
      renderPeriodPicker();
      break;
    case "next-year":
      picker.year = (picker.year || new Date().getFullYear()) + 1;
      renderPeriodPicker();
      break;
    case "goto-year":
      picker.view = "year";
      renderPeriodPicker();
      break;
    case "prev-decade":
      picker.year = (picker.year || new Date().getFullYear()) - 10;
      renderPeriodPicker();
      break;
    case "next-decade":
      picker.year = (picker.year || new Date().getFullYear()) + 10;
      renderPeriodPicker();
      break;
    case "select-month":
      picker.month = Number(action.dataset.periodMonth) || null;
      renderPeriodPicker();
      break;
    case "select-year":
      picker.year = Number(action.dataset.periodYear) || new Date().getFullYear();
      picker.view = "month";
      renderPeriodPicker();
      break;
    case "confirm": {
      const y = picker.year;
      const m = picker.month;
      if (!y || !m) {
        if (picker.view === "year") { picker.view = "month"; renderPeriodPicker(); }
        break;
      }
      state.manualUpload.values = { ...(state.manualUpload.values || {}), period: `${y}-${String(m).padStart(2, "0")}` };
      picker.open = false;
      renderPeriodPicker();
      break;
    }
    case "clear":
      state.manualUpload.values = { ...(state.manualUpload.values || {}), period: "" };
      picker.open = false;
      picker.month = null;
      renderPeriodPicker();
      break;
  }
}

function handleClick(event) {
  const viewButton = event.target.closest("[data-view-mode]");
  if (viewButton) {
    setViewMode(viewButton.dataset.viewMode);
    return;
  }
  const domainButton = event.target.closest("[data-domain]");
  if (domainButton) {
    setDomain(domainButton.dataset.domain);
    return;
  }
  const filterButton = event.target.closest("[data-filter]");
  if (filterButton) {
    setActiveFilter(filterButton);
    return;
  }
  const action = event.target.closest("[data-action]");
  if (action && String(action.dataset.action).startsWith("period-picker-")) {
    handlePeriodPickerAction(action);
    return;
  }
  if (!action) {
    if (state.manualUpload.periodPicker?.open && !event.target.closest("#manualPeriodPicker")) {
      state.manualUpload.periodPicker.open = false;
      renderPeriodPicker();
    }
    return;
  }
  if (action.dataset.action === "open-viewer") loadArchiveDetail(action.dataset.id);
  if (action.dataset.action === "close-viewer") closeViewer();
  if (action.dataset.action === "toggle-viewer-info") {
    state.viewerInfoOpen = !state.viewerInfoOpen;
    renderViewer(state.selectedArchive);
  }
  if (action.dataset.action === "select-file") {
    state.selectedFileId = action.dataset.fileId;
    state.zipPreview = { fileId: null, manifest: null, selectedIndex: null };
    renderViewer(state.selectedArchive);
  }
  if (action.dataset.action === "mirror-archive") mirrorSelectedArchive();
  if (action.dataset.action === "select-zip-entry") {
    const file = selectedFile(state.selectedArchive);
    const manifest = state.zipPreview.manifest;
    state.zipPreview.selectedIndex = action.dataset.entryIndex;
    if (file && manifest) renderZipPreviewPanel(file, manifest, state.zipPreview.selectedIndex);
  }
  if (action.dataset.action === "archive-current") patchSelectedArchive(collectPatch("archived"));
  if (action.dataset.action === "mark-failed") patchSelectedArchive(collectPatch("collect_failed"));
  if (action.dataset.action === "mark-quarantine") patchSelectedArchive(collectPatch("quarantined"));
  if (action.dataset.action === "mark-ready") patchSelectedArchive(collectPatch("ready_for_governance"));
  if (action.dataset.action === "refresh") {
    loadCurrentView();
    loadStorageAudit();
  }
  if (action.dataset.action === "toggle-advanced-filters") {
    state.filterAdvancedOpen = !state.filterAdvancedOpen;
    renderFilters();
  }
  if (action.dataset.action === "crawl-scheduler-all") runIncrementalCrawlAll();
  if (action.dataset.action === "open-coverage-backfill") {
    openCoverageBackfillDialog(action.dataset.regionCode, action.dataset.period);
  }
  if (action.dataset.action === "close-coverage-backfill") closeCoverageBackfillDialog();
  if (action.dataset.action === "select-coverage-backfill-source") selectCoverageBackfillSource(action.dataset.sourceId);
  if (action.dataset.action === "submit-coverage-backfill") submitCoverageBackfill();
  if (action.dataset.action === "open-upload") openManualUploadDialog();
  if (action.dataset.action === "edit-archive") openArchiveEditDialog(action.dataset.id);
  if (action.dataset.action === "supplement-archive") openManualSupplementDialog(action.dataset.id);
  if (action.dataset.action === "delete-archive") handleDeleteArchive(action.dataset.id);
  if (action.dataset.action === "withdraw-archive") handleWithdrawArchive(action.dataset.id);
  if (action.dataset.action === "remove-upload-file") {
    const idx = Number(action.dataset.uploadIdx);
    if (Array.isArray(state.manualUpload.files) && idx >= 0 && idx < state.manualUpload.files.length) {
      state.manualUpload.files.splice(idx, 1);
      renderManualUploadModal();
    }
  }
  if (action.dataset.action === "close-upload") closeManualUploadDialog();
  if (action.dataset.action === "submit-upload") handleManualUploadSubmit(event);
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

document.addEventListener("click", handleClick);

// HOTFIX-QA-UPLOAD-001 · quota 域预览事件桥
// quota-ui.js 行点击时 dispatch quota:preview-archive → 复用既有 loadArchiveDetail + #viewer
document.addEventListener("quota:preview-archive", async (event) => {
  const archiveId = event.detail && event.detail.archiveId;
  if (!archiveId) {
    showToast("预览失败：档案 ID 为空", "error");
    return;
  }
  await loadArchiveDetail(archiveId);
  // 无文件档案：明确提示，不静默无响应
  const detail = state.selectedArchive;
  if (detail && (!detail.files || detail.files.length === 0)) {
    showToast("该档案未关联原件（无 main_document），无法预览", "error");
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.manualUpload.periodPicker?.open) {
    state.manualUpload.periodPicker.open = false;
    renderPeriodPicker();
    return;
  }
  if (state.manualUpload.open) closeManualUploadDialog();
  else if (state.coverageBackfill.open) closeCoverageBackfillDialog();
  else closeViewer();
});
window.addEventListener("load", () => {
  renderDomain();
  refreshIcons();
  loadCurrentView();
  scheduleInitialStorageAudit();
});

$("#globalSearch").addEventListener("input", (event) => {
  state.search = event.target.value;
  if (state.viewMode === "coverage") renderAll();
  else loadArchives();
});

$("#manualUploadForm").addEventListener("submit", handleManualUploadSubmit);
$("#manualUploadForm").addEventListener("input", updateManualUploadValue);
$("#manualUploadForm").addEventListener("change", updateManualUploadValue);

// ── drag-and-drop upload ──
const dropzone = $("#manualUploadDropzone");
if (dropzone) {
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    dropzone.classList.add("is-dragover");
  });
  dropzone.addEventListener("dragleave", (e) => {
    if (e.relatedTarget && dropzone.contains(e.relatedTarget)) return;
    dropzone.classList.remove("is-dragover");
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    addManualUploadFiles(e.dataTransfer.files);
  });
}

// Prevent browser from opening dropped files when modal is open
const uploadModal = $("#manualUploadModal");
if (uploadModal) {
  uploadModal.addEventListener("dragover", (e) => {
    if (state.manualUpload.open) e.preventDefault();
  });
  uploadModal.addEventListener("drop", (e) => {
    if (state.manualUpload.open) e.preventDefault();
  });
}
