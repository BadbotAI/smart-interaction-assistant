// 测试抽屉：右侧拉起的对话流，供组件工作台（验证触发）与模型工作台（验证模型调用）复用。
// keepReasoning=true 时思考过程常驻展开（模型测试需要看过程）；否则回答后折叠。
window.TestChat = (function () {
  const { el } = UI;
  const TENANT = "tenant-demo";
  const USER = "demo-admin-test";

  function open(opts = {}) {
    if (document.querySelector(".drawer")) return; // 防连点开出多个抽屉
    const SESSION = "test-" + Math.random().toString(36).slice(2, 8);
    let busy = false;
    let ctrl = null;
    let lastQuestion = "";

    const msgs = el("div", { class: "tc-msgs" });
    const hint = el("div", { class: "muted", style: "text-align:center;padding:36px 12px" },
      [opts.hint || "像用户一样提问开始测试"]);
    msgs.appendChild(hint);

    // 调用方式选择：调度策略（智能路由）或指定单模型（模型测试的核心控件）
    const modelSel = el("select", { class: "model-pick", "aria-label": "选择调度策略或模型", title: "选择调度策略，或指定单个模型" });
    Promise.all([UI.api("/v1/policies").catch(() => ({ policies: [] })), UI.api("/v1/models").catch(() => ({ models: [] }))])
      .then(([{ policies }, { models }]) => {
        const gp = el("optgroup", { label: "调度策略" });
        (policies || []).filter(p => p.enabled).forEach(p =>
          gp.appendChild(el("option", { value: "policy:" + p.policy_id }, [p.name || p.policy_id])));
        if (gp.children.length) modelSel.appendChild(gp);
        const gm = el("optgroup", { label: "指定模型" });
        (models || []).filter(m => m.status === "active").forEach(m =>
          gm.appendChild(el("option", { value: m.model_id }, [m.display_name])));
        if (gm.children.length) modelSel.appendChild(gm);
        if (opts.model && [...modelSel.options].some(o => o.value === opts.model)) modelSel.value = opts.model;
        else if (gp.children.length) modelSel.value = gp.children[0].value;
      });
    const isAuto = () => String(modelSel.value).startsWith("policy:");
    const pickedPolicy = () => isAuto() ? String(modelSel.value).slice(7) : null;

    const input = el("input", { type: "text", placeholder: "输入消息…", "aria-label": "输入消息", autocomplete: "off" });
    const sendBtn = el("button", { class: "tc-send", title: "发送", "aria-label": "发送" });
    function renderSendBtn() {
      sendBtn.innerHTML = "";
      sendBtn.classList.toggle("stop", busy);
      sendBtn.title = busy ? "中断" : "发送";
      sendBtn.appendChild(busy ? el("span", { style: "width:11px;height:11px;background:#fff;border-radius:2px;display:block" }) : UI.icon("send", 16));
    }
    renderSendBtn();

    const composer = el("div", { class: "tc-composer" }, [modelSel, input, sendBtn]);
    const mask = UI.drawer(opts.title || "测试", msgs, composer);
    mask.querySelector(".drawer").style.width = "540px";
    const closeBtn = mask.querySelector(".close-btn");
    if (closeBtn) { closeBtn.textContent = "结束测试"; closeBtn.className = "btn small"; }
    const bodyBox = mask.querySelector(".drawer-body");
    const scrollBottom = () => { bodyBox.scrollTop = bodyBox.scrollHeight; };
    setTimeout(() => input.focus(), 150);
    if (opts.prefill) input.value = opts.prefill;

    // 事件上报（与线上同一 Schema；channel=test → 不进回显 / 看板 / 标签）
    function sendEvent(eventType, envelope, payload, extras = {}) {
      const ctx = envelope._ctx || {};
      UI.api("/v1/events", { method: "POST", body: { events: [{
        schema_version: "1.0.0", event_id: crypto.randomUUID(),
        trace_id: ctx.traceId || "unknown", tenant_id: TENANT, session_id: SESSION,
        turn_id: ctx.turnId || "unknown", user_id: USER, ts: new Date().toISOString(),
        event_type: eventType, channel: "test",
        card: { card_id: envelope.card_ref?.card_id || null, card_version: envelope.card_ref?.version || null,
          component_type: envelope.component_type, semantic_category: envelope.semantic_category,
          trigger_source: envelope.trigger_source },
        route_context: ctx.routeContext || {}, payload: { render_id: envelope.render_id, ...(payload || {}) },
        group: null, label_hint: extras.label_hint || null,
      }] } }).catch(() => {});
    }

    function userMsg(text) {
      hint.remove();
      msgs.appendChild(el("div", { class: "tc-u" }, [el("span", {}, [text])]));
      scrollBottom();
    }
    function botBubble() {
      hint.remove();
      const bubble = el("div", { class: "bubble" });
      msgs.appendChild(el("div", { class: "tc-b" }, [el("span", { class: "avatar" }, [UI.icon("bot", 14)]), bubble]));
      return bubble;
    }

    async function send(text, cardContext, o = {}) {
      if (busy || !text) return;
      busy = true; renderSendBtn(); modelSel.disabled = true;
      const isReal = !cardContext && !o.silent;
      if (isReal) { userMsg(text); lastQuestion = text; }
      const originText = isReal ? text : lastQuestion;

      const bubble = botBubble();
      const reason = el("div", { class: "reason-panel" }, [el("div", { class: "muted", style: "margin-bottom:4px" }, ["思考过程"])]);
      bubble.appendChild(reason);
      scrollBottom();
      const addStep = (t) => { reason.appendChild(el("div", { class: "reason-step" }, [el("span", { class: "dot" }, ["·"]), el("span", {}, [t])])); scrollBottom(); };

      ctrl = new AbortController();
      try {
        const res = await fetch("/v1/route", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
          body: JSON.stringify({ tenant_id: TENANT, session_id: SESSION, user_id: USER, text,
            card_context: cardContext, skip_card_match: !!o.skipCardMatch,
            mode: isAuto() ? "auto" : "manual", manual_model: isAuto() ? null : modelSel.value,
            policy_id: pickedPolicy() }),
        });
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (!chunk.startsWith("data:")) continue;
            const evt = JSON.parse(chunk.slice(5));
            if (evt.step === "final") renderFinal(bubble, reason, evt, originText);
            else if (evt.text) addStep(evt.text);
          }
        }
      } catch (err) {
        if (err.name === "AbortError") {
          reason.remove();
          bubble.appendChild(el("div", { class: "secondary" }, ["已中断"]));
        } else {
          reason.remove();
          bubble.appendChild(el("div", { class: "field-error" }, ["请求失败：" + err.message]));
        }
      }
      ctrl = null; busy = false; renderSendBtn(); modelSel.disabled = false;
      if (isReal) input.focus();
    }

    function renderFinal(bubble, reason, evt, originText) {
      const steps = reason.querySelectorAll(".reason-step").length;
      if (!steps) reason.remove();
      else if (!opts.keepReasoning) {
        // 组件测试：回答后折叠；模型测试（keepReasoning）：常驻展开
        const summary = el("details", {}, [el("summary", { class: "muted", style: "cursor:pointer" }, [`思考过程（${steps} 步）`])]);
        [...reason.querySelectorAll(".reason-step")].forEach(s => summary.appendChild(s));
        reason.replaceWith(el("div", { class: "reason-panel" }, [summary]));
      }
      const ctx = {
        traceId: evt.trace_id, turnId: evt.turn_id, sessionId: SESSION, userId: USER,
        routeContext: evt.route_context || {},
        titleInBubble: true,
        sendEvent: (type, envelope, payload, extras) => { envelope._ctx = ctx; sendEvent(type, envelope, payload, extras); },
        onCollectSubmit: (payload, envelope, extras = {}) => {
          envelope._ctx = ctx;
          sendEvent("card_submitted", envelope, payload, {});
          const summary = extras.summaryOverride ||
            (payload.form_values ? Object.entries(payload.form_values).map(([k, v]) => `${k}=${v}`).join("，")
              : `选择了「${payload.user_selection}」`);
          send("请基于我的提交继续", { summary, card_id: envelope.card_ref?.card_id || null,
            selection: Array.isArray(payload.user_selection) ? payload.user_selection[0] : payload.user_selection });
        },
        onControl: (action, envelope) => {
          envelope._ctx = ctx;
          sendEvent("control_invoked", envelope, { action });
          if (envelope.component_type === "control.confirm" && action === "confirm")
            send("继续执行刚才的操作", { summary: "用户已确认执行该高风险操作" });
        },
      };
      if (evt.content) bubble.appendChild(el("div", {}, [evt.content]));
      if (evt.ask_card) {
        const env = evt.ask_card;
        env._ctx = ctx;
        if (env.params?.prompt && env.params.reply_text) bubble.appendChild(el("div", { class: "bubble-prompt" }, [env.params.prompt]));
        bubble.appendChild(Components.render(env, ctx));
        bubble.appendChild(el("div", { style: "margin-top:6px" }, [
          el("button", { class: "btn small", style: "border:none;color:var(--text-muted)", onclick: (e) => {
            if (env._submitted || env._skipped) return;
            env._skipped = true; e.target.disabled = true;
            send(originText, null, { silent: true, skipCardMatch: true });
          } }, ["跳过，直接回答"]),
        ]));
      }
      // 呈现型组件照常渲染（评价型在测试抽屉里省略）
      (evt.components || []).filter(c => c.semantic_category === "present").forEach(c => { c._ctx = ctx; bubble.appendChild(Components.render(c, ctx)); });
      if (evt.decision_summary) {
        const d = evt.decision_summary;
        const pathName = { fastlane: "快车道", routed: "单模型路由", aggregated: "多模型聚合",
          degraded: "降级", manual: "手动指定" }[d.switch_result] || d.switch_result;
        if (opts.keepReasoning) {
          // 模型测试：三段式路由决策展示（策略 → 模型 → 成本）
          const tierName = { fast: "极速", balanced: "均衡", quality: "质量" };
          const pol = d.policy || {};
          const rows = [];
          if (d.mode === "manual") {
            rows.push(["策略", "手动指定模型（不走智能路由）"]);
          } else {
            rows.push(["策略", `${pol.name || pol.policy_id || "-"} · ${tierName[pol.latency_tier] || pol.latency_tier || "-"}档` +
              (pol.K ? ` · 候选 ${pol.K} 个` : "") +
              (pol.explore_ratio ? ` · 探索 ${Math.round(pol.explore_ratio * 100)}%` : "") +
              (d.is_explore ? "（本次命中探索流量）" : "")]);
          }
          const finalName = d.final_model || "-";
          rows.push(["模型", el("span", {}, [
            `${pathName}：`,
            ...(d.candidates || [finalName]).map((m, i) => el("span", {}, [
              i ? "、" : "", m === finalName && !d.aggregator ? el("strong", {}, [m]) : m])),
            d.aggregator ? el("span", {}, ["，由 ", el("strong", {}, [d.aggregator]), " 聚合定稿"]) : null,
          ])]);
          const calls = d.model_calls || [];
          if (calls.length) {
            rows.push(["成本", el("span", { class: "num" }, [
              calls.map(c2 => `${c2.model_id} ${(c2.tokens_in || 0) + (c2.tokens_out || 0) + (c2.tokens_thinking || 0)}tk ${UI.fmtCost(c2.cost)}`).join(" + "),
              ` = ${UI.fmtCost(d.total_cost)} · ${UI.fmtMs(d.total_latency_ms)}`,
            ])]);
          } else {
            rows.push(["成本", `${UI.fmtCost(d.total_cost)} · ${UI.fmtMs(d.total_latency_ms)}`]);
          }
          bubble.appendChild(el("div", { class: "route-cot" }, [
            el("div", { class: "rc-title" }, ["路由决策"]),
            ...rows.map(([k, v]) => el("div", { class: "rc-row" }, [
              el("span", { class: "rc-k" }, [k]), el("span", { class: "rc-v" }, [v]),
            ])),
          ]));
        } else {
          bubble.appendChild(el("div", { class: "muted", style: "margin-top:6px;font-size:var(--font-small)" }, [
            el("strong", { style: "color:var(--text-secondary)" }, [d.final_model || "-"]),
            ` · ${pathName} · ${UI.fmtMs(d.total_latency_ms)} · ${UI.fmtCost(d.total_cost)}` + (d.is_explore ? " · 探索流量" : ""),
          ]));
        }
      }
      scrollBottom();
    }

    sendBtn.onclick = () => {
      if (busy) { if (ctrl) ctrl.abort(); return; }
      const t = input.value.trim();
      if (t) { input.value = ""; send(t); }
    };
    input.addEventListener("keydown", e => { if (e.key === "Enter" && !busy) sendBtn.onclick(); });
    return mask;
  }

  return { open };
})();
