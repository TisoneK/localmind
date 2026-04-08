#!/usr/bin/env python3
"""
Test Engine Flow Step by Step

Test each step of the engine process to find where it hangs.
"""
import asyncio
import time
import logging

logging.basicConfig(level=logging.DEBUG)

async def test_engine_steps():
    """Test engine step by step."""
    print("Testing Engine Flow Step by Step")
    print("=" * 50)
    
    from core.engine import Engine
    from core import context_builder
    
    engine = Engine()
    session_id = "test_flow"
    message = "What is 1 + 1"
    
    try:
        # Step 1: Safety gate
        print("Step 1: Safety gate...")
        start = time.monotonic()
        from core.safety_gate import check as safety_check
        is_safe, reason = safety_check(message)
        step1_time = (time.monotonic() - start) * 1000
        print(f"  Safety: {is_safe} ({step1_time:.2f}ms)")
        
        # Step 2: Intent classification
        print("Step 2: Intent classification...")
        start = time.monotonic()
        from core import intent_router as _router
        intent, secondary = _router.classify_multi(message, has_attachment=False)
        step2_time = (time.monotonic() - start) * 1000
        print(f"  Intent: {intent} ({step2_time:.2f}ms)")
        
        # Step 3: History retrieval
        print("Step 3: History retrieval...")
        start = time.monotonic()
        history = engine._store.get_history(session_id)
        step3_time = (time.monotonic() - start) * 1000
        print(f"  History: {len(history)} messages ({step3_time:.2f}ms)")
        
        # Step 4: Memory retrieval (with timeout)
        print("Step 4: Memory retrieval...")
        start = time.monotonic()
        try:
            memory_facts = await asyncio.wait_for(
                engine._memory.compose(
                    query=message,
                    intent=intent,
                    session_id=session_id,
                ),
                timeout=30.0
            )
            step4_time = (time.monotonic() - start) * 1000
            print(f"  Memory: {len(memory_facts)} facts ({step4_time:.2f}ms)")
        except asyncio.TimeoutError:
            print("  Memory: TIMEOUT after 30s!")
            return
        
        # Step 5: Context building (with timeout)
        print("Step 5: Context building...")
        start = time.monotonic()
        try:
            from core.engine import EngineContext
            ctx = EngineContext(
                session_id=session_id,
                message=message,
                intent=intent,
                history=history,
                tool_result=None,
                file_attachment=None,
                memory_facts=memory_facts,
            )
            
            # This might hang - add timeout
            prompt_messages = context_builder.build(ctx, engine._adapter.context_window)
            step5_time = (time.monotonic() - start) * 1000
            print(f"  Context: {len(prompt_messages)} messages ({step5_time:.2f}ms)")
        except asyncio.TimeoutError:
            print("  Context building: TIMEOUT after 30s!")
            return
        
        # Step 6: LLM adapter initialization
        print("Step 6: LLM adapter check...")
        start = time.monotonic()
        adapter = engine._adapter
        step6_time = (time.monotonic() - start) * 1000
        print(f"  Adapter: {type(adapter).__name__} ({step6_time:.2f}ms)")
        
        print("\nAll steps completed successfully!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_engine_steps())
