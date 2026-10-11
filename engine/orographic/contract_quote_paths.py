"""Inactive, offline quote replay: exact identities and explicit clocks only.

Input dictionaries are retained observations, never fills. No file/network/clock or
model imports. This module does not modify the shared mart or register a consumer.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

VERSION = 'atmosphere-contract-quote-path-v1'
IDENTITY = ('source_namespace', 'security_id', 'contract_id', 'contract_namespace',
            'deliverable_id', 'currency', 'multiplier', 'expires_at_utc')
CLOCKS = ('capture_at_utc', 'available_at_utc', 'bid_observed_at_utc', 'ask_observed_at_utc')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('explicit timestamp required')
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


@dataclass(frozen=True)
class ReplayLimits:
    """Per-call safety bounds; limits are not permissions for larger source loads."""
    max_artifacts: int = 32
    max_artifact_bytes: int = 16_000_000
    max_total_artifact_bytes: int = 64_000_000
    max_input_rows: int = 100_000
    max_row_bytes: int = 128_000
    max_total_row_bytes: int = 128_000_000
    max_json_nodes: int = 1_000_000
    max_json_depth: int = 64
    max_pointer_depth: int = 64
    max_reconstructed_rows: int = 200_000

    def validate(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError('positive integer replay limits required')


class ReplayLimitError(ValueError):
    """A bounded audit stopped before returning a partial result."""


def _decode(raw, limits):
    if not isinstance(raw, bytes):
        raise ValueError('retained source must be bytes')
    if len(raw) > limits.max_artifact_bytes:
        raise ReplayLimitError('artifact byte limit exceeded')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        root = json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except RecursionError as exc:
        raise ReplayLimitError('JSON parser depth limit exceeded') from exc
    # Iterator DFS uses depth-bounded traversal memory, even for a wide array.
    stack = [iter((root,))]
    nodes = 0
    while stack:
        try:
            value = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        depth = len(stack) - 1
        if nodes > limits.max_json_nodes or depth > limits.max_json_depth:
            raise ReplayLimitError('decoded JSON structure limit exceeded')
        if isinstance(value, dict):
            stack.append(iter(value.values()))
        elif isinstance(value, list):
            stack.append(iter(value))
    canonical(root)
    return root


def _select(root, pointer, limits):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('RFC6901 pointer required')
    tokens = pointer.split('/')[1:]
    if len(tokens) > limits.max_pointer_depth:
        raise ReplayLimitError('pointer depth limit exceeded')
    selected = root
    for token in tokens:
        if '~' in token.replace('~0', '').replace('~1', ''):
            raise ValueError('invalid pointer escape')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(selected, list):
            if not token.isdigit() or str(int(token)) != token:
                raise ValueError('invalid array pointer')
            selected = selected[int(token)]
        else:
            selected = selected[token]
    return selected


def _normalize(selected, *, source_namespace, pointer, field_map, sha, limits):
    if not text(source_namespace):
        raise ValueError('source namespace required')
    single = isinstance(selected, dict)
    if single:
        selected = [selected]
    if not isinstance(selected, list) or any(not isinstance(row, dict) for row in selected):
        raise ValueError('selected value must be object rows')
    if len(selected) > limits.max_input_rows:
        raise ReplayLimitError('selected row count limit exceeded')
    field_map = field_map or {}
    if not isinstance(field_map, dict) or any(not text(k) or not text(v) or k.startswith('_') for k, v in field_map.items()):
        raise ValueError('explicit direct-field mapping required')
    rows = []
    total = 0
    for i, original in enumerate(selected):
        row_pointer = pointer if single else f'{pointer}/{i}'
        if '_lineage' in original:
            raise ValueError('reserved lineage field')
        row = dict(original)
        if 'source_namespace' in row and row['source_namespace'] != source_namespace:
            raise ValueError('source namespace mismatch')
        row['source_namespace'] = source_namespace
        for target, source in field_map.items():
            if target in row and target != source:
                raise ValueError('mapping would overwrite source field')
            row[target] = original.get(source)
        result = {**row, '_lineage': {
            'source_namespace': source_namespace, 'artifact_sha256': sha,
            'json_pointer': row_pointer, 'row_sha256': digest(row),
            'retained_row_sha256': digest(original), 'field_map': field_map,
            'observation_id': f'{source_namespace}|sha256|{sha}|{row_pointer}',
        }}
        size = len(canonical(result).encode())
        total += size
        if size > limits.max_row_bytes or total > limits.max_total_row_bytes:
            raise ReplayLimitError('normalized row byte limit exceeded')
        rows.append(result)
    return rows


def retained_json(raw: bytes, *, source_namespace: str, pointer: str = '', field_map=None,
                  limits=ReplayLimits()):
    """Normalize retained objects with byte hashes, exact pointers and no clock inference."""
    limits.validate()
    root = _decode(raw, limits)
    return _normalize(_select(root, pointer, limits), source_namespace=source_namespace,
                      pointer=pointer, field_map=field_map,
                      sha=hashlib.sha256(raw).hexdigest(), limits=limits)


class _ArtifactVerifier:
    """One hash/decode per referenced artifact; per-input-row validation cache.

    All caches are scoped to this join call and bounded by ReplayLimits. Source
    references are never used as paths or URLs. Source bytes remain caller-owned.
    """
    def __init__(self, artifacts, limits, metrics, reject_output_aliases=None):
        self.artifacts, self.limits, self.metrics = artifacts, limits, metrics
        if len(artifacts) > limits.max_artifacts:
            raise ReplayLimitError('artifact count limit exceeded')
        total = 0
        for raw in artifacts.values():
            if reject_output_aliases is not None and not isinstance(raw, bytes):
                reject_output_aliases(raw)
            if isinstance(raw, bytes):
                if len(raw) > limits.max_artifact_bytes:
                    raise ReplayLimitError('artifact byte limit exceeded')
                total += len(raw)
        if total > limits.max_total_artifact_bytes:
            raise ReplayLimitError('aggregate artifact byte limit exceeded')
        self.roots = {}
        self.row_reasons = {}

    def root(self, sha):
        if sha in self.roots:
            self.metrics['artifact_cache_hits'] += 1
            return self.roots[sha]
        raw = self.artifacts.get(sha)
        if not isinstance(raw, bytes):
            return ('source_artifact_hash_mismatch', None)
        self.metrics['artifact_hashes'] += 1
        if hashlib.sha256(raw).hexdigest() != sha:
            value = ('source_artifact_hash_mismatch', None)
        else:
            try:
                self.metrics['artifact_decodes'] += 1
                value = (None, _decode(raw, self.limits))
            except ReplayLimitError:
                raise
            except (ValueError, TypeError, OverflowError):
                value = ('source_pointer_or_mapping_mismatch', None)
        self.roots[sha] = value
        return value

    def restore(self, row):
        lineage = row['_lineage']
        reason, root = self.root(lineage['artifact_sha256'])
        if reason:
            return reason
        try:
            selected = _select(root, lineage['json_pointer'], self.limits)
            count = len(selected) if isinstance(selected, list) else 1
            self.metrics['reconstructed_rows'] += count
            if self.metrics['reconstructed_rows'] > self.limits.max_reconstructed_rows:
                raise ReplayLimitError('source reconstruction work limit exceeded')
            restored = _normalize(selected, source_namespace=lineage['source_namespace'],
                                  pointer=lineage['json_pointer'], field_map=lineage.get('field_map'),
                                  sha=lineage['artifact_sha256'], limits=self.limits)
        except ReplayLimitError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            return 'source_pointer_or_mapping_mismatch'
        if len(restored) != 1 or restored[0] != row:
            return 'source_lineage_mismatch'
        return None

    def reason(self, row):
        key = id(row)
        if key in self.row_reasons:
            self.metrics['row_cache_hits'] += 1
            return self.row_reasons[key]
        self.metrics['row_validations'] += 1
        value = _lineage_reason(row, self)
        self.row_reasons[key] = value
        return value


class _QuoteIndex:
    """Exact-identity lookup + sorted availability + prefix conflict counts."""
    def __init__(self, rows, observation_conflicts, source_conflicts, metrics):
        self.metrics = metrics
        rows = sorted(rows, key=lambda q: (timestamp(q['available_at_utc']), canonical(q)))
        self.times = []
        self.observation_prefix, self.source_prefix = [0], [0]
        self.first = {}
        markets = defaultdict(set)
        for q in rows:
            t = timestamp(q['available_at_utc'])
            self.times.append(t)
            self.observation_prefix.append(self.observation_prefix[-1] + (q['_lineage']['observation_id'] in observation_conflicts))
            self.source_prefix.append(self.source_prefix[-1] + ((q['source_namespace'], q['source_record_id']) in source_conflicts))
            self.first.setdefault(t, q)  # canonical row order resolves equal-price ties
            markets[t].add((q['bid'], q['ask']))
        self.market_conflicts = {t for t, prices in markets.items() if len(prices) != 1}
        metrics['quote_rows_indexed'] += len(rows)
        metrics['quote_time_groups'] += len(markets)

    def bound(self, target, *, right=False):
        lo, hi = 0, len(self.times)
        while lo < hi:
            mid = (lo + hi) // 2
            self.metrics['time_index_comparisons'] += 1
            if self.times[mid] < target or (right and self.times[mid] == target):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def select(self, name, start, end):
        self.metrics['window_queries'] += 1
        left, right = self.bound(start), self.bound(end, right=True)
        if left == right:
            return None, f'missing_valid_{name}_quote'
        if self.observation_prefix[right] != self.observation_prefix[left]:
            return None, 'conflicting_observation_normalization'
        if self.source_prefix[right] != self.source_prefix[left]:
            return None, 'conflicting_source_record_id'
        first_at = self.times[left]
        if first_at in self.market_conflicts:
            return None, f'conflicting_{name}_time_quotes'
        return self.first[first_at], None


@dataclass(frozen=True)
class ReplayPolicy:
    policy_id: str
    policy_version: str
    entry_window_seconds: int
    holding_seconds: int
    exit_window_seconds: int
    max_quote_age_seconds: int
    max_capture_delay_seconds: int
    # v1 is single-leg, long option ask-entry / bid-exit quote replay only.
    entry_rule: str = 'first_available_ask_after_decision'
    exit_rule: str = 'first_available_bid_after_fixed_horizon'

    def validate(self):
        if not text(self.policy_id) or not text(self.policy_version):
            raise ValueError('declared policy identity required')
        for field in ('entry_window_seconds', 'holding_seconds', 'exit_window_seconds',
                      'max_quote_age_seconds', 'max_capture_delay_seconds'):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ValueError('nonnegative integer policy durations required')
        if self.holding_seconds <= self.entry_window_seconds:
            raise ValueError('exit horizon must be later than entry window')
        if self.entry_rule != 'first_available_ask_after_decision' or self.exit_rule != 'first_available_bid_after_fixed_horizon':
            raise ValueError('unsupported replay policy')


def _lineage_reason(row, artifacts):
    lineage = row.get('_lineage')
    if not isinstance(lineage, dict):
        return 'missing_source_lineage'
    payload = {k: v for k, v in row.items() if k != '_lineage'}
    if lineage.get('row_sha256') != digest(payload):
        return 'source_row_hash_mismatch'
    for field in ('source_namespace', 'artifact_sha256', 'json_pointer', 'observation_id'):
        if not text(lineage.get(field)) and not (field == 'json_pointer' and lineage.get(field) == ''):
            return 'missing_source_lineage'
    if row.get('source_namespace') != lineage['source_namespace']:
        return 'source_namespace_mismatch'
    return artifacts.restore(row)


def _identity_reason(row):
    if any(not text(row.get(k)) for k in IDENTITY if k != 'multiplier'):
        return 'missing_exact_contract_identity'
    if not positive(row.get('multiplier')):
        return 'unverified_multiplier_or_deliverable'
    # Evidence references are retained declarations requiring upstream review;
    # this module cannot establish correctness of a broker or corporate action.
    proof = row.get('identity_evidence')
    if not isinstance(proof, dict) or proof.get('review_status') != 'verified' or not text(proof.get('reference')):
        return 'unverified_multiplier_or_deliverable'
    try:
        timestamp(row.get('expires_at_utc'))
    except (ValueError, TypeError, OverflowError):
        return 'invalid_contract_expiration'
    if row.get('position_kind') != 'single_long_option':
        return 'unsupported_position_kind'
    return None


def _quote_reason(row, policy, artifacts):
    reason = artifacts.reason(row) or _identity_reason(row)
    if reason:
        return reason
    if not text(row.get('source_record_id')):
        return 'missing_source_record_id'
    try:
        capture, available, bid_at, ask_at = [timestamp(row.get(f)) for f in CLOCKS]
    except (ValueError, TypeError, OverflowError):
        return 'missing_or_invalid_quote_clocks'
    if available > timestamp(row['expires_at_utc']):
        return 'quote_after_contract_expiration'
    if not (bid_at <= capture <= available and ask_at <= capture):
        return 'invalid_quote_clock_order'
    if (available - capture).total_seconds() > policy.max_capture_delay_seconds:
        return 'capture_availability_delay_exceeded'
    if max((available - bid_at).total_seconds(), (available - ask_at).total_seconds()) > policy.max_quote_age_seconds:
        return 'stale_quote'
    if not positive(row.get('bid')) or not positive(row.get('ask')) or row['bid'] > row['ask']:
        return 'nonpositive_or_crossed_market'
    return None


def _identity(row):
    return tuple(timestamp(row[k]).isoformat() if k == 'expires_at_utc' else row.get(k) for k in IDENTITY)


def _witness(row):
    return {**row['_lineage'], 'source_record_id': row['source_record_id'],
            **{k: row[k] for k in CLOCKS}, 'bid': row['bid'], 'ask': row['ask'],
            'identity_evidence': row['identity_evidence']}



COVERAGE_VERSION = 'atmosphere-quote-replay-coverage-v1'
# Guidance names the next evidence to review, never a promise of eligibility.
_EVIDENCE_REASONS = {
    'restore_retained_bytes_and_lineage': (
        'missing_source_lineage', 'source_row_hash_mismatch',
        'source_namespace_mismatch', 'source_artifact_hash_mismatch',
        'source_pointer_or_mapping_mismatch', 'source_lineage_mismatch'),
    'review_exact_identity_and_deliverable': (
        'missing_exact_contract_identity', 'unverified_multiplier_or_deliverable',
        'invalid_contract_expiration', 'unsupported_position_kind',
        'missing_recommendation_id', 'duplicate_recommendation_id',
        'missing_source_record_id', 'conflicting_observation_normalization',
        'conflicting_source_record_id'),
    'review_recorded_clocks_and_availability': (
        'missing_or_invalid_decision_clocks', 'recommendation_not_available_at_asof',
        'recommendation_not_available_at_decision', 'missing_or_invalid_quote_clocks',
        'invalid_quote_clock_order', 'capture_availability_delay_exceeded',
        'quote_not_available_at_asof'),
    'retain_valid_entry_quotes': ('missing_valid_entry_quote', 'conflicting_entry_time_quotes'),
    'retain_valid_exit_quotes': ('missing_valid_exit_quote', 'conflicting_exit_time_quotes'),
    'review_window_maturity_and_expiration': (
        'immature_exit_window', 'window_after_contract_expiration', 'quote_after_contract_expiration'),
    'retain_fresh_uncrossed_two_sided_quotes': ('stale_quote', 'nonpositive_or_crossed_market'),
}
_REASON_EVIDENCE = {reason: evidence for evidence, reasons in _EVIDENCE_REASONS.items()
                    for reason in reasons}


class _CoverageDiagnostics:
    """At most one cohort per bounded recommendation; no source or quote rescans."""
    def __init__(self):
        self.cohorts = defaultdict(Counter)

    def add(self, row, lineage_reason, decision, path):
        # An unverified claim cannot attribute a row to a namespace or date.
        namespace = row['source_namespace'] if lineage_reason is None else None
        date = decision.date().isoformat() if lineage_reason is None and decision else None
        self.cohorts[(namespace, date)][path['reason']] += 1

    def finish(self, report):
        cohorts = []
        for (namespace, date), counts in sorted(self.cohorts.items(), key=lambda item: canonical(item[0])):
            eligible = counts.get(None, 0)
            reasons = {reason: count for reason, count in sorted(
                ((reason, count) for reason, count in counts.items() if reason is not None))}
            cohorts.append({
                'source_status': 'verified_lineage' if namespace is not None else 'unverified_source',
                'source_namespace': namespace,
                'decision_date_status': ('verified_lineage_utc_date' if date is not None else
                                         'unverified_source' if namespace is None else 'missing_or_invalid_date'),
                'decision_date_utc': date,
                'recommendation_rows': sum(counts.values()),
                'eligible_quote_replays': eligible,
                'excluded_recommendation_rows': sum(reasons.values()),
                'first_exclusions_by_reason': reasons,
            })
        coverage = report['coverage']
        reasons = set(coverage['excluded_by_reason']) | set(coverage['invalid_quotes_by_reason'])
        result = {
            'schema_version': COVERAGE_VERSION,
            'replay_report_sha256': report['report_sha256'],
            'policy_sha256': report['policy_sha256'], 'as_of_utc': report['as_of_utc'],
            'evidence_class': 'quote_replay_only', 'exclusion_semantics': 'first_blocker_only',
            'cohort_basis': 'verified_recommendation_lineage_and_utc_decision_date',
            'cohorts': cohorts,
            'totals': {'recommendation_rows': coverage['recommendations'],
                       'eligible_quote_replays': coverage['eligible_quote_replays'],
                       'excluded_recommendation_rows': sum(coverage['excluded_by_reason'].values()),
                       'first_exclusions_by_reason': dict(coverage['excluded_by_reason'])},
            'global_quotes': {'attribution': 'global_only_no_recommendation_cohort_assignment',
                              'input_quote_rows': coverage['input_quote_rows'],
                              'first_exclusions_by_reason': dict(coverage['invalid_quotes_by_reason'])},
            'next_evidence_by_reason': {reason: _REASON_EVIDENCE.get(reason, 'unknown_reason_review_required')
                                        for reason in sorted(reasons)},
        }
        result['diagnostics_sha256'] = digest(result)
        return result


def join_quote_paths(recommendations, quotes, *, policy: ReplayPolicy, as_of_utc: str, artifacts,
                     limits=ReplayLimits(), operation_counts=None, coverage_diagnostics=None):
    """Return separate auditable paths and exclusions, never mutate source tables.

    Windows use quote availability, inclusive at both boundaries; exit target is
    decision + holding_seconds, not a price-selected target. Entry and exit must
    be fresh positive uncrossed markets. Full exit-window maturity is required.
    Identical duplicate observations collapse by content; conflicting source IDs
    or earliest-time markets exclude, rather than choosing a favorable price.
    """
    policy.validate()
    limits.validate()
    if not isinstance(artifacts, Mapping):
        raise ValueError('verified source artifact bytes mapping required')
    as_of = timestamp(as_of_utc)
    metrics = Counter()
    if operation_counts is not None and not isinstance(operation_counts, dict):
        raise ValueError('operation_counts must be a dict or None')
    if coverage_diagnostics is not None and type(coverage_diagnostics) is not dict:
        raise ValueError('coverage_diagnostics must be a plain dict or None')
    if coverage_diagnostics is not None and any(coverage_diagnostics is value for value in
                                                (operation_counts, artifacts, recommendations, quotes)):
        raise ValueError('coverage_diagnostics must be a separate output dict')
    if coverage_diagnostics is not None and operation_counts is not None and any(
            operation_counts is value for value in (artifacts, recommendations, quotes)):
        raise ValueError('operation_counts must not alias inputs')
    if coverage_diagnostics is not None and operation_counts is not None and type(operation_counts) is not dict:
        raise ValueError('operation_counts must be a plain dict when coverage_diagnostics is used')
    diagnostics = _CoverageDiagnostics() if coverage_diagnostics is not None else None
    def reject_output_aliases(value):
        # Inspect containers during the existing bounded input pass, not a later
        # source/quote rescan. Bytes are never decoded here. JSON serialization
        # has already rejected cycles and bounded row bytes. Unused malformed
        # artifact values may also contain containers, so cap this walk itself.
        stack = [iter((value,))]
        nodes = 0
        while stack:
            try:
                item = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            nodes += 1
            if nodes > limits.max_json_nodes or len(stack) - 1 > limits.max_json_depth:
                raise ReplayLimitError('diagnostic alias-check structure limit exceeded')
            if item is coverage_diagnostics or (operation_counts is not None and item is operation_counts):
                raise ValueError('diagnostic outputs must not alias input containers')
            if isinstance(item, dict):
                stack.append(iter(item.values()))
            elif isinstance(item, (list, tuple)):
                stack.append(iter(item))
    verifier = _ArtifactVerifier(artifacts, limits, metrics,
                                 reject_output_aliases if diagnostics is not None else None)
    def bounded_rows(source):
        rows = []
        for row in source:
            metrics['input_rows'] += 1
            if metrics['input_rows'] > limits.max_input_rows:
                raise ReplayLimitError('input row count limit exceeded')
            if not isinstance(row, dict):
                raise ValueError('input row must be an object')
            if coverage_diagnostics is not None and (coverage_diagnostics is row or
                                                     coverage_diagnostics is row.get('_lineage')):
                raise ValueError('coverage_diagnostics must not alias input rows or lineage')
            try:
                size = len(canonical(row).encode())
            except RecursionError as exc:
                raise ReplayLimitError('input row depth limit exceeded') from exc
            metrics['input_row_bytes'] += size
            if size > limits.max_row_bytes or metrics['input_row_bytes'] > limits.max_total_row_bytes:
                raise ReplayLimitError('input row byte limit exceeded')
            if diagnostics is not None:
                reject_output_aliases(row)
            rows.append(row)
        return rows
    recommendations, quotes = bounded_rows(recommendations), bounded_rows(quotes)
    invalid = Counter()
    quote_exclusions = []
    by_identity = defaultdict(list)
    by_source_id = defaultdict(list)
    by_observation_id = defaultdict(list)
    for q in quotes:
        reason = _quote_reason(q, policy, verifier)
        try:
            visible = timestamp(q.get('available_at_utc')) <= as_of
        except (ValueError, TypeError, OverflowError):
            visible = False
        if not reason and not visible:
            reason = 'quote_not_available_at_asof'
        if visible and not verifier.reason(q):
            if text(q.get('source_record_id')):
                by_source_id[(q['source_namespace'], q['source_record_id'])].append(q)
            by_observation_id[q['_lineage']['observation_id']].append(q)
        if reason:
            invalid[reason] += 1
            quote_exclusions.append({'lineage': q.get('_lineage'), 'source_record_id': q.get('source_record_id'), 'reason': reason})
        else:
            by_identity[_identity(q)].append(q)
    conflicts = set()
    for key, rows in by_source_id.items():
        # Artifact copies may differ in lineage but not source content.
        if len({q['_lineage']['row_sha256'] for q in rows}) > 1:
            conflicts.add(key)
    observation_conflicts = {key for key, rows in by_observation_id.items()
                             if len({digest(q) for q in rows}) > 1}
    quote_indexes = {identity: _QuoteIndex(rows, observation_conflicts, conflicts, metrics)
                     for identity, rows in by_identity.items()}
    def visible_at_cutoff(row):
        try:
            return timestamp(row.get('available_at_utc')) <= as_of
        except (ValueError, TypeError, OverflowError):
            return False
    rec_ids = Counter(canonical([r.get('source_namespace'), r.get('recommendation_id')])
                      for r in recommendations if visible_at_cutoff(r) and not verifier.reason(r))
    paths = []
    for r in sorted(recommendations, key=lambda row: canonical(row)):
        base = {'recommendation_id': r.get('recommendation_id'),
                'recommendation_lineage': r.get('_lineage'),
                'policy_id': policy.policy_id, 'policy_version': policy.policy_version,
                'evidence_class': 'quote_replay_not_broker_fill',
                'entry': None, 'exit': None, 'label_available_at_utc': None,
                'status': 'excluded', 'reason': None}
        lineage_reason = verifier.reason(r)
        reason = lineage_reason or _identity_reason(r)
        if not text(r.get('recommendation_id')):
            reason = reason or 'missing_recommendation_id'
        if rec_ids[canonical([r.get('source_namespace'), r.get('recommendation_id')])] > 1:
            reason = reason or 'duplicate_recommendation_id'
        decision = None
        try:
            decision = timestamp(r.get('decision_at_utc'))
            known = timestamp(r.get('available_at_utc'))
            if known > as_of:
                reason = reason or 'recommendation_not_available_at_asof'
            if known > decision:
                reason = reason or 'recommendation_not_available_at_decision'
            entry_end = decision + timedelta(seconds=policy.entry_window_seconds)
            exit_start = decision + timedelta(seconds=policy.holding_seconds)
            exit_end = exit_start + timedelta(seconds=policy.exit_window_seconds)
            if not reason and exit_end > timestamp(r['expires_at_utc']):
                reason = 'window_after_contract_expiration'
            if as_of < exit_end:
                reason = reason or 'immature_exit_window'
        except (ValueError, TypeError, OverflowError):
            reason = reason or 'missing_or_invalid_decision_clocks'
        if reason:
            base['reason'] = reason
            paths.append(base)
            if diagnostics is not None:
                diagnostics.add(r, lineage_reason, decision, base)
            continue
        base['identity'] = dict(zip(IDENTITY, _identity(r)))
        base['windows'] = {'entry_start': decision.isoformat(), 'entry_end': entry_end.isoformat(),
                           'exit_start': exit_start.isoformat(), 'exit_end': exit_end.isoformat()}
        index = quote_indexes.get(_identity(r))
        selected = []
        for name, start, end in [('entry', decision, entry_end), ('exit', exit_start, exit_end)]:
            if index is None:
                reason = f'missing_valid_{name}_quote'
                break
            chosen, reason = index.select(name, start, end)
            if reason:
                break
            selected.append(chosen)
        if reason:
            base['reason'] = reason
        else:
            entry, exit_quote = selected
            base.update(status='eligible_quote_replay', reason=None,
                        entry={**_witness(entry), 'replay_price': entry['ask']},
                        exit={**_witness(exit_quote), 'replay_price': exit_quote['bid']},
                        label_available_at_utc=exit_end.isoformat())
        paths.append(base)
        if diagnostics is not None:
            diagnostics.add(r, lineage_reason, decision, base)
    report = {'schema_version': VERSION, 'policy': asdict(policy),
              'policy_sha256': digest(asdict(policy)), 'as_of_utc': as_of.isoformat(),
              'runtime_active': False, 'broker_fills': False, 'paths': paths,
              'quote_exclusions': sorted(quote_exclusions, key=canonical),
              'source_artifacts': sorted({q['_lineage']['artifact_sha256'] for q in recommendations + quotes if isinstance(q.get('_lineage'), dict) and text(q['_lineage'].get('artifact_sha256'))}),
              'coverage': {'recommendations': len(paths),
                  'eligible_quote_replays': sum(p['status'] == 'eligible_quote_replay' for p in paths),
                  'excluded_by_reason': dict(sorted(Counter(p['reason'] for p in paths if p['reason']).items())),
                  'input_quote_rows': len(quotes), 'invalid_quotes_by_reason': dict(sorted(invalid.items()))}}
    report['report_sha256'] = digest(report)
    sidecar = diagnostics.finish(report) if diagnostics is not None else None
    if coverage_diagnostics is not None:
        coverage_diagnostics.clear()
        coverage_diagnostics.update(sidecar)
    if operation_counts is not None:
        operation_counts.clear()
        operation_counts.update(dict(sorted(metrics.items())))
    return report
