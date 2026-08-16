"""Pre-bet research screen for KalshiKnight. Claude researches each candidate
favorite with live web search and may VETO it. Veto-only (it can never add
bets), and the caller FAILS CLOSED — if this screen errors, no bets are
placed that run. Every verdict is logged by the caller to build a track record.
"""
import json


def screen(picks: list[dict]) -> list[dict]:
    """Returns [{ticker, action: OK|VETO, reason}] for each pick."""
    import anthropic

    lines = "\n".join(
        f"- {p['ticker']} | buy {p['side']} @ ${p['ask']:.2f} "
        f"(resolves in {p['days']}d) | {p['title']}"
        for p in picks
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
        system=(
            "You are the research screen for a bot that buys high-probability "
            "prediction-market contracts (~$0.90-0.98) on Kalshi. For each "
            "candidate, research CURRENT news (injuries, scratches, weather, "
            "schedule changes, breaking developments) and the market's "
            "resolution criteria risk. VETO a candidate only when you find a "
            "concrete reason its true probability is meaningfully lower than "
            "the price implies: a key participant out, event postponed, "
            "ambiguous/technical resolution wording, correlated cascade risk, "
            "or the market being stale versus news. Default to OK — false "
            "vetoes cost real performance; you are a screen, not a picker. "
            "Search only where research could plausibly change the verdict. "
            "After researching, output ONLY a JSON array, no prose: "
            '[{"ticker": "...", "action": "OK"|"VETO", "reason": "..."}] '
            "with exactly one entry per candidate."
        ),
        messages=[{"role": "user", "content": f"Candidates:\n{lines}"}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("research model declined")
    text = "".join(b.text for b in response.content if b.type == "text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"unparseable research output: {text[:200]}")
    verdicts = json.loads(text[start:end + 1])
    known = {p["ticker"] for p in picks}
    verdicts = [v for v in verdicts if v.get("ticker") in known
                and v.get("action") in ("OK", "VETO")]
    if len(verdicts) != len(picks):
        raise RuntimeError("research verdicts incomplete — failing closed")
    return verdicts
