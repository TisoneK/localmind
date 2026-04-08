#!/usr/bin/env python3
"""
Quick Diagnostic Test

Test each component individually to find where it hangs.
"""
import asyncio
import time
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

async def test_intent_classification():
    """Test intent classification speed."""
    print("Testing Intent Classification...")
    start = time.monotonic()
    
    from core import intent_router as _router
    intent, secondary = _router.classify_multi("What is 1 + 1", has_attachment=False)
    
    duration = (time.monotonic() - start) * 1000
    print(f"  Intent: {intent} ({duration:.2f}ms)")
    return intent

async def test_memory_retrieval():
    """Test memory retrieval speed."""
    print("Testing Memory Retrieval...")
    start = time.monotonic()
    
    from core.memory import MemoryComposer
    memory = MemoryComposer()
    facts = await memory.compose(
        query="What is 1 + 1",
        intent="CHAT",
        session_id="test"
    )
    
    duration = (time.monotonic() - start) * 1000
    print(f"  Memory: {len(facts)} facts ({duration:.2f}ms)")
    return facts

async def test_safety_gate():
    """Test safety gate speed."""
    print("Testing Safety Gate...")
    start = time.monotonic()
    
    from core.safety_gate import check as safety_check
    is_safe, reason = safety_check("What is 1 + 1")
    
    duration = (time.monotonic() - start) * 1000
    print(f"  Safety: {is_safe} ({duration:.2f}ms)")
    return is_safe

async def main():
    """Run all diagnostic tests."""
    print("Quick Diagnostic Test")
    print("=" * 40)
    
    # Test each component
    await test_safety_gate()
    await test_intent_classification()
    
    # Memory might hang, so add timeout
    try:
        await asyncio.wait_for(test_memory_retrieval(), timeout=30.0)
    except asyncio.TimeoutError:
        print("  Memory: TIMEOUT after 30s - THIS IS THE BOTTLENECK!")
    
    print("\nDiagnostic complete!")

if __name__ == "__main__":
    asyncio.run(main())
