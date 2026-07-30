/*
 * quota-parse-api.js · 清单定额解析 7 个端点的 HTTP 客户端
 * 不依赖 quota-api.js,自己建 fetch wrapper;与 quota-api.js 同款五态错误契约。
 *
 * 端点契约(quota/web-frontend/SPEC.md §6 + v0.4 manifest 落 MinIO):
 *   1. POST   /archives/{id}/parse            触发阶段 A
 *   2. GET    /archives/{id}/candidate.xlsx  下载候选
 *   3. POST   /archives/{id}/reviewed        上传复核后的 xlsx
 *   4. GET    /archives/{id}/final.xlsx      下载最终
 *   5. GET    /archives/{id}/manifest        JSON 清单
 *   6. GET    /archives/{id}/qa-report       QA 报告(json / md)
 *   7. POST   /archives/{id}/parse/delete    清除解析产物
 */
(function (global) {
  "use strict";

  const QUOTA_API_BASE = "/api/data-lake/quota";

  // 能力五态(对齐 quota-api.js §3)
  const CAP = Object.freeze({
    UNKNOWN: "unknown",
    READY: "ready",
    UNAVAILABLE: "unavailable",
    UNAUTHORIZED: "unauthorized",
    ERROR: "error",
  });

  const ENDPOINTS = Object.freeze({
    triggerParse:    { method: "POST",   path: "/archives/{id}/parse" },
    candidateXlsx:   { method: "GET",    path: "/archives/{id}/candidate.xlsx" },
    uploadReviewed:  { method: "POST",   path: "/archives/{id}/reviewed" },
    finalXlsx:       { method: "GET",    path: "/archives/{id}/final.xlsx" },
    manifest:        { method: "GET",    path: "/archives/{id}/manifest" },
    qaReport:        { method: "GET",    path: "/archives/{id}/qa-report" },
    deleteParse:     { method: "POST",   path: "/archives/{id}/parse/delete" },
  });

  function classifyStatus(httpStatus) {
    if (httpStatus >= 200 && httpStatus < 300) return CAP.READY;
    if (httpStatus === 401 || httpStatus === 403) return CAP.UNAUTHORIZED;
    if (httpStatus === 404) return CAP.UNAVAILABLE;
    return CAP.ERROR;
  }

  function createQuotaParseApi(options) {
    options = options || {};
    const base = options.base || QUOTA_API_BASE;
    const fetchImpl =
      options.fetchImpl ||
      (typeof global.fetch === "function" ? global.fetch.bind(global) : null);

    function resolvePath(ep, archiveId) {
      return ep.path.replace("{id}", encodeURIComponent(archiveId));
    }

    // 通用 request:JSON 入参 + JSON/空响应
    async function jsonRequest(ep, archiveId, opts) {
      opts = opts || {};
      if (!fetchImpl) {
        return { status: CAP.ERROR, httpStatus: 0, data: null, error: "fetch 不可用" };
      }
      try {
        const res = await fetchImpl(base + resolvePath(ep, archiveId), {
          method: ep.method,
          headers: opts.headers || { Accept: "application/json" },
          body: opts.body,
        });
        const status = classifyStatus(res.status);
        if (status !== CAP.READY) {
          let body = null;
          try { body = await res.json(); } catch (_e) { body = null; }
          const detail = (body && (body.detail || body.error)) || "";
          return { status, httpStatus: res.status, data: body, error: detail || `HTTP ${res.status}` };
        }
        if (res.status === 204) {
          return { status: CAP.READY, httpStatus: 204, data: null, error: "" };
        }
        let data = null;
        try { data = await res.json(); } catch (_e) { data = null; }
        return { status: CAP.READY, httpStatus: res.status, data, error: "" };
      } catch (_e) {
        return { status: CAP.ERROR, httpStatus: 0, data: null, error: "网络错误" };
      }
    }

    // xlsx 下载:返回 { status, httpStatus, blob, error, filename }
    async function downloadRequest(ep, archiveId, suggestedFilename) {
      if (!fetchImpl) {
        return { status: CAP.ERROR, httpStatus: 0, blob: null, error: "fetch 不可用" };
      }
      try {
        const res = await fetchImpl(base + resolvePath(ep, archiveId), {
          method: ep.method,
          headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
        });
        if (!res.ok) {
          let detail = "";
          try {
            const body = await res.json();
            detail = body.detail || body.error || "";
          } catch (_e) { /* ignore */ }
          return {
            status: classifyStatus(res.status),
            httpStatus: res.status,
            blob: null,
            error: detail || `HTTP ${res.status}`,
          };
        }
        const blob = await res.blob();
        // 试着从 Content-Disposition 拿文件名;拿不到用 suggestedFilename
        let filename = suggestedFilename;
        const cd = res.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
        if (m) filename = decodeURIComponent(m[1]);
        return { status: CAP.READY, httpStatus: res.status, blob, error: "", filename };
      } catch (_e) {
        return { status: CAP.ERROR, httpStatus: 0, blob: null, error: "网络错误" };
      }
    }

    return {
      base,

      // 1. POST /parse  body: { profile?: "sichuan" | "chongqing" }
      triggerParse: function (archiveId, profile) {
        const body = profile ? JSON.stringify({ profile }) : null;
        return jsonRequest(ENDPOINTS.triggerParse, archiveId, {
          body,
          headers: body
            ? { "Content-Type": "application/json", Accept: "application/json" }
            : { Accept: "application/json" },
        });
      },

      // 2. GET /candidate.xlsx — blob download
      downloadCandidate: function (archiveId, suggestedFilename) {
        return downloadRequest(
          ENDPOINTS.candidateXlsx,
          archiveId,
          suggestedFilename || "candidate.xlsx",
        );
      },

      // 3. POST /reviewed — multipart upload
      uploadReviewed: function (archiveId, file) {
        if (!file) {
          return Promise.resolve({
            status: CAP.ERROR, httpStatus: 0, data: null, error: "未选择文件",
          });
        }
        const fd = new FormData();
        fd.append("file", file, file.name || "reviewed.xlsx");
        return jsonRequest(ENDPOINTS.uploadReviewed, archiveId, {
          body: fd,
          // 设 Content-Type 由浏览器自动加 boundary;只保留 Accept
          headers: { Accept: "application/json" },
        });
      },

      // 4. GET /final.xlsx
      downloadFinal: function (archiveId, suggestedFilename) {
        return downloadRequest(
          ENDPOINTS.finalXlsx,
          archiveId,
          suggestedFilename || "final.xlsx",
        );
      },

      // 5. GET /manifest
      getManifest: function (archiveId) {
        return jsonRequest(ENDPOINTS.manifest, archiveId);
      },

      // 6. GET /qa-report (?format=md 可选)
      getQaReport: function (archiveId, format) {
        const path = `${base}${resolvePath(ENDPOINTS.qaReport, archiveId)}${
          format === "md" ? "?format=md" : ""
        }`;
        if (!fetchImpl) {
          return Promise.resolve({
            status: CAP.ERROR, httpStatus: 0, data: null, error: "fetch 不可用",
          });
        }
        return fetchImpl(path, { headers: { Accept: "application/json" } }).then(async (res) => {
          const status = classifyStatus(res.status);
          if (status !== CAP.READY) {
            return { status, httpStatus: res.status, data: null, error: `HTTP ${res.status}` };
          }
          if (format === "md") {
            const text = await res.text();
            return { status: CAP.READY, httpStatus: res.status, data: text, isMarkdown: true, error: "" };
          }
          const data = await res.json();
          return { status: CAP.READY, httpStatus: res.status, data, isMarkdown: false, error: "" };
        }).catch(() => ({ status: CAP.ERROR, httpStatus: 0, data: null, error: "网络错误" }));
      },

      // 7. POST /parse/delete
      deleteParseResult: function (archiveId) {
        return jsonRequest(ENDPOINTS.deleteParse, archiveId);
      },

      // 工具:把 blob 触发成浏览器下载
      saveBlob: function (blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename || "download.xlsx";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        // 给浏览器一点点时间再 revoke
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
    };
  }

  const QuotaParseApi = {
    QUOTA_API_BASE,
    CAP,
    ENDPOINTS,
    createQuotaParseApi,
  };

  global.QuotaParseApi = QuotaParseApi;
  if (typeof module !== "undefined" && module.exports) module.exports = QuotaParseApi;
})(typeof window !== "undefined" ? window : globalThis);
