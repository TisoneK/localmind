#!/usr/bin/env python3
"""
Setup script for semantic intent classification.

This script installs the optional dependencies needed for
semantic intent classification using sentence-transformers.
"""
import subprocess
import sys
from pathlib import Path

def main():
    """Install semantic dependencies and verify setup."""
    print("Setting up semantic intent classification...")
    
    # Check if we're in a virtual environment
    if sys.prefix == sys.base_prefix:
        print("WARNING: Not in a virtual environment.")
        print("It's recommended to use a virtual environment.")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            print("Setup cancelled.")
            return
    
    # Install semantic dependencies
    print("Installing semantic dependencies...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-e", ".[semantic]"
        ])
        print("Dependencies installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install dependencies: {e}")
        return
    
    # Test the setup
    print("\nTesting semantic classifier...")
    try:
        from core.semantic_classifier import classify_intent_semantic
        
        # Test cases
        test_cases = [
            ("Trending news today", "web_search"),
            ("what time is it", "sysinfo"),
            ("run this python code", "code_exec"),
            ("read the file contents", "file_task"),
        ]
        
        all_passed = True
        for message, expected_intent in test_cases:
            try:
                primary, _, confidence = classify_intent_semantic(message, False)
                if primary.value == expected_intent and confidence > 0.8:
                    print(f"  PASS: '{message}' -> {primary.value} ({confidence:.2f})")
                else:
                    print(f"  FAIL: '{message}' -> {primary.value} ({confidence:.2f}) [expected {expected_intent}]")
                    all_passed = False
            except Exception as e:
                print(f"  ERROR: '{message}' -> {e}")
                all_passed = False
        
        if all_passed:
            print("\nSemantic classification setup complete and verified!")
        else:
            print("\nSetup completed with some test failures.")
            print("The semantic classifier may still work but needs fine-tuning.")
            
    except ImportError as e:
        print(f"Failed to import semantic classifier: {e}")
        print("Please check that all dependencies were installed correctly.")

if __name__ == "__main__":
    main()
