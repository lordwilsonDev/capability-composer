"""llm.intent + llm.qualify — the shared model primitive (blueprint §6).

The deterministic keyword BANT scorer is the zero-spend sandbox intelligence
for EVERY composed skill. It is a first-class registry primitive
(provider: local-model) so skills declare the SAME dependency node — the
capability graph shares it, nothing re-implements it.

Model contract (replaceable — swap the module, not the skills):
    intent(text) -> {"intent": spam|support_escalation|sales|other,
                     "confidence": float}
    qualify(contact, conversation) -> {"qualified": bool,
                                       "signals": {budget,authority,need,timeline},
                                       "rationale": str}
"""

from __future__ import annotations

import re
from typing import Any

_BUDGET = re.compile(r"(\$\s?\d|budget|price|cost|pricing|\d+\s?k\b)", re.I)
_AUTHORITY = re.compile(
    r"\b(i\b.*(approve|decide|responsible|own|am the)|(owner|founder|ceo|director|decision-maker|i make))",
    re.I,
)
_NEED = re.compile(
    r"\b(need|needs|looking for|interested in|want|wants|help with|evaluate|considering)\b", re.I
)
_TIMELINE = re.compile(
    r"\b(now|asap|today|tomorrow|this week|next week|this month|urgent|immediately)\b", re.I
)
_SPAM = re.compile(
    r"\b(buy now|free money|double your|guaranteed profit|crypto|bitcoin|viagra|lottery|earn \$)\b",
    re.I,
)
_ANGRY = re.compile(r"\b(terrible|awful|worst|furious|unacceptable|disgusting|never again)\b", re.I)


class StubModel:
    """Deterministic classifier. `intent` + `qualify` on the model contract."""

    def intent(self, text: str) -> dict[str, Any]:
        spam = bool(_SPAM.search(text))
        angry = bool(_ANGRY.search(text))
        sales = bool(_NEED.search(text)) or bool(_BUDGET.search(text))
        if spam:
            return {"intent": "spam", "confidence": 0.99}
        if angry:
            return {"intent": "support_escalation", "confidence": 0.9}
        if sales:
            return {"intent": "sales", "confidence": 0.7}
        return {"intent": "other", "confidence": 0.5}

    def qualify(self, contact: dict[str, Any], conversation: str) -> dict[str, Any]:
        signals = {
            "budget": bool(_BUDGET.search(conversation)),
            "authority": bool(_AUTHORITY.search(conversation)),
            "need": bool(_NEED.search(conversation)),
            "timeline": bool(_TIMELINE.search(conversation)),
        }
        present = sum(signals.values())
        qualified = present >= 3 and signals["need"] and signals["timeline"]
        return {
            "qualified": qualified,
            "signals": signals,
            "rationale": (
                f"{present}/4 BANT signals"
                + (" (qualified)" if qualified else " (not yet)")
            ),
        }
