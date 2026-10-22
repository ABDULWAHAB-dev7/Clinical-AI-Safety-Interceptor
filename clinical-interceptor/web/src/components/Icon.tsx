import { HugeiconsIcon } from "@hugeicons/react";
import type { IconSvgElement } from "@hugeicons/react";

/** One place that sets how icons look: thin 1.5 stroke, decorative by default (the text next to it carries the meaning). */
export function Icon({ icon, size = 18, label }: { icon: IconSvgElement; size?: number; label?: string }) {
  return label ? (
    <HugeiconsIcon icon={icon} size={size} strokeWidth={1.5} role="img" aria-label={label} />
  ) : (
    <HugeiconsIcon icon={icon} size={size} strokeWidth={1.5} aria-hidden="true" />
  );
}
