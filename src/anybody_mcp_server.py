from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import anyio
from fastmcp import Context, FastMCP

from src.knowledge_index import (
    crawl_professional_sources as crawl_professional_sources_impl,
    knowledge_source_catalog,
    knowledge_stats,
    rebuild_knowledge_index as rebuild_knowledge_index_impl,
    search_knowledge as search_knowledge_impl,
)
from src.anybody_runner import (
    apply_hand_load,
    apply_posture_preset,
    apply_state_description,
    AMMR_DIR,
    CAMERA_CLASS_TEMPLATE_PATH,
    clear_result_cache as clear_result_cache_impl,
    DEFAULT_ANYBODYCON,
    DEFAULT_HUMAN_STANDING,
    DEFAULT_MAX_SWEEP_CASES,
    create_project_from_named_template,
    create_project_from_template,
    export_animation_full,
    export_native_anybody_animation,
    extract_object_value,
    extract_object_value_after_operation,
    no_gui_gait_knee_force_workflow,
    no_gui_new_posture_workflow,
    no_gui_state_workflow,
    POSTURE_PRESETS,
    print_object,
    read_text_file,
    replace_text_in_file,
    run_full_chain_parametric_sweep,
    run_task_spec,
    run_task_spec_file,
    run_default_standing_model,
    run_macro,
    scan_project,
    STDOUT_LIMIT,
    summarize_ankle_reaction_force,
    summarize_bilateral_ankle_reaction_forces,
    summarize_bilateral_hip_reaction_forces,
    summarize_bilateral_knee_reaction_forces,
    summarize_full_chain_joint_forces,
    summarize_hip_reaction_force,
    summarize_knee_reaction_force,
    summarize_l5s1_compression,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "anybody",
    instructions=(
        "You are connected to a local AnyBody Modeling System installation. "
        "You can create biomechanical models, run inverse dynamics simulations, "
        "extract joint reaction forces (L5/S1, hip, knee, ankle), apply loads, "
        "and perform parametric sweeps. All tools return structured JSON. "
        "Use get_installation_info first to verify the connection. "
        "Use create_named_project to set up a new project before running simulations. "
        "For animation export, use export_native_animation to create real 3D rendered videos "
        "with bones, muscles, and segments. The tool auto-configures camera and renders headlessly. "
        "Each AnyBodyCon run is expensive: prefer get_full_chain_joint_forces (one solve for all "
        "four joints) over per-joint queries, and the bilateral_* tools over two single-side calls. "
        "Full-chain results are cached per project fingerprint; call clear_result_cache to force "
        "a re-solve. Long console transcripts are truncated in tool results. "
        "Parametric sweeps checkpoint every completed case and resume unless overwrite is set."
    ),
)


