// gmgn-cli 的时钟补偿包装器。
//
// 背景：本机 Windows Time 服务没启动，系统时钟比标准时间慢 ~18 秒；
// GMGN 的签名接口用 `Math.floor(Date.now()/1000)` 当 timestamp，服务端判定
// AUTH_TIMESTAMP_EXPIRED（记忆里 401 的真凶就是本机时钟偏差，不是 key 失效）。
// 修系统时钟要管理员权限，所以这里只在**本进程内**把 Date 往前拨。
//
// 用法：
//   GMGN_SKEW_MS=18500 node tools/gmgn_cli_skew.mjs market search -q 西瓜 --chain bsc --raw
// 偏差怎么取：对比 `https://api.binance.com/api/v3/time` 的 serverTime 与本机时钟。
const SKEW = Number(process.env.GMGN_SKEW_MS ?? 0);
if (!Number.isFinite(SKEW)) {
  console.error('GMGN_SKEW_MS 必须是毫秒数');
  process.exit(2);
}

const realNow = Date.now.bind(Date);
const RealDate = Date;
class ShiftedDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(realNow() + SKEW);
    else super(...args);
  }
  static now() {
    return realNow() + SKEW;
  }
}
globalThis.Date = ShiftedDate;

process.argv = [process.argv[0], 'gmgn-cli', ...process.argv.slice(2)];
await import('file:///C:/Users/ZhuanZ1/AppData/Roaming/npm/node_modules/gmgn-cli/dist/index.js');
