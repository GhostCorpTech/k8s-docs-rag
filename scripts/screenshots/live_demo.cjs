// Records the README demo against a *running* stack (real index, real LLM) and captures
// screenshots from the same conversation, so every theme/layout reuses one paid answer.
// Usage: node scripts/screenshots/live_demo.cjs <base-url> <out-dir>
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const [baseUrl, outDir] = process.argv.slice(2);
const QUESTION = "Как проверить rollout Deployment и откатить неудачное обновление?";
fs.mkdirSync(outDir, { recursive: true });

async function newPage(browser, { theme, width = 1440, height = 900, mobile = false, video = false, storage = null }) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: video ? 1 : 2,
    colorScheme: theme,
    isMobile: mobile,
    hasTouch: mobile,
    recordVideo: video ? { dir: outDir, size: { width, height } } : undefined,
  });
  await context.addInitScript(
    ([theme, storage]) => {
      localStorage.setItem("theme", theme);
      if (storage) localStorage.setItem("k8s-rag.conversations.v1", storage);
    },
    [theme, storage],
  );
  return { context, page: await context.newPage() };
}

(async () => {
  const browser = await chromium.launch();
  const shot = (page, name, opts = {}) => page.screenshot({ path: path.join(outDir, `${name}.png`), ...opts });

  // 1) Recorded demo: type, stream, open retrieval details.
  const { context, page } = await newPage(browser, { theme: "light", video: true });
  await page.goto(`${baseUrl}/#/`);
  await page.waitForSelector(".empty");
  await page.waitForTimeout(800);
  await page.click("textarea");
  await page.keyboard.type(QUESTION, { delay: 35 });
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter");
  await page.waitForSelector(".msg-actions", { timeout: 180_000 });
  await page.waitForTimeout(1200);
  await page.click("text=Retrieval details");
  await page.waitForSelector(".inspector");
  await page.evaluate(() => document.querySelector(".inspector").scrollIntoView({ behavior: "smooth", block: "center" }));
  await page.waitForTimeout(2500);
  const storage = await page.evaluate(() => localStorage.getItem("k8s-rag.conversations.v1"));
  const video = page.video();
  await context.close();
  fs.renameSync(await video.path(), path.join(outDir, "demo.webm"));
  fs.writeFileSync(path.join(outDir, "conversation.json"), storage);

  // 2) Screenshots from the same conversation (no further LLM calls).
  for (const theme of ["light", "dark"]) {
    const { context, page } = await newPage(browser, { theme, storage });
    await page.goto(`${baseUrl}/#/`);
    await page.click(".history-item >> nth=0");
    await page.waitForSelector(".msg-actions");
    await page.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
    await page.waitForTimeout(300);
    await shot(page, `chat-${theme}`);
    await page.click("text=Retrieval details");
    await page.waitForSelector(".inspector");
    await page.evaluate(() => document.querySelector(".inspector").scrollIntoView({ block: "end" }));
    await page.waitForTimeout(200);
    await shot(page, `inspector-${theme}`);
    if (theme === "light") {
      await page.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
      await page.hover(".prose .cite-wrap >> nth=0");
      await page.waitForTimeout(200);
      const box = await page.locator(".msg.assistant").boundingBox();
      await shot(page, "citation-light", { clip: { x: box.x - 20, y: Math.max(box.y - 140, 0), width: 820, height: 420 } });
    }
    await page.click("text=Evaluation");
    await page.waitForSelector(".matrix");
    await shot(page, `eval-${theme}`);
    if (theme === "dark") {
      await page.evaluate(() => document.querySelectorAll(".card")[1].scrollIntoView({ block: "start" }));
      await shot(page, "eval-detail-dark");
    }
    await context.close();
  }

  const empty = await newPage(browser, { theme: "light" });
  await empty.page.goto(`${baseUrl}/#/`);
  await empty.page.waitForSelector(".empty");
  await empty.page.waitForTimeout(300);
  await shot(empty.page, "empty-light");
  await empty.context.close();

  const mobile = await newPage(browser, { theme: "light", width: 390, height: 844, mobile: true, storage });
  await mobile.page.goto(`${baseUrl}/#/`);
  await mobile.page.click("button[aria-label='Open menu']");
  await mobile.page.click(".history-item >> nth=0");
  await mobile.page.waitForSelector(".msg-actions");
  await mobile.page.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
  await mobile.page.waitForTimeout(400);
  await shot(mobile.page, "mobile-chat-light");
  await mobile.context.close();

  await browser.close();
  console.log("done:", outDir);
})();
