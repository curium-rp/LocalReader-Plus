import { fetchJSON } from "./api.js";
import { renderIcons } from "./ui.js";
import { bindHorizontalRenderState, layoutSpreads } from "./horizontal.js";
import { bindRenderState, syncReaderGeometry } from "./reader-layout.js";
import { state } from "./state.js";

export const DEFAULT_RENDER = {
  font_family: "Georgia",
  font_size: 18,
  font_weight: 400,
  line_height: 1.8,
  paragraph_spacing: 1.1,
  text_align: "justify",
  text_indent: true,
  hyphenation: true,
  indent_mode: "follow",
  h1_align: "center",
  h2_align: "left",
  h3_align: "left",
  two_page_landscape: false,
  horizontal_mode: false,
  margin_left: 8,
  margin_right: 8,
  margins_linked: true,
  center_gutter: 3.5,
  landscape_outer_margin: 4,
  measure_lock: true,
  sidebar_auto_collapse: "auto",
};

const SHARED_FONTS = [
  "Georgia",
  "Times New Roman",
  "Times",
  "Palatino",
  "Garamond",
  "Arial",
  "Helvetica",
  "Verdana",
  "Trebuchet MS",
  "Tahoma",
  "Courier New",
  "Courier",
];

const OS_FONTS = {
  windows: [
    "Segoe UI",
    "Calibri",
    "Cambria",
    "Candara",
    "Consolas",
    "Constantia",
    "Corbel",
    "Sitka Text",
    "Bahnschrift",
    "Cascadia Code",
    "Palatino Linotype",
    "Lucida Console",
    "Lucida Sans Unicode",
    "Comic Sans MS",
    "Impact",
    "Microsoft YaHei",
    "Malgun Gothic",
  ],
  macos: [
    "Helvetica Neue",
    "Avenir",
    "Avenir Next",
    "Menlo",
    "Monaco",
    "New York",
    "Charter",
    "Iowan Old Style",
    "Lucida Grande",
    "Hoefler Text",
    "Optima",
    "Futura",
    "Gill Sans",
    "Apple Garamond",
    "Seravek",
    "Athelas",
  ],
  linux: [
    "Ubuntu",
    "Ubuntu Mono",
    "Cantarell",
    "DejaVu Sans",
    "DejaVu Serif",
    "DejaVu Sans Mono",
    "Liberation Serif",
    "Liberation Sans",
    "Liberation Mono",
    "Noto Serif",
    "Noto Sans",
    "Noto Sans Mono",
    "FreeSerif",
    "FreeSans",
    "FreeMono",
    "Nimbus Roman",
    "Nimbus Sans",
    "Source Serif 4",
    "Source Sans 3",
    "IBM Plex Serif",
    "IBM Plex Sans",
    "Fira Sans",
    "Fira Code",
    "Roboto",
    "Roboto Serif",
  ],
};

const SERIF_HINTS = new Set([
  "georgia",
  "times new roman",
  "times",
  "palatino",
  "palatino linotype",
  "garamond",
  "cambria",
  "constantia",
  "sitka text",
  "charter",
  "iowan old style",
  "hoefler text",
  "new york",
  "liberation serif",
  "dejavu serif",
  "noto serif",
  "freeserif",
  "nimbus roman",
  "source serif 4",
  "ibm plex serif",
  "roboto serif",
  "apple garamond",
  "athelas",
]);

const MONO_HINTS = new Set([
  "courier new",
  "courier",
  "consolas",
  "cascadia code",
  "lucida console",
  "menlo",
  "monaco",
  "ubuntu mono",
  "dejavu sans mono",
  "liberation mono",
  "noto sans mono",
  "freemono",
  "fira code",
]);

let renderState = { ...DEFAULT_RENDER };
let saveTimer = null;
let fontsCache = null;
let weightSyncGen = 0;

const FALLBACK_WEIGHTS = [400, 700];

export function getRenderState() {
  return renderState;
}

bindRenderState(getRenderState);
bindHorizontalRenderState(getRenderState);

function detectOS() {
  const ua = navigator.userAgent || "";
  const platform = navigator.userAgentData?.platform || navigator.platform || "";
  const blob = `${platform} ${ua}`.toLowerCase();
  if (blob.includes("mac") || blob.includes("iphone") || blob.includes("ipad")) return "macos";
  if (blob.includes("win")) return "windows";
  return "linux";
}

function isFontAvailable(fontName) {
  if (!fontName) return false;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;
  const sample = "mmmmmmmmmmlliWw@123";
  const size = "72px";
  ctx.font = `${size} monospace`;
  const baseline = ctx.measureText(sample).width;
  ctx.font = `${size} "${fontName}", monospace`;
  const mixed = ctx.measureText(sample).width;
  ctx.font = `${size} "${fontName}", serif`;
  const mixedSerif = ctx.measureText(sample).width;
  ctx.font = `${size} serif`;
  const serifWidth = ctx.measureText(sample).width;
  return mixed !== baseline || mixedSerif !== serifWidth;
}

