import { state } from "./state.js";
import { syncBackToReadingButton } from "./ui.js";

const HORIZONTAL_MIN_W = 360;
const HORIZONTAL_MIN_H = 220;
const PLAYER_GAP = 12;
const COL_MIN_PX = 240;
const GUTTER_MIN_REM = 3;
const GUTTER_MAX_REM = 12;
const GUTTER_DEFAULT_REM = 3.5;
const OUTER_MIN_PCT = 0;
const OUTER_MAX_PCT = 15;
const OUTER_DEFAULT_PCT = 4;

let pageTurnHandler = null;
let pendingSpread = null;
let layoutGen = 0;
let resizeTimer = null;
let listenersBound = false;
let layoutLock = false;
let pendingLayout = null;

export function setHorizontalPageTurn(fn) {
  pageTurnHandler = typeof fn === "function" ? fn : null;
}

export function requestLandingSpread(which) {
  pendingSpread = which === "last" ? "last" : "first";
}

function paneEl() {
  return document.getElementById("readerContent");
}

function textEl() {
  return document.getElementById("textContent");
}

function paneBox() {
  const pane = paneEl();
  if (!pane) return { width: 0, height: 0 };
  return { width: pane.clientWidth, height: pane.clientHeight };
}

function paneTotalWidth() {
  const pane = paneEl();
  return pane ? Math.max(1, pane.clientWidth) : 1;
}

function clampOuterPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return OUTER_DEFAULT_PCT;
  return Math.max(OUTER_MIN_PCT, Math.min(OUTER_MAX_PCT, Math.round(n)));
}

function userOuterMarginPct() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--reader-landscape-outer").trim();
  return clampOuterPct(parseFloat(raw));
}

function paneContentWidth() {
  const totalW = paneTotalWidth();
  const pct = userOuterMarginPct();
  const outerPx = Math.round((totalW * pct) / 100);
  return Math.max(1, totalW - 2 * outerPx);
}

export function twoPageFits() {
  const pane = paneEl();
  if (!pane) return false;
  const minSpan = 2 * COL_MIN_PX + remPx(GUTTER_MIN_REM);
  const effectiveWidth = paneContentWidth();
  return effectiveWidth >= minSpan;
}

export function isHorizontalMode() {
  const text = textEl();
  if (!text || !text.classList.contains("horizontal") || text.classList.contains("hidden")) {
    return false;
  }
  const box = paneBox();
  return box.width >= HORIZONTAL_MIN_W && box.height >= HORIZONTAL_MIN_H;
}

function wantsNewspaperTwoPage() {
  const text = textEl();
  return !!(
    text &&
    text.classList.contains("two-page") &&
    !text.classList.contains("hidden") &&
    twoPageFits() &&
    !isHorizontalMode()
  );
}

function wantsSpreadTwoPage() {
  const text = textEl();
  return !!(text && text.classList.contains("two-page") && twoPageFits() && isHorizontalMode());
}

function syncLayoutClasses() {
  const horizontal = isHorizontalMode();
  document.body.classList.toggle("horizontal-mode", horizontal);
  document.body.classList.toggle("reader-two-page", wantsNewspaperTwoPage());
  document.body.classList.toggle("spread-two", wantsSpreadTwoPage());
}

function getScroller() {
  return textEl() || paneEl();
}

function maxScrollLeft(scroller) {
  return Math.max(0, scroller.scrollWidth - scroller.clientWidth);
}

function remPx(n) {
  const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  return n * root;
}

function clampGutterRem(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return GUTTER_DEFAULT_REM;
  return Math.max(GUTTER_MIN_REM, Math.min(GUTTER_MAX_REM, Math.round(n * 4) / 4));
}

function userCenterGutterRem() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--reader-center-gutter").trim();
  return clampGutterRem(parseFloat(raw));
}


let renderSource = null;

export function bindHorizontalRenderState(getter) {
  renderSource = typeof getter === "function" ? getter : null;
}

