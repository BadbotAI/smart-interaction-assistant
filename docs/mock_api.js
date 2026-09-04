// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  const genLocal = {};
  let judgeLocal = null; // null=跟随快照; {removed:true} 或 {judge:{...}}
  function getMock(pn, full) {
    if (pn === "/api/settings/judge-model") {
      if (judgeLocal) return { judge: judgeLocal.removed ? null : judgeLocal.judge };
      return D[pn] !== undefined ? D[pn] : { judge: null };
    }
    if (pn === "/v1/bank/scenes" && judgeLocal) {
      const base = JSON.parse(JSON.stringify(D[pn] || { scenes: [], custom_scenes: [] }));
      base.judge_model = judgeLocal.removed ? null : judgeLocal.judge.model_id;
      base.judge_name = judgeLocal.removed ? null : (judgeLocal.judge.display_name || judgeLocal.judge.model_id);
      return base;
    }
    if (pn === "/api/profile/gen-status") {
      const base = D["/api/profile/gen-status"] || { generated: {}, task: { status: "idle" } };
      return { generated: { ...base.generated, ...genLocal }, task: { status: "idle" } };
    }
    if (pn === "/v1/bank/questions") {
      const sc = new URLSearchParams((full || "").split("?")[1] || "").get("scene") || "general";
      return D["bankq:" + sc] || { questions: [], scene: sc };
    }
    if (D[pn] !== undefined) return D[pn];
    const m = pn.match(/^\/api\/cards\/([^/]+)$/);
    if (m && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === m[1]);
      if (card) return { card };
    }
    if (pn.startsWith("/api/dashboard/questions")) return D["/api/dashboard/questions"];
    if (pn.startsWith("/api/dashboard/insights")) return D["/api/dashboard/insights"];
    if (pn.startsWith("/api/dashboard/overview")) return D["/api/dashboard/overview"];
    if (pn.startsWith("/api/profile")) return D["/api/profile"];
    const em = pn.match(/^\/v1\/embed\/envelope\/([^/]+)$/);
    if (em && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === em[1])
        || D["/api/cards"].cards.find(c => c.status === "published");
      if (card) {
        const cfg = (card.field_bindings || {}).config || {};
        return { envelope: { schema_version: "1.0.0", render_id: "emb-" + Math.random().toString(36).slice(2, 8),
          component_type: card.component_type, semantic_category: "collect", trigger_source: "sdk_embed",
          card_ref: { card_id: card.card_id, version: card.version },
          params: { prompt: (card.text_templates || {}).prompt || card.name,
            reply_text: (card.text_templates || {}).reply || "",
            submit_label: (card.text_templates || {}).submit || "提交",
            options: cfg.options || [], option_meta: cfg.option_meta || {}, option_actions: cfg.option_actions || {},
            display: cfg.display || "", recommended_default: cfg.recommended_default || null,
            fields: cfg.fields || [], likert: cfg.likert || null, slider: cfg.slider || null,
            dimensions: cfg.dimensions || [], values: cfg.values || null, placeholder: cfg.placeholder || "",
            echo_results: false } },
          card: { card_id: card.card_id, name: card.name, version: card.version } };
      }
    }
    return {};
  }

  function postMock(pn, body) {
    if (pn === "/api/apikeys") {
      const name = (body && body.name || "").trim();
      if (!name || name.length > 15) return { error: "名称必填，1-15 字" };
      return { key_id: Math.random().toString(36).slice(2, 10), name,
        secret: "sk-live-" + Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b2 => b2.toString(16).padStart(2, "0")).join("") };
    }
    if (/^\/api\/apikeys\/[^/]+\/delete$/.test(pn)) return { ok: true };
    if (pn === "/v1/events") return { accepted: (body && body.events || []).length || 1 };
    if (pn.endsWith("/transition")) return { ok: true, demo: true };
    if (pn === "/api/templates/suggest") {
      const t = (D["/api/templates"] || { templates: [] }).templates.slice(0, 3);
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "静态演示推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") return { trigger_description: (body && body.text || "") + "（演示：静态站不做真实 AI 改写）", examples: [] };
    if (pn === "/api/cards") return { card: { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft", ...(body || {}) } };
    if (pn === "/v1/bank/import") return { imported: (body && body.items || []).length, skipped: 0 };
    if (pn === "/v1/bank/import/start") return { task: { status: "done", done: (body && body.items || []).length, total: (body && body.items || []).length, imported: (body && body.items || []).length, skipped: 0, invalid: 0 } };
    if (pn === "/v1/bank/staged/commit") return { committed: (body && body.query_ids || []).length };
    if (pn === "/v1/bank/staged/discard") return { discarded: (body && body.query_ids || []).length };
    if (pn === "/api/profile/rebuild") { if (body && body.policy_id) genLocal[body.policy_id] = Date.now() / 1000; return { task: { status: "completed", done: 1, total: 1 } }; }
    if (pn === "/v1/bank/question/delete" || pn === "/v1/bank/question/relabel") return { ok: true };
    if (pn === "/api/settings/judge-model") {
      const mid = (body && body.model_id || "").trim();
      if (!mid) { judgeLocal = { removed: true }; return { ok: true, judge: null }; }
      if (!(body && body.display_name)) return { error: "请填写显示名" };
      judgeLocal = { judge: { model_id: mid, display_name: body.display_name } };
      return { ok: true, judge: judgeLocal.judge };
    }
    if (pn === "/api/products") {
      const name = (body && body.name || "").trim();
      if (!name || name.length > 15) return { error: "产品名称必填，1-15 字" };
      const pid = "prod-demo-" + Math.random().toString(36).slice(2, 8);
      return { product_id: pid, mcp_key: "sk-mcp-demo" + Math.random().toString(36).slice(2, 6) };
    }
    if (/^\/api\/products\/[^/]+\/delete$/.test(pn)) return { ok: true };
    if (pn === "/api/scenes/delete") return { ok: true, deleted: 0 };
    if (pn === "/v1/policies") return { policy_id: "policy-demo-" + Math.random().toString(36).slice(2, 8), api_key: "sk-route-demo0000" };
    if (/^\/v1\/policies\/[^/]+\/duplicate$/.test(pn)) return { policy_id: "policy-demo-" + Math.random().toString(36).slice(2, 8), name: "策略 副本" };
    return { ok: true, demo: true };
  }

  function makeEnvelope(card, source) {
    const cfg = (card.field_bindings || {}).config || {};
    return { schema_version: "1.0.0", render_id: "mk-" + Math.random().toString(36).slice(2, 8),
      component_type: card.component_type, semantic_category: "collect", trigger_source: source || "model_tool_call",
      card_ref: { card_id: card.card_id, version: card.version },
      params: { prompt: (card.text_templates || {}).prompt || card.name,
        reply_text: (card.text_templates || {}).reply || "",
        submit_label: (card.text_templates || {}).submit || "提交",
        options: cfg.options || [], option_meta: cfg.option_meta || {}, option_actions: cfg.option_actions || {},
        display: cfg.display || "", recommended_default: cfg.recommended_default || null,
        fields: cfg.fields || [], likert: cfg.likert || null, slider: cfg.slider || null,
        dimensions: cfg.dimensions || [], values: cfg.values || null, placeholder: cfg.placeholder || "",
        echo_results: false } };
  }

  function matchCard(text) {
    const cards = ((D["/api/cards"] || {}).cards || []).filter(c =>
      (c.status === "published" || (c.status === "draft" && c.version >= 1)) &&
      ["collect", "control"].includes(c.semantic_category));
    let best = null;
    for (const c of cards) {
      for (const t of [c.trigger_description || "", ...(c.trigger_examples || [])]) {
        if (!t) continue;
        if (t === text || (t.length >= 5 && (text.includes(t) || t.includes(text)))) { best = c; break; }
      }
      if (best) break;
    }
    return best;
  }

  function sseStream(steps, gap) {
    const enc = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        let i = 0;
        const t = setInterval(() => {
          if (i >= steps.length) { clearInterval(t); c.close(); return; }
          c.enqueue(enc.encode("data:" + JSON.stringify(steps[i++]) + "\n\n"));
        }, gap || 420);
      },
    });
    return new Response(stream, { status: 200 });
  }

  function sseRoute(body) {
    const text = (body && body.text) || "";
    // 组件跟进：用户在组件上提交后的续轮
    if (body && body.card_context) {
      return sseStream([
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "收到，已按你的选择继续跟进：" + ((body.card_context || {}).summary || "") + "（静态演示：正式环境由模型接管后续对话）",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            total_cost: 0.0002, total_latency_ms: 380,
            policy: { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", K: 3 } },
          usage: { cost: 0.0002, tokens: 180 } },
      ], 300);
    }
    // 智能交互：命中触发条件 -> 返回组件信封
    if (!(body && body.skip_card_match)) {
      const hit = matchCard(text);
      if (hit) {
        return sseStream([
          { step: "match", text: `触发条件命中：「${hit.name}」` },
          { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
            content: "", ask_card: makeEnvelope(hit),
            decision_summary: { mode: "auto", switch_result: "await_user", final_model: null, candidates: [],
              total_cost: 0, total_latency_ms: 120,
              policy: { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", K: 3 } },
            usage: { cost: 0, tokens: 0 } },
        ], 350);
      }
    }
    return sseRouteDemo(body);
  }

  function sseRouteDemo(body) {
    const pid = body && body.policy_id;
    const pol = (((D["/v1/policies"] || {}).policies) || []).find(p => p.policy_id === pid);
    const polMeta = pol ? { policy_id: pol.policy_id, name: pol.name, latency_tier: pol.latency_tier,
      allow_aggregation: pol.allow_aggregation, K: (pol.params || {}).K || 3, alpha: (pol.params || {}).alpha ?? 0.7 } 
      : { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", allow_aggregation: 1, K: 3, alpha: 0.7 };
    if (pid === "policy-global-fallback") {
      return sseStream([
        { step: "fallback", text: "默认兜底档：直接调用兜底模型 衡岳 Atlas-72B" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "这批货物当前在宁波舟山港中转，预计后天完成清关。（静态演示：回答为预置数据）",
          decision_summary: { mode: "auto", switch_result: "fallback", final_model: "atlas-72b", candidates: ["atlas-72b"],
            total_cost: 0.0004, total_latency_ms: 620,
            model_calls: [{ model_id: "atlas-72b", tokens_in: 120, tokens_out: 210, tokens_thinking: 0, cost: 0.0004, latency_ms: 620 }],
            policy: polMeta },
          usage: { cost: 0.0004, tokens: 330 } },
      ], 400);
    }
    if (pol && !pol.allow_aggregation) {
      return sseStream([
        { step: "support", text: "检索相似历史问题：命中 42 条支撑样本" },
        { step: "coarse", text: "计算各模型在该场景的历史命中率",
          scores: { "swift-4b": 0.71, "harbor-13b": 0.62, "atlas-72b": 0.58, "sage-r1": 0.44 },
          candidates: ["swift-4b"] },
        { step: "fastlane", text: "策略仅单模型：最高分 迅答 Swift-4B 直接作答" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "近八周价格整体上行，重点关注供给端节奏。（静态演示：回答与打分为预置数据）",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            total_cost: 0.0002, total_latency_ms: 410,
            model_calls: [{ model_id: "swift-4b", tokens_in: 120, tokens_out: 190, tokens_thinking: 0, cost: 0.0002, latency_ms: 410 }],
            policy: polMeta },
          usage: { cost: 0.0002, tokens: 310 } },
      ], 400);
    }
    return sseRouteDemoAgg(polMeta);
  }

  function sseRouteDemoAgg(polMeta) {
    const steps = [
      { step: "support", text: "检索相似历史问题：命中 42 条支撑样本" },
      { step: "coarse", text: "计算各模型在该场景的历史命中率",
        scores: { "sage-r1": 0.72, "nova-x": 0.70, "atlas-72b": 0.69, "swift-4b": 0.68, "harbor-13b": 0.56 },
        candidates: ["sage-r1", "nova-x", "atlas-72b"] },
      { step: "parallel", text: "3 路候选模型并发作答" },
      { step: "switch", text: "细排：两份回答得分接近，保留 2 份交给聚合器 沉思 Sage-R1 融合重写" },
      { step: "final", trace_id: "demo-trace", turn_id: "demo-turn",
        content: "近八周价格整体呈上行趋势，最新值较期初上涨约 22%。建议关注供需两端的边际变化与港口库存去化速度。（静态演示：回答与打分为预置数据，完整能力请本地运行仓库）",
        components: [{ schema_version: "1.0.0", render_id: "demo-pref", component_type: "feedback.preference",
          semantic_category: "evaluate", trigger_source: "system_injected", card_ref: null,
          params: { candidates: [
            { model_id: "sage-r1", alias: "候选1", content: "近八周价格上行，涨幅 22%，动力来自供给收缩。" },
            { model_id: "nova-x", alias: "候选2", content: "价格中枢上移，建议关注库存与需求端边际变化。" }] } }],
        decision_summary: { mode: "auto", switch_result: "aggregated", final_model: "sage-r1",
          candidates: ["sage-r1", "nova-x", "atlas-72b"], aggregator: "sage-r1", is_explore: false,
          total_cost: 0.0083, total_latency_ms: 1240,
          model_calls: [
            { model_id: "sage-r1", tokens_in: 120, tokens_out: 260, tokens_thinking: 80, cost: 0.0041, latency_ms: 980 },
            { model_id: "nova-x", tokens_in: 120, tokens_out: 210, tokens_thinking: 0, cost: 0.0035, latency_ms: 860 },
            { model_id: "atlas-72b", tokens_in: 120, tokens_out: 150, tokens_thinking: 0, cost: 0.0007, latency_ms: 640 }],
          policy: polMeta },
        usage: { cost: 0.0083, tokens: 1060 }, route_context: { policy_id: "policy-global-balanced" } },
    ];
    const enc = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        let i = 0;
        const t = setInterval(() => {
          if (i >= steps.length) { clearInterval(t); c.close(); return; }
          c.enqueue(enc.encode("data:" + JSON.stringify(steps[i++]) + "\n\n"));
        }, 420);
      },
    });
    return new Response(stream, { status: 200 });
  }

  window.fetch = function (url, opts = {}) {
    let u = String(url);
    // 任意形式（完整 URL / 相对路径）归一化成 /api 或 /v1 开头的路径
    try {
      const parsed = new URL(u, location.href);
      if (parsed.origin === location.origin) {
        const i = parsed.pathname.search(/\/(api|v1)\//);
        if (i >= 0) u = parsed.pathname.slice(i) + parsed.search;
      }
    } catch (e) {}
    const isApi = u.startsWith("/api") || u.startsWith("/v1");
    if (!isApi) return realFetch(url, opts);
    const method = (opts.method || "GET").toUpperCase();
    const pn = u.split("?")[0];
    let body = null;
    if (opts.body) { try { body = JSON.parse(opts.body); } catch (e) {} }
    if (pn === "/v1/route") return Promise.resolve(sseRoute(body));
    if (method === "GET") return Promise.resolve(json(getMock(pn, u)));
    return Promise.resolve(json(postMock(pn, body)));
  };
})();
