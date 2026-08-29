"""
Request pacing for the live eval harnesses.

PACE THE HARNESS OR THE NUMBER IS THE QUOTA.

This account allows 10 cross-region requests per minute for either Claude
model and 25 for Nova Pro. A harness that fires its case list as fast as it
can hits that wall part-way through, and the TAIL of the list comes back as
`INTERNAL_ERROR` -- which reads as "the model failed those cases" and is
really "the account stopped answering".

That is not hypothetical. Three consecutive meal-plan bands scored Claude
Haiku 4.5 at 82-91% with every rep contaminated, while Nova Pro scored 100%
clean on the same suite. Paced, Haiku scores 100% too. The gap was the request
budget, not the model.

Shared rather than copied into each harness. The guardrail suite is 20 cases
against the same 10/min ceiling and needs identical treatment, and a pacing
rule that exists twice is one that can be tuned in one place and left stale in
the other -- the same failure `LITERAL_MONEY` was consolidated to avoid. It
also matters more here than it looks: on the guardrail suite an unpaced tail
does not merely lower a score, it makes the Guardrail appear to have let unsafe
content through.
"""

from __future__ import annotations

import time

# 9/min rather than 10 leaves room for the retry the Bedrock client makes
# internally, which also counts against the limit.
DEFAULT_MAX_RPM = 9


def pace_bedrock_calls(max_rpm: int = DEFAULT_MAX_RPM) -> None:
    """
    Rate-limit every `BedrockModelClient._converse` call process-wide.

    Wraps the method rather than threading a delay through each harness,
    because the call sites are inside the graph: a turn makes several model
    calls (classify, plan, up to two repairs, prose) and only the client sees
    all of them.

    `max_rpm=0` disables pacing, for a model with confirmed headroom.
    """
    if max_rpm <= 0:
        return

    import src.models.bedrock as bedrock_mod

    interval = 60.0 / max_rpm
    # -inf, not 0.0: the first call has nothing to wait for, and starting at
    # zero made it sleep a full interval before the run even began.
    last = [float("-inf")]
    original = bedrock_mod.BedrockModelClient._converse

    def paced(self, **kwargs):
        wait = interval - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        return original(self, **kwargs)

    bedrock_mod.BedrockModelClient._converse = paced