function getRenderConfig() {
  if (renderSource) {
    try {
      const cfg = renderSource();
      if (cfg) return cfg;
    } catch {}
  }
  return null;
}

function isSidebarCollapsed() {
  const sidebar = document.querySelector(".sidebar");
  return !sidebar || sidebar.classList.contains("collapsed");
}

function userSingleColumnMargins() {
  const cfg = getRenderConfig();
  const collapsed = isSidebarCollapsed();
  let leftPct = cfg
    ? Number(collapsed ? cfg.margin_left : (cfg.margin_left_open ?? 5))
    : NaN;
  let rightPct = cfg
    ? Number(collapsed ? cfg.margin_right : (cfg.margin_right_open ?? 5))
    : NaN;
  const defaultVal = collapsed ? 8 : 5;
  if (!Number.isFinite(leftPct)) leftPct = defaultVal;
  if (!Number.isFinite(rightPct)) rightPct = defaultVal;
  leftPct = Math.max(0, Math.min(35, Math.round(leftPct)));
  rightPct = Math.max(0, Math.min(35, Math.round(rightPct)));
  return { leftPct, rightPct };
}

function computeSpreadMetrics() {
  const twoPageWanted = !!(textEl()?.classList.contains("two-page") && isHorizontalMode());
  const totalW = paneTotalWidth();

  if (!twoPageWanted) {
    const { leftPct, rightPct } = userSingleColumnMargins();
    if (leftPct === 0 && rightPct === 0) {
      const colW = Math.max(1, totalW);
      return {
        twoPage: false,
        colW,
        gap: 0,
        spreadW: colW,
        stride: colW,
        leftPx: 0,
        rightPx: 0,
      };
    }
    let leftPx = Math.round((totalW * leftPct) / 100);
    let rightPx = Math.round((totalW * rightPct) / 100);

    const minCol = Math.min(totalW, COL_MIN_PX);
    const maxMargin = Math.max(0, totalW - minCol);
    if (leftPx + rightPx > maxMargin && (leftPx + rightPx) > 0) {
      const scale = maxMargin / (leftPx + rightPx);
      leftPx = Math.floor(leftPx * scale);
      rightPx = Math.floor(rightPx * scale);
    }
    const colW = Math.max(1, totalW - leftPx - rightPx);
    return {
      twoPage: false,
      colW,
      gap: 0,
      spreadW: colW,
      stride: colW,
      leftPx,
      rightPx,
    };
  }

  const effectiveWidth = paneContentWidth();
  const minGutterPx = Math.round(remPx(GUTTER_MIN_REM));
  const rawGutterPx = Math.round(remPx(userCenterGutterRem()));
  const canFit = effectiveWidth >= 2 * COL_MIN_PX + minGutterPx;

  if (!canFit) {
    const colW = Math.max(1, effectiveWidth);
    const gap = 0;
    const outerDiff = Math.max(0, totalW - effectiveWidth);
    const leftPx = Math.floor(outerDiff / 2);
    const rightPx = Math.ceil(outerDiff / 2);
    return {
      twoPage: false,
      colW,
      gap,
      spreadW: colW,
      stride: colW,
      leftPx,
      rightPx,
    };
  }

  const maxGap = Math.max(minGutterPx, effectiveWidth - 2 * COL_MIN_PX);
  const gap = Math.max(minGutterPx, Math.min(rawGutterPx, maxGap));
  const colW = Math.max(COL_MIN_PX, Math.floor((effectiveWidth - gap) / 2));
  const spreadW = 2 * colW + gap;
  return {
    twoPage: true,
    colW,
    gap,
    spreadW,
    stride: spreadW + gap,
  };
}

function spreadStride() {
  return Math.max(1, computeSpreadMetrics().stride);
}

export function getSpreadIndex() {
  const scroller = getScroller();
  if (!scroller) return 0;
  const stride = spreadStride();
  return stride > 0 ? Math.round(scroller.scrollLeft / stride) : 0;
}

