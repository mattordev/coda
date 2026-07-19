from ai.privacy.detector import analyze_privacy

REPLACEMENTS = {
    "api_key": "[api key redacted]",
    "email": "[email redacted]",
    "payment_card": "[payment card redacted]",
    "phone": "[phone redacted]",
    "postcode": "[postcode redacted]",
}

def sanitize_text(text: str, privacy_result=None) -> str:
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