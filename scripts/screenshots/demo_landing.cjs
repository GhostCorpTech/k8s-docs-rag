// Captures the public-demo landing page (no key) in English and Russian.
// Needs a backend with DEMO_MODE=true behind the UI.
// Usage: node scripts/screenshots/demo_landing.cjs <base-url> <out-dir>
const path = require("path");
const { chromium } = require("playwright");

const [baseUrl, outDir] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch();
  for (const lang of ["en", "ru"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
    await context.addInitScript((value) => localStorage.setItem("k8s-rag.lang", value), lang);
    const page = await context.newPage();
    await page.goto(`${baseUrl}/#/`);
    await page.waitForSelector("#access-key");
    await page.waitForTimeout(300);
    // Everything above the embedded product screenshot.
    const shot = await page.locator(".landing-shot").boundingBox();
    await page.screenshot({
      path: path.join(outDir, `demo-landing-${lang}.png`),
      fullPage: true,
      clip: { x: 0, y: 0, width: 1440, height: Math.round(shot.y) },
    });
    await context.close();
  }
  await browser.close();
})();
