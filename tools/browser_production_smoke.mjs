import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
page.on("pageerror", error => errors.push(error.message));

try {
  await page.goto("http://127.0.0.1:8766/?production", { waitUntil: "networkidle" });
  const uiLanguage = page.getByLabel("界面语言");
  await uiLanguage.selectOption("ja");
  await page.getByRole("heading", { name: "長編制作" }).waitFor();
  await page.getByLabel("表示言語").selectOption("zh-CN");
  await page.getByRole("heading", { name: "长片制作台" }).waitFor();

  const productionPicker = page.locator(".production-picker select");
  if (await productionPicker.locator("option").count() > 1) {
    await productionPicker.selectOption({ index: 1 });
    await page.locator("#production-card-library").waitFor();
    const languageValues = await page.locator(".production-bible select").first().locator("option").evaluateAll(
      options => options.map(option => option.value),
    );
    if (languageValues.join(",") !== "zh-CN,zh-TW,en,ja") throw new Error("Project language options are incomplete.");

    const cards = page.locator(".production-asset-card");
    const cardCount = await cards.count();
    if (cardCount) {
      const collapsedHeights = await cards.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
      if (collapsedHeights.some(height => height > 100)) throw new Error("Collapsed asset cards are too tall.");
      await cards.first().locator("button[title='编辑卡片']").click();
      if (await page.locator(".production-asset-card.expanded").count() !== 1) throw new Error("Exactly one asset card should expand.");
    }
    await page.getByRole("button", { name: /^角色声线卡/ }).click();
    await page.getByRole("button", { name: /新建.*角色声线卡/ }).click();
    const voiceLanguageValues = await page.locator(".production-card-fields label").filter({ hasText: "目标语言" }).locator("select option").evaluateAll(
      options => options.map(option => option.value),
    );
    if (voiceLanguageValues.join(",") !== "zh-CN,zh-TW,en,ja") throw new Error("Voice language options are incomplete: " + JSON.stringify(voiceLanguageValues));
    const layout = await page.evaluate(() => {
      const section = document.querySelector("#production-card-library").getBoundingClientRect();
      const overview = document.querySelector(".production-overview").getBoundingClientRect();
      return { sectionBottom: section.bottom, overviewTop: overview.top,
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth };
    });
    if (layout.overviewTop < layout.sectionBottom - 1) throw new Error("Asset cards overlap the production overview.");
    if (layout.horizontalOverflow) throw new Error("Production UI has horizontal overflow.");
    await page.screenshot({ path: "D:/h3tool/logs/production-ui-smoke.png", fullPage: true });
    console.log(JSON.stringify({ ok: true, cardCount, voiceLanguageValues, ...layout, errors }, null, 2));
  } else {
    console.log(JSON.stringify({ ok: true, cardCount: 0, note: "No saved production; language switch verified.", errors }, null, 2));
  }
  if (errors.length) throw new Error("Browser console errors: " + errors.join(" | "));
} finally {
  await browser.close();
}
