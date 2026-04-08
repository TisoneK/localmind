"""
Response filtering utilities - clean up system prompt leaks and formatting issues.
"""

def _filter_system_leaks(text: str) -> str:
    """Remove system prompt thinking leaks from model output while preserving user-facing thinking summaries."""
    lines = text.split('\n')
    filtered_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Remove system thinking templates (internal model reasoning)
        if (stripped.startswith(('THINK:', 'REFLECT:', 'ACT:', 'FINISH:', 
                               'Think:', 'Reflect:', 'Act:', 'Finish:')) or
            stripped.startswith(('- What did the user', '- What is the expected', 
                              '- What file extension', '- What tool is'))):
            continue
            
        # Preserve user-facing thinking summaries like _[Thinking: quality: good]_
        # and reflection content lines like quality:, issue:, next:
        filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)
