#!/usr/bin/env python3
"""
Test with debug logs enabled
"""
import asyncio
import logging
import time

# Enable debug logging to see the engine logs
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(name)s - %(message)s')

async def test():
    print("Testing with debug logs...")
    
    from core.engine import Engine
    engine = Engine()
    
    start = time.monotonic()
    
    try:
        response_chunks = []
        async for chunk in engine.process(
            message="What is 1 + 1",
            session_id="test_logs"
        ):
            response_chunks.append(chunk.text)
            if chunk.done:
                break
        
        end = time.monotonic()
        total_time = (end - start) * 1000
        response = ''.join(response_chunks)
        
        print(f"\nResults:")
        print(f"  Time: {total_time:.0f}ms")
        print(f"  Response: '{response}'")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
