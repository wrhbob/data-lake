"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const QuotaApi = require("../../app/ui/quota-api.js");
const { CAP } = QuotaApi;

test("classifyStatus 区分 401/403、404、其它错误", () => {
  assert.equal(QuotaApi.classifyStatus(200), CAP.READY);
  assert.equal(QuotaApi.classifyStatus(204), CAP.READY);
  assert.equal(QuotaApi.classifyStatus(401), CAP.UNAUTHORIZED);
  assert.equal(QuotaApi.classifyStatus(403), CAP.UNAUTHORIZED);
  assert.equal(QuotaApi.classifyStatus(404), CAP.UNAVAILABLE);
  assert.equal(QuotaApi.classifyStatus(500), CAP.ERROR);
});

test("resolveFlags 生产环境忽略 URL quotaFlags", () => {
  const capabilities = QuotaApi.allStatus(CAP.UNAVAILABLE);
  const flags = QuotaApi.resolveFlags({
    capabilities,
    env: "production",
    search: "?quotaFlags=stats,archives",
  });
  assert.equal(flags.stats, false);
  assert.equal(flags.archives, false);
});

test("resolveFlags 开发环境允许 URL quotaFlags 覆盖", () => {
  const capabilities = QuotaApi.allStatus(CAP.UNAVAILABLE);
  const flags = QuotaApi.resolveFlags({
    capabilities,
    env: "development",
    search: "?quotaFlags=stats,archives",
  });
  assert.equal(flags.stats, true);
  assert.equal(flags.archives, true);
  assert.equal(flags.coverage, false);
});

test("resolveFlags 无 URL 覆盖时仅 ready 能力启用", () => {
  const capabilities = Object.assign(QuotaApi.allStatus(CAP.UNAVAILABLE), {
    stats: CAP.READY,
    reconciliation: CAP.READY,
  });
  const flags = QuotaApi.resolveFlags({ capabilities, env: "production" });
  assert.equal(flags.stats, true);
  assert.equal(flags.reconciliation, true);
  assert.equal(flags.archives, false);
});

test("detectEnv/isDev", () => {
  assert.equal(QuotaApi.detectEnv({ hostname: "127.0.0.1" }), "development");
  assert.equal(QuotaApi.detectEnv({ hostname: "app.example.com" }), "production");
  assert.equal(QuotaApi.detectEnv({ explicitEnv: "production", hostname: "localhost" }), "production");
  assert.equal(QuotaApi.isDev("development"), true);
  assert.equal(QuotaApi.isDev("production"), false);
});

function fakeFetch(response) {
  return async () => response;
}

test("probeCapabilities: 404 → 全部 unavailable", async () => {
  const api = QuotaApi.createQuotaApi({ fetchImpl: fakeFetch({ status: 404, ok: false }) });
  const caps = await api.probeCapabilities();
  QuotaApi.FEATURES.forEach((f) => assert.equal(caps[f], CAP.UNAVAILABLE));
});

test("probeCapabilities: 401 → 全部 unauthorized（与 404 区分）", async () => {
  const api = QuotaApi.createQuotaApi({ fetchImpl: fakeFetch({ status: 401, ok: false }) });
  const caps = await api.probeCapabilities();
  QuotaApi.FEATURES.forEach((f) => assert.equal(caps[f], CAP.UNAUTHORIZED));
});

test("probeCapabilities: 网络错误 → 全部 error（与 404/401 区分）", async () => {
  const api = QuotaApi.createQuotaApi({
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
  });
  const caps = await api.probeCapabilities();
  QuotaApi.FEATURES.forEach((f) => assert.equal(caps[f], CAP.ERROR));
});

test("probeCapabilities: 200 + fixture 字段映射", async () => {
  const fixture = JSON.parse(
    fs.readFileSync(path.join(__dirname, "fixtures", "capabilities_partial.json"), "utf-8")
  );
  const api = QuotaApi.createQuotaApi({
    fetchImpl: fakeFetch({ status: 200, ok: true, json: async () => fixture }),
  });
  const caps = await api.probeCapabilities();
  assert.equal(caps.stats, CAP.READY);
  assert.equal(caps.reconciliation, CAP.READY);
  assert.equal(caps.archives, CAP.UNAVAILABLE);
  assert.equal(caps.coverage, CAP.UNAVAILABLE);
  // fixture 未含 archiveFiles → unknown（不臆造）
  assert.equal(caps.archiveFiles, CAP.UNKNOWN);
});

test("request 网络错误返回 error 能力态而非抛出", async () => {
  const api = QuotaApi.createQuotaApi({
    fetchImpl: async () => {
      throw new Error("boom");
    },
  });
  const res = await api.getStats();
  assert.equal(res.status, CAP.ERROR);
  assert.equal(res.data, null);
});