def _safe_call(fn, *args, **kwargs) -> dict:
    """Run a tool body, turning any exception into a structured error result.

    error_type is reported because an AnyBodyRunnerError, a timeout and a KeyError
    are otherwise indistinguishable to a client and call for different responses.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.error("Tool error in %s: %s", fn.__name__, e, exc_info=True)
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "tool": fn.__name__,
        }


_ENV_OVERRIDE_KEYS = (
    "ANYBODY_INSTALL_DIR",
    "ANYBODY_AMMR_DIR",
    "ANYBODYCON_PATH",
    "ANYBODY_DEFAULT_MODEL",
    "ANYBODY_CAMERA_TEMPLATE",
    "ANYBODY_STDOUT_LIMIT",
    "ANYBODY_MAX_SWEEP_CASES",
)


def _installation_info() -> dict:
    """Resolved paths and limits, including which values came from the environment."""
    return {
        "anybodycon": str(DEFAULT_ANYBODYCON),
        "anybodycon_exists": DEFAULT_ANYBODYCON.exists(),
        "ammr_dir": str(AMMR_DIR),
        "ammr_dir_exists": AMMR_DIR.exists(),
        "default_model": str(DEFAULT_HUMAN_STANDING),
        "default_model_exists": DEFAULT_HUMAN_STANDING.exists(),
        "camera_class_template": str(CAMERA_CLASS_TEMPLATE_PATH),
        "camera_class_template_exists": CAMERA_CLASS_TEMPLATE_PATH.exists(),
        "stdout_limit_chars": STDOUT_LIMIT,
        "max_sweep_cases": DEFAULT_MAX_SWEEP_CASES,
        # Anything listed here is being overridden by the process environment.
        "env_overrides": {k: os.environ[k] for k in _ENV_OVERRIDE_KEYS if k in os.environ},
    }


# ──────────────────────────────────────────────
# Resources (read-only data the AI can inspect)
# ──────────────────────────────────────────────


@mcp.resource("anybody://installation")
def get_installation_info_resource() -> dict:
    """Current AnyBody installation paths, limits and availability status."""
    return _installation_info()


@mcp.resource("anybody://presets")
def get_posture_presets() -> dict:
    """Available posture presets and their parameter modifications."""
    return {
        name: {
            "description": f"Preset '{name}' with modifications to mannequin and main file"
        }
        for name in POSTURE_PRESETS
    }


@mcp.resource("anybody://knowledge-sources")
def get_knowledge_sources() -> dict:
    """Whitelisted professional and local sources used by the MCP knowledge index."""
    return knowledge_source_catalog()


@mcp.resource("anybody://knowledge-stats")
def get_knowledge_stats_resource() -> dict:
    """Current local knowledge index status and source counts."""
    return knowledge_stats()


# ──────────────────────────────────────────────
# Prompts (reusable workflow templates)
# ──────────────────────────────────────────────


@mcp.prompt()
def gait_analysis_workflow(project_dir: str = "workspace/auto-gait-run") -> str:
    """Guide for running a full gait analysis with bilateral knee force extraction."""
    return (
        f"Run a gait analysis workflow:\n"
        f"1. Call run_no_gui_gait_knee_force_workflow(project_dir='{project_dir}', overwrite=true)\n"
        f"2. Check the 'summary' field for bilateral knee reaction forces\n"
        f"3. Compare right vs left primary_magnitude for symmetry\n"
        f"4. The JSON report is saved to {{project_dir}}/Output/knee-force-summary.json"
    )


@mcp.prompt()
def posture_study_workflow(
    project_dir: str = "workspace/auto-posture-run", preset: str = "forward-lean"
) -> str:
    """Guide for studying a new posture with joint force extraction."""
    return (
        f"Run a posture study:\n"
        f"1. Call run_no_gui_new_posture_workflow(project_dir='{project_dir}', preset_name='{preset}', overwrite=true)\n"
        f"2. If the preset fails, the system auto-retries with a safer fallback\n"
        f"3. Available presets: forward-lean, mild-squat, staggered-stance\n"
        f"4. Results include bilateral knee forces and a JSON report"
    )


@mcp.prompt()
def parametric_sweep_guide(project_dir: str = "workspace/auto-state-run") -> str:
    """Guide for running a parametric sweep across posture/load combinations."""
    return (
        f"Run a parametric sweep:\n"
        f"1. Call run_parametric_sweep(\n"
        f"     project_dir='{project_dir}',\n"
        f"     trunk_angles=[0, 10, 20, 30],\n"
        f"     knee_angles=[0, 15, 30, 45],\n"
        f"     loads_kg=[0, 5, 10, 15]\n"
        f"   )\n"
        f"2. Each combination creates a separate project case\n"
        f"3. Full-chain joint forces (L5/S1, hip, knee, ankle) are extracted per case\n"
        f"4. Summary statistics and full JSON results are returned"
    )


@mcp.prompt()
def animation_export_guide(model: str = "path/to/model.main.any") -> str:
    """Guide for exporting real 3D rendered AnyBody animation videos."""
    return (
        f"Export real 3D rendered AnyBody animation:\n"
        f"1. Use export_native_animation(model='{model}', output_path='output.mp4', fps=30)\n"
        f"   - Renders real 3D bones, muscles, and segments (not stick figures)\n"
        f"   - Auto-configures camera angle and position\n"
        f"   - Supports .mp4 (H.264) and .gif formats\n"
        f"   - Optional: overlay joint angles and forces with show_values=True\n"
        f"2. Supported models:\n"
        f"   - Human Standing: C:/Program Files/AnyBody Technology/AnyBody.7.3/AMMR/Tools/Templates/Human Standing/Human Standing.main.any\n"
        f"   - GaitVaughan: C:/Program Files/AnyBody Technology/AnyBody.7.3/AMMR/Application/Validation/GaitVaughan/GaitVaughan.main.any\n"
        f"   - Any model with complete BodyModel definition\n"
        f"3. Output shows full 3D model with bones, muscles, and segments during motion"
    )


# ──────────────────────────────────────────────
# Basic tools
# ──────────────────────────────────────────────


@mcp.tool()
def get_installation_info() -> dict:
    """Verify the local AnyBody installation. Call this first to confirm connectivity."""
    return _safe_call(_installation_info)


@mcp.tool()
def run_default_example() -> dict:
    """Run the verified Human Standing example through AnyBodyCon to confirm the solver works."""
    return _safe_call(lambda: run_default_standing_model().to_dict())


@mcp.tool()
def run_model(
    model: str,
    operation: str | None = None,
    run_operation: bool = True,
    extra_commands: list[str] | None = None,
) -> dict:
    """Load an AnyBody main file and optionally run an operation.

    Args:
        model: Absolute path to a .main.any file.
        operation: Full operation name, e.g. Main.RunApplication.
        run_operation: Whether to append the run command.
        extra_commands: Extra macro commands appended before exit.
    """

    def _run():
        commands: list[str] = []
        if operation:
            commands.append(f"operation {operation}")
        if run_operation and operation:
            commands.append("run")
        if extra_commands:
            commands.extend(extra_commands)
        return run_macro(model=model, commands=commands).to_dict()

    return _safe_call(_run)


@mcp.tool()
def write_run_result(
    output_path: str, model: str, operation: str | None = None
) -> dict:
    """Run a model and save the raw execution result as JSON to the specified path."""

    def _run():
        commands: list[str] = []
        if operation:
            commands.extend([f"operation {operation}", "run"])
        result = run_macro(model=model, commands=commands)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # The artifact on disk is the raw record, so it keeps the full console;
        # the copy returned to the client is truncated.
        target.write_text(
            json.dumps(result.to_dict(include_stdout=True), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return {"saved_to": str(target), "result": result.to_dict()}

    return _safe_call(_run)


@mcp.tool()
def inspect_project(project_dir: str) -> dict:
    """Scan an AnyBody project directory and list all .main.any and .any files."""
    return _safe_call(lambda: scan_project(project_dir))


@mcp.tool()
def create_project(destination: str, overwrite: bool = False) -> dict:
    """Create a writable local AnyBody project from the verified Human Standing template."""
    return _safe_call(
        lambda: create_project_from_template(
            destination=destination, overwrite=overwrite
        )
    )


@mcp.tool()
def create_named_project(
    destination: str, template_name: str, overwrite: bool = False
) -> dict:
    """Create a writable local AnyBody project from a named template.

    Args:
        destination: Target directory for the new project.
        template_name: One of: human-standing, gait-vaughan, standing, gait, walk, walking.
        overwrite: If True, replace existing files at destination.
    """
    return _safe_call(
        lambda: create_project_from_named_template(
            destination=destination, template_name=template_name, overwrite=overwrite
        )
    )


# ──────────────────────────────────────────────
# Knowledge retrieval tools
# ──────────────────────────────────────────────


@mcp.tool()
def get_knowledge_source_catalog() -> dict:
    """List the whitelisted sources and local file patterns used for knowledge indexing."""
    return _safe_call(knowledge_source_catalog)


@mcp.tool()
def get_knowledge_index_stats() -> dict:
    """Return document/chunk counts for the local MCP knowledge index."""
    return _safe_call(knowledge_stats)


@mcp.tool()
def crawl_professional_sources(
    source_set: str = "default",
    max_pages: int = 20,
    max_pubmed_results: int = 80,
    timeout: int = 20,
) -> dict:
    """Fetch whitelisted professional sources into the local MCP knowledge store.

    Args:
        source_set: One of default, all, official/anybody, pubmed, pmc, literature.
        max_pages: Maximum pages per official documentation seed.
        max_pubmed_results: Total PubMed search result budget across curated queries.
        timeout: Per-request timeout in seconds.
    """
    return _safe_call(
        lambda: crawl_professional_sources_impl(
            source_set=source_set,
            max_pages=max_pages,
            max_pubmed_results=max_pubmed_results,
            timeout=timeout,
        )
    )


@mcp.tool()
def rebuild_knowledge_index(
    paths: list[str] | None = None, include_remote: bool = True
) -> dict:
    """Build the local SQLite FTS knowledge index from project files and crawled sources.

    Args:
        paths: Optional files/directories to index instead of the default local patterns.
        include_remote: Include previously crawled professional sources.
    """
    return _safe_call(
        lambda: rebuild_knowledge_index_impl(paths=paths, include_remote=include_remote)
    )


@mcp.tool()
def search_knowledge(
    query: str, top_k: int = 5, source_type: str | None = None
) -> dict:
    """Search local project/professional knowledge with source paths, URLs, and citations.

    Args:
        query: Search terms or question.
        top_k: Number of chunks to return, capped at 20.
        source_type: Optional filter: local_project, official_doc, pubmed, pmc_fulltext.
    """
    return _safe_call(
        lambda: search_knowledge_impl(query=query, top_k=top_k, source_type=source_type)
    )


# ──────────────────────────────────────────────
# Posture & state tools
# ──────────────────────────────────────────────


@mcp.tool()
def apply_named_posture_preset(project_dir: str, preset_name: str) -> dict:
    """Apply a predefined posture preset to a Human Standing project.

    Args:
        project_dir: Path to the project directory.
        preset_name: One of: forward-lean, mild-squat, staggered-stance.
    """
    return _safe_call(
        lambda: apply_posture_preset(project_dir=project_dir, preset_name=preset_name)
    )


@mcp.tool()
def apply_state_to_project(project_dir: str, state: dict) -> dict:
    """Apply a structured state description directly to a Human Standing project.

    Args:
        project_dir: Path to the project directory.
        state: Dict with keys like pelvis_pos_x, thorax_extension, right_knee_flexion, etc.
    """
    return _safe_call(
        lambda: apply_state_description(project_dir=project_dir, state=state)
    )


@mcp.tool()
def read_project_file(file_path: str) -> dict:
    """Read a text file from an AnyBody project and return its contents."""
    return _safe_call(lambda: read_text_file(file_path))


@mcp.tool()
def replace_in_project_file(
    file_path: str, old_text: str, new_text: str, count: int = -1
) -> dict:
    """Replace text in a project file for parameter or script edits.

    Args:
        file_path: Absolute path to the file.
        old_text: Text to find.
        new_text: Replacement text.
        count: Max replacements (-1 for all).
    """
    return _safe_call(
        lambda: replace_text_in_file(
            file_path=file_path, old_text=old_text, new_text=new_text, count=count
        )
    )


# ──────────────────────────────────────────────
# Object inspection tools
# ──────────────────────────────────────────────


@mcp.tool()
def print_model_object(model: str, object_name: str, deep: bool = False) -> dict:
    """Load a model and print an object value or full folder subtree.

    Args:
        model: Path to .main.any file.
        object_name: AnyBody object path, e.g. Main.HumanModel.BodyModel.Right.Leg.
        deep: If True, use printdown for recursive output.
    """
    return _safe_call(
        lambda: print_object(model=model, object_name=object_name, deep=deep).to_dict()
    )


@mcp.tool()
def get_model_object_value(model: str, object_name: str) -> dict:
    """Load a model, print an object, and parse the numeric value."""
    return _safe_call(
        lambda: extract_object_value(model=model, object_name=object_name)
    )


@mcp.tool()
def get_model_object_value_after_operation(
    model: str, object_name: str, operation: str
) -> dict:
    """Run an operation first, then parse a printed numeric object value.

    Args:
        model: Path to .main.any file.
        object_name: AnyBody object path to extract.
        operation: Operation to run first, e.g. Main.RunApplication.
    """
    return _safe_call(
        lambda: extract_object_value_after_operation(
            model=model, object_name=object_name, operation=operation
        )
    )


# ──────────────────────────────────────────────
# Joint extraction tools
# ──────────────────────────────────────────────


@mcp.tool()
def summarize_knee_force(model: str, side: str = "Right") -> dict:
    """Extract knee reaction force vector and magnitude for one side.

    Args:
        model: Path to .main.any file.
        side: 'Right' or 'Left'.
    """
    return _safe_call(lambda: summarize_knee_reaction_force(model=model, side=side))


@mcp.tool()
def summarize_bilateral_knee_forces(model: str) -> dict:
    """Extract left and right knee reaction forces and compare magnitudes."""
    return _safe_call(lambda: summarize_bilateral_knee_reaction_forces(model=model))


@mcp.tool()
def get_hip_force(model: str, side: str = "Right") -> dict:
    """Extract hip joint reaction force (RefFrameOutput.F) for one side.

    Args:
        model: Path to .main.any file.
        side: 'Right' or 'Left'.
    """
    return _safe_call(lambda: summarize_hip_reaction_force(model=model, side=side))


@mcp.tool()
def get_ankle_force(model: str, side: str = "Right") -> dict:
    """Extract ankle joint reaction force (RefFrameOutput.F) for one side.

    Args:
        model: Path to .main.any file.
        side: 'Right' or 'Left'.
    """
    return _safe_call(lambda: summarize_ankle_reaction_force(model=model, side=side))


@mcp.tool()
def get_bilateral_hip_forces(model: str) -> dict:
    """Extract left and right hip reaction forces."""
    return _safe_call(lambda: summarize_bilateral_hip_reaction_forces(model=model))


@mcp.tool()
def get_bilateral_ankle_forces(model: str) -> dict:
    """Extract left and right ankle reaction forces."""
    return _safe_call(lambda: summarize_bilateral_ankle_reaction_forces(model=model))


@mcp.tool()
def get_l5s1_compression(model: str) -> dict:
    """Extract L5/S1 spinal compression force from Fout. Returns anteroposterior, compression, and mediolateral components."""
    return _safe_call(lambda: summarize_l5s1_compression(model=model))


@mcp.tool()
def get_full_chain_joint_forces(
    model: str, use_cache: bool = True, include_stdout: bool = False
) -> dict:
    """Run the model once and extract all 4 joints (L5/S1, hip, knee, ankle) simultaneously.

    The most efficient multi-joint query: one solve instead of one per joint.

    Args:
        model: Path to .main.any file.
        use_cache: Reuse a previous result when the project files are unchanged.
            A cache hit returns cached=true and omits stdout.
        include_stdout: Include the full AnyBodyCon console transcript.
    """
    return _safe_call(
        lambda: summarize_full_chain_joint_forces(
            model=model, use_cache=use_cache, include_stdout=include_stdout
        )
    )


@mcp.tool()
def clear_result_cache() -> dict:
    """Drop cached full-chain results so the next query re-solves from scratch."""
    return _safe_call(clear_result_cache_impl)


# ──────────────────────────────────────────────
# Load application
# ──────────────────────────────────────────────


@mcp.tool()
def add_hand_load(project_dir: str, load_kg: float) -> dict:
    """Apply bilateral hand loads to a Human Standing project.

    Creates AnyForce3D on PalmJoint for both hands. Load is specified per hand in kg.

    Args:
        project_dir: Path to the project directory.
        load_kg: Load per hand in kilograms (converted to Newtons internally).
    """
    return _safe_call(lambda: apply_hand_load(project_dir=project_dir, load_kg=load_kg))


# ──────────────────────────────────────────────
# Parametric sweep
# ──────────────────────────────────────────────


@mcp.tool()
async def run_parametric_sweep(
    project_dir: str,
    trunk_angles: list[float],
    knee_angles: list[float],
    loads_kg: list[float],
    output_dir: str | None = None,
    overwrite: bool = False,
    timeout: int = 300,
    max_cases: int | None = None,
    retries: int = 1,
    write_csv: bool = True,
    write_chart: bool = False,
    ctx: Context = None,
) -> dict:
    """Run a parametric sweep across trunk/knee/load combinations with full-chain joint extraction.

    Runs len(trunk_angles) * len(knee_angles) * len(loads_kg) cases, each extracting
    L5/S1, hip, knee and ankle forces. Completed cases are stored under
    <output_dir>/cases/ as they finish, so an interrupted sweep resumes instead of
    restarting from zero.

    Progress is reported to the client as cases complete, and the sweep stops at the
    next case boundary if the request is cancelled.

    Args:
        project_dir: Base project directory.
        trunk_angles: Trunk flexion angles in degrees.
        knee_angles: Knee flexion angles in degrees.
        loads_kg: Hand loads in kg (per hand).
        output_dir: Results directory (default: sibling 'sweep_results' folder).
        overwrite: Rebuild cases even when a stored result exists. Default False resumes.
        timeout: Timeout per case in seconds.
        max_cases: Refuse to start above this case count. Default 512.
        retries: Extra attempts per case after the first failure. Default 1.
        write_csv: Also write sweep_results.csv alongside the JSON.
        write_chart: Also write an L5/S1 compression heatmap PNG.
    """
    total = len(trunk_angles) * len(knee_angles) * len(loads_kg)
    progress = {"done": 0, "total": total, "message": ""}
    cancel_event = threading.Event()

    def _work():
        return run_full_chain_parametric_sweep(
            project_dir=project_dir,
            trunk_angles=trunk_angles,
            knee_angles=knee_angles,
            loads_kg=loads_kg,
            output_dir=output_dir,
            overwrite=overwrite,
            timeout=timeout,
            max_cases=max_cases,
            retries=retries,
            write_csv=write_csv,
            write_chart=write_chart,
            progress_callback=lambda done, count, message: progress.update(
                done=done, total=count, message=message
            ),
            cancel_event=cancel_event,
        )

    async def _pump_progress():
        last = -1
        while True:
            if progress["done"] != last:
                last = progress["done"]
                await ctx.report_progress(
                    progress=float(progress["done"]),
                    total=float(progress["total"] or 1),
                    message=progress["message"],
                )
            await anyio.sleep(0.5)

    try:
        if ctx is not None:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_pump_progress)
                try:
                    result = await anyio.to_thread.run_sync(_work)
                finally:
                    # The pump is cancelled below, so flush the terminal progress
                    # under a shield or the last case never reaches the client.
                    with anyio.CancelScope(shield=True):
                        await ctx.report_progress(
                            progress=float(progress["done"]),
                            total=float(progress["total"] or 1),
                            message=progress["message"],
                        )
                    tg.cancel_scope.cancel()
        else:
            result = await anyio.to_thread.run_sync(_work)
    except anyio.get_cancelled_exc_class():
        # The solver thread cannot be interrupted mid-case; flagging it makes the
        # sweep stop at the next case boundary. Finished cases are already on disk.
        cancel_event.set()
        raise
    except Exception as exc:
        logger.error("Sweep failed: %s", exc, exc_info=True)
        return {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "tool": "run_parametric_sweep",
        }

    return result


# ──────────────────────────────────────────────
# Workflow tools
# ──────────────────────────────────────────────


@mcp.tool()
def run_no_gui_gait_knee_force_workflow(
    project_dir: str, overwrite: bool = False
) -> dict:
    """Create a gait project, run without GUI, and output bilateral knee force summary plus JSON report."""
    return _safe_call(
        lambda: no_gui_gait_knee_force_workflow(
            project_dir=project_dir, overwrite=overwrite
        )
    )


@mcp.tool()
def run_no_gui_new_posture_workflow(
    project_dir: str, preset_name: str, overwrite: bool = False
) -> dict:
    """Create a standing project, apply a posture preset, retry with fallback if needed, and output JSON report.

    Args:
        project_dir: Target project directory.
        preset_name: One of: forward-lean, mild-squat, staggered-stance.
        overwrite: If True, replace existing project.
    """
    return _safe_call(
        lambda: no_gui_new_posture_workflow(
            project_dir=project_dir, preset_name=preset_name, overwrite=overwrite
        )
    )


@mcp.tool()
def run_no_gui_state_workflow(
    project_dir: str, state: dict, overwrite: bool = False
) -> dict:
    """Create a standing project from a structured state description, retry if needed, and output JSON report.

    Args:
        project_dir: Target project directory.
        state: Dict with posture parameters (pelvis_pos_x, thorax_extension, right_knee_flexion, etc.).
        overwrite: If True, replace existing project.
    """
    return _safe_call(
        lambda: no_gui_state_workflow(
            project_dir=project_dir, state=state, overwrite=overwrite
        )
    )


@mcp.tool()
def run_task_spec_workflow(spec: dict) -> dict:
    """Run a lightweight task spec dictionary without opening GUI.

    Supported workflows: gait-knee-force, new-posture, state, full-chain, parametric-sweep.
    """
    return _safe_call(lambda: run_task_spec(spec))


@mcp.tool()
def run_task_spec_file_workflow(spec_file: str) -> dict:
    """Run a lightweight task spec JSON file without opening GUI.

    Args:
        spec_file: Absolute path to a JSON task spec file.
    """
    return _safe_call(lambda: run_task_spec_file(spec_file))


# ──────────────────────────────────────────────
# Animation export tools
# ──────────────────────────────────────────────


@mcp.tool()
def export_animation(
    model: str,
    output_prefix: str = "anybody_animation",
    n_frames: int | None = None,
    formats: str = "bvh,csv,gif",
) -> dict:
    """Export animation data (BVH/CSV/GIF/MP4) from an AnyBody model.

    Extracts per-step joint DOF positions via AnyBody's native Dump, then exports
    to standard animation formats. Use 'mp4' for 3D cylinder bone rendering.

    Args:
        model: Path to AnyBody .main.any file.
        output_prefix: Output path prefix (without extension).
        n_frames: Number of frames. If None, uses model's nStep.
        formats: Comma-separated: bvh, csv, gif, mp4. Default: bvh,csv,gif.
    """

    def _run():
        fmt_list = [f.strip() for f in formats.split(",")]
        return export_animation_full(
            model=model,
            output_prefix=output_prefix,
            n_frames=n_frames,
            formats=fmt_list,
        )

    return _safe_call(_run)


@mcp.tool()
def export_native_animation(
    model: str,
    output_path: str = "anybody_native_animation.mp4",
    n_frames: int | None = None,
    fps: int = 10,
    show_values: bool = False,
) -> dict:
    """Export a native AnyBody 3D rendered animation (bones, muscles, segments).

    Uses AnyBody's built-in AnyCamRecorder to render frames headlessly via AnyBodyCon.exe.
    The pipeline: inject CameraClassTemplate -> run model -> capture PNG frames -> convert to video.

    Camera setup (auto-configured):
        - GaitVaughan: side view from +X, Distance=6, FOV=5, LookAt={0,0.6,0}
        - Human Standing: side view from +X, Distance=4, FOV=3, LookAt={0,0.8,0}

    Output formats:
        - .gif: animated GIF via Pillow
        - .mp4: video via imageio + ffmpeg (libx264)

    The rendered model shows real 3D bones, muscles, and segments — not stick figures.

    Args:
        model: Path to AnyBody .main.any file.
        output_path: Output file path (.mp4 or .gif).
        n_frames: Number of frames. If None, uses model's nStep.
        fps: Frames per second. Default: 10.
        show_values: Overlay joint angles and end-state joint forces on each frame.
            Off by default because it costs three extra solver runs.
    """
    return _safe_call(
        lambda: export_native_anybody_animation(
            model=model,
            output_path=output_path,
            n_frames=n_frames,
            fps=fps,
            show_values=show_values,
        )
    )


def main() -> None:
    # Codex stdio MCP requires stdout to contain only JSON-RPC frames.
    mcp.run(transport="stdio", show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
