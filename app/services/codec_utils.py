"""Utilitaires pour analyser les releases et codecs."""

import re

CODEC_PATTERNS = {
    "av1": re.compile(r"\b(av1|av01)\b", re.IGNORECASE),
    "h265": re.compile(r"\b(x265|h\.?265|hevc)\b", re.IGNORECASE),
    "h264": re.compile(r"\b(x264|h\.?264|avc)\b", re.IGNORECASE),
}


def detect_codec(title: str) -> str | None:
    for codec, pattern in CODEC_PATTERNS.items():
        if pattern.search(title):
            return codec
    return None


def codec_score(title: str, prefer: str = "av1") -> int:
    """Score plus élevé = meilleur choix. Privilégie AV1 puis H265."""
    codec = detect_codec(title)
    if prefer == "av1":
        if codec == "av1":
            return 100
        if codec == "h265":
            return 80
        if codec == "h264":
            return 40
    elif prefer == "h265":
        if codec == "h265":
            return 100
        if codec == "av1":
            return 90
        if codec == "h264":
            return 40
    return 10


def bytes_to_gb(size_bytes: int) -> float:
    return size_bytes / (1024**3)
