import {
  Alert02Icon,
  ArrowRight01Icon,
  CheckmarkCircle02Icon,
  File01Icon,
  FileSearchIcon,
  GitCompareIcon,
  LockIcon,
  SecurityBlockIcon,
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
  { quote: ["When in doubt, the answer is ", "no", "."], text: "A missing model or a broken connection never turns into a pass." },
  { quote: ["The words of the note stay with ", "the writer", "."], text: "The service keeps hashes, offsets and decisions, never the note text." },
  { quote: ["Every flag ", "shows its work", "."], text: "It names the rule, the confidence and the exact record entry it relied on." },
];

export default function Home() {
  return (
    <>
      <section className="wrap flex flex-col items-center gap-8 pb-20 pt-16 text-center sm:pt-24">
        <p className="eyebrow">Clinical AI safety</p>
        <h1 className="t-display max-w-3xl">
          Every statement, checked against <span className="hl">the record</span>.
        </h1>
        <p className="lead max-w-xl">
          A safety layer for AI-written clinical notes. It finds what the record contradicts, and holds it back before it
          reaches a patient chart.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Link href="/analyze" className="btn btn-solid">
            Analyse a note <Icon icon={ArrowRight01Icon} size={16} />
          </Link>
          <Link href="/sample" className="btn btn-line">
            See a sample review
          </Link>
        </div>
      </section>

      <section className="band-cream py-16 sm:py-20" aria-label="Product preview">
        <div className="wrap">
          <div className="mx-auto grid max-w-[1100px] gap-4 md:grid-cols-[1.4fr_1fr_1fr]">
            <figure className="panel space-y-4 p-6 sm:p-8">
              <p className="text-sm text-muted">Note</p>
              <p className="text-lg leading-9">
                <span className="rounded-sm bg-crit-tint px-1 underline decoration-crit decoration-2 underline-offset-4">
                  Patient denies known drug allergies.
                </span>{" "}
                <span className="rounded-sm px-1 underline decoration-ok decoration-1 underline-offset-4">HbA1c was 8.2% on 2026-09-02.</span>{" "}
                <span className="rounded-sm bg-warn-tint px-1 underline decoration-dotted decoration-warn decoration-2 underline-offset-4">
                  Blood pressure stable.
                </span>
              </p>
            </figure>

            <div className="panel space-y-4 p-6 sm:p-8">
              <p className="text-sm text-muted">Findings</p>
              <ul className="space-y-3 text-sm">
                <li className="flex items-center justify-between gap-2">
                  <span className="tag tag-crit">Contradicted</span>
                  <span className="tabular-nums">1</span>
                </li>
                <li className="flex items-center justify-between gap-2">
                  <span className="tag tag-warn">Unconfirmed</span>
                  <span className="tabular-nums">1</span>
                </li>
                <li className="flex items-center justify-between gap-2">
                  <span className="tag tag-ok">Supported</span>
                  <span className="tabular-nums">1</span>
                </li>
              </ul>
            </div>

            <div className="panel space-y-4 p-6 sm:p-8">
              <p className="text-sm text-muted">Status</p>
              <p className="t-h3">Held for review</p>
              <p className="tag tag-crit w-fit">BLOCKED</p>
              <p className="text-sm text-muted">A clinician answers each finding before the note is signed.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="wrap flex flex-col items-center gap-14 py-24 text-center" aria-labelledby="how-heading">
        <Reveal className="space-y-5">
          <p className="eyebrow">How it works</p>
          <h2 id="how-heading" className="t-h1 serif max-w-2xl">
            Three steps, each one <span className="hl">recorded</span>.
          </h2>
        </Reveal>
        <ol className="w-full max-w-2xl divide-y divide-line border-y border-line">
          {STEPS.map((step, index) => (
            <Reveal as="li" key={step.title} delay={index * 80} className="flex flex-col items-center gap-3 py-10">
              <Icon icon={step.icon} size={28} />
              <h3 className="t-h3">{step.title}</h3>
              <p className="max-w-md text-muted">{step.text}</p>
            </Reveal>
          ))}
        </ol>
      </section>

      <section className="band-cream py-24" aria-labelledby="outcomes-heading">
        <div className="wrap flex flex-col items-center gap-14 text-center">
          <Reveal className="space-y-5">
            <p className="eyebrow">Outcomes</p>
            <h2 id="outcomes-heading" className="t-h1 serif max-w-2xl">
              Four ways a note can <span className="hl">end</span>.
            </h2>
          </Reveal>
          <ul className="w-full max-w-2xl space-y-4">
            {OUTCOMES.map((outcome, index) => (
              <Reveal as="li" key={outcome.label} delay={index * 70} className="panel flex flex-col items-center gap-4 p-8 sm:p-10">
                <span className={outcome.tag}>
                  <Icon icon={outcome.icon} size={14} />
                  {outcome.label}
                </span>
                <p className="max-w-md text-muted">{outcome.text}</p>
              </Reveal>
            ))}
          </ul>
        </div>
      </section>

      <section className="wrap flex flex-col items-center gap-16 py-24 text-center" aria-labelledby="principles-heading">
        <Reveal className="space-y-5">
          <p className="eyebrow">Principles</p>
          <h2 id="principles-heading" className="t-h1 serif">
            Built to be <span className="hl">doubted</span>.
          </h2>
        </Reveal>
        <div className="w-full max-w-3xl space-y-16">
          {PRINCIPLES.map((item, index) => (
            <Reveal key={item.text} delay={index * 80} className="space-y-4">
              <blockquote className="serif text-[1.75rem] leading-[1.25] tracking-[-0.02em] sm:text-[2rem]">
                {item.quote[0]}
                <span className="hl">{item.quote[1]}</span>
                {item.quote[2]}
              </blockquote>
              <p className="text-sm text-muted">{item.text}</p>
            </Reveal>
          ))}
        </div>
      </section>

      <Reveal as="section" className="wrap">
        <div className="relative overflow-hidden rounded-[24px] bg-cream px-6 py-20 text-center sm:px-16">
          <div className="wash" aria-hidden="true" />
          <div className="relative flex flex-col items-center gap-6">
            <h2 className="t-h1 serif max-w-xl">Try it on a synthetic note.</h2>
            <p className="lead max-w-md">Paste a note, pick a synthetic patient, and see which statements the record supports.</p>
            <Link href="/analyze" className="btn btn-solid">
              Analyse a note <Icon icon={ArrowRight01Icon} size={16} />
            </Link>
          </div>
        </div>
      </Reveal>
    </>
  );
}
