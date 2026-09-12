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
      else if (typeof v === "boolean") { if (v) node.setAttribute(k, ""); }
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
    pop.style.position = "fixed";
    pop.style.top = (r.bottom + 4) + "px";
    pop.style.left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 12) + "px";
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

  // 标签多选下拉：框内出可删标签，点击/输入过滤下拉选项（候选模型、产品绑定组件等共用）
  function tagSelect({ selected = [], options = [], placeholder = "点击选择…", minKeep = 0, onChange }) {
    const sel = new Set(selected);
    const input = el("input", { class: "tagbox-in", type: "text", placeholder });
    const box = el("div", { class: "tagbox", tabindex: "-1" });
    let pop = null;
    const emit = () => onChange && onChange([...sel]);
    const closePop = () => { if (pop) { pop.remove(); pop = null; } };
    const labelOf = v => { const hit = options.find(o => o[0] === v); return hit ? hit[1] : v; };
    function draw() {
      box.innerHTML = "";
      [...sel].forEach(v => box.appendChild(el("span", { class: "tagsel-tag" }, [labelOf(v),
        el("button", { type: "button", title: "移除", onclick: (e) => {
          e.stopPropagation();
          if (sel.size <= minKeep) { toast(`至少保留 ${minKeep} 项`, true); return; }
          sel.delete(v); draw(); emit(); openPop();
        } }, ["×"])])));
      input.placeholder = sel.size ? "" : placeholder;
      box.appendChild(input);
    }
    function openPop() {
      closePop();
      const kw = input.value.trim().toLowerCase();
      const rest = options.filter(([v, l]) => !sel.has(v) && (!kw || String(l).toLowerCase().includes(kw) || String(v).toLowerCase().includes(kw)));
      pop = el("div", { class: "menu-pop fsel-pop", role: "listbox" }, rest.length
        ? rest.map(([v, l]) => el("button", { class: "menu-item", role: "option", type: "button",
            onmousedown: (e) => e.preventDefault(),
            onclick: () => { sel.add(v); input.value = ""; draw(); emit(); input.focus(); openPop(); },
          }, [l]))
        : [el("div", { class: "menu-item", style: "color:var(--text-muted);cursor:default" }, [kw ? "没有匹配项" : "已全部选入"])]);
      document.body.appendChild(pop);
      const r = box.getBoundingClientRect();
      pop.style.minWidth = r.width + "px";
      pop.style.top = (r.bottom + window.scrollY + 4) + "px";
      pop.style.left = Math.max(12, Math.min(r.left + window.scrollX, window.innerWidth - pop.offsetWidth - 12)) + "px";
    }
    box.onclick = () => { input.focus(); openPop(); };
    input.oninput = () => openPop();
    input.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = pop && pop.querySelector(".menu-item[role=option]");
        if (first) first.click();
      } else if (e.key === "Backspace" && !input.value && sel.size > minKeep) {
        sel.delete([...sel].pop()); draw(); emit(); openPop();
      } else if (e.key === "Escape") closePop();
    };
    input.onblur = () => setTimeout(() => { if (pop && !pop.contains(document.activeElement)) closePop(); }, 150);
    draw();
    box.getSelected = () => [...sel];
    return box;
  }

  // 组件类型中文名（走查 G3）：运营人员不该面对 select.single 这类内部 ID
  const CT_NAMES = {
    "select.single": "选择器（单选）", "select.multi": "选择器（多选）", "select.card": "卡片选择器", "scale.likert": "评分",
    "matrix.compare+select": "对比选择", "form.structured": "表单", "input.followup": "备注填写",
    "slider.range": "数值滑杆", "picker.datetime": "日期时间", "picker.timerange": "时间段选择", "picker.location": "地址卡片", "rank.priority": "排序器", "chart.waterfall": "瀑布图", "upload.file": "文件上传", "upload.image": "图片上传", "suggest.followup": "追问引导", "commerce.order": "商品下单", "entry.link": "入口跳转", "guide.steps": "步骤说明书", "track.map": "物流轨迹", "feedback.binary": "赞踩反馈", "feedback.preference": "偏好选择器",
    "control.confirm": "确认器", "control.interrupt": "中断", "control.retry": "重试", "text.emphasis": "重点结论",
    "metric.card": "指标卡", "table": "表格", "chart.line": "折线图", "chart.area": "面积图", "chart.bar": "柱状图", "chart.pie": "饼图",
    "matrix.compare": "对比矩阵", "timeline": "时间线", "citation.card": "引用卡", "list.ordered": "要点清单", "steps": "步骤条",
    "flow.reasoning": "推理过程", "control.branch": "分支选择", "implicit.behavior": "隐式行为",
  };
  function ctName(ct) { return CT_NAMES[ct] || ct || "-"; }
  // 组件类型 chip：中文名为主，悬停显示内部 ID
  function ctChip(ct, cls = "chip gray") { return el("span", { class: cls, title: ct || "" }, [ctName(ct)]); }

  // 左侧导航：三大模块 + 底部账户。二级项可指向同一页面的不同 tab（#hash）
  // 两个平台各自独立的导航；共享页（操作日志等）按 window.SIA_PLATFORM 或高亮键推断归属
  const PLATFORMS = {
    ia: {
      name: "智能交互平台", home: "./index.html", productSwitcher: true,
      groups: [
        // 切换器（全局上下文）之下 = 当前产品维度的三页一组；全局管理沉到底部区
        { title: "当前产品", items: [
          ["cards", "组件工作台", "./cards.html", "sliders"],
          ["design", "风格主题", "./design.html", "palette"],
          ["analytics", "数据分析", "./analytics.html", "activity"],
        ] },
      ],
      footItems: [["products", "产品与接入", "./products.html", "box"]],
    },
    router: {
      name: "模型路由平台", home: "./home-router.html",
      groups: [
        { title: "", items: [["home", "首页", "./home-router.html", "home"]] },
        // 按用户动线分两组：一次性搭建（配策略→定画像→接模型）与日常运营（测试→数据）
        { title: "配置", items: [
          ["router:dispatch", "路由策略", "./router.html#dispatch", "route2"],
          ["router:profile", "模型画像", "./router.html#profile", "database"],
          ["router:models", "模型接入", "./router.html#models", "cpu"],
        ] },
        { title: "运营", items: [
          ["playground:model", "模型路由测试", "./playground.html#model", "play"],
          ["dashboard:routing", "路由数据分析", "./dashboard.html#routing", "activity"],
        ] },
      ],
    },
  };
  const ROUTER_KEYS = ["router", "playground:model", "dashboard:routing"];
  function platformOf(active) {
    if (window.SIA_PLATFORM && PLATFORMS[window.SIA_PLATFORM]) return window.SIA_PLATFORM;
    if (ROUTER_KEYS.some(k => active === k || (active && k.startsWith(active + ":") && location.hash === "#" + k.split(":")[1]))) return "router";
    if (active === "router") return "router";
    return "ia";
  }
  // v2.1：导航顶部产品切换器——展示当前产品，点击下拉切换（存 localStorage sia_product，工作台等按其聚焦）
  const _brandColors = {};
  async function brandColorOf(file) {
    if (!file) return { p: "#3E63DD", a: "#8E4EC6" };
    if (!_brandColors[file]) {
      try {
        const t = await fetch("./brand/" + file).then(r => r.json());
        _brandColors[file] = { p: (t.color || {}).primary || "#3E63DD",
                               a: (t.color || {}).accent || (t.color || {}).primary || "#8E4EC6" };
      } catch (e) { _brandColors[file] = { p: "#3E63DD", a: "#8E4EC6" }; }
    }
    return _brandColors[file];
  }
  // —— 轻量数据联动：任何页面改了产品 / 主题后，广播给同页其它区域即时刷新 ——
  let _swSlot = null;
  const _subs = {};   // { event: [handler] }
  function on(evt, fn) { (_subs[evt] = _subs[evt] || []).push(fn); return fn; }
  function emit(evt, payload) {
    if (evt === "products-changed" && _swSlot) {
      _swSlot.innerHTML = "";
      mountProductSwitcher(_swSlot);   // 切换器自身总是跟随产品变更重绘
    }
    (_subs[evt] || []).forEach(fn => { try { fn(payload); } catch (e) {} });
  }

  async function mountProductSwitcher(host) {
    // 首帧：用上次缓存的产品名立即画切换行（避免拉取期间导航下移抖动）；数据回来后原位替换
    let ghost = null;
    try {
      const meta = JSON.parse(localStorage.getItem("sia_product_meta") || "null");
      if (meta && meta.name) {
        ghost = el("button", { class: "np-btn", type: "button", disabled: "" }, [
          el("span", { class: "np-avatar", style: "width:22px;height:22px;background:var(--border)" }, [String(meta.name).slice(0, 1)]),
          el("span", { class: "np-meta" }, [el("span", { class: "np-name" }, [meta.name])]),
          el("span", { class: "fsel-caret" }, [icon("chevron", 13)]),
        ]);
        host.appendChild(ghost);
      }
    } catch (e) {}
    try {
      const { products } = await api("/api/products");
      if (ghost) { ghost.remove(); ghost = null; }
      if (!products || !products.length) return;
      const saved = localStorage.getItem("sia_product");
      let cur = products.find(p => p.product_id === saved) || products[0];
      try { localStorage.setItem("sia_product_meta", JSON.stringify({ name: cur.name })); } catch (e) {}
      const HUES = [["#3E63DD", "#8E4EC6"], ["#0FA968", "#1D7FBF"], ["#FF6B4A", "#D97706"],
                    ["#4F5BD5", "#D9569B"], ["#D97706", "#B85C38"], ["#334155", "#5B7A9D"]];
      const hueOf = (name) => { let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return HUES[h % HUES.length]; };
      const avatar = (name, size, colors) => {
        const [a, b] = colors ? [colors.p, colors.a] : hueOf(name);
        return el("span", { class: "np-avatar", style: `width:${size}px;height:${size}px;background:linear-gradient(135deg,${a},${b})` },
          [String(name).slice(0, 1)]);
      };
      // 切产品 = 换气质：顶部区与封面卡随当前产品主题色渐变
      const applyCtxColor = async (p2) => {
        const c2 = await brandColorOf(p2.brand_file);
        document.documentElement.style.setProperty("--ctx-p", c2.p);
        document.documentElement.style.setProperty("--ctx-a", c2.a);
        const av = host.querySelector(".np-avatar");
        if (av) av.style.background = `linear-gradient(135deg, ${c2.p}, ${c2.a})`;
      };
      const nameEl = el("span", { class: "np-name" }, [cur.name]);
      applyCtxColor(cur);
      const avaHost = el("span", { style: "display:inline-flex;flex:none" }, [avatar(cur.name, 22)]);
      const btn = el("button", { class: "np-btn", type: "button", title: "点击切换产品" }, [
        avaHost,
        el("span", { class: "np-meta" }, [nameEl]),
        el("span", { class: "fsel-caret" }, [icon("chevron", 13)]),
      ]);
      btn.onclick = () => {
        document.querySelectorAll(".menu-pop").forEach(n => n.remove());
        const pop = el("div", { class: "menu-pop np-pop", role: "listbox" }, [
          el("div", { class: "np-pop-head" }, ["切换产品",
            el("span", { class: "np-pop-count" }, [products.length + " 个"])]),
        ]);
        Promise.all(products.map(p => brandColorOf(p.brand_file))).then(cols => {
          pop.querySelectorAll(".np-card:not(.np-new) .np-avatar").forEach((av, i) => {
            if (cols[i]) av.style.background = `linear-gradient(135deg, ${cols[i].p}, ${cols[i].a})`;
          });
        });
        products.forEach(p => {
          const on = p.product_id === cur.product_id;
          pop.appendChild(el("button", {
            class: "np-card" + (on ? " on" : ""), role: "option",
            onclick: () => {
              pop.remove();
              localStorage.setItem("sia_product", p.product_id);
              localStorage.setItem("sia_product_meta", JSON.stringify({ name: p.name }));
              // 切产品 = 切它的风格主题：平台内预览（工作台 / 编辑器 / 组件模板）立即跟随
              if (p.brand_file) localStorage.setItem("brand_file", p.brand_file);
              cur = p;
              location.reload();
            } }, [
            avatar(p.name, 34),
            el("span", { class: "np-card-meta" }, [
              el("span", { class: "np-card-name" }, [p.name]),
              el("span", { class: "np-card-sub" }, [(p.card_ids || []).length + " 个组件实例"]),
            ]),
            on ? el("span", { class: "np-card-check" }, [icon("check", 15)]) : null,
          ]));
        });
                pop.appendChild(el("button", { class: "np-card np-new", role: "option", onclick: () => {
          location.href = "./products.html?new=1";
        } }, [
          el("span", { class: "np-avatar", style: "width:34px;height:34px;background:var(--primary-weak);color:var(--primary)" }, ["+"]),
          el("span", { class: "np-meta" }, [el("span", { class: "np-name", style: "color:var(--primary)" }, ["添加产品"])]),
        ]));
        document.body.appendChild(pop);
        const r = btn.getBoundingClientRect();
        pop.style.minWidth = r.width + "px";
        pop.style.left = (r.left + window.scrollX) + "px";
        pop.style.top = (r.bottom + window.scrollY + 6) + "px";
        const close = (e) => { if (!pop.contains(e.target) && !btn.contains(e.target)) { pop.remove(); document.removeEventListener("click", close, true); } };
        setTimeout(() => document.addEventListener("click", close, true), 0);
      };
      host.appendChild(btn);
    } catch (e) {}
  }

  function nav(active) {
    document.querySelectorAll(".sidenav").forEach(n => n.remove());
    const plat = PLATFORMS[platformOf(active)];
    const NAV_GROUPS = plat.groups;
    const side = el("aside", { class: "sidenav" }, [
      (() => {
        const a = el("a", { class: "logo", href: plat.home, title: "回到首页" });
        const mark = el("span", { class: "logo-mark", "aria-hidden": "true" });
        mark.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none">'
          + '<rect x="10.5" y="3" width="10.5" height="8" rx="3" fill="none" stroke="var(--primary)" stroke-width="1.7" opacity="0.45"/>'
          + '<path d="M3 12a4 4 0 0 1 4-4h6.5a4 4 0 0 1 4 4v2.5a4 4 0 0 1-4 4H9.8l-3.4 2.9c-.6.5-1.4.1-1.4-.7v-2.6A4 4 0 0 1 3 14.5z" fill="var(--primary)"/>'
          + '<circle cx="7.6" cy="13.2" r="1.15" fill="#fff"/><circle cx="10.75" cy="13.2" r="1.15" fill="#fff"/><circle cx="13.9" cy="13.2" r="1.15" fill="#fff"/>'
          + '</svg>';
        a.append(mark, el("span", {}, [plat.name]));
        return a;
      })(),
    ]);
    // 全局上下文：当前产品切换条（logo 正下方，作用于全站，不属于任何导航组）
    if (plat.productSwitcher) {
      const swSlot = el("div", { class: "np-context", style: "min-height:36px" });
      side.appendChild(swSlot);
      _swSlot = swSlot;
      mountProductSwitcher(swSlot);
    }
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
    // 底部全局区：跨产品的管理入口（产品与接入）+ 审计日志
    side.appendChild(el("div", { class: "nav-group", style: "margin-top:auto" }, [
      ...((plat.footItems || []).map(([key, name, href, ic]) => el("a", {
        class: "navlink" + (isActive(key) ? " active" : ""), href, "data-key": key,
      }, [icon(ic, 17), el("span", {}, [name])]))),
      el("a", { class: "navlink" + (active === "audit" ? " active" : ""), href: "./audit.html", "data-key": "audit" }, [
        icon("loglist", 17), el("span", {}, ["操作日志"]),
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

  // 说明信息收进图标悬停（砍文案）。自绘即时 tooltip：原生 title 出得慢且样式不可控，
  // 用户悬停常常只看到问号光标就划走了——info 图标必须一悬停就出内容
  let _tipEl = null;
  function _showTip(anchor, text) {
    _hideTip();
    _tipEl = el("div", { class: "help-tip", role: "tooltip" }, [text]);
    document.body.appendChild(_tipEl);
    const r = anchor.getBoundingClientRect();
    const tw = _tipEl.offsetWidth, th = _tipEl.offsetHeight;
    let left = r.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    let top = r.bottom + 7;
    if (top + th > window.innerHeight - 8) top = r.top - th - 7; // 底部放不下翻到上方
    _tipEl.style.left = left + "px";
    _tipEl.style.top = top + "px";
  }
  function _hideTip() { if (_tipEl) { _tipEl.remove(); _tipEl = null; } }
  window.addEventListener("scroll", _hideTip, true);
  function help(text) {
    const n = el("span", { class: "help", "aria-label": text, tabindex: "0" }, [icon("info", 14)]);
    n.onmouseenter = () => _showTip(n, text);
    n.onmouseleave = _hideTip;
    n.onfocus = () => _showTip(n, text);
    n.onblur = _hideTip;
    return n;
  }

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
  const fmtTick = (v) => {
    const a = Math.abs(v);
    if (a < 1e-9) return "0";
    if (a >= 100) return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    if (a >= 1) { const r = Math.round(v * 10) / 10; return r % 1 === 0 ? String(r) : r.toFixed(1); }
    return v.toFixed(2);
  };
  const GRID = () => Brand.chartPalette().grid || "#e1e0d9";

  function chartFrame(w, h) {
    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", style: "display:block" });
    return svg;
  }

  function lineChart(container, { series, labels, height = 180, unit = "", grid = true, gridStyle = "solid",
    gridCount = 3, yZero = true, lastEmph = true,
    lineWidth = 2, lineStyle = "solid", smooth = false, pointShow = true, pointShape = "circle", pointSize = 3,
    areaFill = true, areaOpacity = 0.16, axisShow = false, axisColor, valueLabels = false, lineColor }) {
    container.innerHTML = "";
    const pal = Brand.chartPalette().categorical;
    const w = 560, h = height, padL = 44, padR = 12, padT = 14, padB = 26;
    const svg = chartFrame(w, h);
    const all = series.flatMap(s => s.values);
    const maxV = (Math.max(...all) || 0) > 0 ? Math.max(...all) : 1;
    const minV = yZero ? Math.min(...all, 0) : Math.min(...all);
    const span = (maxV - minV) || 1;
    const x = i => padL + i * (w - padL - padR) / Math.max(1, labels.length - 1);
    const y = v => padT + (h - padT - padB) * (1 - (v - minV) / span);
    const dashOf = st => st === "dashed" ? "6 4" : st === "dotted" ? "2 4" : null;
    const GN = Math.max(2, Math.min(5, gridCount));
    for (let g = 0; g <= GN; g++) {
      const gy = padT + g * (h - padT - padB) / GN;
      if (grid) {
        const gl = svgEl("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, stroke: GRID(), "stroke-width": 1 });
        const dg = dashOf(gridStyle);
        if (dg) gl.setAttribute("stroke-dasharray", dg);
        svg.appendChild(gl);
      }
      const tl = svgEl("text", { x: padL - 6, y: gy + 4, "text-anchor": "end", "font-size": 10, fill: INK() });
      tl.textContent = fmtTick(maxV - g * span / GN);
      svg.appendChild(tl);
    }
    if (axisShow) svg.appendChild(svgEl("line", { x1: padL, y1: h - padB, x2: w - padR, y2: h - padB,
      stroke: axisColor || Brand.chartPalette().axis, "stroke-width": 1.2 }));
    labels.forEach((lb, i) => {
      if (labels.length > 10 && i % Math.ceil(labels.length / 8) !== 0) return;
      const tx = svgEl("text", { x: x(i), y: h - 8, "text-anchor": "middle", "font-size": 10, fill: INK() });
      tx.textContent = lb;
      svg.appendChild(tx);
    });
    const pathOf = (vs) => {
      const pts = vs.map((v, i) => [x(i), y(v)]);
      if (!smooth || pts.length < 3) return "M" + pts.map(p => p.join(",")).join(" L");
      // Catmull-Rom → cubic bezier 平滑曲线
      let d = `M${pts[0][0]},${pts[0][1]}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
        const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
        const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
        d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
      }
      return d;
    };
    series.forEach((s, si) => {
      const color = (si === 0 && lineColor) || pal[si % pal.length];
      if (areaFill && series.length === 1 && s.values.length > 1) {
        const gid = "lg" + Math.random().toString(36).slice(2, 8);
        const defs = svgEl("defs", {});
        defs.innerHTML = `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">` +
          `<stop offset="0" stop-color="${color}" stop-opacity="${areaOpacity}"/>` +
          `<stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient>`;
        svg.appendChild(defs);
        const d = pathOf(s.values) + ` L${x(s.values.length - 1)},${h - padB} L${x(0)},${h - padB} Z`;
        svg.appendChild(svgEl("path", { d, fill: `url(#${gid})`, stroke: "none" }));
      }
      const ln = svgEl("path", { d: pathOf(s.values), fill: "none", stroke: color,
        "stroke-width": lineWidth, "stroke-linejoin": "round", "stroke-linecap": "round" });
      const dl = dashOf(lineStyle);
      if (dl) ln.setAttribute("stroke-dasharray", dl);
      svg.appendChild(ln);
      s.values.forEach((v, i) => {
        const last = lastEmph && i === s.values.length - 1;
        if (!pointShow && !last) return;
        const r0 = last ? pointSize + 1.5 : pointSize;
        let c;
        if (pointShape === "square")
          c = svgEl("rect", { x: x(i) - r0, y: y(v) - r0, width: r0 * 2, height: r0 * 2, rx: 1,
            fill: last ? color : "var(--bg-elevated)", stroke: last ? "var(--bg-elevated)" : color, "stroke-width": last ? 2 : 1.5 });
        else if (pointShape === "diamond")
          c = svgEl("rect", { x: x(i) - r0, y: y(v) - r0, width: r0 * 2, height: r0 * 2, rx: 1,
            transform: `rotate(45 ${x(i)} ${y(v)})`,
            fill: last ? color : "var(--bg-elevated)", stroke: last ? "var(--bg-elevated)" : color, "stroke-width": last ? 2 : 1.5 });
        else
          c = svgEl("circle", { cx: x(i), cy: y(v), r: r0, fill: last ? color : "var(--bg-elevated)",
            stroke: last ? "var(--bg-elevated)" : color, "stroke-width": last ? 2 : 1.5 });
        svgTitle(c, `${labels[i]} · ${s.name}: ${v}${unit}`);
        svg.appendChild(c);
        if (valueLabels && s.values.length <= 12) {
          const vt = svgEl("text", { x: x(i), y: y(v) - r0 - 4, "text-anchor": "middle", "font-size": 10,
            fill: "var(--text-secondary)", style: "font-variant-numeric:tabular-nums" });
          vt.textContent = fmtTick(v);
          svg.appendChild(vt);
        }
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

  function barChart(container, { categories, values, height = 190, unit = "", color, horizontal = false, maxValue, format, grid = true, gridStyle = "solid", valueLabels = true, barWidthPct = 0.55, barRadius = 4, axisColor }) {
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
    const bw = Math.min(46, slot * Math.max(0.2, Math.min(0.75, barWidthPct)));
    for (let g = 0; g <= 3; g++) {
      const gy = padT + g * (h - padT - padB) / 3;
      if (grid) {
        const gl = svgEl("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, stroke: GRID(), "stroke-width": 1 });
        if (gridStyle === "dashed") gl.setAttribute("stroke-dasharray", "6 4");
        svg.appendChild(gl);
      }
      const tl = svgEl("text", { x: padL - 6, y: gy + 4, "text-anchor": "end", "font-size": 10, fill: INK() });
      tl.textContent = fmtTick(maxV * (1 - g / 3));
      svg.appendChild(tl);
    }
    values.forEach((v, i) => {
      const bh = Math.max(2, (h - padT - padB) * v / maxV);
      const bx = padL + i * slot + (slot - bw) / 2;
      const rr = Math.max(0, Math.min(8, barRadius, bw / 2));
      const rect = svgEl("path", {
        d: rr > 0
          ? `M${bx},${h - padB} v${-(bh - rr)} q0,-${rr} ${rr},-${rr} h${bw - rr * 2} q${rr},0 ${rr},${rr} v${bh - rr} z`
          : `M${bx},${h - padB} v${-bh} h${bw} v${bh} z`,
        fill: barColor,
      });
      svgTitle(rect, `${categories[i]}: ${v}${unit}`);
      svg.appendChild(rect);
      if (valueLabels && values.length <= 12) {
        const vt = svgEl("text", { x: bx + bw / 2, y: h - padB - bh - 5, "text-anchor": "middle",
          "font-size": 10, fill: "var(--text-secondary)", style: "font-variant-numeric:tabular-nums" });
        vt.textContent = fmtTick(v);
        svg.appendChild(vt);
      }
      const tx = svgEl("text", { x: bx + bw / 2, y: h - 10, "text-anchor": "middle", "font-size": 10, fill: INK() });
      tx.textContent = String(categories[i]).slice(0, 6);
      svg.appendChild(tx);
    });
    svg.appendChild(svgEl("line", { x1: padL, y1: h - padB, x2: w - padR, y2: h - padB, stroke: axisColor || Brand.chartPalette().axis, "stroke-width": 1 }));
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

  // ---------- Figma 式色盘：SV 面板 + 色相条 + 透明度条 + HEX 输入 ----------
  function hsvToRgb(h, s, v) {
    const f = (n) => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
    return [f(5), f(3), f(1)].map(x => Math.round(x * 255));
  }
  function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h = Math.round(h * 60); if (h < 0) h += 360;
    return [h, mx ? d / mx : 0, mx];
  }
  function hexParse(hex) {
    const m = String(hex || "").trim().match(/^#?([0-9a-f]{6})([0-9a-f]{2})?$/i);
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return { r: n >> 16, g: (n >> 8) & 255, b: n & 255, a: m[2] ? parseInt(m[2], 16) / 255 : 1 };
  }
  function toHex(r, g, b, a) {
    const p = (x) => x.toString(16).padStart(2, "0");
    return "#" + p(r) + p(g) + p(b) + (a < 1 ? p(Math.round(a * 255)) : "");
  }
  function colorPicker(anchorEl, { value = "#3E63DD", alpha = true, onChange } = {}) {
    document.querySelectorAll(".cp-pop").forEach(n => n.remove());
    const init = hexParse(value) || { r: 62, g: 99, b: 221, a: 1 };
    let [h, s, v] = rgbToHsv(init.r, init.g, init.b);
    let a = init.a;
    const pop = el("div", { class: "menu-pop cp-pop" });
    const sv = el("div", { class: "cp-sv" }, [el("i", { class: "cp-cursor" })]);
    const hue = el("div", { class: "cp-slider cp-hue" }, [el("i", { class: "cp-knob" })]);
    const al = alpha ? el("div", { class: "cp-slider cp-alpha" }, [el("i", { class: "cp-knob" })]) : null;
    const hexIn = el("input", { type: "text", class: "num cp-hex", spellcheck: "false", maxlength: "9" });
    const alIn = alpha ? el("input", { type: "number", class: "num cp-a", min: "0", max: "100" }) : null;
    const emit = () => {
      const [r, g, b] = hsvToRgb(h, s, v);
      const hex = toHex(r, g, b, a);
      hexIn.value = hex.toUpperCase();
      if (alIn) alIn.value = String(Math.round(a * 100));
      sv.style.background = `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, hsl(${h},100%,50%))`;
      sv.querySelector(".cp-cursor").style.cssText = `left:${s * 100}%;top:${(1 - v) * 100}%`;
      hue.querySelector(".cp-knob").style.left = (h / 360 * 100) + "%";
      if (al) {
        al.style.setProperty("--cp-c", `rgb(${r},${g},${b})`);
        al.querySelector(".cp-knob").style.left = (a * 100) + "%";
      }
      onChange && onChange(hex);
    };
    const drag = (elx, fn) => {
      const move = (e) => {
        const r2 = elx.getBoundingClientRect();
        fn(Math.max(0, Math.min(1, (e.clientX - r2.left) / r2.width)),
           Math.max(0, Math.min(1, (e.clientY - r2.top) / r2.height)));
        emit();
      };
      elx.addEventListener("mousedown", (e) => {
        e.preventDefault(); move(e);
        const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
      });
    };
    drag(sv, (x, y) => { s = x; v = 1 - y; });
    drag(hue, (x) => { h = Math.round(x * 360); });
    if (al) drag(al, (x) => { a = Math.round(x * 100) / 100; });
    hexIn.onchange = () => {
      const p2 = hexParse(hexIn.value);
      if (p2) { [h, s, v] = rgbToHsv(p2.r, p2.g, p2.b); a = p2.a; }
      emit();
    };
    if (alIn) alIn.onchange = () => { a = Math.max(0, Math.min(100, Number(alIn.value) || 0)) / 100; emit(); };
    pop.append(sv, hue);
    if (al) pop.appendChild(al);
    pop.appendChild(el("div", { class: "cp-inputs" }, [hexIn,
      ...(alIn ? [alIn, el("span", { class: "muted", style: "font-size:11px" }, ["%"])] : [])]));
    document.body.appendChild(pop);
    const r3 = anchorEl.getBoundingClientRect();
    pop.style.position = "fixed";
    pop.style.top = Math.min(r3.bottom + 6, window.innerHeight - pop.offsetHeight - 12) + "px";
    pop.style.left = Math.min(r3.left, window.innerWidth - pop.offsetWidth - 12) + "px";
    const close = (e) => { if (!pop.contains(e.target) && e.target !== anchorEl) { pop.remove(); document.removeEventListener("mousedown", close, true); } };
    setTimeout(() => document.addEventListener("mousedown", close, true), 0);
    emit();
    return pop;
  }

  function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  // ---------- 内联 SVG 图标（无 emoji，描边 1.6，随 currentColor 着色） ----------
  const ICON_PATHS = {
    // 魔法棒：一根斜杖 + 杖头一颗实心四角星 + 一粒小光点。16px 下仍清晰，不再是一堆碎星
    wand: '<path d="M3.5 20.5 13 11" stroke-width="2"/><path d="M16.5 2.5l1.3 3.2 3.2 1.3-3.2 1.3-1.3 3.2-1.3-3.2L12 7l3.2-1.3z" fill="currentColor" stroke="none"/><circle cx="7" cy="6" r="1" fill="currentColor" stroke="none"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
    thumbup: '<path d="M7 11v9M7 11l3.2-6.4A1.8 1.8 0 0 1 13.6 5v4h4.6a1.8 1.8 0 0 1 1.8 2.1l-1.1 6.4a1.8 1.8 0 0 1-1.8 1.5H7"/>',
    alert: '<path d="M12 4 21 19.5H3z" stroke-linejoin="round"/><path d="M12 10.2v4M12 16.8h.01"/>',
    tplselect: '<rect x="4" y="5" width="16" height="6" rx="2"/><circle cx="7.5" cy="8" r="1.2" fill="currentColor" stroke="none"/><rect x="4" y="14" width="16" height="6" rx="2"/><circle cx="7.5" cy="17" r="1.2"/>',
    tplform: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M7 9h7M7 13h10M7 17h6"/>',
    tplpref: '<rect x="3" y="6" width="8.5" height="12" rx="2"/><rect x="12.5" y="6" width="8.5" height="12" rx="2"/><path d="m14.8 11.5 1.4 1.4 2.6-2.8"/>',
    tplrating: '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="2"/>',
    tplrank: '<path d="M7 5v14M7 19l-2.5-2.5M7 19l2.5-2.5"/><path d="M13 6h8M13 12h6M13 18h4"/>',
    tpltable: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M9.5 10v9M15.5 10v9"/>',
    tplpie: '<circle cx="12" cy="12" r="8"/><path d="M12 4v8l6.5 4.5"/>',
    tplmetric: '<rect x="4" y="5" width="16" height="14" rx="3"/><path d="M8 15V9.5M12 15v-3M16 15V8"/>',
    tpltimeline: '<path d="M6 4v16"/><circle cx="6" cy="7" r="1.6" fill="currentColor" stroke="none"/><circle cx="6" cy="13" r="1.6"/><path d="M10 7h9M10 13h7"/>',
    tplsteps: '<circle cx="5" cy="12" r="2.2" fill="currentColor" stroke="none"/><path d="M7.2 12h4"/><circle cx="13.5" cy="12" r="2.2"/><path d="M15.7 12h3"/><circle cx="21" cy="12" r="1.4"/>',
    tplcompare: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M12 5v14"/><path d="m6.5 14.5 1.2 1.2 2-2.2"/>',
    tplhl: '<path d="M5 7h14M5 12h10"/><rect x="4" y="15" width="12" height="4.5" rx="1.5" fill="currentColor" stroke="none" opacity=".25"/>',
    thumbdown: '<path d="M17 13V4M17 13l-3.2 6.4A1.8 1.8 0 0 1 10.4 19v-4H5.8A1.8 1.8 0 0 1 4 12.9l1.1-6.4A1.8 1.8 0 0 1 6.9 5H17"/>',
    arrowup: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    arrowdown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    trash: '<path d="M4 7h16M10 7V5h4v2M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    check: '<path d="m5 12 4.5 4.5L19 7"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    board: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 9h16M9 9v11"/>',
    box: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5"/><path d="M12 12v9"/>',
    loglist: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.3" fill="currentColor" stroke="none"/>',
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
    pipette: '<path d="m14.5 6.5 3 3"/><path d="M17.8 3.2a2.1 2.1 0 0 1 3 3l-2.8 2.8-3-3z"/><path d="M15 8 6.2 16.8a1.6 1.6 0 0 0-.5 1.1v1.3l-1.2 1.2a.8.8 0 0 0 1.1 1.1l1.2-1.2h1.3c.4 0 .8-.2 1.1-.5L18 11"/>',
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


  return { api, el, toast, modal, drawer, confirm: confirmDialog, withBusy, loading, menu, fancySelect, tagSelect, ctName, ctChip, debounce, colorPicker,
    icon, iconBtn, shortId, idChip, keyField, chatMock, tagInput, toggle, help,
    nav, fmtCost, fmtMs, fmtTs, fmtPct, lineChart, barChart, stackedBars, on, emit, brandColorOf };
})();
