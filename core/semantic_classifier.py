"""
Semantic Intent Classifier - Future-proof intent classification using embeddings.

Uses sentence embeddings to detect semantic similarity to known intent clusters,
combined with rule-based fast paths for obvious cases. This eliminates the need
for manual pattern maintenance and handles new phrasing automatically.
"""
from __future__ import annotations
import logging
from typing import Optional, Dict, List, Tuple
import numpy as np

from core.models import Intent
from core import intent_router
from core.config import settings

logger = logging.getLogger(__name__)

# Semantic example clusters for each intent
_SEMANTIC_EXAMPLES = {
    Intent.WEB_SEARCH: [
        "trending news today",
        "latest headlines",
        "what's happening right now",
        "current events",
        "recent developments",
        "breaking news",
        "what's new",
        "latest information",
        "current affairs",
        "recent news",
        "what's trending",
        "latest updates",
        "what happened today",
        "recent stories",
        "current headlines",
        "what's going on",
        "latest reports",
        "recent events",
        "what's the latest",
        "current news",
        "recent developments in technology",
        "latest sports scores",
        "current weather",
        "recent market updates",
        "what's happening in the world",
        "latest political news",
        "recent scientific discoveries",
        "current trends",
        "what's new in tech",
        "latest entertainment news",
        "recent business updates",
        "current world events",
        "what's trending now",
        "latest financial news",
        "recent health updates",
        "current fashion trends",
        "what's happening in sports",
        "latest tech news",
        "recent celebrity news",
        "current economic news",
        "what's new in science",
        "latest gaming news",
        "recent environmental updates",
        "current social media trends",
    ],
    
    Intent.CODE_EXEC: [
        "run this code",
        "execute the script",
        "what does this code do",
        "compute the result",
        "evaluate this function",
        "run python code",
        "execute javascript",
        "test this program",
        "calculate the output",
        "run the algorithm",
        "what will this code output",
        "execute this command",
        "run the calculation",
        "evaluate the expression",
        "test the function",
        "run the snippet",
        "what does this program do",
        "execute the method",
        "run the query",
        "compute the result",
        "evaluate the script",
        "run this python script",
        "execute the function",
        "test this code",
        "what will this print",
        "run the calculation",
        "evaluate the expression",
        "execute the program",
        "compute the value",
        "run this algorithm",
        "test the method",
        "what does this return",
        "execute the command",
        "run the script",
        "evaluate the code",
        "compute the answer",
        "test this snippet",
        "what will this produce",
        "run the function",
        "execute the calculation",
        "evaluate the program",
        "compute the output",
        "run this test",
        "execute the query",
        "test the algorithm",
        "what does this calculate",
    ],
    
    Intent.FILE_TASK: [
        "read the file",
        "open this document",
        "analyze the file",
        "parse the data",
        "summarize the document",
        "extract from file",
        "what's in this file",
        "read the contents",
        "analyze the document",
        "parse the file",
        "summarize this file",
        "extract information",
        "what does this file contain",
        "read the text file",
        "analyze the code file",
        "parse the data file",
        "summarize the report",
        "extract from document",
        "what's in the document",
        "read the PDF",
        "analyze the spreadsheet",
        "parse the JSON file",
        "summarize the text",
        "extract data",
        "what's in the code",
        "read the configuration",
        "analyze the log file",
        "parse the CSV",
        "summarize the article",
        "extract text",
        "what does this contain",
        "read the source code",
        "analyze the data",
        "parse the document",
        "summarize the content",
        "extract details",
        "what's written here",
        "read the report",
        "analyze the text",
        "parse the file contents",
        "summarize the information",
        "extract the data",
        "what's in this document",
    ],
    
    Intent.FILE_WRITE: [
        "write to file",
        "create a file",
        "save this document",
        "write a script",
        "create a program",
        "save the code",
        "write the output",
        "create a text file",
        "save the results",
        "write a report",
        "create a document",
        "save the data",
        "write a function",
        "create a script",
        "save the content",
        "write the code",
        "create a file with",
        "save this as",
        "write to",
        "create new file",
        "save the output to",
        "write a new",
        "create a",
        "save as",
        "write the",
        "create the",
        "save this",
        "write this",
        "create that",
        "save it",
        "write to file",
        "create document",
        "save content",
        "write script",
        "create program",
        "save data",
        "write report",
        "create text",
        "save file",
        "write content",
        "create new",
        "save output",
        "write result",
        "create something",
        "save text",
    ],
    
    Intent.MEMORY_OP: [
        "remember this",
        "recall that",
        "don't forget",
        "keep in mind",
        "note that",
        "what did I say",
        "what did we discuss",
        "from our conversation",
        "you mentioned",
        "I told you",
        "we talked about",
        "remember when",
        "recall our discussion",
        "don't forget that",
        "keep this in mind",
        "note this down",
        "what was said",
        "from last time",
        "you said earlier",
        "I mentioned",
        "we discussed",
        "remember the",
        "recall that",
        "don't forget the",
        "keep in mind the",
        "note the",
        "what did I tell you",
        "what did we talk about",
        "from our chat",
        "you told me",
        "I informed you",
        "we covered",
        "remember this information",
        "recall these details",
        "don't forget this info",
        "keep this information",
        "note these details",
        "what was mentioned",
        "from previous session",
        "you stated",
        "I explained",
        "we reviewed",
        "remember the context",
        "recall the background",
        "don't forget the context",
        "keep the context",
        "note the background",
        "what was covered",
        "from earlier",
        "you responded",
        "I described",
        "we analyzed",
    ],
    
    Intent.SYSINFO: [
        "what time is it",
        "current time",
        "what's the date",
        "today's date",
        "what day is it",
        "system information",
        "computer specs",
        "how much ram",
        "cpu info",
        "disk space",
        "operating system",
        "what os",
        "system specs",
        "pc information",
        "computer details",
        "hardware info",
        "memory usage",
        "cpu usage",
        "disk usage",
        "system details",
        "pc specs",
        "machine info",
        "hardware specs",
        "ram amount",
        "processor info",
        "storage space",
        "os version",
        "system type",
        "computer model",
        "hardware configuration",
        "memory capacity",
        "cpu speed",
        "disk capacity",
        "system architecture",
        "pc configuration",
        "machine specs",
        "hardware details",
        "available memory",
        "processor type",
        "free disk space",
        "os details",
        "system properties",
        "computer hardware",
        "system resources",
        "pc hardware",
        "machine hardware",
        "technical specs",
        "system performance",
        "computer performance",
        "hardware performance",
        "system status",
        "pc status",
        "machine status",
        "current system state",
        "computer state",
        "hardware state",
        "system health",
        "pc health",
        "machine health",
        "current system info",
        "computer info",
        "hardware info",
        "system data",
        "pc data",
        "machine data",
    ],
    
    Intent.SHELL: [
        "run command",
        "execute shell",
        "run terminal",
        "bash command",
        "execute cmd",
        "run script",
        "shell command",
        "terminal command",
        "run bash",
        "execute terminal",
        "run cmd",
        "shell script",
        "terminal script",
        "bash script",
        "cmd command",
        "run shell command",
        "execute bash",
        "run terminal command",
        "execute cmd command",
        "shell terminal",
        "bash terminal",
        "cmd terminal",
        "run shell script",
        "execute bash script",
        "run terminal script",
        "execute cmd script",
        "command line",
        "run command line",
        "execute command line",
        "shell command line",
        "bash command line",
        "terminal command line",
        "cmd command line",
        "run system command",
        "execute system command",
        "system command",
        "run os command",
        "execute os command",
        "os command",
        "run unix command",
        "execute unix command",
        "unix command",
        "run linux command",
        "execute linux command",
        "linux command",
        "run windows command",
        "execute windows command",
        "windows command",
        "run powershell",
        "execute powershell",
        "powershell command",
        "run batch",
        "execute batch",
        "batch command",
        "run sh",
        "execute sh",
        "sh command",
        "run exe",
        "execute exe",
        "exe command",
    ],
}

