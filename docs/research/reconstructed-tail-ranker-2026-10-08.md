# Reconstructed tail-ranker diagnostic, October 8, 2026

**Research-only retrospective evidence. Exact original input bytes remain unverified.**
One fixed corrected run used corroborating development and forward inputs; no
tuning, promotion, live-data call, paid-data access, or active-artifact change.
The [sanitized summary](reconstructed-tail-ranker-2026-10-08.json) records source
hashes, settings, metrics, verification scope, and limitations.

## Findings

| Policy | Selections | Mean after-friction return | Median | Severe-loss rate |
|---|---:|---:|---:|---:|
| Historical development OOF, recorded | 7 / 20 dates | -8.50% | -10.53% | 0 / 7 |
| Corrected reconstructed development OOF, recorded | 6 / 20 dates | -3.93% | -10.53% | 0 / 6 |
| Reconstructed forward tail policy | 9 / 17 scans | +2.22% | -44.32% | 4 / 9 |
| Reconstructed integrated forward policy | 4 / 17 scans | +65.20% | +51.11% | 1 / 4 |

These are per-selection return summaries, not compounded portfolio returns.
Development remains negative. The complete forward and integrated-forward
summaries match the historical card at its saved precision, including selected
rows and probabilities. Big-win AUC is 0.6602, severe-loss AUC 0.5575, and big-win
Brier score 0.1481. A separate read-only audit found all 146 x 4 historical and
reconstructed forward probabilities bitwise identical, without refitting.

The audit also checked fold boundaries, training-only bucket means and corrected
development return arithmetic. Raw OOF probabilities and selected date identities
were not retained; the corrected eligible-candidate count of 10 is run-reported,
not independently recomputed. No second diagnostic fit was performed.

The four integrated selections are a tiny already-inspected historical cohort.
This result establishes no prospective edge, promotion case, or sizing basis.
The recorded quote-derived label policy does not establish actual broker fills.

## Source provenance

The [historical card](https://github.com/mjfrieden/orographic/blob/a291aa090979ae0873c922bc50ddfc5118648bb0/engine/orographic/models/production_payoff_ranker_card.json)
records development `output/option_outcomes_live_recommendations.json` and a
forward research-dataset label, but no input hashes or source artifact IDs. The
[historical trainer](https://github.com/mjfrieden/orographic/blob/a291aa090979ae0873c922bc50ddfc5118648bb0/scripts/train_production_tail_ranker.py)
hard-coded the forward label instead of recording its actual argument. Complete
Git-tree/history checks and four preserved scan bundles did not recover a file
certified as the exact original development input.

- Development: immutable `data/evidence_seed/strict_option_outcomes.json.gz`,
  Git blob `a612fd2fe6ad41d0edc91b6f8a4a92fcd7d6d471`. Its 740 rows become 409
  under the trainer's original identity deduplication. All five historical fold
  counts/boundaries and full-development bucket means match. The diagnostic
  input was losslessly reserialized; complete parsed JSON equality was verified.
- Forward: [run 33137533796](https://github.com/mjfrieden/orographic/actions/runs/33137533796),
  artifact **9672807517**, `orographic-live-research-data`, created August 28.
  Its strict JSON contains 293 source rows and 146 scored rows matching the
  historical cohort counts: 17 scans, 87 calls, 59 puts, August 11-21. Archive and member hashes were
  verified. All recorded selected identities and returns match.

This is strong corroboration, not proof of original-byte identity. Settings were
frozen for this reconstruction; original threshold-selection provenance remains
unknown. No date filter, new sample selection, or threshold sweep was introduced.

## Fixed implementation and reproduction

Code: `4216be36ac210e548a56fcbad8c33bf2a165ed5e`. All 240 retained source files
matched that commit's Git blobs. Corrected trainer SHA-256:
`6491eaf3bfb64430c23b6ec55a80f9c0a9bd4a8c111f9706fdd34637abc46190`.

HistGradientBoostingClassifier used learning rate 0.04, 180 iterations, depth 3,
minimum leaf 15, L2 1.0, seed 42, balanced weights, and five purged folds. Each
fold used only its training returns for clipped [-1, 3] bucket utilities. Gates
stayed at utility >= 0.5582922181314538, big-win probability >=
0.3916891329717107, and severe-loss probability <= 0.65. Development labels were
available by July 17, before the August 11 forward start; identity overlap was 0.

Use a disposable checkout of the pinned commit, an existing environment with the
versions in the JSON summary, and existing artifact access. Download only the
preserved artifact; do not dispatch workflows or fetch replacement market data.
Artifacts have retention limits, so a missing artifact is a reproducibility
blocker, not permission to regenerate different inputs.

```bash
git checkout --detach 4216be36ac210e548a56fcbad8c33bf2a165ed5e
mkdir -p output/research/reconstructed-tail-2026-10-08/inputs
gh run download 33137533796 --repo mjfrieden/orographic \
  --name orographic-live-research-data \
  --dir output/research/reconstructed-tail-2026-10-08/archive
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python - <<'PY'
import gzip, hashlib, json, runpy, socket, sys
from pathlib import Path

root = Path('output/research/reconstructed-tail-2026-10-08')
seed = Path('data/evidence_seed/strict_option_outcomes.json.gz').read_bytes()
assert hashlib.sha256(seed).hexdigest() == '77310cf2ab7d5e7954847574830e6e4a56b92427e18df8ba971166396d31bbeb'
raw = gzip.decompress(seed)
assert hashlib.sha256(raw).hexdigest() == '6985428065644bedf200ecead8a2f2a196652d55343c156ba4bf23be62e27dff'
development = root / 'inputs/development.json'
development.write_text(json.dumps(json.loads(raw)), encoding='utf-8')
forward = root / 'archive/output/research_datasets/strict_option_outcomes.json'
trainer = Path('scripts/train_production_tail_ranker.py')
for path, expected in [
    (development, 'd6c5e8fcefa0276844a472eb0685812e1e4c08f84bac86a2d7b3b83a4ef72804'),
    (forward, '035025f5e708dcee04fb5cbf65b9d71de5bd72f87aa48288b05f8960b903f3ca'),
    (trainer, '6491eaf3bfb64430c23b6ec55a80f9c0a9bd4a8c111f9706fdd34637abc46190'),
]:
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, path
model, card = root / 'candidate.pkl', root / 'candidate-card.json'
assert not model.exists() and not card.exists(), 'Refusing to overwrite an existing run'
def offline(*args, **kwargs):
    raise RuntimeError('Network disabled for retrospective diagnostic')
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = offline
sys.argv = [str(trainer), '--development', str(development), '--forward', str(forward),
            '--output-model', str(model), '--output-card', str(card)]
runpy.run_path(str(trainer), run_name='__main__')
PY
```

The existing trainer's candidate schema is retained only for offline replay;
generated artifacts confer no live-routing authority. The repository change
contains only this report and its sanitized summary, with no model, source rows,
deployment change, or additional run. Historical dependency versions were not
fully recorded; original pickle-byte identity is not claimed.

Existing standard public CI may make read-only public-market calls during its
snapshot smoke check. That established check is separate from this network-blocked
diagnostic and supplies no new forward-performance evidence. This docs-only change
does not dispatch operational, broker, paid-data, R2, or deployment workflows.
