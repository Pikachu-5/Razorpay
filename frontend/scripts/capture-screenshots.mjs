/**
 * Capture the README screenshots from a running dashboard.
 *
 * Screenshots go stale faster than any other kind of documentation, so this is
 * a script rather than a one-off: re-run it after a UI change and the README is
 * current again.
 *
 * Prerequisites: the API and the dashboard are both running, and the demo
 * baseline has been seeded (otherwise every panel captures as empty):
 *
 *   docker compose up -d postgres
 *   .\.venv\Scripts\python -m alembic upgrade head
 *   .\.venv\Scripts\python scripts\seed_demo_baseline.py --days 30
 *   powershell -File scripts\dev_server.ps1
 *   cd frontend; npm run dev
 *
 * Then, from `frontend/`:
 *
 *   npm run screenshots [-- <baseUrl>]
 *
 * It lives here rather than in the repo-level scripts/ directory because Node
 * resolves imports from the script's own location, and Playwright is a frontend
 * dev dependency.
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE_URL = process.argv[2] ?? "http://localhost:5173";
const OUT_DIR = fileURLToPath(new URL("../../docs/img", import.meta.url));
const VIEWPORT = { width: 1440, height: 900 };

/** Click a button by the text it starts with, and wait for the tab to settle. */
async function openTab(page, label) {
  await page.getByRole("button", { name: new RegExp(`^${label}`) }).first().click();
  await page.waitForTimeout(1200);
}

async function shoot(target, name, options = {}) {
  const file = path.join(OUT_DIR, `${name}.png`);
  await target.screenshot({ path: file, ...options });
  console.log(`  wrote ${path.relative(process.cwd(), file)}`);
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 2 });

  console.log(`Capturing from ${BASE_URL}`);
  // Not `networkidle`: the console holds an SSE stream open and polls on a
  // timer, so the network is never idle and the wait would always time out.
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Open the console" }).first().waitFor();

  // Landing page: the hero, then each band of the scrolling body.
  await page.waitForTimeout(1500);
  await shoot(page, "01-landing");
  for (const [id, name] of [
    ["how", "01b-how-it-works"],
    ["mix", "01c-failure-mix"],
    ["worth", "01d-calculator"],
    ["evidence", "01e-evidence"],
  ]) {
    const band = page.locator(`#${id}`);
    if (!(await band.count())) continue;
    await band.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    await shoot(band, name);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);

  // Into the console.
  await page.getByRole("button", { name: "Open the console" }).first().click();
  await page.waitForTimeout(2500);
  await shoot(page, "02-monitor");

  await openTab(page, "Recovery queue");
  await shoot(page, "03-recovery-queue");

  await openTab(page, "Evidence");
  await page.waitForTimeout(1500);
  // The economics comparison is the point of this tab, so frame it rather than
  // capturing a full page of mostly-scrolled panels.
  const economics = page.locator(".economics-card").first();
  if (await economics.count()) {
    await economics.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await shoot(economics, "04-economics-benchmark");
  }
  await shoot(page, "05-evidence", { fullPage: true });

  // The preflight is the safety story, so capture one open. Model promotion is
  // the reliable one to drive: the re-decide preflight only appears on an
  // opportunity still in `open`, which a seeded run may not have.
  await openTab(page, "Evidence");
  // The dropdown defaults to the newest card, which is usually the promoted
  // one — and the promote button is disabled for the model already running.
  // Pick a different candidate so the preflight is reachable.
  const candidates = page.locator(".promote-controls select").first();
  if (await candidates.count()) {
    // The model cards arrive from the API after the tab mounts, so the select
    // is briefly empty and the promote button briefly disabled. Wait for the
    // options rather than for a fixed delay.
    await candidates
      .locator("option")
      .first()
      .waitFor({ timeout: 10_000 })
      .catch(() => {});
    const values = await candidates.locator("option").evaluateAll((nodes) =>
      nodes.map((n) => n.value),
    );
    if (values.length > 1) await candidates.selectOption(values[0]);
    await page.waitForTimeout(700);
  }
  const promote = page.getByRole("button", { name: /^Promote to shadow$/ }).first();
  if ((await promote.count()) && (await promote.isEnabled())) {
    await promote.scrollIntoViewIfNeeded();
    await promote.click();
    await page.waitForTimeout(600);
    const dialog = page.getByRole("alertdialog");
    if (await dialog.count()) {
      await shoot(dialog, "06-preflight");
      // Leave nothing promoted as a side effect of taking a picture.
      await page.getByRole("button", { name: "Cancel" }).first().click();
    }
  }

  // The decision audit trace: the whole chain for one opportunity.
  await openTab(page, "Recovery queue");
  const queueRow = page.locator(".queue-row-button").first();
  if (await queueRow.count()) {
    await queueRow.click();
    await page.waitForTimeout(1600);
    const trace = page.getByRole("dialog");
    if (await trace.count()) await shoot(trace, "07-audit-trace");
  }

  await browser.close();
  console.log("Done.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
