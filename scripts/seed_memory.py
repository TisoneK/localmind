#!/usr/bin/env python3
"""
scripts/seed_memory.py — Pre-populate LocalMind's vector memory with capability facts.

Run this once after setup (or after pulling new models) so the model has
instant access to its own capabilities via passive memory retrieval.

Usage:
    cd /path/to/localmind
    python scripts/seed_memory.py

    # Dry run (print facts without storing):
    python scripts/seed_memory.py --dry-run

    # Clear existing seeded facts first:
    python scripts/seed_memory.py --reset

    # Skip confirmation prompt:
    python scripts/seed_memory.py --yes
"""
from __future__ import annotations
import argparse
import asyncio
import sys
import os
from pathlib import Path

# Make sure we can import from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("LOCALMIND_LOG_LEVEL", "WARNING")  # quiet during seeding

SEED_SESSION_ID = "__system_seed__"

# ── Facts to seed ─────────────────────────────────────────────────────────────
#
# These are injected into every relevant conversation turn via passive retrieval.
# Keep each fact as a single, self-contained, unambiguous sentence.
# Importance 0.9 = high priority in retrieval scoring.
#
SEED_FACTS: list[tuple[str, float]] = [

    # ── Identity ───────────────────────────────────────────────────────────
    ("LocalMind is an AI assistant that runs entirely on the user's local machine using Ollama.", 0.9),
    ("LocalMind never sends data to the cloud — all processing happens locally.", 0.9),
    ("LocalMind version is v0.4-dev.", 0.7),

    # ── Web search ─────────────────────────────────────────────────────────
    ("LocalMind can search the web for current information using its web_search tool.", 0.9),
    ("The web_search tool uses DuckDuckGo first, then SearXNG as a fallback, then Brave Search if an API key is configured.", 0.8),
    ("Web search returns up to 5 results and is triggered by words like 'latest', 'current', 'today', 'news', 'price', 'search for'.", 0.8),
    ("Web search does NOT fire for general knowledge questions — only for queries needing live or recent data.", 0.8),

    # ── File reading ───────────────────────────────────────────────────────
    ("LocalMind can read and analyse files uploaded by the user using its file_task tool.", 0.9),
    ("Supported file formats include PDF, DOCX, TXT, MD, CSV, XLSX, JSON, YAML, and most code files (py, js, ts, sh, rs, go).", 0.9),
    ("LocalMind can OCR images (PNG, JPG, GIF, WEBP) if pytesseract is installed.", 0.7),
    ("The maximum file size LocalMind accepts is 50 MB.", 0.7),
    ("Large files are automatically chunked into 1500-token segments with 200-token overlap.", 0.7),

    # ── File writing ───────────────────────────────────────────────────────
    ("LocalMind can write files to disk using its file_write tool.", 0.9),
    ("Files are saved to ~/LocalMind/ by default, or to ~/Downloads, ~/Documents, ~/Desktop, ~/Pictures, ~/Music, or ~/Videos if requested.", 0.8),
    ("LocalMind asks for confirmation before writing any file because LOCALMIND_REQUIRE_WRITE_PERMISSION is enabled.", 0.8),
    ("LocalMind extracts code from fenced blocks automatically when writing code files.", 0.7),

    # ── Code execution ─────────────────────────────────────────────────────
    ("LocalMind can execute Python code using its code_exec tool and return real stdout/stderr output.", 0.9),
    ("Python code must be wrapped in a ```python fenced block to be executed — plain text is not run.", 0.9),
    ("Code execution has a 30-second timeout and captures up to 4000 characters of output.", 0.8),
    ("The code execution environment has full access to the LocalMind Python environment and all installed packages.", 0.7),

    # ── Shell ──────────────────────────────────────────────────────────────
    ("LocalMind can run shell commands using its shell tool — listing files, checking disk space, opening apps, running git or pip.", 0.9),
    ("The shell tool is enabled and has a 20-second timeout per command.", 0.8),
    ("Shell commands can access standard user folders: Documents, Downloads, Desktop, Pictures, Music, Videos.", 0.8),

    # ── System info ────────────────────────────────────────────────────────
    ("LocalMind knows the current time and date without searching the web — it uses its sysinfo tool which runs offline in under 100ms.", 0.9),
    ("LocalMind can report CPU, RAM, disk space, OS version, hostname, and Python version using its sysinfo tool.", 0.9),
    ("The sysinfo tool is instant and offline — it never guesses time or date.", 0.9),

    # ── Memory ────────────────────────────────────────────────────────────
    ("LocalMind has persistent memory — it remembers facts across conversations using a semantic vector store.", 0.9),
    ("To store a fact, say 'remember that ...' or 'note that ...' and LocalMind will save it permanently.", 0.9),
    ("To recall stored facts, ask 'what do you know about me' or 'list your memory'.", 0.8),
    ("To delete a fact, say 'forget ...' followed by the fact to remove.", 0.8),
    ("Relevant memory facts are automatically injected into every conversation turn based on semantic similarity.", 0.8),
    ("Memory uses nomic-embed-text embeddings via Ollama and stores vectors in a local SQLite database.", 0.7),

    # ── Models ────────────────────────────────────────────────────────────
    ("The main language model is phi3:mini, which has a 4096-token context window.", 0.8),
    ("The code model is llama3.1:8b, used for code execution, shell tasks, and file writing.", 0.8),
    ("phi3:mini is configured to stay loaded in memory permanently (keep_alive=-1) to avoid cold-start delays.", 0.8),
    ("All models run locally via Ollama at http://localhost:11434 — no internet connection is needed for inference.", 0.9),

    # ── Intent routing ────────────────────────────────────────────────────
    ("LocalMind classifies each message into one of these intents: chat, web_search, file_task, file_write, code_exec, shell, sysinfo, memory_op.", 0.8),
    ("Simple chat messages are handled directly without any tool call — only queries needing real data use tools.", 0.7),
    ("Intent classification uses fast rule-based patterns first, then local embeddings, then an LLM call only if needed.", 0.7),

    # ── Limitations ───────────────────────────────────────────────────────
    ("phi3:mini's 4096-token context window means very long conversations will have older history trimmed automatically.", 0.8),
    ("LocalMind's code execution is not sandboxed — it runs with full user permissions.", 0.7),
    ("The semantic classifier requires all-MiniLM-L6-v2 to be downloaded locally to activate.", 0.6),
    ("Memory vector search requires nomic-embed-text to be pulled in Ollama: run 'ollama pull nomic-embed-text'.", 0.8),
]


