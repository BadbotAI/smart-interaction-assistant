// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  // v7 会话状态机：配置智能路由模型 → benchmark 得分表（点格修正）→ 判维路由（静态站可走完整动线）
  const v7 = { router: null, overrides: {}, asof: null, keys: {} };
  // —— 演示数据持久层：产品 / 组件实例 / 审计写进 localStorage，刷新与跨页不丢（v8） ——
  const LS_KEY = "sia_demo_state_v8";
  let S = { prodCreated: [], prodUpdated: {}, prodDeleted: [],
            cardCreated: [], cardUpdated: {}, cardStatus: {}, cardDeleted: [], audit: [] };
  try { S = { ...S, ...(JSON.parse(localStorage.getItem(LS_KEY) || "{}")) }; } catch (e) {}
  const persist = () => { try { localStorage.setItem(LS_KEY, JSON.stringify(S)); } catch (e) {} };
  function auditLog(action, detail) {
    S.audit.unshift({ ts: Date.now() / 1000, actor: "张三", action, detail });
    if (S.audit.length > 100) S.audit.length = 100;
    persist();
  }
  // 兼容旧变量名的轻代理
  const deadCards = { has: id => S.cardDeleted.includes(id), add: id => { if (!S.cardDeleted.includes(id)) S.cardDeleted.push(id); persist(); } };
  const cardStatus = S.cardStatus;
  const prodLocal = { created: S.prodCreated, updated: S.prodUpdated,
    deleted: { has: id => S.prodDeleted.includes(id), add: id => { if (!S.prodDeleted.includes(id)) S.prodDeleted.push(id); persist(); } } };
  // 合并视图：快照组件 + 本地新建组件（含状态与编辑覆盖）
  function allCardsMerged() {
    const seeded = (((D["/api/cards"] || {}).cards) || [])
      .filter(c => !deadCards.has(c.card_id))
      .map(c => ({ ...c, ...(S.cardUpdated[c.card_id] || {}), ...(cardStatus[c.card_id] ? { status: cardStatus[c.card_id] } : {}) }));
    const created = S.cardCreated.filter(c => !deadCards.has(c.card_id));
    return [...created, ...seeded];
  }
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
      // 接入派生字段统一在此补齐（pub_key 缺失会让前端接入代码出现 undefined）
      const derive = p => ({ pub_key: "pk-web-demo" + String(p.product_id || "").slice(-6), current_hash: "demo-hash",
        pulled_hash: p.pulled_hash === undefined ? "demo-hash" : p.pulled_hash,
        pulled_at: p.pulled_at === undefined ? Date.now() / 1000 - 3600 : p.pulled_at, stale: false, ...p });
      base.products = (base.products || []).filter(p => !prodLocal.deleted.has(p.product_id))
        .map(p => derive({ ...p, ...(prodLocal.updated[p.product_id] || {}) }))
        .concat(prodLocal.created.filter(p => !prodLocal.deleted.has(p.product_id))
          .map(p => derive({ ...p, ...(prodLocal.updated[p.product_id] || {}) })));
      return base;
    }
    if (pn === "/api/cards") {
      let cards = allCardsMerged();
      const q = new URLSearchParams((full || "").split("?")[1] || "").get("q");
      if (q && q.trim()) {
        const kw = q.trim().toLowerCase();
        cards = cards.filter(c => (c.name || "").toLowerCase().includes(kw)
          || (c.component_type || "").toLowerCase().includes(kw));
      }
      return { cards: JSON.parse(JSON.stringify(cards)) };
    }
    if (pn === "/api/audit") {
      const base = JSON.parse(JSON.stringify(D["/api/audit"] || { audit: [] }));
      base.audit = [...S.audit, ...(base.audit || [])];
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
    if (pn === "/api/brands") return { brands: window.__mockBrands() };
    if (pn === "/api/brands/active") return { file: "brand-tokens.default.json" };
    const regm = pn.match(/^\/v1\/products\/([^/]+)\/registry$/);
    if (regm) {
      // 静态站演示：注册表由组件目录组装（真实环境按产品绑定的组件实例生成）
      const cat = (D["/api/components/catalog"] || {}).catalog || [];
      const prod = ((D["/api/products"] || {}).products || []).find(p => p.product_id === regm[1]) || {};
      return { registry_version: "2.1", generated_at: new Date().toISOString(), content_hash: "demo",
        product_id: regm[1], product_name: prod.name || "演示产品", brand_file: prod.brand_file || "brand-tokens.default.json",
        usage: "把 components[] 作为工具声明给你的大模型；模型返回 { component_id, params } 后交给 SIA.render 渲染",
        components: cat.map((t, i) => ({ component_id: "ci-demo-" + String(i + 1).padStart(2, "0"), type: t.type,
          name: t.label, description: t.desc, interactive: !!t.interactive,
          params_schema: t.params_schema || {}, fixed: t.fixed || {}, submit_schema: t.submit_schema || null })) };
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
    if (m) {
      const card = allCardsMerged().find(c => c.card_id === m[1]);
      if (card) return { card: JSON.parse(JSON.stringify(card)) };
    }
    if (pn.startsWith("/api/dashboard/questions")) return D["/api/dashboard/questions"];
    if (pn.startsWith("/api/dashboard/insights")) return D["/api/dashboard/insights"];
    if (pn.startsWith("/api/dashboard/overview")) return D["/api/dashboard/overview"];
    const em = pn.match(/^\/v1\/embed\/envelope\/([^/]+)$/);
    if (em) {
      const merged = allCardsMerged();
      const card = merged.find(c => c.card_id === em[1]) || merged.find(c => c.status === "published");
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
            ...(cfg.max_select ? { max_select: cfg.max_select } : {}),
            ...(cfg.down_reasons ? { down_reasons: cfg.down_reasons } : {}),
            ...((card.text_templates || {}).cancel ? { cancel_label: card.text_templates.cancel } : {}),
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
      const own = S.cardCreated.find(c => c.card_id === cid);
      if (own) { own.status = cardStatus[cid] || own.status; if (action === "publish") own.version = (own.version || 0) + 1; }
      persist();
      const base = allCardsMerged().find(c => c.card_id === cid);
      if (base) auditLog(action === "publish" ? "card_publish" : "card_offline", { 组件: base.name, 版本: base.version });
      return { ok: true, card: base || null };
    }
    if (pn === "/api/templates/suggest") {
      const t = (D["/api/templates"] || { templates: [] }).templates.slice(0, 3);
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "按场景匹配推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") {
      // 与服务端 cards.rewrite_trigger 同规则：口语描述 → 规范触发描述 + 3 条示例问法（任意输入都产出完整结构）
      let core = ((body && body.description) || (body && body.text) || "").trim().replace(/[。，,.]+$/, "");
      if (!core) return { trigger_description: "", trigger_examples: [] };
      for (const pre of ["当用户", "用户", "当", "如果", "客户"]) {
        if (core.startsWith(pre)) { core = core.slice(pre.length); break; }
      }
      for (const suf of ["的时候", "的情况下", "时"]) {
        if (core.endsWith(suf)) { core = core.slice(0, -suf.length); break; }
      }
      return {
        trigger_description: `当用户${core}时触发本配置。适用于该场景下的咨询、求助与处理请求；不适用于普通闲聊或与此无关的问题。触发后按本配置的信息与交互组件引导用户。`,
        trigger_examples: [`${core}，怎么处理`, `我遇到了${core}的情况`, `关于${core}想咨询一下`],
      };
    }
    if (pn === "/api/cards") {
      const card = { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft",
        lock_version: 0, created_at: Date.now() / 1000, updated_at: Date.now() / 1000, ...(body || {}) };
      S.cardCreated.unshift(card);
      persist();
      auditLog("card_create", { 组件: card.name || card.card_id, 类型: card.component_type });
      return { card: JSON.parse(JSON.stringify(card)) };
    }
    {
      const cm = pn.match(/^\/api\/cards\/([^/]+)$/);
      if (cm) {
        // 编辑保存：本地新建的直接改，快照种子的记覆盖层
        const patch = (body && body.payload) || body || {};
        const own = S.cardCreated.find(c => c.card_id === cm[1]);
        if (own) { Object.assign(own, patch, { updated_at: Date.now() / 1000, lock_version: (own.lock_version || 0) + 1 }); persist();
          auditLog("card_update", { 组件: own.name || cm[1] });
          return { card: JSON.parse(JSON.stringify(own)) }; }
        S.cardUpdated[cm[1]] = { ...(S.cardUpdated[cm[1]] || {}), ...patch, updated_at: Date.now() / 1000 };
        persist();
        const merged = allCardsMerged().find(c => c.card_id === cm[1]);
        if (merged) auditLog("card_update", { 组件: merged.name || cm[1] });
        return { card: merged ? JSON.parse(JSON.stringify(merged)) : { card_id: cm[1], ...patch } };
      }
    }
    if (pn === "/api/components/schema-preview") {
      const cat = ((D["/api/components/catalog"] || {}).catalog) || [];
      const ct = (body && body.component_type) || "";
      const t = ct === "chart.line" || ct === "chart.bar" ? "chart" : ct.startsWith("select.") ? "select"
        : ct === "form.structured" ? "form" : ct === "control.confirm" ? "confirm"
        : ct === "feedback.binary" ? "feedback" : ct === "feedback.preference" ? "preference" : ct;
      const hit = cat.find(x => x.type === t) || {};
      return { params_schema: hit.params_schema || {}, fixed: {}, submit_schema: hit.submit_schema || null };
    }
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
      const taken = getMock("/api/products").products.some(p => p.name === name);
      if (taken) return { error: "已有同名产品，请换一个名称", __status: 409 };
      const pid = "prod-demo-" + Math.random().toString(36).slice(2, 8);
      prodLocal.created.push({ product_id: pid, name, brand_file: (body && body.brand_file) || "brand-tokens.default.json",
        card_ids: (body && body.card_ids) || [], created_at: Date.now() / 1000,
        mcp_key: "sk-mcp-demo" + Math.random().toString(36).slice(2, 10),
        pub_key: "pk-web-demo" + Math.random().toString(36).slice(2, 8) });
      persist();
      auditLog("product_create", { 产品: name });
      return { product_id: pid, mcp_key: prodLocal.created[prodLocal.created.length - 1].mcp_key };
    }
    if (/^\/api\/products\/[^/]+$/.test(pn)) {
      const pid = pn.split("/")[3];
      prodLocal.updated[pid] = { ...(prodLocal.updated[pid] || {}), ...(body || {}) };
      const c = prodLocal.created.find(p => p.product_id === pid);
      if (c) Object.assign(c, body || {});
      persist();
      const nm = (c || {}).name || ((getMock("/api/products").products.find(p => p.product_id === pid) || {}).name) || pid;
      auditLog("product_update", { 产品: nm });
      return { ok: true };
    }
    if (pn === "/api/brands") {
      const tokens = (body || {}).tokens || {};
      const bid = String((body || {}).brand_id || tokens.brand_id || "").trim().toLowerCase();
      const bname = String((body || {}).brand_name || tokens.brand_name || "").trim();
      if (!/^[a-z0-9][a-z0-9-]{1,23}$/.test(bid)) return { error: "brand_id 需为 2-24 位小写字母、数字或短横线" };
      if (bid === "default") return { error: "默认品牌不可覆盖，请换一个 brand_id" };
      if (!bname) return { error: "缺少 brand_name" };
      const file = "brand-tokens." + bid + ".json";
      const rec = { file, brand_id: bid, brand_name: bname, tokens: { ...tokens, brand_id: bid, brand_name: bname } };
      const i = BRAND_EXTRA.findIndex(b => b.brand_id === bid);
      if (i >= 0) BRAND_EXTRA[i] = rec; else BRAND_EXTRA.push(rec);
      brandPersist();
      auditLog("brand_import", { 主题: bname });
      return { ok: true, file, brand_id: bid, brand_name: bname };
    }
    if (pn === "/api/brands/delete") {
      const dead = BRAND_EXTRA.find(b => b.file === (body || {}).file);
      BRAND_EXTRA = BRAND_EXTRA.filter(b => b.file !== (body || {}).file);
      brandPersist();
      auditLog("brand_delete", { 主题: (dead || {}).brand_name || (body || {}).file });
      return { ok: true };
    }
    if (/^\/api\/products\/[^/]+\/reset-key$/.test(pn)) {
      return { ok: true, mcp_key: "sk-mcp-" + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 8),
        pub_key: "pk-web-" + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 8) };
    }
    if (/^\/api\/products\/[^/]+\/delete$/.test(pn)) {
      const pid = pn.split("/")[3];
      const nm = ((getMock("/api/products").products.find(p => p.product_id === pid) || {}).name) || pid;
      prodLocal.deleted.add(pid);
      auditLog("product_delete", { 产品: nm });
      return { ok: true };
    }
    if (/^\/api\/cards\/[^/]+\/delete$/.test(pn)) {
      const cid = pn.split("/")[3];
      const nm = ((allCardsMerged().find(c => c.card_id === cid) || {}).name) || cid;
      deadCards.add(cid);
      auditLog("card_delete", { 组件: nm });
      return { ok: true };
    }
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
      ...(card.style_overrides && Object.keys(card.style_overrides).length ? { style_overrides: card.style_overrides } : {}),
      component_type: card.component_type, semantic_category: "collect", trigger_source: source || "model_tool_call",
      card_ref: { card_id: card.card_id, version: card.version },
      params: { prompt: (card.text_templates || {}).prompt || card.name,
        reply_text: (card.text_templates || {}).reply || "",
        submit_label: (card.text_templates || {}).submit || "提交",
        options: cfg.options || [], option_meta: cfg.option_meta || {}, option_actions: cfg.option_actions || {},
        display: cfg.display || "", recommended_default: cfg.recommended_default || null,
        fields: cfg.fields || [], likert: cfg.likert || null, slider: cfg.slider || null,
        dimensions: cfg.dimensions || [], values: cfg.values || null, placeholder: cfg.placeholder || "",
        ...(cfg.max_select ? { max_select: cfg.max_select } : {}),
        ...(cfg.down_reasons ? { down_reasons: cfg.down_reasons } : {}),
        ...((card.text_templates || {}).cancel ? { cancel_label: card.text_templates.cancel } : {}),
        echo_results: false } };
  }

  // v2 选件模拟：大模型按组件说明判断该出哪个组件（与服务端规则同步；生产由真实模型决定）
  const V2_PICK = [
    ["pie", ["占比", "比例", "构成", "份额"]],
    ["trend", ["走势", "趋势", "变化", "折线"]],
    ["bar", ["对比图", "柱状", "分布", "环比", "同比", "数量对比"]],
    ["metric", ["指标", "总共多少", "核心数字", "总览"]],
    ["timeline", ["时间线", "历程", "进度", "节点"]],
    ["steps", ["步骤", "怎么操作", "操作指引", "分几步", "流程指引"]],
    ["table", ["表格", "列个表", "清单", "明细", "整理成表", "列出来"]],
    ["control.confirm", ["取消", "删除", "撤销", "退款", "终止", "变更", "确认执行"]],
    ["form.structured", ["登记", "填写", "联系方式", "补充信息", "留个", "资料", "预约"]],
    ["feedback.preference", ["哪个好", "哪份好", "择优", "更认可", "两个方案"]],
    ["feedback.binary", ["评价一下", "满意吗", "打个分", "反馈"]],
    ["select", ["选", "挑", "哪种", "方式", "方案", "怎么处理", "有哪些"]],
    ["slider.range", ["多少钱", "预算", "数量", "额度"]],
    ["scale.likert", ["打几分", "评分", "满意度"]],
    ["picker.datetime", ["什么时候", "约个时间", "哪天", "几点"]],
    ["rank.priority", ["排个序", "优先级", "先后顺序"]],
    ["matrix.compare", ["对比一下", "多维对比", "打分对比"]],
    ["list.ordered", ["要点", "注意事项", "总结几点"]],
    ["text.emphasis", ["一句话结论", "核心结论"]],
    ["chart.waterfall", ["瀑布图", "构成拆解", "增减项"]],
  ];
  function gen_v2_options(text) {
    if (/货|快递|延误|派送/.test(text)) return ["加急派送", "改约取件时间", "转自提点", "申请破损赔付"];
    if (/发票|开票/.test(text)) return ["电子普票", "增值税专票", "纸质普票"];
    if (/方案|对比/.test(text)) return ["方案 A · 时效优先", "方案 B · 成本优先", "方案 C · 均衡"];
    return ["确认继续", "查看详情", "换个方案", "稍后处理"];
  }

  function matchCard(text) {
    const cards = ((D["/api/cards"] || {}).cards || [])
      .map(c => cardStatus[c.card_id] ? { ...c, status: cardStatus[c.card_id] } : c)
      .filter(c => c.status === "published");
    const typeOf = ct => ct === "chart.line" ? "trend" : ct === "chart.bar" ? "bar" : ct === "chart.pie" ? "pie"
      : ct === "metric.card" ? "metric" : ct.startsWith("select.") ? "select" : ct;
    for (const [t, words] of V2_PICK) {
      if (words.some(w => text.includes(w))) {
        const hit = cards.find(c => typeOf(c.component_type) === t);
        if (hit) return hit;
      }
    }
    return null;
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
      if (hit && (hit.semantic_category === "present")) {
        // v2 展示类：模型判定用它翻译结构化内容——带演示数据直接渲染，无提交
        const PRESENT_DEMO = {
          "chart.waterfall": { title: "利润构成", categories: ["营收", "运费", "仓储", "人力", "退款"], values: [420, -86, -54, -120, -32] },
          "matrix.compare": { title: "方案对比", options: ["方案 A", "方案 B"], dimensions: ["时效", "成本", "稳定"], values: [[8, 6, 7], [6, 9, 8]] },
          "list.ordered": { title: "注意事项", items: ["保留原包装", "签收前拍照", "异常尽快反馈"] },
          "text.emphasis": { value: "本月履约率 98.6%，创近半年新高", tone: "positive" },
          "table": { title: "分区域概览", columns: ["区域", "数量", "环比"], rows: [["华东", "352", "2.3%"], ["华南", "332", "1.5%"], ["华北", "372", "3.2%"]] },
          "chart.line": { title: "近半年走势", categories: ["4月", "5月", "6月", "7月", "8月", "9月"], series: [{ name: "金额（万元）", values: [122, 165, 148, 161, 178, 190] }] },
          "chart.bar": { title: "分区域对比", categories: ["华东", "华南", "华北", "西南"], series: [{ name: "数量", values: [352, 332, 372, 222] }] },
          "chart.pie": { title: "构成占比", slices: [{ label: "华东", value: 42 }, { label: "华南", value: 27 }, { label: "华北", value: 22 }, { label: "其他", value: 9 }] },
          "metric.card": { label: "本月累计金额", value: "1,286", unit: "万元", delta: "4.2%", baseline: "对比上月同期" },
          "timeline": { title: "处理进度", events: [{ ts: "09:20", title: "已受理", desc: "工单创建" }, { ts: "10:05", title: "处理中", desc: "已分派专员跟进" }, { ts: "14:30", title: "待确认", desc: "方案已发出" }] },
          "steps": { title: "操作指引", steps: ["填写申请信息", "上传相关凭证", "等待审核", "查收处理结果"], current_index: 1 },
        };
        const params = PRESENT_DEMO[hit.component_type] || PRESENT_DEMO["table"];
        const isTable = hit.component_type === "table";
        return sseStream([
          { step: "match", text: `模型判断适用组件：「${hit.name}」（展示类，直接渲染）` },
          { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
            content: `已为你整理为${isTable ? "表格" : "可视化"}` + (params.title ? `：${params.title}` : "。"),
            components: [{ schema_version: "1.0.0", render_id: "v2-" + Math.random().toString(36).slice(2, 8),
              component_type: hit.component_type, semantic_category: "present", trigger_source: "model_tool_call",
              card_ref: { card_id: hit.card_id, version: hit.version }, params }],
            decision_summary: { mode: "auto", switch_result: "fastlane", final_model: "swift-4b", candidates: ["swift-4b"],
              total_cost: 0.0002, total_latency_ms: 240,
              policy: { policy_id: "policy-global-balanced", name: "全局均衡", latency_tier: "balanced", K: 3 } },
            usage: { cost: 0.0002, tokens: 120 } },
        ], 350);
      }
      if (hit) {
        const env = makeEnvelope(hit);
        // v2 动态选项：选择组件未预置选项时模拟模型按对话给出
        if (hit.component_type.startsWith("select.") && !(env.params.options || []).length)
          env.params.options = gen_v2_options(text);
        if (hit.component_type === "feedback.preference" && !(env.params.candidates || []).length)
          env.params.candidates = [
            { alias: "方案 A", label: "方案 A", content: "优先保证时效：改走直达线路，成本上浮约 8%。" },
            { alias: "方案 B", label: "方案 B", content: "优先控制成本：维持现有线路，预计多用 2 天。" }];
        return sseStream([
          { step: "match", text: `模型判断适用组件：「${hit.name}」` },
          { step: "final", trace_id: "demo-trace", turn_id: "t-" + Math.random().toString(36).slice(2, 8),
            content: "", ask_card: env,
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

  // —— 风格主题 mock：静态站也能新建 / 编辑 / 删除 / 预览（sessionStorage 会话内持久） ——
  const BRAND_BUILTIN = [
    { file: "brand-tokens.default.json", brand_id: "default", brand_name: "默认风格 · 墨蓝" },
    { file: "brand-tokens.chainbao.json", brand_id: "chainbao", brand_name: "自然清新" },
    { file: "brand-tokens.meetnote.json", brand_id: "meetnote", brand_name: "创意活力" },
    { file: "brand-tokens.caishui.json", brand_id: "caishui", brand_name: "政企稳重" },
    { file: "brand-tokens.harbor.json", brand_id: "harbor", brand_name: "远洋港航（示例品牌B）" },
  ];
  let BRAND_EXTRA = [];
  try { BRAND_EXTRA = JSON.parse(localStorage.getItem("mock_brands") || sessionStorage.getItem("mock_brands") || "[]"); } catch (e) {}
  const brandPersist = () => { try { localStorage.setItem("mock_brands", JSON.stringify(BRAND_EXTRA)); } catch (e) {} };
  window.__mockBrands = () => [...BRAND_BUILTIN, ...BRAND_EXTRA.map(b => ({ file: b.file, brand_id: b.brand_id, brand_name: b.brand_name }))];

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
    if (!isApi) {
      try {
        const mb = new URL(String(url), location.href).pathname.match(/\/brand\/([^/]+\.json)$/);
        const hit = mb && BRAND_EXTRA.find(b => b.file === mb[1]);
        if (hit) return Promise.resolve(json(hit.tokens || {}));
      } catch (e) {}
      return realFetch(url, opts);
    }
    const method = (opts.method || "GET").toUpperCase();
    const pn = u.split("?")[0];
    let body = null;
    if (opts.body) { try { body = JSON.parse(opts.body); } catch (e) {} }
    if (pn === "/v1/route") return Promise.resolve(sseRoute(body));
    if (method === "GET") return Promise.resolve(json(getMock(pn, u)));
    const out = postMock(pn, body);
    const st = out && out.__status ? out.__status : (out && out.error ? 400 : 200);
    if (out && out.__status) delete out.__status;
    return Promise.resolve(json(out, st));
  };
})();