function uniqueFonts(list) {
  const seen = new Set();
  const out = [];
  for (const name of list) {
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(name);
  }
  return out;
}

export function detectSystemFonts() {
  if (fontsCache) return fontsCache;
  const os = detectOS();
  const candidates = uniqueFonts([...(OS_FONTS[os] || []), ...SHARED_FONTS]);
  const detected = candidates.filter((name) => isFontAvailable(name));
  fontsCache = {
    os,
    detected,
    generics: ["serif", "sans-serif", "monospace"],
  };
  return fontsCache;
}

function fallbackFor(fontName) {
  const key = String(fontName || "").toLowerCase();
  if (key === "serif" || key === "sans-serif" || key === "monospace") return "";
  if (MONO_HINTS.has(key) || key.includes("mono") || key.includes("console")) return "monospace";
  if (SERIF_HINTS.has(key) || key.includes("serif")) return "serif";
  return "sans-serif";
}

export function fontStack(fontName) {
  const name = fontName || "Georgia";
  if (name === "serif" || name === "sans-serif" || name === "monospace") return name;
  const fallback = fallbackFor(name);
  return `"${name}", ${fallback}`;
}

function defaultFontFamily(detected) {
  if (detected.includes("Georgia")) return "Georgia";
  const serifHit = detected.find((n) => SERIF_HINTS.has(n.toLowerCase()));
  return serifHit || detected[0] || "serif";
}

function fillFontSelect(preferred) {
  const select = document.getElementById("fontFamilySelect");
  if (!select) return;
  const { os, detected, generics } = detectSystemFonts();
  const chosen = preferred && (detected.includes(preferred) || generics.includes(preferred))
    ? preferred
    : defaultFontFamily(detected);

  select.innerHTML = "";
  const detectedGroup = document.createElement("optgroup");
  detectedGroup.label = `System (${os})`;
  detected.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    opt.style.fontFamily = fontStack(name);
    detectedGroup.appendChild(opt);
  });
  if (detected.length) select.appendChild(detectedGroup);

  const genericGroup = document.createElement("optgroup");
  genericGroup.label = "Generic";
  generics.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    genericGroup.appendChild(opt);
  });
  select.appendChild(genericGroup);

  if (![...detected, ...generics].includes(chosen) && preferred) {
    const opt = document.createElement("option");
    opt.value = preferred;
    opt.textContent = preferred;
    select.appendChild(opt);
    select.value = preferred;
  } else {
    select.value = chosen;
  }
  renderState.font_family = select.value;
}

function alphaSum(imgData) {
  let sum = 0;
  for (let i = 3; i < imgData.length; i += 4) {
    sum += imgData[i];
  }
  return sum;
}

export async function detectSupportedWeights(fontFamily) {
  if (document.fonts && document.fonts.ready) {
    await document.fonts.ready;
  }

  const canvas = document.createElement("canvas");
  try {
    canvas.width = 48;
    canvas.height = 48;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return { isVariable: false, weights: [...FALLBACK_WEIGHTS] };

    const testChar = "B";
    const weightsToTest = [400, 700, 100, 200, 300, 500, 600, 800, 900];
    const renderedWeights = [];
    const pixelSums = new Map();
    const family = fontFamily || "serif";

    for (const weight of weightsToTest) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = `${weight} 32px "${family}", monospace`;
      ctx.fillText(testChar, 8, 36);

      const sum = alphaSum(ctx.getImageData(0, 0, canvas.width, canvas.height).data);
      if (sum > 0 && !pixelSums.has(sum)) {
        pixelSums.set(sum, weight);
        renderedWeights.push(weight);
      }
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = `450 32px "${family}", monospace`;
    ctx.fillText(testChar, 8, 36);
    const midSum = alphaSum(ctx.getImageData(0, 0, canvas.width, canvas.height).data);
    const isVariable = midSum > 0 && !pixelSums.has(midSum);

    return {
      isVariable,
      weights: renderedWeights.length > 1
        ? renderedWeights.sort((a, b) => a - b)
        : [...FALLBACK_WEIGHTS],
    };
  } finally {
    canvas.width = canvas.height = 0;
  }
}

function findNearestWeight(target, validWeights) {
  const list = validWeights && validWeights.length ? validWeights : FALLBACK_WEIGHTS;
  return list.reduce((prev, curr) =>
    Math.abs(curr - target) < Math.abs(prev - target) ? curr : prev
  );
}

