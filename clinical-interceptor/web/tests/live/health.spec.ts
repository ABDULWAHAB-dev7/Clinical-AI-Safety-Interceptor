import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// Every page and state, with a real interceptor behind it: no console errors, no failed requests, no CSP violations,
// no horizontal scroll and no serious axe violations at phone and desktop width. Needs LIVE_FLOW=1 (see flow.spec.ts).

test.skip(!process.env.LIVE_FLOW, "set LIVE_FLOW=1 with the dashboard connected to an interceptor");

// A note is identified by its text, so each width uses its own number of trailing spaces to get a note of its own.
const NOTE = "Patient denies known drug allergies. Blood pressure is stable.";

for (const width of [390, 1280]) {
  test(`all pages and states are clean at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const problems: string[] = [];
    // axe adds inline styles of its own while it runs, which the page's strict CSP rightly blocks, so console output is
    // only recorded while the page itself is being used.
    let auditing = false;
    page.on("console", (m) => {
      if (auditing) return;
      if (m.type() === "error" || /content security policy/i.test(m.text())) problems.push(`console: ${m.text()}`);
    });
    page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
    page.on("requestfailed", (r) => {
      if (r.failure()?.errorText !== "net::ERR_ABORTED") problems.push(`request failed: ${r.url()}`); // aborted = navigated away
    });

    const check = async (label: string) => {
      await page.evaluate(async () => {
        for (let y = 0; y < document.body.scrollHeight; y += 400) {
          window.scrollTo(0, y);
          await new Promise((resolve) => setTimeout(resolve, 30));
        }
        await new Promise((resolve) => setTimeout(resolve, 800));
      });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 0) problems.push(`${label}: horizontal overflow ${overflow}px`);
      auditing = true;
      const results = await new AxeBuilder({ page }).analyze();
      auditing = false;
      for (const v of results.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) {
        problems.push(`${label}: axe ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      }
      await page.screenshot({ path: `test-results-live/${label}-${width}.png`, fullPage: true });
    };

    await page.goto("/analyze");
    const text = NOTE + " ".repeat(width === 390 ? 2 : 3);
    await page.getByLabel("Note text").fill(text);
    await page.getByRole("button", { name: /Analyse note/ }).click();
    await expect(page.getByTestId("review-ready")).toBeVisible();
    await check("analyze-report");

    await page.goto("/quarantine");
    await expect(page.getByRole("row").nth(1)).toBeVisible();
    await check("quarantine-list");

    await page.getByRole("link", { name: /Review/ }).first().click();
    await expect(page.getByText(/was found/)).toBeVisible();
    await check("note-before-text");
    await page.getByLabel("Note text").fill(text);
    await page.getByRole("button", { name: "Show highlights" }).click();
    await expect(page.locator("button[data-severity]").first()).toBeVisible();
    await check("note-with-text");

    auditing = true; // the browser logs a missing page as a console error, which is the right answer here
    const missing = await page.goto("/this-page-does-not-exist");
    auditing = false;
    expect(missing?.status()).toBe(404);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Page not found");
    await check("not-found");

    expect(problems).toEqual([]);
  });
}
