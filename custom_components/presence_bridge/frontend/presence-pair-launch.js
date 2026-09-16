export const PRESENCE_PAIR_STORE_URL = "https://apps.apple.com/app/id6808059201";

export function isAppleMobile(nav = navigator) {
  return /iPhone|iPad|iPod/i.test(nav.userAgent || "")
    || (/Macintosh/i.test(nav.userAgent || "") && Number(nav.maxTouchPoints) > 1);
}

function validAppURI(uri) {
  try {
    const url = new URL(uri);
    return url.protocol === "presencepair:" && ["pair", "monitor"].includes(url.hostname)
      && !url.username && !url.password && !url.port && ["", "/"].includes(url.pathname);
  } catch {
    return false;
  }
}

function escape(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

export function presencePairLinks(uri, {
  className = "action",
  openLabel = "Open Presence Pair on this device",
  storeLabel = "Presence Pair on the App Store",
} = {}, nav = navigator) {
  const open = isAppleMobile(nav) && validAppURI(uri)
    ? `<a class="${escape(className)} primary" data-presence-pair-link href="${escape(uri)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:apple"></ha-icon>${escape(openLabel)}</a>`
    : "";
  return `${open}<a class="${escape(className)} store-link" href="${PRESENCE_PAIR_STORE_URL}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:apple"></ha-icon>${escape(storeLabel)}</a>`;
}

export function launchPresencePair(uri, win = window, doc = document) {
  if (!isAppleMobile(win.navigator) || !validAppURI(uri) || doc.hidden) return false;
  // Match HA's URL action: Companion's WKUIDelegate hands a new-window request
  // to iOS. Navigating the current webview breaks HA on a non-HTTP scheme.
  // A null WindowProxy is normal with noopener or a native handoff, not failure.
  win.open(uri, "_blank", "noopener,noreferrer");
  return true;
}

export function bindPresencePairLinks(root) {
  const links = [...root.querySelectorAll("[data-presence-pair-link]")];
  for (const link of links) {
    link.onclick = (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      event.preventDefault();
      event.stopPropagation();
      launchPresencePair(link.getAttribute("href"));
    };
  }
}
