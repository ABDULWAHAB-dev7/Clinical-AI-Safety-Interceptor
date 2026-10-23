import { defineConfig } from "@playwright/test";

// For tests/live: the dashboard must already be running and connected to a fresh interceptor. LIVE_BASE_URL is its address.
export default defineConfig({
  testDir: "tests/live",
  outputDir: "test-results-live",
  workers: 1, // the specs share one interceptor
  use: { baseURL: process.env.LIVE_BASE_URL ?? "http://127.0.0.1:3200" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
