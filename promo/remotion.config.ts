/**
 * Remotion CLI 配置。
 *
 * ⚠️ 这里只放"渲染行为"，不放任何画面常量（画面常量见 src/core/theme.ts）。
 * ⚠️ 浏览器可执行文件**不写死**：本机没有 Google Chrome，第一次渲染要下
 *    chrome-headless-shell。下载位置由 Remotion 决定；若默认位置不可写，
 *    在命令行先设 REMOTION_BROWSER_EXECUTABLE 指向工作区内的可执行文件即可，
 *    这里会自动透传给 Remotion（保持工程可移植，不把本机绝对路径提交进去）。
 */
import {Config} from '@remotion/cli/config';

// 中间帧用 JPEG：1920x1080 的 PNG 一帧数 MB，62 秒就是几个 GB（与基线 record.js 同一取舍）
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);

const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE;
if (browserExecutable) {
  Config.setBrowserExecutable(browserExecutable);
}
