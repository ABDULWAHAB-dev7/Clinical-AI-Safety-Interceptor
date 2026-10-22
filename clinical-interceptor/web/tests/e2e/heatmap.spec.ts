import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.describe("case-0001 heatmap", () => {
  test("shows the three flagged statements and the two supported ones with text labels", async ({ page }) => {
    await page.goto("/sample");

    const critical = page.locator('button[data-severity="CRITICAL"]');
    const warning = page.locator('button[data-severity="WARNING"]');
    const supported = page.locator('button[data-severity="SAFE"]');
    await expect(critical).toHaveCount(2);
    await expect(warning).toHaveCount(1);
    await expect(supported).toHaveCount(2);

    await expect(critical.first()).toContainText("Patient denies known drug allergies");
    await expect(critical.first()).toContainText("Critical");
    await expect(warning.first()).toContainText("Blood pressure stable");
    await expect(warning.first()).toContainText("Warning");
    await expect(supported.first()).toContainText("Supported");

    await expect(page.getByTestId("count-CRITICAL")).toHaveText("2");
    await expect(page.getByTestId("count-WARNING")).toHaveText("1");
    await expect(page.getByTestId("count-SAFE")).toHaveText("2");
    await expect(page.getByText("Pipeline status:")).toContainText("BLOCKED");
  });

  test("keyboard focus reveals the evidence, in document order", async ({ page }) => {
    await page.goto("/sample");
    const buttons = page.locator("button[data-severity]");
    await buttons.first().focus();
    await expect(page.getByRole("tooltip")).toBeVisible();
    await expect(page.getByRole("tooltip")).toContainText("SOURCE_SUPPORTED");

    await page.keyboard.press("Tab");
    await expect(page.getByRole("tooltip")).toContainText("SOURCE_CONTRADICTED");
    await expect(page.getByRole("tooltip")).toContainText("AllergyIntolerance/allergy-penicillin-001");

    const order = await buttons.evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-severity")));
    expect(order).toEqual(["SAFE", "CRITICAL", "SAFE", "CRITICAL", "WARNING"]);
  });

  test("sends a strict CSP, stores nothing in the browser, and shows the patient id as 8 characters", async ({ page }) => {
    const response = await page.goto("/sample");
    const csp = response?.headers()["content-security-policy"] ?? "";
    expect(csp).toContain("script-src 'self' 'nonce-");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).toContain("frame-ancestors 'none'");

    const violations: string[] = [];
    page.on("console", (message) => {
      if (/content security policy/i.test(message.text())) violations.push(message.text());
    });
    await page.reload();
    await page.locator("button[data-severity]").first().click();
    expect(violations).toEqual([]);

    const storage = await page.evaluate(() => localStorage.length + sessionStorage.length);
    expect(storage).toBe(0);
    await expect(page.getByText(/Patient [0-9a-f]{8} ·/)).toBeVisible();
  });

  for (const width of [390, 1280]) {
    test(`renders at ${width}px without horizontal page scroll and with no serious axe violations`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.goto("/sample");
      await expect(page.locator("button[data-severity]")).toHaveCount(5);
      // Sections fade in as they are scrolled to, so scroll through the page once before checking colors.
      await page.evaluate(async () => {
        for (let y = 0; y < document.body.scrollHeight; y += 400) {
          window.scrollTo(0, y);
          await new Promise((resolve) => setTimeout(resolve, 40));
        }
        await new Promise((resolve) => setTimeout(resolve, 800));
      });
      await page.locator('button[data-severity="CRITICAL"]').first().focus();
      await page.screenshot({ path: `test-results/heatmap-${width}.png`, fullPage: true });

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);

      const results = await new AxeBuilder({ page }).analyze();
      const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    });
  }
});
