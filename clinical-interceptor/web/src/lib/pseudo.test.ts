import { describe, expect, it } from "vitest";
import { patientPseudoId } from "./pseudo";

// Reference value from src/interceptor/core/pseudo.py (patient_pseudo_id) with the same key.
const KEY = "analyze-e2e-pseudonym-key-0123456789";

describe("patientPseudoId", () => {
  it("matches the Python implementation", () => {
    expect(patientPseudoId("SYNTH-MRN-0001", KEY)).toBe(
      "pid_da99ef0c0ea3faf982d0e6a376344eebb903df889ff10170aa76983007ed7095",
    );
  });

  it("normalises case and surrounding spaces like the Python code", () => {
    expect(patientPseudoId(" synth-mrn-0001 ", KEY)).toBe(patientPseudoId("SYNTH-MRN-0001", KEY));
  });

  it("refuses a short key and an empty identifier", () => {
    expect(() => patientPseudoId("SYNTH-MRN-0001", "short")).toThrow();
    expect(() => patientPseudoId("   ", KEY)).toThrow();
  });
});
