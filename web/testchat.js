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
    let hint;
    if (opts.suggestions && opts.suggestions.length) {
      // 引导空态：说明 + 预设问法（点击即发送），让测试者第一眼知道能做什么
      hint = el("div", { class: "tc-guide" }, [
        el("div", { class: "tg-title" }, [opts.guideTitle || "试试这些真实场景"]),
        opts.hint ? el("div", { class: "tg-sub" }, [opts.hint]) : null,
        el("div", { class: "tg-chips" }, opts.suggestions.map(sg => el("button", { type: "button", class: "tg-chip",
          onclick: () => send(sg.text) }, [
          el("span", { class: "tg-q" }, [sg.text]),
          sg.label ? el("span", { class: "tg-l" }, [sg.label]) : null,
        ]))),
      ]);
    } else {
      hint = el("div", { class: "muted", style: "text-align:center;padding:36px 12px" },
        [opts.hint || "像用户一样提问开始测试"]);
    }
    msgs.appendChild(hint);

    // 调用方式选择：自绘分组下拉（调度策略 / 多模型 / 指定模型），不用系统默认 select
    const pickState = { kind: "policy", value: null, label: "加载中……" };
    let pickGroups = [];
    const pickLab = el("span", { class: "fsel-label" }, [pickState.label]);
    const modelSel = el("button", { class: "fsel tc-pick", type: "button", "aria-label": "选择调用方式" }, [pickLab, el("span", { class: "fsel-caret" }, [UI.icon("chevron", 13)])]);
    const setPick = (kind, value, label) => { pickState.kind = kind; pickState.value = value; pickState.label = label; pickLab.textContent = label; modelSel.title = label; };
    Promise.all([UI.api("/v1/policies").catch(() => ({ policies: [] })), UI.api("/v1/models").catch(() => ({ models: [] }))])
      .then(([{ policies }, { models }]) => {
        const actives = (models || []).filter(m => m.status === "active");
        pickGroups = [
          { label: "调度策略", items: (policies || []).filter(p => p.enabled).map(p => ({ kind: "policy", value: p.policy_id, label: p.name || p.policy_id })) },
          { label: "多模型", items: [{ kind: "multi", value: null, label: "多模型回答 + 择优" }] },
          { label: "指定模型", items: actives.map(m => ({ kind: "model", value: m.model_id, label: m.display_name })) },
        ];
        const preset = opts.model && actives.find(m => m.model_id === opts.model);
        if (preset) setPick("model", preset.model_id, preset.display_name);
        else if (pickGroups[0].items.length) { const f = pickGroups[0].items[0]; setPick("policy", f.value, f.label); }
        drawModeBar();
      });
    modelSel.onclick = () => {
      if (modelSel.disabled) return;
      document.querySelectorAll(".menu-pop").forEach(n => n.remove());
      const pop = el("div", { class: "menu-pop tc-pick-pop", role: "listbox" });
      pickGroups.forEach(g => {
        if (!g.items.length) return;
        pop.appendChild(el("div", { class: "pop-group" }, [g.label]));
        g.items.forEach(it => pop.appendChild(el("button", {
          class: "menu-item" + (pickState.kind === it.kind && pickState.value === it.value ? " on" : ""), role: "option",
          onclick: () => { pop.remove(); setPick(it.kind, it.value, it.label); },
        }, [it.label])));
      });
      document.body.appendChild(pop);
      const r = modelSel.getBoundingClientRect();
      pop.style.minWidth = Math.max(r.width, 180) + "px";
      pop.style.left = (r.left + window.scrollX) + "px";
      pop.style.top = (r.top + window.scrollY - pop.offsetHeight - 6) + "px"; // 向上展开（按钮在底部输入条）
      const close = (e) => { if (!pop.contains(e.target) && e.target !== modelSel) { pop.remove(); document.removeEventListener("click", close, true); } };
      setTimeout(() => document.addEventListener("click", close, true), 0);
    };
    const isAuto = () => pickState.kind !== "model";
    const pickedPolicy = () => pickState.kind === "policy" ? pickState.value : null;
    const pickedMode = () => pickState.kind === "multi" ? "multi" : (pickState.kind === "model" ? "manual" : "auto");

    const input = el("input", { type: "text", placeholder: "输入消息…", "aria-label": "输入消息", autocomplete: "off" });
    const sendBtn = el("button", { class: "tc-send", title: "发送", "aria-label": "发送" });
    function renderSendBtn() {
      sendBtn.innerHTML = "";
      sendBtn.classList.toggle("stop", busy);
      sendBtn.title = busy ? "中断" : "发送";
      sendBtn.appendChild(busy ? el("span", { style: "width:11px;height:11px;background:#fff;border-radius:2px;display:block" }) : UI.icon("send", 16));
    }
    renderSendBtn();

    // 页面模式的两级选择：左 tab 选策略（右侧显示自动路由）或「固定模型」（右侧出模型下拉）
    const modeBar = el("div", { class: "tc-modebar" });
    function drawModeBar() {
      if (!opts.mountEl) return;
      modeBar.innerHTML = "";
      const segs = el("div", { class: "segmented", style: "overflow-x:auto" });
      pickGroups.filter(g => g.label === "调度策略").forEach(g => g.items.forEach(it => {
        segs.appendChild(el("button", { type: "button",
          class: "seg" + (pickState.kind === "policy" && pickState.value === it.value ? " on" : ""),
          onclick: () => { setPick("policy", it.value, it.label); drawModeBar(); } }, [it.label]));
      }));
      segs.appendChild(el("button", { type: "button", class: "seg" + (pickState.kind === "multi" ? " on" : ""),
        onclick: () => { setPick("multi", null, "多模型回答 + 择优"); drawModeBar(); } }, ["多模型"]));
      const actives = (pickGroups.find(g => g.label === "指定模型") || { items: [] }).items;
      segs.appendChild(el("button", { type: "button", class: "seg" + (pickState.kind === "model" ? " on" : ""),
        onclick: () => { const f = actives[0]; if (!f) { UI.toast("还没有在线模型", true); return; }
          setPick("model", pickState.kind === "model" ? pickState.value : f.value,
            pickState.kind === "model" ? pickState.label : f.label);
          drawModeBar(); } }, ["固定模型"]));
      modeBar.appendChild(segs);
      modeBar.appendChild(el("div", { style: "flex:1" }));
      if (pickState.kind === "model") {
        modeBar.appendChild(UI.fancySelect({ value: pickState.value,
          options: actives.map(it => [it.value, it.label]),
          onChange: (v) => { const hit = actives.find(x => x.value === v); setPick("model", v, hit ? hit.label : v); } }));
      } else {
        modeBar.appendChild(el("span", { class: "chip blue", title: pickState.kind === "multi"
          ? "所有候选模型都答一遍，你来选更好的一份"
          : "按所选策略自动选模型（含聚合）" }, [pickState.kind === "multi" ? "全员作答" : "自动路由"]));
      }
    }
    const composer = el("div", { class: "tc-composer" }, [opts.mountEl ? null : modelSel, input, sendBtn]);
    // 两种宿主：默认右侧抽屉；opts.mountEl 提供容器则渲染为页面内对话区（独立测试页用）
    let mask = null, bodyBox;
    if (opts.mountEl) {
      const host = typeof opts.mountEl === "string" ? document.querySelector(opts.mountEl) : opts.mountEl;
      bodyBox = el("div", { class: "tc-pagebody" }, [msgs]);
      host.innerHTML = "";
      host.appendChild(el("div", { class: "tc-page" }, [bodyBox, el("div", { class: "tc-pagefoot" }, [modeBar, composer])]));
    } else {
      mask = UI.drawer(opts.title || "测试", msgs, composer);
      mask.querySelector(".drawer").style.width = "540px";
      const closeBtn = mask.querySelector(".close-btn");
      if (closeBtn) { closeBtn.textContent = "结束测试"; closeBtn.className = "btn small"; }
      bodyBox = mask.querySelector(".drawer-body");
    }
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
      const addStep = (t, evt2) => {
        const node = el("div", { class: "reason-step" }, [el("span", { class: "dot" }, ["·"]), el("span", {}, [t])]);
        reason.appendChild(node);
        // 粗排打分：展示各候选模型的历史命中率（论文 Step 2 的过程数据）
        if (opts.keepReasoning && evt2 && evt2.scores) {
          const ranked = Object.entries(evt2.scores).sort((x, y) => y[1] - x[1]).slice(0, 5);
          const maxV = ranked.length ? ranked[0][1] || 1 : 1;
          reason.appendChild(el("div", { class: "rs-bars" }, ranked.map(([mid, v]) =>
            el("div", { class: "rs-row" }, [
              el("span", { class: "rs-m num" }, [mid]),
              el("div", { class: "rs-track" }, [el("div", { class: "rs-fill" + ((evt2.candidates || []).includes(mid) ? " on" : ""), style: `width:${Math.round(v / maxV * 100)}%` })]),
              el("span", { class: "rs-v num" }, [Number(v).toFixed(2)]),
            ]))));
        }
        scrollBottom();
      };

      ctrl = new AbortController();
      try {
        const res = await fetch("/v1/route", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
          body: JSON.stringify({ tenant_id: TENANT, session_id: SESSION, user_id: USER, text,
            card_context: cardContext, skip_card_match: !!o.skipCardMatch,
            mode: pickedMode(), manual_model: pickState.kind === "model" ? pickState.value : null,
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
            else if (evt.text) addStep(evt.text, evt);
          }
        }
      } catch (err) {
        if (err.name === "AbortError") {
          reason.remove();
          bubble.appendChild(el("div", { class: "secondary" }, ["已中断"]));
        } else {
          reason.remove();
          bubble.appendChild(el("div", { class: "field-error" }, ["请求失败：" + err.message]));
          bubble.appendChild(el("div", { style: "margin-top:6px" }, [
            el("button", { class: "btn small", onclick: (e2) => { e2.target.closest(".tc-b")?.remove(); send(text, cardContext, o); } }, ["重试"]),
          ]));
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
        bubble.appendChild(el("div", { style: "margin-top:6px;display:flex;gap:8px;align-items:center" }, [
          el("button", { class: "btn small", style: "border:none;color:var(--text-muted)", onclick: (e) => {
            if (env._submitted || env._skipped) return;
            env._skipped = true; e.target.disabled = true;
            send(originText, null, { silent: true, skipCardMatch: true });
          } }, ["跳过，直接回答"]),
          opts.editableCards && env.card_ref?.card_id
            ? el("a", { class: "btn small ghost", href: "/web/cards.html?edit=" + env.card_ref.card_id,
                title: "对触发效果或组件内容不满意？直接改这条配置",
                onclick: (e) => {
                  // 离开会丢当前测试对话，轻确认
                  if (!confirm("去编辑这条配置？当前测试对话不会保留。")) e.preventDefault();
                } }, [UI.icon("edit", 13), "编辑这条配置"])
            : null,
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
          // 聚合路径：标识 + 候选回答查看
          if (d.switch_result === "aggregated") {
            const pref = (evt.components || []).find(c2 => c2.component_type === "feedback.preference");
            const cands = pref?.params?.candidates || [];
            bubble.appendChild(el("div", { class: "agg-badge" }, [
              el("span", { class: "chip blue" }, [`综合了 ${(d.candidates || []).length || cands.length} 个模型的回答`]),
              cands.length ? (() => {
                const det = el("details", {}, [
                  el("summary", { class: "muted", style: "cursor:pointer;font-size:var(--font-small)" }, [`查看 ${cands.length} 份候选回答`]),
                  ...cands.map(c2 => el("div", { class: "agg-cand" }, [
                    el("div", { class: "muted num" }, [c2.model_id || c2.alias]),
                    el("div", {}, [c2.content]),
                  ])),
                ]);
                return det;
              })() : null,
            ]));
          }
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
