import { expect, test } from "@playwright/test";

// The whole clinician journey against a real interceptor (analyse, review, quarantine, override). It needs a running
// interceptor and a web app connected to it, so it only runs when LIVE_FLOW=1. See README.md ("Live flow test").

test.skip(!process.env.LIVE_FLOW, "set LIVE_FLOW=1 with the dashboard connected to an interceptor");

// A note is identified by its text, so each spec uses its own number of trailing spaces to get a note of its own.
const NOTE = "Patient denies known drug allergies. Blood pressure is stable." + " ";

test.describe.configure({ mode: "serial" });

test("analyse a note, answer a warning, then override the critical finding from the quarantine list", async ({ page }) => {
  // 1. Analyse: a contradicted allergy (critical) and an unconfirmed statement (warning), for a patient with the allergy.
  await page.goto("/analyze");
  await page.getByLabel("Note text").fill(NOTE);
  await page.getByRole("button", { name: /Analyse note/ }).click();
  await expect(page.getByTestId("count-CRITICAL")).toHaveText("1");
  await expect(page.getByTestId("count-WARNING")).toHaveText("1");
  await expect(page.getByText("Pipeline status:")).toContainText("BLOCKED");

  // 2. The review panel lists both findings and says the note is not ready to sign.
  await expect(page.getByRole("heading", { name: "Answer each finding" })).toBeVisible();
  await expect(page.getByTestId("review-ready")).toContainText("2 waiting");

  // 3. Nothing is sent without a name and a reason.
  await page.getByRole("button", { name: "Accept as written" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /name or signature/ })).toBeVisible();

  // 4. Answer the warning.
  await page.getByLabel("Your name").fill("Dr. Example");
  await page.getByLabel("Reason", { exact: true }).fill("Checked the chart");
  await page.getByRole("button", { name: "Accept as written" }).click();
  await expect(page.getByText("Accepted", { exact: true })).toBeVisible();
  await expect(page.getByTestId("review-ready")).toContainText("1 waiting");

  // 5. A critical finding cannot be accepted, only overridden.
  await expect(page.getByRole("button", { name: "Accept as written" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Override with a reason" })).toBeVisible();

  // 6. The note is in quarantine, and its link opens the review.
  await page.goto("/quarantine");
  const row = page.getByRole("row").filter({ hasText: "PROGRESS_NOTE" }).first();
  await expect(row).toBeVisible();
  await row.getByRole("link", { name: /Review/ }).click();
  await expect(page).toHaveURL(/\/notes\/[0-9a-f-]{36}\?report=[0-9a-f-]{36}/);
  const noteId = /\/notes\/([0-9a-f-]{36})/.exec(page.url())?.[1] ?? "";
  await expect(page.getByText(/The report for this note was found \(BLOCKED\)/)).toBeVisible();

  // 7. The wrong text is refused, the exact text shows the highlights.
  await page.getByLabel("Note text").fill("some other text");
  await page.getByRole("button", { name: "Show highlights" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /does not match/ })).toBeVisible();
  await expect(page.locator("button[data-severity]")).toHaveCount(0);
  await page.getByLabel("Note text").fill(NOTE);
  await page.getByRole("button", { name: "Show highlights" }).click();
  await expect(page.locator("button[data-severity]").first()).toBeVisible();

  // 8. Override the critical finding. The note is then ready to sign and leaves the quarantine.
  await page.getByLabel("Your name").fill("Dr. Example");
  await page.getByLabel("Reason for the override").fill("Allergy was resolved");
  await page.getByRole("button", { name: "Override with a reason" }).click();
  await expect(page.getByTestId("review-ready")).toHaveText("Ready to sign");

  await page.goto("/quarantine");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Quarantine");
  await expect(page.getByRole("cell", { name: noteId.slice(0, 8) })).toHaveCount(0);
});

test("an invalid note id shows the instructions, not an error page", async ({ page }) => {
  await page.goto("/notes/not-a-uuid");
  await expect(page.getByRole("alert").filter({ hasText: "Open this page as" })).toBeVisible();
});

test("a note that is too long is refused before it is sent", async ({ page }) => {
  await page.goto("/analyze");
  const box = page.getByLabel("Note text");
  await box.fill("x".repeat(5000));
  const length = await box.evaluate((el: HTMLTextAreaElement) => el.value.length);
  expect(length).toBeLessThanOrEqual(2000);
});
