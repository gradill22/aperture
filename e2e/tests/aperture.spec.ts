// End-to-end checks against the running stack, from a container on the internal network
// (no route to the internet). Every test also asserts the page requested nothing off-origin.
import { expect, test as base, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const OUT = process.env.E2E_OUT ?? "test-results";
const DCA: [number, number] = [-77.0377, 38.8512];

type Fixtures = { offOrigin: string[]; consoleErrors: string[] };

const test = base.extend<Fixtures>({
  // Style/validation problems surface only as console errors: fail on any.
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(String(e)));
      await use(errors);
      expect(errors, "console errors").toEqual([]);
    },
    { auto: true },
  ],
  offOrigin: async ({ page, baseURL }, use) => {
    const origin = new URL(baseURL!).origin;
    const bad: string[] = [];
    page.on("request", (r) => {
      const url = r.url();
      if (!url.startsWith(origin + "/") && !url.startsWith("data:") && !url.startsWith("blob:")) bad.push(url);
    });
    await use(bad);
    expect(bad, "requests to other origins").toEqual([]);
  },
});

async function openApp(page: Page) {
  const tile = (layer: string) =>
    page.waitForResponse((r) => r.url().includes(`/tiles/${layer}/`) && r.status() === 200);
  const tiles = Promise.all([tile("basemap"), tile("infrastructure")]);
  await page.goto("/");
  await expect(page.getByTestId("map")).toHaveAttribute("data-map-loaded", "true");
  await tiles;
  await waitIdle(page);
}

async function waitIdle(page: Page) {
  await page.waitForFunction(() => {
    const m = window.__aperture?.map;
    return !!m && m.loaded() && m.areTilesLoaded();
  });
}

const rendered = (page: Page, layers: string[]) =>
  page.evaluate(
    (ls) => window.__aperture!.map.queryRenderedFeatures({ layers: ls.filter((l) => window.__aperture!.map.getLayer(l)) }).length,
    layers,
  );

const renderedFromSource = (page: Page, source: string) =>
  page.evaluate(
    (src) => window.__aperture!.map.queryRenderedFeatures().filter((f) => f.source === src).length,
    source,
  );

test("map, tiles and overlays render offline; aircraft replay", async ({ page, offOrigin }) => {
  await openApp(page);
  const canvas = page.locator(".maplibregl-canvas");
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box!.width).toBeGreaterThan(400);

  expect(await renderedFromSource(page, "basemap")).toBeGreaterThan(50);
  expect(await renderedFromSource(page, "infrastructure")).toBeGreaterThan(0);
  await expect(page.getByText(/OpenStreetMap/).first()).toBeVisible();

  // Aircraft for the initial playhead (14:00 UTC).
  await expect
    .poll(async () => Number(await page.getByTestId("map").getAttribute("data-aircraft-count")))
    .toBeGreaterThan(20);
  await expect.poll(() => rendered(page, ["aircraft"])).toBeGreaterThan(20);

  // Playback moves the clock and keeps aircraft on screen.
  const t0 = await page.evaluate(() => window.__aperture!.store.get().t);
  await page.getByTestId("play").click();
  await page.waitForTimeout(2500);
  await page.getByTestId("play").click();
  const t1 = await page.evaluate(() => window.__aperture!.store.get().t);
  expect(t1 - t0).toBeGreaterThan(60); // 60x default speed
  expect(Number(await page.getByTestId("map").getAttribute("data-aircraft-count"))).toBeGreaterThan(20);

  // Reload: everything comes from the stack again, nothing from the internet (there is no route).
  await page.reload();
  await expect(page.getByTestId("map")).toHaveAttribute("data-map-loaded", "true");
  await waitIdle(page);
  expect(await renderedFromSource(page, "basemap")).toBeGreaterThan(50);

  await page.screenshot({ path: `${OUT}/map.png` });
  expect(offOrigin).toEqual([]);
});

