"""KnightTrader evolution loop — the bot redesigns its own strategy code.

Nightly (cloud): Claude studies the system's source, its performance, the
market, and the log of every past experiment, then proposes ONE change as
real code (a new engine/lineup.py, optionally with a new engine/evolved_*.py
strategy module). A deterministic arena then decides — Claude proposes,
the arena disposes:

  ADOPT only if the candidate beats the incumbent on the FULL 10-year sample
  (CAGR and Sharpe), wins a majority of ~2-year walk-forward folds, has a
  meaningful sample (>=200 trades), and the last adoption was >=14 days ago.

Every trial — adopted or rejected — is appended to state/evolution_log.jsonl
and committed publicly. Hard fences: this loop only ever writes lineup.py and
evolved_*.py; risk rules (engine/risk.py), order execution (paper_bot.py),
and the news screen are out of reach, and the backtester enforces risk rules
no matter what strategy code emits.

Usage: evolve.py [--no-fetch] [--test]   (--test: arena self-check, no API call)
"""
import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv

from engine import stats
from engine.backtest import run
from engine.data import load_universe
from engine.regime import classify

EVO_LOG = HERE / "state" / "evolution_log.jsonl"
LINEUP_FILE = HERE / "engine" / "lineup.py"
UNIVERSE_FILE = HERE / "data" / "universe.txt"
ADOPT_COOLDOWN_DAYS = 14
FOLD_YEARS = 2
MIN_TRADES = 200
MODEL = "claude-opus-5"


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def fold_sharpes(equity):
    out = []
    for _, chunk in equity.groupby(equity.index.year // FOLD_YEARS):
        r = chunk.pct_change().dropna()
        if len(r) > 60 and r.std() > 0:
            out.append(float(r.mean() / r.std() * 252 ** 0.5))
    return out


def build_signals(bars, regime, factories):
    out = {}
    for sym in bars:
        sigs = [make(bars[sym], regime) for make in factories]
        for sig in sigs:
            sig.frame["entry_short"] = False  # longs only (standing decision)
        out[sym] = sigs
    return out


def evaluate(bars, regime, dates, factories, label):
    res = run(bars, build_signals(bars, regime, factories), dates, regime)
    s = stats.summarize(res, label)
    return {
        "cagr": round(float(s["cagr"]), 4), "sharpe": round(float(s["sharpe"]), 3),
        "max_dd": round(float(s["max_drawdown"]), 4), "trades": int(s["trades"]),
        "folds": [round(float(f), 2) for f in fold_sharpes(res.equity)],
    }


def last_adoption_age_days():
    if not EVO_LOG.exists():
        return 10_000
    newest = None
    for line in EVO_LOG.read_text().splitlines():
        e = json.loads(line)
        if e.get("verdict") == "ADOPTED":
            newest = e["ts"]
    if not newest:
        return 10_000
    dt = datetime.fromisoformat(newest)
    return (datetime.now(timezone.utc) - dt).days


def trial_history(n=25):
    if not EVO_LOG.exists():
        return "(no prior trials)"
    lines = EVO_LOG.read_text().splitlines()[-n:]
    out = []
    for line in lines:
        e = json.loads(line)
        c, i = e.get("candidate", {}), e.get("incumbent", {})
        out.append(f"- {e['ts'][:10]} [{e['verdict']}] {e['name']}: {e.get('rationale', '')[:140]}"
                   f" (cand CAGR {c.get('cagr')} vs inc {i.get('cagr')})")
    return "\n".join(out)


def propose(incumbent_metrics, market_summary):
    """One Claude call: propose ONE experiment as real code."""
    import anthropic

    sources = {
        "engine/strategies.py": (HERE / "engine" / "strategies.py").read_text(),
        "engine/indicators.py": (HERE / "engine" / "indicators.py").read_text(),
        "engine/analyzers.py": (HERE / "engine" / "analyzers.py").read_text(),
        "engine/lineup.py": LINEUP_FILE.read_text(),
    }
    findings = (HERE / "README.md").read_text().split("## Findings log")[-1][:6000]

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "rationale": {"type": "string"},
            "evolved_module": {"type": ["string", "null"]},
            "lineup_module": {"type": "string"},
        },
        "required": ["name", "rationale", "evolved_module", "lineup_module"],
        "additionalProperties": False,
    }

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=(
            "You are the evolution loop of KnightTrader, an autonomous paper-trading "
            "system. Each night you may propose exactly ONE experiment: a modified "
            "strategy lineup, expressed as real Python code. A deterministic arena "
            "will backtest your proposal against the incumbent over 10 years and "
            "adopt it only if it wins on the full sample AND most walk-forward folds. "
            "You are judged over months, not nights: propose durable structural edges "
            "(entry/exit logic, filters, regime awareness, new strategies from the "
            "analyzer toolbox), never fits to recent noise. Do not re-propose ideas "
            "similar to past REJECTED trials. Contract: lineup_module is the complete "
            "new source of engine/lineup.py exporting FACTORIES, a list of callables "
            "(bars: DataFrame, regime: Series) -> Signals. evolved_module (or null) "
            "is the complete source of a new module that lineup.py may import as "
            "'from .evolved_<name> import ...'; if provided, name your import "
            "accordingly — the arena saves it as engine/evolved_<name>.py using your "
            "'name' field (lowercase, underscores). Signals frames must include "
            "columns: entry_long, entry_short, exit_long, exit_short, size_scale, "
            "stop_dist, score_long, score_short (size_dist optional). Use only "
            "numpy/pandas and engine.indicators / engine.analyzers / "
            "engine.strategies / engine.regime imports. Never import or modify risk, "
            "backtest, broker, or I/O code. Shorts are disabled by the harness. "
            "Small, well-motivated changes beat big rewrites."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"INCUMBENT (full-sample metrics): {json.dumps(incumbent_metrics)}\n\n"
                f"MARKET SNAPSHOT: {market_summary}\n\n"
                f"FINDINGS LOG (settled decisions — do not relitigate):\n{findings}\n\n"
                f"PAST TRIALS:\n{trial_history()}\n\n"
                "CURRENT SOURCE:\n"
                + "\n".join(f"--- {k} ---\n{v}" for k, v in sources.items())
                + "\n\nPropose tonight's single experiment."
            ),
        }],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to propose")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def load_factories_from_source(lineup_src, evolved_name=None, evolved_src=None):
    """Write candidate sources into engine/ and import the candidate lineup."""
    if evolved_src and evolved_name:
        (HERE / "engine" / f"evolved_{evolved_name}.py").write_text(evolved_src)
    cand_path = HERE / "engine" / "_candidate_lineup.py"
    cand_path.write_text(lineup_src.replace("engine/lineup.py", "candidate lineup"))
    for mod in list(sys.modules):
        if mod.startswith("engine.evolved_") or mod == "engine._candidate_lineup":
            del sys.modules[mod]
    module = importlib.import_module("engine._candidate_lineup")
    return module.FACTORIES


