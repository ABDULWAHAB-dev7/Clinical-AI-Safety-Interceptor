import { SecurityCheckIcon } from "@hugeicons/core-free-icons";
import Link from "next/link";
import { Icon } from "./Icon";

/** A sage tile with a shield-and-check mark, and a two-line wordmark. The name stays for screen readers on a phone. */
export function Logo() {
  return (
    <Link href="/" className="flex items-center gap-3 rounded-2xl">
      <span className="grid size-10 place-items-center rounded-[14px] bg-sage text-ink">
        <Icon icon={SecurityCheckIcon} size={22} />
      </span>
      <span className="sr-only leading-tight sm:not-sr-only">
        <span className="serif block text-xl font-medium tracking-[-0.03em]">Interceptor</span>
        <span className="block text-[0.6875rem] font-medium uppercase tracking-[0.14em] text-muted">Clinical AI safety</span>
      </span>
    </Link>
  );
}
