/*
 * quota-api.js · 清单定额档案台 (domain_type=quota) API 客户端与能力状态
 * SPEC-QA-001 · P0-5A
 *
 * 铁律:
 * - API 前缀统一 /api/data-lake/quota（SPEC 冻结）。
 * - 能力状态使用五态：unknown/ready/unavailable/unauthorized/error（禁止布尔）。
 * - Feature flag：URL query 仅在开发环境允许开启；生产环境由服务端能力/部署配置控制。
 * - 401/403、404、网络错误必须区分。
 * - 本模块不 Mock、不写死数据；数据全部来自真实 API，未就绪时返回 unavailable/unknown。
 */
(function (global) {
  "use strict";

  const QUOTA_API_BASE = "/api/data-lake/quota";

  // 能力五态（禁止用布尔表达能力）
  const CAP = Object.freeze({
    UNKNOWN: "unknown",
    READY: "ready",
    UNAVAILABLE: "unavailable",
    UNAUTHORIZED: "unauthorized",
    ERROR: "error",
  });
  const CAP_VALUES = Object.freeze(Object.values(CAP));

  // 能力清单（对应 SPEC 冻结端点）
  const FEATURES = Object.freeze([
    "stats",
    "facets",
    "archives",
    "reconciliation",
    "publicationSets",
    "coverage",
    "compose",
    "archiveFiles",
  ]);

  // 端点映射（compose / archiveFiles 为 POST）
  const ENDPOINTS = Object.freeze({
    capabilities: { method: "GET", path: "/capabilities" },
    stats: { method: "GET", path: "/stats" },
    facets: { method: "GET", path: "/facets" },
    archives: { method: "GET", path: "/archives" },
    archiveDetail: { method: "GET", path: "/archives/{id}" },
    reconciliation: { method: "GET", path: "/reconciliation" },
    publicationSets: { method: "GET", path: "/publication-sets" },
    coverage: { method: "GET", path: "/coverage" },
    compose: { method: "POST", path: "/compose" },
    archiveFiles: { method: "POST", path: "/archives/{id}/files" },
  });

  function allStatus(status) {
    const map = {};
    FEATURES.forEach((f) => {
      map[f] = status;
    });
    return map;
  }

  // HTTP 状态码 → 能力五态（区分 401/403、404、其它错误）
  function classifyStatus(httpStatus) {
    if (httpStatus >= 200 && httpStatus < 300) return CAP.READY;
    if (httpStatus === 401 || httpStatus === 403) return CAP.UNAUTHORIZED;
    if (httpStatus === 404) return CAP.UNAVAILABLE;
    return CAP.ERROR;
  }

  function normalizeCapabilities(body) {
    const source = (body && (body.features || body.capabilities)) || {};
    const map = {};
    FEATURES.forEach((f) => {
      const value = source[f];
      map[f] = CAP_VALUES.includes(value) ? value : CAP.UNKNOWN;
    });
    return map;
  }

  // 开发环境判定：显式 env 优先，其次 hostname 白名单
  function detectEnv(opts) {
    opts = opts || {};
    if (opts.env) return opts.env;
    if (typeof opts.explicitEnv === "string") return opts.explicitEnv;
    const host = opts.hostname || "";
    if (host === "localhost" || host === "127.0.0.1" || host === "" || host === "0.0.0.0") {
      return "development";
    }
    return "production";
  }

  function isDev(env) {
    return env === "development" || env === "dev";
  }

  // 解析 URL query 中的 quotaFlags（仅开发环境调用）：?quotaFlags=stats,archives
  function parseFlagParam(search) {
    const overrides = {};
    if (!search) return overrides;
    let raw = "";
    try {
      const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
      raw = params.get("quotaFlags") || "";
    } catch (_e) {
      raw = "";
    }
    raw
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean)
      .forEach((token) => {
        const [name, mode] = token.split(":");
        if (FEATURES.includes(name)) overrides[name] = mode !== "off";
      });
    return overrides;
  }

  /*
   * 计算 feature flag（是否启用某能力区块）。
   * - 生产环境：仅当服务端能力为 ready 时启用；URL query 一律忽略。
   * - 开发环境：URL quotaFlags 可覆盖，便于联调。
   */
  function resolveFlags(input) {
    input = input || {};
    const capabilities = input.capabilities || allStatus(CAP.UNKNOWN);
    const env = input.env || "production";
    const flags = {};
    const devOverrides = isDev(env) ? parseFlagParam(input.search) : {};
    FEATURES.forEach((f) => {
      if (Object.prototype.hasOwnProperty.call(devOverrides, f)) {
        flags[f] = Boolean(devOverrides[f]);
      } else {
        flags[f] = capabilities[f] === CAP.READY;
      }
    });
    return flags;
  }

  // 无能力时的原因文案（供页签禁用/区块占位复用，避免各处重复"建设中"）
  function reasonText(status) {
    switch (status) {
      case CAP.READY:
        return "";
      case CAP.UNAUTHORIZED:
        return "无访问权限（401/403），请确认登录或授权。";
      case CAP.UNAVAILABLE:
        return "P0-4 接口未就绪，能力建设中。";
      case CAP.ERROR:
        return "接口异常，请稍后重试。";
      case CAP.UNKNOWN:
      default:
        return "能力探测中…";
    }
  }

  function createQuotaApi(options) {
    options = options || {};
    const base = options.base || QUOTA_API_BASE;
    const fetchImpl =
      options.fetchImpl ||
      (typeof global.fetch === "function" ? global.fetch.bind(global) : null);

    function buildUrl(path, params) {
      let url = base + path;
      if (params) {
        const usp = new URLSearchParams();
        Object.keys(params).forEach((key) => {
          const value = params[key];
          if (value !== undefined && value !== null && value !== "" && value !== "all") {
            usp.append(key, value);
          }
        });
        const qs = usp.toString();
        if (qs) url += "?" + qs;
      }
      return url;
    }

    // 统一请求包装：永远返回 { status(能力五态), httpStatus, data, error }
    async function request(path, opts) {
      opts = opts || {};
      if (!fetchImpl) {
        return { status: CAP.ERROR, httpStatus: 0, data: null, error: "fetch 不可用" };
      }
      try {
        const res = await fetchImpl(buildUrl(path, opts.params), {
          method: opts.method || "GET",
          headers: opts.headers || { Accept: "application/json" },
          body: opts.body,
        });
        const status = classifyStatus(res.status);
        if (status !== CAP.READY) {
          let body = null;
          try { body = await res.json(); } catch (_e) { body = null; }
          const detail = (body && body.detail) ? body.detail : "";
          return { status, httpStatus: res.status, data: body, error: detail || reasonText(status) };
        }
        let data = null;
        try {
          data = await res.json();
        } catch (_e) {
          data = null;
        }
        return { status: CAP.READY, httpStatus: res.status, data, error: "" };
      } catch (_e) {
        // 网络错误：与 404/401 明确区分
        return { status: CAP.ERROR, httpStatus: 0, data: null, error: "网络错误，无法连接接口。" };
      }
    }

    async function probeCapabilities() {
      if (!fetchImpl) return allStatus(CAP.ERROR);
      try {
        const res = await fetchImpl(buildUrl(ENDPOINTS.capabilities.path), {
          method: "GET",
          headers: { Accept: "application/json" },
        });
        if (res.status === 401 || res.status === 403) return allStatus(CAP.UNAUTHORIZED);
        if (res.status === 404) return allStatus(CAP.UNAVAILABLE);
        if (!res.ok) return allStatus(CAP.ERROR);
        let body = null;
        try {
          body = await res.json();
        } catch (_e) {
          body = null;
        }
        return normalizeCapabilities(body);
      } catch (_e) {
        return allStatus(CAP.ERROR);
      }
    }

    return {
      base,
      probeCapabilities,
      getStats: () => request(ENDPOINTS.stats.path),
      getFacets: (params) => request(ENDPOINTS.facets.path, { params }),
      getArchives: (params) => request(ENDPOINTS.archives.path, { params }),
      getArchiveDetail: (id) => request("/archives/" + encodeURIComponent(id)),
      getReconciliation: (params) => request(ENDPOINTS.reconciliation.path, { params }),
      getPublicationSets: (params) => request(ENDPOINTS.publicationSets.path, { params }),
      getCoverage: (params) => request(ENDPOINTS.coverage.path, { params }),
      // 写操作留空接线，P0-5A 不允许经旧 Archive 接口真实写入
      compose: (payload) =>
        request(ENDPOINTS.compose.path, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(payload || {}),
        }),
    };
  }

  const QuotaApi = {
    QUOTA_API_BASE,
    CAP,
    CAP_VALUES,
    FEATURES,
    ENDPOINTS,
    classifyStatus,
    normalizeCapabilities,
    detectEnv,
    isDev,
    parseFlagParam,
    resolveFlags,
    reasonText,
    allStatus,
    createQuotaApi,
  };

  global.QuotaApi = QuotaApi;
  if (typeof module !== "undefined" && module.exports) module.exports = QuotaApi;
})(typeof window !== "undefined" ? window : globalThis);
