"use strict";
var test = require("node:test");
var assert = require("node:assert/strict");

var Compose = require("../../app/ui/quota-compose.js");

function fakeStorage() {
  var map = new Map();
  return {
    map: map,
    getItem: function (k) { return map.has(k) ? map.get(k) : null; },
    setItem: function (k, v) { map.set(k, String(v)); },
    removeItem: function (k) { map.delete(k); },
  };
}

function fakeFile(name, size, type) {
  return { name: name, size: size, type: type || "application/pdf" };
}

// ── 状态模型 ───────────────────────────────────────────────────────────
test("createComposeState new_set 默认值", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, { user: "u1", tenant: "t1" });
  assert.equal(s.action, "new_set");
  assert.equal(s.material_type, "quota_base");
  assert.equal(s.set.material_type, "quota_base");
  assert.equal(s.volumes.length, 1);
  assert.equal(s.volumes[0].files.length, 0); // 不再预填充空文件
  assert.equal(s.unassignedFiles.length, 0);
  assert.equal(s.set.titleUserEdited, false);
  assert.equal(s.user, "u1");
  assert.equal(s.tenant, "t1");
});

// ── 新建定额体系校验：仅 4 项阻断 ───────────────────────────────────────
test("validateNewSet: 空状态 3 项阻断（体系/年份/正文）", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.volumes = []; // 移除默认空分册
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, false);
  // systemType 为空时不要求地区/行业，所以是 3 项（体系/年份/正文），选了体系后才变 4 项
  assert.equal(v.blockingCount, 3, "空状态应有 3 项阻断（体系/年份/正文）: " + JSON.stringify(v.blocking));
  assert.ok(v.blocking.some(function (m) { return m.indexOf("定额体系") !== -1; }), "阻断应有「定额体系」");
  assert.ok(v.blocking.some(function (m) { return m.indexOf("正文文件") !== -1; }), "阻断应有「正文文件」");
  assert.ok(v.blocking.some(function (m) { return m.indexOf("年份") !== -1; }), "阻断应有「年份」");
  assert.ok(v.fields.systemType);
  assert.ok(v.fields.edition_year);
  assert.ok(v.fields.files);
});

test("validateNewSet: 建筑工程定额未选地区阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "";
  s.set.edition_year = "2025";
  s.volumes = [];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, false);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("地区") !== -1; }));
  assert.ok(v.fields.jurisdiction);
});

test("validateNewSet: 专业工程定额未选行业阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "industry_specialty";
  s.path.industry_sector_code = "";
  s.set.edition_year = "2025";
  s.volumes = [];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, false);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("行业") !== -1; }));
  assert.ok(v.fields.industry);
});

test("validateNewSet: 四项全填通过", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  s.set.title = "四川省2025建设工程计价定额";
  var vol = s.volumes[0];
  vol.volume_title = "房屋建筑工程";
  vol.files = [Compose.newFileEntry({ file: fakeFile("房屋建筑工程.pdf", 100), role: "main_document" })];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, true, JSON.stringify(v.blocking));
  assert.equal(v.blockingCount, 0);
});

// ── 体系名称不阻断提交 ──────────────────────────────────────────────────
test("validateNewSet: 体系名称为空不阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  s.set.title = ""; // 空名称
  var vol = s.volumes[0];
  vol.files = [Compose.newFileEntry({ file: fakeFile("test.pdf", 100), role: "main_document" })];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, true, "体系名称为空不应阻断: " + JSON.stringify(v.blocking));
});

// ── 分册级别校验降级为警告（非阻断）──────────────────────────────────────
test("validateNewSet: 分册缺正文仅警告", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "national";
  s.set.edition_year = "2025";
  // 不添加任何文件 → total main count = 0 → 阻断
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, false, "总量无正文应阻断");
  assert.ok(v.blocking.some(function (m) { return m.indexOf("正文文件") !== -1; }));
});

