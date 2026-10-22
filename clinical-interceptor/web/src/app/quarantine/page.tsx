import { QuarantineList } from "@/components/QuarantineList";

export const metadata = { title: "Quarantine · Clinical AI Safety Interceptor" };

export default function QuarantinePage() {
  return (
    <div className="space-y-16 pb-8 pt-16 sm:pt-24">
      <header className="max-w-3xl space-y-6">
        <p className="eyebrow">Held for review</p>
        <h1 className="text-4xl font-extralight leading-tight tracking-tight sm:text-6xl">Quarantine.</h1>
        <p className="max-w-xl text-lg font-light leading-8 text-muted">
          Notes the record contradicts are held here. A clinician reviews each one, and only a documented override releases it.
        </p>
      </header>
      <QuarantineList />
    </div>
  );
}
