// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  // v6 会话状态机：配 Judge → 聚类定版 → 生成画像（静态站可走完整动线）
  const v6 = { judge: null, version: 0, generated: false, deletedQ: new Set() };
  const deadCards = new Set(); // 静态站会话内删除/下线的配置
  const prodLocal = { created: [], updated: {}, deleted: new Set() }; // 会话内产品操作
  const flyLocal = { rate: null, evolution: null, extraFb: 0, importedVersion: null, activeOverride: null }; // 数据飞轮会话内状态
  // 模型操作会话内状态：设默认兜底 / 启停 / 思考开关 / 编辑 / 删除（否则快照回读=界面无反应）
  const modelLocal = { defaultId: null, status: {}, thinking: {}, updated: {}, deleted: new Set() };
  function dimOf(text) {
    if (/图片|图像|截图|照片|音频|语音|视频|看图|扫描/.test(text)) return "multimodal";
    if (/你好|谢谢|在吗|你是谁|早上好/.test(text)) return "chat";
    if (/SQL|接口|报错|脚本|系统|同步/i.test(text)) return "tech";
    if (/毛利|报表|同比|环比|汇总|分析/.test(text)) return "analytics";
    if (/合同|条款|合规|关税|清关|资质/.test(text)) return "compliance";
    if (/写一|起草|润色|通知|邮件|总结/.test(text)) return "writing";
    if (/行情|运价|价格|指数|涨|跌/.test(text)) return "market";
    if (/货|物流|延误|取件|赔付|催/.test(text)) return "logistics";
    return "other";
  }
  const THEME_CN = { logistics: "物流服务与异常", market: "价格与行情", compliance: "合同与合规",
    analytics: "经营分析与报表", writing: "公文与写作", tech: "系统与技术", other: "其他 / 长尾",
    chat: "日常闲聊", multimodal: "多模态" };
  const CLUSTER_STUB = [
    { key: "tech", label: "系统与技术", summary: "系统对接、SQL 查询、接口报错等技术类问题", keywords: ["接口", "SQL", "报错", "系统"], size: 91 },
    { key: "writing", label: "公文与写作", summary: "通知、邮件、总结、函件等商务写作类需求", keywords: ["通知", "邮件", "总结", "函件"], size: 88 },
    { key: "analytics", label: "经营分析与报表", summary: "经营数据分析、报表汇总、利润核算类问题", keywords: ["报表", "毛利", "同比", "汇总"], size: 86 },
    { key: "compliance", label: "合同与合规", summary: "合同条款审查、出口合规、关税申报类问题", keywords: ["合同", "条款", "合规", "关税"], size: 82 },
    { key: "market", label: "价格与行情", summary: "运价与商品行情研判、价格指数走势类问题", keywords: ["运价", "行情", "涨跌", "指数"], size: 81 },
    { key: "logistics", label: "物流服务与异常", summary: "查件、延误、取件改约、破损赔付等售后服务类问题", keywords: ["物流跟踪", "延误", "取件", "赔付"], size: 80 },
  ];
  // 演示画像：judge 分 × 采纳融合后的效果分（每簇每模型）
  const MATRIX_STUB = {
    tech: { "sage-r1": [0.84, 0.67, 6], "nova-x": [0.80, null, 0], "atlas-72b": [0.66, 1.0, 2], "swift-4b": [0.36, 0.2, 1], "harbor-13b": [0.52, null, 0], "lexi-34b": [0.49, null, 0] },
    writing: { "lexi-34b": [0.86, 0.71, 7], "nova-x": [0.85, 0.5, 4], "atlas-72b": [0.72, 1.0, 3], "swift-4b": [0.44, null, 0], "harbor-13b": [0.58, null, 0], "sage-r1": [0.68, null, 0] },
    analytics: { "sage-r1": [0.9, 0.71, 7], "nova-x": [0.84, 0.58, 5], "atlas-72b": [0.74, null, 0], "swift-4b": [0.4, null, 0], "harbor-13b": [0.55, null, 0], "lexi-34b": [0.62, null, 0] },
    compliance: { "lexi-34b": [0.9, 0.6, 5], "nova-x": [0.85, null, 0], "atlas-72b": [0.72, 0.5, 2], "sage-r1": [0.8, null, 0], "swift-4b": [0.37, null, 0], "harbor-13b": [0.52, null, 0] },
    market: { "sage-r1": [0.88, 0.6, 5], "nova-x": [0.84, 0.5, 4], "atlas-72b": [0.75, null, 0], "swift-4b": [0.41, null, 0], "harbor-13b": [0.58, null, 0], "lexi-34b": [0.56, null, 0] },
    logistics: { "harbor-13b": [0.86, 0.62, 8], "atlas-72b": [0.74, 0.55, 4], "nova-x": [0.82, null, 0], "swift-4b": [0.55, 0.4, 2], "sage-r1": [0.66, null, 0], "lexi-34b": [0.6, null, 0] },
  };
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
    if (pn === "/api/dataset/overview") {
      const base = JSON.parse(JSON.stringify(D[pn] || { pool_total: 508, pool_new: 508, threshold: 500,
        recent: [], versions: [], active: 0, clusters: null, theme_names: THEME_CN,
        profile: { generated: false, estimate: { calls: 288, cost: 0.92 } }, judge: null }));
      base.judge = v6.judge;
      base.cluster_task = { status: "idle" }; base.pgen_task = { status: "idle" };
      base.recent = (base.recent || []).filter(r => !v6.deletedQ.has(r.query_id));
      if (v6.version) {
        base.active = 1;
        base.pool_new = 0;
        base.can_cluster = false;
        base.clusters = CLUSTER_STUB;
        base.versions = [{ version: 1, ts: Date.now() / 1000, note: `聚类定版：${base.pool_total} 条 query，6 个簇`,
          cold_count: base.pool_total, reflow_count: 62, active: 1 }];
        base.recent.forEach(r => { r.dataset_version = 1; });
        base.profile = { generated: v6.generated, ts: v6.generated ? Date.now() / 1000 : null,
          judge_name: v6.judge ? v6.judge.display_name : null, estimate: { calls: 288, cost: 0.92 } };
      } else {
        base.can_cluster = base.pool_new >= base.threshold;
      }
      return base;
    }
    if (pn === "/api/dataset/pool") {
      const ov = getMock("/api/dataset/overview");
      return { items: ov.recent || [], total: ov.pool_total || 0, limit: 50, offset: 0 };
    }
    if (pn === "/api/profile/matrix") {
      if (!v6.generated) return { error: "还没有生成画像" };
      const prof = getMock("/api/profile", "/api/profile");
      return { active_version: 1, version: 1, ts: Date.now() / 1000,
        judge_name: v6.judge ? v6.judge.display_name : "Judge", adopt_smooth_k: 20,
        clusters: (prof.clusters || []).map(c => ({ key: c.domain, label: c.label, size: c.queries,
          models: c.scores })) };
    }
    if (pn === "/api/dataset/versions") {
      const ov = getMock("/api/dataset/overview");
      return { versions: ov.versions, active: ov.active };
    }
    if (pn === "/api/dataset/cluster/status") {
      return { task: v6.version ? { status: "completed", done: 508, total: 508, version: 1 } : { status: "idle" } };
    }
    if (pn === "/api/profile/generate/status") {
      return { task: v6.generated ? { status: "completed", done: 288, total: 288, version: 1 } : { status: "idle" } };
    }
    if (pn === "/api/settings/judge-model") {
      return { judge: v6.judge };
    }
    if (pn === "/api/profile") {
      const models = (((D["/v1/models"] || {}).models) || []).filter(m => m.status === "active" && !modelLocal.deleted.has(m.model_id));
      const names = {}; const prices = {};
      models.forEach(m => { names[m.model_id] = m.display_name; prices[m.model_id] = (m.price_input || 0) + (m.price_output || 0); });
      if (!v6.generated) return { version: v6.version, generated: false, clusters: [], alpha: 0.7, models: names };
      const q2 = new URLSearchParams((full || "").split("?")[1] || "");
      const pid = q2.get("policy_id");
      const pol = (((D["/v1/policies"] || {}).policies) || []).find(p => p.policy_id === pid);
      const alpha = pol ? ((pol.params || {}).alpha ?? 0.7) : 0.7;
      const inv = {}; let lo = Infinity, hi = -Infinity;
      Object.keys(prices).forEach(mid => { inv[mid] = 1 / Math.max(0.01, prices[mid]); lo = Math.min(lo, inv[mid]); hi = Math.max(hi, inv[mid]); });
      const eff = {}; Object.keys(inv).forEach(mid => { eff[mid] = hi > lo ? Math.round((inv[mid] - lo) / (hi - lo) * 1000) / 1000 : 0.5; });
      const clusters = CLUSTER_STUB.map(c => {
        const scores = {};
        Object.keys(names).forEach(mid => {
          const cell = (MATRIX_STUB[c.key] || {})[mid];
          const judge = cell ? cell[0] : 0.5, adopt = cell ? cell[1] : null, nAb = cell ? cell[2] : 0;
          const w = Math.round(nAb / (nAb + 20) * 1000) / 1000;
          const perf = adopt == null ? judge : Math.round(((1 - w) * judge + w * adopt) * 1000) / 1000;
          const combined = Math.round((alpha * perf + (1 - alpha) * eff[mid]) * 1000) / 1000;
          scores[mid] = { perf, eff: eff[mid], combined, judge, n_judge: 8, adopt, n_ab: nAb, w_adopt: w };
        });
        const valid = Object.entries(scores).filter(([, s]) => s.combined != null);
        valid.sort((a, b) => b[1].combined - a[1].combined);
        const best = valid.length ? valid[0][0] : null;
        const aggWith = (pol ? pol.allow_aggregation : 1) && valid.length >= 2 &&
          valid[0][1].combined - valid[1][1].combined < 0.06 ? valid[1][0] : null;
        return { domain: c.key, label: c.label, queries: c.size, scores, best, agg_with: aggWith };
      });
      return { version: 1, generated: true, ts: Date.now() / 1000,
        judge_name: v6.judge ? v6.judge.display_name : "Judge", alpha, clusters, models: names };
    }
    if (pn === "/api/settings/ab-sampling") {
      const base = D[pn] || { rate: 0.2 };
      return { rate: flyLocal.rate != null ? flyLocal.rate : base.rate };
    }
    if (pn === "/api/flywheel") {
      const base = JSON.parse(JSON.stringify(D[pn] || { total: 62, last7d: 62, dimensions: {}, win_rates: [],
        sampling_rate: 0.2, min_required: 20, pending: 62, imported: 0, dataset_version: 0, recent: [] }));
      base.total += flyLocal.extraFb; base.last7d += flyLocal.extraFb;
      if (flyLocal.rate != null) base.sampling_rate = flyLocal.rate;
      base.dataset_version = v6.version;
      if (v6.version) {
        base.pending = flyLocal.extraFb; base.imported = base.total - flyLocal.extraFb;
        (base.recent || []).forEach(r => { r.imported_version = 1; });
      } else { base.pending = base.total; base.imported = 0; }
      return base;
    }
    if (pn === "/v1/feedback/pending") {
      const base = D["/api/flywheel"] || { total: 62 };
      const n = v6.version ? flyLocal.extraFb : (base.total || 62) + flyLocal.extraFb;
      return { pending: [], total_pending: n };
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
    if (D[pn] !== undefined) return D[pn];
    const m = pn.match(/^\/api\/cards\/([^/]+)$/);
    if (m && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === m[1]);
      if (card) return { card };
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
    if (pn.endsWith("/transition")) return { ok: true, demo: true };
    if (pn === "/api/templates/suggest") {
      const t = (D["/api/templates"] || { templates: [] }).templates.slice(0, 3);
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "按场景匹配推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") return { trigger_description: (body && body.text || "") + "（演示：静态站不做真实 AI 改写）", examples: [] };
    if (pn === "/api/cards") return { card: { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft", ...(body || {}) } };
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
    if (pn === "/api/settings/judge-model") {
      const mid = (body && body.model_id || "").trim();
      if (!mid) { v6.judge = null; return { ok: true, judge: null }; }
      if (!(body && body.display_name)) return { error: "请填写显示名" };
      v6.judge = { model_id: mid, display_name: body.display_name };
      return { ok: true, judge: v6.judge };
    }
    if (pn === "/api/dataset/cluster") {
      if (v6.version) return { error: "上次定版后没有新增 query，暂不需要重新聚类" };
      v6.version = 1;
      return { task: { status: "running", done: 0, total: 508 } };
    }
    if (pn === "/api/profile/generate") {
      if (!v6.version) return { error: "还没有数据集版本：先攒够 Query 再聚类定版" };
      if (!v6.judge) return { error: "未配置 Judge 模型：画像打分需要它，请先在数据集页配置" };
      v6.generated = true;
      return { task: { status: "running", done: 0, total: 288 } };
    }
    if (pn === "/api/dataset/rollback") {
      return { error: "已是当前生效版本" };
    }
    if (pn === "/api/dataset/query/delete") { if (body && body.query_id) v6.deletedQ.add(body.query_id); return { ok: true }; }
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
          modelLocal.updated[mid] = { ...(modelLocal.updated[mid] || {}), ...(body && body.display_name ? { display_name: body.display_name } : {}) };
          return { ok: true };
        }
        if (act === "profile-data") {
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
    // v6：还没生成画像 → 冷启动随机探索（各模型均匀分流直答）
    if (!v6.generated) {
      const models = (((D["/v1/models"] || {}).models) || []).filter(m => m.status === "active" && !modelLocal.deleted.has(m.model_id));
      const pick = models[Math.floor(Math.random() * Math.max(1, models.length))] || { model_id: "swift-4b", display_name: "迅答 Swift-4B" };
      return sseStream([
        { step: "explore", text: `冷启动随机探索：本次随机分配 ${pick.display_name} 作答（各模型均匀分流，收集数据）` },
        { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
          content: "（随机探索期示例回答）围绕这个问题，可以从现状、约束与可行动作三个层面展开分析。",
          decision_summary: { mode: "auto", switch_result: "explore", final_model: pick.model_id, candidates: [pick.model_id],
            route_layer: "explore", dimension: dimOf(text), is_explore: true,
            total_cost: 0.0004, total_latency_ms: 520,
            model_calls: [{ model_id: pick.model_id, tokens_in: 90, tokens_out: 150, tokens_thinking: 0, cost: 0.0004, latency_ms: 520 }],
            policy: { policy_id: "policy-global-balanced", name: "全局均衡", allow_aggregation: 1, alpha: 0.7 } },
          usage: { cost: 0.0004, tokens: 240 } },
      ], 420);
    }
    const pid = body && body.policy_id;
    const pol = (((D["/v1/policies"] || {}).policies) || []).find(p => p.policy_id === pid);
    const polMeta = pol ? { policy_id: pol.policy_id, name: pol.name, latency_tier: pol.latency_tier,
      allow_aggregation: pol.allow_aggregation, K: (pol.params || {}).K || 3, alpha: (pol.params || {}).alpha ?? 0.7 }
      : { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", allow_aggregation: 1, K: 3, alpha: 0.7 };
    const dim = dimOf(text);
    const DIM_CN = THEME_CN;
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
    const aggReq = (body && body.aggregate) || "auto";
    const overrideDenied = aggReq === "on" && pol && ((pol.params || {}).allow_agg_override === 0);
    if (aggReq === "on" && pol && !pol.allow_aggregation && !overrideDenied) {
      return sseRouteDemoAgg({ ...polMeta, _override: "on" }, dim, DIM_CN[dim] || dim);
    }
    if ((pol && !pol.allow_aggregation) || aggReq === "off" || overrideDenied) {
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
            aggregate_override: aggReq !== "auto" ? aggReq : null, aggregate_override_denied: overrideDenied,
            total_cost: 0.0002, total_latency_ms: 410,
            model_calls: [{ model_id: "swift-4b", tokens_in: 120, tokens_out: 190, tokens_thinking: 0, cost: 0.0002, latency_ms: 410 }],
            policy: polMeta },
          usage: { cost: 0.0002, tokens: 310 } },
      ], 400);
    }
    return sseRouteDemoAgg({ ...polMeta, _override: aggReq !== "auto" ? aggReq : null }, dim, DIM_CN[dim] || dim);
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
          aggregate_override: polMeta._override || null, aggregate_override_denied: false,
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
