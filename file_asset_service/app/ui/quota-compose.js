/*
 * quota-compose.js · 清单定额档案台补录（四类新增）表单 · 状态 / 校验 / 本地草稿
 * SPEC-QA-001 · P0-5A · v2 表单简化
 *
 * 纯逻辑（无 DOM 依赖），可被浏览器与 node:test 同时加载。
 *
 * 简化规则:
 * - 新建定额体系仅 4 项阻断：定额体系 / 地区或行业 / 年份 / 至少一份正文文件。
 * - 体系名称自动生成，可修改，不阻断提交。
 * - 分册专业允许"待识别"，首次补录不强制填写。
 * - 法律状态根据日期自动计算，不要求用户选择。
 * - 内部仍保留完整字段校验，但不暴露英文字段名/技术错误给用户。
 * - 正文 PDF 自动创建分册卡片；封面/目录/附录/公告进入待归属文件池。
 * - 本地草稿只存元数据 + 文件名/大小/类型/角色 + 分册归属；禁止存 File 对象。
 * - 草稿键包含 schema 版本 + tenant + user + draftId。
 * - 目标查询 API 未就绪时，目标选择区显示占位，禁止使用静态选项。
 */
(function (global) {
  "use strict";

  const DRAFT_SCHEMA_VERSION = "qa1-p0_5a-v2";
  const DRAFT_KEY_PREFIX = "quota_compose_draft";
  const DRAFT_FILE_WARNING = "文件内容未保存在本地草稿中，请重新选择原文件。";

  // 四类新增动作
  const ACTIONS = Object.freeze({
    NEW_BOQ: "new_boq",
    NEW_SET: "new_set",
    ADD_VOLUME: "add_volume",
    SUPPLEMENT: "supplement",
  });

  const ACTION_META = Object.freeze({
    new_boq: { label: "新增清单规范", material_type: "boq_standard", needsTarget: null },
    new_set: { label: "新增定额", material_type: "quota_base", needsTarget: null },
    add_volume: { label: "向已有体系新增分册", material_type: null, needsTarget: "publicationSets" },
    supplement: { label: "向已有档案补充文件", material_type: null, needsTarget: "archives" },
  });

  // 数据体系性质（DB 小写枚举）
  const MATERIAL_TYPES = Object.freeze([
    { value: "boq_standard", label: "清单规范" },
    { value: "quota_base", label: "主定额" },
    { value: "quota_supplement", label: "补充定额" },
    { value: "quota_explanation", label: "定额解释" },
    { value: "amendment_errata", label: "修订/勘误" },
    { value: "related_notice", label: "相关公告" },
  ]);

  // 两条定额分类路径
  const SYSTEM_TYPES = Object.freeze([
    { value: "construction_regional", label: "建筑工程定额" },
    { value: "industry_specialty", label: "专业工程定额" },
  ]);

  // 六文件角色
  const FILE_ROLES = Object.freeze([
    { value: "main_document", label: "正文" },
    { value: "cover", label: "封面" },
    { value: "table_of_contents", label: "目录" },
    { value: "appendix", label: "附录" },
    { value: "release_announcement", label: "发布公告" },
    { value: "other", label: "其他" },
  ]);

  // 非正文角色关键词（用于自动识别）
  const NON_MAIN_PATTERNS = Object.freeze([
    { pattern: /附录|附件|appendix/i, role: "appendix" },
    { pattern: /封面|cover/i, role: "cover" },
    { pattern: /目录|toc|table.of.contents/i, role: "table_of_contents" },
    { pattern: /公告|通知|announcement|notice/i, role: "release_announcement" },
  ]);

  // 补充/解释/勘误 必须关联的关系类型
  const RELATION_TYPES = Object.freeze([
    { value: "supplements", label: "补充" },
    { value: "explains", label: "解释" },
    { value: "amends", label: "修订" },
    { value: "corrects", label: "勘误" },
  ]);

  // 需要关联体系字段的 material_type
  const RELATION_REQUIRED_MATERIALS = Object.freeze([
    "quota_supplement",
    "quota_explanation",
    "amendment_errata",
  ]);

  function requiresRelation(materialType) {
    return RELATION_REQUIRED_MATERIALS.indexOf(materialType) !== -1;
  }

  let idCounter = 0;
  function tempId(prefix) {
    idCounter += 1;
    const rand = Math.random().toString(36).slice(2, 8);
    return (prefix || "t") + "_" + Date.now().toString(36) + "_" + idCounter + rand;
  }

  // ── 角色自动识别 ─────────────────────────────────────────────────────
  function detectFileRole(filename) {
    if (!filename) return "main_document";
    const name = String(filename);
    for (let i = 0; i < NON_MAIN_PATTERNS.length; i++) {
      if (NON_MAIN_PATTERNS[i].pattern.test(name)) {
        return NON_MAIN_PATTERNS[i].role;
      }
    }
    return "main_document";
  }

  function isMainRole(role) {
    return role === "main_document";
  }

  // ── 文件名 → 分册名提取 ──────────────────────────────────────────────
  function extractVolumeName(filename) {
    if (!filename) return "";
    // 去掉 .pdf 后缀
    let name = String(filename).replace(/\.pdf$/i, "");
    // 去掉前导序号（如 "01-", "01_", "01.", "1-", "1."）
    name = name.replace(/^\d{1,3}[-_.\s]+/, "");
    // 去掉纯序号无分隔符的情况（如 "01xxx" → "xxx"）
    name = name.replace(/^0?\d{1,2}(?=[^\d])/, "");
    return name.trim();
  }

  // ── 体系名称自动生成 ────────────────────────────────────────────────
  function autoGenerateTitle(state) {
    const year = (state.set && state.set.edition_year) || "";
    if (!state.systemType) return "";
    if (state.systemType === "construction_regional") {
      var region = (state.path && state.path.jurisdiction_label) || "";
      // 去掉计划单列市后缀, 仅用于名称展示
      region = region.replace(/\(计划单列市\)$/, "");
      if (!region && !year) return "";
      return (region + year + "建设工程计价定额").trim();
    }
    if (state.systemType === "industry_specialty") {
      const industry = (state.path && state.path.industry_sector_label) || "";
      if (!industry && !year) return "";
      return (industry + year + "工程定额").trim();
    }
    return "";
  }

  // ── 法律状态自动计算 ────────────────────────────────────────────────
  function computeLegalStatus(set) {
    set = set || {};
    var eff = set.effective_date;
    if (!eff) {
      // 有发布日期但无实施日期 → unknown
      return "unknown";
    }
    try {
      var today = new Date();
      // 日期比较只取日期部分
      var effDate = new Date(eff);
      var todayStr = today.toISOString().slice(0, 10);
      var effStr = effDate.toISOString().slice(0, 10);
      if (effStr <= todayStr) return "effective";
      return "pending";
    } catch (_e) {
      return "unknown";
    }
  }

  // ── 工厂函数 ─────────────────────────────────────────────────────────
  function newFileEntry(meta) {
    meta = meta || {};
    var name = meta.name || (meta.file && meta.file.name) || "";
    // 角色：显式传入 > 文件名自动识别 > main_document
    var role = meta.role;
    if (!role && name) {
      role = detectFileRole(name);
    }
    if (!role) role = "main_document";
    return {
      tempId: tempId("file"),
      role: role,
      file: meta.file || null,
      name: name,
      size: meta.size != null ? meta.size : meta.file ? meta.file.size : null,
      type: meta.type || (meta.file && meta.file.type) || "",
      missingContent: Boolean(meta.missingContent),
    };
  }

  function newVolume(meta) {
    meta = meta || {};
    var files = Array.isArray(meta.files) ? meta.files.map(newFileEntry) : [];
    // 如果有文件且未提供分册名，从第一个文件名提取
    var title = meta.volume_title || "";
    if (!title && files.length) {
      title = extractVolumeName(files[0].name);
    }
    return {
      tempId: tempId("vol"),
      volume_title: title,
      discipline_code: meta.discipline_code || "",
      discipline_label: meta.discipline_label || "",
      volume_code: meta.volume_code || "",
      files: files,
    };
  }

  // ── 状态创建 ─────────────────────────────────────────────────────────
  function createComposeState(action, ctx) {
    ctx = ctx || {};
    var meta = ACTION_META[action] || ACTION_META.new_set;
    var usesVolumes = action === ACTIONS.NEW_BOQ || action === ACTIONS.NEW_SET || action === ACTIONS.ADD_VOLUME;
    return {
      open: true,
      action: action,
      material_type: meta.material_type || "",
      systemType: "",
      path: {
        jurisdiction_level: "",
        jurisdiction_code: "",
        jurisdiction_label: "",
        industry_sector_code: "",
        industry_sector_label: "",
      },
      set: {
        title: "",
        titleUserEdited: false,   // 用户是否手动修改过名称
        edition_year: "",
        // 高级信息（默认折叠）
        material_type: meta.material_type || "quota_base",
        edition_label: "",
        issuer_name: "",
        effective_date: "",
        publish_date: "",
        legal_status: "unknown",  // 自动计算
        standard_or_quota_code: "",
      },
      relation: { related_publication_set_id: "", relation_type: "" },
      targetKind: meta.needsTarget,
      targetId: "",
      targetLabel: "",
      volumes: usesVolumes ? [newVolume()] : [],
      // 待归属文件池（封面/目录/附录/公告 → 不自动创建分册）
      unassignedFiles: [],
      supplementFiles: action === ACTIONS.SUPPLEMENT ? [] : [],
      draftId: ctx.draftId || tempId("draft"),
      tenant: ctx.tenant || "platform_public",
      user: ctx.user || "anonymous",
      submitting: false,
    };
  }

  // ── 批量文件处理：正文→创建分册，其余→待归属 ─────────────────────────
  function processDroppedFiles(state, fileList) {
    if (!fileList || !fileList.length) return { volumes: [], unassigned: [] };
    var newVolumes = [];
    var newUnassigned = [];
    for (var i = 0; i < fileList.length; i++) {
      var file = fileList[i];
      var role = detectFileRole(file.name);
      var entry = newFileEntry({ file: file, name: file.name, size: file.size, type: file.type, role: role });
      if (isMainRole(role)) {
        // 正文 → 创建独立分册卡片
        var vol = newVolume({ volume_title: extractVolumeName(file.name), files: [entry] });
        newVolumes.push(vol);
      } else {
        // 非正文 → 进入待归属文件池
        newUnassigned.push(entry);
      }
    }
    return { volumes: newVolumes, unassigned: newUnassigned };
  }

  // ── 校验 ─────────────────────────────────────────────────────────────
  function volumeMainCount(volume) {
    return (volume.files || []).filter(function (f) { return f.role === "main_document"; }).length;
  }

  function totalMainCount(state) {
    var count = 0;
    (state.volumes || []).forEach(function (vol) {
      count += volumeMainCount(vol);
    });
    (state.supplementFiles || []).forEach(function (f) {
      if (f.role === "main_document") count += 1;
    });
    return count;
  }

  // ── 年份校验 ────────────────────────────────────────────────────────
  function _validateYear(val) {
    if (!val && val !== 0) return ""; // null/undefined 不算格式错误, 由必填校验处理
    var s = String(val).trim();
    if (!s) return "";
    if (!/^\d{4}$/.test(s)) return "年份须为 4 位数字";
    var y = parseInt(s, 10);
    var maxY = new Date().getFullYear() + 1;
    if (y < 1990) return "年份不能早于 1990";
    if (y > maxY) return "年份不能晚于 " + maxY;
    return "";
  }

  // ── 新建定额体系校验（仅 4 项阻断）────────────────────────────────────
  function validateNewSet(state) {
    var blocking = [];
    var warnings = [];
    var fields = {};

    // 阻断 1：定额体系
    if (!state.systemType) {
      blocking.push("请选择定额体系");
      fields.systemType = "请选择定额体系";
    }

    // 阻断 2：地区或行业
    if (state.systemType === "construction_regional") {
      if (!state.path.jurisdiction_code && state.path.jurisdiction_level !== "national") {
        blocking.push("请选择适用地区");
        fields.jurisdiction = "请选择适用地区";
      }
    } else if (state.systemType === "industry_specialty") {
      if (!state.path.industry_sector_code) {
        blocking.push("请选择行业");
        fields.industry = "请选择行业";
      }
    } else if (!state.systemType) {
      // systemType 未选时不报地区错误
      fields.jurisdiction = "";
    }

    // 阻断 3：年份 (4 位数字, 1990 – 当前年+1)
    var yearVal = state.set.edition_year;
    var yearErr = _validateYear(yearVal);
    if (yearErr) {
      blocking.push(yearErr);
      fields.edition_year = yearErr;
    } else if (!yearVal) {
      blocking.push("请填写年份");
      fields.edition_year = "请填写年份";
    }

    // 阻断 4：至少一份正文文件
    var bodyCount = totalMainCount(state);
    if (bodyCount === 0) {
      blocking.push("请至少添加一份正文文件");
      fields.files = "请至少添加一份正文文件";
    }

    // 非阻断警告：分册级别校验
    (state.volumes || []).forEach(function (vol, idx) {
      var label = vol.volume_title || ("分册" + (idx + 1));
      var mains = volumeMainCount(vol);
      if (mains === 0) {
        warnings.push(label + "：缺少正文文件");
      }
      if (mains > 1) {
        warnings.push(label + "：包含多个正文文件");
      }
    });

    // 关联体系字段（仅当 material_type 需要时，不阻断提交）
    if (requiresRelation(state.set.material_type || state.material_type)) {
      if (!state.relation.related_publication_set_id) {
        warnings.push("请关联主体系");
      }
      if (!state.relation.relation_type) {
        warnings.push("请选择关联关系类型");
      }
    }

    return {
      ok: blocking.length === 0,
      blocking: blocking,
      warnings: warnings,
      blockingCount: blocking.length,
      fields: fields,
    };
  }

  // ── 新增分册校验 ─────────────────────────────────────────────────────
  function validateAddVolume(state) {
    var blocking = [];
    var warnings = [];
    var fields = {};

    if (state.targetKind && !state.targetId) {
      blocking.push("请选择目标资料体系");
      fields.target = "请选择目标资料体系";
    }

    var hasTitle = (state.volumes || []).some(function (v) { return !!v.volume_title; });
    if (!hasTitle) {
      blocking.push("请填写分册名称");
      fields.volume_title = "请填写分册名称";
    }

    if (totalMainCount(state) === 0) {
      blocking.push("请添加正文文件");
      fields.files = "请添加正文文件";
    }

    return {
      ok: blocking.length === 0,
      blocking: blocking,
      warnings: warnings,
      blockingCount: blocking.length,
      fields: fields,
    };
  }

  // ── 补充文件校验 ─────────────────────────────────────────────────────
  function validateSupplement(state) {
    var blocking = [];
    var fields = {};

    if (state.targetKind && !state.targetId) {
      blocking.push("请选择目标档案");
      fields.target = "请选择目标档案";
    }
    if (!(state.supplementFiles || []).length) {
      blocking.push("请至少添加一个文件");
      fields.files = "请至少添加一个文件";
    }

    return {
      ok: blocking.length === 0,
      blocking: blocking,
      warnings: [],
      blockingCount: blocking.length,
      fields: fields,
    };
  }

  // ── 新增清单规范校验 ─────────────────────────────────────────────────
  function validateNewBoq(state) {
    var blocking = [];
    var warnings = [];
    var fields = {};

    if (!state.set.title) {
      blocking.push("请填写适用范围");
      fields.title = "请填写适用范围";
    }

    if (!state.set.edition_year && !state.set.edition_label) {
      blocking.push("请填写年份或版本");
      fields.edition_year = "请填写年份或版本";
    }

    if (totalMainCount(state) === 0) {
      blocking.push("请添加正文文件");
      fields.files = "请添加正文文件";
    }

    // 内部校验：每个分册恰好一个正文
    (state.volumes || []).forEach(function (vol, idx) {
      var label = vol.volume_title || ("分册" + (idx + 1));
      var mains = volumeMainCount(vol);
      if (mains === 0) warnings.push(label + "：缺少正文文件");
      if (mains > 1) warnings.push(label + "：包含多个正文文件");
    });

    return {
      ok: blocking.length === 0,
      blocking: blocking,
      warnings: warnings,
      blockingCount: blocking.length,
      fields: fields,
    };
  }

  function validateCompose(state) {
    switch (state.action) {
      case ACTIONS.NEW_SET:
        return validateNewSet(state);
      case ACTIONS.ADD_VOLUME:
        return validateAddVolume(state);
      case ACTIONS.SUPPLEMENT:
        return validateSupplement(state);
      case ACTIONS.NEW_BOQ:
        return validateNewBoq(state);
      default:
        return validateNewSet(state);
    }
  }

  // ── footer 动作判定 ──────────────────────────────────────────────────
  function resolveComposeActions(state, opts) {
    opts = opts || {};
    var apiReady = Boolean(opts.apiReady);
    var validation = validateCompose(state);
    var reason = "";
    if (!apiReady) {
      reason = "P0-4 接口未就绪，暂只能保存本地草稿。";
    } else if (!validation.ok) {
      reason = "还有 " + validation.blockingCount + " 项必填信息未完成";
    }
    return {
      canSaveDraft: true,
      canSubmit: apiReady && validation.ok,
      submitDisabledReason: reason,
      saveDraftLabel: apiReady ? "保存草稿" : "保存本地草稿",
      validation: validation,
    };
  }

  // ── 更新体系名称（保持 titleUserEdited 跟踪）─────────────────────────
  function updateTitle(state, newTitle) {
    state.set.title = newTitle;
    state.set.titleUserEdited = true;
  }

  function refreshAutoTitle(state) {
    if (state.set.titleUserEdited) return; // 用户手动修改过，不覆盖
    var generated = autoGenerateTitle(state);
    if (generated) {
      state.set.title = generated;
    }
  }

  // ── 本地草稿（不存 File 对象）────────────────────────────────────────
  function draftKey(parts) {
    parts = parts || {};
    return [
      DRAFT_KEY_PREFIX,
      DRAFT_SCHEMA_VERSION,
      parts.tenant || "platform_public",
      parts.user || "anonymous",
      parts.draftId || "unknown",
    ].join("::");
  }

  function serializeFile(entry) {
    return {
      role: entry.role,
      name: entry.name || (entry.file && entry.file.name) || "",
      size: entry.size != null ? entry.size : entry.file ? entry.file.size : null,
      type: entry.type || (entry.file && entry.file.type) || "",
    };
  }

  function serializeDraft(state) {
    return {
      schemaVersion: DRAFT_SCHEMA_VERSION,
      savedAt: new Date().toISOString(),
      tenant: state.tenant,
      user: state.user,
      draftId: state.draftId,
      action: state.action,
      material_type: state.material_type,
      systemType: state.systemType,
      path: Object.assign({}, state.path),
      set: Object.assign({}, state.set),
      relation: Object.assign({}, state.relation),
      targetKind: state.targetKind,
      targetId: state.targetId,
      targetLabel: state.targetLabel,
      volumes: (state.volumes || []).map(function (vol) {
        return {
          volume_title: vol.volume_title,
          discipline_code: vol.discipline_code,
          discipline_label: vol.discipline_label,
          volume_code: vol.volume_code,
          files: (vol.files || []).map(serializeFile),
        };
      }),
      supplementFiles: (state.supplementFiles || []).map(serializeFile),
      unassignedFiles: (state.unassignedFiles || []).map(serializeFile),
    };
  }

  function deserializeDraft(payload) {
    var restoreFiles = function (arr) {
      return (arr || []).map(function (f) {
        return newFileEntry({ role: f.role, name: f.name, size: f.size, type: f.type, missingContent: true, file: null });
      });
    };
    var state = createComposeState(payload.action || ACTIONS.NEW_SET, {
      draftId: payload.draftId,
      tenant: payload.tenant,
      user: payload.user,
    });
    state.material_type = payload.material_type || state.material_type;
    state.systemType = payload.systemType || "";
    state.path = Object.assign(state.path, payload.path || {});
    state.set = Object.assign(state.set, payload.set || {});
    state.relation = Object.assign(state.relation, payload.relation || {});
    state.targetKind = payload.targetKind || state.targetKind;
    state.targetId = payload.targetId || "";
    state.targetLabel = payload.targetLabel || "";
    state.volumes = (payload.volumes || []).map(function (v) {
      return newVolume({
        volume_title: v.volume_title,
        discipline_code: v.discipline_code,
        discipline_label: v.discipline_label,
        volume_code: v.volume_code,
        files: [],
      });
    });
    (payload.volumes || []).forEach(function (v, idx) {
      if (state.volumes[idx]) state.volumes[idx].files = restoreFiles(v.files);
    });
    if (!state.volumes.length && (payload.action === ACTIONS.NEW_SET || payload.action === ACTIONS.NEW_BOQ)) {
      state.volumes = [newVolume()];
    }
    state.supplementFiles = restoreFiles(payload.supplementFiles);
    state.unassignedFiles = restoreFiles(payload.unassignedFiles);
    return state;
  }

  function saveDraft(state, storage) {
    var store = storage || (typeof global.localStorage !== "undefined" ? global.localStorage : null);
    if (!store) return { ok: false, error: "localStorage 不可用" };
    try {
      var key = draftKey(state);
      var payload = serializeDraft(state);
      var json = JSON.stringify(payload);
      store.setItem(key, json);
      return { ok: true, key: key, payload: payload };
    } catch (e) {
      var msg = "草稿保存失败: " + (e && e.message ? e.message : String(e));
      if (typeof console !== "undefined" && console.error) console.error(msg, e);
      return { ok: false, error: msg };
    }
  }

  function loadDraft(parts, storage) {
    var store = storage || (typeof global.localStorage !== "undefined" ? global.localStorage : null);
    if (!store) return { ok: false, error: "localStorage 不可用" };
    try {
      var key = draftKey(parts);
      var raw = store.getItem(key);
      if (!raw) return { ok: false, error: "未找到草稿" };
      var payload;
      try {
        payload = JSON.parse(raw);
      } catch (_e) {
        return { ok: false, error: "草稿解析失败" };
      }
      var hasFiles =
        (payload.supplementFiles && payload.supplementFiles.length) ||
        (payload.unassignedFiles && payload.unassignedFiles.length) ||
        (payload.volumes || []).some(function (v) { return (v.files || []).length; });
      var restored = deserializeDraft(payload);
      return {
        ok: true,
        key: key,
        state: restored,
        warning: hasFiles ? DRAFT_FILE_WARNING : "",
      };
    } catch (e) {
      var msg = "草稿恢复失败: " + (e && e.message ? e.message : String(e));
      if (typeof console !== "undefined" && console.error) console.error(msg, e);
      return { ok: false, error: msg };
    }
  }

  function deleteDraft(parts, storage) {
    var store = storage || (typeof global.localStorage !== "undefined" ? global.localStorage : null);
    if (!store) return { ok: false };
    store.removeItem(draftKey(parts));
    return { ok: true };
  }

  // ── 导出 ─────────────────────────────────────────────────────────────
  var QuotaCompose = {
    DRAFT_SCHEMA_VERSION: DRAFT_SCHEMA_VERSION,
    DRAFT_KEY_PREFIX: DRAFT_KEY_PREFIX,
    DRAFT_FILE_WARNING: DRAFT_FILE_WARNING,
    ACTIONS: ACTIONS,
    ACTION_META: ACTION_META,
    MATERIAL_TYPES: MATERIAL_TYPES,
    SYSTEM_TYPES: SYSTEM_TYPES,
    FILE_ROLES: FILE_ROLES,
    RELATION_TYPES: RELATION_TYPES,
    RELATION_REQUIRED_MATERIALS: RELATION_REQUIRED_MATERIALS,
    requiresRelation: requiresRelation,
    tempId: tempId,
    newFileEntry: newFileEntry,
    newVolume: newVolume,
    createComposeState: createComposeState,
    volumeMainCount: volumeMainCount,
    totalMainCount: totalMainCount,
    validateCompose: validateCompose,
    validateNewSet: validateNewSet,
    validateAddVolume: validateAddVolume,
    validateSupplement: validateSupplement,
    validateNewBoq: validateNewBoq,
    resolveComposeActions: resolveComposeActions,
    detectFileRole: detectFileRole,
    isMainRole: isMainRole,
    extractVolumeName: extractVolumeName,
    autoGenerateTitle: autoGenerateTitle,
    computeLegalStatus: computeLegalStatus,
    processDroppedFiles: processDroppedFiles,
    updateTitle: updateTitle,
    refreshAutoTitle: refreshAutoTitle,
    draftKey: draftKey,
    serializeDraft: serializeDraft,
    deserializeDraft: deserializeDraft,
    saveDraft: saveDraft,
    loadDraft: loadDraft,
    deleteDraft: deleteDraft,
  };

  global.QuotaCompose = QuotaCompose;
  if (typeof module !== "undefined" && module.exports) module.exports = QuotaCompose;
})(typeof window !== "undefined" ? window : globalThis);
