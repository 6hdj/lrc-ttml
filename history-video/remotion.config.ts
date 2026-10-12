/**
 * Remotion CLI 配置：这里只放"渲染行为"，**不放任何画面常量**（画面常量在 `src/constants.ts`）。
 *
 * ⚠️ 本文件刻意**不写死浏览器路径**：
 *    这台机器上 `chrome-headless-shell` 已经缓存在 `node_modules/.remotion/` 里，
 *    Remotion 自己能找到。写死本机绝对路径会让工程没法换机器跑。
 *    若某台机器上首次渲染卡在下载（国内直连 Google CDN 会挂住且不报错），
 *    两种办法：① 把下载好的 headless shell 放进 `node_modules/.remotion/`；
 *    ② 设环境变量 `REMOTION_BROWSER_EXECUTABLE` 指向本机 Edge/Chrome（下面会自动透传）。
 */
import {Config} from '@remotion/cli/config';

// 中间帧用 JPEG：1920x1080 的 PNG 一帧几 MB，1800 帧就是几个 GB
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);

const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE;
if (browserExecutable) {
  Config.setBrowserExecutable(browserExecutable);
}
