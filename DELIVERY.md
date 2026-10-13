# AnyBody MCP Delivery

## Delivered toolkit root

- The repository root (previously documented as `E:\anybody-automation`).
  `start-anybody-mcp.bat` resolves its own directory, so the checkout can live
  anywhere.

## Main entrypoints

- MCP server (37 tools, 4 resources, 4 prompts): `src\anybody_mcp_server.py`
- Core automation layer: `src\anybody_runner.py`
- Knowledge index: `src\knowledge_index.py`
- CLI wrapper: `scripts\run_anybody.py`
- No-GUI gait workflow: `scripts\run_no_gui_workflow.py`
- No-GUI new-posture workflow: `scripts\run_new_posture_workflow.py`
- One-click launcher: `start-anybody-mcp.bat`
- MCP config template: `mcp-config.anybody.json`

## Technology stack

- **FastMCP 3.3.1** — Python MCP framework
- **MCP SDK 1.27.0** — Model Context Protocol
- **Python 3.11** — isolated virtual environment
- Runtime dependencies pinned in `requirements.txt`; test dependencies in
  `requirements-dev.txt`

## Configuration

Paths default to an AnyBody 7.3 install and are overridable by environment
variable, so an upgraded or relocated installation needs no code change:

`ANYBODY_INSTALL_DIR`, `ANYBODY_AMMR_DIR`, `ANYBODYCON_PATH`,
`ANYBODY_DEFAULT_MODEL`, `ANYBODY_CAMERA_TEMPLATE`, `ANYBODY_STDOUT_LIMIT`,
`ANYBODY_MAX_SWEEP_CASES`.

`get_installation_info` (and the `anybody://installation` resource) reports the
resolved values and which ones came from the environment.

## Workflow diagram

- `anybody-mcp-workflow.png`

## Ready-to-open successful GUI demo

- Project: `workspace\gui-gait-demo\GaitVaughan.main.any`
- Saved result: `workspace\gui-gait-demo\Output\GaitVaughan.main.anydata.h5`

## Stable verified capabilities

### Basic
- Start MCP server from isolated Python environment
- Create writable AnyBody projects from template
- Scan project files, separating main from support `.any` files
- Read and replace text in `.any` files
- Run AnyBody main files and operations
- Print / printdown raw model object values
- Parse numeric values from printed AnyBody objects

### Joint extraction
- Left/right knee, hip and ankle reaction forces (RefFrameOutput.F)
- L5/S1 spinal compression force (Fout)
- Full-chain extraction: all 4 joints simultaneously in one run
- Bilateral queries cost one solve, with a symmetry percentage

### Workflows
- Gait knee force (bilateral)
- New posture (with preset fallback)
- Structured state description (reports ignored/unapplied keys)
- Task spec JSON (unified entry point, validated)
- Hand load application (AnyForce3D on PalmJoint, bilateral)

### Parametric sweep
- Multi-dimensional sweep across trunk/knee/load
- Full-chain joint extraction per case, cached per project fingerprint
- Per-case checkpointing under `<output_dir>/cases/`, so sweeps resume
- Per-case retry; a single failing case does not abort the sweep
- Progress notifications to the MCP client and cancellation at case boundaries
- Refuses to start above `max_cases` (default 512)
- Outputs `sweep_results.json`, `sweep_results.csv`, optional L5/S1 heatmap PNG
- Summary statistics (mean/std/min/max per joint, symmetry index)

### Cache
- Full-chain results cached against a fingerprint of every `.any` file in the
  project, so editing `Model/Mannequin.any` invalidates correctly
- `clear_result_cache` forces a re-solve; cache hits report `cached: true`

### Animation export
- BVH motion capture format export
- CSV per-frame joint angle time series
- GIF stick figure animation
- MP4 3D cylinder bone rendering
- Native AnyBody headless video rendering
- The overlaid force panel is labelled as an end-state snapshot, not a per-frame
  trace; `show_values` is off by default because it costs three extra runs

### Knowledge index
- Crawl whitelisted documentation and PubMed into a local JSONL store
- SQLite FTS5 index over local project files plus crawled sources
- Fault-tolerant loading: one damaged stored record does not break the corpus

## MCP Resources

- `anybody://installation` — paths, limits and environment overrides
- `anybody://presets` — posture preset catalog
- `anybody://knowledge-sources` — whitelisted crawl sources
- `anybody://knowledge-stats` — knowledge index status

## MCP Prompts

- `gait_analysis_workflow` — step-by-step gait analysis guide
- `posture_study_workflow` — posture study with preset options
- `parametric_sweep_guide` — multi-dimensional sweep setup
- `animation_export_guide` — animation format selection

## Tests

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

`tests/unit/` holds the maintained suite (78 tests, no AnyBody or license
required). The top-level `tests/*.py` are historical debug scripts; pytest
collection is scoped to `tests/unit` in `pyproject.toml`.

## Validation

The full-chain sweep has been exercised end to end at 64 combinations
(4 trunk x 4 knee x 4 load) with a 100% success rate, and the values it parses
agree exactly with the dedicated per-joint extraction path.

## Recommended first-use workflow

1. Start `start-anybody-mcp.bat`
2. Connect your MCP client using `mcp-config.anybody.json`
3. Call `get_installation_info`
4. Call `run_default_example` to verify the solver
5. Call `get_full_chain_joint_forces` — one solve for all four joints
6. Call `summarize_bilateral_knee_forces` for both sides from the same run

## Error handling

Tool bodies are wrapped by `_safe_call()`; exceptions are caught and returned as
structured error dicts instead of crashing the client connection:

```json
{"error": "description", "error_type": "AnyBodyRunnerError", "tool": "tool_name"}
```

`error_type` distinguishes an invalid argument from a solver failure from a
timeout. Tracebacks are logged to stderr only — stdout carries JSON-RPC frames
and nothing else.
