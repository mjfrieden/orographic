"""Concentration controls for historical replay candidate pools."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from engine.orographic.schemas import ContractCandidate


DEFAULT_SECTOR_MAP: dict[str, str] = {
    "SPY": "broad_index",
    "QQQ": "broad_index",
    "IWM": "broad_index",
    "DIA": "broad_index",
    "XLF": "financials",
    "XLK": "technology",
    "XLE": "energy",
    "XLI": "industrials",
    "XLV": "healthcare",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLU": "utilities",
    "SMH": "semiconductors",
    "TLT": "rates",
    "GLD": "commodities",
    "SLV": "commodities",
    "HYG": "credit",
    "USO": "commodities",
    "ARKK": "growth",
    "AAPL": "technology",
    "MSFT": "technology",
    "NVDA": "semiconductors",
    "GOOGL": "communication_services",
    "AMZN": "consumer_discretionary",
    "META": "communication_services",
    "TSLA": "consumer_discretionary",
    "BRK-B": "financials",
    "LLY": "healthcare",
    "AVGO": "semiconductors",
    "JPM": "financials",
    "UNH": "healthcare",
    "V": "financials",
    "XOM": "energy",
    "MA": "financials",
    "JNJ": "healthcare",
    "PG": "consumer_staples",
    "COST": "consumer_staples",
    "HD": "consumer_discretionary",
    "ABBV": "healthcare",
    "CVX": "energy",
    "CRM": "technology",
    "NFLX": "communication_services",
    "WMT": "consumer_staples",
    "KO": "consumer_staples",
    "BAC": "financials",
    "PEP": "consumer_staples",
    "IBM": "technology",
    "ORCL": "technology",
    "CSCO": "technology",
    "ACN": "technology",
    "ADBE": "technology",
    "QCOM": "semiconductors",
    "INTC": "semiconductors",
    "TXN": "semiconductors",
    "AMD": "semiconductors",
    "MU": "semiconductors",
    "AMAT": "semiconductors",
    "LRCX": "semiconductors",
    "KLAC": "semiconductors",
    "ANET": "technology",
    "NOW": "technology",
    "PANW": "technology",
    "CRWD": "technology",
    "PLTR": "technology",
    "UBER": "consumer_discretionary",
    "SHOP": "technology",
    "MCD": "consumer_discretionary",
    "DIS": "communication_services",
    "NKE": "consumer_discretionary",
    "BA": "industrials",
    "GS": "financials",
    "MS": "financials",
    "WFC": "financials",
    "C": "financials",
    "AXP": "financials",
    "PYPL": "financials",
    "COF": "financials",
    "BLK": "financials",
    "SCHW": "financials",
    "PFE": "healthcare",
    "MRK": "healthcare",
    "AMGN": "healthcare",
    "GILD": "healthcare",
    "TMO": "healthcare",
    "ISRG": "healthcare",
    "DHR": "healthcare",
    "T": "communication_services",
    "VZ": "communication_services",
    "TMUS": "communication_services",
    "CAT": "industrials",
    "DE": "industrials",
    "GE": "industrials",
    "HON": "industrials",
    "RTX": "industrials",
    "LMT": "industrials",
    "LOW": "consumer_discretionary",
    "SBUX": "consumer_discretionary",
    "CMG": "consumer_discretionary",
    "COP": "energy",
    "UPS": "industrials",
}


def sector_for_symbol(symbol: str) -> str:
    """Return a stable sector bucket for concentration caps."""
    return DEFAULT_SECTOR_MAP.get(symbol.upper(), "unknown")


def apply_candidate_concentration_caps(
    candidates: list[ContractCandidate],
    *,
    max_symbol_candidates: int | None = None,
    max_sector_candidates: int | None = None,
) -> tuple[list[ContractCandidate], dict[str, int]]:
    """Keep highest-ranked candidates while limiting symbol/sector clustering."""
    symbol_cap = max_symbol_candidates if max_symbol_candidates and max_symbol_candidates > 0 else None
    sector_cap = max_sector_candidates if max_sector_candidates and max_sector_candidates > 0 else None
    if symbol_cap is None and sector_cap is None:
        return list(candidates), {
            "kept": len(candidates),
            "dropped_symbol_cap": 0,
            "dropped_sector_cap": 0,
        }

    symbol_counts: dict[str, int] = defaultdict(int)
    sector_counts: dict[str, int] = defaultdict(int)
    kept: list[ContractCandidate] = []
    dropped_symbol = 0
    dropped_sector = 0

    for candidate in sorted(candidates, key=lambda row: row.forge_score, reverse=True):
        symbol = candidate.symbol.upper()
        sector = sector_for_symbol(symbol)
        if symbol_cap is not None and symbol_counts[symbol] >= symbol_cap:
            dropped_symbol += 1
            continue
        if sector_cap is not None and sector_counts[sector] >= sector_cap:
            dropped_sector += 1
            continue
        kept.append(
            replace(
                candidate,
                notes=[*candidate.notes, f"sector_bucket={sector}"],
            )
        )
        symbol_counts[symbol] += 1
        sector_counts[sector] += 1

    return kept, {
        "kept": len(kept),
        "dropped_symbol_cap": dropped_symbol,
        "dropped_sector_cap": dropped_sector,
    }
