# MCP-for-anybody

`MCP-for-anybody` lets an AI agent use `AnyBody Modeling System` without opening the GUI.

![AnyBody MCP Workflow](./anybody-mcp-workflow.png)

It can:

- create a local AnyBody project copy
- run `AnyBodyCon.exe`
- extract result objects
- calculate joint reaction forces (L5/S1, hip, knee, ankle)
- run parametric sweeps across posture/load combinations
- export animation data (BVH, CSV, GIF, MP4)
- index local and crawled literature for retrieval

This repository is a **working version** focused on local Windows usage with FastMCP 3.x.

## Who this is for

If you are new to MCP or AnyBody, start here.

You do **not** need to understand the full codebase to try it.

## What is included

- `src/anybody_mcp_server.py`: MCP server (37 tools, 4 resources, 4 prompts)
- `src/anybody_runner.py`: local AnyBody automation helpers
- `src/knowledge_index.py`: SQLite FTS index over project files and crawled sources
- `scripts/run_no_gui_workflow.py`: no-GUI gait example
- `scripts/run_new_posture_workflow.py`: no-GUI new-posture example
- `scripts/run_state_workflow.py`: no-GUI structured state example
- `scripts/run_task_spec.py`: one-file task spec runner
- `start-anybody-mcp.bat`: one-click launcher (resolves its own directory)
- `mcp-config.anybody.json`: example MCP client config
- `requirements.txt`, `requirements-dev.txt`: runtime and test dependencies
- `tests/unit/`: the maintained pytest suite
- `tests/*.py`: historical one-off debug scripts (excluded from pytest collection)
- `template/`: task spec JSON examples

## Requirements

