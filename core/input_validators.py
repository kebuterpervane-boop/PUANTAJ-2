from datetime import datetime


def clean_text(value):
    """Return stripped text; None becomes empty string."""
    if value is None:
        return ""
    return str(value).strip()


def ensure_non_empty(value, field_name):
    """Validate required text fields."""
    text = clean_text(value)
    if not text:
        return False, f"{field_name} bos olamaz."
    return True, text


def ensure_non_negative_number(value, field_name, default=None):
    """
    Parse a number and ensure it is >= 0.
    Returns (ok, parsed_or_error_message).
    """
    text = clean_text(value)
    if text == "":
        if default is None:
            return False, f"{field_name} bos olamaz."
        return True, float(default)
    try:
        num = float(text)
    except Exception:
        return False, f"{field_name} sayi olmali."
    if num < 0:
        return False, f"{field_name} negatif olamaz."
    return True, num


def ensure_choice(value, allowed_values, field_name):
    """Validate that value is one of allowed_values."""
    text = clean_text(value)
    if text not in set(allowed_values):
        allowed = ", ".join(str(v) for v in allowed_values)
        return False, f"{field_name} gecersiz. Gecerli degerler: {allowed}"
    return True, text


def ensure_optional_iso_date(value, field_name):
    """Validate optional YYYY-MM-DD date text."""
    text = clean_text(value)
    if not text:
        return True, None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except Exception:
        return False, f"{field_name} YYYY-MM-DD formatinda olmali."
    return True, text


def parse_hhmm_to_minutes(value):
    """Parse HH:MM text and return total minutes, or None if invalid."""
    text = clean_text(value)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except Exception:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def ensure_hhmm_time(value, field_name):
    """
    Validate HH:MM and return normalized 2-digit HH:MM.
    Returns (ok, normalized_or_error_message).
    """
    minutes = parse_hhmm_to_minutes(value)
    if minutes is None:
        return False, f"{field_name} HH:MM formatinda olmali (orn: 08:20)."
    hour = minutes // 60
    minute = minutes % 60
    return True, f"{hour:02d}:{minute:02d}"


def ensure_positive_int(value, field_name, min_value=1):
    """Validate integer values >= min_value."""
    text = clean_text(value)
    try:
        num = int(text)
    except Exception:
        return False, f"{field_name} gecerli bir tamsayi olmali."
    if num < int(min_value):
        return False, f"{field_name} en az {int(min_value)} olmali."
    return True, num
