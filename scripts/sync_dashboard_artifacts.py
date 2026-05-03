from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "output"
WEB_DATA_DIR = REPO_ROOT / "web" / "data"
WALK_FORWARD_TARGET = WEB_DATA_DIR / "walk_forward_results.json"
BACKTEST_TARGET = WEB_DATA_DIR / "backtest_results.json"

VARIANT_LABELS = {
    "baseline_all_candidates": "All Forge Candidates",
    "council_only": "Council Only",
    "council_cost_cap": "Council + Cost Cap",
    "council_cost_cap_symbol_priors": "Council + Cost Cap + Symbol Priors",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _generated_at_ordinal(payload: dict[str, Any]) -> int:
    raw = str(payload.get("generated_at") or "").strip()
    try:
        return date.fromisoformat(raw).toordinal()
    except ValueError:
        return 0


def _preferred_backtest(path: Path, payload: dict[str, Any]) -> tuple[int, int, int, str]:
    name = path.name
    fully_real = payload.get("options_data_coverage", {}).get("fully_real_trade_pct")
    real_score = 1 if fully_real == 1.0 else 0
    strict_score = 1 if "strict_real" in name else 0
    non_smoke_score = 1 if "smoke" not in name else 0
    execution_stress_score = 1 if "execution_stress" in name else 0
    months_match = re.search(r"_(\d+)mo", name)
    months_score = int(months_match.group(1)) if months_match else 0
    return (
        non_smoke_score,
        execution_stress_score,
        months_score,
        _generated_at_ordinal(payload),
        real_score,
        strict_score,
        name,
    )


def _choose_latest_walk_forward() -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(OUTPUT_DIR.glob("alpha_experiment_results_*.json")):
        payload = _load_json(path)
        if not isinstance(payload.get("variant_results"), dict):
            continue
        variant_key = str(payload.get("recommended_default_variant") or "council_cost_cap").strip()
        if not variant_key or variant_key not in payload["variant_results"]:
            continue
        candidates.append((path, payload))
    if not candidates:
        raise FileNotFoundError("No canonical walk-forward artifact found in output/.")
    return max(candidates, key=lambda item: (_generated_at_ordinal(item[1]), item[0].name))


def _choose_latest_backtest() -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(OUTPUT_DIR.glob("backtest_results_*.json")):
        payload = _load_json(path)
        if int(payload.get("total_trades") or 0) <= 0:
            continue
        candidates.append((path, payload))
    if not candidates:
        raise FileNotFoundError("No canonical backtest artifact with trades found in output/.")
    return max(candidates, key=lambda item: _preferred_backtest(item[0], item[1]))


def _materialize_walk_forward(payload: dict[str, Any]) -> dict[str, Any]:
    variant_key = str(payload.get("recommended_default_variant") or "council_cost_cap").strip()
    variant_result = deepcopy(payload["variant_results"][variant_key])
    variant_label = str(
        payload.get("recommended_default_variant_label")
        or VARIANT_LABELS.get(variant_key, variant_key.replace("_", " ").title())
    )
    return {
        **variant_result,
        "study_type": "walk_forward",
        "study_kind": "walk_forward",
        "study_label": "Walk-Forward Validation",
        "variant_key": variant_key,
        "variant_label": variant_label,
        "symbols_count": len(payload.get("symbols") or []),
        "months": payload.get("months"),
        "config": payload.get("config", {}),
        "recommended_default_variant": variant_key,
        "recommended_default_variant_label": variant_label,
        "experimental_variants": payload.get("experimental_variants", []),
        "variant_summaries": payload.get("variant_summaries", {}),
    }


def _materialize_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    rendered = deepcopy(payload)
    rendered.setdefault("study_type", "backtest")
    rendered.setdefault("study_kind", "backtest")
    rendered.setdefault("study_label", "Historical Performance")
    return rendered


def main() -> int:
    walk_forward_path, walk_forward_raw = _choose_latest_walk_forward()
    backtest_path, backtest_raw = _choose_latest_backtest()

    walk_forward_rendered = _materialize_walk_forward(walk_forward_raw)
    backtest_rendered = _materialize_backtest(backtest_raw)

    _write_json(WALK_FORWARD_TARGET, walk_forward_rendered)
    _write_json(BACKTEST_TARGET, backtest_rendered)

    print(f"Synced walk-forward dashboard artifact from {walk_forward_path.relative_to(REPO_ROOT)}")
    print(
        "  variant:",
        walk_forward_rendered.get("variant_key"),
        "generated_at:",
        walk_forward_rendered.get("generated_at"),
    )
    print(f"Synced backtest dashboard artifact from {backtest_path.relative_to(REPO_ROOT)}")
    print(
        "  trades:",
        backtest_rendered.get("total_trades"),
        "generated_at:",
        backtest_rendered.get("generated_at"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
