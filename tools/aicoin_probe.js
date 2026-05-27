const fs = require("fs");
const os = require("os");
const path = require("path");

const addonPath = process.env.AICOIN_ADDON_PATH;
if (!addonPath) {
  fs.writeSync(
    2,
    "AICOIN_ADDON_PATH is required, for example: D:/AICOIN/resources/app.asar.unpacked/node_modules/@aicoin/cryptaddon/build/Release/Aicoin_Crypt_Addon.node\n",
  );
  process.exit(1);
}

function line(value) {
  fs.writeSync(1, `${value}\n`);
  if (process.env.AICOIN_PROBE_LOG) {
    fs.appendFileSync(process.env.AICOIN_PROBE_LOG, `${value}\n`);
  }
}

line("probe:start");
const addon = require(addonPath);
line(`probe:loaded:${Object.keys(addon).join(",")}`);

if (process.argv.includes("call") || process.argv.includes("--call")) {
  const buf = Buffer.alloc(16);
  buf.writeUInt32LE(7);
  buf[4] = 0x01;
  buf[5] = 0xa8;
  buf[6] = 0x8a;
  line("probe:before-call");
  try {
    const result = addon.cryptProcesss(buf, 7, (...args) => {
      line(
        `probe:callback:${args
          .map((arg) =>
            Buffer.isBuffer(arg)
              ? `buffer:${arg.length}:${arg.toString("hex").slice(0, 80)}`
              : `${typeof arg}:${String(arg).slice(0, 80)}`,
          )
          .join("|")}`,
      );
    });
    line(`probe:return:${typeof result}:${String(result)}`);
  } catch (error) {
    line(`probe:error:${error && error.stack ? error.stack : String(error)}`);
  }
  setTimeout(() => line("probe:timer"), 2000);
} else {
  line(`probe:dirname:${path.dirname(addonPath)}`);
}

if (process.argv.includes("wrap")) {
  const wrapperPath = process.env.AICOIN_WRAPPER_PATH;
  if (!wrapperPath) {
    line("wrap:missing-wrapper-path:set AICOIN_WRAPPER_PATH to the local wrapper index.js");
    process.exit(1);
  }
  const wrapper = require(wrapperPath);
  const describe = (args) =>
    args
      .map((arg) =>
        Buffer.isBuffer(arg)
          ? `buffer:${arg.length}:${arg.toString("hex").slice(0, 120)}:${arg.toString("utf8").slice(0, 80)}`
          : `${typeof arg}:${String(arg).slice(0, 120)}`,
      )
      .join("|");
  const call = (name, ...args) =>
    new Promise((resolve) => {
      line(`wrap:before:${name}`);
      wrapper[name](...args, (...cbArgs) => {
        line(`wrap:callback:${name}:${describe(cbArgs)}`);
        resolve(cbArgs);
      });
    });
  const parsePayload = (buffer) => {
    if (!Buffer.isBuffer(buffer) || buffer.length < 8) return Buffer.alloc(0);
    return buffer.subarray(6, Math.max(6, buffer.length - 2));
  };
  (async () => {
    await call("GetVersion");
    const md5Host = process.argv.includes("os-host") ? os.hostname() : "vip-pcapi.aicoin.com";
    line(`wrap:md5Host:${md5Host}`);
    const md5Args = await call("GetMD5KEY", md5Host);
    const condHost = await call("GetCond", md5Host);
    const condArgs = await call("GetCond", "vip-pcapi.aicoin.com/api/");
    line(`wrap:condArgCount:${condArgs.length}`);
    if (process.argv.includes("enc")) {
      const md5Payload = parsePayload(md5Args[1]).toString("utf8");
      const condBuffer = process.argv.includes("api-host") ? condArgs[1] : condHost[1];
      const condPayload = parsePayload(condBuffer).toString("utf8");
      const md5Key = process.argv.includes("md5-only") ? md5Payload : condPayload.slice(0, 32);
      const version = condPayload.slice(32) || "CRYPT_V1.0.0";
      const timestamp = Math.floor(Date.now() / 1000);
      line(`wrap:parsed:md5Len=${md5Key.length}:version=${version}:ts=${timestamp}`);
      if (process.argv.includes("set-user")) {
        const userId = process.env.AICOIN_PROBE_USER_ID || "";
        const userData = process.env.AICOIN_PROBE_USER_DATA || path.join(os.homedir(), "AppData", "Roaming", "AiCoin");
        if (userId) {
          await call("SetUserID", userId, Buffer.from(userData));
        } else {
          line("wrap:set-user-skipped:set AICOIN_PROBE_USER_ID to test user binding");
        }
      }
      await call("InitKey", timestamp, version, md5Key);
      const body = JSON.stringify({
        size: 20,
        page: 1,
        currency: "USD",
        keyWord: "",
        customGroupIds: [],
      });
      await call("EncMsg", body);
    }
    setTimeout(() => line("wrap:done"), 1000);
  })().catch((error) => line(`wrap:error:${error && error.stack ? error.stack : String(error)}`));
}
