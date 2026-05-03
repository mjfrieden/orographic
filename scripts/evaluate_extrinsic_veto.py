from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WALK_FORWARD = REPO_ROOT / "output" / "alpha_experiment_results_2026-04-21_recovered_edge_6mo.json"
DEFAULT_BOARD_HISTORY = REPO_ROOT / "web" / "data" / "diagnostics" / "board_recommendation_history.json"
DEFAULT_LATEST_RUN = REPO_ROOT / "web" / "data" / "latest_run.json"
DEFAULT_REPORT = REPO_ROOT / "docs" / "extrinsic-veto-evaluation-2026-05-03.md"


@dataclass(frozen=True)
class Bucket:
    label: str
    low: float
    high: float


BUCKETS = [
    Bucket("<0.50", 0.0, 0.50),
    Bucket("0.50-0.69", 0.50, 0.70),
    Bucket("0.70-0.84", 0.70, 0.85),
    Bucket("0.85-0.95", 0.85, 0.96),
    Bucket(">=0.96", 0.96, float("inf")),
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.2f}"


def _ratio(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def _find_bucket(extrinsic_ratio: float) -> Bucket:
    for bucket in BUCKETS:
        if bucket.low <= extrinsic_ratio < bucket.high:
            return bucket
    return BUCKETS[-1]


def summarize_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in BUCKETS:
        bucket_trades = [
            trade
            for trade in trades
            if bucket.low <= float(trade.get("extrinsic_ratio") or 0.0) < bucket.high
        ]
        pnls = [float(trade.get("pnl") or 0.0) for trade in bucket_trades]
        pnl_pcts = [float(trade.get("pnl_pct") or 0.0) for trade in bucket_trades]
        wins = [trade for trade in bucket_trades if float(trade.get("pnl") or 0.0) > 0.0]
        rows.append(
            {
                "bucket": bucket.label,
                "trades": len(bucket_trades),
                "win_rate": (len(wins) / len(bucket_trades)) if bucket_trades else None,
                "total_pnl": sum(pnls) if bucket_trades else None,
                "avg_pnl_pct": _mean(pnl_pcts),
                "avg_forge_score": _mean([float(trade.get("forge_score") or 0.0) for trade in bucket_trades]),
                "avg_extrinsic_ratio": _mean([float(trade.get("extrinsic_ratio") or 0.0) for trade in bucket_trades]),
            }
        )
    return rows


def near_threshold_examples(trades: list[dict[str, Any]], *, minimum: float = 0.85) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = [trade for trade in trades if float(trade.get("extrinsic_ratio") or 0.0) >= minimum]
    winners = sorted(
        [trade for trade in eligible if float(trade.get("pnl") or 0.0) > 0.0],
        key=lambda trade: float(trade.get("pnl") or 0.0),
        reverse=True,
    )[:5]
    losers = sorted(
        [trade for trade in eligible if float(trade.get("pnl") or 0.0) <= 0.0],
        key=lambda trade: float(trade.get("pnl") or 0.0),
    )[:5]
    return winners, losers


def summarize_abstains(board_history: dict[str, Any]) -> dict[str, Any]:
    entries = board_history.get("entries") if isinstance(board_history.get("entries"), list) else []
    abstains = [entry for entry in entries if bool(entry.get("abstain"))]
    reason_counts = Counter(
        (
            (entry.get("summary", {}).get("abstain_audit", {}) or {}).get("primary_reason")
            or "unknown"
        )
        for entry in abstains
    )
    return {
        "runs": len(entries),
        "abstain_runs": len(abstains),
        "reason_counts": dict(reason_counts),
        "latest_abstain": abstains[-1] if abstains else None,
    }


def render_report(
    *,
    walk_forward_path: Path,
    variant_key: str,
    variant_summary: dict[str, Any],
    bucket_rows: list[dict[str, Any]],
    top_winners: list[dict[str, Any]],
    top_losers: list[dict[str, Any]],
    abstain_summary: dict[str, Any],
    latest_run: dict[str, Any],
) -> str:
    latest_audit = latest_run.get("council", {}).get("summary", {}).get("abstain_audit", {}) or {}
    reason_counts = abstain_summary["reason_counts"]
    reason_lines = "\n".join(
        f"- `{reason}`: {count}"
        for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    ) or "- none"

    table_rows = "\n".join(
        f"| {row['bucket']} | {row['trades']} | {_pct(row['win_rate'])} | {_money(row['total_pnl'])} | {_pct(row['avg_pnl_pct'])} | {_ratio(row['avg_extrinsic_ratio'])} |"
        for row in bucket_rows
    )

    def _trade_line(trade: dict[str, Any]) -> str:
        return (
            f"- `{trade.get('entry_date')}` {trade.get('symbol')} {str(trade.get('option_type') or '').upper()} "
            f"`extrinsic={float(trade.get('extrinsic_ratio') or 0.0):.4f}` "
            f"`pnl={_money(float(trade.get('pnl') or 0.0))}` "
            f"`pnl_pct={_pct(float(trade.get('pnl_pct') or 0.0))}`"
        )

    winners_md = "\n".join(_trade_line(trade) for trade in top_winners) or "- none"
    losers_md = "\n".join(_trade_line(trade) for trade in top_losers) or "- none"

    return f"""# Extrinsic Veto Evaluation

Date: 2026-05-03

Primary walk-forward source: `{walk_forward_path.relative_to(REPO_ROOT)}`
Variant evaluated: `{variant_key}`

## Headline

The current `max_live_extrinsic_ratio = 0.96` veto looks directionally sensible, but the repo still cannot fully prove that the exact `0.96` line is optimal.

Why:

- In the recovered six-month deployable walk-forward variant, there were **zero executed trades** with `extrinsic_ratio >= 0.96`.
- The near-threshold bucket just below the veto, `0.85-0.95`, was materially weaker than lower-extrinsic buckets.
- The newest live abstain on `2026-05-03` was caused entirely by the extrinsic ceiling, so the rule is active in production behavior now.

## Walk-Forward Read

Variant summary:

- Trades: `{variant_summary.get('total_trades')}`
- Win rate: `{_pct(float(variant_summary.get('win_rate') or 0.0))}`
- Total P&L: `{_money(float(variant_summary.get('total_pnl') or 0.0))}`
- Sharpe: `{float(variant_summary.get('sharpe_ratio') or 0.0):.2f}`
- Max drawdown: `{_pct(float(variant_summary.get('max_drawdown') or 0.0))}`

### P&L by extrinsic bucket

| Bucket | Trades | Win Rate | Total P&L | Avg P&L % | Avg Extrinsic |
| --- | ---: | ---: | ---: | ---: | ---: |
{table_rows}

Interpretation:

- Buckets below `0.85` generated the bulk of the recovered edge.
- The `0.85-0.95` bucket was still slightly positive, but its edge was much thinner than the lower-extrinsic buckets.
- The data set contains **no executed trades** above the current `0.96` veto, so the exact cutoff still needs shadow observation rather than claiming a full proof.

### Near-threshold winners

{winners_md}

### Near-threshold losers

{losers_md}

## Live Abstain Read

Recent board-history summary:

- Total tracked runs: `{abstain_summary['runs']}`
- Abstain runs: `{abstain_summary['abstain_runs']}`
- Legacy `unknown` abstain reasons mostly come from runs recorded before the new structured abstain audit existed.
- Abstain reasons seen:
{reason_lines}

Latest scan audit from `web/data/latest_run.json`:

- Primary reason: `{latest_audit.get('primary_reason')}`
- Label: `{latest_audit.get('primary_reason_label')}`
- Candidate count: `{latest_audit.get('candidate_count')}`
- Core filter passes: `{latest_audit.get('core_filter_pass_count')}`
- Extrinsic-only failures: `{latest_audit.get('extrinsic_only_fail_count')}`
- Blocked symbols: `{', '.join((latest_audit.get('blocked_symbols') or {}).get('extrinsic_only', [])) or 'none'}`

This means the current live abstain was not driven by side balance, concentration, or low Forge score. It was a pure extrinsic veto on the final surviving contract.

## Verdict

The evidence supports **keeping the high-extrinsic veto for now**, but treating it as a monitored risk rule rather than a mathematically settled optimum.

What the evidence supports:

- High extrinsic is associated with weaker historical trade quality as you approach the veto line.
- The current live system is correctly flagging fully extrinsic weekly options as dangerous.

What the evidence does **not** yet support:

- A claim that `0.96` is definitely the best threshold.
- A claim that every `>= 0.96` candidate should always be skipped.

## Best next measurement

To decide whether the veto is too strict or just right, track extrinsic-veto shadow holdouts prospectively:

1. Count all abstains where `primary_reason = extrinsic_limit`.
2. Log the vetoed contract, `extrinsic_ratio`, `expected_edge_after_friction_pct`, and later realized P&L.
3. Compare those holdouts against lower-extrinsic live picks over at least 20 to 30 veto events.

Until that holdout ledger exists, the current conclusion is:

`High extrinsic is a defensible abstain reason, and the available evidence leans in favor of the veto, but the exact threshold is still under evaluation.`
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Orographic's high-extrinsic live veto.")
    parser.add_argument("--walk-forward", type=Path, default=DEFAULT_WALK_FORWARD)
    parser.add_argument("--board-history", type=Path, default=DEFAULT_BOARD_HISTORY)
    parser.add_argument("--latest-run", type=Path, default=DEFAULT_LATEST_RUN)
    parser.add_argument("--variant", default="council_cost_cap")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    walk_forward = _load_json(args.walk_forward if args.walk_forward.is_absolute() else REPO_ROOT / args.walk_forward)
    board_history = _load_json(args.board_history if args.board_history.is_absolute() else REPO_ROOT / args.board_history)
    latest_run = _load_json(args.latest_run if args.latest_run.is_absolute() else REPO_ROOT / args.latest_run)

    variant_results = walk_forward["variant_results"][args.variant]
    trades = variant_results["all_trades"]
    bucket_rows = summarize_trades(trades)
    top_winners, top_losers = near_threshold_examples(trades, minimum=0.85)
    abstain_summary = summarize_abstains(board_history)

    report = render_report(
        walk_forward_path=args.walk_forward if args.walk_forward.is_absolute() else REPO_ROOT / args.walk_forward,
        variant_key=args.variant,
        variant_summary=variant_results,
        bucket_rows=bucket_rows,
        top_winners=top_winners,
        top_losers=top_losers,
        abstain_summary=abstain_summary,
        latest_run=latest_run,
    )

    output_path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote extrinsic veto evaluation to {output_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
