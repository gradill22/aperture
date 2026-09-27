import { defineConfig } from "@playwright/test";

const out = process.env.E2E_OUT ?? "test-results";

export default defineConfig({
  testDir: "tests",
  outputDir: `${out}/artifacts`,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: `${out}/results.json` }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://frontend:8080",
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      // WebGL on CPU (no GPU in the container).
      args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
    },
  },
});