test("validateNewSet: 多正文仅警告不阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  var vol = s.volumes[0];
  vol.volume_title = "测试分册";
  vol.files = [
    Compose.newFileEntry({ file: fakeFile("a.pdf", 100), role: "main_document" }),
    Compose.newFileEntry({ file: fakeFile("b.pdf", 200), role: "main_document" }),
  ];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, true, "多正文应仅为警告不阻断: " + JSON.stringify(v.blocking));
  assert.ok(v.warnings.some(function (m) { return m.indexOf("多个正文") !== -1; }), "应有多个正文警告");
});

test("validateNewSet: 分册专业缺失不阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  var vol = s.volumes[0];
  vol.volume_title = "测试分册";
  vol.discipline_code = "";
  vol.discipline_label = "";
  vol.files = [Compose.newFileEntry({ file: fakeFile("test.pdf", 100), role: "main_document" })];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, true, "分册专业缺失不应阻断");
});

// ── 关联体系字段降级为警告 ──────────────────────────────────────────────
test("validateNewSet: 关联体系字段缺失仅警告", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.set.material_type = "quota_supplement";
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  s.relation.related_publication_set_id = "";
  s.relation.relation_type = "";
  var vol = s.volumes[0];
  vol.files = [Compose.newFileEntry({ file: fakeFile("m.pdf", 10), role: "main_document" })];
  var v = Compose.validateNewSet(s);
  assert.equal(v.ok, true, "关联体系缺失不应阻断");
  assert.ok(v.warnings.some(function (m) { return m.indexOf("关联主体系") !== -1; }));
  assert.ok(v.warnings.some(function (m) { return m.indexOf("关联关系") !== -1; }));
});

// ── 其他动作校验 ──────────────────────────────────────────────────────
test("validateAddVolume: 目标/名称/正文 三项阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.ADD_VOLUME, {});
  s.volumes = [];
  var v = Compose.validateAddVolume(s);
  assert.equal(v.ok, false);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("目标资料体系") !== -1; }));
  assert.ok(v.blocking.some(function (m) { return m.indexOf("分册名称") !== -1; }));
  assert.ok(v.blocking.some(function (m) { return m.indexOf("正文文件") !== -1; }));
});

test("validateAddVolume: 三项全填通过", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.ADD_VOLUME, {});
  s.targetKind = "publicationSets";
  s.targetId = "ps_001";
  s.volumes = [Compose.newVolume({ volume_title: "市政工程", files: [] })];
  s.volumes[0].files = [Compose.newFileEntry({ file: fakeFile("市政.pdf", 100), role: "main_document" })];
  var v = Compose.validateAddVolume(s);
  assert.equal(v.ok, true, JSON.stringify(v.blocking));
});

test("validateSupplement: 目标/文件 两项阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.SUPPLEMENT, {});
  var v = Compose.validateSupplement(s);
  assert.equal(v.ok, false);
  assert.equal(v.blockingCount, 2);
});

test("validateSupplement: 通过", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.SUPPLEMENT, {});
  s.targetKind = "archives";
  s.targetId = "a_001";
  s.supplementFiles = [Compose.newFileEntry({ file: fakeFile("a.pdf", 100) })];
  var v = Compose.validateSupplement(s);
  assert.equal(v.ok, true, JSON.stringify(v.blocking));
});

test("validateNewBoq: 范围/年份版本/正文 三项阻断", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_BOQ, {});
  s.volumes = [];
  var v = Compose.validateNewBoq(s);
  assert.equal(v.ok, false);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("适用范围") !== -1; }));
  assert.ok(v.blocking.some(function (m) { return m.indexOf("年份") !== -1 || m.indexOf("版本") !== -1; }));
  assert.ok(v.blocking.some(function (m) { return m.indexOf("正文文件") !== -1; }));
});

test("validateCompose 路由到正确的校验函数", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.volumes = [];
  var v = Compose.validateCompose(s);
  assert.ok(v.fields && v.fields.systemType, "new_set 应有 fields.systemType");

  var s2 = Compose.createComposeState(Compose.ACTIONS.ADD_VOLUME, {});
  s2.volumes = [];
  var v2 = Compose.validateCompose(s2);
  assert.ok(v2.blocking.some(function (m) { return m.indexOf("分册名称") !== -1; }));
});

