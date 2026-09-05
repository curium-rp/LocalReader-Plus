/**
 * In-book search query handling and result rendering.
 *
 * Explicit dependencies (imported, original names):
 *   state         from ./modules/state.js
 *   fetchJSON     from ./modules/api.js
 *   showToast     from ./modules/ui.js
 *   escapeRegex   from ./modules/ui.js
 *   renderPage    from ./modules/library.js
 *
 * Exported (original names):
 *   closeSearchMode
 *   handleSearchPopupKeys
 *   initSearch
 */
import { state } from "./modules/state.js";
import { fetchJSON } from "./modules/api.js";
import { showToast, escapeRegex } from "./modules/ui.js";
import { renderPage } from "./modules/library.js";

function closeSearchMode() {
  const modal = document.getElementById("searchModal");
  if (modal) modal.classList.add("hidden");
  state.currentSearchQuery = "";
  state.searchTargetSnippet = "";
  document.querySelectorAll(".search-highlight").forEach((el) => {
    const parent = el.parentNode;
    if (!parent) return;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.removeChild(el);
    parent.normalize();
  });
}

let searchMatchCase = false;
let searchWholeWord = false;

function isSearchModalOpen() {
  const modal = document.getElementById("searchModal");
  return modal && !modal.classList.contains("hidden");
}

let searchSelectedIndex = -1;

function getSearchResultItems() {
  return Array.from(document.querySelectorAll("#searchResultsList .search-result-item"));
}

function setSearchSelection(index) {
  const items = getSearchResultItems();
  items.forEach((el) => el.classList.remove("search-result-item-selected"));
  if (!items.length) {
    searchSelectedIndex = -1;
    return;
  }
  searchSelectedIndex = ((index % items.length) + items.length) % items.length;
  const selected = items[searchSelectedIndex];
  selected.classList.add("search-result-item-selected");
  selected.scrollIntoView({ block: "nearest" });
}

function escapeSearchHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlightSearchSnippet(snippet, regex) {
  regex.lastIndex = 0;
  const parts = [];
  let lastIndex = 0;
  let match;
  while ((match = regex.exec(snippet)) !== null) {
    if (!match[0]) {
      regex.lastIndex += 1;
      continue;
    }
    parts.push(escapeSearchHtml(snippet.slice(lastIndex, match.index)));
    parts.push(`<mark>${escapeSearchHtml(match[0])}</mark>`);
    lastIndex = match.index + match[0].length;
  }
  parts.push(escapeSearchHtml(snippet.slice(lastIndex)));
  return parts.join("");
}

// Jump to a search hit: scroll/highlight only. Never starts playback.
async function activateSearchResult(item) {
  if (!item) return;
  const query = item.dataset.searchQuery || "";
  const pageIndex = parseInt(item.dataset.pageIndex, 10);
  if (Number.isNaN(pageIndex)) return;

  state.currentSearchQuery = query;
  state.searchMatchCase = searchMatchCase;
  state.searchWholeWord = searchWholeWord;
  state.searchTargetSnippet = item.dataset.snippet || "";
  state.searchTargetPage = pageIndex;
  state.viewPageIndex = pageIndex;
  state.autoScrollEnabled = false;

  document.getElementById("searchModal").classList.add("hidden");
  await renderPage();

  let checkCount = 0;
  const scrollInterval = setInterval(() => {
    const finalHl = document.querySelector(".search-highlight");
    if (finalHl) {
      clearInterval(scrollInterval);
      finalHl.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (checkCount > 20) {
      clearInterval(scrollInterval);
    }
    checkCount++;
  }, 100);
}

function handleSearchPopupKeys(e) {
  if (!isSearchModalOpen()) return false;
  if (e.key === "Escape") {
    e.preventDefault();
    closeSearchMode();
    return true;
  }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Enter") return false;

  const items = getSearchResultItems();
  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (items.length) setSearchSelection(searchSelectedIndex < 0 ? 0 : searchSelectedIndex + 1);
    return true;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    if (items.length) setSearchSelection(searchSelectedIndex < 0 ? items.length - 1 : searchSelectedIndex - 1);
    return true;
  }
  e.preventDefault();
  if (!items.length) return true;
  if (searchSelectedIndex < 0) setSearchSelection(0);
  activateSearchResult(getSearchResultItems()[searchSelectedIndex]);
  return true;
}

let searchDebounce = null;
export function initSearch() {
document.getElementById("searchBtn").onclick = () => {
  if (!state.currentDoc) {
    showToast("No document loaded");
    return;
  }
  document.getElementById("searchModal").classList.remove("hidden");
  document.getElementById("searchInput").focus();
};

document.getElementById("closeSearchBtn").onclick = () => closeSearchMode();

document.getElementById("btnMatchCase").onclick = (e) => {
  searchMatchCase = !searchMatchCase;
  const btn = e.currentTarget;
  btn.classList.toggle("bg-blue-600/20", searchMatchCase);
  btn.classList.toggle("text-blue-400", searchMatchCase);
  btn.classList.toggle("border-blue-500/50", searchMatchCase);
  document.getElementById("searchInput").dispatchEvent(new Event("input"));
};

document.getElementById("btnWholeWord").onclick = (e) => {
  searchWholeWord = !searchWholeWord;
  const btn = e.currentTarget;
  btn.classList.toggle("bg-blue-600/20", searchWholeWord);
  btn.classList.toggle("text-blue-400", searchWholeWord);
  btn.classList.toggle("border-blue-500/50", searchWholeWord);
  document.getElementById("searchInput").dispatchEvent(new Event("input"));
};

document.getElementById("searchInput").oninput = (e) => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(async () => {
    const query = e.target.value.trim();
    const resultsList = document.getElementById("searchResultsList");
    searchSelectedIndex = -1;
    if (!query || query.length < 2) {
      resultsList.innerHTML = "";
      document.getElementById("searchEmpty").classList.add("hidden");
      return;
    }
    try {
      const data = await fetchJSON(
        `/api/library/search/${state.currentDoc.id}?q=${encodeURIComponent(query)}&match_case=${searchMatchCase}&whole_word=${searchWholeWord}`
      );
      resultsList.innerHTML = "";
      if (data.results.length === 0) {
        document.getElementById("searchEmpty").classList.remove("hidden");
        return;
      }
      document.getElementById("searchEmpty").classList.add("hidden");
      const fragment = document.createDocumentFragment();

      let snippetPattern = escapeRegex(query).replace(/\s+/g, "\\s+");
      if (searchWholeWord) snippetPattern = `\\b${snippetPattern}\\b`;
      const hlRegex = new RegExp(`(${snippetPattern})`, searchMatchCase ? "g" : "gi");

      data.results.forEach((result) => {
        result.matches.forEach((match) => {
          const div = document.createElement("div");
          div.className = "search-result-item";
          div.dataset.searchQuery = query;
          div.dataset.pageIndex = String(result.page_index);
          div.dataset.snippet = match.snippet;
          div.innerHTML = `<div class="flex justify-between mb-2"><span class="text-xs font-bold text-blue-400">Page ${result.page_index + 1}</span></div><div class="search-result-snippet">${highlightSearchSnippet(match.snippet, hlRegex)}</div>`;
          div.onclick = () => activateSearchResult(div);
          fragment.appendChild(div);
        });
      });
      resultsList.appendChild(fragment);
      setSearchSelection(0);
    } catch (e) {}
  }, 300);
};
}

export { closeSearchMode, handleSearchPopupKeys };
