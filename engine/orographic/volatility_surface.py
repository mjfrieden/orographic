from __future__ import annotations

from datetime import date
import math
from statistics import median
from typing import Any

import numpy as np
import pandas as pd


def _clean_side(frame: pd.DataFrame, option_type: str, spot: float) -> pd.DataFrame:
    if frame.empty or spot <= 0:
        return pd.DataFrame(columns=["strike", "iv", "log_moneyness", "option_type"])
    cleaned = pd.DataFrame(index=frame.index)
    cleaned["strike"] = pd.to_numeric(frame.get("strike"), errors="coerce")
    cleaned["iv"] = pd.to_numeric(frame.get("impliedVolatility"), errors="coerce")
    cleaned = cleaned.dropna(subset=["strike", "iv"])
    cleaned = cleaned[(cleaned["strike"] > 0) & cleaned["iv"].between(0.03, 5.0)].copy()
    if cleaned.empty:
        return cleaned.assign(log_moneyness=pd.Series(dtype=float), option_type=option_type)
    cleaned["log_moneyness"] = np.log(cleaned["strike"] / float(spot))
    cleaned["option_type"] = option_type
    return cleaned[cleaned["log_moneyness"].abs() <= 0.20].copy()


def _nearest_atm_iv(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    nearest = frame.assign(distance=frame["log_moneyness"].abs()).nsmallest(2, "distance")
    values = [float(value) for value in nearest["iv"] if math.isfinite(float(value))]
    return median(values) if values else None


def _wing_iv(frame: pd.DataFrame, low: float, high: float) -> float | None:
    wing = frame[frame["log_moneyness"].between(low, high)]
    values = [float(value) for value in wing["iv"] if math.isfinite(float(value))]
    return median(values) if values else None


def compute_expiry_surface(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    *,
    spot: float,
    expiry: str,
    as_of: date,
) -> dict[str, Any]:
    """Fit a point-in-time quadratic smile from liquidly quoted chain rows.

    This is observational telemetry. It does not modify Forge scores.
    """
    call_rows = _clean_side(calls, "call", spot)
    put_rows = _clean_side(puts, "put", spot)
    call_atm = _nearest_atm_iv(call_rows)
    put_atm = _nearest_atm_iv(put_rows)
    atm_values = [value for value in (call_atm, put_atm) if value is not None]
    atm_iv = median(atm_values) if atm_values else None

    # Use out-of-the-money wings to avoid duplicating economically equivalent
    # ITM quotes and to make the smile definition stable across option sides.
    smile = pd.concat(
        [
            call_rows[call_rows["log_moneyness"] >= -0.005],
            put_rows[put_rows["log_moneyness"] <= 0.005],
        ],
        ignore_index=True,
    ).drop_duplicates(subset=["option_type", "strike"])
    slope = curvature = fit_rmse = None
    if len(smile) >= 5 and smile["log_moneyness"].nunique() >= 3:
        x = smile["log_moneyness"].to_numpy(dtype=float)
        y = smile["iv"].to_numpy(dtype=float)
        quadratic, linear, intercept = np.polyfit(x, y, 2)
        fitted = quadratic * x * x + linear * x + intercept
        slope = float(linear)
        curvature = float(2.0 * quadratic)
        fit_rmse = float(np.sqrt(np.mean((y - fitted) ** 2)))

    put_wing = _wing_iv(put_rows, -0.08, -0.03)
    call_wing = _wing_iv(call_rows, 0.03, 0.08)
    try:
        dte = max((date.fromisoformat(expiry) - as_of).days, 0)
    except (TypeError, ValueError):
        dte = None
    return {
        "expiry": expiry,
        "days_to_expiry": dte,
        "atm_iv": round(atm_iv, 6) if atm_iv is not None else None,
        "skew_slope": round(slope, 6) if slope is not None else None,
        "curvature": round(curvature, 6) if curvature is not None else None,
        "put_call_wing_skew": (
            round(put_wing - call_wing, 6)
            if put_wing is not None and call_wing is not None
            else None
        ),
        "fit_rmse": round(fit_rmse, 6) if fit_rmse is not None else None,
        "observation_count": int(len(smile)),
        "call_observation_count": int(len(call_rows)),
        "put_observation_count": int(len(put_rows)),
        "definition": "quadratic IV fit over OTM call/put log-moneyness within +/-20%; wing skew is 3-8% OTM put IV minus call IV",
    }


def compute_term_structure_slope(
    surfaces: dict[str, dict[str, Any]],
    selected_expiry: str,
) -> float | None:
    selected = surfaces.get(selected_expiry) or {}
    selected_iv = selected.get("atm_iv")
    selected_dte = selected.get("days_to_expiry")
    if selected_iv is None or selected_dte is None:
        return None
    later = sorted(
        (
            (int(surface["days_to_expiry"]), float(surface["atm_iv"]))
            for expiry, surface in surfaces.items()
            if expiry != selected_expiry
            and surface.get("atm_iv") is not None
            and surface.get("days_to_expiry") is not None
            and int(surface["days_to_expiry"]) > int(selected_dte)
        ),
        key=lambda item: item[0],
    )
    if not later:
        return None
    later_dte, later_iv = later[0]
    day_gap = later_dte - int(selected_dte)
    return round((later_iv - float(selected_iv)) * 30.0 / day_gap, 6) if day_gap > 0 else None
