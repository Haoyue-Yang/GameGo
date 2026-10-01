<div align="center">

# GameGo

### Training Game-Dev Agents with Synthetic Trajectories Anchored in Real-World Assets

[![Paper source](https://img.shields.io/badge/Paper%20source-ICLR%202027-b31b1b.svg)](./iclr2027_conference.tex)
[![Tasks](https://img.shields.io/badge/GameGoBench-124%20tasks-4c78a8.svg)](#gamegobench)
[![Training data](https://img.shields.io/badge/GameGoData-55%2C060%20trajectories-59a14f.svg)](#gamegodata)

**GameGo** is a data-centric framework for training coding agents to create playable browser games. It turns real-world game seeds into structured development specifications, selects a compact task-specific interface for rollout, and collects executable development trajectories as process supervision.

</div>

<p align="center">
  <img src="figure/T_soft_palette_overview_v2.png" alt="GameGo task-adaptive query construction results" width="100%" />
</p>

## Overview

Game creation is a long-horizon software task: an agent must coordinate gameplay rules, state transitions, controls, spatial interaction, assets, visual presentation, and runtime verification in one executable artifact. Short user requests leave these dependencies implicit, while exhaustive specifications can burden the rollout agent with unnecessary commitments.

GameGo addresses this task-interface gap with an **expand-then-project** pipeline:

```text
real-world game seed
        │
        ▼
structured planning and asset grounding
        │
        ▼
full Product Requirements Document (PRD)
        │
        ▼
task-adaptive compact query
        │
        ▼
teacher-agent rollout in a sandbox
        │
        ▼
validated trajectory + runnable game artifact
        │
        ▼
GameGoCoder supervised fine-tuning
```

The PRD is a coverage-oriented internal representation. The compact query preserves task identity, executable dependencies, controls, spatial contracts, and success or recovery paths while leaving routine implementation choices open to the coding agent.

## Highlights

- **Real-world grounded task synthesis.** Game seeds are collected from public game catalogs and normalized before planning.
- **Three-dimensional coverage.** GameGoData spans 2D, 2.5D, and 3D browser games across 20 gameplay categories.
- **Process-grounded supervision.** Each retained example contains a development trajectory paired with a verified runnable artifact.
- **Adaptive task interfaces.** Query detail grows with task complexity instead of applying one fixed prompt length.
- **End-to-end evaluation.** GameGoCoder is evaluated on ArtifactsBench-G, CookieBench-G, and the held-out GameGoBench.

## GameGoData

GameGoData contains **55,060** filtered development trajectories. The data pipeline records teacher-agent messages, tool calls, tool responses, and the final project, then keeps examples that pass execution, artifact-integrity, and formatting checks.

<p align="center">
  <img src="figure/train_test_spaced.png" alt="GameGoData and GameGoBench composition" width="100%" />
</p>

| Split | Count | Description |
| --- | ---: | --- |
| GameGoData | 55,060 | Training trajectories with runnable browser-game artifacts |
| GameGoBench | 124 | Held-out game-development tasks for evaluation |

GameGoData composition is **61.4% 2D**, **20.1% 2.5D**, and **18.5% 3D**. GameGoBench contains **47 2D**, **22 2.5D**, and **55 3D** tasks, stratified into Easy, Medium, and Hard difficulty levels.

## GameGoBench

GameGoBench is a held-out benchmark for evaluating game-development agents under a shared browser-game scaffold and sandbox budget. Evaluation considers:

- execution and runtime success;
- satisfaction of task requirements;
- visual and interaction quality.

The benchmark is kept separate from the training seed pool from the task-construction stage onward.

## Results

GameGoCoder is trained with standard supervised fine-tuning on GameGoData. On the held-out GameGoBench, it improves over its matched base models:

| Model | Execution | Requirements | Quality | Overall |
| --- | ---: | ---: | ---: | ---: |
| Qwen3.5-27B | 91.80 | 36.89 | 31.12 | 34.00 |
| **GameGoCoder 3.5** | **98.29** | **57.99** | **45.72** | **51.85** |
| Qwen3.8-27B | 100.00 | 62.81 | 42.25 | 52.53 |
| **GameGoCoder 3.8** | **100.00** | **66.94** | **43.64** | **55.29** |

The task-construction ablation on 124 benchmark tasks shows that compact task-specific queries are preferred over full PRDs in **85.7%** of non-tied judgments and over direct queries in **86.6%**. They achieve a median **3.79× relation-density gain** while retaining a concise, complexity-adaptive length.

<p align="center">
  <img src="figure/pairwise_result_v2.png" alt="Human pairwise preference results" width="100%" />
</p>

## Repository contents

This repository snapshot contains the paper source and publication figures:

```text
.
├── iclr2027_conference.tex       # Paper source
├── iclr2027_conference.bib       # Bibliography
├── math_commands.tex             # LaTeX math commands
├── figure/                       # Overview, analysis, and qualitative figures
└── README.md
```

The data-manufacturing pipeline and benchmark release are maintained separately and will be linked here as their public release packages are attached to the `GameGo` repository.

## Paper

- [Paper source](./iclr2027_conference.tex)
- [Bibliography](./iclr2027_conference.bib)

## Citation

If you find GameGo useful, please cite:

```bibtex
@inproceedings{gamego2027,
  title     = {GameGo: Training Game-Dev Agents with Synthetic Trajectories Anchored in Real-World Assets},
  author    = {Yang, Haoyue and others},
  booktitle = {International Conference on Learning Representations},
  year      = {2027}
}
```

The author list and final bibliographic fields will be updated with the camera-ready version.

## Contact

For questions and collaboration, please open an issue in the [GameGo repository](https://github.com/Haoyue-Yan/GameGo).