test("draw a geofence, see passes on the timeline, export a finding", async ({ page, offOrigin }) => {
  await openApp(page);
  await page.evaluate((c) => window.__aperture!.map.jumpTo({ center: c, zoom: 12 }), DCA);
  await waitIdle(page);

  await page.getByTestId("tab-geofence").click();
  await page.getByTestId("draw-fence").click();

  // A box around Reagan National, drawn with the mouse.
  const corners = [
    [DCA[0] - 0.02, DCA[1] - 0.015],
    [DCA[0] + 0.02, DCA[1] - 0.015],
    [DCA[0] + 0.02, DCA[1] + 0.015],
    [DCA[0] - 0.02, DCA[1] + 0.015],
  ];
  const canvasBox = (await page.locator(".maplibregl-canvas").boundingBox())!;
  const px = await page.evaluate(
    (cs) => cs.map((c) => window.__aperture!.map.project(c as [number, number])).map((p) => [p.x, p.y]),
    corners,
  );
  const pts = px.map(([x, y]) => [canvasBox.x + x, canvasBox.y + y]);
  for (const [x, y] of [...pts, pts[0]]) {
    await page.mouse.move(x, y, { steps: 5 });
    await page.mouse.click(x, y);
    await page.waitForTimeout(150);
  }

  const summary = page.getByTestId("fence-summary");
  await expect(summary).toContainText("passes by");
  const hits = page.getByTestId("hits").locator("li");
  expect(await hits.count()).toBeGreaterThan(5);
  // Flagged on the timeline, one tick per pass.
  expect(await page.locator(".hit-tick").count()).toBe(await hits.count());
  expect(await rendered(page, ["fence-line"])).toBeGreaterThan(0);

  // Select a pass: playhead jumps to its entry, track is highlighted, finding export appears.
  await hits.first().locator(".hit-row").click();
  const selected = await page.evaluate(() => {
    const s = window.__aperture!.store.get();
    return { hit: s.selectedHit, t: s.t };
  });
  expect(selected.hit).toMatch(/^[0-9a-f]{6}:\d+$/);
  await page.getByTestId("finding-note").fill("E2E: pass over DCA");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-finding").click()]);
  expect(download.suggestedFilename()).toMatch(/^finding-[0-9a-f]{6}-\d{8}-\d{4}Z\.md$/);
  const md = readFileSync((await download.path())!, "utf8");
  expect(md).toContain("# Finding:");
  expect(md).toContain("E2E: pass over DCA");
  expect(md).toContain('"type":"Polygon"');
  expect(md).toContain("v2026.09.24-planes-readsb-prod-0");
  await page.screenshot({ path: `${OUT}/geofence.png` });
  expect(offOrigin).toEqual([]);
});

test("chat replies apply map actions", async ({ page, request, offOrigin }) => {
  // The LLM itself is exercised by Gate 2; here /api/chat is stubbed so the UI path is deterministic.
  const track = await (await request.get("/api/tracks/a00929")).json();
  await page.route("**/api/chat", (route) =>
    route.fulfill({
      json: {
        reply: "N101HQ (a00929) flew 9 legs.",
        map_actions: [
          { type: "show_track", icao24: "a00929", label: "N101HQ", legs: track.legs },
          { type: "fit_bounds", bbox: [-77.2, 38.7, -76.8, 39.1] },
        ],
        tool_calls: [{ name: "get_entity_track", arguments: { identifier: "N101HQ" }, ok: true }],
      },
    }),
  );
  await openApp(page);
  await page.getByTestId("chat-input").fill("Show me the track of N101HQ");
  await page.getByTestId("chat-input").press("Enter");
  await expect(page.getByText("N101HQ (a00929) flew 9 legs.")).toBeVisible();
  await expect(page.locator(".tool", { hasText: "get_entity_track" })).toBeVisible();
  await waitIdle(page);
  await expect.poll(() => rendered(page, ["tracks"])).toBeGreaterThan(0);
  expect(offOrigin).toEqual([]);
});

