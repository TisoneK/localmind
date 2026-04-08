#!/usr/bin/env python3
"""
Test Engine Directly

Test the engine.process() method directly without HTTP.
"""
import asyncio
import time
from core.engine import Engine

async def test_engine_direct():
    """Test engine directly."""
    print("Testing Engine Directly")
    print("=" * 40)
    
    engine = Engine()
    session_id = "test_direct"
    
    # Test the math query
    message = "What is 1 + 1"
    
    print(f"Sending: '{message}'")
    start_time = time.monotonic()
    
    try:
        response_chunks = []
        async for chunk in engine.process(
            message=message,
            session_id=session_id
        ):
            response_chunks.append(chunk.text)
            print(f"Chunk: '{chunk.text}' (done: {chunk.done})")
            
            if chunk.done:
                break
        
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        full_response = ''.join(response_chunks)
        
        print(f"\nResults:")
        print(f"  Total time: {total_time:.0f}ms")
        print(f"  Response: '{full_response}'")
        print(f"  Length: {len(full_response)} chars")
        
        if total_time < 1000:
            print(f"  Status: FAST - Math fast-path working! ")
        else:
            print(f"  Status: SLOW - Going through agent loop")
            
    except Exception as e:
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        print(f"Error after {total_time:.0f}ms: {e}")

if __name__ == "__main__":
    asyncio.run(test_engine_direct())
