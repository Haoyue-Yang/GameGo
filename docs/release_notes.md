# Release scope and portability

This package retains the selected image-input and text-input planning code, stage prompts, skill routing, rendering branches, and gameplay profiles. The original source directories are not modified.

Deployment-specific changes:

- API endpoints, credentials, planning models, vision models, and compaction models are supplied by the user.
- Internal runner paths and container registry defaults have been removed. Optional legacy execution hooks require an explicit external runner.
- Provider-specific temperature exceptions are replaced by `GAMEGO_OMIT_TEMPERATURE`.
- API calls no longer send the internal proxy session header and respect the environment's proxy configuration.
- Personal paths, old batch launchers, monitoring tools, production logs, run results, and copied third-party dependency caches are excluded.
- Gameplay profiles are included because both pipelines require them.
- Synthetic input examples are written specifically for this package.

The two bundled pipelines contain Protected Domain Compact. They do not include the separate later adaptive query conversion stage, the execution sandbox, the training set, or the complete evaluation pipeline.

Offline tests validate adapters, routing, contracts, and export helpers. They do not establish end-to-end model output quality or reproduce paper scores. No paid model generation is performed while preparing this release.

Two historical tests requiring a separate historical pipeline are skipped. Any adjustments to obsolete test fixtures are limited to matching the current asset contract and do not relax production validation.

The shader technique catalog contains reference metadata and source attribution links, not original shader implementations. Its existing per-entry usage and license-status fields are retained. No new blanket license is assigned to third-party material.
