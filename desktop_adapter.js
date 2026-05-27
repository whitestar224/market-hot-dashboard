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
})();