function playerPaintTop() {
  const el =
    document.querySelector(".player-bar:not(.hidden):not(.minimized)") ||
    document.getElementById("controls");
  if (!el) return Infinity;
  const cs = getComputedStyle(el);
  if (cs.display === "none" || cs.visibility === "hidden") return Infinity;
  if ((parseFloat(cs.opacity) || 0) < 0.05) return Infinity;
  const rect = el.getBoundingClientRect();
  if (rect.height < 8 || rect.width < 8) return Infinity;
  if (rect.bottom <= 0 || rect.top >= window.innerHeight) return Infinity;
  return rect.top;
}

function snapScroll(scroller, preferredLeft) {
  if (!scroller) return;
  const stride = spreadStride();
  const maxLeft = maxScrollLeft(scroller);
  const raw = preferredLeft == null ? scroller.scrollLeft : preferredLeft;
  const page = Math.round(raw / stride);
  scroller.scrollLeft = Math.max(0, Math.min(maxLeft, page * stride));
}

function setPageBox() {
  const text = textEl();
  const pane = paneEl();
  if (!text || !pane) return;

  const csPane = getComputedStyle(pane);
  const csText = getComputedStyle(text);
  const padTop = parseFloat(csPane.paddingTop) || 0;
  const padBottom = parseFloat(csPane.paddingBottom) || 0;
  const paneRect = pane.getBoundingClientRect();
  const playerTop = playerPaintTop();
  const rawBottom = Math.min(paneRect.bottom - padBottom, playerTop - PLAYER_GAP);
  const rawH = Math.max(80, rawBottom - paneRect.top - padTop);

  // Snap to integer lines
  const fontSize = parseFloat(csText.fontSize) || 18;
  const lineHeightVal = parseFloat(csText.lineHeight) || (fontSize * 1.8);
  const totalLines = Math.floor(rawH / lineHeightVal);
  const snappedH = Math.max(lineHeightVal * 2, totalLines * lineHeightVal);

  text.style.height = `${Math.floor(snappedH)}px`;
}

function setColumnMetrics() {
  const text = textEl();
  const pane = paneEl();
  if (!text || !pane) return;
  pane.style.paddingLeft = "";
  pane.style.paddingRight = "";
  const m = computeSpreadMetrics();

  text.style.setProperty("--spread-column-width", `${m.colW}px`);
  text.style.setProperty("--spread-column-gap", `${m.gap}px`);
  text.style.setProperty("width", `${m.spreadW}px`, "important");
  text.style.maxWidth = "none";

  if (!m.twoPage) {
    text.style.paddingRight = "";
    if (m.leftPx != null && m.rightPx != null) {
      if (m.leftPx === m.rightPx) {
        text.style.setProperty("margin", "0 auto", "important");
        text.style.marginLeft = "auto";
        text.style.marginRight = "auto";
        text.style.alignSelf = "center";
      } else {
        text.style.marginTop = "0";
        text.style.marginBottom = "0";
        text.style.setProperty("margin-left", `${m.leftPx}px`, "important");
        text.style.setProperty("margin-right", `${m.rightPx}px`, "important");
        text.style.alignSelf = "flex-start";
      }
    } else {
      text.style.setProperty("margin", "0 auto", "important");
      text.style.marginLeft = "auto";
      text.style.marginRight = "auto";
      text.style.alignSelf = "center";
    }
  } else {
    text.style.setProperty("margin", "0 auto", "important");
    text.style.marginLeft = "auto";
    text.style.marginRight = "auto";
    text.style.alignSelf = "center";

    const colStride = m.colW + m.gap;
    if (colStride > 0) {
      const totalCols = Math.round((text.scrollWidth + m.gap) / colStride);
      if (totalCols > 0 && totalCols % 2 !== 0) {
        text.style.paddingRight = `${colStride}px`;
      } else {
        text.style.paddingRight = "";
      }
    }
  }
  void text.offsetWidth;
}

