import re

# regex patterns for detecting sensitive information, trying to be uk aware.

EMAIL_PATTERN = r"[\w\.-]+@[\w\.-]+\.\w+"
PHONE_PATTERN = r"\b(?:\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}\b"
UK_POSTCODE_PATTERN = r"\b[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}\b"
CREDIT_CARD_PATTERN = r"\b\d{13,16}\b"
API_KEY_PATTERN = r"(sk-[a-zA-Z0-9]{20,}|api[_-]?key\s*[:=]\s*\S+)"

# Keyword signals (more intent base, less precise)

HIGH_RISK_KEYWORDS = [
    "password",
    "my address",
    "my bank",
    "my card",
    "my details",
    "login",
    "ssn",
    "national insurance",
]

MEDIUM_RISK_KEYWORDS = [
    "email me",
    "call me",
    "contact me",
    "send to",
    "account",
    "details",
]

def detect_privacy(text: str) -> float:
    """
    Returns a privacy risk score between 0.0 and 1.0 based on the presence of sensitive information.
    """
    if not text:
        return 0.0
    
    text_lower = text.lower()
    risk_score = 0.0
    
    # --- Pattern checks (strong signals) ---
    if re.search(EMAIL_PATTERN, text):
        risk += 0.4

    if re.search(PHONE_PATTERN, text):
        risk += 0.4

    if re.search(UK_POSTCODE_PATTERN, text.upper()):
        risk += 0.3

    if re.search(CREDIT_CARD_PATTERN, text):
        risk += 0.6

    if re.search(API_KEY_PATTERN, text_lower):
        risk += 0.8

    # --- Keyword checks (context signals) ---
    for keyword in HIGH_RISK_KEYWORDS:
        if keyword in text_lower:
            risk += 0.4

    for keyword in MEDIUM_RISK_KEYWORDS:
        if keyword in text_lower:
            risk += 0.2

    # clamp to 1.0 max to get a normalized risk score
    return min(risk, 1.0)

