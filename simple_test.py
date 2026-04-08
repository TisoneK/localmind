#!/usr/bin/env python3
"""
Simple Direct Test

Minimal test to directly check LocalMind response times.
"""
import asyncio
import time
import json
import aiohttp

async def simple_test():
    """Test a single simple query."""
    print("🧪 Testing simple query: 'What is 1 + 1'")
    
    start_time = time.monotonic()
    
    try:
        async with aiohttp.ClientSession() as session:
            # Create FormData like the UI does
            from aiohttp import FormData
            form_data = FormData()
            form_data.add_field('message', 'What is 1 + 1')
            form_data.add_field('session_id', 'simple_test')
            
            async with session.post(
                "http://localhost:8000/api/chat",
                data=form_data,
                timeout=aiohttp.ClientTimeout(total=300)  # 5 minute timeout
            ) as response:
                
                print(f"📡 Response status: {response.status}")
                
                chunks = []
                first_chunk_time = None
                
                async for line in response.content:
                    current_time = time.monotonic()
                    
                    if first_chunk_time is None:
                        first_chunk_time = current_time
                        time_to_first = (first_chunk_time - start_time) * 1000
                        print(f"⚡ First chunk: {time_to_first:.0f}ms")
                    
                    try:
                        line_str = line.decode('utf-8').strip()
                        if line_str.startswith('data: '):
                            data = json.loads(line_str[6:])
                            chunk_text = data.get('text', '')
                            if chunk_text:
                                chunks.append(chunk_text)
                                print(f"📝 Chunk: {chunk_text}")
                            
                            if data.get('done', False):
                                break
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"⚠️  Chunk error: {e}")
                        continue
                
                end_time = time.monotonic()
                total_time = (end_time - start_time) * 1000
                full_response = ''.join(chunks)
                
                print(f"\n✅ Test Results:")
                print(f"  Total time: {total_time:.0f}ms")
                print(f"  Time to first chunk: {time_to_first:.0f}ms")
                print(f"  Response length: {len(full_response)} chars")
                print(f"  Full response: '{full_response}'")
                
                # Performance analysis
                if total_time < 1000:
                    print(f"  🟢 Performance: EXCELLENT (< 1s)")
                elif total_time < 5000:
                    print(f"  🟡 Performance: GOOD (1-5s)")
                elif total_time < 10000:
                    print(f"  🟠 Performance: SLOW (5-10s)")
                else:
                    print(f"  🔴 Performance: VERY SLOW (> 10s) - BOTTLENECK!")
                
    except asyncio.TimeoutError:
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        print(f"❌ TIMEOUT after {total_time:.0f}ms")
        
    except Exception as e:
        end_time = time.monotonic()
        total_time = (end_time - start_time) * 1000
        print(f"❌ ERROR after {total_time:.0f}ms: {e}")

async def main():
    """Run the simple test."""
    print("LocalMind Simple Performance Test")
    print("Testing: '1 + 1' query")
    print("Make sure LocalMind server is running on http://localhost:8000")
    print("=" * 50)
    
    await simple_test()

if __name__ == "__main__":
    asyncio.run(main())