function clearPaneHeight() {
  const text = textEl();
  const pane = paneEl();
  if (pane) {
    pane.style.paddingLeft = "";
    pane.style.paddingRight = "";
  }
  if (!text) return;
  text.style.height = "";
  text.style.paddingRight = "";
  text.style.removeProperty("width");
  text.style.width = "";
  text.style.maxWidth = "";
  text.style.removeProperty("margin");
  text.style.removeProperty("margin-left");
  text.style.removeProperty("margin-right");
  text.style.margin = "";
  text.style.marginTop = "";
  text.style.marginBottom = "";
  text.style.marginLeft = "";
  text.style.marginRight = "";
  text.style.alignSelf = "";
  text.style.removeProperty("--spread-column-width");
  text.style.removeProperty("--spread-column-gap");
}

function afterLayout(cb) {
  requestAnimationFrame(cb);
}

let isProgrammaticScroll = false;

export function layoutSpreads({ reset = false, spreadIndex = null } = {}) {
  bindListeners();
  if (layoutLock) {
    pendingLayout = {
      reset: reset,
      spreadIndex: spreadIndex != null ? spreadIndex : (pendingLayout ? pendingLayout.spreadIndex : null),
    };
    return Promise.resolve();
  }
  layoutLock = true;
  const gen = ++layoutGen;
  const wantReset = reset;
  syncLayoutClasses();
  const text = textEl();
  const pane = paneEl();
  const active = isHorizontalMode();

  if (!active) {
    pendingSpread = null;
    clearPaneHeight();
    if (text) text.scrollLeft = 0;
    if (pane) pane.scrollLeft = 0;
    layoutLock = false;
    if (pendingLayout) {
      const nextArgs = pendingLayout;
      pendingLayout = null;
      layoutSpreads(nextArgs);
    }
    return Promise.resolve();
  }

  const scroller = getScroller();
  const oldStride = spreadStride();
  const oldPage =
    spreadIndex != null
      ? spreadIndex
      : scroller && oldStride > 0
        ? Math.round(scroller.scrollLeft / oldStride)
        : 0;

  return new Promise((resolve) => {
    afterLayout(() => {
      try {
        if (gen !== layoutGen) return;
        setPageBox();
        setColumnMetrics();
        const next = getScroller();
        if (!next) return;
        const landing = pendingSpread;
        pendingSpread = null;
        const stride = spreadStride();

        let targetLeft = 0;
        if (landing === "last") {
          targetLeft = maxScrollLeft(next);
        } else if (landing === "first" || wantReset) {
          targetLeft = 0;
        } else {
          // If auto-scroll is enabled on the reading page, re-anchor to the active sentence after reflow
          let activeAnchorSpread = null;
          if (state.autoScrollEnabled && state.viewPageIndex === state.readingPageIndex) {
            activeAnchorSpread = getActiveSentenceSpreadIndex();
          }
          if (activeAnchorSpread != null) {
            targetLeft = activeAnchorSpread * stride;
          } else {
            targetLeft = oldPage * stride;
          }
        }
        isProgrammaticScroll = true;
        next.scrollLeft = Math.max(0, Math.min(maxScrollLeft(next), targetLeft));
        snapScroll(next);
        setTimeout(() => { isProgrammaticScroll = false; }, 150);
      } finally {
        layoutLock = false;
        if (pendingLayout) {
          const nextArgs = pendingLayout;
          pendingLayout = null;
          layoutSpreads(nextArgs);
        }
        resolve();
      }
    });
  });
}

export async function flipSpread(dir) {
  if (!isHorizontalMode()) return false;
  const scroller = getScroller();
  if (!scroller) return false;

  snapScroll(scroller);
  const stride = spreadStride();
  const left = scroller.scrollLeft;
  const maxLeft = maxScrollLeft(scroller);
  const atFirst = left <= 8;
  const atLast = left >= maxLeft - 8;
  const step = dir < 0 ? -1 : 1;

  if (step < 0 && atFirst) {
    if (!pageTurnHandler) return true;
    pendingSpread = "last";
    const moved = await pageTurnHandler(-1);
    if (!moved) pendingSpread = null;
    return true;
  }
  if (step > 0 && atLast) {
    if (!pageTurnHandler) return true;
    pendingSpread = "first";
    const moved = await pageTurnHandler(1);
    if (!moved) pendingSpread = null;
    return true;
  }

  snapScroll(scroller, left + step * stride);
  updateHorizontalSpreadFocus();
  return true;
}

