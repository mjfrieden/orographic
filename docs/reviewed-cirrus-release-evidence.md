# Optional reviewed Cirrus release evidence

The existing curated summary may supply `reviewed_release_evidence` on each of `cirrus-hybrid`, `cirrus-boosting`, and `cirrus-tail-ensemble`. This patch supplies no real release values and does not modify the published summary or generated snapshot. A separate review and publication decision is required before adding any private-source metadata.

The synthetic fixture at `tests/fixtures/cirrus_reviewed_release.synthetic.json` defines the accepted fields. It is test data, not a model release. Release ID and declared model hash identify reviewed historical artifact metadata; they do not establish current serving. Build time does not populate `trained_at`, and release ID does not overwrite the existing version field. Source-data end and training-label cutoff are separate calendar dates. Artifact observation, release-evidence review, overall source verification, and scan timestamps remain separate.

Timestamps use canonical second-precision UTC (`YYYY-MM-DDTHH:MM:SSZ`); dates use valid `YYYY-MM-DD`. No future timestamps are accepted. Training-label cutoff must be no later than source-data end; source-data end cannot follow build day; build <= historical observation <= release-evidence review. The names array has 1–64 unique ASCII feature identifiers (at most 64 characters), and its length must equal fitted-feature count. Optional candidate-pool count is null/absent or an integer from the fitted count through 10,000; it is never substituted for fitted inputs. The release ID is at most 96 ASCII identifier characters, and the hash is 64 lowercase hexadecimal characters.

The builder projects only the listed fields. Missing or malformed optional evidence is withheld locally while existing cards remain available. The renderer independently checks optional evidence and uses text nodes. Discovery-study cards never receive the disclosure. No performance metrics, promotion status, runtime identity, trading authority, collectors, endpoints, workflows, or dependencies change.

Offline checks:

- `python -m unittest engine.tests.test_reviewed_release_evidence engine.tests.test_current_model_cards -v`
- `node --test tests/js/reviewed_release_evidence.test.js tests/js/current_model_cards.test.js tests/js/model_history.test.js`

These focused checks do not replace full repository CI or browser layout/accessibility QA.
