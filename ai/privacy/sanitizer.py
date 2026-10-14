from ai.privacy.detector import analyze_privacy

REPLACEMENTS = {
    "api_key": "[api key redacted]",
    "email": "[email redacted]",
    "payment_card": "[payment card redacted]",
    "phone": "[phone redacted]",
    "postcode": "[postcode redacted]",
}


def sanitize_text(text: str, privacy_result=None) -> str:
    """
    Replace exact sensitive matches with category placeholders.

    This only touches spans reported by analyze_privacy(), so surrounding wording
    stays useful while the detected value is removed.
    """
    if not text:
        return text
    
    if privacy_result is None:
        privacy_result = analyze_privacy(text)
        
    matches = privacy_result["matches"]
    if not matches:
        return text
    
    output = []
    cursor = 0
    
    for match in sorted(matches, key=lambda item: item["start"]):
        output.append(text[cursor:match["start"]])
        output.append(REPLACEMENTS.get(match["category"], "[sensitive data redacted]"))
        cursor = match["end"]

    output.append(text[cursor:])
    return "".join(output)


def sanitize_structure(value: object) -> object:
    """Apply the existing text sanitizer to nested JSON values and object keys."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            sanitize_text(key): sanitize_structure(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_structure(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("Privacy sanitization requires JSON-compatible data.")