function applyWeightValue(weight) {
  const val = Number(weight);
  renderState.font_weight = val;
  document.documentElement.style.setProperty("--reader-font-weight", String(val));
}

export async function syncWeightSlider(fontFamily) {
  const slider = document.getElementById("fontWeightSlider");
  const display = document.getElementById("fontWeightVal");
  if (!slider) return;

  const gen = ++weightSyncGen;
  const { isVariable, weights } = await detectSupportedWeights(fontFamily);
  if (gen !== weightSyncGen) return;

  const currentVal = Number(slider.value) || renderState.font_weight || 400;
  const prevWeight = renderState.font_weight;

  if (isVariable) {
    slider.min = "100";
    slider.max = "900";
    slider.step = "10";
    slider.value = String(currentVal);
    if (display) display.textContent = String(currentVal);
    applyWeightValue(currentVal);

    slider.oninput = (e) => {
      const val = Number(e.target.value);
      if (display) display.textContent = String(val);
      applyWeightValue(val);
      persistRender();
    };
  } else {
    const minW = Math.min(...weights);
    const maxW = Math.max(...weights);
    slider.min = String(minW);
    slider.max = String(maxW);
    slider.step = "any";

    const snappedVal = findNearestWeight(currentVal, weights);
    slider.value = String(snappedVal);
    if (display) display.textContent = String(snappedVal);
    applyWeightValue(snappedVal);

    slider.oninput = (e) => {
      const snapped = findNearestWeight(Number(e.target.value), weights);
      slider.value = String(snapped);
      if (display) display.textContent = String(snapped);
      applyWeightValue(snapped);
      persistRender();
    };
  }

  if (renderState.font_weight !== prevWeight) persistRender(true);
}

function clampMargin(value) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n)) return DEFAULT_RENDER.margin_left;
  return Math.max(0, Math.min(35, n));
}

function clampCenterGutter(value) {
  const n = parseFloat(value);
  if (!Number.isFinite(n)) return DEFAULT_RENDER.center_gutter;
  return Math.max(3, Math.min(12, Math.round(n * 4) / 4));
}

function clampOuterMargin(value) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n)) return DEFAULT_RENDER.landscape_outer_margin;
  return Math.max(0, Math.min(15, n));
}

function paneRawWidthPx() {
  const pane = document.getElementById("readerContent");
  if (!pane) return Math.max(1, Math.floor(window.innerWidth || 1));
  return Math.max(1, Math.floor(pane.getBoundingClientRect().width));
}

function rootFontPx() {
  return parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
}

/** Two-page AUTO: outer % and gutter rem from pane width. Single-column AUTO: outer % only. */
export function computeAutoBalance(rawWidth = paneRawWidthPx()) {
  const W = Math.max(1, Number(rawWidth) || paneRawWidthPx());
  const root = rootFontPx();
  const twoPage = !!renderState.two_page_landscape;
  if (twoPage) {
    const outer = Math.max(3, Math.min(8, Math.floor(W / 300) + 1));
    const effective = W * (1 - (2 * outer) / 100);
    const gapPx = Math.max(48, Math.min(160, effective * 0.05));
    const gutter = clampCenterGutter(Math.round(gapPx / (root * 0.25)) * 0.25);
    return { outer, gutter };
  }
  const target = Math.min(700, W * 0.9);
  const outer = Math.max(4, Math.min(15, Math.round(((W - target) / (2 * W)) * 100)));
  return { outer, gutter: clampCenterGutter(renderState.center_gutter) };
}

function autoBalanceMatches() {
  const preset = computeAutoBalance();
  const outerOk = clampOuterMargin(renderState.landscape_outer_margin) === preset.outer;
  if (!renderState.two_page_landscape) return outerOk;
  return outerOk && clampCenterGutter(renderState.center_gutter) === preset.gutter;
}

/** Outer/Gap controls follow two-page landscape only — not horizontal mode. */
function usesOuterGapUi() {
  return !!renderState.two_page_landscape;
}

function formatGutterRem(value) {
  return `${clampCenterGutter(value).toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}rem`;
}

function t(path, fallback) {
  const keys = String(path).split(".");
  let val = state.translations;
  for (const k of keys) val = val ? val[k] : null;
  return val || fallback;
}

