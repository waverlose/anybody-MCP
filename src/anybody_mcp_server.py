from __future__ import annotations

import json
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.anybody_runner import (
    apply_posture_preset,
    DEFAULT_ANYBODYCON,
    DEFAULT_HUMAN_STANDING,
    create_project_from_named_template,
    create_project_from_template,
    extract_object_value,
    extract_object_value_after_operation,
    no_gui_gait_knee_force_workflow,
    no_gui_new_posture_workflow,
    print_object,
    read_text_file,
    replace_text_in_file,
    run_default_standing_model,
    run_macro,
    scan_project,
    summarize_bilateral_knee_reaction_forces,
    summarize_knee_reaction_force,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("anybody", json_response=True)


@mcp.tool()
def get_installation_info() -> dict:
    """Return the detected local AnyBody installation paths and defaults."""
    return {
        "anybodycon": str(DEFAULT_ANYBODYCON),
        "default_model": str(DEFAULT_HUMAN_STANDING),
        "anybodycon_exists": DEFAULT_ANYBODYCON.exists(),
        "default_model_exists": DEFAULT_HUMAN_STANDING.exists(),
    }


@mcp.tool()
def run_default_example() -> dict:
    """Run the verified Human Standing example through AnyBodyCon."""
    result = run_default_standing_model()
    return result.to_dict()


@mcp.tool()
def run_model(model: str, operation: str | None = None, run_operation: bool = True, extra_commands: list[str] | None = None) -> dict:
    """Load an AnyBody main file and optionally run an operation.

    Args:
        model: Absolute path to a .main.any file.
        operation: Full operation name, for example Main.RunApplication.
        run_operation: Whether to append the run command.
        extra_commands: Extra macro commands appended before exit.
    """
    commands: list[str] = []
    if operation:
        commands.append(f"operation {operation}")
    if run_operation and operation:
        commands.append("run")
    if extra_commands:
        commands.extend(extra_commands)
    result = run_macro(model=model, commands=commands)
    return result.to_dict()


@mcp.tool()
def write_run_result(output_path: str, model: str, operation: str | None = None) -> dict:
    """Run a model and save the raw execution result as JSON."""
    commands: list[str] = []
    if operation:
        commands.extend([f"operation {operation}", "run"])
    result = run_macro(model=model, commands=commands)
    target = Path(output_path)
    target.write_text(json.dumps(result.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")
    return {"saved_to": str(target), "result": result.to_dict()}


@mcp.tool()
def inspect_project(project_dir: str) -> dict:
    """Scan an AnyBody project directory and list main and any files."""
    return scan_project(project_dir)


@mcp.tool()
def create_project(destination: str, overwrite: bool = False) -> dict:
    """Create a writable local AnyBody project from the verified Human Standing template."""
    return create_project_from_template(destination=destination, overwrite=overwrite)


@mcp.tool()
def create_named_project(destination: str, template_name: str, overwrite: bool = False) -> dict:
    """Create a writable local AnyBody project from a named template such as human-standing or gait-vaughan."""
    return create_project_from_named_template(destination=destination, template_name=template_name, overwrite=overwrite)


@mcp.tool()
def apply_named_posture_preset(project_dir: str, preset_name: str) -> dict:
    """Apply a predefined new-state posture preset to a Human Standing project."""
    return apply_posture_preset(project_dir=project_dir, preset_name=preset_name)


@mcp.tool()
def read_project_file(file_path: str) -> dict:
    """Read a text file from an AnyBody project."""
    return read_text_file(file_path)


@mcp.tool()
def replace_in_project_file(file_path: str, old_text: str, new_text: str, count: int = -1) -> dict:
    """Replace text in a project file for parameter or script edits."""
    return replace_text_in_file(file_path=file_path, old_text=old_text, new_text=new_text, count=count)


@mcp.tool()
def print_model_object(model: str, object_name: str, deep: bool = False) -> dict:
    """Load a model and print an object value or folder subtree."""
    result = print_object(model=model, object_name=object_name, deep=deep)
    return result.to_dict()


@mcp.tool()
def get_model_object_value(model: str, object_name: str) -> dict:
    """Load a model, print an object, and parse the numeric value."""
    return extract_object_value(model=model, object_name=object_name)


@mcp.tool()
def get_model_object_value_after_operation(model: str, object_name: str, operation: str) -> dict:
    """Run an operation and then parse a printed numeric object value."""
    return extract_object_value_after_operation(model=model, object_name=object_name, operation=operation)


@mcp.tool()
def summarize_knee_force(model: str, side: str = "Right") -> dict:
    """Return a parsed knee reaction force vector and force magnitude for one side."""
    return summarize_knee_reaction_force(model=model, side=side)


@mcp.tool()
def summarize_bilateral_knee_forces(model: str) -> dict:
    """Return parsed left/right knee reaction forces and compare their magnitudes."""
    return summarize_bilateral_knee_reaction_forces(model=model)


@mcp.tool()
def run_no_gui_gait_knee_force_workflow(project_dir: str, overwrite: bool = False) -> dict:
    """Create a gait project, run it without GUI, and output bilateral knee force summary plus a JSON report."""
    return no_gui_gait_knee_force_workflow(project_dir=project_dir, overwrite=overwrite)


@mcp.tool()
def run_no_gui_new_posture_workflow(project_dir: str, preset_name: str, overwrite: bool = False) -> dict:
    """Create a standing project, apply a new posture preset, retry if needed, and output a JSON report."""
    return no_gui_new_posture_workflow(project_dir=project_dir, preset_name=preset_name, overwrite=overwrite)


def main() -> None:
    logger.info("AnyBody MCP server starting")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
