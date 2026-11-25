// Replays recorded evaluation outputs through the built UI and captures README screenshots.
// Usage: node scripts/screenshots/capture.cjs <fixtures.json> <base-url> <out-dir>
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const [fixturesPath, baseUrl, outDir] = process.argv.slice(2);
const fx = JSON.parse(fs.readFileSync(fixturesPath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

async function page(browser, { theme, width = 1440, height = 900, mobile = false, history = true }) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: theme,
    isMobile: mobile,
    hasTouch: mobile,
  });
  await context.addInitScript(
    ([conversations, theme]) => {
      localStorage.setItem("k8s-rag.conversations.v1", JSON.stringify(conversations));
      localStorage.setItem("theme", theme);
    },
    [history ? fx.history : [], theme],
  );
  const p = await context.newPage();
  const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await p.route("**/health", (r) => r.fulfill(json(fx.health)));
  await p.route("**/api/index/status", (r) => r.fulfill(json(fx.index)));
  await p.route("**/api/eval/latest", (r) => r.fulfill(json(fx.summary)));
  await p.route("**/api/eval/latest/questions", (r) => r.fulfill(json(fx.questions)));
  await p.route("**/api/chat/stream", (r) =>
    r.fulfill({ status: 200, contentType: "text/event-stream", body: fx.stream }),
  );
  return p;
}

async function ask(p) {
  await p.fill("textarea", fx.question);
  await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-actions");
  await p.waitForTimeout(300);
}

(async () => {
  const browser = await chromium.launch();
  const shot = (p, name, opts = {}) => p.screenshot({ path: path.join(outDir, `${name}.png`), ...opts });

  for (const theme of ["light", "dark"]) {
    let p = await page(browser, { theme, history: false });
    await p.goto(`${baseUrl}/#/`);
    await p.waitForSelector(".empty");
    await shot(p, `empty-${theme}`);
    await p.close();

    p = await page(browser, { theme });
    await p.goto(`${baseUrl}/#/`);
    await ask(p);
    await p.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
    await shot(p, `chat-${theme}`);

    await p.click("text=Retrieval details");
    await p.waitForSelector(".inspector");
    await p.evaluate(() => document.querySelector(".sources").scrollIntoView({ block: "start" }));
    await p.waitForTimeout(150);
    await shot(p, `inspector-${theme}`);

    await p.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
    await p.hover(".prose .cite-wrap >> nth=0");
    await p.waitForTimeout(150);
    const box = await p.locator(".msg.assistant").boundingBox();
    await shot(p, `citation-${theme}`, { clip: { x: box.x - 20, y: Math.max(box.y - 140, 0), width: 820, height: 420 } });

    await p.click("text=Evaluation");
    await p.waitForSelector(".stats");
    await p.waitForSelector(".matrix");
    await shot(p, `eval-${theme}`);
    await p.evaluate(() => document.querySelectorAll(".card")[1].scrollIntoView({ block: "start" }));
    await shot(p, `eval-detail-${theme}`);
    await p.close();
  }

  const m = await page(browser, { theme: "light", width: 390, height: 844, mobile: true });
  await m.goto(`${baseUrl}/#/`);
  await ask(m);
  await m.evaluate(() => document.querySelector(".scroll").scrollTo(0, 0));
  await shot(m, "mobile-chat-light");
  await browser.close();
  console.log("screenshots written to", outDir);
})();