export function applyReaderTypography() {
  const root = document.documentElement;
  root.style.setProperty("--reader-font-family", fontStack(renderState.font_family));
  root.style.setProperty("--reader-font-size", `${renderState.font_size}px`);
  root.style.setProperty("--reader-font-weight", String(renderState.font_weight));
  root.style.setProperty("--reader-line-height", String(renderState.line_height));
  document.body.classList.toggle("tight-lines", Number(renderState.line_height) < 1.45);
  root.style.setProperty("--reader-paragraph-spacing", `${renderState.paragraph_spacing}em`);
  root.style.setProperty("--reader-text-align", renderState.text_align);
  root.style.setProperty("--reader-h1-align", renderState.h1_align);
  root.style.setProperty("--reader-h2-align", renderState.h2_align);
  root.style.setProperty("--reader-h3-align", renderState.h3_align);
  root.style.setProperty("--reader-margin-left", `${clampMargin(renderState.margin_left)}%`);
  root.style.setProperty("--reader-margin-right", `${clampMargin(renderState.margin_right)}%`);
  root.style.setProperty("--reader-center-gutter", String(clampCenterGutter(renderState.center_gutter)));
  root.style.setProperty("--reader-landscape-outer", String(clampOuterMargin(renderState.landscape_outer_margin)));
  root.style.setProperty("--reader-landscape-margin", `${clampOuterMargin(renderState.landscape_outer_margin)}%`);
  syncReaderGeometry();

  document.body.classList.toggle("two-page-landscape", !!renderState.two_page_landscape);

  const textContent = document.getElementById("textContent");
  if (textContent) {
    textContent.style.fontSize = "";
    textContent.style.lineHeight = "";
    textContent.classList.toggle("indent-on", !!renderState.text_indent);
    textContent.classList.toggle("indent-follow", renderState.indent_mode === "follow");
    textContent.classList.toggle("indent-all", renderState.indent_mode === "all");
    textContent.classList.toggle("hyphens-on", renderState.hyphenation !== false);
    textContent.classList.toggle("two-page", !!renderState.two_page_landscape);
    textContent.classList.toggle("horizontal", !!renderState.horizontal_mode);
    applyContentLang(textContent);
  }

  layoutSpreads();

  const preview = document.getElementById("currentSentencePreview");
  if (preview) {
    preview.style.fontSize = "";
    preview.style.lineHeight = "";
  }

  syncControlUi();
}

function applyContentLang(textContent) {
  if (!textContent) return;
  const bookLang = state.bookLanguage;
  const pageLang = state.pageLanguage;
  const fallback = document.documentElement.lang || "en";
  textContent.lang = pageLang || bookLang || fallback;
}