test("chat renders Markdown and previews the draft", async ({ page, offOrigin }) => {
  const reply = [
    "**2 aircraft** passed [DCA](/provenance.json):",
    "",
    "| icao24 | min km |",
    "|---|---:|",
    "| a00929 | 1.2 |",
    "",
    "![plot](http://example.com/plot.png) <b>raw</b>",
  ].join("\n");
  await page.route("**/api/chat", (route) =>
    route.fulfill({ json: { reply, map_actions: [], tool_calls: [] } }),
  );
  await openApp(page);
  const input = page.getByTestId("chat-input");
  const preview = page.getByTestId("chat-preview");

  await input.fill("plain question");
  await expect(preview).toHaveCount(0);
  await input.fill("which of **these** used `a00929`?");
  await expect(preview.locator("strong")).toHaveText("these");
  await expect(preview.locator("code")).toHaveText("a00929");
  await input.press("Enter");
  await expect(preview).toHaveCount(0);

  const user = page.locator(".msg.user").last();
  await expect(user.locator("strong")).toHaveText("these");
  const answer = page.locator(".msg.assistant").last();
  await expect(answer.locator("strong")).toHaveText("2 aircraft");
  await expect(answer.locator("table td").first()).toHaveText("a00929");
  const link = answer.getByRole("link", { name: "DCA" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  // Images and raw HTML never become elements: the image URL is not fetched (offOrigin stays empty).
  await expect(answer.locator("img, b")).toHaveCount(0);
  await expect(answer.locator(".img-alt")).toHaveText("[plot]");
  await page.screenshot({ path: `${OUT}/chat-markdown.png` });
  expect(offOrigin).toEqual([]);
});

test("side panel width: drag, keyboard, reset, remembered", async ({ page }) => {
  await openApp(page);
  const splitter = page.getByTestId("splitter");
  const aside = page.locator("aside");
  const width = async () => Math.round((await aside.boundingBox())!.width);
  const mapWidth = () => page.evaluate(() => window.__aperture!.map.getCanvas().clientWidth);
  await expect(splitter).toHaveAttribute("aria-valuenow", "400");
  expect(await width()).toBe(400);
  const map0 = await mapWidth();

  // Drag 200 px to the left: panel wider, map narrower (MapLibre follows its container).
  const box = (await splitter.boundingBox())!;
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2];
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 100, y, { steps: 5 });
  await page.mouse.move(x - 200, y, { steps: 5 });
  await page.mouse.up();
  expect(await width()).toBe(600);
  await expect(splitter).toHaveAttribute("aria-valuenow", "600");
  await expect.poll(mapWidth).toBe(map0 - 200);

  // Dragging far past the limit clamps: the map keeps 360 px, the panel tops out at 900.
  await page.mouse.move(x - 200, y);
  await page.mouse.down();
  await page.mouse.move(0, y, { steps: 5 });
  await page.mouse.up();
  expect(await width()).toBe(900);

  await splitter.focus();
  await page.keyboard.press("Home");
  expect(await width()).toBe(300);
  await page.keyboard.press("ArrowLeft");
  expect(await width()).toBe(316);
  await page.keyboard.press("Shift+ArrowLeft");
  expect(await width()).toBe(380);
  await page.keyboard.press("ArrowRight");
  expect(await width()).toBe(364);

  await page.reload();
  await expect(page.getByTestId("map")).toHaveAttribute("data-map-loaded", "true");
  expect(await width()).toBe(364);

  await splitter.dblclick();
  expect(await width()).toBe(400);
  await page.screenshot({ path: `${OUT}/splitter.png` });
});

test("MCP endpoint is reachable through the edge", async ({ request }) => {
  const r = await request.post("/mcp", {
    headers: { accept: "application/json, text/event-stream", "content-type": "application/json" },
    data: { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
  });
  expect(r.status()).toBe(200);
  const names = (await r.json()).result.tools.map((t: { name: string }) => t.name).sort();
  expect(names).toEqual(["geofence_alert", "get_entity_track", "search_entities"]);
});

declare global {
  interface Window {
    __aperture?: {
      map: import("maplibre-gl").Map;
      store: { get(): { t: number; selectedHit: string | null } };
    };
  }
}
