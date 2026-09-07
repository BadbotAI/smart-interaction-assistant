// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  const genLocal = {};
  let genCleared = false; // 一键更新画像后：各策略路由效果回到未生成
  const deadCards = new Set(); // 静态站会话内删除/下线的配置
  const prodLocal = { created: [], updated: {}, deleted: new Set() }; // 会话内产品操作
  const flyLocal = { rate: null, evolution: null, extraFb: 0 }; // 数据飞轮会话内状态
  function dimOf(text) {
    if (/图片|图像|截图|照片|音频|语音|视频|看图|扫描/.test(text)) return "multimodal";
    if (/代码|SQL|函数|脚本|报错|bug|正则|接口/i.test(text)) return "coding";
    if (/计算|求解|概率|利率|百分|多少/.test(text)) return "math";
    if (/写一|起草|润色|文案|通知|周报|公文/.test(text)) return "writing";
    if (/你好|谢谢|在吗|你是谁|早上好/.test(text)) return "chat";
    return "qa";
  }
  function getMock(pn, full) {
    if (pn === "/api/products") {
      const base = JSON.parse(JSON.stringify(D[pn] || { products: [] }));
      base.products = (base.products || []).filter(p => !prodLocal.deleted.has(p.product_id))
        .map(p => ({ ...p, ...(prodLocal.updated[p.product_id] || {}) }))
        .concat(prodLocal.created);
      return base;
    }
    if (pn === "/api/cards" && deadCards.size) {
      const base = JSON.parse(JSON.stringify(D[pn] || { cards: [] }));
      base.cards = (base.cards || []).filter(c => !deadCards.has(c.card_id));
      return base;
    }
    if (pn === "/api/profile/gen-status") {
      const base = D["/api/profile/gen-status"] || { generated: {}, task: { status: "idle" } };
      return { generated: genCleared ? { ...genLocal } : { ...base.generated, ...genLocal }, task: { status: "idle" } };
    }
    if (pn === "/api/settings/ab-sampling") {
      const base = D[pn] || { rate: 0.2 };
      return { rate: flyLocal.rate != null ? flyLocal.rate : base.rate };
    }
    if (pn === "/api/flywheel") {
      const base = JSON.parse(JSON.stringify(D[pn] || { total: 62, last7d: 62, dimensions: {}, win_rates: [],
        sampling_rate: 0.2, evolution: null, min_required: 20 }));
      base.total += flyLocal.extraFb; base.last7d += flyLocal.extraFb;
      if (flyLocal.rate != null) base.sampling_rate = flyLocal.rate;
      if (flyLocal.evolution) base.evolution = flyLocal.evolution;
      base.evolve_task = { status: "idle" };
      return base;
    }
    if (pn === "/api/profile/evolve/status") {
      return { task: flyLocal.evolution ? { status: "completed", done: 1, total: 1, version: flyLocal.evolution.version } : { status: "idle" } };
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
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "按场景匹配推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") return { trigger_description: (body && body.text || "") + "（演示：静态站不做真实 AI 改写）", examples: [] };
    if (pn === "/api/cards") return { card: { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft", ...(body || {}) } };
    if (pn === "/v1/bank/import") return { imported: (body && body.items || []).length, skipped: 0 };
    if (pn === "/v1/bank/import/start") return { task: { status: "done", done: (body && body.items || []).length, total: (body && body.items || []).length, imported: (body && body.items || []).length, skipped: 0, invalid: 0 } };
    if (pn === "/v1/bank/staged/commit") return { committed: (body && body.query_ids || []).length };
    if (pn === "/v1/bank/staged/discard") return { discarded: (body && body.query_ids || []).length };
    if (pn === "/api/profile/rebuild") { if (body && body.policy_id) genLocal[body.policy_id] = Date.now() / 1000; return { task: { status: "completed", done: 1, total: 1 } }; }
    if (pn === "/v1/bank/question/delete" || pn === "/v1/bank/question/relabel") return { ok: true };
    if (pn === "/api/settings/ab-sampling") {
      const r = Number(body && body.rate);
      if (!(r >= 0 && r <= 1)) return { error: "采样率需在 0-1 之间" };
      flyLocal.rate = r;
      return { ok: true, rate: r };
    }
    if (pn === "/v1/feedback") {
      flyLocal.extraFb += 1;
      const base = D["/api/flywheel"] || { total: 62 };
      return { ok: true, flywheel_total: (base.total || 62) + flyLocal.extraFb };
    }
    if (pn === "/api/profile/evolve") {
      const base = D["/api/flywheel"] || {};
      const dims = base.dimensions || { qa: 13, math: 18, writing: 18, coding: 9, multimodal: 4 };
      const names = { qa: "通用问答", coding: "代码", math: "数学推理", writing: "长文写作", multimodal: "多模态理解" };
      const kw = { qa: ["保税仓", "电子提单", "信用证", "甩柜"], coding: ["SQL", "脚本", "正则", "报错"],
        math: ["利率", "配载", "毛利率", "折算"], writing: ["通知", "邮件", "总结", "涨价函"],
        multimodal: ["提单照片", "截图", "磅单", "货损对比"] };
      const rank = { qa: [["nova-x", 0.8], ["atlas-72b", 0.62], ["swift-4b", 0.44]],
        coding: [["atlas-72b", 1.0], ["sage-r1", 0.67], ["swift-4b", 0.2]],
        math: [["sage-r1", 0.71], ["nova-x", 0.58], ["harbor-13b", 0.4]],
        writing: [["atlas-72b", 1.0], ["lexi-34b", 0.71], ["nova-x", 0.5]],
        multimodal: [["nova-x", 0.75], ["atlas-72b", 0.5]] };
      const mnames = { "nova-x": "曜极 Nova-X", "atlas-72b": "衡岳 Atlas-72B", "sage-r1": "沉思 Sage-R1",
        "swift-4b": "迅答 Swift-4B", "harbor-13b": "港航 Harbor-13B", "lexi-34b": "法准 Lexi-34B" };
      const prevV = flyLocal.evolution ? flyLocal.evolution.version : 1;
      flyLocal.evolution = { version: prevV + 1, ts: Date.now() / 1000,
        clusters: Object.keys(dims).map(k => ({ key: k, name: names[k] || k, keywords: kw[k] || [],
          size: dims[k], sample: "", ranking: (rank[k] || []).map(([m, w]) => ({ model_id: m, name: mnames[m] || m, win_rate: w, n: dims[k] })) })) };
      genCleared = true; Object.keys(genLocal).forEach(k => delete genLocal[k]);
      return { task: { status: "running", done: 0, total: 62 } };
    }
    if (pn === "/api/products") {
      const name = (body && body.name || "").trim();
      if (!name || name.length > 15) return { error: "产品名称必填，1-15 字" };
      const pid = "prod-demo-" + Math.random().toString(36).slice(2, 8);
      prodLocal.created.push({ product_id: pid, name, brand_file: (body && body.brand_file) || "brand-tokens.default.json",
        card_ids: (body && body.card_ids) || [], created_at: Date.now() / 1000, mcp_key: "sk-mcp-demo" + Math.random().toString(36).slice(2, 10) });
      return { product_id: pid, mcp_key: prodLocal.created[prodLocal.created.length - 1].mcp_key };
    }
    if (/^\/api\/products\/[^/]+$/.test(pn)) {
      const pid = pn.split("/")[3];
      prodLocal.updated[pid] = { ...(prodLocal.updated[pid] || {}), ...(body || {}) };
      const c = prodLocal.created.find(p => p.product_id === pid);
      if (c) Object.assign(c, body || {});
      return { ok: true };
    }
    if (/^\/api\/products\/[^/]+\/delete$/.test(pn)) {
      const pid = pn.split("/")[3];
      prodLocal.deleted.add(pid);
      prodLocal.created = prodLocal.created.filter(p => p.product_id !== pid);
      return { ok: true };
    }
    if (pn === "/api/scenes/delete") return { ok: true, deleted: 0 };
    if (/^\/api\/cards\/[^/]+\/delete$/.test(pn)) { deadCards.add(pn.split("/")[3]); return { ok: true }; }
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
          content: "收到，已按你的选择继续跟进：" + ((body.card_context || {}).summary || "") + "",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            route_layer: "dimension", dimension: "qa",
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
    const text = (body && body.text) || "";
    const pid = body && body.policy_id;
    const pol = (((D["/v1/policies"] || {}).policies) || []).find(p => p.policy_id === pid);
    const polMeta = pol ? { policy_id: pol.policy_id, name: pol.name, latency_tier: pol.latency_tier,
      allow_aggregation: pol.allow_aggregation, K: (pol.params || {}).K || 3, alpha: (pol.params || {}).alpha ?? 0.7 }
      : { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", allow_aggregation: 1, K: 3, alpha: 0.7 };
    const dim = dimOf(text);
    const DIM_CN = { qa: "通用问答", coding: "代码", math: "数学推理", writing: "长文写作", multimodal: "多模态理解", chat: "日常闲聊" };
    // 第 1 层 · 硬规则：多模态直接按能力分流
    if (dim === "multimodal") {
      return sseStream([
        { step: "rule", text: "第 1 层 · 硬规则命中：多模态请求，仅在 2 个支持多模态的模型中路由" },
        { step: "coarse", text: "计算多模态模型在该维度的基准成绩",
          scores: { "nova-x": 0.92, "atlas-72b": 0.78 }, candidates: ["nova-x"] },
        { step: "fastlane", text: "曜极 Nova-X 显著领先，直接作答" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "已识别图像内容：箱号 TEMU1203987，箱体完好无明显破损。",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "nova-x", candidates: ["nova-x", "atlas-72b"],
            route_layer: "rule", dimension: "multimodal",
            total_cost: 0.0031, total_latency_ms: 900,
            model_calls: [{ model_id: "nova-x", tokens_in: 140, tokens_out: 120, tokens_thinking: 0, cost: 0.0031, latency_ms: 900 }],
            policy: polMeta },
          usage: { cost: 0.0031, tokens: 260 } },
      ], 400);
    }
    if (dim === "chat") {
      return sseStream([
        { step: "rule", text: "第 1 层 · 硬规则命中：日常闲聊，轻量直答" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "你好，我是本平台的智能助手，可以协助你做分析、写作、代码等多类问题。",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            route_layer: "rule", dimension: "chat",
            total_cost: 0.0001, total_latency_ms: 320,
            model_calls: [{ model_id: "swift-4b", tokens_in: 30, tokens_out: 60, tokens_thinking: 0, cost: 0.0001, latency_ms: 320 }],
            policy: polMeta },
          usage: { cost: 0.0001, tokens: 90 } },
      ], 400);
    }
    if (pol && !pol.allow_aggregation) {
      return sseStream([
        { step: "support", text: `第 2 层 · 维度匹配：判定为「${DIM_CN[dim] || dim}」，命中 42 条相似基准题` },
        { step: "coarse", text: "计算各模型在该维度的基准成绩",
          scores: { "swift-4b": 0.71, "harbor-13b": 0.62, "atlas-72b": 0.58, "sage-r1": 0.44 },
          candidates: ["swift-4b"] },
        { step: "fastlane", text: "策略仅单模型：最高分 迅答 Swift-4B 直接作答" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "结论先行：整体趋势上行，建议优先关注供给端节奏，必要时再拆分区域看结构差异。",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            route_layer: "dimension", dimension: dim,
            total_cost: 0.0002, total_latency_ms: 410,
            model_calls: [{ model_id: "swift-4b", tokens_in: 120, tokens_out: 190, tokens_thinking: 0, cost: 0.0002, latency_ms: 410 }],
            policy: polMeta },
          usage: { cost: 0.0002, tokens: 310 } },
      ], 400);
    }
    return sseRouteDemoAgg(polMeta, dim, DIM_CN[dim] || dim);
  }

  function sseRouteDemoAgg(polMeta, dim, dimName) {
    const steps = [
      { step: "support", text: `第 2 层 · 维度匹配：判定为「${dimName || "通用问答"}」，命中 42 条相似基准题` },
      { step: "coarse", text: "计算各模型在该维度的基准成绩",
        scores: { "sage-r1": 0.72, "nova-x": 0.70, "atlas-72b": 0.69, "swift-4b": 0.68, "harbor-13b": 0.56 },
        candidates: ["sage-r1", "nova-x", "atlas-72b"] },
      { step: "parallel", text: "3 路候选模型并发作答" },
      { step: "switch", text: "细排：两份回答得分接近，保留 2 份交给聚合模型 沉思 Sage-R1 总结定稿" },
      { step: "final", trace_id: "demo-trace", turn_id: "demo-turn",
        content: "近八周价格整体呈上行趋势，最新值较期初上涨约 22%。建议关注供需两端的边际变化与港口库存去化速度。",
        components: [{ schema_version: "1.0.0", render_id: "demo-pref", component_type: "feedback.preference",
          semantic_category: "evaluate", trigger_source: "system_injected", card_ref: null,
          params: { candidates: [
            { model_id: "sage-r1", alias: "候选1", content: "近八周价格上行，涨幅 22%，动力来自供给收缩。" },
            { model_id: "nova-x", alias: "候选2", content: "价格中枢上移，建议关注库存与需求端边际变化。" }] } }],
        decision_summary: { mode: "auto", switch_result: "aggregated", final_model: "sage-r1",
          candidates: ["sage-r1", "nova-x", "atlas-72b"], aggregator: "sage-r1", is_explore: false,
          route_layer: "dimension", dimension: dim || "qa",
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
