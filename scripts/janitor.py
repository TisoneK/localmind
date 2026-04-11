#!/usr/bin/env python3
"""
Flywheel Janitor — v1.0

Offline script. Run via cron at low-traffic hours (e.g. 03:00 daily).
Reads unlabeled Amber/Red flywheel events, asks the local LLM whether the
intent resolution was correct, and writes outcome labels back to the DB.

The Janitor is the bridge between the observability pipeline and the training
data pipeline. It never runs in the hot path.

Usage
─────
    python scripts/janitor.py [--limit N] [--model MODEL] [--dry-run]

    --limit N      Process at most N events per run (default: 200).
                   Keeps each run to a predictable wall-clock budget.
    --model MODEL  Ollama model to use for review (default: settings.ollama_model).
                   For higher-quality labels, use a larger model if available.
    --dry-run      Print decisions without writing to DB.
    --export PATH  After labeling, export labeled events as JSONL for FastText training.

Output format (flywheel_events.outcome values)
──────────────────────────────────────────────
    correct             — LLM confirmed the intent was right
    wrong               — LLM says the intent was wrong; target_label set if inferrable
    wrong_tool_params   — Tool dispatch returned null/error (structural signal, already set)
    ambiguous           — LLM couldn't determine correctness (excluded from training)
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

# Allow running from repo root or scripts/ directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("janitor")

# ── LLM review prompt ─────────────────────────────────────────────────────────

_REVIEW_SYSTEM = """\
You are an intent classification auditor for a local AI assistant.

Valid intents and their meanings:
  chat        — general conversation or questions the LLM can answer from knowledge
  sysinfo     — queries about the current state of this machine (RAM, CPU, disk, time, date)
  web_search  — queries that need live / current information from the internet
  shell       — requests to run a shell command
  code_exec   — requests to execute or evaluate code
  file_task   — requests to read, parse, or summarize an existing file
  file_write  — requests to create or write a new file
  memory_op   — explicit requests to remember or recall stored facts

Your job: given a user query and the intent that was chosen, decide if the choice
was correct. Respond ONLY with a JSON object on a single line, nothing else:

{"verdict": "correct"|"wrong"|"ambiguous", "correct_intent": "intent_name_or_null", "reason": "one sentence"}

