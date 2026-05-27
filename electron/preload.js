const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("XingyunDesktopShell", {
  platform: process.platform,
  packaged: process.env.NODE_ENV !== "development"
});
