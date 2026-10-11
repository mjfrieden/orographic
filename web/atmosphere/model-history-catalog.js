// Reviewed repository evidence only. Update through a source-verified code review.
export const MODEL_HISTORY = {
  "schema_version": 1,
  "repository": "mjfrieden/orographic",
  "reviewed_revision": "f9c6cc593ab7987774d8c249d37aab43d0e74a0d",
  "reviewed_at_utc": "2026-10-11T17:50:43Z",
  "source_path": "engine/orographic/models/artifact_manifest.json",
  "coverage": "Eight retained manifest revisions reviewed through the pinned repository revision. Earlier models, changes outside the manifest, and actual served deployment times are not established. This catalog is manually reviewed; refreshing scans does not extend it.",
  "entries": [
    {
      "commit": "a291aa090979ae0873c922bc50ddfc5118648bb0",
      "committed_at": "2026-08-30T19:26:10Z",
      "title": "feat: promote active tail-utility ranker",
      "manifest_blob": "5937a345b50d5e1397ff12540b35f832525b9e0f",
      "manifest_sha256": "ec35522e63a47a467a151f6c5ab641f0260016ced381c1fe6f0562c1e0365b4f",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "d38cc7c392fca619a19c38d57c040bfa4ea6e972cb714e0d4d6ee8bd055483ca",
          "role": "production directional Scout",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "f867df30e5cadf84b8fd844a53abff4255b82b54ddd6d7e68e9e529e667afaba",
          "role": "production Scout preprocessing and calibration",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "bb097b2db11d5de3b124a98cc9dbb8537910cb9a2589f9d8ef815f6802ed53f8",
          "role": "production trade-abstain and side decision",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "production_payoff_ranker": {
          "path": "engine/orographic/models/production_payoff_ranker.pkl",
          "sha256": "fe95801fd2aebed04f20d25d15ee1e49a6f216aaa578739573c3d72defaf8410",
          "role": "sole active tail-utility production contract ranker",
          "required": true,
          "card_version": 2,
          "trained_at": "2026-08-30T19:22:53.794682+00:00",
          "card_path": "engine/orographic/models/production_payoff_ranker_card.json",
          "card_sha256": "e7491c828521cd7bceb37965e8c2b13fb08f8d2ea3b55d839ce8080386c8934c"
        }
      },
      "policy": {
        "profile": "production_v2",
        "lane_count": 1,
        "lane": "council.live_board",
        "ranker_count": 1,
        "ranker_objective": "large_after_friction_return_with_explicit_abstention",
        "research_risk_posture": "active",
        "legacy_runtime_authority": false,
        "probability_sizing_enabled": false,
        "artifact_change_rule": "Any production artifact hash change must update this manifest and its model card."
      },
      "decision": {}
    },
    {
      "commit": "e8d88b39434a6956a6e6e05acd8570ee6a0a0242",
      "committed_at": "2026-08-28T02:58:50Z",
      "title": "feat: replace model stack with production v2",
      "manifest_blob": "1023965483ba032296fa30531d9aed3eefec356c",
      "manifest_sha256": "2aab1713369b26ec4f7fb116275994895196e905fce1764d1c3c0bd136e1bde0",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "d38cc7c392fca619a19c38d57c040bfa4ea6e972cb714e0d4d6ee8bd055483ca",
          "role": "production directional Scout",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "f867df30e5cadf84b8fd844a53abff4255b82b54ddd6d7e68e9e529e667afaba",
          "role": "production Scout preprocessing and calibration",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "bb097b2db11d5de3b124a98cc9dbb8537910cb9a2589f9d8ef815f6802ed53f8",
          "role": "production trade-abstain and side decision",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "production_payoff_ranker": {
          "path": "engine/orographic/models/production_payoff_ranker.pkl",
          "sha256": "ed3c987dfc5fd38634927c82c6a3d63d921f1f8b640d4e5700528e4ae8c06c93",
          "role": "sole production contract ranker",
          "required": true,
          "card_version": 1,
          "trained_at": null,
          "card_path": "engine/orographic/models/production_payoff_ranker_card.json",
          "card_sha256": "9fdfe45051131bd8ce76a38d01dfc91385de9def440dcc81c2cd5464bd1b6a52"
        }
      },
      "policy": {
        "profile": "production_v2",
        "lane_count": 1,
        "lane": "council.live_board",
        "ranker_count": 1,
        "legacy_runtime_authority": false,
        "probability_sizing_enabled": false,
        "artifact_change_rule": "Any production artifact hash change must update this manifest and its model card."
      },
      "decision": {}
    },
    {
      "commit": "9b36c3c323f0aeb1f78e969facb887f4ebc19475",
      "committed_at": "2026-08-13T02:37:54Z",
      "title": "Unify Orographic into one production lane",
      "manifest_blob": "fc1e60e06b8d7f62c1fda1eaf82dc14c9370ec1f",
      "manifest_sha256": "33f3ced77b5ef325b65d52d9cbd4b654197465c1507764b3f47c1077dfea9813",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "d38cc7c392fca619a19c38d57c040bfa4ea6e972cb714e0d4d6ee8bd055483ca",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "f867df30e5cadf84b8fd844a53abff4255b82b54ddd6d7e68e9e529e667afaba",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "bb097b2db11d5de3b124a98cc9dbb8537910cb9a2589f9d8ef815f6802ed53f8",
          "role": "production-active option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "5518f5898c364959749a3a96168efe397443f4dc44c219bea3d403c738fd3829"
        },
        "sentinel_model": {
          "path": "engine/orographic/models/sentinel_model.json",
          "sha256": "8cbeb8e6dc964a1181561930e27fc7cc2255e1ff13916a8f658a8103baa23002",
          "role": "production-active structured Sentinel event model",
          "required": true
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "634d618da76663e31a4958cf90c9bbecda9eac545435cd7dde2a2544f94ecaa4",
          "role": "primary option payoff input to the single unified rank",
          "required": true,
          "card_version": 5,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "d681a9d256f4793d031731c3edad13bb83dbc032a417f7b8c6d5bf58dd4fbc6d"
        },
        "path_model": {
          "path": "engine/orographic/models/path_model.pkl",
          "sha256": "df78e9be9525c7c8d8017c79a6f5caaedac3f44b3fad5a9aed1b2552c43c7ac9",
          "role": "bounded path-quality input to the single unified rank",
          "required": true
        },
        "scout_hierarchical_challenger": {
          "path": "engine/orographic/models/scout_hierarchical_challenger.pkl",
          "sha256": "5114acbcd0f7a12ddec17e97d2b4e3c8cf9d58b12812eb0131970e68db13c15a",
          "role": "bounded 20 percent side-ensemble input; no independent routing authority",
          "required": true
        },
        "payoff_cost_aware_challenger": {
          "path": "engine/orographic/models/payoff_cost_aware_challenger.pkl",
          "sha256": "9efcf85bf38e321f642cb7e46e4c59817b997bacb332f4bb48a4175a63fd5bea",
          "role": "bounded cost-aware and conservative-utility input; no independent routing authority",
          "required": true
        }
      },
      "policy": {
        "side_aware_scout_default": "active",
        "side_aware_scout_active_monitoring_requirements": [
          "monitor live disagreement P&L after costs",
          "monitor abstain-rate stability across at least 30 production trading days",
          "maintain side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "path_model_default": "unified_rnd_active_bounded",
        "production_lane_count": 1,
        "production_lane": "council.live_board",
        "challenger_policy": [
          "compare offline or as non-routable telemetry",
          "replace or reweight a component inside the unified stack after leakage-safe validation",
          "never grant a challenger an independent Council board or Tradier route"
        ],
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {
        "effective_date": "2026-08-12",
        "production_default_variant": "unified_council_cost_cap",
        "evidence_note_path": "docs/single-production-lane-impact-and-model-priorities-2026-08-12.md"
      }
    },
    {
      "commit": "6403797d4236d27fcc02b40c55150cc893e77552",
      "committed_at": "2026-06-28T14:33:41Z",
      "title": "Promote active models and add exit advice scoring",
      "manifest_blob": "13f00f95c60be9e031043c2d4a3bec01dded7a37",
      "manifest_sha256": "66adbf7eb01950b392755f03da4fcdd93320993a069f3feef42c4c6400f733b6",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "d38cc7c392fca619a19c38d57c040bfa4ea6e972cb714e0d4d6ee8bd055483ca",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "3eed480ef3296e74e1aa7b77a4902d3227d500aef7ea900dacae17265fb71fc8"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "f867df30e5cadf84b8fd844a53abff4255b82b54ddd6d7e68e9e529e667afaba",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "3eed480ef3296e74e1aa7b77a4902d3227d500aef7ea900dacae17265fb71fc8"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "bb097b2db11d5de3b124a98cc9dbb8537910cb9a2589f9d8ef815f6802ed53f8",
          "role": "production-active option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "3eed480ef3296e74e1aa7b77a4902d3227d500aef7ea900dacae17265fb71fc8"
        },
        "sentinel_model": {
          "path": "engine/orographic/models/sentinel_model.json",
          "sha256": "8cbeb8e6dc964a1181561930e27fc7cc2255e1ff13916a8f658a8103baa23002",
          "role": "production-active structured Sentinel event model",
          "required": true
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "634d618da76663e31a4958cf90c9bbecda9eac545435cd7dde2a2544f94ecaa4",
          "role": "active option payoff ranker",
          "required": true,
          "card_version": 5,
          "trained_at": "2026-06-28",
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "752f769fb352610b34ce77cb636e65ea43c4b4608053167013c17cc9a52e20cb"
        },
        "path_model": {
          "path": "engine/orographic/models/path_model.pkl",
          "sha256": "df78e9be9525c7c8d8017c79a6f5caaedac3f44b3fad5a9aed1b2552c43c7ac9",
          "role": "shadow-only path-quality observer",
          "required": false
        }
      },
      "policy": {
        "side_aware_scout_default": "active",
        "side_aware_scout_active_monitoring_requirements": [
          "monitor live disagreement P&L after costs",
          "monitor abstain-rate stability across at least 30 production trading days",
          "maintain side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "path_model_default": "shadow",
        "path_model_required_before_any_live_use": [
          "positive disagreement analysis against the terminal payoff ranker",
          "stable quote-path coverage across at least 30 live shadow trading days",
          "no increase in decay-driven false positives"
        ],
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {
        "effective_date": "2026-05-06",
        "production_default_variant": "council_cost_cap",
        "evidence_note_path": "docs/model-deployment-decision-2026-05-06.md"
      }
    },
    {
      "commit": "7243f4df13caaa6397b9d3c62fe65c7bb2add090",
      "committed_at": "2026-06-28T13:26:29Z",
      "title": "feat: ship multi-head policy stack",
      "manifest_blob": "b5e790fd826b166898b313c4351354318946316f",
      "manifest_sha256": "3d25793afc17bcaa551fd8702abb03ac4ff6551faf532823cccea15a8f7f389f",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "1d4699183413c18a2492af181e0143a79e5bd2472b8a88155ac9d777cfa070e9",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-27",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "ec814c59a288f96410012089723d3070c91c87be6da88df50fb22e5e7867d525"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "ada91ac7659033b41fabc2db33847b2f1dd71296a2e96d7138f79587d1367757",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-27",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "ec814c59a288f96410012089723d3070c91c87be6da88df50fb22e5e7867d525"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "43ebe05b6643cb95321ffadae456efa87190d70ddb09156c18a76b678c48648a",
          "role": "shadow option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-27",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "ec814c59a288f96410012089723d3070c91c87be6da88df50fb22e5e7867d525"
        },
        "sentinel_model": {
          "path": "engine/orographic/models/sentinel_model.json",
          "sha256": "8cbeb8e6dc964a1181561930e27fc7cc2255e1ff13916a8f658a8103baa23002",
          "role": "shadow structured Sentinel event model",
          "required": true
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "4f27b9e55fad8ba9c29f902b503d58636c5de6628dab2465c200a73ef1bfdc0e",
          "role": "active option payoff ranker",
          "required": true,
          "card_version": 5,
          "trained_at": "2026-06-27",
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "d55f4b098ddcba006c3b8deb6353ee5081288585a712128dd6f55d305e8e0eed"
        },
        "path_model": {
          "path": "engine/orographic/models/path_model.pkl",
          "sha256": "df78e9be9525c7c8d8017c79a6f5caaedac3f44b3fad5a9aed1b2552c43c7ac9",
          "role": "shadow-only path-quality observer",
          "required": false
        }
      },
      "policy": {
        "side_aware_scout_default": "shadow",
        "side_aware_scout_required_before_active": [
          "positive live shadow disagreement P&L after costs",
          "at least 30 live shadow trading days",
          "materially improved side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "path_model_default": "shadow",
        "path_model_required_before_any_live_use": [
          "positive disagreement analysis against the terminal payoff ranker",
          "stable quote-path coverage across at least 30 live shadow trading days",
          "no increase in decay-driven false positives"
        ],
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {
        "effective_date": "2026-05-06",
        "production_default_variant": "council_cost_cap",
        "evidence_note_path": "docs/model-deployment-decision-2026-05-06.md"
      }
    },
    {
      "commit": "efca061d4d051f444f8b42f3bae2df97d5dff4ba",
      "committed_at": "2026-06-24T22:07:57Z",
      "title": "feat: retrain Scout selection on outcome evidence",
      "manifest_blob": "e9528464a5873a95ede73e0b5d093df6f5f47870",
      "manifest_sha256": "692e31f51ab0544c1c6244eda6fe47d6322c83dfa0ba19055a3edfffcec4b84c",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "a355728430d601a4fc6f4bd229b80dd401c32821a751f62a39bf835fb41dd90e",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "37ee7b25ce3b6d1878f8328ec46b634377e61f444c1e65ed85644e86980198ca"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "4d21e1fafbea398359f372e649a12063b9873923889302d7787a4afd642ed0ca",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "37ee7b25ce3b6d1878f8328ec46b634377e61f444c1e65ed85644e86980198ca"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "8bb27a544ed7383ea75da486c584b2f3958ffb778564186bb65ebf4533f8a091",
          "role": "shadow option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-06-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "37ee7b25ce3b6d1878f8328ec46b634377e61f444c1e65ed85644e86980198ca"
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "b27a680af86d2b8120afce7c7b9cc2c2fdf7f716b34f202025d5481617de9c2e",
          "role": "active option payoff ranker",
          "required": true,
          "card_version": 5,
          "trained_at": null,
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "a42e9d3aa93596fe4fe209030816147eccc1d00ece7e86774a99cad7dd6a52cc"
        },
        "path_model": {
          "path": "engine/orographic/models/path_model.pkl",
          "sha256": "df78e9be9525c7c8d8017c79a6f5caaedac3f44b3fad5a9aed1b2552c43c7ac9",
          "role": "shadow-only path-quality observer",
          "required": false
        }
      },
      "policy": {
        "side_aware_scout_default": "shadow",
        "side_aware_scout_required_before_active": [
          "positive live shadow disagreement P&L after costs",
          "at least 30 live shadow trading days",
          "materially improved side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "path_model_default": "shadow",
        "path_model_required_before_any_live_use": [
          "positive disagreement analysis against the terminal payoff ranker",
          "stable quote-path coverage across at least 30 live shadow trading days",
          "no increase in decay-driven false positives"
        ],
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {
        "effective_date": "2026-05-06",
        "production_default_variant": "council_cost_cap",
        "evidence_note_path": "docs/model-deployment-decision-2026-05-06.md"
      }
    },
    {
      "commit": "088ee9dced1f32081352b45aab0e2be4f27fd806",
      "committed_at": "2026-05-13T20:36:36Z",
      "title": "Add shadow path model and governance controls",
      "manifest_blob": "15757a5a32db0b95b6683e7db9b3f5a59b55453a",
      "manifest_sha256": "cb9079ca24e03278543b63d00537aae037d5059eb3cd9a9e8cf60838146052b7",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "94129bb3472218ad0980d9fd433afc54585ee0a0652fa6f997cec187b050ec13",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "99aabfe1f016a30fd7ff052c9d39198be2c46048e1cd69a61e20220e1b3be0ab",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "c7b8a165b9489a426e59bc25ebfb9200f7125923f9fb48fb6414b669da9cbffa",
          "role": "shadow option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "ec8c13688609ce978b82a6f54ec8bab55004a573d9456ef8197edb266acc893a",
          "role": "active option payoff ranker",
          "required": true,
          "card_version": 3,
          "trained_at": null,
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "e211efc5a6cd3b49825809875833cdc4914a703971510fcf0609b5f562944ce3"
        },
        "path_model": {
          "path": "engine/orographic/models/path_model.pkl",
          "sha256": "df78e9be9525c7c8d8017c79a6f5caaedac3f44b3fad5a9aed1b2552c43c7ac9",
          "role": "shadow-only path-quality observer",
          "required": false
        }
      },
      "policy": {
        "side_aware_scout_default": "shadow",
        "side_aware_scout_required_before_active": [
          "positive live shadow disagreement P&L after costs",
          "at least 30 live shadow trading days",
          "materially improved side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "path_model_default": "shadow",
        "path_model_required_before_any_live_use": [
          "positive disagreement analysis against the terminal payoff ranker",
          "stable quote-path coverage across at least 30 live shadow trading days",
          "no increase in decay-driven false positives"
        ],
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {
        "effective_date": "2026-05-06",
        "production_default_variant": "council_cost_cap",
        "evidence_note_path": "docs/model-deployment-decision-2026-05-06.md"
      }
    },
    {
      "commit": "e8ed32852928fd0082bcceb74ec3a5188d74e6ac",
      "committed_at": "2026-04-23T00:21:48Z",
      "title": "Add ML artifact governance and shadow regression gates",
      "manifest_blob": "5b1e093cb0f8efbbbf38051b9147b7086a605411",
      "manifest_sha256": "e0098a0b0b5b49748e8b4934f00d90b9ebfc5b53208ff21c8245c3dca355684e",
      "artifacts": {
        "scout_model": {
          "path": "engine/orographic/models/scout_model.pkl",
          "sha256": "94129bb3472218ad0980d9fd433afc54585ee0a0652fa6f997cec187b050ec13",
          "role": "active directional Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "scout_scaler": {
          "path": "engine/orographic/models/scout_scaler.pkl",
          "sha256": "99aabfe1f016a30fd7ff052c9d39198be2c46048e1cd69a61e20220e1b3be0ab",
          "role": "active directional Scout scaler and calibrator metadata",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "scout_side_model": {
          "path": "engine/orographic/models/scout_side_model.pkl",
          "sha256": "c7b8a165b9489a426e59bc25ebfb9200f7125923f9fb48fb6414b669da9cbffa",
          "role": "shadow option-payoff side-aware Scout model",
          "required": true,
          "card_version": 3,
          "trained_at": "2026-04-22",
          "card_path": "engine/orographic/models/scout_model_card.json",
          "card_sha256": "9ffa9c81df4bd3ca023b3955db156ec0282421f237525dc3327714f68a2c12be"
        },
        "payoff_model": {
          "path": "engine/orographic/models/payoff_model.pkl",
          "sha256": "ec8c13688609ce978b82a6f54ec8bab55004a573d9456ef8197edb266acc893a",
          "role": "active option payoff ranker",
          "required": true,
          "card_version": 3,
          "trained_at": null,
          "card_path": "engine/orographic/models/payoff_model_card.json",
          "card_sha256": "88a5c5cffdba71e605f9d7472debf294e0e0a5be30540f72a8900f226e2f073c"
        }
      },
      "policy": {
        "side_aware_scout_default": "shadow",
        "side_aware_scout_required_before_active": [
          "positive live shadow disagreement P&L after costs",
          "at least 30 live shadow trading days",
          "materially improved side-balanced validation, especially put-edge coverage"
        ],
        "payoff_ranker_default": "active",
        "artifact_change_rule": "Any artifact hash change must update this manifest and the corresponding model card."
      },
      "decision": {}
    }
  ]
};
