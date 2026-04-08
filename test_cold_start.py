#!/usr/bin/env python3
"""
Cold Start Performance Test

Tests LocalMind performance specifically for cold-start scenarios.
This helps identify if ChromaDB initialization is still causing delays.
"""
import asyncio
import time
import json
import aiohttp
import subprocess
import sys

async def test_cold_start():
    """Test performance when server is freshly started."""
    print("🧊 Testing Cold Start Performance")
    print("=" * 50)
    
    # Test queries
    queries = ["1 + 1", "Hello", "What time is it?"]
    
    for i, query in enumerate(queries, 1):
        print(f"\n📍 Test {i}: '{query}'")
        
        start_time = time.monotonic()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:8000/api/chat",
                    json={
                        "message": query,
                        "session_id": f"cold_start_test_{i}_{int(time.time())}"
                    }
                ) as response:
                    
                    chunks = []
                    first_chunk_time = None
                    
                    async for line in response.content:
                        if first_chunk_time is None:
                            first_chunk_time = time.monotonic()
                            time_to_first = (first_chunk_time - start_time) * 1000
                            print(f"  ⚡ First response: {time_to_first:.0f}ms")
                        
                        try:
                            line_str = line.decode('utf-8').strip()
                            if line_str.startswith('data: '):
                                data = json.loads(line_str[6:])
                                chunks.append(data.get('text', ''))
                                if data.get('done', False):
                                    break
                        except:
                            continue
                    
                    end_time = time.monotonic()
                    total_time = (end_time - start_time) * 1000
                    
                    print(f"  ✅ Completed: {total_time:.0f}ms")
                    print(f"  📝 Response: {''.join(chunks)[:100]}...")
                    
        except Exception as e:
            end_time = time.monotonic()
            total_time = (end_time - start_time) * 1000
            print(f"  ❌ Error after {total_time:.0f}ms: {e}")

async def test_warm_performance():
    """Test performance after server is warm."""
    print("\n🔥 Testing Warm Performance")
    print("=" * 50)
    
    query = "1 + 1"
    
    for i in range(3):
        print(f"\n📍 Warm test {i + 1}: '{query}'")
        
        start_time = time.monotonic()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:8000/api/chat",
                    json={
                        "message": query,
                        "session_id": f"warm_test_{i}_{int(time.time())}"
                    }
                ) as response:
                    
                    chunks = []
                    first_chunk_time = None
                    
                    async for line in response.content:
                        if first_chunk_time is None:
                            first_chunk_time = time.monotonic()
                            time_to_first = (first_chunk_time - start_time) * 1000
                            print(f"  ⚡ First response: {time_to_first:.0f}ms")
                        
                        try:
                            line_str = line.decode('utf-8').strip()
                            if line_str.startswith('data: '):
                                data = json.loads(line_str[6:])
                                chunks.append(data.get('text', ''))
                                if data.get('done', False):
                                    break
                        except:
                            continue
                    
                    end_time = time.monotonic()
                    total_time = (end_time - start_time) * 1000
                    
                    print(f"  ✅ Completed: {total_time:.0f}ms")
                    print(f"  📝 Response: {''.join(chunks)}")
                    
        except Exception as e:
            end_time = time.monotonic()
            total_time = (end_time - start_time) * 1000
            print(f"  ❌ Error after {total_time:.0f}ms: {e}")
        
        # Small delay between tests
        await asyncio.sleep(1)

async def check_server_health():
    """Check if LocalMind server is running."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8000/api/health", timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ Server healthy: {data}")
                    return True
    except Exception as e:
        print(f"❌ Server health check failed: {e}")
        return False

async def main():
    """Main test runner."""
    print("LocalMind Cold Start Performance Test")
    print("This test will help identify if ChromaDB cold-start is still causing delays.")
    print()
    
    # Check server health
    if not await check_server_health():
        print("❌ LocalMind server is not running!")
        print("Please start it with: python server.py")
        sys.exit(1)
    
    # Test cold start
    await test_cold_start()
    
    # Test warm performance
    await test_warm_performance()
    
    print("\n" + "=" * 50)
    print("🎯 Test Complete!")
    print()
    print("📊 Expected Results:")
    print("  Cold start: < 5s (with ChromaDB pre-warming)")
    print("  Warm queries: < 1s (for simple math)")
    print("  If times are > 10s, there's still a bottleneck")

if __name__ == "__main__":
    asyncio.run(main())
