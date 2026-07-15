# SceneExchangeContracts

This repository is the only source of truth for contracts exchanged by
TriggerEngine, NeuralSceneBridge, and ClosedLoopBench.

Canonical identity is the 32-character lowercase nuScenes scene token.
`scene_name` is query/display metadata only. Scenario IR uses
`scene_local_ego_start` (x forward, y left, metres, seconds, degrees).

Ownership is intentionally split:

- NeuralSceneBridge produces `reconstruction_package.v1` and
  `reconstruction_result.v1`.
- ClosedLoopBench produces `closed_loop_scene_package.v1` and
  `evaluation_run_result.v1`.
- TriggerEngine produces `scenario_ir.v1` and consumes evaluation results as
  `scenario_feedback.v1`.

Each project imports this package's validator. Local copied Schema files are
not contract sources.
