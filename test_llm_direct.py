#!/usr/bin/env python3
"""
Test LLM Directly

Test the LLM adapter directly to see if the model itself is slow.
"""
import asyncio
import time
from adapters.ollama import OllamaAdapter

async def test_llm_direct():
    """Test LLM adapter directly."""
    print("Testing LLM Directly")
    print("=" * 40)
    
    adapter = OllamaAdapter()
    
    # Simple prompt
    messages = [
        {"role": "user", "content": "What is 1 + 1? Answer with just the number."}
    ]
    
    print(f"Sending: {messages}")
    start_time = time.monotonic()
    
    try:
        response_chunks = []
        async for chunk in adapter.chat(messages):
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
        
        if total_time < 5000:
            print(f"  Status: FAST - LLM is responsive")
        elif total_time < 15000:
            print(f"  Status: OK - LLM is working")
        else:
            print(f"  Status: SLOW - LLM itself is slow")
            
    except Exception as e:
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        print(f"Error after {total_time:.0f}ms: {e}")

async def test_llm_simple():
    """Test with even simpler prompt."""
    print("\n" + "=" * 40)
    print("Testing LLM with Simple Prompt")
    print("=" * 40)
    
    adapter = OllamaAdapter()
    
    # Very simple prompt
    messages = [
        {"role": "user", "content": "2"}
    ]
    
    print(f"Sending: {messages}")
    start_time = time.monotonic()
    
    try:
        response_chunks = []
        async for chunk in adapter.chat(messages):
            response_chunks.append(chunk.text)
            if chunk.done:
                break
        
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        full_response = ''.join(response_chunks)
        
        print(f"Results:")
        print(f"  Time: {total_time:.0f}ms")
        print(f"  Response: '{full_response}'")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_llm_direct())
    asyncio.run(test_llm_simple())
