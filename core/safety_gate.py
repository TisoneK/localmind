"""
Safety Gate — basic content filtering for LocalMind.

Provides a simple check function to filter obviously harmful or inappropriate content.
This is a minimal implementation focused on preventing obvious misuse.
"""
from __future__ import annotations
import re
from typing import Tuple


def check(message: str) -> Tuple[bool, str]:
    """
    Basic safety check for incoming messages.
    
    Returns:
        (is_safe, reason) - True if safe, False if blocked with reason
    """
    if not message or not message.strip():
        return False, "Empty message"
    
    # Basic patterns that should be blocked
    blocked_patterns = [
        # Obvious harmful requests
        r'\b(hate|kill|harm|hurt|violence|terror|bomb|weapon)\b',
        # Illegal activities
        r'\b(drug|illegal|hack|crack|steal|theft)\b',
        # Inappropriate content
        r'\b(porn|adult|explicit|nsfw)\b',
        # Self-harm
        r'\b(suicide|self.harm|kill myself)\b',
    ]
    
    message_lower = message.lower()
    for pattern in blocked_patterns:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return False, f"Content blocked: {pattern}"
    
    # If we get here, the message passed basic checks
    return True, ""
