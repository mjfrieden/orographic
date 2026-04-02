from __future__ import annotations

from collections import Counter

from .schemas import ContractCandidate, CouncilResult, MarketRegime


def select_board(
    candidates: list[ContractCandidate],
    regime: MarketRegime,
    *,
    live_size: int = 3,
    shadow_size: int = 3,
    minimum_live_score: float = 0.57,
    max_same_side_share: float = 0.67,
    max_live_extrinsic_ratio: float = 0.96,
) -> CouncilResult:
    live_board: list[ContractCandidate] = []
    shadow_board: list[ContractCandidate] = []
    seen_symbols: set[str] = set()
    notes: list[str] = []

    for candidate in candidates:
        if candidate.forge_score < minimum_live_score:
            continue
        if candidate.extrinsic_ratio > max_live_extrinsic_ratio:
            if candidate not in shadow_board and len(shadow_board) < shadow_size:
                shadow_board.append(candidate)
            continue
        if candidate.symbol in seen_symbols:
            continue

        projected_live = live_board + [candidate]
        side_counts = Counter(row.option_type for row in projected_live)
        same_side_share = max(side_counts.values()) / len(projected_live)
        if len(projected_live) > 1 and same_side_share > max_same_side_share:
            shadow_board.append(candidate)
            continue

        live_board.append(candidate)
        seen_symbols.add(candidate.symbol)
        if len(live_board) >= live_size:
            break

    for candidate in candidates:
        if len(shadow_board) >= shadow_size:
            break
        if candidate in live_board or candidate in shadow_board:
            continue
        shadow_board.append(candidate)

    if not live_board:
        notes.append("Council abstained because no contract cleared the live board threshold.")

    if regime.mode == "risk_off":
        notes.append("Council is operating under a risk-off market regime.")
    elif regime.mode == "risk_on":
        notes.append("Council is operating under a risk-on market regime.")
    else:
        notes.append("Council is operating under a neutral market regime.")

    summary = {
        "candidate_count": len(candidates),
        "live_count": len(live_board),
        "shadow_count": len(shadow_board),
        "regime_mode": regime.mode,
        "minimum_live_score": minimum_live_score,
        "notes": notes,
    }
    return CouncilResult(
        live_board=live_board,
        shadow_board=shadow_board,
        abstain=not bool(live_board),
        summary=summary,
    )
