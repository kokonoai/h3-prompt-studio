import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));

try {
  await page.goto("http://127.0.0.1:8766/?production", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "剧本管理" }).first().click();
  await page.getByRole("heading", { name: "剧本管理" }).waitFor();
  const picker = page.locator(".series-toolbar select");
  const count = await picker.locator("option").count();
  if (count > 1) {
    await picker.selectOption({ index: 1 });
    await page.locator(".series-assembly-overview").waitFor();
    await page.getByRole("button", { name: "合并全剧" }).waitFor();
    await page.getByRole("button", { name: "合并选中集" }).waitFor();
    await page.getByRole("button", { name: "合并本集" }).first().waitFor();
  }
  await page.screenshot({ path: "D:/h3tool/logs/series-ui-smoke.png", fullPage: true });
  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  console.log(JSON.stringify({ ok: !errors.length && !desktopOverflow && !mobileOverflow,
    savedScripts: count - 1, desktopOverflow, mobileOverflow, errors }, null, 2));
  if (errors.length || desktopOverflow || mobileOverflow) process.exitCode = 1;
} finally {
  await browser.close();
}
