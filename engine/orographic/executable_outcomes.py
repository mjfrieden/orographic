"""Versioned, leakage-safe executable outcome labels for long option trades.

The v1 contract deliberately requires both market quotes and any actual fills.
Quotes make execution quality observable; fills, when present, are the source of
truth for P&L.  In their absence the contract uses the executable side of the
market (entry ask and exit bid), never mid or last prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any


CONTRACT_NAME = "orographic.executable_option_outcome"
CONTRACT_VERSION = 2
CONTRACT_ID = f"{CONTRACT_NAME}.v{CONTRACT_VERSION}"


class OutcomeContractError(ValueError):
    """Raised when an outcome cannot be labeled without ambiguity or leakage."""


def _utc(value: datetime | str, field: str) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OutcomeContractError(f"{field} must be an ISO-8601 timestamp") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise OutcomeContractError(f"{field} must be a datetime or ISO-8601 string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OutcomeContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _positive(value: float, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OutcomeContractError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise OutcomeContractError(f"{field} must be finite and greater than zero")
    return number


def _nonnegative(value: float, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OutcomeContractError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise OutcomeContractError(f"{field} must be finite and nonnegative")
    return number


@dataclass(frozen=True)
class QuoteObservation:
    """A timestamped NBBO-like observation for one option contract."""

    bid: float
    ask: float
    observed_at_utc: datetime | str
    source: str = "unknown"


@dataclass(frozen=True)
class FillObservation:
    """An actual execution, exclusive of separately supplied fees."""

    price: float
    filled_at_utc: datetime | str
    execution_id: str | None = None


@dataclass(frozen=True)
class ExecutableOutcomeRequest:
    """Inputs required to create one long-option executable outcome label."""

    recommendation_id: str
    symbol: str
    contract_symbol: str
    decision_at_utc: datetime | str
    horizon: str
    horizon_target_at_utc: datetime | str
    label_available_at_utc: datetime | str
    entry_quote: QuoteObservation
    exit_quote: QuoteObservation
    entry_fill: FillObservation | None = None
    exit_fill: FillObservation | None = None
    contracts: int = 1
    contract_multiplier: float = 100.0
    entry_fees_usd: float = 0.0
    exit_fees_usd: float = 0.0
    max_entry_quote_age_seconds: float | None = None
    max_exit_capture_delay_seconds: float | None = None


def _validated_quote(quote: QuoteObservation, field: str) -> tuple[float, float, datetime]:
    bid = _nonnegative(quote.bid, f"{field}.bid")
    ask = _positive(quote.ask, f"{field}.ask")
    if bid > ask:
        raise OutcomeContractError(f"{field}.bid cannot exceed {field}.ask")
    return bid, ask, _utc(quote.observed_at_utc, f"{field}.observed_at_utc")


def build_executable_option_outcome(request: ExecutableOutcomeRequest) -> dict[str, Any]:
    """Build a deterministic v1 label, rejecting temporally invalid evidence.

    Slippage uses a signed adverse-cost convention: positive values are worse
    than the executable quote, while negative values represent price improvement.
    It is diagnostic only and is not subtracted again from fill-based P&L.
    """

    for field in ("recommendation_id", "symbol", "contract_symbol", "horizon"):
        if not str(getattr(request, field)).strip():
            raise OutcomeContractError(f"{field} must be non-empty")

    if isinstance(request.contracts, bool) or not isinstance(request.contracts, int) or request.contracts <= 0:
        raise OutcomeContractError("contracts must be a positive integer")
    multiplier = _positive(request.contract_multiplier, "contract_multiplier")
    entry_fees = _nonnegative(request.entry_fees_usd, "entry_fees_usd")
    exit_fees = _nonnegative(request.exit_fees_usd, "exit_fees_usd")

    decision_at = _utc(request.decision_at_utc, "decision_at_utc")
    horizon_target = _utc(request.horizon_target_at_utc, "horizon_target_at_utc")
    label_available_at = _utc(request.label_available_at_utc, "label_available_at_utc")
    entry_bid, entry_ask, entry_quote_at = _validated_quote(request.entry_quote, "entry_quote")
    exit_bid, exit_ask, exit_quote_at = _validated_quote(request.exit_quote, "exit_quote")

    if horizon_target <= decision_at:
        raise OutcomeContractError("horizon_target_at_utc must be after decision_at_utc")
    if entry_quote_at > decision_at:
        raise OutcomeContractError("entry quote cannot be observed after the decision")
    if exit_quote_at < horizon_target:
        raise OutcomeContractError("exit quote cannot be observed before the horizon target")
    if label_available_at < exit_quote_at:
        raise OutcomeContractError("label cannot be available before the exit quote")

    entry_quote_age = (decision_at - entry_quote_at).total_seconds()
    exit_capture_delay = (exit_quote_at - horizon_target).total_seconds()
    if request.max_entry_quote_age_seconds is not None:
        limit = _nonnegative(request.max_entry_quote_age_seconds, "max_entry_quote_age_seconds")
        if entry_quote_age > limit:
            raise OutcomeContractError("entry quote exceeds max_entry_quote_age_seconds")
    if request.max_exit_capture_delay_seconds is not None:
        limit = _nonnegative(request.max_exit_capture_delay_seconds, "max_exit_capture_delay_seconds")
        if exit_capture_delay > limit:
            raise OutcomeContractError("exit quote exceeds max_exit_capture_delay_seconds")

    entry_price = entry_ask
    entry_source = "entry_ask_proxy"
    entry_fill_at: datetime | None = None
    entry_execution_id: str | None = None
    if request.entry_fill is not None:
        entry_price = _positive(request.entry_fill.price, "entry_fill.price")
        entry_fill_at = _utc(request.entry_fill.filled_at_utc, "entry_fill.filled_at_utc")
        if entry_fill_at < decision_at:
            raise OutcomeContractError("entry fill cannot precede the decision")
        if entry_fill_at > label_available_at:
            raise OutcomeContractError("label cannot be available before the entry fill")
        entry_source = "actual_fill"
        entry_execution_id = request.entry_fill.execution_id

    exit_price = exit_bid
    exit_source = "exit_bid_proxy"
    exit_fill_at: datetime | None = None
    exit_execution_id: str | None = None
    if request.exit_fill is not None:
        exit_price = _positive(request.exit_fill.price, "exit_fill.price")
        exit_fill_at = _utc(request.exit_fill.filled_at_utc, "exit_fill.filled_at_utc")
        if exit_fill_at < horizon_target:
            raise OutcomeContractError("exit fill cannot precede the horizon target")
        if exit_fill_at < exit_quote_at:
            raise OutcomeContractError("exit fill cannot precede its reference quote")
        if exit_fill_at > label_available_at:
            raise OutcomeContractError("label cannot be available before the exit fill")
        exit_source = "actual_fill"
        exit_execution_id = request.exit_fill.execution_id

    entry_event_at = entry_fill_at or decision_at
    exit_event_at = exit_fill_at or exit_quote_at
    if exit_event_at < entry_event_at:
        raise OutcomeContractError("exit execution evidence cannot precede entry execution evidence")

    units = request.contracts * multiplier
    entry_mid = (entry_bid + entry_ask) / 2.0
    exit_mid = (exit_bid + exit_ask) / 2.0
    midpoint_cost_basis = entry_mid * units
    midpoint_counterfactual_pnl = (exit_mid - entry_mid) * units
    gross_pnl = (exit_price - entry_price) * units
    fees = entry_fees + exit_fees
    net_pnl = gross_pnl - fees
    cost_basis = entry_price * units + entry_fees
    entry_slippage = (entry_price - entry_ask) * units if request.entry_fill else 0.0
    exit_slippage = (exit_bid - exit_price) * units if request.exit_fill else 0.0

    return {
        "label_contract": {"id": CONTRACT_ID, "name": CONTRACT_NAME, "version": CONTRACT_VERSION},
        "recommendation_id": request.recommendation_id,
        "symbol": request.symbol,
        "contract_symbol": request.contract_symbol,
        "position_side": "long",
        "contracts": request.contracts,
        "contract_multiplier": multiplier,
        "decision_at_utc": _iso(decision_at),
        "horizon": {
            "name": request.horizon,
            "target_at_utc": _iso(horizon_target),
            "elapsed_seconds": (horizon_target - decision_at).total_seconds(),
        },
        "label_available_at_utc": _iso(label_available_at),
        "entry": {
            "quote": {
                "bid": entry_bid,
                "ask": entry_ask,
                "observed_at_utc": _iso(entry_quote_at),
                "age_at_decision_seconds": entry_quote_age,
                "source": request.entry_quote.source,
            },
            "execution_price": entry_price,
            "execution_price_source": entry_source,
            "fill_at_utc": _iso(entry_fill_at) if entry_fill_at else None,
            "quote_age_at_execution_seconds": (
                (entry_fill_at - entry_quote_at).total_seconds() if entry_fill_at else entry_quote_age
            ),
            "execution_id": entry_execution_id,
            "fees_usd": entry_fees,
            "signed_adverse_slippage_usd": entry_slippage,
        },
        "exit": {
            "quote": {
                "bid": exit_bid,
                "ask": exit_ask,
                "observed_at_utc": _iso(exit_quote_at),
                "capture_delay_seconds": exit_capture_delay,
                "age_at_label_availability_seconds": (label_available_at - exit_quote_at).total_seconds(),
                "source": request.exit_quote.source,
            },
            "execution_price": exit_price,
            "execution_price_source": exit_source,
            "fill_at_utc": _iso(exit_fill_at) if exit_fill_at else None,
            "quote_age_at_execution_seconds": (
                (exit_fill_at - exit_quote_at).total_seconds() if exit_fill_at else 0.0
            ),
            "execution_id": exit_execution_id,
            "fees_usd": exit_fees,
            "signed_adverse_slippage_usd": exit_slippage,
        },
        "gross_executable_pnl_usd": gross_pnl,
        "midpoint_counterfactual_cost_basis_usd": midpoint_cost_basis,
        "midpoint_counterfactual_pnl_usd": midpoint_counterfactual_pnl,
        "midpoint_counterfactual_return": (
            midpoint_counterfactual_pnl / midpoint_cost_basis if midpoint_cost_basis > 0 else None
        ),
        "total_execution_friction_usd": midpoint_counterfactual_pnl - net_pnl,
        "friction_flipped_winner_to_loser": midpoint_counterfactual_pnl > 0 and net_pnl <= 0,
        "total_fees_usd": fees,
        "net_executable_pnl_usd": net_pnl,
        "net_executable_return": net_pnl / cost_basis,
        "cost_basis_usd": cost_basis,
        "is_net_profitable": net_pnl > 0,
        "total_signed_adverse_slippage_usd": entry_slippage + exit_slippage,
    }
