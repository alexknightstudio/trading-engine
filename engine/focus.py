"""Owner-directed focus list (data/focus.txt): AI, semiconductors, data-center
infrastructure, and emerging markets (US-listed ETFs and ADRs).

Focus symbols get PRIORITY in the slot competition — when entry signals
outnumber free position slots, a valid signal on a focus name always outranks
a valid signal on a non-focus name (then score decides within each group).
This is a portfolio-composition directive from the owner, not a lab-derived
edge; the signal and risk rules themselves are unchanged.
"""
from pathlib import Path

FOCUS_FILE = Path(__file__).parent.parent / "data" / "focus.txt"


def load_focus() -> frozenset[str]:
    if FOCUS_FILE.exists():
        return frozenset(FOCUS_FILE.read_text().split())
    return frozenset()
