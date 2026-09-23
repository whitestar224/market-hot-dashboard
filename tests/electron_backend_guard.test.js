const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("desktop backend guardian shares the project lifecycle", () => {
  const source = fs.readFileSync("electron/main.js", "utf8");

  assert.match(source, /child\.once\("exit"[\s\S]*scheduleBackendRestart\(\)/);
  assert.match(source, /Math\.min\(60000,[\s\S]*2000/);
  assert.match(source, /backendStopping \|\| appIsQuitting/);
  assert.match(source, /app\.on\("before-quit"[\s\S]*appIsQuitting = true;[\s\S]*stopBackend\(\)/);
  assert.match(source, /clearTimeout\(backendRestartTimer\)/);
});

test("desktop package carries the browser-mode guardian without installing it separately", () => {
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  const resource = pkg.build.extraResources.find((item) => item.to === "dashboard");
  assert.ok(resource.filter.includes("service_guard.py"));
});
