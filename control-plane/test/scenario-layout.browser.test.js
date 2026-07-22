import assert from "node:assert/strict";
import { access, readFile, rm, stat } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const CONTROL_PLANE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MIME_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".jpg", "image/jpeg"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
]);

async function chromeExecutable() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Try the next standard Chrome location.
    }
  }
  throw new Error("Chrome is required for scenario layout regression tests. Set CHROME_BIN if it is installed elsewhere.");
}

async function staticServer(root) {
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      let filename = path.resolve(root, `.${pathname}`);
      if (!filename.startsWith(`${root}${path.sep}`)) throw new Error("Path escapes browser fixture root.");
      if ((await stat(filename)).isDirectory()) filename = path.join(filename, "index.html");
      const body = await readFile(filename);
      response.writeHead(200, { "content-type": MIME_TYPES.get(path.extname(filename)) || "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found");
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

function assertBoundsMatch(actual, expected, label, tolerance = 0.15) {
  for (const property of ["x", "y", "width", "height"]) {
    assert.ok(Math.abs(actual[property] - expected[property]) <= tolerance,
      `${label} ${property}: ${actual[property]} != ${expected[property]}`);
  }
}

async function loadScenario(page, origin, scenarioId, frameIndex = 0) {
  await page.goto(`${origin}/scenarios/?scenario=${scenarioId}`);
  await page.locator("#scenario-detail:not([hidden])").waitFor();
  if (frameIndex !== 0) {
    await page.locator("#frame-scrubber").evaluate((scrubber, index) => {
      scrubber.value = String(index);
      scrubber.dispatchEvent(new Event("input", { bubbles: true }));
    }, frameIndex);
  }
  await page.waitForFunction(() => {
    const image = document.querySelector("#scenario-frame");
    return image?.complete && image.naturalWidth > 0 && document.querySelector("#media-state")?.hidden;
  });
}

async function frameGeometry(page) {
  return page.evaluate(() => {
    const bounds = (element) => {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    };
    const stage = document.querySelector("#frame-stage");
    const image = document.querySelector("#scenario-frame");
    const svg = document.querySelector("#scenario-overlay");
    const imageBounds = bounds(image);
    const imageScale = Math.min(imageBounds.width / image.naturalWidth, imageBounds.height / image.naturalHeight);
    const imageContent = {
      x: imageBounds.x + (imageBounds.width - image.naturalWidth * imageScale) / 2,
      y: imageBounds.y + (imageBounds.height - image.naturalHeight * imageScale) / 2,
      width: image.naturalWidth * imageScale,
      height: image.naturalHeight * imageScale,
    };
    const svgBounds = bounds(svg);
    const viewBox = svg.viewBox.baseVal;
    const svgScale = Math.min(svgBounds.width / viewBox.width, svgBounds.height / viewBox.height);
    const svgContent = {
      x: svgBounds.x + (svgBounds.width - viewBox.width * svgScale) / 2,
      y: svgBounds.y + (svgBounds.height - viewBox.height * svgScale) / 2,
      width: viewBox.width * svgScale,
      height: viewBox.height * svgScale,
    };
    const target = document.querySelector(".overlay-box.target");
    return {
      image: imageBounds,
      imageContent,
      stage: bounds(stage),
      svg: svgBounds,
      svgContent,
      target: target ? bounds(target) : null,
    };
  });
}

test("image pixels and SVG annotations share one rendered coordinate space", async (context) => {
  const output = path.join(CONTROL_PLANE, "dist-test-browser-layout");
  context.after(async () => rm(output, { recursive: true, force: true }));
  const build = spawnSync(process.execPath, ["scripts/build-scenario-catalog.mjs", "--output", output], {
    cwd: CONTROL_PLANE,
    encoding: "utf8",
  });
  assert.equal(build.status, 0, `${build.stdout}\n${build.stderr}`);

  const fixture = await staticServer(output);
  context.after(fixture.close);
  const browser = await chromium.launch({ executablePath: await chromeExecutable(), headless: true });
  context.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  await loadScenario(page, fixture.origin, "synthetic-acquisition", 2);
  const synthetic = await frameGeometry(page);
  assertBoundsMatch(synthetic.image, synthetic.stage, "synthetic image layer");
  assertBoundsMatch(synthetic.svg, synthetic.stage, "synthetic SVG layer");
  assertBoundsMatch(synthetic.imageContent, synthetic.svgContent, "synthetic content transform");
  assert.ok(synthetic.target, "known synthetic target overlay must render");
  const scale = synthetic.imageContent.width / 160;
  assertBoundsMatch(synthetic.target, {
    x: synthetic.imageContent.x + 8 * scale,
    y: synthetic.imageContent.y + 40 * scale,
    width: 20 * scale,
    height: 26 * scale,
  }, "known [8,40,28,66] target overlay", 0.25);

  await loadScenario(page, fixture.origin, "rvmot-a1c9", 75);
  const realVideo = await frameGeometry(page);
  assertBoundsMatch(realVideo.image, realVideo.stage, "real-video image layer");
  assertBoundsMatch(realVideo.svg, realVideo.stage, "real-video SVG layer");
  assertBoundsMatch(realVideo.imageContent, realVideo.svgContent, "real-video content transform");
  assert.ok(realVideo.target, "dense real-video tracked-object overlay must render");
  assert.equal(await page.locator("#ignores-overlay-label").isHidden(), true);
  assert.equal(await page.locator("#region-overlay-label").isHidden(), true);
  assert.match(await page.locator("#overlay-disclosure").textContent(), /never these boxes, identities, annotations, or future frames/);

  const before = Number(await page.locator("#frame-scrubber").inputValue());
  await page.locator("#play-pause").click();
  await page.waitForFunction((value) => Number(document.querySelector("#frame-scrubber").value) >= value + 3, before);
  await page.locator("#play-pause").click();
  assert.equal(await page.locator("#playback-speed").inputValue(), "1");

  for (const width of [320, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    await loadScenario(page, fixture.origin, "synthetic-acquisition", 2);
    const mobile = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - innerWidth,
      controls: [...document.querySelectorAll(".viewer-controls button")].map((button) => {
        const rect = button.getBoundingClientRect();
        return { width: rect.width, height: rect.height };
      }),
    }));
    assert.ok(mobile.overflow <= 0, `${width}px viewport must not overflow`);
    for (const control of mobile.controls) {
      assert.ok(control.width >= 44 && control.height >= 44, `${width}px viewer buttons must remain at least 44px`);
    }
  }
});