function syncControlUi() {
  const horiz = !!renderState.horizontal_mode;
  const twoPage = !!renderState.two_page_landscape;

  const fontSizeSlider = document.getElementById("fontSizeSlider");
  const textSizeVal = document.getElementById("textSizeVal");
  const textSizeBadge = document.getElementById("textSizeBadge");
  if (fontSizeSlider) fontSizeSlider.value = String(renderState.font_size);
  if (textSizeVal) textSizeVal.textContent = String(renderState.font_size);
  if (textSizeBadge) textSizeBadge.textContent = String(renderState.font_size);

  const weightSlider = document.getElementById("fontWeightSlider");
  const weightVal = document.getElementById("fontWeightVal");
  if (weightSlider) weightSlider.value = String(renderState.font_weight);
  if (weightVal) weightVal.textContent = String(renderState.font_weight);

  const lineSlider = document.getElementById("lineHeightSlider");
  const lineVal = document.getElementById("lineHeightVal");
  const linePct = Math.round(renderState.line_height * 100);
  if (lineSlider) lineSlider.value = String(linePct);
  if (lineVal) lineVal.textContent = `${linePct}%`;

  const paraSlider = document.getElementById("paragraphSpacingSlider");
  const paraVal = document.getElementById("paragraphSpacingVal");
  const paraEm = Number(renderState.paragraph_spacing).toFixed(1);
  if (paraSlider) paraSlider.value = paraEm;
  if (paraVal) paraVal.textContent = `${paraEm}em`;

  const marginLeftSlider = document.getElementById("marginLeftSlider");
  const marginRightSlider = document.getElementById("marginRightSlider");
  const marginLeftVal = document.getElementById("marginLeftVal");
  const marginRightVal = document.getElementById("marginRightVal");
  const marginLeftLetter = document.getElementById("marginLeftLetter");
  const marginRightLetter = document.getElementById("marginRightLetter");
  const outerPct = clampOuterMargin(renderState.landscape_outer_margin);
  const gutterRem = clampCenterGutter(renderState.center_gutter);
  const outerGapUi = usesOuterGapUi();

  if (marginLeftSlider) {
    marginLeftSlider.disabled = false;
    marginLeftSlider.style.opacity = "1";
    marginLeftSlider.style.cursor = "pointer";
    if (outerGapUi) {
      marginLeftSlider.min = "0";
      marginLeftSlider.max = "15";
      marginLeftSlider.step = "1";
      marginLeftSlider.value = String(outerPct);
    } else {
      marginLeftSlider.min = "0";
      marginLeftSlider.max = "35";
      marginLeftSlider.step = "1";
      marginLeftSlider.value = String(renderState.margin_left);
    }
  }
  if (marginRightSlider) {
    marginRightSlider.disabled = false;
    marginRightSlider.style.opacity = "1";
    marginRightSlider.style.cursor = "pointer";
    if (outerGapUi) {
      marginRightSlider.min = "3";
      marginRightSlider.max = "12";
      marginRightSlider.step = "0.25";
      marginRightSlider.value = String(gutterRem);
    } else {
      marginRightSlider.min = "0";
      marginRightSlider.max = "35";
      marginRightSlider.step = "1";
      marginRightSlider.value = String(renderState.margin_right);
    }
  }
  if (marginLeftLetter) marginLeftLetter.textContent = outerGapUi ? t("sidebar.margin_out", "OUT") : "L";
  if (marginRightLetter) marginRightLetter.textContent = outerGapUi ? t("sidebar.margin_gap", "GAP") : "R";
  if (marginLeftVal) marginLeftVal.textContent = outerGapUi ? `${outerPct}%` : `${renderState.margin_left}%`;
  if (marginRightVal) {
    marginRightVal.textContent = outerGapUi ? formatGutterRem(gutterRem) : `${renderState.margin_right}%`;
    marginRightVal.style.opacity = "1";
  }
  const marginsLabel = document.getElementById("marginsLabel");
  if (marginsLabel) {
    marginsLabel.setAttribute("data-i18n", outerGapUi ? "sidebar.margins_out" : "sidebar.margins");
    marginsLabel.textContent = outerGapUi
      ? t("sidebar.margins_out", "Outer / Gap")
      : t("sidebar.margins", "L/R Margins");
  }

  const centerGutterRow = document.getElementById("centerGutterRow");
  if (centerGutterRow) centerGutterRow.classList.add("hidden");

  const marginsLinkBtn = document.getElementById("marginsLinkBtn");
  if (marginsLinkBtn) {
    if (outerGapUi) {
      const autoOn = autoBalanceMatches();
      marginsLinkBtn.textContent = t("sidebar.measure_auto", "AUTO");
      marginsLinkBtn.classList.toggle("active", autoOn);
      marginsLinkBtn.disabled = false;
      marginsLinkBtn.style.opacity = "1";
      marginsLinkBtn.style.cursor = "pointer";
      marginsLinkBtn.setAttribute("data-i18n-title", "sidebar.measure_lock");
      marginsLinkBtn.title = t("sidebar.measure_lock", "Balance outer margin and center gap for this screen");
    } else {
      marginsLinkBtn.textContent = "L=R";
      marginsLinkBtn.classList.toggle("active", !!renderState.margins_linked);
      marginsLinkBtn.disabled = false;
      marginsLinkBtn.style.opacity = "1";
      marginsLinkBtn.style.cursor = "pointer";
      marginsLinkBtn.setAttribute("data-i18n-title", "sidebar.margins_link");
      marginsLinkBtn.title = t("sidebar.margins_link", "Keep left and right margins equal");
    }
  }

  const horizontalToggle = document.getElementById("horizontalToggle");
  if (horizontalToggle) {
    horizontalToggle.classList.toggle("on", horiz);
    horizontalToggle.setAttribute("aria-checked", horiz ? "true" : "false");
  }

  document.querySelectorAll("#textAlignGroup .typo-align-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.align === renderState.text_align);
  });

  const indentToggle = document.getElementById("textIndentToggle");
  if (indentToggle) {
    indentToggle.classList.toggle("on", !!renderState.text_indent);
    indentToggle.setAttribute("aria-checked", renderState.text_indent ? "true" : "false");
  }

  const indentModeBtn = document.getElementById("indentModeBtn");
  if (indentModeBtn) {
    indentModeBtn.classList.toggle("active", renderState.indent_mode === "follow");
    indentModeBtn.disabled = !renderState.text_indent;
    indentModeBtn.style.opacity = renderState.text_indent ? "1" : "0.4";
  }

  const hyphenationToggle = document.getElementById("hyphenationToggle");
  if (hyphenationToggle) {
    hyphenationToggle.classList.toggle("on", renderState.hyphenation !== false);
    hyphenationToggle.setAttribute("aria-checked", renderState.hyphenation !== false ? "true" : "false");
  }

  const twoPageToggle = document.getElementById("twoPageToggle");
  if (twoPageToggle) {
    twoPageToggle.classList.toggle("on", twoPage);
    twoPageToggle.setAttribute("aria-checked", twoPage ? "true" : "false");
  }

  const fontSelect = document.getElementById("fontFamilySelect");
  if (fontSelect && renderState.font_family) fontSelect.value = renderState.font_family;

  ["h1", "h2", "h3"].forEach((level) => {
    const align = renderState[`${level}_align`];
    document.querySelectorAll(`[data-heading-popup="${level}"] button`).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.align === align);
    });
  });

  syncSidebarAutoCollapseBtn();
}