// ── 角色自动检测 ──────────────────────────────────────────────────────
test("detectFileRole: 附录/封面/目录/公告 自动识别", function () {
  assert.equal(Compose.detectFileRole("附录.pdf"), "appendix");
  assert.equal(Compose.detectFileRole("08-附录A.pdf"), "appendix");
  assert.equal(Compose.detectFileRole("附件一.pdf"), "appendix");
  assert.equal(Compose.detectFileRole("封面.pdf"), "cover");
  assert.equal(Compose.detectFileRole("目录.pdf"), "table_of_contents");
  assert.equal(Compose.detectFileRole("发布公告.pdf"), "release_announcement");
});

test("detectFileRole: 未命中默认为正文", function () {
  assert.equal(Compose.detectFileRole("房屋建筑工程.pdf"), "main_document");
  assert.equal(Compose.detectFileRole("市政工程.pdf"), "main_document");
  assert.equal(Compose.detectFileRole("test.pdf"), "main_document");
});

test("detectFileRole: 空文件名", function () {
  assert.equal(Compose.detectFileRole(""), "main_document");
  assert.equal(Compose.detectFileRole(null), "main_document");
});

test("isMainRole", function () {
  assert.equal(Compose.isMainRole("main_document"), true);
  assert.equal(Compose.isMainRole("appendix"), false);
});

// ── 文件名提取分册名 ──────────────────────────────────────────────────
test("extractVolumeName: 去除 .pdf 和前导序号", function () {
  assert.equal(Compose.extractVolumeName("01-房屋建筑工程.pdf"), "房屋建筑工程");
  assert.equal(Compose.extractVolumeName("1.园林绿化工程.pdf"), "园林绿化工程");
  assert.equal(Compose.extractVolumeName("02_市政工程.pdf"), "市政工程");
  assert.equal(Compose.extractVolumeName("房屋建筑工程.pdf"), "房屋建筑工程");
});

test("extractVolumeName: 空值", function () {
  assert.equal(Compose.extractVolumeName(""), "");
  assert.equal(Compose.extractVolumeName(null), "");
});

// ── 体系名称自动生成 ─────────────────────────────────────────────────
test("autoGenerateTitle: 建筑工程定额", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  assert.equal(Compose.autoGenerateTitle(s), "四川省2025建设工程计价定额");
});

test("autoGenerateTitle: 专业工程定额", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "industry_specialty";
  s.path.industry_sector_label = "电网工程";
  s.set.edition_year = "2025";
  assert.equal(Compose.autoGenerateTitle(s), "电网工程2025工程定额");
});

test("autoGenerateTitle: 缺少信息返回空", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  assert.equal(Compose.autoGenerateTitle(s), "");
});

// ── 法律状态自动计算 ─────────────────────────────────────────────────
test("computeLegalStatus: 无实施日期返回 unknown", function () {
  assert.equal(Compose.computeLegalStatus({ effective_date: "" }), "unknown");
});

test("computeLegalStatus: 过去日期返回 effective", function () {
  assert.equal(Compose.computeLegalStatus({ effective_date: "2020-01-01" }), "effective");
});

test("computeLegalStatus: 未来日期返回 pending", function () {
  // 使用远未来日期
  assert.equal(Compose.computeLegalStatus({ effective_date: "2099-12-31" }), "pending");
});

// ── 批量文件处理 ──────────────────────────────────────────────────────
test("processDroppedFiles: 正文创建分册，附录入待归属", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  var files = [
    fakeFile("房屋建筑工程.pdf", 100),
    fakeFile("市政工程.pdf", 200),
    fakeFile("附录.pdf", 50),
    fakeFile("封面.pdf", 30),
  ];
  var result = Compose.processDroppedFiles(s, files);
  assert.equal(result.volumes.length, 2, "两个正文文件应创建 2 个分册");
  assert.equal(result.unassigned.length, 2, "附录和封面应入待归属");
  assert.equal(result.volumes[0].volume_title, "房屋建筑工程");
  assert.equal(result.volumes[1].volume_title, "市政工程");
  assert.equal(result.unassigned[0].role, "appendix");
  assert.equal(result.unassigned[1].role, "cover");
});