Rules:
- "correct"   — the chosen intent was the best available option
- "wrong"     — a different intent would clearly have been better
- "ambiguous" — you cannot tell without more context (use sparingly)
- correct_intent — set only when verdict is "wrong"; must be a valid intent name
- reason — max 15 words
"""

_REVIEW_USER_TMPL = """\
Query: {query}
Chosen intent: {intent}
Confidence zone: {zone} ({conf:.2f})
"""


def _review_one(
    query: str,
    intent: str,
    zone: str,
    conf: float,
    base_url: str,
    model: str,
) -> dict:
    """
    Call the Ollama API synchronously (this script is not async).
    Returns the parsed JSON verdict dict, or {"verdict": "ambiguous"} on failure.
    """
    import httpx

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user",   "content": _REVIEW_USER_TMPL.format(
                query=query, intent=intent, zone=zone, conf=conf,
            )},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"].strip()
        # Strip any accidental markdown fences.
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        verdict = data.get("verdict", "ambiguous")
        if verdict not in ("correct", "wrong", "ambiguous"):
            verdict = "ambiguous"
        return {
            "verdict": verdict,
            "correct_intent": data.get("correct_intent"),
            "reason": data.get("reason", "")[:120],
        }
    except Exception as exc:
        logger.warning("  LLM review failed: %s", exc)
        return {"verdict": "ambiguous", "correct_intent": None, "reason": ""}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_unlabeled(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Fetch unlabeled Amber/Red events, oldest first."""
    return conn.execute(
        """
        SELECT id, ts, query, final_intent, final_conf, zone, path, llm_result, llm_conf
        FROM flywheel_events
        WHERE outcome IS NULL
          AND zone IN ('amber', 'red')
        ORDER BY ts ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _write_verdict(
    conn: sqlite3.Connection,
    event_id: str,
    verdict: str,
    correct_intent: Optional[str],
) -> None:
    outcome = "correct" if verdict == "correct" else ("wrong" if verdict == "wrong" else "ambiguous")
    conn.execute(
        "UPDATE flywheel_events SET outcome = ?, target_label = ? WHERE id = ?",
        (outcome, correct_intent, event_id),
    )


# ── FastText export ───────────────────────────────────────────────────────────

def _export_training_data(conn: sqlite3.Connection, output_path: Path) -> int:
    """
    Export labeled events as FastText training format:
        __label__sysinfo what is my ram usage right now

    Only exports events with definitive outcomes (correct or wrong with target).
    Returns the number of examples exported.
    """
    rows = conn.execute(
        """
        SELECT query,
               CASE
                 WHEN outcome = 'correct' THEN final_intent
                 WHEN outcome = 'wrong' AND target_label IS NOT NULL THEN target_label
               END AS label
        FROM flywheel_events
        WHERE outcome IN ('correct', 'wrong')
          AND (outcome = 'correct' OR target_label IS NOT NULL)
        ORDER BY ts ASC
        """
    ).fetchall()

    count = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            label, query = row["label"], row["query"]
            if label and query:
                # FastText format: __label__<intent> <query text>
                line = f"__label__{label} {query.strip()}"
                fh.write(line + "\n")
                count += 1

    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="LocalMind Flywheel Janitor")
    parser.add_argument("--limit",   type=int, default=200,
                        help="Max events to process per run (default: 200)")
    parser.add_argument("--model",   default="",
                        help="Ollama model for review (default: settings.ollama_model)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print decisions without writing to DB")
    parser.add_argument("--export",  default="",
                        help="After labeling, export JSONL training data to this path")
    args = parser.parse_args()

    model     = args.model or settings.ollama_model
    base_url  = settings.ollama_base_url
    db_path   = settings.localmind_db_path

    logger.info("Janitor starting — db=%s model=%s limit=%d dry_run=%s",
                db_path, model, args.limit, args.dry_run)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    rows = _get_unlabeled(conn, args.limit)
    logger.info("Found %d unlabeled amber/red events to review", len(rows))

    stats = {"correct": 0, "wrong": 0, "ambiguous": 0, "errors": 0}
    t0 = time.monotonic()

    for i, row in enumerate(rows, 1):
        event_id = row["id"]
        query    = row["query"]
        intent   = row["final_intent"]
        zone     = row["zone"]
        conf     = float(row["final_conf"])

        logger.info("[%d/%d] reviewing %s zone=%s intent=%s conf=%.2f",
                    i, len(rows), event_id[:8], zone, intent, conf)
        logger.info("  query: %s", query[:80])

        verdict_data = _review_one(query, intent, zone, conf, base_url, model)
        verdict      = verdict_data["verdict"]
        correct_int  = verdict_data.get("correct_intent")
        reason       = verdict_data.get("reason", "")

        logger.info("  verdict: %s  correct_intent: %s  reason: %s",
                    verdict, correct_int or "—", reason)

        stats[verdict] = stats.get(verdict, 0) + 1

        if not args.dry_run:
            _write_verdict(conn, event_id, verdict, correct_int)
            if i % 20 == 0:
                conn.commit()   # batch commits for efficiency

    if not args.dry_run:
        conn.commit()

    elapsed = round(time.monotonic() - t0, 1)
    logger.info(
        "Done in %.1fs — correct=%d wrong=%d ambiguous=%d",
        elapsed, stats["correct"], stats["wrong"], stats["ambiguous"],
    )

    if args.export:
        export_path = Path(args.export)
        n = _export_training_data(conn, export_path)
        logger.info("Exported %d training examples → %s", n, export_path)
        logger.info("Train FastText with:")
        logger.info("  fasttext supervised -input %s -output model/intent_ft "
                    "-epoch 25 -wordNgrams 2 -dim 50", export_path)

    conn.close()


if __name__ == "__main__":
    main()