1. Windows
2. AnyBody installed locally. The default location is detected automatically;
   override it with `ANYBODY_INSTALL_DIR` (see [Configuration](#configuration)).
3. Python 3.11+
4. `ffmpeg` on `PATH` only for MP4 output from `export_animation`. GIF export
   and every simulation feature work without it.

## Quick Start

### 1. Install dependencies

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

To run the test suite as well:

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
```

### 2. Start the MCP server

Double-click `start-anybody-mcp.bat`, or run it from your checkout:

```powershell
<repo>\start-anybody-mcp.bat
```

### 3. Add this server to your MCP client

Both paths must be absolute. `mcp-config.anybody.json` is a template:

```json
{
  "mcpServers": {
    "anybody": {
      "command": "<repo>\\.venv\\Scripts\\python.exe",
      "args": ["<repo>\\src\\anybody_mcp_server.py"]
    }
  }
}
```

### 4. First test

After your MCP client connects, call:

- `get_installation_info`

If that works, the server is connected correctly.

## Configuration

Every path defaults to a standard AnyBody 7.3 install. Set any of these
environment variables to point somewhere else (an upgraded version, a different
drive) without editing code. `get_installation_info` reports which ones are
active under `env_overrides`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANYBODY_INSTALL_DIR` | `C:\Program Files\AnyBody Technology\AnyBody.7.3` | Installation root; everything else derives from it |
| `ANYBODY_AMMR_DIR` | `<install>\AMMR` | AMMR library location |
| `ANYBODYCON_PATH` | `<install>\AnyBodyCon.exe` | Solver executable |
| `ANYBODY_DEFAULT_MODEL` | `<AMMR>\Tools\Templates\Human Standing\Human Standing.main.any` | Default model |
| `ANYBODY_CAMERA_TEMPLATE` | `<AMMR>\Tools\ModelUtilities\Video\CameraClassTemplate.any` | Camera class for animation export |
| `ANYBODY_STDOUT_LIMIT` | `4000` | Console characters returned per tool result |
| `ANYBODY_MAX_SWEEP_CASES` | `512` | Default refusal threshold for `run_parametric_sweep` |

## MCP Server Features

### Tools (37 total)

#### Basic
- `get_installation_info` — verify installation, paths, limits and active overrides
- `run_default_example` — run the Human Standing example
- `run_model` — load and run any .main.any file
- `write_run_result` — run and save the raw result as JSON
- `inspect_project` — scan project directory (main vs. support files)
- `create_project` / `create_named_project` — create writable project copies

#### Posture & State
- `apply_named_posture_preset` — forward-lean, mild-squat, staggered-stance
- `apply_state_to_project` — structured posture parameters; reports ignored and
  unapplied keys rather than failing silently
- `read_project_file` / `replace_in_project_file` — read/edit .any files

#### Joint Extraction
- `summarize_knee_force` / `get_hip_force` / `get_ankle_force` — one side, one solve
- `summarize_bilateral_knee_forces` / `get_bilateral_hip_forces` /
  `get_bilateral_ankle_forces` — **both sides from a single solve**, plus a
  symmetry percentage
- `get_l5s1_compression` — spinal compression (Fout)
- `get_full_chain_joint_forces` — all 4 joints in one run (the efficient default)

#### Load Application
- `add_hand_load` — AnyForce3D on PalmJoint, bilateral

#### Parametric Sweep
- `run_parametric_sweep` — multi-dimensional sweep with full-chain extraction,
  per-case checkpointing, resume, retry, progress reporting and cancellation

#### Workflows
- `run_no_gui_gait_knee_force_workflow`
- `run_no_gui_new_posture_workflow`
- `run_no_gui_state_workflow`
- `run_task_spec_workflow` / `run_task_spec_file_workflow` — spec validation
  reports the offending key instead of raising a bare `KeyError`

#### Animation Export
- `export_animation` — BVH/CSV/GIF/MP4 from an AnyBody Dump
- `export_native_animation` — native AnyBody 3D rendered video (真实渲染骨骼肌肉)

#### Knowledge Index
- `get_knowledge_source_catalog` / `get_knowledge_index_stats`
- `crawl_professional_sources` — fetch whitelisted docs and PubMed records
- `rebuild_knowledge_index` / `search_knowledge` — build and query the FTS index

#### Cache
- `clear_result_cache` — drop cached full-chain results and force a re-solve

### Resources (4)

- `anybody://installation` — installation paths, limits and env overrides
- `anybody://presets` — available posture presets
- `anybody://knowledge-sources` — whitelisted crawl sources
- `anybody://knowledge-stats` — knowledge index status

### Prompts (4)

- `gait_analysis_workflow` — guide for gait analysis
- `posture_study_workflow` — guide for posture studies
- `parametric_sweep_guide` — guide for parametric sweeps
- `animation_export_guide` — guide for animation export

## Performance and cost

Every AnyBodyCon invocation is a full solver run, so the design tries to keep
the count down:

- **One solve, all joints.** `get_full_chain_joint_forces` prints every joint in
  a single run. The bilateral tools use it, so both sides cost one run instead of
  two. They fall back to a dedicated per-side run only for a side the combined
  parse could not recover.
- **Results are cached.** Full-chain results are cached against a fingerprint of
  every `.any` file in the project, so editing `Model/Mannequin.any` correctly
  invalidates the entry. A cache hit returns `cached: true` and omits `stdout`.
  Call `clear_result_cache` to force a re-solve.
- **Console output is truncated.** AnyBodyCon prints a large banner and progress
  log. Tool results keep only the head and tail (see `ANYBODY_STDOUT_LIMIT`);
  `include_stdout: true` on `get_full_chain_joint_forces` returns the full
  transcript, and `write_run_result` always writes the untruncated record to disk.
- **Sweeps checkpoint and resume.** Each completed case is written to
  `<output_dir>/cases/`. Re-running without `overwrite` reuses finished cases, so
  an interrupted sweep continues rather than restarting. `max_cases` (default
  512) refuses to start a sweep larger than the limit.

Two caveats worth knowing:

- The joint-force panel in `export_native_animation` output is an **end-state
  snapshot from one solved configuration**, labelled as such on the image. It is
  not a per-frame force trace.
- `show_values` on `export_native_animation` costs three extra solver runs on top
  of the recording run, so it is off by default.

## Testing

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite in `tests/unit/` covers parsing, cache invalidation, sweep
bookkeeping, retry/resume, file editing and the knowledge index. It stubs the
solver, so it needs neither AnyBody nor a license and runs in about a second.

`tests/*.py` at the top level are historical one-off debug scripts that execute
on import and launch AnyBody; `pyproject.toml` scopes collection to `tests/unit`
so a plain `pytest` run never touches them.

## Easiest demo

```powershell
<repo>\.venv\Scripts\python.exe "<repo>\scripts\run_no_gui_workflow.py" --project-dir "<repo>\workspace\auto-gait-run" --overwrite
```

Expected report: `workspace\auto-gait-run\Output\knee-force-summary.json`

## Full parametric sweep example

A 64-case parametric sweep (4 trunk x 4 knee x 4 load) with simultaneous L5/S1,
hip, knee, ankle extraction:

```python
run_parametric_sweep(
  project_dir="workspace/auto-state-run",
  trunk_angles=[0, 10, 20, 30],
  knee_angles=[0, 15, 30, 45],
  loads_kg=[0, 5, 10, 15],
  write_csv=True,
)
```

Each case is checkpointed under `sweep_results/cases/`, and the run also writes
`sweep_results.json` and `sweep_results.csv`.

## Workspace structure

```
<repo>\
  src\                    # MCP server, runner and knowledge index
  scripts\                # CLI workflow scripts
  template\               # Task spec JSON examples
  tests\unit\             # pytest suite
  tests\*.py              # historical debug scripts
  workspace\              # Project outputs and data
    auto-gait-run\        # Gait analysis projects
    auto-posture-run\     # Posture study projects
    auto-state-run\       # State-based projects
  requirements.txt        # Runtime dependencies
  start-anybody-mcp.bat   # One-click launcher
  mcp-config.anybody.json # MCP client config template
```

## Files for advanced users

- `DELIVERY.md` — implementation details
- `src/anybody_runner.py` — core automation logic
- `src/anybody_mcp_server.py` — MCP server with FastMCP 3.x
- `src/knowledge_index.py` — retrieval index
