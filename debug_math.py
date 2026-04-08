#!/usr/bin/env python3
"""
Debug Math Fast-Path

Test the math fast-path logic directly to identify the issue.
"""
import re

def test_math_pattern():
    """Test the math regex pattern."""
    math_pattern = r'^\s*\d+(\.\d+)?\s*[\+\-\*\/]\s*\d+(\.\d+)?\s*$'
    
    test_cases = [
        "1 + 1",
        "2*5", 
        "10 / 2",
        "3-1",
        "  5 + 3  ",
        "1.5 + 2.5",
        "invalid math",
        "what is 1+1",
    ]
    
    print("🧮 Testing Math Pattern")
    print("=" * 40)
    
    for test in test_cases:
        match = re.match(math_pattern, test.strip())
        result = "✅ MATCH" if match else "❌ NO MATCH"
        print(f"{result:10} '{test}'")
        
        if match:
            try:
                calc_result = eval(test.strip())
                print(f"         Result: {calc_result}")
            except Exception as e:
                print(f"         Error: {e}")
        print()

def test_eval_safety():
    """Test eval safety."""
    print("🔒 Testing Eval Safety")
    print("=" * 40)
    
    safe_cases = ["1 + 1", "2 * 5", "10 / 2"]
    unsafe_cases = ["__import__('os')", "exec('print(1)')", "open('file.txt')"]
    
    for case in safe_cases + unsafe_cases:
        try:
            result = eval(case)
            print(f"✅ '{case}' → {result}")
        except Exception as e:
            print(f"❌ '{case}' → ERROR: {e}")

if __name__ == "__main__":
    test_math_pattern()
    print()
    test_eval_safety()
