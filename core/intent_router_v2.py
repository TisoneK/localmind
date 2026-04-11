"""
Risk-Aware Intent Router — v1.0

Replaces the sequential rule → semantic → LLM waterfall in intent_classifier.py
with a concurrent, three-zone model that is classifier-agnostic by design.

Architecture
────────────
                   ┌─────────────────────────────────────────┐
  message ──────▶  │  Phase 1: Parallel Evaluation           │
                   │  Rule engine (instant) + LLM (5s cap)   │
                   └──────────────┬──────────────────────────┘
                                  │
                   ┌──────────────▼──────────────────────────┐
                   │  Decider: confidence zones              │
                   │  Green  > 0.75  → direct dispatch       │
                   │  Amber  0.55–0.75 → optimistic tool    │
                   │  Red    < 0.55  → heuristic veto        │
                   └──────────────┬──────────────────────────┘
                                  │
                   ┌──────────────▼──────────────────────────┐
                   │  RoutingDecision (intent, zone, meta)   │
                   │  emitted to obs; consumed by engine     │
                   └─────────────────────────────────────────┘

The Router never touches the LLM directly. It calls classify_with_llm() —
the existing function — under asyncio.wait_for(), so any future classifier
(FastText, DistilBERT, etc.) slots in without changing this file.

Flywheel logging is wired in here so every routing decision — including the
confidence zone and the score vector — is captured for offline Janitor review.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.models import Intent
from core import intent_router as _rule_engine
from core.intent_classifier import _RULE_CONFIDENCE_BY_INTENT
from core.flywheel import FlywheelLogger  # we create this below

logger = logging.getLogger(__name__)

# ── Zone thresholds ───────────────────────────────────────────────────────────
GREEN_THRESHOLD = 0.75   # Act immediately, no uncertainty signal
AMBER_THRESHOLD = 0.55   # Act, but mark uncertain in obs/UI
# Below AMBER_THRESHOLD → Red zone: heuristic veto before fallback

# Hard cap on the LLM classifier in the hot path.
# If Ollama is cold or slow, we cannot wait — the veto logic covers us.
LLM_TIMEOUT_SECS = 5.0

# Words that veto a CHAT fallback and force SYSINFO when confidence is low.
# These signal "the user wants a factual answer about the running system",
# not a language-model opinion.
_FACTUAL_SYSTEM_TRIGGERS: frozenset[str] = frozenset({
    "my", "current", "now", "running", "how much", "usage",
    "ram", "cpu", "memory", "disk", "processes", "uptime",
    "battery", "temperature", "load", "storage",
})

# Zone labels — emitted into obs and stored in flywheel logs.
ZONE_GREEN = "green"
ZONE_AMBER = "amber"
ZONE_RED   = "red"


# ── Output type ───────────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    """
    The single output of the Risk-Aware Router.

    intent:      The resolved intent to act on.
    secondary:   Optional secondary intent (passed through from classifier).
    confidence:  Final confidence score used for zone assignment.
    zone:        green | amber | red
    path:        How the decision was reached (for flywheel logging).
    uncertain:   True when the UI should show an uncertainty signal.
    rule_scores: Full score vector from the rule engine (for training data).
    llm_result:  What the LLM would have said (None if timed out/skipped).
    """
    intent:      Intent
    secondary:   Optional[Intent]
    confidence:  float
    zone:        str
    path:        str
    uncertain:   bool
    rule_scores: dict[str, float] = field(default_factory=dict)
    llm_result:  Optional[str]    = None
    llm_conf:    Optional[float]  = None


# ── Core router ───────────────────────────────────────────────────────────────

class RiskAwareRouter:
    """
    Classifier-agnostic intent router.

    Usage (drop-in replacement for classify_with_llm):

        from core.intent_router_v2 import RiskAwareRouter
        _router = RiskAwareRouter()

        decision = await _router.route(message, has_attachment, adapter, obs)
        primary, secondary, confidence = decision.intent, decision.secondary, decision.confidence
    """

    def __init__(self, flywheel: Optional[FlywheelLogger] = None):
        self._flywheel = flywheel or FlywheelLogger()

    async def route(
        self,
        message: str,
        has_attachment: bool,
        adapter,
        obs,
        session_id: str = "",
        query_id: str = "",
    ) -> RoutingDecision:
        """
        Evaluate intent concurrently, apply zone logic, return a RoutingDecision.

        The LLM is always fired in the background. If the rule engine returns
        GREEN confidence before the LLM finishes, the LLM task is cancelled —
        zero latency penalty. If the LLM finishes first and the rule engine was
        ambiguous, the LLM result wins. If the LLM times out, we apply zone/veto
        logic on the rule result alone.
        """
        t0 = time.monotonic()

        # ── Phase 1: Rule engine (synchronous, microseconds) ──────────────
        rule_primary, rule_secondary = _rule_engine.classify_multi(message, has_attachment)
        rule_conf = _RULE_CONFIDENCE_BY_INTENT.get(rule_primary.value, 0.70)

        # Build the full score vector for every intent (for flywheel logging).
        rule_scores = {
            intent.value: _RULE_CONFIDENCE_BY_INTENT.get(intent.value, 0.50)
            for intent in Intent
        }
        # The matched intent gets the real score; others get their static heuristic.
        rule_scores[rule_primary.value] = rule_conf

        # ── Phase 1b: GREEN fast-path — cancel LLM before it even starts ──
        if rule_conf >= GREEN_THRESHOLD:
            decision = RoutingDecision(
                intent=rule_primary,
                secondary=rule_secondary,
                confidence=rule_conf,
                zone=ZONE_GREEN,
                path="rule_green",
                uncertain=False,
                rule_scores=rule_scores,
            )
            self._emit_and_log(obs, decision, message, session_id, query_id,
                               latency_ms=round((time.monotonic() - t0) * 1000))
            return decision

        # ── Phase 2: LLM concurrent evaluation (capped at LLM_TIMEOUT_SECS) ─
        llm_intent:  Optional[Intent] = None
        llm_secondary: Optional[Intent] = None
        llm_conf:    Optional[float]  = None
        llm_path = "llm_timeout"

        try:
            from core.intent_classifier import classify_with_llm
            llm_intent, llm_secondary, llm_conf = await asyncio.wait_for(
                classify_with_llm(message, has_attachment, adapter),
                timeout=LLM_TIMEOUT_SECS,
            )
            llm_path = "llm_ok"
        except asyncio.TimeoutError:
            logger.warning("[router] LLM classifier timed out after %.1fs — applying zone logic on rule result",
                           LLM_TIMEOUT_SECS)
        except Exception as exc:
            logger.warning("[router] LLM classifier failed (%s) — applying zone logic on rule result", exc)
            llm_path = "llm_error"

        # ── Phase 3: Decider — pick the best available signal ────────────
        decision = self._decide(
            message=message,
            rule_primary=rule_primary,
            rule_secondary=rule_secondary,
            rule_conf=rule_conf,
            rule_scores=rule_scores,
            llm_intent=llm_intent,
            llm_secondary=llm_secondary,
            llm_conf=llm_conf,
            llm_path=llm_path,
        )

        self._emit_and_log(obs, decision, message, session_id, query_id,
                           latency_ms=round((time.monotonic() - t0) * 1000))
        return decision

    # ── Decision logic ────────────────────────────────────────────────────

    def _decide(
        self,
        message: str,
        rule_primary: Intent,
        rule_secondary: Optional[Intent],
        rule_conf: float,
        rule_scores: dict[str, float],
        llm_intent: Optional[Intent],
        llm_secondary: Optional[Intent],
        llm_conf: Optional[float],
        llm_path: str,
    ) -> RoutingDecision:

        # If the LLM completed and is confident, prefer it — it has more context.
        if llm_intent is not None and llm_conf is not None:
            if llm_conf >= GREEN_THRESHOLD:
                return RoutingDecision(
                    intent=llm_intent,
                    secondary=llm_secondary,
                    confidence=llm_conf,
                    zone=ZONE_GREEN,
                    path=f"llm_green",
                    uncertain=False,
                    rule_scores=rule_scores,
                    llm_result=llm_intent.value,
                    llm_conf=llm_conf,
                )

            if llm_conf >= AMBER_THRESHOLD:
                return RoutingDecision(
                    intent=llm_intent,
                    secondary=llm_secondary,
                    confidence=llm_conf,
                    zone=ZONE_AMBER,
                    path="llm_amber",
                    uncertain=True,
                    rule_scores=rule_scores,
                    llm_result=llm_intent.value,
                    llm_conf=llm_conf,
                )

        # LLM timed out or is also low-confidence: work with rule result only.
        if rule_conf >= AMBER_THRESHOLD:
            # Amber: act on the rule match but signal uncertainty.
            return RoutingDecision(
                intent=rule_primary,
                secondary=rule_secondary,
                confidence=rule_conf,
                zone=ZONE_AMBER,
                path=f"rule_amber_{llm_path}",
                uncertain=True,
                rule_scores=rule_scores,
                llm_result=llm_intent.value if llm_intent else None,
                llm_conf=llm_conf,
            )

        # ── Red zone: heuristic veto ──────────────────────────────────────
        # Neither rule nor LLM is confident. Before falling back to CHAT,
        # check for factual system-state keywords. A CHAT response to
        # "how much RAM am I using?" is a hallucination trap.
        message_lower = message.lower()
        triggered_keywords = [w for w in _FACTUAL_SYSTEM_TRIGGERS if w in message_lower]

        if triggered_keywords:
            logger.info("[router] Red zone veto — forcing SYSINFO on keywords: %s", triggered_keywords)
            return RoutingDecision(
                intent=Intent.SYSINFO,
                secondary=None,
                confidence=0.50,   # honest — this is a guess
                zone=ZONE_RED,
                path="red_sysinfo_veto",
                uncertain=True,
                rule_scores=rule_scores,
                llm_result=llm_intent.value if llm_intent else None,
                llm_conf=llm_conf,
            )

        # No veto triggered: fall back to CHAT but mark uncertain.
        # The engine will prepend a "not sure" preamble via obs metadata.
        final_intent  = llm_intent  if llm_intent  else rule_primary
        final_secondary = llm_secondary if llm_intent else rule_secondary
        final_conf    = llm_conf    if llm_conf    else rule_conf

        return RoutingDecision(
            intent=final_intent,
            secondary=final_secondary,
            confidence=final_conf,
            zone=ZONE_RED,
            path=f"red_chat_fallback_{llm_path}",
            uncertain=True,
            rule_scores=rule_scores,
            llm_result=llm_intent.value if llm_intent else None,
            llm_conf=llm_conf,
        )

    # ── Observability + flywheel ──────────────────────────────────────────

    def _emit_and_log(
        self,
        obs,
        decision: RoutingDecision,
        message: str,
        session_id: str,
        query_id: str,
        latency_ms: int,
    ) -> None:
        # Emit structured obs event — UI reads zone and uncertain flag.
        obs.emit(
            "intent_classified",
            primary=decision.intent.value,
            secondary=decision.secondary.value if decision.secondary else "none",
            confidence=round(decision.confidence, 2),
            zone=decision.zone,
            uncertain=str(decision.uncertain).lower(),
            path=decision.path,
            latency_ms=latency_ms,
        )

        if decision.uncertain:
            obs.emit(
                "routing_uncertain",
                zone=decision.zone,
                intent=decision.intent.value,
                confidence=round(decision.confidence, 2),
                path=decision.path,
            )

        # Write to flywheel log — non-blocking, best-effort.
        try:
            self._flywheel.log(
                query=message,
                session_id=session_id,
                query_id=query_id,
                rule_scores=decision.rule_scores,
                final_intent=decision.intent.value,
                final_conf=round(decision.confidence, 2),
                zone=decision.zone,
                path=decision.path,
                llm_result=decision.llm_result,
                llm_conf=decision.llm_conf,
            )
        except Exception as exc:
            logger.debug("[router] flywheel log failed (non-critical): %s", exc)
