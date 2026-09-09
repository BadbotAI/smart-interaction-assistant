// 静态站 mock 状态机冒烟：node tests/mock_smoke.mjs
// 驱动 v7 完整动线（配置智能路由模型 → benchmark 修正 → 判维路由 → 模型操作），断言状态推进。
// mock 曾出过多份重复函数覆盖的事故——语法检查不够，必须行为断言。
import { readFileSync } from "fs";

global.window = {
  location: { origin: "http://demo.local", href: "http://demo.local/router.html" },
  addEventListener() {},
  MOCK_DATA: undefined,
  fetch: async () => { throw new Error("real fetch should not be called for /api|/v1"); },
};
global.location = window.location;

// 载入快照与 mock（与线上同文件）
eval(readFileSync("docs/mock_data.js", "utf8").replace("window.MOCK_DATA", "window.MOCK_DATA"));
eval(readFileSync("docs/mock_api.js", "utf8"));

const api = async (path, opts) => {
  const res = await window.fetch(path, opts || {});
  return res.json();
};
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
// SSE 路由：收齐全部事件（skip_card_match 跳过组件触发层）
const sse = async (body) => {
  const res = await window.fetch("/v1/route", { method: "POST", body: JSON.stringify({ skip_card_match: true, ...body }) });
  const txt = await res.text();
  return txt.split("\n\n").filter(Boolean).map(l => JSON.parse(l.replace(/^data:/, "")));
};
const finalOf = evts => evts.find(e => e.step === "final") || {};

let failures = 0;
const assert = (cond, msg) => { if (!cond) { failures++; console.error("FAIL:", msg); } };

// 1) 初始态：未配置智能路由模型，benchmark 表就绪
let rm = await api("/api/settings/router-model");
assert(rm.router === null, "初始应无智能路由模型");
let bm = await api("/api/benchmark");
assert((bm.dims || []).length === 7, "应有 7 个 benchmark 维度，实际 " + (bm.dims || []).length);
assert(bm.scores["swift-4b"] && bm.scores["swift-4b"].knowledge === 58, "快照分应就绪");
assert(bm.scores["swift-4b"].multimodal === null, "缺失分应为 null");
let prof = await api("/api/profile?policy_id=policy-scene-fast");
assert(prof.generated === true && prof.clusters.length === 7, "画像常绿：7 个维度行");
assert(prof.alpha === 0.25, "省钱优先 α 应为 0.25，实际 " + prof.alpha);

// 2) 未配置路由模型：路由应 no_router 兜底；闲聊硬规则不受硬依赖影响
let evts = await sse({ text: "起草一份复工通知", policy_id: "policy-global-balanced" });
let fin = finalOf(evts);
assert(fin.decision_summary && fin.decision_summary.route_layer === "no_router", "未配置应 no_router 兜底: " + JSON.stringify(fin.decision_summary || {}));
evts = await sse({ text: "你好，在吗", policy_id: "policy-global-balanced" });
fin = finalOf(evts);
assert(fin.decision_summary.route_layer === "rule" && !(fin.decision_summary.dimensions || []).length,
  "闲聊应硬规则直答（未配置路由模型也不兜底）: " + JSON.stringify(fin.decision_summary));

// 3) 配置智能路由模型（含校验）
let r = await post("/api/settings/router-model", { model_id: "pilot-2b" });
assert(r.error, "缺显示名应报错");
r = await post("/api/settings/router-model", { model_id: "pilot-2b", display_name: "Pilot-2B",
  endpoint: "https://api.mocklab.local/pilot", credential_ref: "vault://router/pilot" });
assert(r.ok && r.router.model_id === "pilot-2b", "配置路由模型");
rm = await api("/api/settings/router-model");
assert(rm.router && rm.router.display_name === "Pilot-2B", "配置后可回读");

// 4) 判维路由：复合任务双维、多模态过滤、强制聚合
evts = await sse({ text: "帮我算下这单的毛利率再写一篇经营分析", policy_id: "policy-global-balanced" });
const dimsEvt = evts.find(e => e.step === "dims");
assert(dimsEvt && dimsEvt.dims.length === 2 && dimsEvt.dims.includes("math") && dimsEvt.dims.includes("writing"),
  "复合任务应判出 math+writing: " + JSON.stringify(dimsEvt && dimsEvt.dims));
fin = finalOf(evts);
assert((fin.decision_summary.dimensions || []).length === 2, "决策摘要应带双维度");

evts = await sse({ text: "识别这张图片里的集装箱箱号", policy_id: "policy-global-balanced" });
assert(evts.some(e => e.step === "rule" && /支持图像/.test(e.text)), "多模态应命中硬规则 vision 过滤");
fin = finalOf(evts);
assert((fin.decision_summary.dimensions || []).includes("multimodal"), "多模态维度应入决策");

evts = await sse({ text: "写一段产品介绍", policy_id: "policy-scene-quality", aggregate: "on" });
fin = finalOf(evts);
assert(fin.decision_summary.switch_result === "aggregated", "aggregate=on 应聚合: " + fin.decision_summary.switch_result);

