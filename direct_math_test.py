#!/usr/bin/env python3
"""
Direct Math Test

Test the math fast-path logic directly without HTTP pipeline.
This isolates just the math evaluation logic.
"""
import re
import time

def test_math_directly():
    """Test math fast-path logic directly."""
    print("direct Math Test")
    print("=" * 40)
    
    # Test cases from UI
    test_cases = [
        "What is 1 + 1",
        "1 + 1", 
        "What is 2 * 5",
        "10 / 2",
        "What is 3-1?",
        "  What is  5 + 3  ",
        "What is 1.5 + 2.5",
        "invalid math",
        "what is 1+1",
    ]
    
    math_pattern = r'^\s*(what\s+is\s+)?\d+(\.\d+)?\s*[\+\-\*\/]\s*\d+(\.\d+)?\s*\??\s*$'
    
    for test in test_cases:
        print(f"\nTesting: '{test}'")
        
        start_time = time.monotonic()
        
        # Check if pattern matches
        match = re.match(math_pattern, test.strip())
        if not match:
            print(f"  Pattern: NO MATCH")
            continue
            
        print(f"  Pattern: MATCH")
        
        try:
            # Extract math expression
            math_match = re.search(r'(\d+(\.\d+)?\s*[\+\-\*\/]\s*\d+(\.\d+)?)', test.strip())
            if not math_match:
                print(f"  Math extraction: FAILED")
                continue
                
            math_expr = math_match.group(1)
            print(f"  Math expression: '{math_expr}'")
            
            # Evaluate
            result = eval(math_expr)
            result_text = str(result)
            
            end_time = time.monotonic()
            calc_time = (end_time - start_time) * 1000
            
            print(f"  Result: {result_text}")
            print(f"  Time: {calc_time:.2f}ms")
            
        except Exception as e:
            end_time = time.monotonic()
            calc_time = (end_time - start_time) * 1000
            print(f"  ERROR: {e}")
            print(f"  Time: {calc_time:.2f}ms")

def test_performance():
    """Test performance of math evaluation."""
    print("\n" + "=" * 40)
    print("Performance Test")
    print("=" * 40)
    
    math_expr = "1 + 1"
    iterations = 1000
    
    print(f"Testing '{math_expr}' for {iterations} iterations...")
    
    start_time = time.monotonic()
    
    for i in range(iterations):
        result = eval(math_expr)
    
    end_time = time.monotonic()
    total_time = (end_time - start_time) * 1000
    avg_time = total_time / iterations
    
    print(f"Total time: {total_time:.2f}ms")
    print(f"Average per evaluation: {avg_time:.4f}ms")
    print(f"Evaluations per second: {1000 / avg_time:.0f}")

if __name__ == "__main__":
    test_math_directly()
    test_performance()
