import { DotField } from "./Visuals";

/** The centered heading block of an inner page, over a soft dot field and a blue wash. */
export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="relative mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
      <DotField className="absolute -inset-x-24 -inset-y-12 -z-10 opacity-70" />
      <p className="eyebrow">{eyebrow}</p>
      <h1 className="t-display">{title}</h1>
      {children}
    </header>
  );
}
