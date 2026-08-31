// P1 预置组件库：组件协议信封 → DOM。
// 语义层+结构层在此实现；业务文案来自卡片配置/preset；视觉全部走 design token。
// 任何渲染失败降级为纯文本（协议渲染兜底，§2.5），并上报 render_degraded。
window.Components = (function () {
  const { el } = UI;

  // 交互系统：1) 交互后出现「重置」（提交后随组件冻结）；2) 组件定高，超出折叠为「展开全部」
  function _enhance(node, envelope, ctx) {
    if (!node.classList || !node.classList.contains("comp")) return;
    if (envelope.semantic_category === "collect" && !node.classList.contains("comp-bare")) {
      const resetBtn = el("button", { class: "comp-reset", type: "button", title: "清除已填内容，恢复初始状态" }, ["重置"]);
      resetBtn.onclick = (e) => {
        e.stopPropagation();
        envelope._submitted = false;
        envelope._resetRender = true;
        const fresh = render(envelope, ctx);
        node.replaceWith(fresh);
      };
      const sb = node.querySelector(".submit-bar, .cm-total");
      if (sb) { resetBtn.classList.add("inbar"); sb.appendChild(resetBtn); }
      else node.appendChild(resetBtn);
      const markDirty = (e) => {
        if (envelope._submitted) return;
        if (e.target.closest && e.target.closest(".comp-reset")) return;
        node.classList.add("dirty");
      };
      node.addEventListener("input", markDirty, true);
      node.addEventListener("change", markDirty, true);
      node.addEventListener("click", (e) => {
        if (e.target.closest && (e.target.closest("button, .opt-item, .opt-card, .loc-row, .quick-chip"))) markDirty(e);
      }, true);
    }
    // 定高：内容超过阈值折叠，点「展开全部」放开
    if (ctx && ctx.preview) return; // 预览环境完整展示，不折叠
    const CLAMP = 340;
    const tryClamp = () => {
      if (node._expanded || node.classList.contains("clamped")) return;
      if (node.scrollHeight > CLAMP + 48) {
        node.classList.add("clamped");
        const bar = el("button", { class: "comp-expand", type: "button" }, ["展开全部"]);
        bar.onclick = () => { node._expanded = true; node.classList.remove("clamped"); bar.remove(); };
        node.appendChild(bar);
      }
    };
    requestAnimationFrame(() => setTimeout(tryClamp, 60));
  }

  function render(envelope, ctx) {
    try {
      const fn = RENDERERS[envelope.component_type];
      if (!fn) throw new Error("unsupported component_type");
      const node = fn(envelope, ctx);
      // 溯源信息：每个由配置触发的组件都带配置 ID 与版本（后台按此统计），dataset 供埋点与自动化测试定位
      node.dataset.componentType = envelope.component_type;
      node.dataset.renderId = envelope.render_id || "";
      if (envelope.card_ref?.card_id && !ctx.preview) {
        node.dataset.cardId = envelope.card_ref.card_id;
        node.dataset.cardVersion = String(envelope.card_ref.version ?? "");
        node.appendChild(el("div", { class: "comp-meta" }, [
          el("span", { class: "num", title: "配置 ID（完整）：" + envelope.card_ref.card_id },
            ["配置 " + String(envelope.card_ref.card_id).slice(0, 8) + " · v" + (envelope.card_ref.version ?? "-")]),
        ]));
      }
      if (!envelope._resetRender) ctx.sendEvent && ctx.sendEvent("card_rendered", envelope, {});
      envelope._resetRender = false;
      trackFirstInteraction(node, envelope, ctx);
      // 层级并入：组件文案（标题）由对话流气泡承载，白卡内只留纯交互内容
      if (ctx.titleInBubble) node.querySelectorAll(":scope > .comp-title").forEach(t => t.remove());
      _enhance(node, envelope, ctx);
      return node;
    } catch (err) {
      ctx.sendEvent && ctx.sendEvent("render_degraded", envelope, { reason: String(err) });
      return el("div", { class: "comp comp-degraded" }, [
        el("div", { class: "muted" }, ["组件渲染降级为文本"]),
        el("div", {}, [envelope.degraded_text || "（无内容）"]),
      ]);
    }
  }

  function trackFirstInteraction(node, envelope, ctx) {
    const renderTs = Date.now();
    envelope._renderTs = renderTs;
    let fired = false;
    node.addEventListener("pointerdown", () => {
      if (fired || !ctx.sendEvent) return;
      fired = true;
      ctx.sendEvent("card_interaction_started", envelope, { time_to_interact_ms: Date.now() - renderTs });
    }, { capture: true });
  }

  const CT_LABEL = {
    "select.single": "文本选择", "select.multi": "文本选择", "select.card": "卡片选择", "scale.likert": "评分",
    "matrix.compare+select": "对比选择", "form.structured": "信息登记", "input.followup": "备注填写", "slider.range": "数值选择",
    "picker.datetime": "日期选择", "picker.timerange": "时间段选择", "picker.location": "地址卡片", "rank.priority": "优先级排序", "upload.file": "文件上传", "upload.image": "图片上传", "suggest.followup": "追问引导", "commerce.order": "商品下单", "entry.link": "入口跳转", "track.map": "物流轨迹",
    "control.confirm": "操作确认",
  };

  function compCard(children, cls = "") {
    // brand-scope：品牌 design token 的作用域（导入品牌风格只改组件）
    return el("div", { class: "comp brand-scope " + cls }, children);
  }
  function compTitle(text) {
    return text ? el("div", { class: "comp-title" }, [text]) : null;
  }

  // ================= 呈现型 =================

  function rText(env) {
    const p = env.params;
    const toneColor = { positive: "var(--success)", negative: "var(--danger)", neutral: "var(--text-primary)" }[p.tone || "neutral"];
    return compCard([
      p.caption ? el("div", { class: "muted" }, [p.caption]) : null,
      el("div", { style: `font-size:var(--font-hero);font-weight:600;color:${toneColor}` },
        [String(p.value) + (p.unit ? " " + p.unit : "")]),
    ]);
  }

  function rMetric(env) {
    const p = env.params;
    const deltaStr = p.delta != null ? String(p.delta) : null;
    const up = deltaStr && !deltaStr.startsWith("-");
    return compCard([
      el("div", { class: "muted" }, [p.label || ""]),
      el("div", { style: "display:flex;align-items:baseline;gap:10px" }, [
        el("span", { style: "font-size:var(--font-hero);font-weight:600" }, [String(p.value)]),
        p.unit ? el("span", { class: "secondary" }, [p.unit]) : null,
        deltaStr ? el("span", { style: `font-size:var(--font-small);color:${up ? "#006300" : "var(--danger)"}` },
          [(up ? "上升 " : "下降 ") + deltaStr.replace("-", "")]) : null,
      ]),
      p.baseline ? el("div", { class: "muted" }, ["基线：" + p.baseline]) : null,
    ]);
  }

  function rListOrdered(env) {
    return compCard([
      compTitle(env.params.title),
      el("ol", { style: "padding-left:20px" }, (env.params.items || []).map(i => el("li", {}, [String(i)]))),
    ]);
  }

  function rTimeline(env) {
    const p = env.params;
    return compCard([
      compTitle(p.title),
      el("div", {}, (p.events || []).map(e =>
        el("div", { style: "display:flex;gap:12px;padding:6px 0;border-left:2px solid var(--primary);padding-left:12px;margin-left:4px" }, [
          el("div", { class: "muted", style: "min-width:48px;font-variant-numeric:tabular-nums" }, [e.ts || ""]),
          el("div", {}, [
            el("div", { style: "font-weight: 600" }, [e.title || ""]),
            e.desc ? el("div", { class: "muted" }, [e.desc]) : null,
          ]),
        ]))),
    ]);
  }

  function rSteps(env) {
    const p = env.params;
    const cur = p.current_index || 0;
    return compCard([
      compTitle(p.title),
      el("div", { style: "display:flex;gap:6px;flex-wrap:wrap" }, (p.steps || []).map((s, i) =>
        el("span", { class: "chip " + (i < cur ? "green" : i === cur ? "blue" : "gray") },
          [`${i + 1}. ${s}`]))),
    ]);
  }

  function rTable(env) {
    const p = env.params;
    return compCard([
      compTitle(p.title),
      el("div", { style: "overflow-x:auto" }, [
        el("table", { class: "data" }, [
          el("thead", {}, [el("tr", {}, (p.columns || []).map(c => el("th", {}, [String(c)])))]),
          el("tbody", {}, (p.rows || []).map(r => el("tr", {}, r.map(c => el("td", {}, [String(c)]))))),
        ]),
      ]),
    ]);
  }

  function rChartLine(env) {
    const p = env.params;
    const box = compCard([compTitle(p.title)]);
    const chartBox = el("div", {});
    box.appendChild(chartBox);
    requestAnimationFrame(() => UI.lineChart(chartBox, {
      series: p.series || [], labels: p.x_axis || [], unit: p.unit || "",
    }));
    return box;
  }

  function rChartBar(env) {
    const p = env.params;
    const box = compCard([compTitle(p.title)]);
    const chartBox = el("div", {});
    box.appendChild(chartBox);
    const series0 = (p.series || [])[0] || { values: [] };
    requestAnimationFrame(() => UI.barChart(chartBox, {
      categories: p.categories || [], values: series0.values || [], unit: p.unit || "",
    }));
    return box;
  }

  function rChartPie(env) {
    // 部分与整体：类别 <= 7；用水平百分比条实现（比扇形更易读，同样表达占比）
    const p = env.params;
    const slices = (p.slices || []).slice(0, 7);
    const total = slices.reduce((s, x) => s + x.value, 0) || 1;
    const pal = Brand.chartPalette().categorical;
    return compCard([
      compTitle(p.title),
      el("div", { style: "display:flex;height:16px;border-radius:4px;overflow:hidden;gap:2px" },
        slices.map((s, i) => el("div", {
          style: `width:${(s.value / total * 100)}%;background:${pal[i % pal.length]}`,
          title: `${s.label}: ${(s.value / total * 100).toFixed(1)}%`,
        }))),
      el("div", { style: "display:flex;gap:12px;flex-wrap:wrap;margin-top:6px" },
        slices.map((s, i) => el("span", { class: "muted", style: "display:flex;align-items:center;gap:4px" }, [
          el("span", { style: `width:9px;height:9px;border-radius:2px;background:${pal[i % pal.length]};display:inline-block` }),
          `${s.label} ${(s.value / total * 100).toFixed(0)}%`,
        ]))),
    ]);
  }

  function matrixTable(p, interactive, onPick, picked) {
    // 表格对比工具：整行可点选择；紧凑列宽 + 单元格省略，窄容器不再乱换行
    const dims = p.dimensions || [];
    const head = el("tr", {}, [
      el("th", { class: "mx-name" }, ["方案"]),
      ...dims.map(d => el("th", { class: "mx-v", title: d }, [d])),
      el("th", { class: "mx-sum" }, ["综合"]),
    ]);
    const sums = (p.options || []).map((_, i) => (p.values[i] || []).reduce((a2, b2) => a2 + (Number(b2) || 0), 0));
    const best = Math.max(...sums, 0);
    const rows = (p.options || []).map((opt, i) => {
      const vals = p.values[i] || [];
      const isRec = p.recommended_default === opt || p.recommended === opt;
      const isOn = picked === opt;
      return el("tr", {
        class: "mx-row" + (isOn ? " on" : "") + (interactive ? " pick" : ""),
        ...(interactive ? { onclick: () => onPick(opt), role: "radio", "aria-checked": isOn ? "true" : "false", tabindex: "0",
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(opt); } } } : {}),
      }, [
        el("td", { class: "mx-name" }, [
          interactive ? selDot(isOn, false) : null,
          el("span", { class: "mx-opt", title: opt }, [opt]),
          isRec ? el("span", { class: "chip blue", style: "flex:none" }, ["推荐"]) : null,
        ]),
        ...dims.map((d, j) => el("td", { class: "mx-v num" }, [vals[j] == null ? "-" : String(vals[j])])),
        el("td", { class: "mx-sum num" + (sums[i] === best && best > 0 ? " best" : "") }, [sums[i] ? sums[i].toFixed(1) : "-"]),
      ]);
    });
    return el("table", { class: "data mx-table" }, [el("thead", {}, [head]), el("tbody", {}, rows)]);
  }

  function rMatrixCompare(env) {
    return compCard([compTitle(env.params.title || "方案对比"), matrixTable(env.params, false)]);
  }

  function rFlowReasoning(env) {
    const p = env.params;
    return compCard([
      compTitle(p.title || "执行过程"),
      el("div", {}, (p.nodes || []).map(n =>
        el("div", { style: "display:flex;gap:10px;padding:4px 0" }, [
          el("span", { class: "chip " + (n.status === "done" ? "green" : n.status === "active" ? "blue" : "gray") },
            [n.label || n.id]),
          n.desc ? el("span", { class: "secondary" }, [n.desc]) : null,
        ]))),
    ]);
  }

  function rCitation(env) {
    const p = env.params;
    return compCard([
      el("div", { style: "font-weight: 600;margin-bottom:8px" }, [p.claim || ""]),
      ...(p.sources || []).map(s => el("div", {
        style: "border-left:3px solid var(--primary);padding:6px 10px;margin:6px 0;background:var(--bg-page);border-radius:0 var(--radius-control) var(--radius-control) 0",
      }, [
        el("div", { style: "display:flex;justify-content:space-between;gap:8px" }, [
          el("a", { href: s.url || "#", onclick: (e) => e.preventDefault(), style: "font-weight: 600" }, [s.title || "来源"]),
          s.confidence != null ? el("span", { class: "chip gray" }, ["置信 " + Math.round(s.confidence * 100) + "%"]) : null,
        ]),
        s.snippet ? el("div", { class: "muted" }, [s.snippet]) : null,
      ])),
    ], "comp-citation");
  }

  // ================= 采集型 =================

  function submitBar(env, ctx, getPayload, validate) {
    const bar = el("div", { class: "submit-bar" });
    const btn = el("button", {
      class: "btn primary", onclick: () => {
        const err = validate && validate();
        if (err) { UI.toast(err, true); return; }
        const payload = getPayload();
        payload.time_to_submit_ms = Date.now() - (env._renderTs || Date.now());
        env._submitted = true;
        // 提交即终态：冻结组件内全部交互控件，避免"已提交但还能改"的状态错觉
        const root = btn.closest(".comp");
        if (root) root.querySelectorAll("button, input, textarea, select").forEach(n => n.disabled = true);
        btn.textContent = "已提交";
        ctx.onCollectSubmit(payload, env);
        // 群体回显开关：提交完成后显示其他人的选择情况
        if (env.params.echo_results && env.card_ref?.card_id) {
          showEcho(env, bar, payload.user_selection);
        }
      },
    }, [env.params.submit_label || "提交"]);
    bar.appendChild(btn);
    return bar;
  }

  // 回显：提交后展示群体分布。单面板：一句结论 + 细分布条，自己的选择用主色标出
  async function showEcho(env, container, ownSelection) {
    const box = el("div", { class: "echo-panel" }, [el("div", { class: "muted" }, ["正在汇总大家的回答……"])]);
    // 设计系统：回显是独立区块，插在提交栏上方；按钮行保持在底部（已提交禁用态）
    if (container.parentNode) container.parentNode.insertBefore(box, container);
    else container.appendChild(box);
    await new Promise(r => setTimeout(r, 700)); // 等自己的提交事件先入库
    let d;
    try {
      d = await UI.api(`/v1/cards/${env.card_ref.card_id}/responses`);
    } catch (e) {
      // 回显被关闭（服务端 403）→ 静默收起，不打扰用户
      if (e.status === 403) { box.remove(); return; }
      box.innerHTML = "";
      box.appendChild(el("div", { class: "muted" }, ["暂时看不到大家的回答，稍后可在看板查看"]));
      return;
    }
    try {
      box.innerHTML = "";
      const own = Array.isArray(ownSelection) ? ownSelection.map(String) : [String(ownSelection)];
      const dist = { ...d.distribution };
      const serverHasOwn = d.total_submissions > 0 && own.every(o => (dist[o] || 0) > 0);
      if (!serverHasOwn) own.forEach(o => { if (o !== "null" && o !== "undefined") dist[o] = (dist[o] || 0) + 1; });
      const entries = Object.entries(dist).sort((a, b) => b[1] - a[1]);
      const total = entries.reduce((s, [, n]) => s + n, 0) || 1;
      const ownCount = own.reduce((s, o) => s + (dist[o] || 0), 0);
      const ownPct = Math.round(ownCount / total * 100);
      const people = Math.max(d.respondents, 1);
      let headline;
      if (total <= 1) headline = "你是第一个回答的人";
      else if (ownCount <= 1) headline = `${people} 人回答 · 你的选择比较独特`;
      else headline = `${people} 人回答 · ${ownPct}% 和你选了一样`;
      box.appendChild(el("div", { class: "echo-head" }, [headline]));
      entries.forEach(([opt, n]) => {
        const isOwn = own.includes(opt);
        box.appendChild(el("div", { class: "echo-row" + (isOwn ? " own" : "") }, [
          el("span", { class: "echo-name", title: opt }, [opt, isOwn ? UI.icon("check", 12) : null]),
          el("span", { class: "echo-track" }, [el("span", { class: "echo-fill", style: `width:${Math.max(3, n / total * 100)}%` })]),
          el("span", { class: "echo-pct num" }, [`${Math.round(n / total * 100)}%`]),
        ]));
      });
      if (!entries.length && d.recent_texts?.length) {
        box.appendChild(el("div", { class: "echo-head" }, [`${people} 人回答 · 大家这样说`]));
        d.recent_texts.slice(0, 3).forEach(t2 => box.appendChild(el("div", { class: "echo-quote" }, [t2])));
      }
      if (!entries.length && !d.recent_texts?.length) {
        box.appendChild(el("div", { class: "muted" }, ["你是第一个回答的人"]));
      }
    } catch (e) {
      box.innerHTML = "";
      box.appendChild(el("div", { class: "muted" }, ["暂时看不到大家的回答，稍后可在看板查看"]));
    }
  }

  function emptyState(env) {
    return env.params.empty_state
      ? el("div", { class: "empty-state", style: "padding:16px" }, [env.params.empty_state]) : null;
  }

  // ---------- 采集组件显示样式变体（params.display，默认取各自第一种） ----------
  function selDot(on, multi) {
    return el("span", { class: "sel-dot" + (on ? " on" : "") + (multi ? " sq" : "") }, on ? [UI.icon("check", 12)] : []);
  }

  function optionList(opts, p, multi, getPicked, setPicked) {
    const rows = opts.map(o => {
      const row = el("button", { class: "opt-item", type: "button", role: multi ? "checkbox" : "radio", "aria-checked": "false" }, [
        selDot(false, multi),
        el("span", { class: "opt-text" }, [o]),
        o === p.recommended_default ? el("span", { class: "chip blue", style: "flex:none" }, ["推荐"]) : null,
      ]);
      row._opt = o;
      row.onclick = () => {
        setPicked(o);
        rows.forEach(r => {
          const on = multi ? getPicked().has(r._opt) : getPicked() === r._opt;
          r.classList.toggle("on", on);
          r.setAttribute("aria-checked", on ? "true" : "false");
          r.replaceChild(selDot(on, multi), r.firstChild);
        });
      };
      return row;
    });
    return el("div", { class: "opt-list", role: multi ? "group" : "radiogroup" }, rows);
  }

  function rSelectSingle(env, ctx) {
    const p = env.params;
    const opts = p.options || [];
    if (!opts.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const display = p.display || "list";
    let picked = null;
    let body;
    if (display === "inline" || display === "composer") {
      const btns = opts.map(o => el("button", {
        class: "btn opt", type: "button", role: "radio", "aria-checked": "false",
        onclick: (e) => { picked = o;
          btns.forEach(b => { b.classList.remove("primary"); b.setAttribute("aria-checked", "false"); });
          e.currentTarget.classList.add("primary"); e.currentTarget.setAttribute("aria-checked", "true"); },
      }, [o + (o === p.recommended_default ? "（推荐）" : "")]));
      body = el("div", { class: display === "composer" ? "quick-float" : "opt-row" }, btns);
    } else if (display === "card") {
      const meta = p.option_meta || {};
      const cards = opts.map(o => {
        const m = meta[o] || {};
        const node = el("div", { class: "opt-card", role: "radio", "aria-checked": "false" }, [
          m.image ? el("img", { src: m.image, alt: o }) : (m.desc ? el("div", { class: "img-ph" }, [o.slice(0, 1)]) : null),
          el("div", { style: "font-weight:600" }, [o + (o === p.recommended_default ? "（推荐）" : "")]),
          m.desc ? el("div", { class: "muted" }, [m.desc]) : null,
        ]);
        node.onclick = () => {
          picked = o;
          cards.forEach(c => { c.classList.remove("on"); c.setAttribute("aria-checked", "false"); });
          node.classList.add("on");
          node.setAttribute("aria-checked", "true");
        };
        return node;
      });
      body = el("div", { class: "opt-row", style: "gap:10px" }, cards);
    } else {
      body = optionList(opts, p, false, () => picked, (o) => picked = o);
    }
    return compCard([
      compTitle(p.prompt),
      body,
      submitBar(env, ctx, () => ({
        options_offered: opts, recommended_default: p.recommended_default || null,
        user_selection: picked, modified_from_default: picked !== p.recommended_default,
      }), () => picked == null ? "请先选择一项" : null),
    ]);
  }

  function rSelectMulti(env, ctx) {
    const p = env.params;
    const opts = p.options || [];
    if (!opts.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const display = p.display || "list";
    const picked = new Set();
    let body;
    if (display === "inline" || display === "composer") {
      body = el("div", { class: display === "composer" ? "quick-float" : "opt-row" }, opts.map(o => el("button", {
        class: "btn opt", type: "button", role: "checkbox", "aria-checked": "false",
        onclick: (e) => {
          if (picked.has(o)) { picked.delete(o); e.currentTarget.classList.remove("primary"); e.currentTarget.setAttribute("aria-checked", "false"); }
          else { picked.add(o); e.currentTarget.classList.add("primary"); e.currentTarget.setAttribute("aria-checked", "true"); }
        },
      }, [o])));
    } else if (display === "card") {
      const meta = p.option_meta || {};
      const cards = opts.map(o => {
        const m = meta[o] || {};
        const node = el("div", { class: "opt-card", role: "checkbox", "aria-checked": "false" }, [
          m.image ? el("img", { src: m.image, alt: o }) : (m.desc ? el("div", { class: "img-ph" }, [o.slice(0, 1)]) : null),
          el("div", { style: "font-weight:600" }, [o]),
          m.desc ? el("div", { class: "muted" }, [m.desc]) : null,
        ]);
        node.onclick = () => {
          picked.has(o) ? picked.delete(o) : picked.add(o);
          const on = picked.has(o);
          node.classList.toggle("on", on);
          node.setAttribute("aria-checked", on ? "true" : "false");
        };
        return node;
      });
      body = el("div", { class: "opt-row", style: "gap:10px" }, cards);
    } else {
      body = optionList(opts, p, true, () => picked, (o) => { picked.has(o) ? picked.delete(o) : picked.add(o); });
    }
    return compCard([
      compTitle(p.prompt),
      body,
      submitBar(env, ctx, () => ({
        options_offered: opts, user_selection: [...picked],
      }), () => picked.size === 0 ? "请至少选择一项" : null),
    ]);
  }

  function rSelectCard(env, ctx) {
    const p = env.params;
    const opts = p.options || [];
    if (!opts.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const display = p.display || "card";
    const names = opts.map(o => typeof o === "object" ? o.label : o);
    let picked = null;
    let body;
    if (display === "list") {
      body = optionList(names, p, false, () => picked, (o) => picked = o);
    } else {
      const meta = p.option_meta || {};
      const cards = opts.map(o => {
        const isObj = typeof o === "object";
        const name = isObj ? o.label : o;
        const m = meta[name] || {};
        const desc = m.desc || (isObj ? o.desc : null);
        const node = el("div", {
          class: "opt-card" + (m.image ? " media" : ""), role: "radio", "aria-checked": "false",
          onclick: () => {
            picked = name;
            cards.forEach(c => { c.classList.remove("on"); c.setAttribute("aria-checked", "false"); });
            node.classList.add("on");
            node.setAttribute("aria-checked", "true");
          },
        }, [
          m.image ? el("img", { src: m.image, alt: name }) : null,
          el("div", { class: "oc-name" }, [name + (name === p.recommended_default ? "（推荐）" : "")]),
          desc ? el("div", { class: "oc-desc" }, [desc]) : null,
        ]);
        return node;
      });
      body = el("div", { class: "opt-row", style: "gap:10px" }, cards);
    }
    return compCard([
      compTitle(p.prompt),
      body,
      submitBar(env, ctx, () => ({
        options_offered: names,
        recommended_default: p.recommended_default || null,
        user_selection: picked, modified_from_default: picked !== p.recommended_default,
      }), () => picked == null ? "请先选择一项" : null),
    ]);
  }

  function rSlider(env, ctx) {
    const p = env.params;
    const min = p.min ?? 0, max = p.max ?? 100;
    const init = p.recommended_default ?? Math.round((min + max) / 2);
    const display = p.display || "slider";
    let getVal;
    let body;
    if (display === "stepper") {
      let v = init;
      const step = Math.max(1, Math.round((max - min) / 20));
      const valEl = el("span", { class: "val" }, [String(v) + (p.unit ? " " + p.unit : "")]);
      const draw = () => valEl.textContent = String(v) + (p.unit ? " " + p.unit : "");
      body = el("div", { class: "stepper" }, [
        el("button", { class: "btn", type: "button", "aria-label": "减少", onclick: () => { v = Math.max(min, v - step); draw(); } }, ["-"]),
        valEl,
        el("button", { class: "btn", type: "button", "aria-label": "增加", onclick: () => { v = Math.min(max, v + step); draw(); } }, ["+"]),
        el("span", { class: "muted" }, [`${min} ~ ${max}`]),
      ]);
      getVal = () => v;
    } else if (display === "input") {
      const num = el("input", { type: "number", min, max, value: init, style: "max-width:140px" });
      body = el("div", { style: "display:flex;align-items:center;gap:8px" }, [
        num, p.unit ? el("span", { class: "muted" }, [p.unit]) : null,
        el("span", { class: "muted" }, [`范围 ${min} ~ ${max}`]),
      ]);
      getVal = () => Math.min(max, Math.max(min, Number(num.value) || min));
    } else {
      const val = el("span", { style: "font-weight:600;font-variant-numeric:tabular-nums" }, [String(init)]);
      const slider = el("input", { type: "range", min, max, value: init, style: "width:100%" });
      const setFill = () => slider.style.setProperty("--fill", ((Number(slider.value) - min) / (max - min || 1) * 100) + "%");
      setFill();
      slider.oninput = () => { val.textContent = slider.value; setFill(); };
      body = el("div", { style: "display:flex;align-items:center;gap:12px" }, [slider, val, p.unit ? el("span", { class: "muted" }, [p.unit]) : null]);
      getVal = () => Number(slider.value);
    }
    return compCard([
      compTitle(p.prompt),
      body,
      submitBar(env, ctx, () => ({
        recommended_default: p.recommended_default ?? null, user_selection: getVal(),
        modified_from_default: getVal() !== p.recommended_default,
      })),
    ]);
  }

  function rForm(env, ctx) {
    const p = env.params;
    const fields = p.fields || [];
    const display = p.display || "stacked";
    const inputs = {};
    const errBoxes = {};
    const fieldNodes = fields.map(f => {
      const input = el("input", { type: f.type === "number" ? "number" : "text", placeholder: f.placeholder || "" });
      inputs[f.key] = input;
      const errBox = el("div", { class: "field-error" });
      errBoxes[f.key] = errBox;
      return el("label", { class: "field" }, [
        el("span", { class: "label-text" + (f.required ? " req" : "") }, [f.label]),
        input, errBox,
      ]);
    });
    return compCard([
      compTitle(p.prompt),
      display === "compact" ? el("div", { class: "form-grid" }, fieldNodes) : el("div", {}, fieldNodes),
      submitBar(env, ctx, () => {
        const values = {};
        fields.forEach(f => values[f.key] = inputs[f.key].value.trim());
        return { form_values: values, user_selection: JSON.stringify(values) };
      }, () => {
        let bad = null;
        fields.forEach(f => {
          errBoxes[f.key].textContent = "";
          if (f.required && !inputs[f.key].value.trim()) {
            errBoxes[f.key].textContent = "此项必填";
            bad = bad || "请补全必填字段";
          }
        });
        return bad;
      }),
    ]);
  }

  function rInputFollowup(env, ctx) {
    const p = env.params;
    const display = p.display || "box";
    const field = display === "mini"
      ? el("input", { type: "text", placeholder: p.placeholder || "补充说明" })
      : el("textarea", { rows: 3, placeholder: p.placeholder || "补充说明" });
    return compCard([
      compTitle(p.prompt),
      field,
      submitBar(env, ctx, () => ({ user_selection: field.value.trim(), text: field.value.trim() }),
        () => !field.value.trim() ? "请输入内容" : null),
    ]);
  }

  // 物流轨迹卡（呈现型）：查件时由模型数据驱动——地图定位当前位置 + 节点时间线
  function rTrackMap(env, ctx) {
    const p = env.params;
    const cur = p.current || {};
    const kids = [compTitle(p.title || "物流轨迹")];
    if (cur.lat && cur.lon) {
      const d = 0.03;
      const bbox = `${cur.lon - d},${cur.lat - d},${cur.lon + d},${cur.lat + d}`;
      kids.push(el("iframe", {
        class: "loc-map", loading: "lazy", referrerpolicy: "no-referrer",
        title: "当前位置：" + (cur.name || ""),
        src: `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${cur.lat},${cur.lon}`,
      }));
    }
    kids.push(el("div", { class: "trk-cur" }, [
      el("div", { class: "loc-main" }, [
        el("div", { class: "loc-name" }, [cur.name || "运输中",
          cur.status_text ? el("span", { class: "chip blue", style: "margin-left:6px" }, [cur.status_text]) : null]),
        cur.addr ? el("div", { class: "loc-addr" }, [cur.addr]) : null,
      ]),
      cur.updated_text ? el("span", { class: "trk-upd num" }, [cur.updated_text]) : null,
    ]));
    if ((p.nodes || []).length) {
      const tl = el("div", { class: "trk-list" });
      p.nodes.forEach(n => tl.appendChild(el("div", { class: "trk-node " + (n.state || "todo") }, [
        el("span", { class: "trk-dot" }),
        el("div", { class: "trk-body" }, [
          el("div", { class: "trk-text" }, [n.text]),
          n.time ? el("div", { class: "trk-time num" }, [n.time]) : null,
        ]),
      ])));
      kids.push(tl);
    }
    return compCard(kids);
  }

  // ---------- 补齐协议中定义但缺失的采集形式 ----------
  function _dstr(offset) {
    const d = new Date(Date.now() + offset * 86400000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function _quickChips(presets, onPick) {
    const box = el("div", { class: "quick-chips" });
    presets.forEach(pr => box.appendChild(el("button", { type: "button", class: "quick-chip", onclick: (e) => {
      onPick(pr);
      box.querySelectorAll(".quick-chip").forEach(x => x.classList.remove("on"));
      e.currentTarget.classList.add("on");
    } }, [pr[0]])));
    return box;
  }
  function rPickerDatetime(env, ctx) {
    const p = env.params;
    const withTime = (p.display || "date") === "datetime";
    const input = el("input", { type: withTime ? "datetime-local" : "date", style: "max-width:240px" });
    // 快捷选项：多数人约的就是这三天
    const quick = withTime ? null : _quickChips([["今天", 0], ["明天", 1], ["后天", 2]], pr => { input.value = _dstr(pr[1]); });
    return compCard([
      compTitle(p.prompt),
      quick,
      input,
      submitBar(env, ctx, () => ({ user_selection: input.value }),
        () => !input.value ? (withTime ? "请选择日期和时间" : "请选择日期") : null),
    ]);
  }

  // 地址库（演示数据：供应链基地常用节点，含真实坐标供地图展示）
  const ADDRESS_BOOK = [
    { name: "上海仓", addr: "上海市浦东新区外高桥保税区基隆路 1 号", lat: 31.353, lon: 121.588 },
    { name: "宁波舟山港", addr: "浙江省宁波市北仑区港区大道", lat: 29.935, lon: 121.844 },
    { name: "青岛港", addr: "山东省青岛市市北区港青路 6 号", lat: 36.088, lon: 120.316 },
    { name: "深圳盐田港", addr: "广东省深圳市盐田区明珠道", lat: 22.577, lon: 114.271 },
    { name: "天津港", addr: "天津市滨海新区新港二号路", lat: 39.003, lon: 117.712 },
    { name: "广州南沙港", addr: "广州市南沙区港前大道南", lat: 22.759, lon: 113.607 },
    { name: "成都青白江铁路港", addr: "成都市青白江区香岛大道 1 号", lat: 30.783, lon: 104.250 },
    { name: "武汉阳逻港", addr: "武汉市新洲区平江东路", lat: 30.706, lon: 114.550 },
  ];
  function rPickerLocation(env, ctx) {
    // 地址卡片：商家在配置里选好的地址（含详址）直接推给用户确认——平台不读取用户本地地址
    const p = env.params;
    const names = (p.options || []).length ? p.options : ADDRESS_BOOK.slice(0, 3).map(x => x.name);
    const cards = names.map(n => ADDRESS_BOOK.find(bk => bk.name === n) || { name: n, addr: "" });
    let picked = null;
    const listBox = el("div", { class: "addr-list" });
    const filter = cards.length > 4 ? el("input", { type: "text", placeholder: "筛选地址" }) : null;
    function draw() {
      listBox.innerHTML = "";
      const kw = (filter?.value || "").trim().toLowerCase();
      cards.filter(x => !kw || (x.name + x.addr).toLowerCase().includes(kw)).forEach(x => {
        const isOn = picked && picked.name === x.name;
        listBox.appendChild(el("div", { class: "addr-card" + (isOn ? " on" : ""), role: "radio",
          "aria-checked": isOn ? "true" : "false", tabindex: "0",
          onclick: () => { picked = x; draw(); },
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picked = x; draw(); } } }, [
          selDot(isOn, false),
          el("div", { class: "loc-main" }, [
            el("div", { class: "loc-name" }, [x.name]),
            x.addr ? el("div", { class: "loc-addr" }, [x.addr]) : null,
          ]),
        ]));
      });
    }
    if (filter) filter.oninput = draw;
    draw();
    return compCard([
      compTitle(p.prompt),
      filter,
      listBox,
      el("div", { class: "muted" }, ["地址不对？直接在对话里说明，客服会为你修改"]),
      submitBar(env, ctx, () => ({ options_offered: names, user_selection: picked?.name || "",
        ...(picked?.addr ? { address_detail: picked.addr } : {}) }),
        () => !picked ? "请选择一个地址" : null),
    ]);
  }

  // 商品下单器：图 / 详情 / 销量 / 标签 / 价格与折扣 + 「去下单」转数量步进 + 合计
  function rCommerce(env, ctx) {
    const p = env.params;
    const items = (p.options || []).map(name => ({ name, ...(p.option_meta || {})[name], qty: 0 }));
    if (!items.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const fmt = v => "¥" + (Number(v) || 0).toLocaleString("zh-CN");
    const listBox = el("div", {});
    const totalBar = el("div", { class: "cm-total" });
    function doSubmit(btn) {
      const picked = items.filter(it => it.qty > 0);
      if (!picked.length) { UI.toast("请先选择商品", true); return; }
      const payload = {
        user_selection: picked.map(it => it.name),
        order_items: picked.map(it => ({ name: it.name, qty: it.qty, price: Number(it.price) || 0 })),
        order_total: picked.reduce((s2, it) => s2 + it.qty * (Number(it.price) || 0), 0),
        time_to_submit_ms: Date.now() - (env._renderTs || Date.now()),
      };
      env._submitted = true;
      const root = totalBar.closest(".comp");
      if (root) root.querySelectorAll("button, input, textarea, select").forEach(n => n.disabled = true);
      btn.textContent = "已提交";
      ctx.onCollectSubmit(payload, env);
      if (env.params.echo_results && env.card_ref?.card_id) showEcho(env, totalBar, payload.user_selection);
    }
    function draw() {
      listBox.innerHTML = "";
      items.forEach(it => {
        const price = Number(it.price) || 0;
        const orig = Number(it.price_original) || 0;
        const discount = orig > price && orig > 0 ? (price / orig * 10).toFixed(1).replace(/\.0$/, "") + "折" : null;
        let act;
        if (!it.qty) {
          act = el("button", { class: "cm-order", type: "button",
            onclick: () => { it.qty = 1; draw(); } }, ["去下单"]);
        } else {
          const minus = el("button", { type: "button", class: "cm-step", "aria-label": "减少",
            onclick: () => { it.qty = Math.max(0, it.qty - 1); draw(); } }, ["-"]);
          const plus = el("button", { type: "button", class: "cm-step", "aria-label": "增加",
            onclick: () => { it.qty += 1; draw(); } }, ["+"]);
          act = el("span", { class: "cm-qty" }, [minus, el("span", { class: "cm-qty-n num" }, [String(it.qty)]), plus]);
        }
        listBox.appendChild(el("div", { class: "cm-item" + (it.qty ? " on" : "") }, [
          it.image
            ? el("img", { class: "cm-img", src: it.image, alt: it.name })
            : el("span", { class: "cm-img ph" }, [UI.icon("layers", 26)]),
          el("div", { class: "cm-main" }, [
            el("div", { class: "cm-name" }, [it.name]),
            (it.desc && !it.sales && !(it.tags || []).length) ? el("div", { class: "cm-desc" }, [it.desc]) : null,
            el("div", { class: "cm-meta" }, [
              it.sales ? el("span", { class: "cm-sales num" }, [`已售 ${Number(it.sales) >= 10000 ? (it.sales / 10000).toFixed(1) + "w+" : it.sales}`]) : null,
              ...(it.tags || []).map(t => el("span", { class: "cm-tag" }, [t])),
            ]),
            el("div", { class: "cm-price-row" }, [
              el("span", { class: "cm-price num" }, [el("span", { class: "cm-yen" }, ["¥"]), String(price)]),
              orig > price ? el("span", { class: "cm-orig num" }, [fmt(orig)]) : null,
              discount ? el("span", { class: "cm-discount" }, [discount]) : null,
              el("span", { style: "flex:1" }),
              act,
            ]),
          ]),
        ]));
      });
      const count = items.reduce((s2, it) => s2 + it.qty, 0);
      const total = items.reduce((s2, it) => s2 + it.qty * (Number(it.price) || 0), 0);
      totalBar.innerHTML = "";
      if (count > 0 && !env._submitted) {
        const sb = el("button", { class: "cm-submit", type: "button" }, [env.params.submit_label || "提交订单"]);
        sb.onclick = () => doSubmit(sb);
        totalBar.append(
          el("span", { class: "muted" }, [`共 ${count} 件`]),
          el("span", { class: "cm-total-val num" }, [`合计 ${fmt(total)}`]),
          sb);
      }
    }
    draw();
    return compCard([
      compTitle(p.prompt),
      listBox,
      totalBar,
    ]);
  }

  // 入口跳转器（已合并追问引导）：不配链接 = 追问发给 AI，配链接 = 直达页面；可回显「多少人点开」
  function rEntryLink(env, ctx) {
    const p = env.params;
    const opts = p.options || [];
    if (!opts.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const meta = p.option_meta || {};
    const acts = p.option_actions || {};
    let clicked = null;
    const listBox = el("div", { class: "el-list" });
    function draw() {
      listBox.innerHTML = "";
      opts.forEach(o => {
        const m = meta[o] || {};
        const act = acts[o] || {};
        const btn = el("button", { type: "button", class: "el-item" + (clicked === o ? " on" : ""),
          title: act.api ? "打开：" + act.api : "继续向 AI 追问",
          onclick: () => {
            if (clicked) return;
            clicked = o;
            env._submitted = true;
            draw();
            if (act.api) window.open(act.api, "_blank", "noopener");
            ctx.onCollectSubmit && ctx.onCollectSubmit({ user_selection: o, followup_kind: act.api ? "link" : "ask" }, env);
            if (p.echo_results && env.card_ref?.card_id) {
              const marker = el("div", {});
              listBox.parentNode && listBox.parentNode.insertBefore(marker, listBox.nextSibling);
              showEcho(env, marker, o);
            }
          } }, [
          m.image
            ? el("img", { class: "el-ico", src: m.image, alt: "" })
            : el("span", { class: "el-ico ph" }, [o.slice(0, 1)]),
          el("span", { class: "el-main" }, [
            el("span", { class: "el-name" }, [o]),
            m.desc ? el("span", { class: "el-sub" }, [m.desc]) : null,
          ]),
          el("span", { class: "el-arrow" }, [UI.icon(act.api ? "link" : "arrow", 14)]),
        ]);
        if (clicked && clicked !== o) btn.disabled = true;
        listBox.appendChild(btn);
      });
    }
    draw();
    return compCard([p.prompt ? compTitle(p.prompt) : null, listBox], "comp-bare");
  }

  // 追问引导（兼容存量配置；新建请用入口跳转器）
  function rSuggestFollowup(env, ctx) {
    const p = env.params;
    const opts = p.options || [];
    if (!opts.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const acts = p.option_actions || {};
    let clicked = null;
    const listBox = el("div", { class: "fu-list" });
    function draw() {
      listBox.innerHTML = "";
      opts.forEach(o => {
        const act = acts[o] || {};
        const isLink = !!act.api;
        const btn = el("button", { type: "button", class: "fu-item" + (clicked === o ? " on" : ""),
          title: isLink ? "打开：" + act.api : "继续向 AI 追问",
          onclick: () => {
            if (clicked) return;
            clicked = o;
            env._submitted = true;
            draw();
            if (isLink) window.open(act.api, "_blank", "noopener");
            ctx.onCollectSubmit && ctx.onCollectSubmit({ user_selection: o, followup_kind: isLink ? "link" : "ask" }, env);
          } }, [
          el("span", { class: "fu-text" }, [o]),
          el("span", { class: "fu-ico" }, [UI.icon(isLink ? "link" : "arrow", 14)]),
        ]);
        if (clicked && clicked !== o) btn.disabled = true;
        listBox.appendChild(btn);
      });
    }
    draw();
    return compCard([compTitle(p.prompt || "你可能还想问"), listBox]);
  }

  function rRank(env, ctx) {
    const p = env.params;
    let order = [...(p.options || [])];
    if (!order.length) return compCard([compTitle(p.prompt), emptyState(env)]);
    const listBox = el("div", { class: "rank-list" });
    function getAfter(y) {
      const els = [...listBox.querySelectorAll(".rank-item:not(.dragging)")];
      return els.find(n => y <= n.getBoundingClientRect().top + n.offsetHeight / 2) || null;
    }
    function draw() {
      listBox.innerHTML = "";
      order.forEach((o, i) => {
        const item = el("div", { class: "rank-item", draggable: "true" }, [
          el("span", { class: "rank-no num" + (i === 0 ? " top" : "") }, [String(i + 1)]),
          el("span", { class: "opt-text" }, [o]),
          el("span", { class: "rank-drag", title: "按住拖动调整顺序" }, [UI.icon("drag", 14)]),
        ]);
        item.dataset.val = o;
        item.addEventListener("dragstart", (e) => {
          item.classList.add("dragging");
          e.dataTransfer.effectAllowed = "move";
          try { e.dataTransfer.setData("text/plain", o); } catch (err) {}
        });
        item.addEventListener("dragend", () => {
          item.classList.remove("dragging");
          order = [...listBox.querySelectorAll(".rank-item")].map(n => n.dataset.val);
          draw();
        });
        listBox.appendChild(item);
      });
    }
    listBox.addEventListener("dragover", (e) => {
      e.preventDefault();
      const dragging = listBox.querySelector(".rank-item.dragging");
      if (!dragging) return;
      const after = getAfter(e.clientY);
      if (after == null) listBox.appendChild(dragging);
      else if (after !== dragging) listBox.insertBefore(dragging, after);
    });
    draw();
    return compCard([
      compTitle(p.prompt),
      el("div", { class: "muted" }, ["按住右侧把手拖动，按重要程度从上到下排序"]),
      listBox,
      submitBar(env, ctx, () => ({ options_offered: p.options, user_selection: order })),
    ]);
  }

  function rTimerange(env, ctx) {
    const p = env.params;
    const isTime = (p.display || "date") === "time";
    if (isTime) {
      // 左栏日期 / 右栏时段（参考电商取件时间选择）：过期时段置灰，点选即定
      const now = new Date();
      const DAYS = [0, 1, 2, 3, 4].map(off => {
        const d = new Date(Date.now() + off * 86400000);
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        const label = off === 0 ? "今天" : off === 1 ? "明天" : off === 2 ? "后天" : `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return { off, iso, label };
      });
      const SLOTS = [["09:00", "11:00"], ["11:00", "13:00"], ["13:00", "15:00"], ["15:00", "17:00"], ["17:00", "19:00"]];
      let dayIdx = 0;
      let picked = null; // { iso, label, slot: "13:00-15:00" }
      const daysCol = el("div", { class: "ts-days" });
      const slotsCol = el("div", { class: "ts-slots" });
      function expired(day, slot) {
        if (day.off > 0) return false;
        const [eh, em] = slot[1].split(":").map(Number);
        return now.getHours() * 60 + now.getMinutes() >= eh * 60 + em;
      }
      function draw() {
        daysCol.innerHTML = "";
        DAYS.forEach((d, i) => daysCol.appendChild(el("button", { type: "button",
          class: "ts-day" + (i === dayIdx ? " on" : ""),
          onclick: () => { dayIdx = i; draw(); } }, [d.label])));
        slotsCol.innerHTML = "";
        const day = DAYS[dayIdx];
        SLOTS.forEach(slot => {
          const dead = expired(day, slot);
          const label = `${slot[0]} – ${slot[1]}`;
          const isOn = picked && picked.iso === day.iso && picked.slot === label;
          slotsCol.appendChild(el("button", { type: "button",
            class: "ts-slot" + (isOn ? " on" : ""), ...(dead ? { disabled: "" } : {}),
            onclick: () => { picked = { iso: day.iso, label: day.label, slot: label }; draw(); } },
            [el("span", {}, [label]), dead ? el("span", { class: "ts-dead" }, ["已过期"]) : (isOn ? UI.icon("check", 14) : null)]));
        });
      }
      draw();
      return compCard([
        compTitle(p.prompt),
        el("div", { class: "ts-wrap" }, [daysCol, slotsCol]),
        submitBar(env, ctx, () => ({ user_selection: `${picked.label}（${picked.iso}） ${picked.slot}` }),
          () => !picked ? "请选择一个时间段" : null),
      ]);
    }
    // 日期段：起止日期 + 快捷区间
    const a2 = el("input", { type: "date", "aria-label": "开始" });
    const b2 = el("input", { type: "date", "aria-label": "结束" });
    const quick = _quickChips(
      [["今明两天", _dstr(0), _dstr(1)], ["未来三天", _dstr(0), _dstr(2)], ["未来一周", _dstr(0), _dstr(6)]],
      pr => { a2.value = pr[1]; b2.value = pr[2]; });
    return compCard([
      compTitle(p.prompt),
      quick,
      el("div", { class: "range-row" }, [a2, el("span", { class: "muted" }, ["至"]), b2]),
      submitBar(env, ctx, () => ({ user_selection: `${a2.value} ~ ${b2.value}` }),
        () => (!a2.value || !b2.value) ? "请选择起止" : (a2.value > b2.value ? "结束不能早于开始" : null)),
    ]);
  }

  function _fileMeta(f) {
    // 隐私：不上传原始文件名（可能含手机号等），只记类型与大小
    const ext = (f.name.split(".").pop() || "").toLowerCase().slice(0, 8);
    const size = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + "MB" : Math.max(1, Math.round(f.size / 1024)) + "KB";
    return { ext, size };
  }
  function rUpload(env, ctx) {
    const p = env.params;
    const file = el("input", { type: "file", accept: ".pdf,.doc,.docx,.xls,.xlsx,.csv,.zip", style: "display:none" });
    const nameEl = el("span", { class: "muted" }, [p.placeholder || "支持 PDF / Word / Excel，演示环境不真实上传"]);
    file.onchange = () => { if (file.files[0]) { nameEl.textContent = file.files[0].name + "（仅本机可见，不上传文件名）"; nameEl.classList.remove("muted"); } };
    return compCard([
      compTitle(p.prompt),
      el("div", { style: "display:flex;align-items:center;gap:10px;flex-wrap:wrap" }, [
        el("button", { class: "btn small", type: "button", onclick: () => file.click() }, [UI.icon("upload", 14), "选择文件"]),
        nameEl, file,
      ]),
      submitBar(env, ctx, () => {
        const m = _fileMeta(file.files[0]);
        return { user_selection: `已上传文件（${m.ext} · ${m.size}）`, file_meta: m };
      }, () => !file.files[0] ? "请先选择文件" : null),
    ]);
  }

  function rUploadImage(env, ctx) {
    const p = env.params;
    const file = el("input", { type: "file", accept: "image/*", style: "display:none" });
    const thumb = el("div", { class: "img-thumb", style: "display:none" });
    const pickBtn = el("button", { class: "btn small", type: "button", onclick: () => file.click() }, [UI.icon("upload", 14), "拍照或选择图片"]);
    const hintEl = el("span", { class: "muted" }, [p.placeholder || "支持 JPG / PNG，演示环境不真实上传"]);
    file.onchange = () => {
      const f = file.files[0];
      if (!f) return;
      const rdr = new FileReader();
      rdr.onload = () => {
        thumb.style.display = "";
        thumb.innerHTML = "";
        thumb.appendChild(el("img", { src: rdr.result, alt: "图片预览" }));
        pickBtn.replaceChildren(UI.icon("refresh", 14), "更换图片");
        hintEl.textContent = "仅本机预览，不上传原图与文件名";
      };
      rdr.readAsDataURL(f);
    };
    return compCard([
      compTitle(p.prompt),
      thumb,
      el("div", { style: "display:flex;align-items:center;gap:10px;flex-wrap:wrap" }, [pickBtn, hintEl, file]),
      submitBar(env, ctx, () => {
        const m = _fileMeta(file.files[0]);
        return { user_selection: `已上传图片（${m.ext} · ${m.size}）`, file_meta: m };
      }, () => !file.files[0] ? "请先拍照或选择图片" : null),
    ]);
  }

  function rMatrixSelect(env, ctx) {
    const p = env.params;
    if (!(p.options || []).length) return compCard([compTitle(p.prompt), emptyState(env)]);
    let picked = null;
    const box = compCard([compTitle(p.prompt || p.title || "方案比选")]);
    const tableBox = el("div", {});
    const rerender = () => {
      tableBox.innerHTML = "";
      tableBox.appendChild(matrixTable(p, true, (opt) => { picked = opt; rerender(); }, picked));
    };
    rerender();
    box.appendChild(tableBox);
    box.appendChild(submitBar(env, ctx, () => ({
      options_offered: p.options, recommended_default: p.recommended_default || null,
      user_selection: picked, modified_from_default: picked !== p.recommended_default,
      matrix_snapshot: { dimensions: p.dimensions, values: p.values },
    }), () => picked == null ? "请先选择一个方案" : null));
    return box;
  }

  function likertRange(lk) {
    // 新协议 from/to（如 -2~2、0-10）；兼容旧 steps（1..steps）
    if (lk.from != null && lk.to != null && lk.to > lk.from) {
      const span = Math.min(11, lk.to - lk.from + 1);
      return Array.from({ length: span }, (_, i) => lk.from + i);
    }
    const steps = Math.max(2, Math.min(10, lk.steps || 5));
    return Array.from({ length: steps }, (_, i) => i + 1);
  }

  function rScaleLikert(env, ctx) {
    const p = env.params;
    const lk = p.likert || { left: "非常不认可", right: "非常认可", steps: 5 };
    const values = likertRange(lk);
    const display = p.display || "dots";
    let picked = null;
    let body;
    if (display === "bar") {
      const segs = [];
      body = el("div", {}, [
        el("div", { class: "likert-bar" }, values.map((v, i) => {
          const b = el("button", { type: "button", onclick: () => {
            picked = v;
            segs.forEach((x, xi) => x.classList.toggle("primary", xi <= i));
          } }, [String(v)]);
          segs.push(b);
          return b;
        })),
        el("div", { style: "display:flex;justify-content:space-between;margin-top:4px" }, [
          el("span", { class: "muted" }, [lk.left]), el("span", { class: "muted" }, [lk.right]),
        ]),
      ]);
    } else {
      const btns = [];
      body = el("div", {}, [
        el("div", { class: "likert-row" }, values.map(v => {
          const b = el("button", { class: "btn opt likert", type: "button", onclick: () => {
            picked = v;
            btns.forEach(x => x.classList.remove("primary"));
            b.classList.add("primary");
          } }, [String(v)]);
          btns.push(b);
          return b;
        })),
        el("div", { style: "display:flex;justify-content:space-between;margin-top:6px" }, [
          el("span", { class: "muted" }, [lk.left]), el("span", { class: "muted" }, [lk.right]),
        ]),
      ]);
    }
    return compCard([
      compTitle(p.prompt),
      body,
      submitBar(env, ctx, () => ({
        options_offered: values.map(String),
        user_selection: picked,
      }), () => picked == null ? "请先打分" : null),
    ]);
  }

  // ================= 控制型 =================

  function rConfirm(env, ctx) {
    const p = env.params;
    return compCard([
      el("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:6px" }, [
        el("span", { class: "chip red" }, ["高风险"]),
        el("span", { style: "font-weight:600" }, [p.title || "操作确认"]),
      ]),
      el("div", { class: "secondary" }, [p.action_desc || ""]),
      el("div", { style: "margin-top:10px;display:flex;gap:8px" }, [
        el("button", { class: "btn primary", onclick: (e) => { disableSiblings(e); ctx.onControl("confirm", env); } }, [p.confirm_label || "确认执行"]),
        el("button", { class: "btn", onclick: (e) => { disableSiblings(e); ctx.onControl("cancel", env); } }, [p.cancel_label || "取消"]),
      ]),
    ]);
  }

  function disableSiblings(e) {
    e.target.parentElement.querySelectorAll("button").forEach(b => b.disabled = true);
  }

  function rRetry(env, ctx) {
    return compCard([
      el("div", { style: "display:flex;gap:8px" }, [
        el("button", { class: "btn", onclick: (e) => { disableSiblings(e); ctx.onControl("retry", env); } }, ["换模型重答"]),
      ]),
    ]);
  }

  function rBranch(env, ctx) {
    const p = env.params;
    return compCard([
      compTitle(p.prompt || "接下来怎么走"),
      el("div", { style: "display:flex;gap:8px" }, [
        el("button", { class: "btn", onclick: (e) => { disableSiblings(e); ctx.onControl("continue", env); } }, [p.continue_label || "沿当前方向继续"]),
        el("button", { class: "btn", onclick: (e) => { disableSiblings(e); ctx.onControl("branch", env); } }, [p.branch_label || "换个方向"]),
      ]),
    ]);
  }

  // ================= 评价型 =================

  function rFeedbackBinary(env, ctx) {
    // §2.3.4 的合规路径二：单一赞踩 + 点踩后追问原因分类（原因分组区分 能力/偏好 两种语义）。
    // 双维度并排展示信息过载，已按走查 A9 修正为此形态。
    const row = el("span", { style: "display:inline-flex;gap:2px;align-items:center" });
    const up = el("button", { class: "btn small ghost", onclick: () => {
      up.disabled = down.disabled = true;
      up.style.color = "var(--primary)";
      emitBinary(env, ctx, { key: "capability" }, 1.0, null);
      UI.toast("已记录，谢谢反馈");
    } }, ["赞"]);
    const down = el("button", { class: "btn small ghost", onclick: () => {
      up.disabled = down.disabled = true;
      down.style.color = "var(--primary)";
      askDownReason(env, ctx);
    } }, ["踩"]);
    row.appendChild(up);
    row.appendChild(down);
    return row;
  }

  function askDownReason(env, ctx) {
    // 原因分组决定标签语义：答得不对 → capability；不合需要 → preference
    const groups = [
      { kind: "capability", title: "答得不对", reasons: ["结论错误", "数据不对", "答非所问"] },
      { kind: "preference", title: "不合需要", reasons: ["风格不合", "太啰嗦", "太简略"] },
    ];
    const body = el("div", {}, groups.map(g => el("div", { style: "margin-bottom:10px" }, [
      el("div", { class: "muted", style: "margin-bottom:4px" }, [g.title]),
      el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, g.reasons.map(r =>
        el("button", { class: "btn", onclick: () => {
          emitBinary(env, ctx, { key: g.kind }, 0.0, r);
          mask.remove();
          UI.toast("已记录，谢谢反馈");
        } }, [r]))),
    ])));
    const mask = UI.modal("哪里不好", body);
  }

  function emitBinary(env, ctx, dim, polarityVal, reason) {
    ctx.sendEvent("feedback_given", env, { dimension: dim.key, value: polarityVal >= 0.5 ? "up" : "down", reason },
      { label_hint: {
        label_kind: dim.key === "capability" ? "capability" : "preference",
        polarity: polarityVal >= 0.5 ? 1.0 : -1.0, confidence: 0.6,
        target_models: env.params.target_models || [], source: "explicit_binary" } });
    UI.toast("反馈已记录，将用于优化模型调度");
  }

  function rFeedbackPreference(env, ctx) {
    // 折叠呈现（走查 A9）：核心信号源，但不该在每条聚合回答下强占屏幕
    const p = env.params;
    const cands = p.candidates || [];
    const box = el("details", {
      class: "comp",
      style: "background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-card);padding:10px 14px;margin-top:8px",
    }, [
      el("summary", { style: "cursor:pointer;color:var(--text-secondary);font-size:var(--font-small)" },
        [`本次综合了 ${cands.length} 个候选回答，展开选出你认为更好的（可选）`]),
    ]);
    const list = el("div", { style: "display:flex;flex-direction:column;gap:8px;margin-top:8px" });
    cands.forEach(c => {
      list.appendChild(el("div", {
        style: "border:1px solid var(--border);border-radius:var(--radius-control);padding:8px 12px;cursor:pointer",
        onclick: (e) => {
          list.querySelectorAll("div").forEach(d => d.style.pointerEvents = "none");
          e.currentTarget.style.borderColor = "var(--primary)";
          e.currentTarget.style.background = "var(--primary-weak)";
          ctx.sendEvent("feedback_given", env, {
            selected_model_id: c.model_id,
            unselected_model_ids: cands.filter(x => x.model_id !== c.model_id).map(x => x.model_id),
          });
          UI.toast("已记录你的择优选择");
        },
      }, [
        el("div", { class: "muted" }, [c.alias || c.model_id]),
        el("div", { style: "font-size:var(--font-small)" }, [c.content || ""]),
      ]));
    });
    box.appendChild(list);
    return box;
  }

  const RENDERERS = {
    "text.emphasis": rText,
    "metric.card": rMetric,
    "list.ordered": rListOrdered,
    "timeline": rTimeline,
    "steps": rSteps,
    "table": rTable,
    "chart.line": rChartLine,
    "chart.area": rChartLine,
    "chart.bar": rChartBar,
    "chart.pie": rChartPie,
    "matrix.compare": rMatrixCompare,
    "flow.reasoning": rFlowReasoning,
    "citation.card": rCitation,
    "select.single": rSelectSingle,
    "select.card": rSelectCard,
    "select.multi": rSelectMulti,
    "slider.range": rSlider,
    "scale.likert": rScaleLikert,
    "form.structured": rForm,
    "input.followup": rInputFollowup,
    "matrix.compare+select": rMatrixSelect,
    "picker.datetime": rPickerDatetime,
    "picker.timerange": rTimerange,
    "picker.location": rPickerLocation,
    "rank.priority": rRank,
    "upload.file": rUpload,
    "upload.image": rUploadImage,
    "suggest.followup": rSuggestFollowup,
    "commerce.order": rCommerce,
    "entry.link": rEntryLink,
    "track.map": rTrackMap,
    "control.confirm": rConfirm,
    "control.retry": rRetry,
    "control.branch": rBranch,
    "feedback.binary": rFeedbackBinary,
    "feedback.preference": rFeedbackPreference,
  };

  return { render, supported: Object.keys(RENDERERS) };
})();