async def seed(dry_run: bool = False, reset: bool = False, db_path: str | None = None) -> None:
    from storage.vector import VectorStore, _EXECUTOR
    from core.config import settings

    resolved_path = db_path or settings.localmind_db_path
    store = VectorStore(resolved_path)

    if not store._ready and not dry_run:
        if store._dim_mismatch:
            stored_dim, actual_dim = store._dim_mismatch
            print(f"Embedding dimension mismatch detected:")
            print(f"   DB has dim={stored_dim}, but {settings.ollama_embed_model} returns dim={actual_dim}.")
            print()
            print("   Fix it first, then re-run seed:")
            print("     python scripts/reset_vector_db.py --yes")
            print("     python scripts/seed_memory.py --yes")
        else:
            print("VectorStore not ready -- is localmind.db initialised? Run LocalMind once first.")
            print("   (The DB is created on first startup -- seed after that.)")
        sys.exit(1)

    if reset and not dry_run:
        print("Clearing existing seeded facts...")
        all_facts = await store.list_all_with_metadata()
        seeded = [f for f in all_facts if f.get("source") == "seed"]
        for fact in seeded:
            await store.forget(fact["fact"])
        print(f"  Cleared {len(seeded)} previously seeded facts.")

    print(f"\n{'DRY RUN -- ' if dry_run else ''}Seeding {len(SEED_FACTS)} facts into LocalMind memory...\n")

    if dry_run:
        for i, (fact, _) in enumerate(SEED_FACTS, 1):
            print(f"  [{i:02d}/{len(SEED_FACTS)}] {fact[:80]}{'...' if len(fact) > 80 else ''}")
        print()
        print(f"DRY RUN complete -- {len(SEED_FACTS)} facts would be stored (nothing written).")
        return

    for i, (fact, _) in enumerate(SEED_FACTS, 1):
        print(f"  [{i:02d}/{len(SEED_FACTS)}] {fact[:80]}{'...' if len(fact) > 80 else ''}")
    print()
    print("Embedding and writing (this may take 30-60 s)...")

    # Use store_batch: one embed-pass + one DB transaction instead of N individual
    # store() calls each fighting for their own write lock.  This avoids
    # "database is locked" errors when the server is running concurrently.
    facts_only = [fact for fact, _ in SEED_FACTS]
    stored = await store.store_batch(
        facts=facts_only,
        session_id=SEED_SESSION_ID,
        source="seed",
        extra_metadata={"importance": "0.8", "memory_type": "capability"},
    )

    # Flush any pending embed_cache writes before the event loop closes
    store.flush_write_buf()

    # Shut down the shared executor cleanly so asyncio.run() does not get a
    # CancelledError on Python 3.12 when the loop tears down mid-future.
    _EXECUTOR.shutdown(wait=True, cancel_futures=False)

    skipped = len(SEED_FACTS) - stored
    total = await store.count()
    print(f"Done. Stored {stored} new facts ({skipped} skipped as duplicates).")
    print(f"Total facts in memory: {total}")
    print()
    print("LocalMind will now retrieve relevant capability facts on every turn.")
    print("To see stored facts, ask: 'what do you know about me'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed LocalMind memory with capability facts.")
    parser.add_argument("--dry-run", action="store_true", help="Print facts without storing them.")
    parser.add_argument("--reset", action="store_true", help="Clear previously seeded facts before inserting.")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to localmind.db (default: LOCALMIND_DB_PATH from .env or ~/LocalMind/localmind.db)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        print(f"This will store {len(SEED_FACTS)} capability facts into LocalMind's vector memory.")
        if args.reset:
            print("Existing seeded facts will be cleared first.")
        confirm = input("Continue? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    asyncio.run(seed(dry_run=args.dry_run, reset=args.reset, db_path=args.db))


if __name__ == "__main__":
    main()