// 5) benchmark 分数修正：校验、生效、联动、恢复
r = await post("/api/benchmark/score", { model_id: "swift-4b", dim: "nope", score: 50 });
assert(r.error, "未知维度应报错");
r = await post("/api/benchmark/score", { model_id: "swift-4b", dim: "math", score: 150 });
assert(r.error, "越界分数应报错");
r = await post("/api/benchmark/score", { model_id: "swift-4b", dim: "math", score: 90 });
assert(r.ok, "修正分数");
bm = await api("/api/benchmark");
assert(bm.scores["swift-4b"].math === 90 && bm.overrides["swift-4b"].math === 90, "修正应生效并标记 override");
prof = await api("/api/profile");
const mathRow = prof.clusters.find(c => c.domain === "math");
assert(mathRow.scores["swift-4b"].raw === 90 && mathRow.scores["swift-4b"].override === true, "效果表应即时反映修正");
r = await post("/api/benchmark/score/reset", { model_id: "swift-4b", dim: "math" });
bm = await api("/api/benchmark");
assert(bm.scores["swift-4b"].math === 31, "恢复榜单值应回 31，实际 " + bm.scores["swift-4b"].math);

// 6) 刷新榜单快照：日期更新、修正保留
await post("/api/benchmark/score", { model_id: "swift-4b", dim: "coding", score: 77 });
r = await post("/api/benchmark/refresh");
assert(r.ok && r.asof !== "2026-08", "刷新应更新快照日期");
bm = await api("/api/benchmark");
assert(bm.asof === r.asof && bm.scores["swift-4b"].coding === 77, "刷新后修正应保留");

// 7) 模型操作联动
await post("/v1/models/nova-x/set-default");
const ms = await api("/v1/models");
assert(ms.models.find(m => m.model_id === "nova-x").is_default === 1, "设默认兜底应生效");
await post("/v1/models/swift-4b/delete");
bm = await api("/api/benchmark");
assert(!bm.models.find(m => m.model_id === "swift-4b"), "删除模型应从成绩表消失");
prof = await api("/api/profile");
assert(!("swift-4b" in prof.clusters[0].scores), "效果表应随删除联动");

// 7.5) 重置策略 API Key：新 Key 生效且列表反映
const polsBefore = (await api("/v1/policies")).policies;
const oldKey = polsBefore.find(x => x.policy_id === "policy-global-balanced").api_key;
r = await post("/v1/policies/policy-global-balanced/reset-key");
assert(r.ok && r.api_key && r.api_key !== oldKey, "重置应返回不同的新 Key");
const polsAfter = (await api("/v1/policies")).policies;
assert(polsAfter.find(x => x.policy_id === "policy-global-balanced").api_key === r.api_key, "策略列表应反映新 Key");

// 8) 移除路由模型：恢复 no_router 兜底
r = await post("/api/settings/router-model", { model_id: "" });
assert(r.ok && r.router === null, "移除路由模型");
evts = await sse({ text: "起草一份复工通知", policy_id: "policy-global-balanced" });
assert(finalOf(evts).decision_summary.route_layer === "no_router", "移除后应回 no_router 兜底");

// 9) 智能交互：卡片上下线状态机（曾因 mock 无状态导致点「下线」界面无反应）
const cardsAll = (await api("/api/cards")).cards || [];
const pub = cardsAll.find(c => c.status === "published");
if (pub) {
  r = await post(`/api/cards/${pub.card_id}/transition`, { action: "offline" });
  assert(r.card && r.card.status === "offline", "下线应返回更新后的 card");
  const one = await api(`/api/cards/${pub.card_id}`);
  assert(one.card.status === "offline", "单卡详情应反映下线");
  const listAfter = (await api("/api/cards")).cards.find(c => c.card_id === pub.card_id);
  assert(listAfter.status === "offline", "列表应反映下线");
  r = await post(`/api/cards/${pub.card_id}/transition`, { action: "publish" });
  assert(r.card && r.card.status === "published", "重新上线应生效");
} else {
  assert(false, "快照中应有已上线卡片");
}

// 10) AI 改写触发条件：任意输入都应产出完整结构（曾是敷衍拼接 + 字段错位）
r = await post("/api/scenarios/rewrite-trigger", { description: "用户想催快递的时候" });
assert(/^当用户想催快递时触发本配置/.test(r.trigger_description), "改写应输出规范触发描述: " + r.trigger_description);
assert((r.trigger_examples || []).length === 3, "应生成 3 条示例问法");
r = await post("/api/scenarios/rewrite-trigger", { description: "s d f g" });
assert(r.trigger_description.includes("s d f g") && (r.trigger_examples || []).length === 3, "乱输入也应结构完整");

console.log(failures === 0 ? "MOCK SMOKE: ALL PASS" : `MOCK SMOKE: ${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
