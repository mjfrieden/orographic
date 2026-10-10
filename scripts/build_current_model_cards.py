"""Project public model evidence into a bounded, identity-bound dashboard snapshot.

No network, inference, training or credentials. Cirrus input is a curated public summary only.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

SHA = re.compile(r'^[0-9a-f]{64}$')

def quantity(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991 else None

def metric(label, value, context):
    return {'label': label, 'value': value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1 else None, 'context': context}

def read(root, path):
    try:
        raw = (root / path).read_bytes()
        if len(raw) > 2_000_000:
            raise ValueError('oversized source')
        payload = json.loads(raw)
        return (payload if isinstance(payload, dict) else {}), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        return {}, None

CIRRUS_SOURCE = 'web/data/model-evidence/cirrus_public_summary.json'
CIRRUS_IDS = ['cirrus-hybrid', 'cirrus-boosting', 'cirrus-tail-ensemble', 'cirrus-calibration-study', 'cirrus-three-session-study']

def nonempty(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 2000

def dated(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc)
    except ValueError:
        return False

def cirrus_cards(root):
    """Project only reviewed summary fields; never consume private reports or ledgers."""
    fallback = [{'id': 'cirrus', 'system': 'Cirrus', 'title': 'Cirrus', 'version': None, 'trained_at': None, 'runtime_mode': 'unavailable', 'identity_status': 'unavailable', 'metrics': [], 'how_it_works': 'The curated public Cirrus summary is unavailable or malformed.', 'limitations': ['Model identity and current performance are unavailable. Shared research counts are not performance metrics.']}]
    source, digest = read(root, CIRRUS_SOURCE)
    try:
        if source.get('schema_version') != 1 or not dated(source.get('verified_at_utc')) or not re.fullmatch('[a-f0-9]{40}', str(source.get('source_revision', ''))):
            return fallback
        if not all(nonempty(source.get(key)) for key in ['source_description', 'refresh_policy']):
            return fallback
        rows = source.get('cards')
        if not isinstance(rows, list) or len(rows) != 5 or [row.get('id') for row in rows] != CIRRUS_IDS:
            return fallback
        cards = []
        for row in rows:
            text_fields = ['title', 'role', 'how_it_works', 'evaluation_window', 'population']
            if not all(nonempty(row.get(key)) for key in text_fields):
                return fallback
            if row.get('evidence_kind') not in ['registry_role', 'discovery_study']:
                return fallback
            if row['evidence_kind'] == 'discovery_study' and not dated(row.get('evaluation_completed_at')):
                return fallback
            if row['evidence_kind'] == 'registry_role' and row.get('evaluation_completed_at') is not None:
                return fallback
            for key in ['limitations', 'evaluation_summary']:
                if not isinstance(row.get(key), list) or not 1 <= len(row[key]) <= 8 or not all(nonempty(value) for value in row[key]):
                    return fallback
            cards.append({**{key: row[key] for key in ['id', 'evidence_kind', 'evaluation_completed_at', 'limitations', 'evaluation_summary', *text_fields]},
                'system': 'Cirrus', 'version': None, 'trained_at': None, 'runtime_mode': 'not verified', 'identity_status': 'curated_snapshot', 'metrics': [],
                'source_path': CIRRUS_SOURCE, 'source_sha256': digest, **{key: source[key] for key in ['verified_at_utc', 'source_revision', 'source_description', 'refresh_policy']}})
        return cards
    except (AttributeError, TypeError, KeyError, ValueError):
        return fallback

def _build(root):
    run_path = 'web/data/latest_run.json'
    run, run_hash = read(root, run_path)
    artifacts = run.get('model_artifacts', {})
    modes = run.get('model_modes', {})
    cards = []
    for model_id, title, artifact, card_name, mode in [
        ('scout-direction', 'Directional Scout', 'scout_model', 'scout_model_card', 'directional_scout'),
        ('scout-side', 'Scout side and abstention', 'scout_side_model', 'scout_model_card', 'side_aware_scout'),
        ('tail-ranker', 'Forge tail-utility ranker', 'production_payoff_ranker', 'production_payoff_ranker_card', 'payoff_ranker'),
    ]:
        source_path = f'engine/orographic/models/{card_name}.json'
        source, source_hash = read(root, source_path)
        declared = source.get('model_sha256') if model_id == 'tail-ranker' else source.get('artifacts', {}).get('side_model_sha256' if model_id == 'scout-side' else 'model_sha256')
        observed = artifacts.get(artifact, {})
        card_observed = artifacts.get(card_name, {})
        matched = bool(SHA.fullmatch(str(declared or '')) and observed.get('present') is True and observed.get('sha256') == declared and card_observed.get('present') is True and card_observed.get('sha256') == source_hash)
        if model_id == 'scout-direction':
            scaler = artifacts.get('scout_scaler', {})
            scaler_hash = source.get('artifacts', {}).get('scaler_sha256')
            matched = matched and bool(SHA.fullmatch(str(scaler_hash or '')) and scaler.get('present') is True and scaler.get('sha256') == scaler_hash)
        card = {'id': model_id, 'system': 'Orographic', 'title': title, 'version': source.get('version'), 'trained_at': source.get('trained_at_utc', source.get('trained_at')), 'runtime_mode': modes.get(mode, 'not reported'), 'model_sha256': declared, 'identity_status': 'matched_metadata' if matched else 'unverified', 'source_path': source_path, 'source_sha256': source_hash, 'metrics': [], 'limitations': ['Historical validation is not current live performance. F1 is not reported in this model card.', 'Identity matches published runtime metadata and exact card bytes; the model binary was not independently rehashed here.']}
        if model_id == 'scout-direction':
            cv = source.get('cross_validation', {})
            coverage = source.get('observability', {}).get('coverage', {})
            card.update(how_it_works='LightGBM uses stock and market features to estimate a positive five-day underlying return. This directional artifact is distinct from the active call/put/no-trade policy.', evaluation_window=f"Training coverage: {coverage.get('date_start', 'not reported')} to {coverage.get('date_end', 'not reported')}. Validation fold date endpoints are not reported.", population='Underlying-stock symbol-date observations; not option trades.', sample_size=source.get('calibration', {}).get('oof_rows'))
            card['metrics'] = [metric('Mean binary directional validation AUC', cv.get('mean_auc'), 'Mean across historical validation folds. 0.5 is no ranking discrimination; AUC is not accuracy.'), metric('F1', None, 'Not reported; no threshold-based F-score inferred.')]
        elif model_id == 'scout-side':
            side = source.get('side_aware_output', {}).get('training_metrics', {})
            cv = side.get('cross_validation', {})
            folds = cv.get('fold_reports', [])
            rows = [f.get('validation_rows') for f in folds]
            sample = sum(rows) if rows and all(quantity(n) is not None for n in rows) else None
            card.update(how_it_works='Three-class Scout estimates call-edge, put-edge and no-trade probabilities before Forge selects option contracts.', evaluation_window='Historical cross-validation; fold date endpoints are not reported.', population='Strict-real option-payoff labels grouped by symbol/date.', sample_size=sample, training_class_counts={key: quantity(side.get('class_counts', {}).get(key)) for key in ['call_edge', 'put_edge', 'no_trade']})
            card['metrics'] = [metric('Mean validation balanced accuracy', cv.get('mean_balanced_accuracy'), 'Three classes; equal-chance reference is about 0.3333. Distinct from binary directional AUC.'), metric('F1', None, 'Not reported; no macro/micro/weighted F1 inferred.')]
            card['limitations'].append('Imbalanced training classes limit conclusions, especially the put class. Training accuracy is not out-of-sample skill.')
        else:
            validation = source.get('source_validation', {})
            sides = validation.get('by_side', {})
            counts = [sides.get(s, {}).get('rows') for s in ['call', 'put']]
            sample = sum(counts) if all(quantity(n) is not None for n in counts) else None
            card.update(how_it_works='Forge filters contracts, then the tail-utility ranker estimates large-win and severe-loss risks and ranks after-friction utility. Council applies the final selection and abstention policy.', evaluation_window='Later frozen forward validation window; complete start/end dates are not reported by the model card.', population='Option candidate rows in forward evaluation. Selected-policy replay is a separate population.', sample_size=sample)
            card['metrics'] = [metric('Forward big-win AUC', validation.get('forward_big_win_auc'), 'Binary target: after-friction return at least +50%. Ranking discrimination, not accuracy.'), metric('Forward big-win Brier score', validation.get('forward_big_win_brier'), 'Probability error; lower is better. No matched constant-probability baseline supplied.'), metric('Forward severe-loss AUC', validation.get('forward_severe_loss_auc'), 'Binary target: after-friction return at most −50%.'), metric('F1', None, 'Not reported; no threshold-based F-score inferred.')]
            policy = validation.get('forward_policy', {})
            card['replay'] = {'sample_size': quantity(policy.get('selected_trades')), 'mean_return': policy.get('average_return_after_friction_pct'), 'median_return': policy.get('median_return_after_friction_pct')}
            card['limitations'].append('Selected-policy results are historical quote replays, not broker fills or account P&L. Small samples cannot establish profitability.')
        if model_id == 'scout-direction':
            card['scaler_sha256'] = scaler_hash
        sample = card.get('sample_size')
        card['sample_size'] = quantity(sample)
        if not matched:
            card['metrics'] = []
            card['limitations'] = [item for item in card['limitations'] if not item.startswith('Identity matches')]
            card.pop('replay', None)
            card['limitations'].insert(0, 'Metrics withheld: source card and runtime model identity could not be matched.')
        cards.append(card)
    cards.extend(cirrus_cards(root))
    return {'schema_version': 1, 'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'runtime_at_utc': run.get('generated_at_utc'), 'runtime_source_sha256': run_hash, 'runtime_source_path': run_path, 'freshness_policy_hours': 96, 'cards': cards}

def unavailable_snapshot():
    return {'schema_version': 1, 'status': 'unavailable', 'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'runtime_at_utc': None, 'cards': [
        {'id': key, 'system': system, 'title': title, 'version': None, 'trained_at': None, 'runtime_mode': 'unavailable', 'identity_status': 'unavailable', 'metrics': [], 'how_it_works': 'Current model evidence could not be validated.', 'limitations': ['Performance withheld because source evidence is unavailable or malformed.']}
        for key, system, title in [('scout-direction', 'Orographic', 'Directional Scout'), ('scout-side', 'Orographic', 'Scout side and abstention'), ('tail-ranker', 'Orographic', 'Forge tail-utility ranker'), ('cirrus', 'Cirrus', 'Cirrus')]]}

def build(root):
    try:
        return _build(root)
    except (AttributeError, TypeError, KeyError, ValueError, OverflowError):
        return unavailable_snapshot()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, default=Path('web/data/diagnostics/current_model_cards_latest.json'))
    args = parser.parse_args()
    payload = build(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    temp.replace(args.output)

if __name__ == '__main__':
    main()
