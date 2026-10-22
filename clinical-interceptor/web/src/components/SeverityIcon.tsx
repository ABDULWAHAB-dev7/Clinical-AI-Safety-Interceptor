import { Alert02Icon, CheckmarkCircle02Icon, SecurityBlockIcon } from "@hugeicons/core-free-icons";
import type { Severity } from "@/lib/types";
import { Icon } from "./Icon";

const ICON = { CRITICAL: SecurityBlockIcon, WARNING: Alert02Icon, SAFE: CheckmarkCircle02Icon } as const;

/** The icon for a severity. Decorative: the word next to it says the same thing. */
export function SeverityIcon({ severity, size = 14 }: { severity: Severity; size?: number }) {
  return <Icon icon={ICON[severity]} size={size} />;
}

export const TAG: Record<Severity, string> = { CRITICAL: "tag tag-crit", WARNING: "tag tag-warn", SAFE: "tag tag-ok" };
