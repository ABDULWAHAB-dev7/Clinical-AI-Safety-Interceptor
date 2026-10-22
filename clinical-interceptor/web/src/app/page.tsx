import {
  Alert02Icon,
  ArrowRight01Icon,
  CheckmarkCircle02Icon,
  DatabaseIcon,
  File01Icon,
  FileSearchIcon,
  GitCompareIcon,
  LockIcon,
  SecurityBlockIcon,
  Shield01Icon,
} from "@hugeicons/core-free-icons";
import Link from "next/link";
import { Icon } from "@/components/Icon";
import { Reveal } from "@/components/Reveal";

const STEPS = [
  { icon: File01Icon, title: "Extract", text: "The note is split into single statements. Each one keeps its exact position in the text." },
  { icon: GitCompareIcon, title: "Verify", text: "Every statement is compared with the patient record: supported, contradicted, or not found." },
  { icon: FileSearchIcon, title: "Audit", text: "The result, the rule behind it and the evidence are written to an append-only record." },
];

const OUTCOMES = [
  { tag: "tag tag-ok", label: "Clear", icon: CheckmarkCircle02Icon, text: "Every statement is supported. The note passes through untouched." },
  { tag: "tag tag-warn", label: "Review", icon: Alert02Icon, text: "Something cannot be confirmed. A clinician accepts, edits or rejects it before signing." },
  { tag: "tag tag-crit", label: "Held", icon: SecurityBlockIcon, text: "The record contradicts the note. It is quarantined, the safety team is told, and only a documented override releases it." },
  { tag: "tag tag-neutral", label: "Unavailable", icon: LockIcon, text: "If verification cannot run, nothing is released. The system fails closed." },
];

const PRINCIPLES = [
  { icon: LockIcon, title: "Fails closed", text: "When in doubt, the answer is no. A missing model or a broken connection never turns into a pass." },
  { icon: DatabaseIcon, title: "Keeps no note text", text: "The service stores hashes, offsets and decisions. The words of the note stay with the person who wrote them." },
  { icon: Shield01Icon, title: "Shows its work", text: "Each flag names the rule, the confidence and the exact record entry it relied on." },
];

export default function Home() {
  return (
    <div className="space-y-40 pb-8">
      <section className="grid gap-16 pt-20 sm:pt-28 lg:grid-cols-[1.15fr_1fr] lg:items-end">
        <div className="space-y-10">
          <p className="eyebrow">Clinical AI safety</p>
          <h1 className="text-5xl font-extralight leading-[1.05] tracking-tight sm:text-6xl">
            Every statement,
            <br />
            checked against
            <br />
            the record.
          </h1>
          <p className="max-w-md text-lg font-light leading-8 text-muted">
            A safety layer for AI-written clinical notes. It finds what the record contradicts, and holds it back before it
            reaches a patient chart.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/analyze" className="btn btn-solid">
              Analyse a note <Icon icon={ArrowRight01Icon} size={16} />
            </Link>
            <Link href="/sample" className="btn btn-line">
              See a sample review
            </Link>
          </div>
        </div>

        <figure className="panel space-y-5 p-7 sm:p-9" aria-label="Example of a reviewed note">
          <p className="eyebrow">Specimen</p>
          <p className="text-xl font-light leading-10">
            <span className="rounded-sm bg-crit-tint px-1 underline decoration-crit decoration-2 underline-offset-4">
              Patient denies known drug allergies.
            </span>{" "}
            <span className="rounded-sm px-1 underline decoration-ok decoration-1 underline-offset-4">HbA1c was 8.2% on 2026-09-02.</span>{" "}
            <span className="rounded-sm bg-warn-tint px-1 underline decoration-dotted decoration-warn decoration-2 underline-offset-4">
              Blood pressure stable.
            </span>
          </p>
          <figcaption className="flex flex-wrap gap-2 border-t border-line pt-5">
            <span className="tag tag-crit">Contradicted</span>
            <span className="tag tag-ok">Supported</span>
            <span className="tag tag-warn">Unconfirmed</span>
          </figcaption>
        </figure>
      </section>

      <section className="space-y-14" aria-labelledby="how-heading">
        <Reveal className="max-w-xl space-y-4">
          <p className="eyebrow">How it works</p>
          <h2 id="how-heading" className="text-3xl font-extralight tracking-tight sm:text-5xl">
            Three steps, each one recorded.
          </h2>
        </Reveal>
        <ol className="grid gap-px overflow-hidden rounded-2xl border border-line bg-line md:grid-cols-3">
          {STEPS.map((step, index) => (
            <Reveal as="li" key={step.title} delay={index * 90} className="space-y-6 bg-surface p-8">
              <span className="flex items-center justify-between text-muted">
                <Icon icon={step.icon} size={26} />
                <span className="font-mono text-xs">0{index + 1}</span>
              </span>
              <h3 className="text-xl font-normal">{step.title}</h3>
              <p className="text-sm leading-6 text-muted">{step.text}</p>
            </Reveal>
          ))}
        </ol>
      </section>

      <section className="grid gap-14 lg:grid-cols-[1fr_1.4fr]" aria-labelledby="outcomes-heading">
        <Reveal className="space-y-4">
          <p className="eyebrow">Outcomes</p>
          <h2 id="outcomes-heading" className="text-3xl font-extralight tracking-tight sm:text-5xl">
            Four ways a note can end.
          </h2>
        </Reveal>
        <ul className="divide-y divide-line border-y border-line">
          {OUTCOMES.map((outcome, index) => (
            <Reveal as="li" key={outcome.label} delay={index * 70} className="grid gap-3 py-6 sm:grid-cols-[9rem_1fr] sm:items-start">
              <span className={`${outcome.tag} w-fit`}>
                <Icon icon={outcome.icon} size={14} />
                {outcome.label}
              </span>
              <p className="text-sm leading-6 text-muted">{outcome.text}</p>
            </Reveal>
          ))}
        </ul>
      </section>

      <section className="space-y-14" aria-labelledby="principles-heading">
        <Reveal className="max-w-xl space-y-4">
          <p className="eyebrow">Principles</p>
          <h2 id="principles-heading" className="text-3xl font-extralight tracking-tight sm:text-5xl">
            Built to be doubted.
          </h2>
        </Reveal>
        <div className="grid gap-10 md:grid-cols-3">
          {PRINCIPLES.map((item, index) => (
            <Reveal key={item.title} delay={index * 90} className="space-y-4 border-t border-ink pt-6">
              <Icon icon={item.icon} size={24} />
              <h3 className="text-lg font-normal">{item.title}</h3>
              <p className="text-sm leading-6 text-muted">{item.text}</p>
            </Reveal>
          ))}
        </div>
      </section>

      <Reveal as="section" className="flex flex-col items-start justify-between gap-8 rounded-3xl bg-ink p-10 text-paper sm:flex-row sm:items-center sm:p-14">
        <div className="space-y-3">
          <h2 className="text-3xl font-extralight tracking-tight sm:text-4xl">Try it on a synthetic note.</h2>
          <p className="max-w-md text-sm leading-6 text-[#cfcfca]">
            Paste a note, pick a synthetic patient, and see which statements the record supports.
          </p>
        </div>
        <Link href="/analyze" className="btn !border-paper bg-paper text-ink hover:bg-white">
          Analyse a note <Icon icon={ArrowRight01Icon} size={16} />
        </Link>
      </Reveal>
    </div>
  );
}