function syncSidebarAutoCollapseBtn() {
  const btn = document.getElementById("sidebarAutoCollapseBtn");
  if (!btn) return;
  const mode = renderState.sidebar_auto_collapse === "show" ? "show" : "auto";
  btn.textContent = mode;
  btn.dataset.mode = mode;
  btn.setAttribute("aria-pressed", mode === "auto" ? "true" : "false");
  btn.title = mode === "auto"
    ? "Sidebar after open: auto"
    : "Sidebar after open: show";
}

async function persistRender(immediate = false) {
  const body = { ...renderState };
  const run = async () => {
    try {
      await fetchJSON("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      console.error("Failed to save render settings", e);
    }
  };
  if (immediate) {
    if (saveTimer) clearTimeout(saveTimer);
    await run();
    return;
  }
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(run, 250);
}

function closeHeadingPopups(except) {
  document.querySelectorAll(".typo-heading-popup").forEach((popup) => {
    if (popup !== except) popup.classList.remove("open");
  });
  document.querySelectorAll(".typo-heading-trigger").forEach((btn) => {
    if (!except || btn.dataset.heading !== except.dataset.headingPopup) {
      btn.classList.remove("open");
    }
  });
}

function isTypoMenuOpen() {
  return document.getElementById("typoFloatMenu")?.classList.contains("open");
}

function positionTypoMenu() {
  const topbarTrigger = document.getElementById("settingsMenuTrigger");
  const isFromTopbar = topbarTrigger?.classList.contains("open");
  const btn = isFromTopbar ? topbarTrigger : document.getElementById("typoMenuBtn");
  const menu = document.getElementById("typoFloatMenu");
  if (!btn || !menu || !menu.classList.contains("open")) return;
  const rect = btn.getBoundingClientRect();
  const pad = 8;
  const menuW = menu.offsetWidth || 280;
  const menuH = menu.offsetHeight || 420;

  let left, top;
  if (isFromTopbar) {
    left = Math.max(pad, Math.min(rect.left, window.innerWidth - menuW - pad));
    top = Math.max(pad, rect.bottom + 4);
    if (top + menuH > window.innerHeight - pad) {
      top = Math.max(pad, window.innerHeight - menuH - pad);
    }
  } else {
    left = rect.right + 8;
    if (left + menuW > window.innerWidth - pad) {
      left = Math.max(pad, rect.left - menuW - 8);
    }
    top = rect.top;
    if (top + menuH > window.innerHeight - pad) {
      top = Math.max(pad, window.innerHeight - menuH - pad);
    }
  }

  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

export function closeTypoMenu() {
  const menu = document.getElementById("typoFloatMenu");
  const btn = document.getElementById("typoMenuBtn");
  const topbarTrigger = document.getElementById("settingsMenuTrigger");
  if (menu) {
    menu.classList.remove("open");
    menu.setAttribute("aria-hidden", "true");
  }
  if (btn) btn.classList.remove("active");
  if (topbarTrigger) topbarTrigger.classList.remove("open");
  closeHeadingPopups();
}

export function openTypoMenu() {
  const menu = document.getElementById("typoFloatMenu");
  const btn = document.getElementById("typoMenuBtn");
  const topbarTrigger = document.getElementById("settingsMenuTrigger");
  if (!menu) return;
  menu.classList.add("open");
  menu.setAttribute("aria-hidden", "false");
  if (topbarTrigger?.classList.contains("open")) {
    if (btn) btn.classList.remove("active");
  } else if (btn) {
    btn.classList.add("active");
  }
  positionTypoMenu();
  requestAnimationFrame(positionTypoMenu);
  renderIcons();
}

function toggleTypoMenu() {
  if (isTypoMenuOpen()) closeTypoMenu();
  else openTypoMenu();
}

function wireFloatMenu() {
  const btn = document.getElementById("typoMenuBtn");
  const menu = document.getElementById("typoFloatMenu");
  if (btn) {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleTypoMenu();
    };
    btn.oncontextmenu = (e) => {
      e.preventDefault();
      e.stopPropagation();
      openTypoMenu();
    };
  }
  if (menu) {
    menu.addEventListener("click", (e) => e.stopPropagation());
    menu.addEventListener("contextmenu", (e) => e.stopPropagation());
  }
  window.addEventListener("resize", () => {
    if (isTypoMenuOpen()) positionTypoMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeTypoMenu();
  });
}

function wireControls() {
  const fontSelect = document.getElementById("fontFamilySelect");
  if (fontSelect) {
    fontSelect.onchange = (e) => {
      renderState.font_family = fontSelect.value;
      applyReaderTypography();
      persistRender(true);
      syncWeightSlider(e.target.value);
    };
  }

  const autoCollapseBtn = document.getElementById("sidebarAutoCollapseBtn");
  if (autoCollapseBtn) {
    autoCollapseBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      renderState.sidebar_auto_collapse =
        renderState.sidebar_auto_collapse === "show" ? "auto" : "show";
      syncSidebarAutoCollapseBtn();
      persistRender(true);
    };
  }

  const fontSizeSlider = document.getElementById("fontSizeSlider");
  if (fontSizeSlider) {
    fontSizeSlider.oninput = (e) => {
      renderState.font_size = parseInt(e.target.value, 10);
      applyReaderTypography();
      persistRender();
    };
    fontSizeSlider.onchange = () => persistRender(true);
  }

  const weightSlider = document.getElementById("fontWeightSlider");
  if (weightSlider) {
    weightSlider.oninput = (e) => {
      renderState.font_weight = parseInt(e.target.value, 10);
      applyReaderTypography();
      persistRender();
    };
    weightSlider.onchange = () => persistRender(true);
  }

  const lineSlider = document.getElementById("lineHeightSlider");
  if (lineSlider) {
    lineSlider.oninput = (e) => {
      renderState.line_height = parseInt(e.target.value, 10) / 100;
      applyReaderTypography();
      persistRender();
    };
    lineSlider.onchange = () => persistRender(true);
  }

  const paraSlider = document.getElementById("paragraphSpacingSlider");
  if (paraSlider) {
    paraSlider.oninput = (e) => {
      renderState.paragraph_spacing = parseFloat(e.target.value);
      applyReaderTypography();
      persistRender();
    };
    paraSlider.onchange = () => persistRender(true);
  }

  const setMargin = (side, raw) => {
    if (usesOuterGapUi()) {
      if (side === "left") renderState.landscape_outer_margin = clampOuterMargin(raw);
      else renderState.center_gutter = clampCenterGutter(raw);
      applyReaderTypography();
      persistRender();
      return;
    }
    const value = clampMargin(raw);
    if (side === "left") renderState.margin_left = value;
    else renderState.margin_right = value;
    if (renderState.margins_linked) {
      renderState.margin_left = value;
      renderState.margin_right = value;
    }
    applyReaderTypography();
    persistRender();
  };

  const marginLeftSlider = document.getElementById("marginLeftSlider");
  if (marginLeftSlider) {
    marginLeftSlider.oninput = (e) => setMargin("left", e.target.value);
    marginLeftSlider.onchange = () => {
      setMargin("left", marginLeftSlider.value);
      persistRender(true);
    };
  }

  const marginRightSlider = document.getElementById("marginRightSlider");
  if (marginRightSlider) {
    marginRightSlider.oninput = (e) => setMargin("right", e.target.value);
    marginRightSlider.onchange = () => {
      setMargin("right", marginRightSlider.value);
      persistRender(true);
    };
  }

  const centerGutterSlider = document.getElementById("centerGutterSlider");
  if (centerGutterSlider) {
    centerGutterSlider.oninput = null;
  }

  const marginsLinkBtn = document.getElementById("marginsLinkBtn");
  if (marginsLinkBtn) {
    marginsLinkBtn.onclick = (e) => {
      e.stopPropagation();
      if (usesOuterGapUi()) {
        const preset = computeAutoBalance();
        renderState.landscape_outer_margin = preset.outer;
        renderState.center_gutter = preset.gutter;
      } else {
        renderState.margins_linked = !renderState.margins_linked;
        if (renderState.margins_linked) {
          renderState.margin_right = renderState.margin_left;
        }
      }
      applyReaderTypography();
      persistRender(true);
    };
  }

  document.querySelectorAll("#textAlignGroup .typo-align-btn").forEach((btn) => {
    btn.onclick = () => {
      renderState.text_align = btn.dataset.align;
      applyReaderTypography();
      persistRender(true);
    };
  });

  const indentToggle = document.getElementById("textIndentToggle");
  if (indentToggle) {
    indentToggle.onclick = (e) => {
      e.stopPropagation();
      renderState.text_indent = !renderState.text_indent;
      applyReaderTypography();
      persistRender(true);
    };
  }

  const indentModeBtn = document.getElementById("indentModeBtn");
  if (indentModeBtn) {
    indentModeBtn.onclick = (e) => {
      e.stopPropagation();
      if (!renderState.text_indent) return;
      renderState.indent_mode = renderState.indent_mode === "follow" ? "all" : "follow";
      applyReaderTypography();
      persistRender(true);
    };
  }

  const hyphenationToggle = document.getElementById("hyphenationToggle");
  if (hyphenationToggle) {
    hyphenationToggle.onclick = (e) => {
      e.stopPropagation();
      renderState.hyphenation = renderState.hyphenation === false;
      applyReaderTypography();
      persistRender(true);
    };
  }

  const horizontalToggle = document.getElementById("horizontalToggle");
  if (horizontalToggle) {
    horizontalToggle.onclick = (e) => {
      e.stopPropagation();
      renderState.horizontal_mode = !renderState.horizontal_mode;
      applyReaderTypography();
      persistRender(true);
    };
  }

  const twoPage = document.getElementById("twoPageToggle");
  if (twoPage) {
    twoPage.onclick = (e) => {
      e.stopPropagation();
      renderState.two_page_landscape = !renderState.two_page_landscape;
      applyReaderTypography();
      persistRender(true);
    };
  }

  document.querySelectorAll(".typo-heading-trigger").forEach((trigger) => {
    trigger.onclick = (e) => {
      e.stopPropagation();
      const popup = document.querySelector(`[data-heading-popup="${trigger.dataset.heading}"]`);
      const willOpen = popup && !popup.classList.contains("open");
      closeHeadingPopups();
      if (willOpen && popup) {
        const rect = trigger.getBoundingClientRect();
        popup.style.left = `${Math.round(rect.left + rect.width / 2)}px`;
        popup.style.top = `${Math.round(rect.bottom + 4)}px`;
        popup.style.transform = "translateX(-50%)";
        popup.classList.add("open");
        trigger.classList.add("open");
      }
    };
  });

  document.querySelectorAll(".typo-heading-popup button").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const popup = btn.closest("[data-heading-popup]");
      const level = popup?.dataset.headingPopup;
      if (!level) return;
      renderState[`${level}_align`] = btn.dataset.align;
      applyReaderTypography();
      persistRender(true);
      closeHeadingPopups();
    };
  });

  document.addEventListener("click", (e) => {
    closeHeadingPopups();
    const menu = document.getElementById("typoFloatMenu");
    const btn = document.getElementById("typoMenuBtn");
    if (menu?.contains(e.target) || btn?.contains(e.target) || e.target.closest("#settingsMenuTrigger")) return;
    closeTypoMenu();
  });
}

