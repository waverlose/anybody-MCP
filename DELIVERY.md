# AnyBody MCP Delivery

## Delivered toolkit root

- `D:\Python projects\anybody-automation`

## Main entrypoints

- MCP server (29 tools): `src\anybody_mcp_server.py`
- CLI wrapper: `scripts\run_anybody.py`
- No-GUI gait workflow: `scripts\run_no_gui_workflow.py`
- No-GUI new-posture workflow: `scripts\run_new_posture_workflow.py`
- One-click launcher: `start-anybody-mcp.bat`
- MCP config sample: `mcp-config.anybody.json`

## Workflow diagram

- `anybody-mcp-workflow.png`

## Ready-to-open successful GUI demo

- Project: `workspace\gui-gait-demo\GaitVaughan.main.any`
- Saved result: `workspace\gui-gait-demo\Output\GaitVaughan.main.anydata.h5`

## Stable verified capabilities

### Basic
- Start MCP server from isolated Python environment
- Create writable AnyBody projects from template
- Scan project files
- Read and replace text in `.any` files
- Run AnyBody main files and operations
- Print / printdown raw model object values
- Parse numeric values from printed AnyBody objects

### Joint extraction
- Left/right knee reaction forces (RefFrameOutput.F)
- Left/right hip reaction forces (RefFrameOutput.F)
- Left/right ankle reaction forces (RefFrameOutput.F)
- L5/S1 spinal compression force (Fout)
- Full-chain extraction: all 4 joints simultaneously in one run

### Workflows
- Gait knee force (bilateral)
- New posture (with preset fallback)
- Structured state description
- Task spec JSON (unified entry point)
- Hand load application (AnyForce3D on PalmJoint, bilateral)

### Parametric sweep
- Multi-dimensional sweep across trunk/knee/load
- Full-chain joint extraction per case
- Auto-save to JSON with summary statistics

## Verified data (Desktop research project)

Path: `C:\Users\A\Desktop\LiftingPostureResearch`
- 64-combination full-chain sweep (4 trunk x 4 knee x 4 load)
- 100% success rate
- Excel output: `results/full_chain_results.xlsx`
- JSON output: `results/full_chain_results.json`
- Paper draft: `results/full_chain_draft.md`

## Recommended first-use workflow

1. Start `start-anybody-mcp.bat`
2. Connect your MCP client using `mcp-config.anybody.json`
3. Call `get_installation_info`
4. Call `run_default_example` to verify solver
5. Try `summarize_knee_force` on a model
6. Try `get_full_chain_joint_forces` to extract all 4 joints
