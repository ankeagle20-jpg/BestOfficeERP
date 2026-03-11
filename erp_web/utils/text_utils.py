def turkish_lower(value: str) -> str:
    """Türkçe karakterleri dikkate alarak case-insensitive karşılaştırma için normalize eder.

    Örn: "VİTA", "Vita", "vita", "vİta" -> "vita"
    """
    if value is None:
        return ""
    s = str(value)
    table = str.maketrans({
        "İ": "i", "I": "i", "ı": "i",
        "Ş": "s", "ş": "s",
        "Ğ": "g", "ğ": "g",
        "Ü": "u", "ü": "u",
        "Ö": "o", "ö": "o",
        "Ç": "c", "ç": "c",
    })
    return s.translate(table).lower()
