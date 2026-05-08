from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine.orographic.event_feature_builders import (
    build_edt_daily_features,
    build_event_feature_store,
    build_fnspid_daily_features,
    build_mirai_daily_features,
    build_sec_filing_daily_features,
    build_stockemotions_daily_features,
)


class EventFeatureBuilderTests(unittest.TestCase):
    def test_build_fnspid_daily_features_aggregates_volume_sentiment_and_novelty(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "date": "2026-04-21 08:15:00",
                    "title": "Apple earnings beat expectations",
                    "sentiment": 0.8,
                },
                {
                    "ticker": "AAPL",
                    "date": "2026-04-21 09:15:00",
                    "title": "Apple earnings beat expectations",
                    "sentiment": 0.6,
                },
                {
                    "ticker": "AAPL",
                    "date": "2026-04-22 08:15:00",
                    "title": "Apple unveils new product contract",
                    "sentiment": "positive",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fnspid.csv"
            frame.to_csv(path, index=False)
            built = build_fnspid_daily_features(path)

        day_one = built.loc[built["date"] == pd.Timestamp("2026-04-21")].iloc[0]
        day_two = built.loc[built["date"] == pd.Timestamp("2026-04-22")].iloc[0]
        self.assertEqual(float(day_one["fnspid_news_volume_1d"]), 2.0)
        self.assertEqual(float(day_one["fnspid_news_volume_3d"]), 2.0)
        self.assertAlmostEqual(float(day_one["fnspid_sentiment_mean"]), 0.7, places=6)
        self.assertAlmostEqual(float(day_one["fnspid_novelty_score"]), 0.5, places=6)
        self.assertGreater(float(day_one["fnspid_catalyst_density"]), 0.0)
        self.assertEqual(float(day_two["fnspid_news_volume_3d"]), 3.0)

    def test_build_fnspid_daily_features_falls_back_to_filename_symbol(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "date": "2026-04-21 08:15:00",
                    "text": "Alcoa buyback plan boosts shares",
                    "sentiment": 0.7,
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aa.csv"
            frame.to_csv(path, index=False)
            built = build_fnspid_daily_features(path)

        row = built.iloc[0]
        self.assertEqual(row["symbol"], "AA")
        self.assertEqual(float(row["fnspid_news_volume_1d"]), 1.0)

    def test_build_edt_daily_features_maps_official_event_taxonomy(self) -> None:
        rows = [
            {
                "pub_time": "2026-04-21T10:00:00",
                "ticker": "PFE",
                "event_type": "CT",
            },
            {
                "pub_time": "2026-04-21T11:00:00",
                "ticker": "PFE",
                "event_type": "DI",
            },
            {
                "pub_time": "2026-04-22T09:00:00",
                "ticker": "MSFT",
                "event_type": "NC",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "edt.jsonl"
            pd.DataFrame(rows).to_json(path, orient="records", lines=True)
            built = build_edt_daily_features(path)

        pfe = built.loc[(built["symbol"] == "PFE") & (built["date"] == pd.Timestamp("2026-04-21"))].iloc[0]
        msft = built.loc[(built["symbol"] == "MSFT") & (built["date"] == pd.Timestamp("2026-04-22"))].iloc[0]
        self.assertEqual(float(pfe["edt_event_intensity"]), 2.0)
        self.assertEqual(float(pfe["edt_clinical_trial_score"]), 1.0)
        self.assertEqual(float(pfe["edt_dividend_score"]), 1.0)
        self.assertEqual(float(msft["edt_new_contract_score"]), 1.0)
        self.assertEqual(float(msft["edt_financing_score"]), 0.0)

    def test_build_event_feature_store_merges_fnspid_and_edt_rows(self) -> None:
        fnspid = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "date": "2026-04-21 08:15:00",
                    "title": "Apple earnings beat expectations",
                    "sentiment": 0.8,
                }
            ]
        )
        edt = pd.DataFrame(
            [
                {
                    "pub_time": "2026-04-21T10:00:00",
                    "ticker": "AAPL",
                    "event_type": "A",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            fnspid_path = Path(tmpdir) / "fnspid.csv"
            edt_path = Path(tmpdir) / "edt.jsonl"
            fnspid.to_csv(fnspid_path, index=False)
            edt.to_json(edt_path, orient="records", lines=True)
            built = build_event_feature_store(
                fnspid_inputs=[fnspid_path],
                edt_inputs=[edt_path],
            )

        row = built.loc[(built["symbol"] == "AAPL") & (built["date"] == pd.Timestamp("2026-04-21"))].iloc[0]
        self.assertEqual(float(row["fnspid_news_volume_1d"]), 1.0)
        self.assertEqual(float(row["edt_acquisition_score"]), 1.0)
        self.assertEqual(row["dataset_tags"], "fnspid,edt")

    def test_build_mirai_daily_features_emits_global_macro_overlay_rows(self) -> None:
        rows = [
            {
                "date": "2026-04-21T05:00:00",
                "headline": "Oil prices jump after missile attack and new sanctions",
                "relation_text": "military conflict",
            },
            {
                "date": "2026-04-22T05:00:00",
                "headline": "Ceasefire agreement supports diplomatic progress",
                "relation_text": "peace talks",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mirai.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            built = build_mirai_daily_features(path)

        risk_off = built.loc[built["date"] == pd.Timestamp("2026-04-21")].iloc[0]
        risk_on = built.loc[built["date"] == pd.Timestamp("2026-04-22")].iloc[0]
        self.assertEqual(risk_off["symbol"], "__GLOBAL__")
        self.assertGreater(float(risk_off["mirai_risk_off_score"]), 0.0)
        self.assertGreater(float(risk_off["mirai_commodity_risk_score"]), 0.0)
        self.assertGreater(float(risk_off["mirai_macro_shock_score"]), 0.0)
        self.assertGreater(float(risk_on["mirai_risk_on_score"]), 0.0)

    def test_build_stockemotions_daily_features_aggregates_message_ratios(self) -> None:
        rows = [
            {
                "ticker": "TSLA",
                "date": "2026-04-21",
                "senti_label": "bullish",
                "emo_label": "excitement",
            },
            {
                "ticker": "TSLA",
                "date": "2026-04-21",
                "senti_label": "bearish",
                "emo_label": "panic",
            },
            {
                "ticker": "TSLA",
                "date": "2026-04-21",
                "senti_label": "bullish",
                "emo_label": "optimism",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stockemotions.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            built = build_stockemotions_daily_features(path)

        row = built.iloc[0]
        self.assertEqual(float(row["stocktwits_message_count"]), 3.0)
        self.assertAlmostEqual(float(row["stocktwits_bullish_ratio"]), 2 / 3, places=6)
        self.assertAlmostEqual(float(row["stocktwits_bearish_ratio"]), 1 / 3, places=6)
        self.assertGreater(float(row["stocktwits_emotion_intensity"]), 0.0)

    def test_build_event_feature_store_merges_global_macro_and_symbol_retail_rows(self) -> None:
        mirai = pd.DataFrame(
            [
                {
                    "date": "2026-04-21T05:00:00",
                    "headline": "Oil prices jump after missile attack and new sanctions",
                }
            ]
        )
        stock = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "date": "2026-04-21",
                    "senti_label": "bullish",
                    "emo_label": "optimism",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            mirai_path = Path(tmpdir) / "mirai.csv"
            stock_path = Path(tmpdir) / "stock.csv"
            mirai.to_csv(mirai_path, index=False)
            stock.to_csv(stock_path, index=False)
            built = build_event_feature_store(
                mirai_inputs=[mirai_path],
                stockemotions_inputs=[stock_path],
            )

        macro_row = built.loc[built["symbol"] == "__GLOBAL__"].iloc[0]
        stock_row = built.loc[built["symbol"] == "AAPL"].iloc[0]
        self.assertGreater(float(macro_row["mirai_macro_shock_score"]), 0.0)
        self.assertEqual(float(stock_row["stocktwits_message_count"]), 1.0)

    def test_build_sec_filing_daily_features_aggregates_form_buckets(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-21T12:30:00Z",
                "form": "8-K",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-21T18:15:00Z",
                "form": "4",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-22T10:00:00Z",
                "form": "10-Q/A",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-22T13:00:00Z",
                "form": "424B2",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-22T13:30:00Z",
                "form": "424B5",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-22T14:00:00Z",
                "form": "SCHEDULE 13G/A",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sec.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            built = build_sec_filing_daily_features(path)

        first = built.loc[built["date"] == pd.Timestamp("2026-04-21")].iloc[0]
        second = built.loc[built["date"] == pd.Timestamp("2026-04-22")].iloc[0]
        self.assertEqual(float(first["sec_filing_count_1d"]), 2.0)
        self.assertEqual(float(first["sec_8k_count"]), 1.0)
        self.assertEqual(float(first["sec_8k_flag"]), 1.0)
        self.assertEqual(float(first["sec_signal_count_1d"]), 1.0)
        self.assertEqual(float(first["sec_insider_count"]), 1.0)
        self.assertEqual(float(second["sec_10q_count"]), 1.0)
        self.assertEqual(float(second["sec_amendment_count"]), 2.0)
        self.assertEqual(float(second["sec_capital_markets_count"]), 2.0)
        self.assertEqual(float(second["sec_debt_markets_count"]), 1.0)
        self.assertEqual(float(second["sec_fwp_count"]), 0.0)
        self.assertEqual(float(second["sec_capital_markets_noise_count"]), 1.0)
        self.assertEqual(float(second["sec_offering_count"]), 1.0)
        self.assertEqual(float(second["sec_ownership_count"]), 1.0)
        self.assertGreater(float(second["sec_noise_count_1d"]), 0.0)
        self.assertEqual(float(second["sec_signal_count_1d"]), 2.0)
        self.assertGreaterEqual(float(second["sec_signal_count_5d"]), 3.0)
        self.assertLess(float(second["sec_signal_ratio"]), 1.0)
        self.assertEqual(float(second["sec_filing_count_5d"]), 6.0)

    def test_build_sec_filing_daily_features_supports_custom_8k_weight(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-21T12:30:00Z",
                "form": "8-K",
            },
            {
                "symbol": "AAPL",
                "acceptance_datetime": "2026-04-21T13:00:00Z",
                "form": "10-Q",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sec_weighted.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            built = build_sec_filing_daily_features(path, sec_8k_weight=1.5)

        row = built.iloc[0]
        self.assertEqual(float(row["sec_8k_flag"]), 1.0)
        self.assertEqual(float(row["sec_10q_flag"]), 1.0)
        self.assertAlmostEqual(float(row["sec_material_event_score"]), 2.75, places=6)


if __name__ == "__main__":
    unittest.main()