test("processDroppedFiles: 全部正文", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  var files = [
    fakeFile("园林绿化工程.pdf", 100),
    fakeFile("智能建造与建筑工业化.pdf", 200),
  ];
  var result = Compose.processDroppedFiles(s, files);
  assert.equal(result.volumes.length, 2);
  assert.equal(result.unassigned.length, 0);
});

// ── 动作判定 ─────────────────────────────────────────────────────────
test("resolveComposeActions: apiReady=false 仅可存草稿", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  var a = Compose.resolveComposeActions(s, { apiReady: false });
  assert.equal(a.canSaveDraft, true);
  assert.equal(a.canSubmit, false);
  assert.equal(a.saveDraftLabel, "保存本地草稿");
  assert.ok(a.submitDisabledReason.indexOf("P0-4") !== -1);
});

test("resolveComposeActions: 阻断时显示计数", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.volumes = [];
  var a = Compose.resolveComposeActions(s, { apiReady: true });
  assert.equal(a.canSubmit, false);
  assert.ok(a.submitDisabledReason.indexOf("还有") !== -1);
  assert.ok(a.submitDisabledReason.indexOf("3") !== -1, "应显示 3 项未完成: " + a.submitDisabledReason);
});

test("resolveComposeActions: 校验通过可提交", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_level = "province";
  s.path.jurisdiction_code = "510000";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  var vol = s.volumes[0];
  vol.files = [Compose.newFileEntry({ file: fakeFile("test.pdf", 100), role: "main_document" })];
  var a = Compose.resolveComposeActions(s, { apiReady: true });
  assert.equal(a.canSubmit, true, a.submitDisabledReason);
  assert.equal(a.submitDisabledReason, "");
});

// ── titleUserEdited 跟踪 ──────────────────────────────────────────────
test("updateTitle 标记已编辑", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  assert.equal(s.set.titleUserEdited, false);
  Compose.updateTitle(s, "自定义名称");
  assert.equal(s.set.title, "自定义名称");
  assert.equal(s.set.titleUserEdited, true);
});

test("refreshAutoTitle: 未编辑时更新，已编辑时不覆盖", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.systemType = "construction_regional";
  s.path.jurisdiction_label = "四川省";
  s.set.edition_year = "2025";
  Compose.refreshAutoTitle(s);
  assert.equal(s.set.title, "四川省2025建设工程计价定额");
  assert.equal(s.set.titleUserEdited, false);

  // 手动修改后不覆盖
  Compose.updateTitle(s, "我的自定义名称");
  Compose.refreshAutoTitle(s);
  assert.equal(s.set.title, "我的自定义名称");
});

// ── 草稿序列化（含 unassignedFiles）─────────────────────────────────────
test("草稿键包含 schema/tenant/user/draftId", function () {
  var key = Compose.draftKey({ tenant: "t1", user: "u1", draftId: "d1" });
  assert.ok(key.indexOf(Compose.DRAFT_SCHEMA_VERSION) !== -1);
  assert.ok(key.indexOf("t1") !== -1);
  assert.ok(key.indexOf("u1") !== -1);
  assert.ok(key.indexOf("d1") !== -1);
});

test("serializeDraft 剥离 File 对象，包含 unassignedFiles", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.volumes[0].files = [Compose.newFileEntry({ file: fakeFile("x.pdf", 999, "application/pdf"), role: "main_document" })];
  s.unassignedFiles = [Compose.newFileEntry({ file: fakeFile("附录.pdf", 50), role: "appendix" })];
  var payload = Compose.serializeDraft(s);
  var f = payload.volumes[0].files[0];
  assert.equal(f.name, "x.pdf");
  assert.equal(f.size, 999);
  assert.equal(f.role, "main_document");
  assert.equal(f.file, undefined, "草稿禁止保存 File 对象");
  assert.equal(payload.unassignedFiles.length, 1);
  assert.equal(payload.unassignedFiles[0].name, "附录.pdf");
  assert.equal(payload.unassignedFiles[0].role, "appendix");
  assert.doesNotThrow(function () { JSON.stringify(payload); });
});

