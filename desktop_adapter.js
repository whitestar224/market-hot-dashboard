(function () {
  const KEY = "xingyun:desktop-platform";
  const aliases = {
    pc: "windows",
    win: "windows",
    windows: "windows",
    apple: "macos",
    mac: "macos",
    macos: "macos"
  };

  const params = new URLSearchParams(window.location.search);
  const requested = String(params.get("desktop") || params.get("platform") || "").toLowerCase();
  const storage = window.sessionStorage;

  if (["off", "web", "none", "reset"].includes(requested)) {
    storage.removeItem(KEY);
  } else if (aliases[requested]) {
    storage.setItem(KEY, aliases[requested]);
  }

  const platform = aliases[storage.getItem(KEY)] || "";
  const root = document.documentElement;

  root.dataset.desktopPlatform = platform || "web";
  root.classList.toggle("desktop-app", Boolean(platform));
  root.classList.toggle("desktop-windows", platform === "windows");
  root.classList.toggle("desktop-macos", platform === "macos");

  window.XingyunDesktop = {
    platform,
    setPlatform(nextPlatform) {
      const normalized = aliases[String(nextPlatform || "").toLowerCase()];
      if (normalized) {
        storage.setItem(KEY, normalized);
      } else {
        storage.removeItem(KEY);
      }
      window.location.reload();
    }
  };

  const renderedHtml = new WeakMap();

  function elementKey(node) {
    if (!(node instanceof Element)) return "";
    const explicit = node.getAttribute("data-live-key");
    if (explicit) return `live:${explicit}`;
    if (node.id) return `id:${node.id}`;
    const symbol = node.getAttribute("data-symbol");
    if (symbol) {
      const detail = node.getAttribute("data-structure-interval")
        || node.getAttribute("data-role")
        || node.getAttribute("data-action")
        || "";
      return `symbol:${node.tagName}:${symbol}:${detail}`;
    }
    return "";
  }

  function compatibleNode(current, next) {
    if (!current || !next || current.nodeType !== next.nodeType) return false;
    if (current.nodeType !== Node.ELEMENT_NODE) return true;
    const currentKey = elementKey(current);
    const nextKey = elementKey(next);
    return current.tagName === next.tagName && (!currentKey || !nextKey || currentKey === nextKey);
  }

  function syncAttributes(current, next) {
    const active = document.activeElement === current;
    [...current.attributes].forEach((attribute) => {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    });
    [...next.attributes].forEach((attribute) => {
      if (active && ["value", "checked", "selected"].includes(attribute.name)) return;
      if (current.getAttribute(attribute.name) !== attribute.value) {
        current.setAttribute(attribute.name, attribute.value);
      }
    });
    if (!active && current instanceof HTMLInputElement) {
      if (current.type !== "file" && current.value !== next.value) current.value = next.value;
      current.checked = next.checked;
    } else if (!active && current instanceof HTMLTextAreaElement && current.value !== next.value) {
      current.value = next.value;
    }
  }

  function syncChildren(current, next) {
    const keyed = new Map();
    [...current.children].forEach((child) => {
      const key = elementKey(child);
      if (key) keyed.set(key, child);
    });
    let cursor = current.firstChild;
    [...next.childNodes].forEach((nextChild) => {
      const key = elementKey(nextChild);
      let match = key ? keyed.get(key) : null;
      if (!match && compatibleNode(cursor, nextChild) && !elementKey(cursor)) match = cursor;
      if (!match) {
        current.insertBefore(nextChild.cloneNode(true), cursor);
        return;
      }
      if (match !== cursor) current.insertBefore(match, cursor);
      syncNode(match, nextChild);
      cursor = match.nextSibling;
      if (key) keyed.delete(key);
    });
    while (cursor) {
      const nextCursor = cursor.nextSibling;
      cursor.remove();
      cursor = nextCursor;
    }
  }

  function syncNode(current, next) {
    if (!compatibleNode(current, next)) {
      current.replaceWith(next.cloneNode(true));
      return;
    }
    if (current.nodeType === Node.TEXT_NODE || current.nodeType === Node.COMMENT_NODE) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return;
    }
    syncAttributes(current, next);
    syncChildren(current, next);
    if (current instanceof HTMLSelectElement && document.activeElement !== current && current.value !== next.value) {
      current.value = next.value;
    }
  }

  window.XingyunLiveDom = {
    render(container, html) {
      if (!container) return false;
      const markup = String(html ?? "");
      if (renderedHtml.get(container) === markup) return false;
      const template = document.createElement("template");
      template.innerHTML = markup;
      syncChildren(container, template.content);
      renderedHtml.set(container, markup);
      return true;
    },
    text(node, value) {
      const next = String(value ?? "");
      if (node && node.textContent !== next) node.textContent = next;
    },
    invalidate(container) {
      if (container) renderedHtml.delete(container);
    }
  };
})();
