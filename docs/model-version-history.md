# Atmosphere model version history

The read-only history view follows the current model cards on Atmosphere. It does
not change scoring, training, scheduling, broker requests, or deployment authority.

## Evidence contract

`web/atmosphere/model-history-catalog.js` is a manually reviewed, bounded catalog
of artifact-manifest revisions in `mjfrieden/orographic`, pinned through commit
`f9c6cc593ab7987774d8c249d37aab43d0e74a0d`. The review used the GitHub commits API
with that exact `sha` and `path=engine/orographic/models/artifact_manifest.json`,
then fetched the manifest and source cards at each returned immutable commit.
Eight retained revisions were returned, April 23 through August 30, 2026.

The view distinguishes:

- Repository commit timestamp: a change recorded in source control, not a served
  deployment timestamp or independent proof that a promotion succeeded.
- Artifact identity: the manifest's declared SHA-256. Binary bytes are not
  independently hashed by this view.
- Source-card version and training date: copied from the source card only after
  verifying its exact bytes match the manifest's source-card SHA-256. Card versions
  can be reused across different hashes. Missing training dates stay missing.
- Policy effective date: a declaration in the manifest, not a deployment receipt.
  Disclosures show each changed policy or decision field with its before/after
  value; an absent declaration is distinguished from false.
- Runtime observation: the separately fetched current-card snapshot, with model,
  source-card and applicable scaler identity matched against the latest reviewed
  manifest. A newer scan cannot add a history entry or make this catalog fresh.
- Earliest retained baseline: not the first release. Absence from a later manifest
  is not proof of the actual date on which serving stopped.

The catalog intentionally contains no performance metrics. Historical model-card
links preserve the evaluation context without creating cross-model comparisons.
Cirrus curated registry and study dates do not establish served-release history.
No deployment receipts are available in this catalog; the UI explicitly says so.

## Safe maintenance

To extend the history, review manifest-path commits up to a new immutable revision.
Fetch each manifest and relevant source card through a read-only repository tool.
Verify the fetched blob identity and the exact source-card SHA-256 against its
manifest before recording versions or training dates. Retain previous revisions;
never turn a repeated scan, a card schema change, or a training date into a release.
Use commit committer dates for repository ordering. Preserve the distinct policy
and training dates even when they differ.

Only copy the displayed metadata allowlist into the catalog: commit, committed_at,
title, manifest_blob, manifest_sha256, artifact path/hash/role/required, optional
card version/training date/path/hash, policy, and selected decision fields. Do not
copy developer filesystem paths, authors' contact information, credentials, account
information, model binaries, or raw trading records. Validate bounded schema,
review source links, and run the named offline tests below. Catalog updates need a
source-verified code review; scan refresh does not maintain the catalog.

A future served-release timeline requires an independently sourced deployment
receipt tying system, artifact and policy identities to the actual serving time.
It must remain a different event type from repository changes and observations.
This change does not install collection automation or invent missing receipts.

## Offline checks

Run only these named tests for the view:

    node --test tests/js/model_history.test.js tests/js/current_model_cards.test.js

They cover hash replacements with reused version numbers, removals, metadata-only
changes, initial baselines, source-card/scaler matching, malformed catalogs, bounded allowlisted policy/decision fields, safe
links/text, repeated filtering and empty states, and existing refresh/auth/race/
timeout/back-forward behavior. Browser visual validation is separate and is not
established by these source-level DOM tests.
