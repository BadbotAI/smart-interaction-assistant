// 品牌 token 注入：加载 brand-tokens.*.json，作用域限定在 .brand-scope（渲染出的组件），平台界面不受影响。
window.Brand = (function () {
  let current = null;

  const VAR_MAP = {
    "color.primary": "--primary",
    "color.primary_weak": "--primary-weak",
    "color.bg_page": "--bg-page",
    "color.bg_surface": "--bg-surface",
    "color.bg_elevated": "--bg-elevated",
    "color.bg_sunken": "--bg-sunken",
    "color.text_primary": "--text-primary",
    "color.text_secondary": "--text-secondary",
    "color.text_muted": "--text-muted",
    "color.border": "--border",
    "color.success": "--success",
    "color.warning": "--warning",
    "color.danger": "--danger",
    "radius.card": "--radius-card",
    "radius.control": "--radius-control",
    "radius.chip": "--radius-chip",
    "font.family": "--font-family",
    "font.weight_base": "--font-weight-base",
    "font.size_base": "--font-base",
    "font.size_small": "--font-small",
    "font.size_title": "--font-title",
    "font.size_hero": "--font-hero",
    "spacing.unit": "--space",
    "spacing.card_padding": "--card-pad",
    "spacing.gap": "--gap",
    "motion.duration_fast": "--dur-fast",
    "motion.duration_base": "--dur-base",
    "chart.grid": "--chart-grid",
    "chart.axis": "--chart-axis",
    // v2 语义 token：强调色 / 焦点环 / 阴影 / 密度
    "color.accent": "--brand-accent",
    "color.ring": "--brand-ring",
    "shadow": "--brand-shadow",
    "density": "--brand-density",
  };

  function get(obj, path) {
    return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }

  // 品牌 token 只作用于「组件」（.brand-scope 容器），平台界面保持自身风格
  function apply(tokens) {
    current = tokens;
    let decls = "";
    for (const [path, cssVar] of Object.entries(VAR_MAP)) {
      const v = get(tokens, path);
      if (v !== undefined) decls += `${cssVar}:${v};`;
    }
    let st = document.getElementById("brand-style");
    if (!st) { st = document.createElement("style"); st.id = "brand-style"; document.head.appendChild(st); }
    st.textContent = `.brand-scope{${decls}}`;
  }

  async function load(file) {
    const res = await fetch("./brand/" + file);
    apply(await res.json());
    localStorage.setItem("brand_file", file);
  }

  async function init() {
    // 优先级：当前产品的风格主题（切产品 = 切风格）> 租户级 active > 本地记录
    let file = localStorage.getItem("brand_file") || "brand-tokens.default.json";
    const pid = localStorage.getItem("sia_product");
    let fromProduct = null;
    if (pid) {
      try {
        const pr = await fetch("/api/products").then(x => x.json());
        fromProduct = ((pr.products || []).find(p => p.product_id === pid) || {}).brand_file;
      } catch (e) {}
    }
    if (fromProduct) file = fromProduct;
    else {
      try {
        const r = await fetch("/api/brands/active");
        file = (await r.json()).file || file;
      } catch (e) { /* 服务端不可达时用本地记录 */ }
    }
    if (window._editingStyle) return; // 风格编辑器预览中，勿覆盖
    try { await load(file); } catch (e) { await load("brand-tokens.default.json"); }
  }

  function chartPalette() {
    return (current && current.chart) || {
      categorical: ["#3E63DD", "#0FA3A3", "#8E4EC6", "#EE7712", "#D6409F", "#30A46C", "#D6970A", "#E5484D"],
      sequential: ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
      grid: "#e1e0d9", axis: "#c3c2b7",
    };
  }

  async function mountSwitcher(selectEl) {
    const res = await fetch("/api/brands");
    const { brands } = await res.json();
    const cur = localStorage.getItem("brand_file") || "brand-tokens.default.json";
    selectEl.innerHTML = brands.map(b =>
      `<option value="${b.file}" ${b.file === cur ? "selected" : ""}>${b.brand_name}</option>`).join("");
    selectEl.onchange = () => load(selectEl.value);
  }

  return { init, load, apply, chartPalette, mountSwitcher, get: () => current };
})();
