import re

# regex patterns for detecting sensitive information, trying to be uk aware.

EMAIL_PATTERN = r"[\w\.-]+@[\w\.-]+\.\w+"
PHONE_PATTERN = r"\b(?:\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}\b"
UK_POSTCODE_PATTERN = r"\b[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}\b"
CREDIT_CARD_PATTERN = r"\b\d{13,16}\b"
API_KEY_PATTERN = r"(sk-[a-zA-Z0-9]{20,}|api[_-]?key\s*[:=]\s*\S+)"

# Keyword signals (more intent base, less precise)

HIGH_RISK_KEYWORDS = [
    # Authentication / security
    "password",
    "passcode",
    "pin",
    "2fa",
    "authentication",
    "login details",
    "sign in",
    "credentials",

    # Financial
    "bank details",
    "card number",
    "credit card",
    "debit card",
    "sort code",
    "account number",
    "iban",
    "payment details",

    # Identity
    "national insurance",
    "ni number",
    "passport",
    "driver's licence",
    "id number",

    # Explicit personal ownership
    "my address",
    "my phone number",
    "my email is",
    "my details are",
    "here are my details",

    # Secrets / keys
    "api key",
    "secret key",
    "private key",
    "token",
]

MEDIUM_RISK_KEYWORDS = [
    # Communication
    "email",
    "call",
    "message",
    "text",
    "contact",

    # Personal/work relationships
    "boss",
    "manager",
    "client",
    "customer",
    "colleague",

    # General personal data references
    "address",
    "phone",
    "details",
    "information",
    "account",

    # Actions involving personal data
    "send",
    "share",
    "forward",
    "submit",
    "register",

    # Admin / forms / life tasks
    "form",
    "application",
    "appointment",
    "booking",
    "registration",

    # Work/personal context signals
    "my job",
    "my work",
    "my company",
    "my manager",
]

def _calculate_privacy_risk(text: str) -> float:
    """
    Returns a privacy risk score between 0.0 and 1.0 based on the presence of sensitive information.
    """
    if not text:
        return 0.0
    
    text_lower = text.lower()
    risk_score = 0.0
    
    # --- Pattern checks (strong signals) ---
    if re.search(EMAIL_PATTERN, text):
        risk_score += 0.4

    if re.search(PHONE_PATTERN, text):
        risk_score += 0.4

    if re.search(UK_POSTCODE_PATTERN, text.upper()):
        risk_score += 0.3

    if re.search(CREDIT_CARD_PATTERN, text):
        risk_score += 0.6

    if re.search(API_KEY_PATTERN, text_lower):
        risk_score += 0.8

    # --- Keyword checks (context signals) ---
    for keyword in HIGH_RISK_KEYWORDS:
        if keyword in text_lower:
            risk_score += 0.4

    for keyword in MEDIUM_RISK_KEYWORDS:
        if keyword in text_lower:
            risk_score += 0.2

    # clamp to 1.0 max to get a normalized risk score
    return min(risk_score, 1.0)

def _detect_categories(text: str) -> list[str]:
    if not text:
        return []

    text_lower = text.lower()
    categories = set()

    if re.search(EMAIL_PATTERN, text):
        categories.add("email")

    if re.search(PHONE_PATTERN, text):
        categories.add("phone")

    if re.search(UK_POSTCODE_PATTERN, text.upper()):
        categories.add("postcode")

    if re.search(CREDIT_CARD_PATTERN, text):
        categories.add("payment_card")

    if re.search(API_KEY_PATTERN, text_lower):
        categories.add("api_key")

    for keyword in HIGH_RISK_KEYWORDS:
        if keyword in text_lower:
            categories.add("sensitive_intent")

    for keyword in MEDIUM_RISK_KEYWORDS:
        if keyword in text_lower:
            categories.add("personal_context")

    return sorted(categories)

def _detect_matches(text: str) -> list[dict]:
    if not text:
        return []

    pattern_checks = [
        ("email", EMAIL_PATTERN, 0),
        ("phone", PHONE_PATTERN, 0),
        ("postcode", UK_POSTCODE_PATTERN, re.IGNORECASE),
        ("payment_card", CREDIT_CARD_PATTERN, 0),
        ("api_key", API_KEY_PATTERN, re.IGNORECASE),
    ]

    matches = []

    for category, pattern, flags in pattern_checks:
        for match in re.finditer(pattern, text, flags):
            matches.append({
                "category": category,
                "value": match.group(0),
                "start": match.start(),
                "end": match.end(),
            })

    return matches

def _risk_level(risk: float) -> str:
    if risk > 0.7:
        return "high"
    if risk > 0.3:
        return "medium"
    return "low"

def analyze_privacy(text: str) -> dict:
    risk = _calculate_privacy_risk(text)
    return {
        "risk": risk,
        "level": _risk_level(risk),
        "categories": _detect_categories(text),
        "matches": _detect_matches(text),
    }


def detect_privacy(text: str) -> float:
    """
    Returns a privacy risk score between 0.0 and 1.0 based on the presence of sensitive information.
    """
    return analyze_privacy(text)["risk"]

