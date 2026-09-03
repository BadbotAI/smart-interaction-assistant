// 公共 UI 工具：请求封装、导航、toast、模态框、SVG 迷你图表库。
window.UI = (function () {
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error?.message || data.error || "请求失败"), { status: res.status, data });
    return data;
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node[k] = v;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function toast(msg, isError = false) {
    const t = el("div", { class: "toast" + (isError ? " error" : "") }, [msg]);
    document.body.appendChild(t);
    // 错误信息需要更长的阅读时间（走查 G2）
    setTimeout(() => t.remove(), isError ? 5200 : 3200);
  }

  function modal(title, contentNode, footNode, opts = {}) {
    const mask = el("div", { class: "modal-mask", onclick: (e) => { if (e.target === mask && !opts.persistent) mask.remove(); } });
    const box = el("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true" }, [
      el("div", { class: "modal-head" }, [
        el("div", { class: "modal-title" }, [title]),
        el("button", { class: "close-btn", "aria-label": "关闭", onclick: () => mask.remove() }, ["×"]),
      ]),
      contentNode,
      footNode || null,
    ]);
    mask.appendChild(box);
    document.body.appendChild(mask);
    // Esc 只关最顶层弹窗（叠加确认框时不误关下层编辑器）；经关闭按钮走各自的收尾逻辑
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      const masks = document.querySelectorAll(".modal-mask");
      if (masks[masks.length - 1] !== mask) return;
      const cb = mask.querySelector(".close-btn");
      cb ? cb.click() : mask.remove();
    };
    document.addEventListener("keydown", onKey);
    const obs = new MutationObserver(() => { if (!document.body.contains(mask)) { document.removeEventListener("keydown", onKey); obs.disconnect(); } });
    obs.observe(document.body, { childList: true });
    return mask;
  }

  // 统一确认框（走查 G1）：替代原生 confirm，破坏性操作用 danger 样式。返回 Promise<boolean>
  function confirmDialog(title, message, opts = {}) {
    return new Promise(resolve => {
      const body = el("div", { class: "secondary", style: "white-space:pre-line" }, [message]);
      const foot = el("div", { style: "display:flex;justify-content:flex-end;gap:8px;margin-top:16px" }, [
        el("button", { class: "btn", onclick: () => { mask.remove(); resolve(false); } }, [opts.cancelText || "取消"]),
        el("button", { class: "btn " + (opts.danger ? "danger" : "primary"), onclick: () => { mask.remove(); resolve(true); } }, [opts.okText || "确定"]),
      ]);
      const mask = modal(title, body, foot, { persistent: true });
      const obs = new MutationObserver(() => { if (!document.body.contains(mask)) { resolve(false); obs.disconnect(); } });
      obs.observe(document.body, { childList: true });
      setTimeout(() => foot.lastChild.focus(), 0);
    });
  }

  // 按钮忙碌态（走查 G5）：异步操作期间禁用并改文案，防重复提交
  async function withBusy(btn, fn, busyText) {
    if (!btn || btn.disabled) return;
    const orig = btn.textContent;
    // 锁定当前宽度：忙碌文案长度不同不再引起按钮与整行跳动
    const w = btn.offsetWidth;
    if (w) btn.style.minWidth = w + "px";
    btn.disabled = true;
    if (busyText) btn.textContent = busyText;
    try { return await fn(); }
    finally { btn.disabled = false; btn.textContent = orig; btn.style.minWidth = ""; }
  }

  // 内容区加载占位（走查 R7）
  function loading(container, text = "加载中……") {
    container.innerHTML = "";
    container.appendChild(el("div", { class: "muted", style: "padding:24px;text-align:center" }, [text]));
  }

  // 「更多」下拉菜单（走查 C2/R1）：次要与破坏性操作收纳
  function menu(anchorBtn, items) {
    document.querySelectorAll(".menu-pop").forEach(n => n.remove());
    const pop = el("div", { class: "menu-pop", role: "menu" },
      items.filter(Boolean).map(it => el("button", {
        class: "menu-item" + (it.danger ? " danger" : ""), role: "menuitem",
        onclick: () => { pop.remove(); it.onClick(); },
      }, [it.label])));
    document.body.appendChild(pop);
    const r = anchorBtn.getBoundingClientRect();
    pop.style.top = (r.bottom + window.scrollY + 4) + "px";
    pop.style.left = Math.min(r.left + window.scrollX, window.innerWidth - pop.offsetWidth - 12) + "px";
    const close = (e) => { if (!pop.contains(e.target) && e.target !== anchorBtn) { pop.remove(); document.removeEventListener("click", close, true); } };
    setTimeout(() => document.addEventListener("click", close, true), 0);
    return pop;
  }

  // 自绘下拉：闭合态与输入体系同款，展开态用主题弹层（替代系统原生下拉）
  function fancySelect({ value = "", options = [], onChange, width, display }) {
    const labelOf = v => { const hit = options.find(o => o[0] === v); return hit ? hit[1] : v; };
    const shown = v => display ? display(v, labelOf(v)) : labelOf(v);
    const lab = el("span", { class: "fsel-label" }, [shown(value)]);
    const caret = el("span", { class: "fsel-caret" }, [icon("chevron", 13)]);
    const btn = el("button", { class: "fsel", type: "button", ...(width ? { style: `width:${width}` } : {}) }, [lab, caret]);
    let cur = value;
    btn.onclick = () => {
      document.querySelectorAll(".menu-pop").forEach(n => n.remove());
      const pop = el("div", { class: "menu-pop fsel-pop", role: "listbox" },
        options.map(([v, l]) => el("button", {
          class: "menu-item" + (v === cur ? " on" : ""), role: "option", "aria-selected": v === cur ? "true" : "false",
          onclick: () => { pop.remove(); if (v === cur) return; cur = v; lab.textContent = shown(v); onChange && onChange(v); },
        }, [l])));
      document.body.appendChild(pop);
      const r = btn.getBoundingClientRect();
      pop.style.minWidth = r.width + "px";
      pop.style.top = (r.bottom + window.scrollY + 4) + "px";
      pop.style.left = Math.min(r.left + window.scrollX, window.innerWidth - pop.offsetWidth - 12) + "px";
      const close = (e) => { if (!pop.contains(e.target) && e.target !== btn) { pop.remove(); document.removeEventListener("click", close, true); } };
      setTimeout(() => document.addEventListener("click", close, true), 0);
    };
    btn.setValue = (v) => { cur = v; lab.textContent = labelOf(v); };
    return btn;
  }

  // 组件类型中文名（走查 G3）：运营人员不该面对 select.single 这类内部 ID
  const CT_NAMES = {
    "select.single": "文本选择（单选）", "select.multi": "文本选择（多选）", "select.card": "卡片选择", "scale.likert": "评分",
    "matrix.compare+select": "对比选择", "form.structured": "表单收集", "input.followup": "备注填写",
    "slider.range": "数值选择", "picker.datetime": "日期选择", "picker.timerange": "时间段选择", "picker.location": "地址卡片", "rank.priority": "优先级排序", "upload.file": "文件上传", "upload.image": "图片上传", "suggest.followup": "追问引导", "commerce.order": "商品下单", "entry.link": "入口跳转", "guide.steps": "步骤说明书", "track.map": "物流轨迹", "feedback.binary": "赞踩反馈", "feedback.preference": "多回答择优",
    "control.confirm": "操作确认", "control.interrupt": "中断", "control.retry": "重试", "text.emphasis": "重点文本",
    "metric.card": "指标卡", "table": "表格", "chart.line": "折线图", "chart.area": "面积图", "chart.bar": "柱状图", "chart.pie": "占比图",
    "matrix.compare": "对比矩阵", "timeline": "时间线", "citation.card": "引用卡", "list.ordered": "有序列表", "steps": "步骤条",
    "flow.reasoning": "推理过程", "control.branch": "分支选择", "implicit.behavior": "隐式行为",
  };
  function ctName(ct) { return CT_NAMES[ct] || ct || "-"; }
  // 组件类型 chip：中文名为主，悬停显示内部 ID
  function ctChip(ct, cls = "chip gray") { return el("span", { class: cls, title: ct || "" }, [ctName(ct)]); }

  // 左侧导航：三大模块 + 底部账户。二级项可指向同一页面的不同 tab（#hash）
  const NAV_GROUPS = [
    { title: "", items: [
      ["home", "首页", "./index.html", "home"],
    ] },
    { title: "智能交互", items: [
      ["cards", "组件工作台", "./cards.html", "board"],
      ["library", "组件库", "./library.html", "grid"],
      ["design", "组件风格", "./design.html", "palette2"],
      ["playground:comp", "智能交互测试", "./playground.html#comp", "chat"],
      ["dashboard:survey", "交互数据", "./dashboard.html#survey", "chart"],
    ] },
    { title: "模型路由", items: [
      ["router:dispatch", "路由策略", "./router.html#dispatch", "route2"],
      ["router:flow", "场景数据集", "./router.html#flow", "database"],
      ["playground:model", "模型路由测试", "./playground.html#model", "play"],
      ["dashboard:routing", "模型路由数据", "./dashboard.html#routing", "activity"],
      ["router:models", "模型接入", "./router.html#models", "cpu"],
    ] },
  ];
  function nav(active) {
    const side = el("aside", { class: "sidenav" }, [
      (() => {
        const a = el("a", { class: "logo", href: "./index.html", title: "回到首页" });
        const mark = el("span", { class: "logo-mark", "aria-hidden": "true" });
        mark.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none">'
          + '<rect x="10.5" y="3" width="10.5" height="8" rx="3" fill="none" stroke="var(--primary)" stroke-width="1.7" opacity="0.45"/>'
          + '<path d="M3 12a4 4 0 0 1 4-4h6.5a4 4 0 0 1 4 4v2.5a4 4 0 0 1-4 4H9.8l-3.4 2.9c-.6.5-1.4.1-1.4-.7v-2.6A4 4 0 0 1 3 14.5z" fill="var(--primary)"/>'
          + '<circle cx="7.6" cy="13.2" r="1.15" fill="#fff"/><circle cx="10.75" cy="13.2" r="1.15" fill="#fff"/><circle cx="13.9" cy="13.2" r="1.15" fill="#fff"/>'
          + '</svg>';
        a.append(mark, el("span", {}, ["智能交互平台"]));
        return a;
      })(),
    ]);
    // 二级项按 #hash 高亮；无 hash 时默认该页第一个二级项
    const isActive = (key) => {
      if (key === active) return true;
      if (!active || !key.startsWith(active + ":")) return false;
      const first = NAV_GROUPS.flatMap(g => g.items).find(([k]) => k.startsWith(active + ":"));
      const defHash = first ? "#" + first[0].split(":")[1] : "";
      return (location.hash || defHash) === "#" + key.split(":")[1];
    };
    NAV_GROUPS.forEach(g => {
      const box = el("div", { class: "nav-group" }, [g.title ? el("div", { class: "nav-title" }, [g.title]) : null]);
      g.items.forEach(([key, name, href, ic]) => box.appendChild(el("a", {
        class: "navlink" + (isActive(key) ? " active" : ""), href, "data-key": key,
      }, [icon(ic, 17), el("span", {}, [name])])));
      side.appendChild(box);
    });
    // 审计日志：低频入口，收在底部账户上方
    side.appendChild(el("div", { class: "nav-group", style: "margin-top:auto" }, [
      el("a", { class: "navlink" + (active === "apikeys" ? " active" : ""), href: "./apikeys.html", "data-key": "apikeys" }, [
        icon("link", 17), el("span", {}, ["API 接入管理"]),
      ]),
      el("a", { class: "navlink" + (active === "audit" ? " active" : ""), href: "./audit.html", "data-key": "audit" }, [
        icon("layers", 17), el("span", {}, ["操作日志"]),
      ]),
    ]));
    side.appendChild(el("div", { class: "nav-account", style: "margin-top:0" }, [
      el("span", { class: "acc-avatar" }, ["张"]),
      el("span", { class: "acc-info" }, [
        el("span", { class: "acc-name" }, ["张三"]),
        el("span", { class: "acc-role" }, ["管理员 · tenant-demo"]),
      ]),
    ]));
    document.body.classList.add("has-side");
    document.body.prepend(side);
    Brand.init();
    // 同页 tab 切换时同步高亮
    window.addEventListener("hashchange", () => {
      side.querySelectorAll(".navlink").forEach(a => a.classList.toggle("active", isActive(a.dataset.key)));
    });
    return side;
  }

  // 说明信息收进图标悬停（砍文案）
  function help(text) { return el("span", { class: "help", title: text, "aria-label": text }, [icon("info", 14)]); }

  // 右侧抽屉：参数配置等中量级编辑场景（比弹窗更适合边看列表边改）
  function drawer(title, contentNode, footNode) {
    document.querySelectorAll(".drawer-mask").forEach(m => m.remove()); // 防连点叠开多个抽屉
    const mask = el("div", { class: "drawer-mask", onclick: (e) => { if (e.target === mask) close(); } });
    const box = el("aside", { class: "drawer", role: "dialog", "aria-modal": "true" }, [
      el("div", { class: "drawer-head" }, [
        el("div", { style: "font-weight:600;font-size: var(--font-title)" }, [title]),
        el("button", { class: "close-btn", "aria-label": "关闭", onclick: () => close() }, ["×"]),
      ]),
      el("div", { class: "drawer-body" }, [contentNode]),
      footNode ? el("div", { class: "drawer-foot" }, [footNode]) : null,
    ]);
    function close() { box.classList.remove("open"); setTimeout(() => mask.remove(), 180); }
    mask.appendChild(box);
    document.body.appendChild(mask);
    requestAnimationFrame(() => box.classList.add("open"));
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    const obs = new MutationObserver(() => { if (!document.body.contains(mask)) { document.removeEventListener("keydown", onKey); obs.disconnect(); } });
    obs.observe(document.body, { childList: true });
    mask.close = close;
    return mask;
  }

  function fmtCost(v) { return v == null ? "-" : "¥" + Number(v).toFixed(4); }
  function fmtMs(v) { return v == null ? "-" : (v >= 1000 ? (v / 1000).toFixed(1) + "s" : v + "ms"); }
  function fmtTs(sec) {
    if (!sec) return "-";
    const d = new Date(sec * 1000);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  function fmtPct(v) { return v == null ? "-" : (v * 100).toFixed(1) + "%"; }

  // ---------- SVG 迷你图表库（遵循 dataviz 规范：细标记、静默网格、悬停提示、文本用文字色） ----------
  const NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  }
  function svgTitle(node, text) {
    const t = svgEl("title", {});
    t.textContent = text;
    node.appendChild(t);
    return node;
  }
  const INK = () => getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim() || "#898781";
  const GRID = () => Brand.chartPalette().grid || "#e1e0d9";

  function chartFrame(w, h) {
    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", style: "display:block" });
    return svg;
  }

  function lineChart(container, { series, labels, height = 180, unit = "" }) {
    container.innerHTML = "";
    const pal = Brand.chartPalette().categorical;
    const w = 560, h = height, padL = 44, padR = 12, padT = 14, padB = 26;
    const svg = chartFrame(w, h);
    const all = series.flatMap(s => s.values);
    // 走查：原先 Y 轴上限被强制为 ≥1，成本这类小数值曲线被压成一条直线
    const maxV = (Math.max(...all) || 0) > 0 ? Math.max(...all) : 1, minV = Math.min(...all, 0);
    const span = (maxV - minV) || 1;
    const x = i => padL + i * (w - padL - padR) / Math.max(1, labels.length - 1);
    const y = v => padT + (h - padT - padB) * (1 - (v - minV) / span);
    for (let g = 0; g <= 3; g++) {
      const gy = padT + g * (h - padT - padB) / 3;
      svg.appendChild(svgEl("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, stroke: GRID(), "stroke-width": 1 }));
      const val = maxV - g * span / 3;
      const tl = svgEl("text", { x: padL - 6, y: gy + 4, "text-anchor": "end", "font-size": 10, fill: INK() });
      const v0 = Math.abs(val) < 1e-9 ? 0 : val;
      tl.textContent = v0 >= 100 ? Math.round(v0) : v0.toFixed(v0 >= 1 ? 1 : 3);
      svg.appendChild(tl);
    }
    labels.forEach((lb, i) => {
      if (labels.length > 10 && i % Math.ceil(labels.length / 8) !== 0) return;
      const tx = svgEl("text", { x: x(i), y: h - 8, "text-anchor": "middle", "font-size": 10, fill: INK() });
      tx.textContent = lb;
      svg.appendChild(tx);
    });
    series.forEach((s, si) => {
      const color = pal[si % pal.length];
      const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
      svg.appendChild(svgEl("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
      s.values.forEach((v, i) => {
        const c = svgEl("circle", { cx: x(i), cy: y(v), r: 3.5, fill: color, stroke: "var(--bg-surface)", "stroke-width": 2 });
        svgTitle(c, `${labels[i]} · ${s.name}: ${v}${unit}`);
        svg.appendChild(c);
      });
      if (series.length > 1) {
        const last = s.values[s.values.length - 1];
        const lt = svgEl("text", { x: w - padR + 2, y: y(last) + 4, "font-size": 10, fill: "var(--text-secondary)" });
        lt.textContent = s.name;
        svg.appendChild(lt);
      }
    });
    container.appendChild(svg);
  }

  function barChart(container, { categories, values, height = 190, unit = "", color, horizontal = false, maxValue, format }) {
    container.innerHTML = "";
    const barColor = color || "var(--primary)";
    const fmtVal = format || (v => String(typeof v === "number" && v % 1 !== 0 ? v.toFixed(3) : v) + unit);
    if (horizontal) {
      const maxV = maxValue || Math.max(...values, 1);
      const wrap = el("div", {});
      categories.forEach((c, i) => {
        const row = el("div", { style: "display:flex;align-items:center;gap:8px;margin:4px 0" }, [
          el("div", { class: "muted", style: "width:130px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: c }, [c]),
          el("div", { style: "flex:1;height:14px;background:transparent;position:relative" }, [
            el("div", {
              style: `height:14px;width:${Math.max(1, values[i] / maxV * 100)}%;background:${barColor};border-radius:0 4px 4px 0`,
              title: `${c}: ${fmtVal(values[i])}`,
            }),
          ]),
          el("div", { style: "width:78px;font-variant-numeric:tabular-nums;font-size: var(--font-small);color:var(--text-secondary)" },
            [fmtVal(values[i])]),
        ]);
        wrap.appendChild(row);
      });
      container.appendChild(wrap);
      return;
    }
    const w = 560, h = height, padL = 44, padR = 12, padT = 14, padB = 30;
    const svg = chartFrame(w, h);
    const maxV = maxValue || Math.max(...values, 1);
    const n = categories.length;
    const slot = (w - padL - padR) / n;
    const bw = Math.min(38, slot * 0.55);
    for (let g = 0; g <= 3; g++) {
      const gy = padT + g * (h - padT - padB) / 3;
      svg.appendChild(svgEl("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, stroke: GRID(), "stroke-width": 1 }));
      const tl = svgEl("text", { x: padL - 6, y: gy + 4, "text-anchor": "end", "font-size": 10, fill: INK() });
      const val = maxV * (1 - g / 3);
      tl.textContent = val >= 100 ? Math.round(val) : val.toFixed(val >= 1 ? 1 : 3);
      svg.appendChild(tl);
    }
    values.forEach((v, i) => {
      const bh = Math.max(2, (h - padT - padB) * v / maxV);
      const bx = padL + i * slot + (slot - bw) / 2;
      const rect = svgEl("path", {
        d: `M${bx},${h - padB} v${-(bh - 4)} q0,-4 4,-4 h${bw - 8} q4,0 4,4 v${bh - 4} z`,
        fill: barColor,
      });
      svgTitle(rect, `${categories[i]}: ${v}${unit}`);
      svg.appendChild(rect);
      const tx = svgEl("text", { x: bx + bw / 2, y: h - 10, "text-anchor": "middle", "font-size": 10, fill: INK() });
      tx.textContent = String(categories[i]).slice(0, 6);
      svg.appendChild(tx);
    });
    svg.appendChild(svgEl("line", { x1: padL, y1: h - padB, x2: w - padR, y2: h - padB, stroke: Brand.chartPalette().axis, "stroke-width": 1 }));
    container.appendChild(svg);
  }

  function stackedBars(container, { rows, keys, height = 190 }) {
    // rows: [{label, values: {key: n}}] — 按日期的堆叠柱（标签来源构成等）
    container.innerHTML = "";
    const pal = Brand.chartPalette().categorical;
    const w = 560, h = height, padL = 40, padR = 12, padT = 12, padB = 44;
    const svg = chartFrame(w, h);
    const totals = rows.map(r => keys.reduce((s, k) => s + (r.values[k] || 0), 0));
    const maxV = Math.max(...totals, 1);
    const slot = (w - padL - padR) / rows.length;
    const bw = Math.min(34, slot * 0.6);
    rows.forEach((r, i) => {
      let yCur = h - padB;
      keys.forEach((k, ki) => {
        const v = r.values[k] || 0;
        if (!v) return;
        const bh = (h - padT - padB) * v / maxV;
        const rect = svgEl("rect", {
          x: padL + i * slot + (slot - bw) / 2, y: yCur - bh, width: bw, height: Math.max(1, bh - 2),
          fill: pal[ki % pal.length], rx: 2,
        });
        svgTitle(rect, `${r.label} · ${k}: ${v}`);
        svg.appendChild(rect);
        yCur -= bh;
      });
      const tx = svgEl("text", { x: padL + i * slot + slot / 2, y: h - padB + 14, "text-anchor": "middle", "font-size": 10, fill: INK() });
      tx.textContent = r.label;
      svg.appendChild(tx);
    });
    container.appendChild(svg);
    const legend = el("div", { style: "display:flex;gap:14px;flex-wrap:wrap;margin-top:4px" },
      keys.map((k, ki) => el("span", { class: "muted", style: "display:flex;align-items:center;gap:5px" }, [
        el("span", { style: `width:10px;height:10px;border-radius:2px;background:${pal[ki % pal.length]};display:inline-block` }),
        k,
      ])));
    container.appendChild(legend);
  }

  function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  // ---------- 内联 SVG 图标（无 emoji，描边 1.6，随 currentColor 着色） ----------
  const ICON_PATHS = {
    // 魔法棒：一根斜杖 + 杖头一颗实心四角星 + 一粒小光点。16px 下仍清晰，不再是一堆碎星
    wand: '<path d="M3.5 20.5 13 11" stroke-width="2"/><path d="M16.5 2.5l1.3 3.2 3.2 1.3-3.2 1.3-1.3 3.2-1.3-3.2L12 7l3.2-1.3z" fill="currentColor" stroke="none"/><circle cx="7" cy="6" r="1" fill="currentColor" stroke="none"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    trash: '<path d="M4 7h16M10 7V5h4v2M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    check: '<path d="m5 12 4.5 4.5L19 7"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    board: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 9h16M9 9v11"/>',
    database: '<ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6"/><path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
    palette2: '<circle cx="12" cy="12" r="8.5"/><circle cx="9" cy="9.5" r="1.2" fill="currentColor" stroke="none"/><circle cx="14.5" cy="8.8" r="1.2" fill="currentColor" stroke="none"/><circle cx="15.5" cy="13.5" r="1.2" fill="currentColor" stroke="none"/><path d="M12 20.5c-1.8 0-2.4-1.4-1.4-2.5.9-1 .3-2.5-1-2.5H8"/>',
    route2: '<path d="M5 20V10a4 4 0 0 1 4-4h10"/><path d="m15 2 4 4-4 4"/><path d="M5 14h7a4 4 0 0 1 4 4v2"/>',
    calendar: '<rect x="4" y="6" width="16" height="14" rx="2"/><path d="M8 3v4M16 3v4M4 11h16"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    home: '<path d="M4 11 12 4l8 7"/><path d="M6 10v10h12V10"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    sliders: '<path d="M5 7h14M5 12h14M5 17h14"/><circle cx="9" cy="7" r="2" fill="var(--bg-surface)"/><circle cx="15" cy="12" r="2" fill="var(--bg-surface)"/><circle cx="8" cy="17" r="2" fill="var(--bg-surface)"/>',
    chat: '<path d="M5 6h14v9H9l-4 4z"/>',
    cpu: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    layers: '<path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16 8 4 8-4"/>',
    route: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8 17h5a4 4 0 0 0 4-4V9"/>',
    split: '<path d="M12 4v6"/><path d="M12 10 6 16v4"/><path d="m12 10 6 6v4"/>',
    chart: '<path d="M4 20h16"/><path d="M7 16V10M12 16V6M17 16v-3"/>',
    activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    send: '<path d="M4 12 20 4l-4 16-4-7z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01"/>',
    download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    edit: '<path d="m4 20 4-1L19 8l-3-3L5 16z"/>',
    more: '<circle cx="6" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="12" r="1.5"/>',
    drag: '<path d="M5 9h14M5 15h14"/>',
    play: '<path d="m7 5 12 7-12 7z"/>',
    palette: '<path d="M12 3a9 9 0 1 0 0 18h1.2a2.3 2.3 0 0 0 1.6-4 2.3 2.3 0 0 1 1.6-4H19a2 2 0 0 0 2-2 9 9 0 0 0-9-8z"/><circle cx="7.5" cy="11.5" r="1" fill="currentColor" stroke="none"/><circle cx="9.5" cy="7" r="1" fill="currentColor" stroke="none"/><circle cx="14" cy="6" r="1" fill="currentColor" stroke="none"/>',
    upload: '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/>',
    sparkles: '<path d="M9 3.5 10.4 7 14 8.4 10.4 9.8 9 13.3 7.6 9.8 4 8.4 7.6 7z" fill="currentColor" stroke="none"/><path d="M17 11.5l1.1 2.6 2.6 1.1-2.6 1.1L17 19l-1.1-2.7-2.6-1.1 2.6-1.1z" fill="currentColor" stroke="none"/>',
    power: '<path d="M12 3v8"/><path d="M6.3 6.5a8 8 0 1 0 11.4 0"/>',
  };
  function icon(name, size = 16) {
    const span = document.createElement("span");
    span.className = "ico";
    span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name] || ""}</svg>`;
    return span;
  }
  function iconBtn(name, title, onClick, cls = "") {
    return el("button", { class: "icon-btn " + cls, title, "aria-label": title, type: "button", onclick: onClick }, [icon(name)]);
  }

  // ---------- ID 展示（走查：所有可统计对象必须露出 ID，后台按 ID 统计） ----------
  // UUID 类长 ID 取前 8 位；可读 ID（如组件类型 select.single、model_id）完整展示
  function shortId(id) {
    if (!id) return "-";
    const s = String(id);
    return /^[0-9a-f]{8}-?[0-9a-f]{4}/i.test(s) && s.length >= 24 ? s.slice(0, 8) : s;
  }
  function idChip(id, label = "ID") {
    if (!id) return el("span", { class: "muted" }, ["-"]);
    const chip = el("button", { class: "id-chip num", type: "button", title: `完整 ID：${id}\n点击复制`,
      onclick: (e) => { e.stopPropagation(); navigator.clipboard.writeText(id); toast(`已复制 ${label}：${id}`); } },
      [`${label} ${shortId(id)}`, icon("copy", 12)]);
    return chip;
  }

  // API key 样式的标识字段：label | code | 复制
  function keyField(label, value, opts = {}) {
    const wrap = el("div", { class: "key-field" + (opts.block ? " block" : "") }, [
      label ? el("span", { class: "key-label" }, [label]) : null,
      el("code", { title: value }, [value]),
      iconBtn("copy", "复制", () => { navigator.clipboard.writeText(opts.copyValue || value); toast("已复制"); }),
    ]);
    return wrap;
  }

  // 对话样式的预览容器：用户气泡 + AI 气泡（含说明文案与组件）
  function chatMock({ question, reply, node, prompt }) {
    return el("div", { class: "chat-mock" }, [
      question ? el("div", { class: "m-user" }, [el("span", {}, [question])]) : null,
      el("div", { class: "m-bot" }, [
        el("span", { class: "avatar" }, [icon("bot", 16)]),
        el("div", { class: "bubble" }, [
          reply ? el("div", {}, [reply]) : null,
          prompt && prompt !== reply ? el("div", { class: "bubble-prompt" }, [prompt]) : null,
          node,
        ]),
      ]),
    ]);
  }

  // ---------- 标签输入（示例问法等多值字段） ----------
  function tagInput({ values = [], max = 10, placeholder = "输入后回车添加", onChange }) {
    const list = [...values];
    const wrap = el("div", { class: "tag-input", onclick: () => input.focus() });
    const input = el("input", { type: "text", placeholder });
    function draw() {
      wrap.innerHTML = "";
      list.forEach((v, i) => wrap.appendChild(el("span", { class: "tag", title: v }, [
        el("span", {}, [v]), el("button", { type: "button", class: "tag-x", title: "移除", "aria-label": "移除 " + v,
          onclick: (e) => { e.stopPropagation(); list.splice(i, 1); draw(); onChange && onChange([...list]); } }, [icon("x", 12)]),
      ])));
      input.placeholder = list.length >= max ? `最多 ${max} 条` : placeholder;
      input.disabled = list.length >= max;
      wrap.appendChild(input);
    }
    function commit() {
      const v = input.value.trim();
      if (!v) return;
      if (list.includes(v)) { toast("已存在相同条目", true); input.value = ""; return; }
      if (list.length >= max) { toast(`最多 ${max} 条`, true); return; }
      list.push(v); input.value = ""; draw(); input.focus(); onChange && onChange([...list]);
    }
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); draw(); input.focus(); onChange && onChange([...list]); }
    });
    input.addEventListener("blur", commit);
    draw();
    wrap.getValues = () => [...list];
    return wrap;
  }

  // ---------- 开关（带文字说明） ----------
  function toggle({ checked = false, label, hint, onChange, disabled = false, disabledHint }) {
    const btn = el("button", { class: "toggle" + (checked ? " on" : ""), type: "button", role: "switch",
      "aria-checked": checked ? "true" : "false", title: disabled ? (disabledHint || "") : "" });
    if (disabled) btn.disabled = true;
    btn.onclick = async () => {
      if (btn.disabled) return;
      const next = !btn.classList.contains("on");
      btn.disabled = true;
      try {
        const ok = onChange ? await onChange(next) : true;
        if (ok !== false) { btn.classList.toggle("on", next); btn.setAttribute("aria-checked", next ? "true" : "false"); }
      } finally { if (!disabled) btn.disabled = false; }
    };
    if (!label) return btn;
    return el("label", { class: "toggle-row" }, [
      btn,
      el("span", {}, [el("span", { class: "toggle-label" }, [label]), hint ? el("span", { class: "muted", style: "display:block" }, [hint]) : null]),
    ]);
  }


  return { api, el, toast, modal, drawer, confirm: confirmDialog, withBusy, loading, menu, fancySelect, ctName, ctChip, debounce,
    icon, iconBtn, shortId, idChip, keyField, chatMock, tagInput, toggle, help,
    nav, fmtCost, fmtMs, fmtTs, fmtPct, lineChart, barChart, stackedBars };
})();
