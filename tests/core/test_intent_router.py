"""Tests for the intent router."""
import pytest
from core.intent_router import classify
from core.models import Intent


def test_file_task_from_attachment():
    assert classify("anything", has_attachment=True) == Intent.FILE_TASK


def test_web_search_on_recent():
    assert classify("What is the latest news today?") == Intent.WEB_SEARCH


def test_web_search_on_current():
    assert classify("Who is the current president of Kenya?") == Intent.WEB_SEARCH


def test_code_exec_detected():
    assert classify("What does this code output?\n```python\nprint(1+1)\n```") == Intent.CODE_EXEC


def test_memory_store():
    assert classify("Please remember that I prefer Python over JavaScript") == Intent.MEMORY_OP


def test_memory_recall():
    assert classify("What did we discuss last session?") == Intent.MEMORY_OP


def test_file_write():
    assert classify("Write a Python script to sort a list and save it as sort.py") == Intent.FILE_WRITE


def test_plain_chat():
    assert classify("Hello, how are you?") == Intent.CHAT


def test_plain_chat_simple_question():
    assert classify("What is the capital of France?") == Intent.CHAT


def test_file_task_by_keyword():
    assert classify("Read the attached PDF and summarise it") == Intent.FILE_TASK