export function getElementSpreadIndex(el) {
  if (!el || !el.isConnected || !isHorizontalMode()) return null;
  const scroller = getScroller();
  if (!scroller) return null;
  const er = el.getBoundingClientRect();
  const ar = scroller.getBoundingClientRect();
  const left = er.left - ar.left + scroller.scrollLeft;
  const m = computeSpreadMetrics();
  const colStride = Math.max(1, m.colW + m.gap);
  // Add a subpixel epsilon (+2px) to prevent boundary truncation
  const colIndex = Math.max(0, Math.floor((left + 2) / colStride));
  return m.twoPage ? Math.floor(colIndex / 2) : colIndex;
}

export function getActiveSentenceSpreadIndex() {
  const el = document.querySelector("#textContent .active-sentence")
    || (state.sentenceElements && state.sentenceElements[state.currentSentenceIndex]);
  return getElementSpreadIndex(el);
}

export function updateHorizontalSpreadFocus() {
  if (!isHorizontalMode()) return;
  if (state.viewPageIndex !== state.readingPageIndex) {
    state.autoScrollEnabled = false;
  } else {
    const currentSpread = getSpreadIndex();
    const activeSpread = getActiveSentenceSpreadIndex();
    if (activeSpread != null) {
      state.autoScrollEnabled = (currentSpread === activeSpread);
    }
  }
  syncBackToReadingButton();
}

export function revealInSpread(el) {
  if (!el || !isHorizontalMode()) return false;
  afterLayout(() => {
    if (!el.isConnected || !isHorizontalMode()) return;
    const scroller = getScroller();
    if (!scroller) return;
    const er = el.getBoundingClientRect();
    const ar = scroller.getBoundingClientRect();
    const left = er.left - ar.left + scroller.scrollLeft;
    const m = computeSpreadMetrics();
    const colStride = Math.max(1, m.colW + m.gap);
    const colIndex = Math.max(0, Math.floor((left + 2) / colStride));
    const spreadIndex = m.twoPage ? Math.floor(colIndex / 2) : colIndex;
    const targetLeft = Math.min(spreadIndex * m.stride, maxScrollLeft(scroller));
    if (Math.abs(scroller.scrollLeft - targetLeft) > 1) {
      isProgrammaticScroll = true;
      scroller.scrollLeft = targetLeft;
      setTimeout(() => { isProgrammaticScroll = false; }, 150);
    }
    updateHorizontalSpreadFocus();
  });
  return true;
}

function onViewportChange() {
  const sidebar = document.querySelector(".sidebar");
  if (sidebar && sidebar.classList.contains("animating")) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    layoutSpreads();
  }, 80);
}

let spreadScrollTimer = null;
function onScrollerScroll() {
  if (!isHorizontalMode()) return;
  if (window.isJumpingCamera || isProgrammaticScroll) return;
  clearTimeout(spreadScrollTimer);
  spreadScrollTimer = setTimeout(() => {
    updateHorizontalSpreadFocus();
  }, 100);
}

function bindListeners() {
  if (listenersBound) return;
  listenersBound = true;
  window.addEventListener("resize", onViewportChange);
  const scroller = getScroller();
  if (scroller) {
    scroller.addEventListener("scroll", onScrollerScroll, { passive: true });
  }
  const pane = paneEl();
  if (pane && pane !== scroller) {
    pane.addEventListener("scroll", onScrollerScroll, { passive: true });
  }
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => onViewportChange());
    if (pane) ro.observe(pane);
    const area = document.querySelector(".content-area");
    const sidebar = document.querySelector(".sidebar");
    if (area && area !== pane) ro.observe(area);
    if (sidebar) ro.observe(sidebar);
  }
}