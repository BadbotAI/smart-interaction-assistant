// 静态站 mock 状态机冒烟：node tests/mock_smoke.mjs
// 驱动完整动线（配 Judge → 归类生成 → 生成画像 → 效果换算 → 模型操作），断言状态推进。
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

let failures = 0;
const assert = (cond, msg) => { if (!cond) { failures++; console.error("FAIL:", msg); } };

// 1) 冷启动初始态
let ov = await api("/api/dataset/overview");
assert(ov.active === 0, "初始应为未生成数据集");
assert(ov.pool_new >= ov.threshold, "问题池应达标");
assert(ov.judge === null, "初始无 Judge");
let prof = await api("/api/profile");
assert(prof.generated === false, "初始无画像");

// 2) 守卫：未配 Judge / 未归类时生成画像应拒
let r = await post("/api/profile/generate");
assert(r.error && r.error.includes("数据集"), "未归类生成画像应报数据集错误: " + JSON.stringify(r));

// 3) 配 Judge → 归类 → 生成画像
r = await post("/api/settings/judge-model", { model_id: "judge-72b", display_name: "Judge-72B" });
assert(r.ok && r.judge.model_id === "judge-72b", "配置 Judge");
r = await post("/api/dataset/cluster");
assert(r.task, "归类生成应返回任务");
ov = await api("/api/dataset/overview");
assert(ov.active === 1 && (ov.clusters || []).length === 6, "归类后 v1 生效且 6 个分类");
r = await post("/api/profile/generate");
assert(r.task, "生成画像应返回任务: " + JSON.stringify(r));
prof = await api("/api/profile?policy_id=policy-scene-fast");
assert(prof.generated === true && prof.clusters.length === 6, "画像生成后效果表 6 行");
assert(prof.alpha === 0.25, "省钱优先 α 应为 0.25，实际 " + prof.alpha);
const cell = prof.clusters[0].scores["swift-4b"];
assert(cell && typeof cell.judge === "number" && "adopt" in cell && "w_adopt" in cell, "分数格应含 Judge/采纳构成");
const mx = await api("/api/profile/matrix");
assert(mx.version === 1 && mx.clusters.length === 6, "原始矩阵可取");

// 4) 飞轮与版本联动
const fly = await api("/api/flywheel");
assert(fly.dataset_version === 1 && fly.pending === 0, "归类后回流应已归入版本");

// 5) 模型操作状态化
await post("/v1/models/nova-x/set-default");
const ms = await api("/v1/models");
const nova = ms.models.find(m => m.model_id === "nova-x");
assert(nova && nova.is_default === 1, "设默认兜底应生效");
await post("/v1/models/swift-4b/delete");
const ms2 = await api("/v1/models");
assert(!ms2.models.find(m => m.model_id === "swift-4b"), "删除模型应生效");
const prof2 = await api("/api/profile");
assert(!("swift-4b" in prof2.clusters[0].scores), "效果表应随删除联动");

// 6) 问题池删除
await post("/api/dataset/query/delete", { query_id: (ov.recent[0] || {}).query_id });
const ov2 = await api("/api/dataset/overview");
assert(ov2.recent.length === ov.recent.length - 1, "删除问题应从列表消失");

console.log(failures === 0 ? "MOCK SMOKE: ALL PASS" : `MOCK SMOKE: ${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