test("saveDraft/loadDraft 往返：含 unassignedFiles", function () {
  var storage = fakeStorage();
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, { tenant: "t1", user: "u1", draftId: "d2" });
  s.set.title = "四川2025定额";
  s.systemType = "construction_regional";
  s.path.jurisdiction_label = "四川省";
  s.path.jurisdiction_code = "510000";
  s.set.edition_year = "2025";
  s.volumes = [Compose.newVolume({ volume_title: "房屋建筑工程", files: [] })];
  s.volumes[0].files = [Compose.newFileEntry({ file: fakeFile("房屋建筑工程.pdf", 5), role: "main_document" })];
  s.unassignedFiles = [Compose.newFileEntry({ file: fakeFile("附录.pdf", 3), role: "appendix" })];
  var saved = Compose.saveDraft(s, storage);
  assert.equal(saved.ok, true);

  var loaded = Compose.loadDraft({ tenant: "t1", user: "u1", draftId: "d2" }, storage);
  assert.equal(loaded.ok, true);
  assert.equal(loaded.state.set.title, "四川2025定额");
  assert.equal(loaded.state.path.jurisdiction_label, "四川省");
  assert.equal(loaded.state.volumes.length, 1);
  assert.equal(loaded.state.unassignedFiles.length, 1);
  assert.equal(loaded.state.unassignedFiles[0].role, "appendix");
  var rf = loaded.state.volumes[0].files[0];
  assert.equal(rf.missingContent, true);
  assert.equal(rf.file, null);
  assert.equal(loaded.warning, Compose.DRAFT_FILE_WARNING);
});

test("文件角色包含六种", function () {
  var values = Compose.FILE_ROLES.map(function (r) { return r.value; });
  ["main_document", "cover", "table_of_contents", "appendix", "release_announcement", "other"].forEach(function (v) {
    assert.ok(values.indexOf(v) !== -1, v);
  });
});

test("add_volume/supplement 目标未选时报错", function () {
  var addVol = Compose.createComposeState(Compose.ACTIONS.ADD_VOLUME, {});
  assert.equal(addVol.targetKind, "publicationSets");
  var v = Compose.validateAddVolume(addVol);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("目标资料体系") !== -1; }));

  var sup = Compose.createComposeState(Compose.ACTIONS.SUPPLEMENT, {});
  assert.equal(sup.targetKind, "archives");
  v = Compose.validateSupplement(sup);
  assert.ok(v.blocking.some(function (m) { return m.indexOf("目标档案") !== -1; }));
});

test("totalMainCount 跨分册计数", function () {
  var s = Compose.createComposeState(Compose.ACTIONS.NEW_SET, {});
  s.volumes = [
    Compose.newVolume({ volume_title: "v1", files: [] }),
    Compose.newVolume({ volume_title: "v2", files: [] }),
  ];
  s.volumes[0].files = [Compose.newFileEntry({ file: fakeFile("a.pdf", 100), role: "main_document" })];
  s.volumes[1].files = [Compose.newFileEntry({ file: fakeFile("b.pdf", 100), role: "appendix" })];
  assert.equal(Compose.totalMainCount(s), 1);
});

// ── newFileEntry 自动角色检测 ─────────────────────────────────────────
test("newFileEntry: 未指定角色时从文件名自动检测", function () {
  var entry = Compose.newFileEntry({ name: "附录一.pdf" });
  assert.equal(entry.role, "appendix");

  var entry2 = Compose.newFileEntry({ name: "房屋建筑工程.pdf" });
  assert.equal(entry2.role, "main_document");

  // 显式指定角色优先
  var entry3 = Compose.newFileEntry({ name: "附录.pdf", role: "main_document" });
  assert.equal(entry3.role, "main_document");
});
