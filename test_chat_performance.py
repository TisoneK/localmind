#!/usr/bin/env python3
"""
Chat Performance Test Script

Tests LocalMind chat endpoint response times for various queries.
Helps identify performance bottlenecks by measuring actual API response times.
"""
import asyncio
import time
import json
import aiohttp
from typing import List, Dict

# Test queries that should be fast
TEST_QUERIES = [
    "1 + 1",
    "What time is it?", 
    "Hello",
    "2 * 5",
    "10 / 2",
    "What is 3 + 4?",
    "List desktop files",
    "How are you?",
]

class ChatPerformanceTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session_id = f"test_session_{int(time.time())}"
    
    async def test_single_query(self, session: aiohttp.ClientSession, query: str) -> Dict:
        """Test a single query and return timing data."""
        print(f"🧪 Testing: '{query}'")
        
        start_time = time.monotonic()
        
        try:
            async with session.post(
                f"{self.base_url}/api/chat",
                json={
                    "message": query,
                    "session_id": self.session_id
                },
                headers={"Content-Type": "application/json"}
            ) as response:
                
                # Stream response and collect chunks
                chunks = []
                first_chunk_time = None
                
                async for line in response.content:
                    if line:
                        chunk_time = time.monotonic()
                        if first_chunk_time is None:
                            first_chunk_time = chunk_time
                            time_to_first_chunk = (chunk_time - start_time) * 1000
                            print(f"  ⚡ First chunk: {time_to_first_chunk:.0f}ms")
                        
                        try:
                            # SSE format: data: {"text": "...", "done": false}
                            line_str = line.decode('utf-8').strip()
                            if line_str.startswith('data: '):
                                data = json.loads(line_str[6:])
                                chunks.append(data.get('text', ''))
                                if data.get('done', False):
                                    break
                        except json.JSONDecodeError:
                            continue
                
                end_time = time.monotonic()
                total_time = (end_time - start_time) * 1000
                full_response = ''.join(chunks)
                
                result = {
                    "query": query,
                    "total_time_ms": round(total_time, 0),
                    "time_to_first_chunk_ms": round(time_to_first_chunk, 0) if first_chunk_time else None,
                    "response_length": len(full_response),
                    "response_preview": full_response[:100] + "..." if len(full_response) > 100 else full_response,
                    "success": response.status == 200
                }
                
                print(f"  ✅ Total time: {result['total_time_ms']}ms")
                print(f"  📝 Response: {result['response_preview']}")
                print()
                
                return result
                
        except Exception as e:
            end_time = time.monotonic()
            total_time = (end_time - start_time) * 1000
            
            result = {
                "query": query,
                "total_time_ms": round(total_time, 0),
                "time_to_first_chunk_ms": None,
                "response_length": 0,
                "response_preview": f"ERROR: {str(e)}",
                "success": False
            }
            
            print(f"  ❌ Error after {result['total_time_ms']}ms: {e}")
            print()
            
            return result
    
    async def run_all_tests(self):
        """Run all test queries and collect results."""
        print("🚀 Starting LocalMind Performance Tests")
        print(f"📍 Target: {self.base_url}")
        print(f"🆔 Session: {self.session_id}")
        print("=" * 60)
        
        async with aiohttp.ClientSession() as session:
            results = []
            
            for query in TEST_QUERIES:
                result = await self.test_single_query(session, query)
                results.append(result)
                
                # Small delay between tests
                await asyncio.sleep(1)
            
            # Print summary
            self.print_summary(results)
            
            return results
    
    def print_summary(self, results: List[Dict]):
        """Print performance summary."""
        print("=" * 60)
        print("📊 PERFORMANCE SUMMARY")
        print("=" * 60)
        
        successful = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]
        
        if successful:
            times = [r['total_time_ms'] for r in successful]
            first_chunks = [r['time_to_first_chunk_ms'] for r in successful if r['time_to_first_chunk_ms']]
            
            print(f"✅ Successful queries: {len(successful)}/{len(results)}")
            print(f"❌ Failed queries: {len(failed)}")
            print()
            print("⏱️  TIMING ANALYSIS:")
            print(f"  Average response time: {sum(times)/len(times):.0f}ms")
            print(f"  Fastest response: {min(times):.0f}ms")
            print(f"  Slowest response: {max(times):.0f}ms")
            
            if first_chunks:
                print(f"  Average time to first chunk: {sum(first_chunks)/len(first_chunks):.0f}ms")
                print(f"  Fastest first chunk: {min(first_chunks):.0f}ms")
            
            print()
            print("📈 DETAILED RESULTS:")
            for result in successful:
                status = "🐌 SLOW" if result['total_time_ms'] > 10000 else "⚡ FAST"
                print(f"  {status} {result['total_time_ms']:5.0f}ms - '{result['query']}'")
        
        if failed:
            print()
            print("❌ FAILED QUERIES:")
            for result in failed:
                print(f"  {result['total_time_ms']:5.0f}ms - '{result['query']}' - {result['response_preview']}")
        
        print()
        print("🎯 PERFORMANCE CATEGORIES:")
        print("  < 1s:    Excellent (should be target for simple queries)")
        print("  1-5s:    Good (acceptable for complex queries)")  
        print("  5-10s:   Slow (needs investigation)")
        print("  > 10s:    Very Slow (definite bottleneck)")

async def main():
    """Main test runner."""
    tester = ChatPerformanceTester()
    
    try:
        await tester.run_all_tests()
    except KeyboardInterrupt:
        print("\n🛑 Tests interrupted by user")
    except Exception as e:
        print(f"\n💥 Test suite failed: {e}")

if __name__ == "__main__":
    print("LocalMind Chat Performance Tester")
    print("Make sure LocalMind server is running on http://localhost:8000")
    print()
    
    asyncio.run(main())
