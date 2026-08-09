"""Leakage-resistant validation utilities for time-dependent market labels."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit


def purged_date_splits(
    feature_dates: Sequence[object] | np.ndarray,
    label_dates: Sequence[object] | np.ndarray | None = None,
    *,
    n_splits: int = 5,
) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """Yield walk-forward row indices grouped by date and purged by outcome date.

    Every observation from a market date stays in the same fold. A training row
    is retained only when its label was fully observable before the first
    validation feature date, preventing overlapping option exits or forward-
    return windows from leaking into model selection.
    """
    features = pd.to_datetime(pd.Series(feature_dates), errors="coerce").dt.normalize()
    labels = (
        features.copy()
        if label_dates is None
        else pd.to_datetime(pd.Series(label_dates), errors="coerce").dt.normalize()
    )
    if len(features) != len(labels):
        raise ValueError("feature_dates and label_dates must have the same length")

    valid_feature_dates = features.dropna()
    unique_dates = np.array(sorted(valid_feature_dates.unique()))
    if len(unique_dates) < 3:
        return
    effective_splits = min(max(int(n_splits), 2), len(unique_dates) - 1)
    splitter = TimeSeriesSplit(n_splits=effective_splits)

    for train_date_idx, validation_date_idx in splitter.split(unique_dates):
        train_dates = unique_dates[train_date_idx]
        validation_dates = unique_dates[validation_date_idx]
        validation_start = pd.Timestamp(validation_dates[0])
        train_mask = features.isin(train_dates) & labels.notna() & (labels < validation_start)
        validation_mask = features.isin(validation_dates) & labels.notna()
        train_idx = np.flatnonzero(train_mask.to_numpy())
        validation_idx = np.flatnonzero(validation_mask.to_numpy())
        if len(train_idx) and len(validation_idx):
            yield train_idx, validation_idx
