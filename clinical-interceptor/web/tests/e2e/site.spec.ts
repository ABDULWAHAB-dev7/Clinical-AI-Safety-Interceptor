import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const PAGES = [
  { path: "/", heading: /Every statement/ },
  { path: "/sample", heading: /checked line by line/ },
  { path: "/analyze", heading: /Analyse a note/ },
  { path: "/quarantine", heading: /Quarantine/ },
];

test.describe("the site", () => {
  test("the landing page leads to the sample and the analyser", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Every statement");
    await page.getByRole("link", { name: "See a sample review" }).click();
    await expect(page).toHaveURL(/\/sample$/);
    await page.getByRole("link", { name: "Analyse", exact: true }).click();
    await expect(page).toHaveURL(/\/analyze$/);
  });

  test("the main navigation marks the current page", async ({ page }) => {
    await page.goto("/quarantine");
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Quarantine" })).toHaveAttribute("aria-current", "page");
  });

  test("the quarantine page says plainly when no interceptor is connected", async ({ page }) => {
    await page.goto("/quarantine");
    await expect(page.getByRole("status")).toContainText("not connected");
  });

  test("the analyser keeps its button disabled until there is text", async ({ page }) => {
    await page.goto("/analyze");
    const button = page.getByRole("button", { name: /Analyse note/ });
    await expect(button).toBeDisabled();
    await page.getByLabel("Note text").fill("Patient denies known drug allergies.");
    await expect(button).toBeEnabled();
  });

  test("smooth scrolling is active, and is off when reduced motion is asked for", async ({ page, browser }) => {
    await page.goto("/");
    await expect(page.locator("html.lenis")).toHaveCount(1);

    const calm = await browser.newContext({ reducedMotion: "reduce" });
    const quiet = await calm.newPage();
    await quiet.goto("http://127.0.0.1:3100/");
    await expect(quiet.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(quiet.locator("html.lenis")).toHaveCount(0);
    await calm.close();
  });

  test("the sections below the fold appear when scrolled to", async ({ page }) => {
    await page.goto("/");
    const heading = page.getByRole("heading", { name: "Four ways a note can end." });
    const block = page.locator(".reveal", { has: heading });
    await expect(block).toHaveAttribute("data-state", "hidden"); // below the fold: waiting
    await heading.scrollIntoViewIfNeeded();
    await expect(block).toHaveAttribute("data-state", "shown");
    await expect(heading).toBeVisible();
  });

  for (const { path, heading } of PAGES) {
    for (const width of [390, 1280]) {
      test(`${path} at ${width}px: no horizontal scroll, no serious axe violations`, async ({ page }) => {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 })).toContainText(heading);
        // Scroll the whole page once, so every reveal has run before axe looks at it.
        await page.evaluate(async () => {
          for (let y = 0; y < document.body.scrollHeight; y += 400) {
            window.scrollTo(0, y);
            await new Promise((resolve) => setTimeout(resolve, 40));
          }
        });
        await page.screenshot({ path: `test-results/site-${path.replace(/\W/g, "_")}-${width}.png`, fullPage: true });

        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow).toBeLessThanOrEqual(0);

        const results = await new AxeBuilder({ page }).analyze();
        const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      });
    }
  }
});
