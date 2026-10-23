import { QuarantineList } from "@/components/QuarantineList";

export const metadata = { title: "Quarantine · Clinical AI Safety Interceptor" };

export default function QuarantinePage() {
  return (
    <div className="wrap space-y-16 pt-12 sm:pt-20">
      <header className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
        <p className="eyebrow">Held for review</p>
        <h1 className="t-display">
          <span className="hl">Quarantine</span>.
        </h1>
        <p className="lead max-w-xl">
          Notes the record contradicts are held here. A clinician reviews each one, and only a documented override releases it.
        </p>
      </header>
      <QuarantineList />
    </div>
  );
}
