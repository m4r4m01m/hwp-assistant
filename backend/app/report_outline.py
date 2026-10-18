"""Short outline items for new previews; frozen drafts and saved blobs stay intact."""
import math
import re
from .report_layout import lines, width


def split_bullet(text, protected=()):
    marker = re.match(r"^(□|ㅇ|○|-) ", text)
    if not marker:
        return [text]
    prefix = marker[0]
    # Same 15pt body and hanging margins used by report_layout, conservatively rounded.
    limit = {"□": 29, "ㅇ": 28, "○": 28, "-": 27}[marker[1]]
    def fits(value):
        return sum(max(1, math.ceil(width(p.rstrip()) / limit)) for p in lines(prefix + value, limit)) <= 3
    remaining, result = text[len(prefix):], []
    while not fits(remaining):
        end = 1
        while end < len(remaining) and fits(remaining[:end + 1]):
            end += 1
        # Prefer a complete sentence; otherwise an existing word boundary.
        sentences = [m.end() for m in re.finditer(r"[.!?。]\s+", remaining[:end]) if m.end() >= end // 2]
        spaces = [m.end() for m in re.finditer(r"\s+", remaining[:end])]
        cut = sentences[-1] if sentences else spaces[-1] if spaces else end
        # New rich drafts keep a selected phrase together when adding another
        # bullet. Existing callers and frozen plans retain the previous policy.
        spans = [m.span() for phrase in protected for m in re.finditer(re.escape(phrase), remaining)]
        while True:
            crossing = [(a, b) for a, b in spans if a < cut < b]
            if not crossing: break
            start = min(a for a, _ in crossing)
            if start == 0: break  # A cluster wider than an item cannot be kept together.
            cut = start
        result.append(prefix + remaining[:cut]); remaining = remaining[cut:]
    if remaining: result.append(prefix + remaining)
    assert ''.join(p[len(prefix):] for p in result) == text[len(prefix):]
    return result or [text]
