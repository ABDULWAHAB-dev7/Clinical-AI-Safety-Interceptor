"""Release policy for intercepted output (API.md 4.1.4; PLAN 7.5.1). One pure function per decision, no I/O.

Per sentence, by its highest severity:
  SAFE                                     RELEASED
  WARNING  with warning_action ANNOTATE    ANNOTATED  (released, event recorded)
  WARNING  with warning_action REDACT      REDACTED   "[REDACTED: WARNING]"
  CRITICAL with critical_action REDACT     REDACTED   "[REDACTED: CRITICAL]"
  CRITICAL with critical_action BLOCK      BLOCK      the whole response is withheld
  UNVERIFIED (could not be verified)       BLOCK      fail closed
  WITHHELD   (verification timed out)      WITHHELD   "[WITHHELD: VERIFICATION_TIMEOUT]" (streaming only)

Response status precedence: BLOCKED > REDACTED > RELEASED.
"""

RELEASED, ANNOTATED, REDACTED, WITHHELD, BLOCK = "RELEASED", "ANNOTATED", "REDACTED", "WITHHELD", "BLOCK"
WITHHELD_MARKER = "[WITHHELD: VERIFICATION_TIMEOUT]"


def sentence_action(severity, warning_action="ANNOTATE", critical_action="REDACT_SENTENCE"):
    if severity == "SAFE":
        return RELEASED
    if severity == "WARNING":
        return REDACTED if warning_action == "REDACT_SENTENCE" else ANNOTATED
    if severity == "CRITICAL":
        return BLOCK if critical_action == "BLOCK_RESPONSE" else REDACTED
    if severity == "WITHHELD":
        return WITHHELD
    return BLOCK  # UNVERIFIED, or anything unexpected: never release


def marker_for(action, severity):
    if action == REDACTED:
        return f"[REDACTED: {severity}]"
    if action == WITHHELD:
        return WITHHELD_MARKER
    return None


def response_status(actions):
    """BLOCKED if any sentence blocks, REDACTED if any sentence was replaced, else RELEASED."""
    if BLOCK in actions:
        return "BLOCKED"
    if REDACTED in actions or WITHHELD in actions:
        return "REDACTED"
    return "RELEASED"


def finish_reason(status, upstream_finish):
    if status == "BLOCKED":
        return "content_filter"
    if status == "REDACTED":
        return "stop"
    return upstream_finish or "stop"


def render(text, spans):
    """spans: (start, end, replacement or None). Replaces redacted spans; everything else is kept as is."""
    out, cursor = [], 0
    for start, end, replacement in sorted(spans):
        out.append(text[cursor:start])
        out.append(text[start:end] if replacement is None else replacement)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)
