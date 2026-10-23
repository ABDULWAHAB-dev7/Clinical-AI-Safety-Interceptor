import { PageHeader } from "@/components/PageHeader";
import { QuarantineList } from "@/components/QuarantineList";

export const metadata = { title: "Quarantine · Clinical AI Safety Interceptor" };

export default function QuarantinePage() {
  return (
    <div className="wrap space-y-16 pt-12 sm:pt-20">
      <PageHeader eyebrow="Held for review" title={<><span className="hl">Quarantine</span>.</>}>
        <p className="lead max-w-xl">
          Notes the record contradicts are held here. A clinician reviews each one, and only a documented override releases it.
        </p>
      </PageHeader>
      <QuarantineList />
    </div>
  );
}
