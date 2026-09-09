// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  // v7 会话状态机：配置智能路由模型 → benchmark 得分表（点格修正）→ 判维路由（静态站可走完整动线）
  const v7 = { router: null, overrides: {}, asof: null, keys: {} };
  const deadCards = new Set(); // 静态站会话内删除的配置
  const cardStatus = {}; // 会话内上下线状态（否则点「下线」快照回读=界面无反应且无法编辑）
  const prodLocal = { created: [], updated: {}, deleted: new Set() }; // 会话内产品操作
  // 模型操作会话内状态：设默认兜底 / 启停 / 思考开关 / 编辑 / 删除（否则快照回读=界面无反应）
  const modelLocal = { defaultId: null, status: {}, thinking: {}, updated: {}, deleted: new Set() };
  // 判维演示实现：与服务端 classify_bench_dims 同规则（关键词判定，最多 2 维，multimodal 优先）
  const DIM_KEYWORDS = {
    math: ["计算", "利息", "毛利", "百分", "求解", "多少", "利率", "环比", "同比", "配载", "折算"],
    coding: ["SQL", "sql", "代码", "脚本", "接口", "报错", "正则", "函数", "调试", "同步失败"],
    writing: ["写一", "起草", "润色", "通知", "邮件", "总结", "函", "纪要", "汇报", "文案"],
    chinese: ["翻译", "成语", "文言", "改写", "理解这段", "润色这段", "什么意思"],
    instruct: ["按格式", "列表输出", "JSON", "表格输出", "分步骤", "字数", "按模板", "逐条"],
    multimodal: ["图片", "图像", "截图", "照片", "识别图", "看图", "音频", "语音", "视频", "扫描件"],
    knowledge: ["是什么", "为什么", "区别", "解释", "介绍", "怎么看", "要点", "要求", "流程"],
  };
  const CHAT_WORDS = ["你好", "谢谢", "在吗", "你是谁", "早上好"];
  function classifyDims(text) {
    const hits = [];
    Object.keys(DIM_KEYWORDS).forEach(d => {
      const s = DIM_KEYWORDS[d].reduce((n, w) => n + (text.includes(w) ? 1 : 0), 0);
      if (s) hits.push([s, d]);
    });
    hits.sort((a, b) => b[0] - a[0]);
    let dims = hits.slice(0, 2).map(h => h[1]);
    if (hits.some(h => h[1] === "multimodal") && !dims.includes("multimodal")) dims = ["multimodal", ...dims.slice(0, 1)];
    return dims.length ? dims : ["knowledge"];
  }
  const DIM_CN = { knowledge: "通用知识", math: "数学推理", coding: "代码生成", writing: "长文写作",
    instruct: "指令遵循", chinese: "中文理解", multimodal: "多模态理解" };
  function benchState() {
    const base = JSON.parse(JSON.stringify(D["/api/benchmark"] || { dims: [], scores: {}, models: [], overrides: {},
      asof: "2026-08", source: "公开榜单汇总" }));
    Object.keys(v7.overrides).forEach(mid => {
      base.scores[mid] = { ...(base.scores[mid] || {}) };
      base.overrides[mid] = { ...(base.overrides[mid] || {}) };
      Object.keys(v7.overrides[mid]).forEach(d => {
        if (v7.overrides[mid][d] === "__reset__") { delete base.overrides[mid][d]; return; }
        base.scores[mid][d] = v7.overrides[mid][d];
        base.overrides[mid][d] = v7.overrides[mid][d];
      });
    });
    base.models = (base.models || []).filter(m => !modelLocal.deleted.has(m.model_id))
      .map(m => ({ ...m, ...(modelLocal.updated[m.model_id] || {}) }));
    if (v7.asof) base.asof = v7.asof;
    base.router = v7.router;
    return base;
  }
  function getMock(pn, full) {
    if (pn === "/api/products") {
      const base = JSON.parse(JSON.stringify(D[pn] || { products: [] }));
      base.products = (base.products || []).filter(p => !prodLocal.deleted.has(p.product_id))
        .map(p => ({ ...p, ...(prodLocal.updated[p.product_id] || {}) }))
        .concat(prodLocal.created);
      return base;
    }
    if (pn === "/api/cards") {
      const base = JSON.parse(JSON.stringify(D[pn] || { cards: [] }));
      base.cards = (base.cards || []).filter(c => !deadCards.has(c.card_id))
        .map(c => cardStatus[c.card_id] ? { ...c, status: cardStatus[c.card_id] } : c);
      return base;
    }
    if (pn === "/api/benchmark") {
      return benchState();
    }
    if (pn === "/api/settings/router-model") {
      return { router: v7.router };
    }
    if (pn === "/api/profile") {
      const bm = benchState();
      const models = bm.models.filter(m => modelLocal.status[m.model_id] !== "disabled");
      const names = {};
      models.forEach(m => { names[m.model_id] = m.display_name; });
      const q2 = new URLSearchParams((full || "").split("?")[1] || "");
      const pid = q2.get("policy_id");
      const pol = (((D["/v1/policies"] || {}).policies) || []).find(p => p.policy_id === pid);
      const alpha = pol ? ((pol.params || {}).alpha ?? 0.7) : 0.7;
      const inv = {}; let lo = Infinity, hi = -Infinity;
      models.forEach(m => { const v = 1 / Math.max(0.01, (m.price_input || 0) + (m.price_output || 0));
        inv[m.model_id] = v; lo = Math.min(lo, v); hi = Math.max(hi, v); });
      const eff = {}; Object.keys(inv).forEach(mid => { eff[mid] = hi > lo ? Math.round((inv[mid] - lo) / (hi - lo) * 1000) / 1000 : 0.5; });
      const clusters = (bm.dims || []).map(d => {
        const scores = {};
        models.forEach(m => {
          const raw = (bm.scores[m.model_id] || {})[d.key];
          const perf = raw == null ? null : Math.round(raw / 100 * 1000) / 1000;
          const combined = perf == null ? null : Math.round((alpha * perf + (1 - alpha) * eff[m.model_id]) * 1000) / 1000;
          scores[m.model_id] = { perf, eff: eff[m.model_id], combined, raw,
            override: d.key in ((bm.overrides || {})[m.model_id] || {}) };
        });
        const valid = Object.entries(scores).filter(([, s]) => s.combined != null);
        valid.sort((a, b) => b[1].combined - a[1].combined);
        const best = valid.length ? valid[0][0] : null;
        const t = pol ? ((pol.params || {}).t ?? 0.8) : 0.8;
        const aggWith = (pol ? pol.allow_aggregation : 1) && valid.length >= 2 &&
          valid[0][1].combined - valid[1][1].combined < Math.max(0.02, (1 - t) * 0.3) ? valid[1][0] : null;
        return { domain: d.key, label: d.label, bench: d.bench, scores, best, agg_with: aggWith };
      });
      return { generated: true, asof: bm.asof, alpha, clusters, models: names, router: v7.router };
    }
    if (pn === "/v1/models") {
      const base = JSON.parse(JSON.stringify(D[pn] || { models: [] }));
      base.models = (base.models || []).filter(m => !modelLocal.deleted.has(m.model_id)).map(m => {
        const out = { ...m, ...(modelLocal.updated[m.model_id] || {}) };
        if (modelLocal.status[m.model_id]) out.status = modelLocal.status[m.model_id];
        if (modelLocal.defaultId) out.is_default = m.model_id === modelLocal.defaultId ? 1 : 0;
        if (modelLocal.thinking[m.model_id] !== undefined) {
          out.capabilities = { ...(out.capabilities || {}), thinking_enabled: modelLocal.thinking[m.model_id] };
        }
        return out;
      });
      return base;
    }
    if (pn === "/v1/policies") {
      const base = JSON.parse(JSON.stringify(D[pn] || { policies: [] }));
      (base.policies || []).forEach(p => { if (v7.keys[p.policy_id]) p.api_key = v7.keys[p.policy_id]; });
      return base;
    }
    if (D[pn] !== undefined) return D[pn];
    const m = pn.match(/^\/api\/cards\/([^/]+)$/);
    if (m && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === m[1]);
      if (card) return { card: cardStatus[card.card_id] ? { ...card, status: cardStatus[card.card_id] } : card };
    }
    if (pn.startsWith("/api/dashboard/questions")) return D["/api/dashboard/questions"];
    if (pn.startsWith("/api/dashboard/insights")) return D["/api/dashboard/insights"];
    if (pn.startsWith("/api/dashboard/overview")) return D["/api/dashboard/overview"];
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
    if (pn.endsWith("/transition")) {
      const cid = pn.split("/")[3];
      const action = body && body.action;
      if (action === "publish") cardStatus[cid] = "published";
      else if (action === "offline") cardStatus[cid] = "offline";
      const base = ((D["/api/cards"] || {}).cards || []).find(c => c.card_id === cid);
      return { ok: true, card: base ? { ...base, status: cardStatus[cid] || base.status } : null };
    }
    if (pn === "/api/templates/suggest") {
      const t = (D["/api/templates"] || { templates: [] }).templates.slice(0, 3);
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "按场景匹配推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") return { trigger_description: (body && body.text || "") + "（演示：静态站不做真实 AI 改写）", examples: [] };
    if (pn === "/api/cards") return { card: { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft", ...(body || {}) } };
    if (pn === "/api/settings/router-model") {
      const mid = (body && body.model_id || "").trim();
      if (!mid) { v7.router = null; return { ok: true, router: null }; }
      if (!(body && body.display_name)) return { error: "请填写显示名" };
      if (!/^[a-z0-9][a-z0-9-]{1,23}$/.test(mid)) return { error: "模型 ID 需为 2-24 位小写字母、数字或短横线" };
      v7.router = { model_id: mid, display_name: body.display_name,
        endpoint: (body && body.endpoint) || "", credential_ref: (body && body.credential_ref) || "" };
      return { ok: true, router: v7.router };
    }
    if (pn === "/api/benchmark/score") {
      const mid = (body && body.model_id || "").trim(), dim = (body && body.dim || "").trim();
      const dims = ((D["/api/benchmark"] || {}).dims || []).map(d => d.key);
      if (!dims.includes(dim)) return { error: "未知维度" };
      let score = body ? body.score : undefined;
      if (score !== null) {
        score = Number(score);
        if (!Number.isFinite(score)) return { error: "得分需为 0-100 的数字，或 null 标记缺失" };
        if (score < 0 || score > 100) return { error: "得分需在 0-100 之间" };
      }
      v7.overrides[mid] = { ...(v7.overrides[mid] || {}) };
      v7.overrides[mid][dim] = score;
      return { ok: true };
    }
    if (pn === "/api/benchmark/score/reset") {
      const mid = (body && body.model_id || "").trim(), dim = (body && body.dim || "").trim();
      if (v7.overrides[mid]) v7.overrides[mid][dim] = "__reset__";
      const baseOv = ((D["/api/benchmark"] || {}).overrides || {})[mid] || {};
      if (dim in baseOv) { v7.overrides[mid] = { ...(v7.overrides[mid] || {}) }; v7.overrides[mid][dim] = "__reset__"; }
      else if (v7.overrides[mid]) delete v7.overrides[mid][dim];
      return { ok: true };
    }
    if (pn === "/api/benchmark/refresh") {
      const d2 = new Date();
      v7.asof = d2.getFullYear() + "-" + String(d2.getMonth() + 1).padStart(2, "0");
      return { ok: true, asof: v7.asof };
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
    if (/^\/api\/cards\/[^/]+\/delete$/.test(pn)) { deadCards.add(pn.split("/")[3]); return { ok: true }; }
    if (pn === "/v1/policies") return { policy_id: "policy-demo-" + Math.random().toString(36).slice(2, 8), api_key: "sk-route-demo0000" };
    if (/^\/v1\/policies\/[^/]+\/reset-key$/.test(pn)) {
      const pid = pn.split("/")[3];
      const nk = "sk-route-" + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 8);
      v7.keys[pid] = nk;
      return { ok: true, api_key: nk };
    }
    if (/^\/v1\/policies\/[^/]+\/duplicate$/.test(pn)) return { policy_id: "policy-demo-" + Math.random().toString(36).slice(2, 8), name: "策略 副本" };
    if (/^\/v1\/policies\/[^/]+$/.test(pn)) return { ok: true, version: 2 };
    {
      const mm2 = pn.match(/^\/v1\/models\/([^/]+)\/(set-default|status|thinking|update|profile-data|delete)$/);
      if (mm2) {
        const mid = mm2[1], act = mm2[2];
        if (act === "set-default") { modelLocal.defaultId = mid; return { ok: true }; }
        if (act === "status") { modelLocal.status[mid] = (body && body.status) || "active"; return { ok: true }; }
        if (act === "thinking") { modelLocal.thinking[mid] = !(body && body.enabled === false); return { ok: true }; }
        if (act === "delete") { modelLocal.deleted.add(mid); return { ok: true }; }
        if (act === "update") {
          const patch = {};
          if (body && body.display_name) patch.display_name = body.display_name;
          if (body && body.provider) patch.provider = body.provider;
          modelLocal.updated[mid] = { ...(modelLocal.updated[mid] || {}), ...patch };
          return { ok: true };
        }
        if (act === "profile-data") {
          if (body) {
            const bad = ["price_input", "price_output"].some(k => body[k] != null && !(Number(body[k]) >= 0 && Number(body[k]) <= 10000));
            if (bad) return { error: "单价需在 0 ~ 10000 之间" };
          }
          const u = modelLocal.updated[mid] = { ...(modelLocal.updated[mid] || {}) };
          if (body) {
            if (body.price_input != null) u.price_input = Number(body.price_input) || 0;
            if (body.price_output != null) u.price_output = Number(body.price_output) || 0;
            if (body.deploy_type) u.deploy_type = body.deploy_type;
            if (body.gpu_count != null) u.gpu_count = Number(body.gpu_count) || 0;
            if (u.deploy_type === "self_hosted" && !u.price_input && !u.price_output && u.gpu_count) {
              u.price_input = Math.round(0.15 * u.gpu_count * 100) / 100;
              u.price_output = Math.round(0.30 * u.gpu_count * 100) / 100;
            }
          }
          return { ok: true };
        }
      }
    }
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
    const cards = ((D["/api/cards"] || {}).cards || [])
      .map(c => cardStatus[c.card_id] ? { ...c, status: cardStatus[c.card_id] } : c)
      .filter(c =>
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
            route_layer: "dims", dimensions: ["knowledge"],
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

    // 硬规则第 1 层：闲聊不判维、不聚合，最便宜模型直答（未配置路由模型也可用，先于硬依赖检查）
    const otherHit = Object.keys(DIM_KEYWORDS).some(d => DIM_KEYWORDS[d].some(w => text.includes(w)));
    if (CHAT_WORDS.some(w => text.includes(w)) && !otherHit && text.length <= 12) {
      return sseStream([
        { step: "rule", text: "硬规则命中：日常闲聊，轻量模型 迅答 Swift-4B 直答（不判维、不聚合）" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "你好，我是本平台的智能助手，可以协助你做分析、写作、代码等多类问题。",
          decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
            route_layer: "rule", dimensions: [],
            total_cost: 0.0001, total_latency_ms: 320,
            model_calls: [{ model_id: "swift-4b", tokens_in: 30, tokens_out: 60, tokens_thinking: 0, cost: 0.0001, latency_ms: 320 }],
            policy: polMeta },
          usage: { cost: 0.0001, tokens: 90 } },
      ], 400);
    }

    // 硬依赖：未配置智能路由模型 → 直连兜底
    if (!v7.router) {
      return sseStream([
        { step: "rule", text: "未配置智能路由模型：无法判定问题相关维度，本次直连兜底 衡岳 Atlas-72B（请在「模型画像」页配置）" },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "（兜底直连示例回答）围绕这个问题，可以从现状、约束与可行动作三个层面展开分析。",
          decision_summary: { mode: "auto", switch_result: "fallback", final_model: "atlas-72b", candidates: ["atlas-72b"],
            route_layer: "no_router", dimensions: [], total_cost: 0.0005, total_latency_ms: 640,
            model_calls: [{ model_id: "atlas-72b", tokens_in: 90, tokens_out: 160, tokens_thinking: 0, cost: 0.0005, latency_ms: 640 }],
            policy: polMeta },
          usage: { cost: 0.0005, tokens: 250 } },
      ], 420);
    }

    const dims = classifyDims(text);
    const isMM = dims.includes("multimodal");
    const bm = benchState();
    let models = bm.models.filter(m => modelLocal.status[m.model_id] !== "disabled");
    const steps = [];
    if (isMM) {
      models = models.filter(m => (m.capabilities || {}).vision);
      steps.push({ step: "rule", text: `第 1 层 · 硬规则命中：多模态请求，仅在 ${models.length} 个支持图像的模型中路由` });
    }
    const rname = v7.router.display_name || v7.router.model_id;
    steps.push({ step: "dims", text: `智能路由模型 ${rname} 判定：相关维度「${dims.map(d => DIM_CN[d] || d).join("、")}」（180ms · ¥0.0001）`, dims });

    // 综合分：判定维度平均得分 × 权重 + 省钱分 ×（1 - 权重）
    const inv = {}; let lo = Infinity, hi = -Infinity;
    models.forEach(m => { const v = 1 / Math.max(0.01, (m.price_input || 0) + (m.price_output || 0));
      inv[m.model_id] = v; lo = Math.min(lo, v); hi = Math.max(hi, v); });
    const alpha = polMeta.alpha;
    const ranked = [];
    models.forEach(m => {
      const vals = dims.map(d => (bm.scores[m.model_id] || {})[d]).filter(v => v != null);
      if (!vals.length) return;
      const perf = vals.reduce((a, b) => a + b, 0) / vals.length / 100;
      const eff = hi > lo ? (inv[m.model_id] - lo) / (hi - lo) : 0.5;
      ranked.push([m.model_id, Math.round((alpha * perf + (1 - alpha) * eff) * 1000) / 1000, m.display_name]);
    });
    ranked.sort((a, b) => b[1] - a[1]);
    if (!ranked.length) {
      steps.push({ step: "rule", text: "候选模型在判定维度上均无得分，切兜底直连" });
      steps.push({ step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
        content: "（兜底直连示例回答）已按通用能力尽力作答。",
        decision_summary: { mode: "auto", switch_result: "fallback", final_model: "atlas-72b", candidates: ["atlas-72b"],
          route_layer: "else", dimensions: dims, total_cost: 0.0005, total_latency_ms: 640,
          model_calls: [{ model_id: "atlas-72b", tokens_in: 90, tokens_out: 140, tokens_thinking: 0, cost: 0.0005, latency_ms: 640 }],
          policy: polMeta },
        usage: { cost: 0.0005, tokens: 230 } });
      return sseStream(steps, 420);
    }
    const scoreMap = {};
    ranked.forEach(r => { scoreMap[r[0]] = r[1]; });
    steps.push({ step: "coarse", text: "取各模型在这些维度的平均分，融合成本得到综合分", scores: scoreMap });

    const aggReq = (body && body.aggregate) || "auto";
    const overrideDenied = aggReq === "on" && pol && ((pol.params || {}).allow_agg_override === 0);
    const t = pol ? ((pol.params || {}).t ?? 0.8) : 0.8;
    const gapClose = ranked.length >= 2 && ranked[0][1] - ranked[1][1] < Math.max(0.02, (1 - t) * 0.3);
    const canAgg = (pol ? pol.allow_aggregation : 1) && aggReq !== "off" && !overrideDenied;
    const forceAgg = aggReq === "on" && !overrideDenied && ranked.length >= 2;
    const doAgg = ranked.length >= 2 && canAgg && (gapClose || forceAgg);
    const top = ranked[0];

    if (!doAgg) {
      steps.push({ step: "fastlane", text: `${top[2]} 综合分领先，直接作答` });
      steps.push({ step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
        content: isMM ? "已识别图像内容：箱号 TEMU1203987，箱体完好无明显破损。"
          : "结论先行：整体趋势上行，建议优先关注供给端节奏，必要时再拆分区域看结构差异。",
        decision_summary: { mode: "auto", switch_result: "fastlane", final_model: top[0], candidates: [top[0]],
          route_layer: isMM ? "rule" : "dims", dimensions: dims,
          aggregate_override: aggReq !== "auto" ? aggReq : null, aggregate_override_denied: overrideDenied,
          total_cost: 0.0021, total_latency_ms: 780,
          model_calls: [{ model_id: top[0], tokens_in: 120, tokens_out: 190, tokens_thinking: 0, cost: 0.0021, latency_ms: 780 }],
          policy: polMeta },
        usage: { cost: 0.0021, tokens: 310 } });
      return sseStream(steps, 400);
    }
    const second = ranked[1];
    steps.push({ step: "calling", text: forceAgg && !gapClose ? "请求要求聚合：2 个候选并发作答" : "综合分接近：2 个候选并发作答",
      models: [{ id: top[0], name: top[2] }, { id: second[0], name: second[2] }] });
    steps.push({ step: "switch", text: `保留 2 份回答，交给聚合模型 ${top[2]} 总结定稿`, result: "aggregated" });
    steps.push({ step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
      content: "综合 2 个候选模型的回答并交叉验证：近八周价格整体呈上行趋势，建议关注供需两端的边际变化。",
      components: [{ schema_version: "1.0.0", render_id: "demo-pref", component_type: "feedback.preference",
        semantic_category: "evaluate", trigger_source: "system_injected", card_ref: null,
        params: { candidates: [
          { model_id: top[0], alias: "候选1", content: "近八周价格上行，涨幅 22%，动力来自供给收缩。" },
          { model_id: second[0], alias: "候选2", content: "价格中枢上移，建议关注库存与需求端边际变化。" }] } }],
      decision_summary: { mode: "auto", switch_result: "aggregated", final_model: top[0],
        candidates: [top[0], second[0]], aggregator: top[0],
        route_layer: isMM ? "rule" : "dims", dimensions: dims,
        aggregate_override: aggReq !== "auto" ? aggReq : null, aggregate_override_denied: false,
        total_cost: 0.0083, total_latency_ms: 1240,
        model_calls: [
          { model_id: top[0], tokens_in: 120, tokens_out: 260, tokens_thinking: 0, cost: 0.0047, latency_ms: 980 },
          { model_id: second[0], tokens_in: 120, tokens_out: 210, tokens_thinking: 0, cost: 0.0036, latency_ms: 860 }],
        policy: polMeta },
      usage: { cost: 0.0083, tokens: 1060 } });
    return sseStream(steps, 420);
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
