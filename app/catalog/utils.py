import re


MANUFACTURER_SUFFIX_RE = re.compile(
    r"(?iu)\s*(?:[-–—,:;/]\s*|\(\s*)?(?:FAB(?:RICANTE)?|MARCA)\.?\s*[:\-]?\s*[^()\[\]]*(?:\)|\])?\s*$"
)


def strip_manufacturer_hint(description: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""

    previous = None
    while text and text != previous:
        previous = text
        text = MANUFACTURER_SUFFIX_RE.sub("", text).rstrip(" -–—,:;/(").strip()

    return text
