"""
Natural Language Task Input (WP-06).

Parses free-form text into structured Task fields.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

_PRIORITY_KEYWORDS = {
    "critical": ["critical", "urgent", "asap", "p0"],
    "high": ["high", "important", "p1"],
    "medium": ["medium", "normal", "p2"],
    "low": ["low", "minor", "p3", "someday"],
}

_WEEKDAY_MAP = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _next_weekday(target: int) -> date:
    today = date.today()
    days_ahead = target - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _parse_date(text: str) -> Tuple[Optional[str], str]:
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
    if m:
        return m.group(1), text[:m.start()].strip() + " " + text[m.end():].strip()
    pattern = r'\b(?:by|on|due|before)\s+(?:next\s+)?(' + '|'.join(_WEEKDAY_MAP.keys()) + r')\b'
    m = re.search(pattern, text.lower())
    if m:
        d = _next_weekday(_WEEKDAY_MAP[m.group(1)])
        clean = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
        return d.isoformat(), clean
    if re.search(r'\b(?:by|due)\s+tomorrow\b', text.lower()):
        d = date.today() + timedelta(days=1)
        return d.isoformat(), re.sub(r'\b(?:by|due)\s+tomorrow\b', '', text, flags=re.IGNORECASE).strip()
    m = re.search(r'\bin\s+(\d+)\s+days?\b', text.lower())
    if m:
        d = date.today() + timedelta(days=int(m.group(1)))
        return d.isoformat(), re.sub(r'\bin\s+\d+\s+days?\b', '', text, flags=re.IGNORECASE).strip()
    return None, text


def _parse_tags(text: str) -> Tuple[List[str], str]:
    tags = re.findall(r'#(\w+)', text)
    return tags, re.sub(r'#\w+', '', text).strip()


def _parse_priority(text: str) -> Tuple[str, str]:
    lower = text.lower()
    for priority, keywords in _PRIORITY_KEYWORDS.items():
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower):
                return priority, re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
    return "medium", text


def _parse_depends_on(text: str) -> Tuple[List[int], str]:
    m = re.search(r'\b(?:depends\s+on|after|blocked\s+by)\s+([\w\s,\-]+)', text, re.IGNORECASE)
    if m:
        ids = [int(i) for i in re.findall(r'task[- ]?(\d+)', m.group(1), re.IGNORECASE)]
        return ids, text[:m.start()].strip() + " " + text[m.end():].strip()
    return [], text


def _parse_estimate(text: str) -> Tuple[Optional[int], str]:
    m = re.search(r'\b(\d+)\s*(?:min(?:utes?)?|m)\b', text, re.IGNORECASE)
    if m:
        return int(m.group(1)), text[:m.start()].strip() + " " + text[m.end():].strip()
    m = re.search(r'\b(\d+)\s*(?:hours?|h)\b', text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60, text[:m.start()].strip() + " " + text[m.end():].strip()
    return None, text


def parse_task_text(text: str) -> Dict[str, Any]:
    remaining = text.strip()
    due_date, remaining = _parse_date(remaining)
    tags, remaining = _parse_tags(remaining)
    priority, remaining = _parse_priority(remaining)
    depends_on, remaining = _parse_depends_on(remaining)
    estimated_minutes, remaining = _parse_estimate(remaining)
    title = re.sub(r'\s+', ' ', remaining).strip().rstrip('.,;:')
    if not title:
        title = "Untitled Task"
    result: Dict[str, Any] = {"title": title}
    if due_date:
        result["due_date"] = due_date
    if tags:
        result["tags"] = tags
    if priority != "medium":
        result["priority"] = priority
    if depends_on:
        result["depends_on"] = depends_on
    if estimated_minutes:
        result["estimated_minutes"] = estimated_minutes
    return result
