import { renderIcons } from "./ui.js";

export const THEME_LIST = [
  { id: "dark", name: "Dark Default", bg: "#09090b", text: "#ffffff", icon: "D" },
  { id: "dark-soft", name: "Dark Soft", bg: "#121212", text: "#8b8b8b", icon: "S" },
  { id: "soft-white", name: "Soft White", bg: "#f9fafb", text: "#4b5563", icon: "W" },
  { id: "sepia-contrast", name: "Sepia Contrast", bg: "#fdf6e3", text: "#2c2116", icon: "C" },
  { id: "sepia-soft", name: "Sepia Soft", bg: "#f4e8d1", text: "#6b543a", icon: "S" },
  { id: "twilight", name: "Twilight (Gray)", bg: "#292d3e", text: "#a6accd", icon: "T" }
];

let currentThemeId = localStorage.getItem("lr_theme") || "dark";

export function getCurrentThemeId() {
  return currentThemeId;
}

export async function setTheme(themeId, saveToBackend = true) {
  currentThemeId = themeId;
  document.documentElement.dataset.theme = themeId;
  localStorage.setItem("lr_theme", themeId);
  renderIcons();
  document.dispatchEvent(new CustomEvent("lr-theme-change", { detail: themeId }));

  if (saveToBackend) {
    try {
      await fetch("/api/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme_id: themeId })
      });
    } catch (e) {}
  }
}

export async function initThemeSystem() {
  const contentArea = document.querySelector(".content-area");
  if (contentArea) {
    contentArea.style.filter = "";
    contentArea.style.transition = "";
  }

  // Remove legacy injected safe-theme tag if present from previous runs
  const legacyStyle = document.getElementById("localreader-safe-themes");
  if (legacyStyle) legacyStyle.remove();

  let loadedThemeId = localStorage.getItem("lr_theme") || "dark";
  try {
    const res = await fetch("/api/theme");
    if (res.ok) {
      const data = await res.json();
      if (data.theme_id) loadedThemeId = data.theme_id;
    }
  } catch (e) {}

  await setTheme(loadedThemeId, false);
}