def main():
    load_dotenv(HERE.parent / "ALPACA" / "apikeys.env")
    test_mode = "--test" in sys.argv

    if not test_mode and not os.getenv("ANTHROPIC_API_KEY"):
        log("no ANTHROPIC_API_KEY — evolution skipped")
        return

    if "--no-fetch" not in sys.argv:
        log("refreshing data...")
        import fetch
        for sym in dict.fromkeys(fetch.ETFS + UNIVERSE_FILE.read_text().split()):
            try:
                fetch.fetch(sym)
            except Exception as e:
                log(f"  fetch {sym} failed: {e}")

    universe = UNIVERSE_FILE.read_text().split()
    bars = load_universe(universe)
    regime = classify(bars["SPY"])
    dates = bars["SPY"].index

    from engine.lineup import FACTORIES as INCUMBENT
    inc = evaluate(bars, regime, dates, INCUMBENT, "incumbent")
    log(f"incumbent: {inc}")

    spy = bars["SPY"]["close"]
    market = (f"regime={regime.iloc[-1]}, SPY 1m {(spy.iloc[-1]/spy.iloc[-21]-1)*100:+.1f}%, "
              f"3m {(spy.iloc[-1]/spy.iloc[-63]-1)*100:+.1f}%, 1y {(spy.iloc[-1]/spy.iloc[-252]-1)*100:+.1f}%")

    if test_mode:
        proposal = {
            "name": "selftest_donchian_25_12",
            "rationale": "arena self-check: slightly slower momentum channel",
            "evolved_module": None,
            "lineup_module": (
                "from functools import partial\n"
                "from .strategies import mean_reversion_rsi2, momentum_donchian\n"
                "FACTORIES = [partial(momentum_donchian, entry_n=25, exit_n=12), mean_reversion_rsi2]\n"
            ),
        }
    else:
        proposal = propose(inc, market)
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in proposal["name"].lower())[:48]
    log(f"proposal: {name} — {proposal['rationale'][:200]}")

    try:
        cand_factories = load_factories_from_source(
            proposal["lineup_module"], name, proposal.get("evolved_module"))
        cand = evaluate(bars, regime, dates, cand_factories, "candidate")
    except Exception as e:
        cand, verdict, reason = None, "INVALID", f"candidate failed to run: {e}"
    else:
        folds_won = int(sum(c > i for c, i in zip(cand["folds"], inc["folds"])))
        gates = {
            "beats_cagr": bool(cand["cagr"] > inc["cagr"]),
            "beats_sharpe": bool(cand["sharpe"] >= inc["sharpe"]),
            "folds_won": folds_won,
            "folds_majority": folds_won >= (len(inc["folds"]) // 2 + 1),
            "enough_trades": cand["trades"] >= MIN_TRADES,
            "cooldown_ok": last_adoption_age_days() >= ADOPT_COOLDOWN_DAYS,
        }
        passed = all([gates["beats_cagr"], gates["beats_sharpe"],
                      gates["folds_majority"], gates["enough_trades"]])
        if passed and gates["cooldown_ok"]:
            verdict, reason = "ADOPTED", f"beat incumbent, {folds_won} folds won"
        elif passed:
            verdict, reason = "PASSED_COOLDOWN", "won the arena but adoption is rate-limited"
        else:
            verdict, reason = "REJECTED", json.dumps(gates)
        log(f"candidate: {cand}")
    log(f"verdict: {verdict} — {reason}")

    if verdict == "ADOPTED" and not test_mode:
        LINEUP_FILE.write_text(proposal["lineup_module"])
        log(f"lineup.py rewritten by evolution: {name}")
    else:
        # clean up candidate artifacts that didn't ship
        (HERE / "engine" / "_candidate_lineup.py").unlink(missing_ok=True)
        if proposal.get("evolved_module"):
            (HERE / "engine" / f"evolved_{name}.py").unlink(missing_ok=True)
    (HERE / "engine" / "_candidate_lineup.py").unlink(missing_ok=True)

    EVO_LOG.parent.mkdir(exist_ok=True)
    with open(EVO_LOG, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "name": name, "rationale": proposal["rationale"],
            "verdict": verdict, "reason": reason,
            "incumbent": inc, "candidate": cand,
        }) + "\n")
    log("evolution run complete")


if __name__ == "__main__":
    main()
