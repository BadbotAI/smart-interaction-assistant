// 品牌 token 注入：加载 brand-tokens.*.json，作用域限定在 .brand-scope（渲染出的组件），平台界面不受影响。
window.Brand = (function () {
  let current = null;

  const VAR_MAP = {
    "color.primary": "--primary",
    "color.primary_weak": "--primary-weak",
    "color.bg_page": "--bg-page",
    "color.bg_surface": "--bg-surface",
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
    // 生效风格是租户级设置（服务端），本地记录仅作降级
    let file = localStorage.getItem("brand_file") || "brand-tokens.default.json";
    try {
      const r = await fetch("/api/brands/active");
      file = (await r.json()).file || file;
    } catch (e) { /* 服务端不可达时用本地记录 */ }
    try { await load(file); } catch (e) { await load("brand-tokens.default.json"); }
  }

  function chartPalette() {
    return (current && current.chart) || {
      categorical: ["#1DA1F2", "#17B890", "#F2C14E", "#2ECC71", "#E0446E", "#6E4BD8", "#B97A17", "#E5484D"],
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