# Fast-path patterns for obvious cases (keep existing)
_OBVIOUS_CHAT_PATTERNS = [
    r"^(hi|hey|hello|howdy|yo|sup|greetings|good (morning|afternoon|evening|night))[\s!?.]*$",
    r"^(thanks?|thank you|thx|ty|cheers|cool|ok|okay|got it|understood|noted|sure|np|no problem)[\s!?.]*$",
    r"^(bye|goodbye|cya|see ya|later|take care|good night)[\s!?.]*$",
]

class SemanticClassifier:
    def __init__(self):
        self._embedding_model = None
        self._embeddings_cache = {}
        self._initialized = False
    
    def _initialize_model(self):
        """Lazy initialization of embedding model."""
        if self._initialized:
            return
        
        try:
            # Try to import sentence-transformers
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            self._initialized = True
            logger.info("[semantic] Initialized sentence transformer model")
            
            # Pre-compute embeddings for semantic examples
            self._precompute_embeddings()
            
        except ImportError:
            logger.warning("[semantic] sentence-transformers not available, using fallback")
            self._initialized = False
        except Exception as e:
            logger.error(f"[semantic] Failed to initialize model: {e}")
            self._initialized = False
    
    def _precompute_embeddings(self):
        """Pre-compute embeddings for semantic examples."""
        if not self._embedding_model:
            return
        
        for intent, examples in _SEMANTIC_EXAMPLES.items():
            embeddings = self._embedding_model.encode(examples, convert_to_numpy=True)
            self._embeddings_cache[intent] = embeddings
        
        logger.info(f"[semantic] Pre-computed embeddings for {len(_SEMANTIC_EXAMPLES)} intents")
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding for a single text."""
        if not self._embedding_model:
            return None
        
        try:
            return self._embedding_model.encode([text], convert_to_numpy=True)[0]
        except Exception as e:
            logger.error(f"[semantic] Failed to encode text: {e}")
            return None
    
    def _semantic_similarity(self, text: str, intent: Intent) -> float:
        """Calculate semantic similarity between text and intent examples."""
        if not self._initialized or intent not in self._embeddings_cache:
            return 0.0
        
        text_embedding = self._get_embedding(text)
        if text_embedding is None:
            return 0.0
        
        # Calculate cosine similarity with all examples for this intent
        intent_embeddings = self._embeddings_cache[intent]
        similarities = np.dot(intent_embeddings, text_embedding) / (
            np.linalg.norm(intent_embeddings, axis=1) * np.linalg.norm(text_embedding)
        )
        
        # Return maximum similarity
        return float(np.max(similarities))
    
    def classify(self, message: str, has_attachment: bool = False) -> Tuple[Intent, Optional[Intent], float]:
        """
        Classify message intent using semantic similarity + rule-based fallback.
        
        Returns:
            (primary_intent, secondary_intent, confidence)
        """
        self._initialize_model()
        
        # Fast-path: obvious conversational messages
        import re
        stripped = message.strip()
        if any(re.fullmatch(p, stripped, re.IGNORECASE) for p in _OBVIOUS_CHAT_PATTERNS):
            return Intent.CHAT, None, 0.95
        
        # Fast-path: very short messages with no tool signals
        words = stripped.split()
        if len(words) <= 6 and not has_attachment:
            has_tool_signal = any([
                self._semantic_similarity(message, Intent.SYSINFO) > 0.7,
                self._semantic_similarity(message, Intent.MEMORY_OP) > 0.7,
                self._semantic_similarity(message, Intent.CODE_EXEC) > 0.7,
                self._semantic_similarity(message, Intent.WEB_SEARCH) > 0.7,
                self._semantic_similarity(message, Intent.FILE_WRITE) > 0.7,
                self._semantic_similarity(message, Intent.FILE_TASK) > 0.7,
                self._semantic_similarity(message, Intent.SHELL) > 0.7,
            ])
            if not has_tool_signal:
                return Intent.CHAT, None, 0.85
        
        # Attachment handling
        if has_attachment:
            file_write_sim = self._semantic_similarity(message, Intent.FILE_WRITE)
            if file_write_sim > 0.7:
                return Intent.FILE_TASK, Intent.FILE_WRITE, 0.90
            return Intent.FILE_TASK, None, 0.85
        
        # Semantic similarity classification
        intent_scores = []
        
        # Calculate similarity scores for all intents
        for intent in Intent:
            if intent == Intent.CHAT:
                continue  # Handle CHAT as fallback
            
            similarity = self._semantic_similarity(message, intent)
            if similarity > 0.6:  # Threshold for semantic match
                intent_scores.append((intent, similarity))
        
        # Sort by similarity score
        intent_scores.sort(key=lambda x: x[1], reverse=True)
        
        if intent_scores:
            primary, primary_score = intent_scores[0]
            secondary = intent_scores[1][0] if len(intent_scores) > 1 else None
            secondary_score = intent_scores[1][1] if len(intent_scores) > 1 else 0.0
            
            # Adjust confidence based on score strength
            confidence = min(primary_score * 1.2, 0.95)  # Boost confidence but cap at 0.95
            
            logger.debug(f"[semantic] {primary.value}={confidence:.2f} (semantic match)")
            return primary, secondary, confidence
        
        # Fallback to rule-based classification
        logger.debug("[semantic] No semantic match, falling back to rule-based")
        rule_primary, rule_secondary = intent_router.classify_multi(message, has_attachment)
        return rule_primary, rule_secondary, 0.70  # Lower confidence for fallback


# Global instance
_semantic_classifier = SemanticClassifier()

def classify_intent_semantic(message: str, has_attachment: bool = False) -> Tuple[Intent, Optional[Intent], float]:
    """Public interface for semantic intent classification."""
    return _semantic_classifier.classify(message, has_attachment)
