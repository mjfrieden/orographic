from __future__ import annotations

import argparse
from pathlib import Path

from engine.orographic.event_feature_builders import (
    build_event_feature_store,
    save_event_feature_store,
)
from engine.orographic.event_features import DEFAULT_EVENT_FEATURES_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Orographic's canonical daily event-feature store from FNSPID and EDT-style raw datasets."
    )
    parser.add_argument(
        "--fnspid-input",
        action="append",
        type=Path,
        default=None,
        help="Raw FNSPID-style news file (.csv/.json/.jsonl/.parquet). May be repeated.",
    )
    parser.add_argument(
        "--edt-input",
        action="append",
        type=Path,
        default=None,
        help="Raw EDT-style event file (.csv/.json/.jsonl/.parquet). May be repeated.",
    )
    parser.add_argument(
        "--mirai-input",
        action="append",
        type=Path,
        default=None,
        help="Raw MIRAI/GDELT-style macro event file (.csv/.json/.jsonl/.parquet). May be repeated.",
    )
    parser.add_argument(
        "--sec-input",
        action="append",
        type=Path,
        default=None,
        help="Raw SEC filings event file (.csv/.json/.jsonl/.parquet). May be repeated.",
    )
    parser.add_argument(
        "--sec-8k-weight",
        type=float,
        default=1.0,
        help="Optional weight for SEC 8-K flags inside sec_material_event_score. Default: 1.0",
    )
    parser.add_argument(
        "--stockemotions-input",
        action="append",
        type=Path,
        default=None,
        help="Raw StockEmotions/StockTwits-style file (.csv/.json/.jsonl/.parquet). May be repeated.",
    )
    parser.add_argument(
        "--observatory-input",
        action="append",
        type=Path,
        default=None,
        help="Normalized Event Observatory file. May be repeated.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EVENT_FEATURES_PATH,
        help="Output path for the canonical event-feature store.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Do not merge with an existing output file even if one already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    merge_existing_path = None if args.replace else args.output
    frame = build_event_feature_store(
        fnspid_inputs=args.fnspid_input or [],
        edt_inputs=args.edt_input or [],
        mirai_inputs=args.mirai_input or [],
        sec_inputs=args.sec_input or [],
        sec_8k_weight=args.sec_8k_weight,
        stockemotions_inputs=args.stockemotions_input or [],
        observatory_inputs=args.observatory_input or [],
        merge_existing_path=merge_existing_path,
    )
    save_event_feature_store(frame, args.output)
    print(f"Saved canonical event-feature store -> {args.output}")
    print(f"Rows: {len(frame)}")
    print(f"Symbols: {int(frame['symbol'].nunique()) if not frame.empty else 0}")


if __name__ == "__main__":
    main()
