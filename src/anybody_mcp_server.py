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
    apply_hand_load,
    apply_posture_preset,
    apply_state_description,
    DEFAULT_ANYBODYCON,
    DEFAULT_HUMAN_STANDING,
    create_project_from_named_template,
    create_project_from_template,
    extract_object_value,
    extract_object_value_after_operation,
    no_gui_gait_knee_force_workflow,
    no_gui_new_posture_workflow,
    no_gui_state_workflow,
    print_object,
    read_text_file,
    replace_text_in_file,
    run_full_chain_parametric_sweep,
    run_task_spec,
    run_task_spec_file,
    run_default_standing_model,
    run_macro,
    scan_project,
    summarize_ankle_reaction_force,
    summarize_bilateral_ankle_reaction_forces,
    summarize_bilateral_hip_reaction_forces,
    summarize_bilateral_knee_reaction_forces,
    summarize_full_chain_joint_forces,
    summarize_hip_reaction_force,
    summarize_knee_reaction_force,
    summarize_l5s1_compression,
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
def apply_state_to_project(project_dir: str, state: dict) -> dict:
    """Apply a structured state description directly to a Human Standing project."""
    return apply_state_description(project_dir=project_dir, state=state)


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


@mcp.tool()
def run_no_gui_state_workflow(project_dir: str, state: dict, overwrite: bool = False) -> dict:
    """Create a standing project from a structured state description, retry if needed, and output a JSON report."""
    return no_gui_state_workflow(project_dir=project_dir, state=state, overwrite=overwrite)


@mcp.tool()
def run_task_spec_workflow(spec: dict) -> dict:
    """Run a lightweight task spec dictionary without opening GUI."""
    return run_task_spec(spec)


@mcp.tool()
def run_task_spec_file_workflow(spec_file: str) -> dict:
    """Run a lightweight task spec JSON file without opening GUI."""
    return run_task_spec_file(spec_file)


# ---- New joint extraction tools ----

@mcp.tool()
def get_hip_force(model: str, side: str = "Right") -> dict:
    """Run the model and return parsed hip joint reaction force (RefFrameOutput.F)."""
    return summarize_hip_reaction_force(model=model, side=side)


@mcp.tool()
def get_ankle_force(model: str, side: str = "Right") -> dict:
    """Run the model and return parsed ankle joint reaction force (RefFrameOutput.F)."""
    return summarize_ankle_reaction_force(model=model, side=side)


@mcp.tool()
def get_bilateral_hip_forces(model: str) -> dict:
    """Run the model and return left/right hip reaction forces."""
    return summarize_bilateral_hip_reaction_forces(model=model)


@mcp.tool()
def get_bilateral_ankle_forces(model: str) -> dict:
    """Run the model and return left/right ankle reaction forces."""
    return summarize_bilateral_ankle_reaction_forces(model=model)


@mcp.tool()
def get_l5s1_compression(model: str) -> dict:
    """Run the model and return L5/S1 spinal compression force from Fout."""
    return summarize_l5s1_compression(model=model)


@mcp.tool()
def get_full_chain_joint_forces(model: str) -> dict:
    """Run the model once and extract all 4 joints (L5/S1, hip, knee, ankle) simultaneously."""
    return summarize_full_chain_joint_forces(model=model)


# ---- New load application tool ----

@mcp.tool()
def add_hand_load(project_dir: str, load_kg: float) -> dict:
    """Apply bilateral hand loads (AnyForce3D on PalmJoint) to a Human Standing project. Specify load per hand in kg."""
    return apply_hand_load(project_dir=project_dir, load_kg=load_kg)


# ---- New parametric sweep tool ----

@mcp.tool()
def run_parametric_sweep(
    project_dir: str,
    trunk_angles: list[float],
    knee_angles: list[float],
    loads_kg: list[float],
    output_dir: str | None = None,
    timeout: int = 300,
) -> dict:
    """Run a parametric sweep across trunk/knee/load combinations, extracting full-chain joint forces.
    Returns summary statistics and saves full results as JSON.
    """
    return run_full_chain_parametric_sweep(
        project_dir=project_dir,
        trunk_angles=trunk_angles,
        knee_angles=knee_angles,
        loads_kg=loads_kg,
        output_dir=output_dir,
        timeout=timeout,
    )


def main() -> None:
    logger.info("AnyBody MCP server starting")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
