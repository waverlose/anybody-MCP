# AnyBody Automation

Minimal local automation and MCP bridge for `AnyBody Modeling System` on this machine.

## What is included

- `src/anybody_runner.py`: Python wrapper around `AnyBodyCon.exe`
- `scripts/run_anybody.py`: simple CLI entrypoint
- `src/anybody_mcp_server.py`: MCP server exposing AnyBody tools
- `.venv/`: isolated Python environment for MCP runtime
- `template/`: standard project metadata and layout notes
- `start-anybody-mcp.bat`: one-click MCP server launcher
- `mcp-config.anybody.json`: ready-to-copy MCP configuration example
- `DELIVERY.md`: final handoff summary

## Verified local paths

- `C:\Program Files\AnyBody Technology\AnyBody.7.3\AnyBodyCon.exe`
- `C:\Program Files\AnyBody Technology\AnyBody.7.3\AMMR\Tools\Templates\Human Standing\Human Standing.main.any`

## CLI examples

Run the verified standing example:

```powershell
D:\Python projects\anybody-automation\.venv\Scripts\python.exe "D:\Python projects\anybody-automation\scripts\run_anybody.py" --default-standing
```

Run a specific model and operation:

```powershell
D:\Python projects\anybody-automation\.venv\Scripts\python.exe "D:\Python projects\anybody-automation\scripts\run_anybody.py" --model "C:\path\to\model.main.any" --command "operation Main.RunApplication" --command run --json
```

Run a full no-GUI gait workflow and output knee force summary:

```powershell
D:\Python projects\anybody-automation\.venv\Scripts\python.exe "D:\Python projects\anybody-automation\scripts\run_no_gui_workflow.py" --project-dir "D:\Python projects\anybody-automation\workspace\auto-gait-run" --overwrite
```

Run a full no-GUI new-posture workflow:

```powershell
D:\Python projects\anybody-automation\.venv\Scripts\python.exe "D:\Python projects\anybody-automation\scripts\run_new_posture_workflow.py" --project-dir "D:\Python projects\anybody-automation\workspace\auto-posture-run" --preset "forward-lean" --overwrite
```

## MCP server

The project now includes an isolated Python environment at:

- `D:\Python projects\anybody-automation\.venv\Scripts\python.exe`

Start the server:

```powershell
D:\Python projects\anybody-automation\.venv\Scripts\python.exe "D:\Python projects\anybody-automation\src\anybody_mcp_server.py"
```

## Exposed MCP tools

- `get_installation_info`
- `run_default_example`
- `run_model`
- `write_run_result`
- `inspect_project`
- `create_project`
- `create_named_project`
- `apply_named_posture_preset`
- `read_project_file`
- `replace_in_project_file`
- `print_model_object`
- `get_model_object_value`
- `get_model_object_value_after_operation`
- `summarize_knee_force`
- `summarize_bilateral_knee_forces`
- `run_no_gui_gait_knee_force_workflow`
- `run_no_gui_new_posture_workflow`

## Standard project workflow

1. Create a writable local project copy with `create_project`
2. Scan the project with `inspect_project`
3. Read and edit `.any` files with `read_project_file` and `replace_in_project_file`
4. Run the main model with `run_model`
5. Extract values with `print_model_object`
6. Parse and summarize forces with `get_model_object_value` or `summarize_bilateral_knee_forces`

## No-GUI workflow

If your goal is to avoid AnyBody GUI entirely, start with the built-in high-level workflow:

1. Call `run_no_gui_gait_knee_force_workflow`
2. It will create a fresh `gait-vaughan` project copy
3. It will run the model through `AnyBodyCon`
4. It will extract bilateral knee reaction forces
5. It will save a JSON report to the project's `Output` directory

This is the current best demonstration of "AI writes/runs/extracts results without opening GUI".

For new states not already present in AnyBody examples, start with posture presets such as:

- `forward-lean`
- `mild-squat`
- `staggered-stance`

These are applied to the `human-standing` template and will automatically retry a milder fallback preset if the first posture does not solve.

## Ready GUI demo

You already have one stable, successful, GUI-openable motion example:

- Project: `D:\Python projects\anybody-automation\workspace\gui-gait-demo\GaitVaughan.main.any`
- Saved output: `D:\Python projects\anybody-automation\workspace\gui-gait-demo\Output\GaitVaughan.main.anydata.h5`

This project loads in AnyBody GUI and has already been run successfully with inverse dynamics and saved output.

## One-click server start

```powershell
D:\Python projects\anybody-automation\start-anybody-mcp.bat
```

## Suggested MCP config

```json
{
  "mcpServers": {
    "anybody": {
      "command": "D:\\Python projects\\anybody-automation\\.venv\\Scripts\\python.exe",
      "args": [
        "D:\\Python projects\\anybody-automation\\src\\anybody_mcp_server.py"
      ]
    }
  }
}
```
