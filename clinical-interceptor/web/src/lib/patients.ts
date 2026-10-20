import patientA from "./fixtures/patients/A.json";
import patientB from "./fixtures/patients/B.json";
import patientC from "./fixtures/patients/C.json";
import patientD from "./fixtures/patients/D.json";

// Synthetic patient contexts from benchmark/corpus/v1. Every name and identifier is invented. The MRN is only used to
// derive the pseudonym on the server. The browser sees the context letter and the description, never the MRN.
export interface PatientContext {
  id: "A" | "B" | "C" | "D";
  label: string;
  bundle: unknown;
  mrn: string;
}

function mrnOf(bundle: unknown): string {
  const entries = (bundle as { entry: { resource: { resourceType: string; identifier?: { value: string }[] } }[] }).entry;
  const patient = entries.find((e) => e.resource.resourceType === "Patient");
  const mrn = patient?.resource.identifier?.[0]?.value;
  if (!mrn) throw new Error("synthetic patient has no MRN");
  return mrn;
}

const LABELS: Record<PatientContext["id"], string> = {
  A: "Penicillin allergy and type 2 diabetes",
  B: "Type 2 diabetes, no drug allergy",
  C: "Penicillin allergy, no diabetes",
  D: "No allergy, no diabetes",
};

const BUNDLES: [PatientContext["id"], unknown][] = [
  ["A", patientA],
  ["B", patientB],
  ["C", patientC],
  ["D", patientD],
];

export const PATIENTS: PatientContext[] = BUNDLES.map(([id, bundle]) => ({
  id,
  bundle,
  label: LABELS[id],
  mrn: mrnOf(bundle),
}));

export function patientById(id: string): PatientContext | undefined {
  return PATIENTS.find((p) => p.id === id);
}
