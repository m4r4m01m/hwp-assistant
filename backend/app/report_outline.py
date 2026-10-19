"""Short outline items for new previews; frozen drafts and saved blobs stay intact."""
import math
import re
from .report_layout import lines, width


# A predicate must retain its preceding word when a new outline item is made.
# This adds no prose and does not reinterpret sentence contents or citations.
ENDING = re.compile(r"\S+\s+(?:필요(?:함|가\s+있음)?|아님|있음|없음|가능함|불가함|어려움|"
                    r"예정임|계획임|대상임|확인됨|요구됨|권고함|기대됨|중요함|타당함|"
                    r"적절함|미정임|완료됨|유지됨|우려됨|관련됨|해당함|해야\s+함|하지\s+않음)"
                    r"(?=[.!?。\s]|\[S|주\d+\)|\*\d|$)"
                    r"(?:[.!?。]?\s*(?:\[S[1-9]\d?\]|주[1-9]\d?\)|\*[1-9]\d?))*")


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
        spans.extend(match.span() for match in ENDING.finditer(remaining))
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
