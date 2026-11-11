"""
Contact information detector.

Scans message text for phone numbers, emails, and social handles.
Returns {detected: bool, type: str|None, matches: list}.

Used by the messaging router to block messages that try to move
communication off-platform.
"""
import re
import unicodedata
from typing import Optional


PHONE_PATTERNS = [
    r'\b0\d{2}[\s\-]?\d{3}[\s\-]?\d{4}\b',              # 0244123456
    r'\b\+233[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{4}\b',   # +233 24 412 3456
    r'\b0\d{9}\b',                                       # 0244123456
    r'\d{3}[\s\-]?\d{3}[\s\-]?\d{4}',                   # 244 123 4567
]

EMAIL_PATTERN = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'

SOCIAL_PATTERNS = [
    r'wa\.me/\S+',
    r't\.me/\S+',
    r'@[a-zA-Z0-9_]{2,}',
    r'whatsapp\s*:?\s*\d+',
    r'telegram\s*:?\s*\S+',
    r'instagram\s*:?\s*\S+',
    r'facebook\s*:?\s*\S+',
    r'snapchat\s*:?\s*\S+',
    r'tiktok\s*:?\s*\S+',
]

SPELLED_PHONE_PATTERNS = [
    r'\bzero\s*two\s*four\s*four\b',
    r'\b\d[\s\-]+(?:zero|one|two|three|four|five|six|seven|eight|nine)',
]

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_SPOKEN = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _normalize(text: str) -> str:
    t = text.translate(_ZERO_WIDTH)
    t = unicodedata.normalize("NFKC", t)
    for w, d in _SPOKEN.items():
        t = re.sub(rf"\b{w}\b", d, t, flags=re.I)
    t = re.sub(r"\s*[\[\(]\s*at\s*[\]\)]\s*", "@", t, flags=re.I)
    t = re.sub(r"\s*[\[\(]\s*dot\s*[\]\)]\s*", ".", t, flags=re.I)
    return t


def detect_contact_info(text: str) -> dict:
    """
    Returns:
        {
            "detected": bool,
            "type": "phone"|"email"|"social"|"spelled_phone"|None,
            "matches": list[str],
        }
    """
    if not text:
        return {"detected": False, "type": None, "matches": []}

    text = _normalize(text)
    matches = []

    # Phone
    for pattern in PHONE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return {"detected": True, "type": "phone", "matches": [m.group(0)]}

    # Email
    m = re.search(EMAIL_PATTERN, text, re.IGNORECASE)
    if m:
        return {"detected": True, "type": "email", "matches": [m.group(0)]}

    # Social handles
    for pattern in SOCIAL_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return {"detected": True, "type": "social", "matches": [m.group(0)]}

    # Spelled-out numbers
    text_lower = text.lower()
    for pattern in SPELLED_PHONE_PATTERNS:
        if re.search(pattern, text_lower):
            return {"detected": True, "type": "spelled_phone", "matches": ["(spelled-out number)"]}

    return {"detected": False, "type": None, "matches": matches}