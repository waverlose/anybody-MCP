# AnyBody MCP Delivery

## Delivered toolkit root

- `D:\Python projects\anybody-automation`

## Main entrypoints

- MCP server: `src\anybody_mcp_server.py`
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

- Start MCP server from isolated Python environment
- Create writable AnyBody projects from template
- Scan project files
- Read and replace text in `.any` files
- Run AnyBody main files and operations
- Print raw model object values
- Parse numeric values from printed AnyBody objects
- Summarize left/right knee reaction forces

## Recommended first-use workflow

1. Start `start-anybody-mcp.bat`
2. Connect your MCP client using `mcp-config.anybody.json`
3. Call `get_installation_info`
4. Open `workspace\gui-gait-demo\GaitVaughan.main.any` in AnyBody GUI
5. Call `summarize_bilateral_knee_forces` on the same model to verify extraction