function mergeRender(data) {
  const next = { ...DEFAULT_RENDER, ...(data || {}) };
  next.font_size = parseInt(next.font_size, 10) || DEFAULT_RENDER.font_size;
  next.font_weight = parseInt(next.font_weight, 10) || DEFAULT_RENDER.font_weight;
  next.line_height = Number(next.line_height) || DEFAULT_RENDER.line_height;
  next.paragraph_spacing = Number(next.paragraph_spacing);
  if (!Number.isFinite(next.paragraph_spacing)) next.paragraph_spacing = DEFAULT_RENDER.paragraph_spacing;
  next.text_indent = next.text_indent !== false;
  next.hyphenation = next.hyphenation !== false;
  next.indent_mode = next.indent_mode === "all" ? "all" : "follow";
  next.two_page_landscape = next.two_page_landscape === true;
  next.horizontal_mode = next.horizontal_mode === true;
  next.margin_left = clampMargin(next.margin_left);
  next.margin_right = clampMargin(next.margin_right);
  next.center_gutter = clampCenterGutter(next.center_gutter);
  next.landscape_outer_margin = clampOuterMargin(next.landscape_outer_margin);
  next.measure_lock = next.measure_lock !== false;
  next.margins_linked = next.margins_linked !== false;
  if (next.margins_linked) next.margin_right = next.margin_left;
  next.sidebar_auto_collapse = next.sidebar_auto_collapse === "show" ? "show" : "auto";
  const aligns = new Set(["left", "center", "right", "justify"]);
  if (!aligns.has(next.text_align)) next.text_align = "justify";
  ["h1_align", "h2_align", "h3_align"].forEach((key) => {
    if (!["left", "center", "right"].includes(next[key])) {
      next[key] = DEFAULT_RENDER[key];
    }
  });
  return next;
}

export async function initTypography(fallbackFontSize) {
  let loaded = null;
  try {
    loaded = await fetchJSON("/api/render");
  } catch (e) {
    console.error("Render settings load error", e);
  }

  renderState = mergeRender(loaded);
  if ((!loaded || loaded.font_size == null) && fallbackFontSize) {
    renderState.font_size = parseInt(fallbackFontSize, 10) || renderState.font_size;
  }

  fillFontSelect(renderState.font_family);
  wireControls();
  wireFloatMenu();
  applyReaderTypography();
  const activeFont =
    document.getElementById("fontFamilySelect")?.value || renderState.font_family;
  await syncWeightSlider(activeFont);
  renderIcons();
  persistRender(true);
}

