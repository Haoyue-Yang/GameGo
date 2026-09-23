# GameGo

GameGo constructs game-development tasks through structured planning. This repository contains two task-construction pipelines:

| Directory | Input | Image handling |
|---|---|---|
| `harness_steam_v4` | Game descriptions, metadata, and optional screenshots | Up to five screenshots are supplied to Stage 1 |
| `harness_webgame_v5` | Game titles, descriptions, instructions, categories, and tags | Text only; source URLs are not fetched |

Both pipelines produce a seed specification, a game blueprint, and an asset contract. They select a canonical specification and apply Protected Domain Compact to export execution queries.

```text
Game seed
  -> Seed specification
  -> Game blueprint
  -> Asset contract
  -> Canonical specification and Protected Domain Compact
  -> Execution query JSONL
```

## Scope

This package includes planning prompts, rendering and gameplay routing, skill cards, asset validation, and query export. It does not include the training dataset, teacher credentials or identity, production execution infrastructure, model checkpoints, or training trajectories. The later adaptive query conversion policy is not included in these two harness snapshots. This repository is not a complete reproduction package for all paper experiments.

The batch JSONL interfaces below construct queries without executing games. Legacy execution hooks require a user-supplied runner and are not needed for query construction.

## Installation

Use Python 3.10 or later:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The Steam pipeline uses `tiktoken` when available. Its first use may download the public `cl100k_base` encoding. An approximate byte-based budget is used if `tiktoken` is not installed. WebGame uses `json-repair` to recover malformed JSON.

## Model configuration

Use an API that implements compatible `/v1/chat/completions` requests. Configure your own endpoint, credentials, and model IDs locally:

```bash
export GAMEGO_BASE_URL='https://your-provider.example/v1'
export GAMEGO_API_KEY='your-api-key'
export GAMEGO_MODEL='your-planning-model'
export GAMEGO_VISION_MODEL='your-vision-model'
export GAMEGO_COMPACT_MODEL='your-compaction-model'
```

Do not commit credentials. The vision and compaction models default to the planning model when unset. For image inputs, the selected vision model must accept image messages. If your provider rejects the temperature parameter, set `GAMEGO_OMIT_TEMPERATURE=1`.

## Image-capable pipeline

From the repository root:

```bash
python harness_steam_v4/lifecycle_harness_steam.py \
  --config harness_steam_v4/stages.json \
  --steam-jsonl examples/steam_seed.jsonl \
  --batch-limit 1 \
  --pipeline-workers 1 \
  --run-dir outputs/steam
```

The example is a newly written synthetic seed and contains no training or benchmark record. Add your own PNG or JPEG screenshot paths to `media.local_paths` to exercise image input. Paths are resolved from the working directory. The normalized example uses `input_contract: raw_game_v1` and `origin: steam`.

## Text-only pipeline

```bash
python harness_webgame_v5/lifecycle_harness_webgame.py \
  --config harness_webgame_v5/stages.json \
  --webgame-jsonl examples/webgame_seed.jsonl \
  --batch-limit 1 \
  --pipeline-workers 1 \
  --run-dir outputs/webgame
```

WebGame resumes completed records by default. Add `--no-resume` to recompute them. Steam supports `--resume`. Both interfaces support `--from-stage`, `--to-stage`, and `--item-retries`. WebGame additionally supports `--no-skill-cards`.

These commands call your configured model API and may incur provider charges.

## Output

Each run records stage artifacts, routing decisions, and selected specifications. The batch root contains `rle_inputs.jsonl` with one execution query per successful record:

```json
{"data_id": "example_001", "query": "Implementation instructions..."}
```

The final stage also writes `canonical_rle_query.md` and `compact_rle_query.md`. The historical `rle` filename prefix denotes the downstream execution interface; the batch pipeline does not invoke that runner.

Generated outputs can contain input metadata and local paths. They are ignored by Git and should be reviewed separately before sharing.

## Offline checks

```bash
python -m unittest discover -s harness_steam_v4 -p 'test_*.py'
python -m unittest discover -s harness_webgame_v5 -p 'test_*.py'
```

These tests do not call model APIs. Two historical comparisons against a separate, unreleased harness version are explicitly skipped. See `docs/release_notes.md` for portability changes and the limits of validation.

## Benchmark and demonstrations

The test-query package is maintained separately from this code repository. Training data are not distributed. Demonstrations will use videos; no live game hosting is included in this package.
