from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


def _env_path(name: str, default: Path | str) -> Path:
    """Resolve a path from an environment variable, falling back to the default."""
    raw = os.environ.get(name)
    return Path(raw) if raw else Path(default)


# The installation root is configurable so a relocated or upgraded AnyBody
# (e.g. 7.4, or a non-default drive) works without editing source.
ANYBODY_INSTALL_DIR = _env_path(
    "ANYBODY_INSTALL_DIR", r"C:\Program Files\AnyBody Technology\AnyBody.7.3"
)
AMMR_DIR = _env_path("ANYBODY_AMMR_DIR", ANYBODY_INSTALL_DIR / "AMMR")

DEFAULT_ANYBODYCON = _env_path("ANYBODYCON_PATH", ANYBODY_INSTALL_DIR / "AnyBodyCon.exe")
DEFAULT_HUMAN_STANDING = _env_path(
    "ANYBODY_DEFAULT_MODEL",
    AMMR_DIR / "Tools" / "Templates" / "Human Standing" / "Human Standing.main.any",
)
DEFAULT_HUMAN_TEMPLATE_DIR = DEFAULT_HUMAN_STANDING.parent
DEFAULT_GAIT_VAUGHAN_MAIN = (
    AMMR_DIR / "Application" / "Validation" / "GaitVaughan" / "GaitVaughan.main.any"
)
DEFAULT_GAIT_VAUGHAN_TEMPLATE_DIR = DEFAULT_GAIT_VAUGHAN_MAIN.parent
DEFAULT_AMMR_LIBDEF = AMMR_DIR / "libdef.any"
DEFAULT_VALIDATION_LIBDEF_INCLUDE = '#include "<ANYBODY_PATH_INSTALLDIR>/AMMR/Application/Validation/libdef.any"\n'
DEFAULT_AMMR_LIBDEF_INCLUDE = '#include "<ANYBODY_PATH_INSTALLDIR>/AMMR/libdef.any"\n'

# AnyBodyCon prints a lot of banner/progress noise. Console output returned to an
# MCP client is truncated to this many characters; full output stays on disk.
STDOUT_LIMIT = int(os.environ.get("ANYBODY_STDOUT_LIMIT") or 4000)

logger = logging.getLogger(__name__)

POSTURE_PRESETS = {
    "mild-squat": {
        "mannequin": {
            "PelvisPosY=0.969;": "PelvisPosY=0.90;",
            "PelvisThoraxExtension=0;": "PelvisThoraxExtension=10;",
            "Right.HipFlexion = 0.0;": None,
            "HipFlexion = 0.0; ": "HipFlexion = 18.0; ",
            "KneeFlexion = 4.0;": "KneeFlexion = 28.0;",
            "AnklePlantarFlexion =0.0; ": "AnklePlantarFlexion =4.0; ",
            "HipFlexion =.Right.HipFlexion;  ": "HipFlexion =.Right.HipFlexion;  ",
            "KneeFlexion = .Right.KneeFlexion;": "KneeFlexion = .Right.KneeFlexion;",
            "AnklePlantarFlexion = .Right.AnklePlantarFlexion ;": "AnklePlantarFlexion = .Right.AnklePlantarFlexion ;",
        },
        "main": {
            "nStep = 11;": "nStep = 1;",
            "nStep = 7;": "nStep = 1;",
        },
    },
    "forward-lean": {
        "mannequin": {
            "PelvisPosX=0.0502;": "PelvisPosX=0.09;",
            "PelvisThoraxExtension=0;": "PelvisThoraxExtension=18;",
            "KneeFlexion = 4.0;": "KneeFlexion = 12.0;",
            "AnklePlantarFlexion =0.0; ": "AnklePlantarFlexion =2.0; ",
        },
        "main": {
            "nStep = 11;": "nStep = 1;",
            "nStep = 7;": "nStep = 1;",
        },
    },
    "staggered-stance": {
        "mannequin": {
            "PelvisPosX=0.0502;": "PelvisPosX=0.08;",
            "PelvisThoraxExtension=0;": "PelvisThoraxExtension=6;",
            "KneeFlexion = 4.0;": "KneeFlexion = 10.0;",
        },
        "main": {
            "AnyVec3 HeelPosition = {-0.08, 0, 0.14};": "AnyVec3 HeelPosition = {-0.02, 0, 0.14};",
            "AnyVec3 HeelPosition = {-0.08, 0, -0.14};": "AnyVec3 HeelPosition = {-0.14, 0, -0.14};",
            "nStep = 11;": "nStep = 1;",
            "nStep = 7;": "nStep = 1;",
        },
    },
}

STATE_TEXT_MAP = {
    "pelvis_pos_x": ("Model/Mannequin.any", "PelvisPosX=0.0502;", "PelvisPosX={value};"),
    "pelvis_pos_y": ("Model/Mannequin.any", "PelvisPosY=0.969;", "PelvisPosY={value};"),
    "pelvis_pos_z": ("Model/Mannequin.any", "PelvisPosZ=0;", "PelvisPosZ={value};"),
    "pelvis_rot_z": ("Model/Mannequin.any", "PelvisRotZ=-3;", "PelvisRotZ={value};"),
    "thorax_extension": ("Model/Mannequin.any", "PelvisThoraxExtension=0;", "PelvisThoraxExtension={value};"),
    "right_hip_flexion": ("Model/Mannequin.any", "HipFlexion = 0.0; ", "HipFlexion = {value}; "),
    "right_knee_flexion": ("Model/Mannequin.any", "KneeFlexion = 4.0;", "KneeFlexion = {value};"),
    "right_ankle_plantar_flexion": ("Model/Mannequin.any", "AnklePlantarFlexion =0.0; ", "AnklePlantarFlexion ={value}; "),
    "left_hip_flexion": ("Model/Mannequin.any", "HipFlexion =.Right.HipFlexion;  ", "HipFlexion ={value};  "),
    "left_knee_flexion": ("Model/Mannequin.any", "KneeFlexion = .Right.KneeFlexion;", "KneeFlexion = {value};"),
    "left_ankle_plantar_flexion": ("Model/Mannequin.any", "AnklePlantarFlexion = .Right.AnklePlantarFlexion ;", "AnklePlantarFlexion = {value} ;"),
    "right_foot_heel_x": ("Human Standing.main.any", "AnyVec3 HeelPosition = {-0.08, 0, 0.14};", "AnyVec3 HeelPosition = {{{value}, 0, 0.14}};"),
    "left_foot_heel_x": ("Human Standing.main.any", "AnyVec3 HeelPosition = {-0.08, 0, -0.14};", "AnyVec3 HeelPosition = {{{value}, 0, -0.14}};"),
    "analysis_steps": ("Human Standing.main.any", "nStep = 11;", "nStep = {value};"),
}


class AnyBodyRunnerError(RuntimeError):
    pass


_OBJECT_VALUE_PATTERN = re.compile(r"=\s*(\{.*\}|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*;", re.DOTALL)
_ANYBODY_ERROR_PATTERN = re.compile(
    r"(^|\n)\s*(ERROR|ERROR\(|FATAL ERROR|Aborted|Unresolved object)",
    re.IGNORECASE,
)


def _truncate(text: str, limit: int = STDOUT_LIMIT) -> str:
    """Keep both ends of a long console dump; the middle is banner/progress noise."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [{omitted} chars omitted] ...\n{text[-tail:]}"


@dataclass
class RunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    # The macro file is a temporary that run_macro deletes before returning, so the
    # commands are reported instead of a path that no longer resolves.
    macro_commands: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return "Loaded successfully." in (self.stdout or "")

    @property
    def has_errors(self) -> bool:
        return bool(_ANYBODY_ERROR_PATTERN.search(self.stdout or "") or _ANYBODY_ERROR_PATTERN.search(self.stderr or ""))

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.loaded and not self.has_errors

    def to_dict(self, include_stdout: bool = False, stdout_limit: int | None = None) -> dict:
        """Serialize for an MCP client.

        Long AnyBodyCon consoles are truncated by default; pass include_stdout=True
        when the full transcript is needed (e.g. writing a debug artifact to disk).
        """
        limit = STDOUT_LIMIT if stdout_limit is None else stdout_limit
        stdout = self.stdout if include_stdout else _truncate(self.stdout, limit)
        return {
            "command": self.command,
            "returncode": self.returncode,
            "macro_commands": self.macro_commands,
            "stdout": stdout,
            "stdout_truncated": len(stdout) < len(self.stdout or ""),
            "stderr": _truncate(self.stderr, limit),
            "loaded": self.loaded,
            "has_errors": self.has_errors,
            "ok": self.ok,
        }


def resolve_anybodycon(path: str | None = None) -> Path:
    candidate = Path(path) if path else DEFAULT_ANYBODYCON
    if not candidate.exists():
        raise AnyBodyRunnerError(f"AnyBodyCon not found: {candidate}")
    return candidate


def resolve_model(path: str | None = None) -> Path:
    # AnyBodyCon resolves relative load paths against the macro file location, so use absolute paths.
    candidate = (Path(path).resolve() if path else DEFAULT_HUMAN_STANDING)
    if not candidate.exists():
        raise AnyBodyRunnerError(f"Model file not found: {candidate}")
    return candidate


def get_named_template(template_name: str) -> dict:
    normalized = template_name.strip().lower().replace("_", "-")
    if normalized in {"human-standing", "standing", "human"}:
        return {
            "name": "human-standing",
            "template_dir": DEFAULT_HUMAN_TEMPLATE_DIR,
            "default_main": DEFAULT_HUMAN_STANDING.name,
            "root_libdef": DEFAULT_AMMR_LIBDEF_INCLUDE,
        }
    if normalized in {"gait-vaughan", "gait", "walk", "walking"}:
        return {
            "name": "gait-vaughan",
            "template_dir": DEFAULT_GAIT_VAUGHAN_TEMPLATE_DIR,
            "default_main": DEFAULT_GAIT_VAUGHAN_MAIN.name,
            "root_libdef": DEFAULT_VALIDATION_LIBDEF_INCLUDE,
        }
    raise AnyBodyRunnerError(f"Unknown template name: {template_name}")


def build_macro(model: Path, commands: Iterable[str] | None = None) -> str:
    macro_lines = [f'load "{model}"']
    if commands:
        macro_lines.extend(commands)
    macro_lines.append("exit")
    return "\n".join(macro_lines) + "\n"


def run_macro(
    model: str | None = None,
    commands: Iterable[str] | None = None,
    anybodycon: str | None = None,
    workdir: str | None = None,
    timeout: int = 300,
    cancel_event: "threading.Event | None" = None,
) -> RunResult:
    anybodycon_path = resolve_anybodycon(anybodycon)
    model_path = resolve_model(model)
    cwd = Path(workdir) if workdir else model_path.parent
    if not cwd.exists():
        raise AnyBodyRunnerError(f"Working directory not found: {cwd}")

    command_list = list(commands or [])
    macro_content = build_macro(model_path, command_list)
    fd, macro_path = tempfile.mkstemp(suffix=".anymcr", prefix="anybody_", text=True)
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write(macro_content)

    command = [str(anybodycon_path), f'--macro={macro_path}', "--noninteractive"]
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        Path(macro_path).unlink(missing_ok=True)
        raise AnyBodyRunnerError(f"Could not start AnyBodyCon: {exc}") from exc

    deadline = time.monotonic() + timeout
    returncode: int | None = None
    outcome = ""
    try:
        # Polled rather than a single blocking wait so a long solve can be cancelled
        # at the case boundary; the interval only bounds cancellation latency.
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
                returncode = proc.returncode
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    returncode = -2
                    outcome = "Cancelled before completion."
                    break
                if time.monotonic() > deadline:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    returncode = -1
                    outcome = f"AnyBodyCon timed out after {timeout} seconds."
                    break
    finally:
        Path(macro_path).unlink(missing_ok=True)

    if returncode is None:
        raise AnyBodyRunnerError("AnyBodyCon did not return a process result")

    return RunResult(
        command=command,
        returncode=returncode,
        stdout=stdout or "",
        stderr=((stderr or "") + ("\n" + outcome if outcome else "")).strip(),
        macro_commands=command_list,
    )


def run_default_standing_model(
    anybodycon: str | None = None,
    timeout: int = 300,
) -> RunResult:
    return run_macro(
        model=str(DEFAULT_HUMAN_STANDING),
        commands=["operation Main.RunApplication", "run"],
        anybodycon=anybodycon,
        timeout=timeout,
    )


def print_object(model: str, object_name: str, deep: bool = False, anybodycon: str | None = None, timeout: int = 300) -> RunResult:
    command = "printdown" if deep else "print"
    return run_macro(
        model=model,
        commands=[f"{command} {object_name}"],
        anybodycon=anybodycon,
        timeout=timeout,
    )


def create_project_from_template(destination: str, template_dir: str | None = None, overwrite: bool = False) -> dict:
    template_path = Path(template_dir) if template_dir else DEFAULT_HUMAN_TEMPLATE_DIR
    if not template_path.exists():
        raise AnyBodyRunnerError(f"Template directory not found: {template_path}")

    destination_path = Path(destination)
    if destination_path.exists() and any(destination_path.iterdir()) and not overwrite:
        raise AnyBodyRunnerError(f"Destination already contains files: {destination_path}")

    destination_path.mkdir(parents=True, exist_ok=True)
    copied_files: list[str] = []
    for source in template_path.rglob("*"):
        relative = source.relative_to(template_path)
        target = destination_path / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_files.append(str(target))

    root_libdef = destination_path.parent / "libdef.any"
    root_libdef.write_text(
        '#include "<ANYBODY_PATH_INSTALLDIR>/AMMR/libdef.any"\n',
        encoding="utf-8",
    )
    copied_files.append(str(root_libdef))

    main_candidates = sorted(destination_path.glob("*.main.any"))
    return {
        "template": str(template_path),
        "destination": str(destination_path),
        "main_files": [str(path) for path in main_candidates],
        "copied_file_count": len(copied_files),
    }


def create_project_from_named_template(destination: str, template_name: str, overwrite: bool = False) -> dict:
    template = get_named_template(template_name)
    result = create_project_from_template(
        destination=destination,
        template_dir=str(template["template_dir"]),
        overwrite=overwrite,
    )
    root_libdef = Path(destination).parent / "libdef.any"
    root_libdef.write_text(template["root_libdef"], encoding="utf-8")
    result["template_name"] = template["name"]
    result["default_main"] = str(Path(destination) / template["default_main"])
    return result


def apply_posture_preset(project_dir: str, preset_name: str) -> dict:
    preset = POSTURE_PRESETS.get(preset_name)
    if not preset:
        raise AnyBodyRunnerError(f"Unknown posture preset: {preset_name}")

    project_path = Path(project_dir)
    main_file = next(project_path.glob("*.main.any"), None)
    mannequin_file = project_path / "Model" / "Mannequin.any"
    if not main_file or not mannequin_file.exists():
        raise AnyBodyRunnerError(f"Project does not contain expected files: {project_dir}")

    replacements = []
    for old_text, new_text in preset.get("main", {}).items():
        result = replace_text_in_file(str(main_file), old_text, new_text, count=1)
        replacements.append(result)
    for old_text, new_text in preset.get("mannequin", {}).items():
        result = replace_text_in_file(str(mannequin_file), old_text, new_text, count=1)
        replacements.append(result)

    return {
        "project_dir": project_dir,
        "preset_name": preset_name,
        "replacements": replacements,
        "main_file": str(main_file),
    }


def replace_text_in_file(file_path: str, old_text: str, new_text: str | None, count: int = -1) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise AnyBodyRunnerError(f"File not found: {path}")
    original = path.read_text(encoding="utf-8")
    available = original.count(old_text)
    replacements = available if count < 0 else min(available, count)
    # Some presets intentionally remove template lines; None is treated as deletion.
    replacement_text = "" if new_text is None else new_text
    updated = (
        original.replace(old_text, replacement_text)
        if count < 0
        else original.replace(old_text, replacement_text, count)
    )
    path.write_text(updated, encoding="utf-8")
    return {
        "file": str(path),
        "replacements": replacements,
        "found": available,
    }


def apply_state_description(project_dir: str, state: dict) -> dict:
    project_path = Path(project_dir)
    replacements = []
    ignored_keys = []
    unapplied_keys = []
    for key, value in state.items():
        mapping = STATE_TEXT_MAP.get(key)
        if not mapping:
            ignored_keys.append(key)
            continue
        relative_file, old_text, new_template = mapping
        target_file = project_path / relative_file
        rendered = new_template.format(value=value)
        result = replace_text_in_file(str(target_file), old_text, rendered, count=1)
        if result["replacements"] == 0:
            unapplied_keys.append(key)
        replacements.append({"key": key, **result})
    main_file = next(project_path.glob("*.main.any"), None)
    return {
        "project_dir": project_dir,
        "state": state,
        "replacements": replacements,
        # These fields make no-op state edits visible to MCP callers.
        "ignored_keys": ignored_keys,
        "unapplied_keys": unapplied_keys,
        "main_file": str(main_file) if main_file else None,
    }


def read_text_file(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise AnyBodyRunnerError(f"File not found: {path}")
    return {
        "file": str(path),
        "text": path.read_text(encoding="utf-8"),
    }


def parse_anybody_value(stdout: str):
    match = _OBJECT_VALUE_PATTERN.search(stdout)
    if not match:
        raise AnyBodyRunnerError("Could not parse AnyBody printed value")
    raw = match.group(1)
    normalized = raw.replace("{", "[").replace("}", "]")
    return json.loads(normalized)


def vector_magnitude(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _joint_value(joints: dict, name: str, frame: int, idx: int = 0, default: float = 0.0) -> float:
    """One DOF of one joint at one frame, zero-filled when the series is short.

    A Dump omits joints the model does not have, and callers can ask for more
    frames than were solved; neither case should abort an export.
    """
    series = joints.get(name)
    if not series or frame >= len(series):
        return default
    row = series[frame]
    return row[idx] if idx < len(row) else default


def _frame_joint_map(joints: dict, frame: int) -> dict:
    """Per-joint angle row for one frame, falling back to the first solved step."""
    return {
        name: (series[frame] if frame < len(series) else (series[0] if series else [0.0]))
        for name, series in joints.items()
    }


def extract_object_value(model: str, object_name: str, anybodycon: str | None = None, timeout: int = 300):
    result = print_object(model=model, object_name=object_name, anybodycon=anybodycon, timeout=timeout)
    return {
        "object": object_name,
        "value": parse_anybody_value(result.stdout),
        "stdout": result.stdout,
    }


def extract_object_value_after_operation(
    model: str,
    object_name: str,
    operation: str,
    anybodycon: str | None = None,
    timeout: int = 300,
):
    result = run_macro(
        model=model,
        commands=[f"operation {operation}", "run", f"print {object_name}"],
        anybodycon=anybodycon,
        timeout=timeout,
    )
    return {
        "object": object_name,
        "operation": operation,
        "value": parse_anybody_value(result.stdout),
        "stdout": result.stdout,
    }


_JOINT_SEGMENT = {"knee": "Knee", "hip": "Hip", "ankle": "Ankle"}


def _reframe_object(side: str, joint: str) -> str:
    if joint not in _JOINT_SEGMENT:
        raise AnyBodyRunnerError(
            f"Unknown joint {joint!r}; expected one of {sorted(_JOINT_SEGMENT)}"
        )
    return (
        f"Main.HumanModel.BodyModel.{side.capitalize()}.Leg.Jnt."
        f"{_JOINT_SEGMENT[joint]}.Constraints.Reaction.RefFrameOutput.F"
    )


def summarize_joint_reaction_force(
    model: str,
    joint: str,
    side: str = "Right",
    anybodycon: str | None = None,
    timeout: int = 300,
) -> dict:
    """One leg joint's reaction force from a dedicated single-joint model run."""
    side_name = side.capitalize()
    object_name = _reframe_object(side_name, joint)
    extracted = extract_object_value_after_operation(
        model=model,
        object_name=object_name,
        operation="Main.RunApplication",
        anybodycon=anybodycon,
        timeout=timeout,
    )
    value = extracted["value"]
    primary, secondary = value[0], value[1]
    return {
        "side": side_name,
        "joint": joint,
        "object": object_name,
        "force_vectors": value,
        "primary_vector": primary,
        "secondary_vector": secondary,
        "primary_magnitude": vector_magnitude(primary),
        "secondary_magnitude": vector_magnitude(secondary),
    }


def summarize_knee_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_joint_reaction_force(model, "knee", side, anybodycon, timeout)


def summarize_hip_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_joint_reaction_force(model, "hip", side, anybodycon, timeout)


def summarize_ankle_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_joint_reaction_force(model, "ankle", side, anybodycon, timeout)


def summarize_l5s1_compression(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    object_name = "Main.HumanModel.BodyModel.Trunk.JointsLumbar.L5SacrumJnt.Constraints.Reaction.Fout"
    extracted = extract_object_value_after_operation(
        model=model, object_name=object_name, operation="Main.RunApplication",
        anybodycon=anybodycon, timeout=timeout,
    )
    value = extracted["value"]
    return {
        "joint": "L5S1", "object": object_name,
        "force_vector": value,
        "anteroposterior": value[0],
        "compression": abs(value[1]),
        "mediolateral": value[2],
        "magnitude": vector_magnitude(value),
    }


# Full-chain results are cached per project fingerprint. Sweeps and bilateral queries
# routinely re-read the same unchanged project, and each miss costs a full solve.
_FULLCHAIN_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_FULLCHAIN_CACHE_MAX = 32


def _project_fingerprint(model_path: Path) -> tuple:
    """Fingerprint every .any file in the project.

    Keyed on the whole project rather than the main file alone, because editing
    Model/Mannequin.any leaves the main file's mtime untouched.
    """
    entries = []
    for path in sorted(model_path.parent.rglob("*.any")):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def clear_result_cache() -> dict:
    """Drop all cached full-chain results and report how many were held."""
    count = len(_FULLCHAIN_CACHE)
    _FULLCHAIN_CACHE.clear()
    return {"cleared_entries": count}


def summarize_full_chain_joint_forces(
    model: str,
    anybodycon: str | None = None,
    timeout: int = 300,
    use_cache: bool = True,
    include_stdout: bool = False,
    cancel_event=None,
) -> dict:
    """Extract all 4 joints (L5/S1, hip, knee, ankle) in a single model run.

    Cached per (model, AnyBodyCon, project fingerprint). A cache hit returns
    cached=True and omits stdout.
    """
    model_path = resolve_model(model)
    cache_key = (
        str(model_path),
        str(anybodycon or DEFAULT_ANYBODYCON),
        _project_fingerprint(model_path),
    )
    if use_cache:
        hit = _FULLCHAIN_CACHE.get(cache_key)
        if hit is not None:
            _FULLCHAIN_CACHE.move_to_end(cache_key)
            return {**hit, "stdout": "", "stdout_truncated": False, "cached": True}

    result = run_macro(model=str(model_path), commands=[
        "operation Main.RunApplication", "run",
        "print Main.HumanModel.BodyModel.Trunk.JointsLumbar.L5SacrumJnt.Constraints.Reaction.Fout",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Hip.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Hip.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Ankle.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Ankle.Constraints.Reaction.RefFrameOutput.F",
    ], anybodycon=anybodycon, timeout=timeout, cancel_event=cancel_event)
    stdout = result.stdout
    success = "Inverse dynamic analysis completed" in stdout

    def _parse_reframe(side, joint):
        pat = re.compile(
            rf'Main\.HumanModel\.BodyModel\.{side}\.Leg\.Jnt\.{joint}\.Constraints\.Reaction\.RefFrameOutput\.F\s*=\s*\{{\{{([\d\.\-eE+,\s]+)\}}',
            re.DOTALL)
        m = pat.search(stdout)
        if m:
            try:
                vals = [float(x.strip()) for x in m.group(1).split(",") if x.strip()]
                if len(vals) == 3:
                    return {"primary": vals, "secondary": [-v for v in vals], "magnitude": vector_magnitude(vals)}
            except ValueError as exc:
                logger.debug("Could not parse %s %s force vector: %s", side, joint, exc)
        return None

    def _parse_l5s1():
        pat = re.compile(
            r'Main\.HumanModel\.BodyModel\.Trunk\.JointsLumbar\.L5SacrumJnt\.Constraints\.Reaction\.Fout\s*=\s*\{([\d\.\-eE+,\s]+)\}',
            re.DOTALL)
        m = pat.search(stdout)
        if m:
            try:
                vals = [float(x.strip()) for x in m.group(1).split(",") if x.strip()]
                if len(vals) == 3:
                    return {"vector": vals, "compression": abs(vals[1]), "magnitude": vector_magnitude(vals)}
            except ValueError as exc:
                logger.debug("Could not parse L5/S1 force vector: %s", exc)
        return None

    parsed = {
        "l5s1": _parse_l5s1(),
        "right_hip": _parse_reframe("Right", "Hip"),
        "left_hip": _parse_reframe("Left", "Hip"),
        "right_knee": _parse_reframe("Right", "Knee"),
        "left_knee": _parse_reframe("Left", "Knee"),
        "right_ankle": _parse_reframe("Right", "Ankle"),
        "left_ankle": _parse_reframe("Left", "Ankle"),
    }

    payload = {
        "success": success,
        **parsed,
        "returncode": result.returncode,
        "stdout": stdout if include_stdout else _truncate(stdout),
        "stdout_truncated": not include_stdout and len(stdout) > STDOUT_LIMIT,
        # Every joint present means the parse is complete, so the run is worth caching.
        "cached": False,
    }

    if use_cache and success and all(parsed.values()):
        _FULLCHAIN_CACHE[cache_key] = dict(parsed) | {"success": success, "returncode": result.returncode}
        _FULLCHAIN_CACHE.move_to_end(cache_key)
        while len(_FULLCHAIN_CACHE) > _FULLCHAIN_CACHE_MAX:
            _FULLCHAIN_CACHE.popitem(last=False)

    return payload


def _joint_from_full_chain(full: dict, joint: str, side: str) -> dict | None:
    entry = full.get(f"{side.lower()}_{joint}")
    if not entry:
        return None
    primary = entry.get("primary") or []
    secondary = entry.get("secondary") or []
    return {
        "side": side.capitalize(),
        "joint": joint,
        "object": _reframe_object(side, joint),
        "force_vectors": [primary, secondary],
        "primary_vector": primary,
        "secondary_vector": secondary,
        "primary_magnitude": entry.get("magnitude", vector_magnitude(primary)),
        "secondary_magnitude": vector_magnitude(secondary),
    }


def summarize_bilateral_joint_forces(
    model: str,
    joint: str,
    anybodycon: str | None = None,
    timeout: int = 300,
    use_cache: bool = True,
) -> dict:
    """Both sides of one joint from a single full-chain solve.

    Falls back to a dedicated per-side run only for a side the combined parse
    could not recover, so the common path costs one solver run instead of two.
    """
    full = summarize_full_chain_joint_forces(
        model=model, anybodycon=anybodycon, timeout=timeout,
        use_cache=use_cache, cancel_event=None,
    )
    right = _joint_from_full_chain(full, joint, "Right")
    left = _joint_from_full_chain(full, joint, "Left")

    fallback_sides = []
    if right is None:
        right = summarize_joint_reaction_force(model, joint, "Right", anybodycon, timeout)
        fallback_sides.append("right")
    if left is None:
        left = summarize_joint_reaction_force(model, joint, "Left", anybodycon, timeout)
        fallback_sides.append("left")

    difference = right["primary_magnitude"] - left["primary_magnitude"]
    peak = max(right["primary_magnitude"], left["primary_magnitude"])
    return {
        "joint": joint,
        "right": right,
        "left": left,
        "primary_magnitude_difference": difference,
        # 100% = perfectly symmetrical loading; the usual reporting form in gait work.
        "symmetry_percent": round(100.0 * (1.0 - abs(difference) / peak), 2) if peak > 0 else None,
        "success": full.get("success", False),
        "solver_runs": 1 + len(fallback_sides),
        "fallback_sides": fallback_sides,
        "cached": full.get("cached", False),
    }


def summarize_bilateral_knee_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_bilateral_joint_forces(model, "knee", anybodycon, timeout)


def summarize_bilateral_hip_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_bilateral_joint_forces(model, "hip", anybodycon, timeout)


def summarize_bilateral_ankle_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    return summarize_bilateral_joint_forces(model, "ankle", anybodycon, timeout)


def no_gui_gait_knee_force_workflow(project_dir: str, overwrite: bool = False, timeout: int = 900) -> dict:
    created = create_project_from_named_template(destination=project_dir, template_name="gait-vaughan", overwrite=overwrite)
    main_file = created["default_main"]
    summary = summarize_bilateral_knee_reaction_forces(main_file, timeout=timeout)
    report_path = Path(project_dir) / "Output" / "knee-force-summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "workflow": "no-gui-gait-knee-force",
        "project_dir": project_dir,
        "main_file": main_file,
        "summary": summary,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def no_gui_new_posture_workflow(project_dir: str, preset_name: str, overwrite: bool = False, timeout: int = 900) -> dict:
    created = create_project_from_named_template(destination=project_dir, template_name="human-standing", overwrite=overwrite)
    modified = apply_posture_preset(project_dir=project_dir, preset_name=preset_name)
    main_file = created["default_main"]

    attempts = [preset_name]
    if preset_name == "mild-squat":
        attempts.append("forward-lean")
    elif preset_name == "staggered-stance":
        attempts.append("forward-lean")

    last_result = None
    success = False
    used_preset = preset_name
    for index, candidate in enumerate(attempts):
        if index > 0:
            created = create_project_from_named_template(destination=project_dir, template_name="human-standing", overwrite=True)
            modified = apply_posture_preset(project_dir=project_dir, preset_name=candidate)
            main_file = created["default_main"]
            used_preset = candidate
        last_result = run_macro(model=main_file, commands=["operation Main.RunApplication", "run"], timeout=timeout)
        if "Inverse dynamic analysis completed" in last_result.stdout:
            success = True
            break

    report = {
        "workflow": "no-gui-new-posture",
        "project_dir": project_dir,
        "requested_preset": preset_name,
        "used_preset": used_preset,
        "success": success,
        "main_file": main_file,
        "modification": modified,
        "run_result": last_result.to_dict() if last_result else None,
    }

    if success:
        summary = summarize_bilateral_knee_reaction_forces(main_file, timeout=timeout)
        report["summary"] = summary

    report_path = Path(project_dir) / "Output" / "posture-workflow-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def no_gui_state_workflow(project_dir: str, state: dict, overwrite: bool = False, timeout: int = 900) -> dict:
    created = create_project_from_named_template(destination=project_dir, template_name="human-standing", overwrite=overwrite)
    modified = apply_state_description(project_dir=project_dir, state=state)
    main_file = created["default_main"]

    attempts = [
        state,
        {**state, "right_knee_flexion": min(float(state.get("right_knee_flexion", 12)), 12), "left_knee_flexion": min(float(state.get("left_knee_flexion", 12)), 12), "thorax_extension": min(float(state.get("thorax_extension", 8)), 8)},
    ]

    success = False
    last_result = None
    used_state = attempts[0]
    for index, candidate in enumerate(attempts):
        if index > 0:
            created = create_project_from_named_template(destination=project_dir, template_name="human-standing", overwrite=True)
            modified = apply_state_description(project_dir=project_dir, state=candidate)
            main_file = created["default_main"]
            used_state = candidate
        last_result = run_macro(model=main_file, commands=["operation Main.RunApplication", "run"], timeout=timeout)
        if "Inverse dynamic analysis completed" in last_result.stdout:
            success = True
            break

    report = {
        "workflow": "no-gui-state",
        "project_dir": project_dir,
        "requested_state": state,
        "used_state": used_state,
        "success": success,
        "main_file": main_file,
        "modification": modified,
        "run_result": last_result.to_dict() if last_result else None,
    }
    if success:
        report["summary"] = summarize_bilateral_knee_reaction_forces(main_file, timeout=timeout)
    report_path = Path(project_dir) / "Output" / "state-workflow-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def apply_hand_load(project_dir: str, load_kg: float) -> dict:
    """Apply bilateral hand loads to a Human Standing project via AnyForce3D on PalmJoint."""
    project_path = Path(project_dir)
    mannequin_file = project_path / "Model" / "Mannequin.any"
    joints_file = project_path / "Model" / "JointsAndDrivers.any"
    if not mannequin_file.exists() or not joints_file.exists():
        raise AnyBodyRunnerError(f"Expected files not found in {project_dir}")

    load_n = load_kg * 9.81
    modifications = []

    # Add Load folder to Mannequin.any
    mannequin_text = mannequin_file.read_text(encoding="utf-8")
    if "AnyFolder Load" not in mannequin_text:
        load_block = f"""
  AnyFolder Load = {{
    AnyVec3 TopVertebra = {{0.000, 0.000, 0.000}};
    AnyFolder Right = {{
      AnyVec3 Shoulder = {{0.000, 0.000, 0.000}};
      AnyVec3 Elbow = {{0.000, 0.000, 0.000}};
      AnyVec3 Hand = {{0.000, {-load_n:.4f}, 0.000}};
      AnyVec3 Hip = {{0.000, 0.000, 0.000}};
      AnyVec3 Knee = {{0.000, 0.000, 0.000}};
      AnyVec3 Ankle = {{0.000, 0.000, 0.000}};
    }};
    AnyFolder Left = {{
      AnyVec3 Shoulder = {{0.000, 0.000, 0.000}};
      AnyVec3 Elbow = {{0.000, 0.000, 0.000}};
      AnyVec3 Hand = {{0.000, {-load_n:.4f}, 0.000}};
      AnyVec3 Hip = {{0.000, 0.000, 0.000}};
      AnyVec3 Knee = {{0.000, 0.000, 0.000}};
      AnyVec3 Ankle = {{0.000, 0.000, 0.000}};
    }};
  }};
"""
        mannequin_text = mannequin_text.rstrip().rstrip("};") + load_block + "\n};\n"
        mannequin_file.write_text(mannequin_text, encoding="utf-8")
        modifications.append("Added Load folder to Mannequin.any")
    else:
        modifications.append("Load folder already exists in Mannequin.any")

    # Add ExternalLoads to JointsAndDrivers.any
    joints_text = joints_file.read_text(encoding="utf-8")
    if "ExternalLoads" not in joints_text:
        external_loads = f"""

AnyFolder ExternalLoads = {{
  AnyFolder &hm = Main.Model.BodyModel;
  AnyFolder &mql = Main.HumanModel.Mannequin.Load;

  AnyFolder Right = {{
    AnyFolder &rhm = .hm.Right;
    AnyFolder &locmql = .mql.Right;

    AnyForce3D Hand = {{
      AnyRefNode &ApplPoint = .rhm.ShoulderArm.Seg.Hand.Ref.PalmJoint;
      F = .locmql.Hand;
    }};
  }};

  AnyFolder Left = {{
    AnyFolder &lhm = .hm.Left;
    AnyFolder &locmql = .mql.Left;

    AnyForce3D Hand = {{
      AnyRefNode &ApplPoint = .lhm.ShoulderArm.Seg.Hand.Ref.PalmJoint;
      F = .locmql.Hand;
    }};
  }};
}};
"""
        joints_text += external_loads
        joints_file.write_text(joints_text, encoding="utf-8")
        modifications.append("Added ExternalLoads to JointsAndDrivers.any")
    else:
        modifications.append("ExternalLoads already exists in JointsAndDrivers.any")

    return {
        "project_dir": project_dir,
        "load_kg": load_kg,
        "load_newtons": round(load_n, 2),
        "modifications": modifications,
    }


DEFAULT_MAX_SWEEP_CASES = int(os.environ.get("ANYBODY_MAX_SWEEP_CASES") or 512)


class _CaseFailed(RuntimeError):
    """A single sweep case failed; retried in isolation rather than aborting the sweep."""


def _stats(values: list[float]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / len(clean)
    return {
        "n": len(clean),
        "mean": round(mean, 2),
        "std": round(math.sqrt(variance), 2),
        "min": round(min(clean), 2),
        "max": round(max(clean), 2),
    }


def _symmetry_percent(right: dict | None, left: dict | None) -> float | None:
    """100% = identical left/right load. The usual reporting form in gait work."""
    if not right or not left:
        return None
    r, l = right.get("magnitude", 0.0), left.get("magnitude", 0.0)
    peak = max(r, l)
    return round(100.0 * (1.0 - abs(r - l) / peak), 2) if peak > 0 else None


def _case_metric(entry: dict, key: str):
    value = entry.get(key)
    if not isinstance(value, dict):
        return None
    # L5/S1 reports compression; the leg joints report a vector magnitude.
    return value.get("compression") if key == "l5s1" else value.get("magnitude")


def _write_sweep_chart(rows: list[dict], out_dir: Path, trunk_angles, knee_angles, load) -> str | None:
    """Heatmap of L5/S1 compression over the trunk x knee grid at one load level."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    index = {
        (r["trunk_angle"], r["knee_angle"]): r["l5s1"]["compression"]
        for r in rows
        if r["load_kg"] == load and r.get("l5s1")
    }
    if not index:
        return None
    # NaN keeps a missing case visually distinct from a genuine zero.
    grid = [
        [float("nan") if (t, k) not in index else index[(t, k)] for k in knee_angles]
        for t in trunk_angles
    ]

    fig, ax = plt.subplots(figsize=(1.2 * len(knee_angles) + 3, 0.9 * len(trunk_angles) + 3))
    image = ax.imshow(grid, cmap="viridis", aspect="auto", origin="lower")
    ax.set_xticks(range(len(knee_angles)), [f"{v:g}" for v in knee_angles])
    ax.set_yticks(range(len(trunk_angles)), [f"{v:g}" for v in trunk_angles])
    ax.set_xlabel("Knee flexion (deg)")
    ax.set_ylabel("Trunk flexion (deg)")
    ax.set_title(f"L5/S1 compression (N) - hand load {load:g} kg")
    for i, row in enumerate(grid):
        for j, value in enumerate(row):
            if value == value:  # not NaN
                ax.text(j, i, f"{value:.0f}", ha="center", va="center", color="w", fontsize=8)
    fig.colorbar(image, ax=ax, label="N")
    fig.tight_layout()
    path = out_dir / "sweep_l5s1_heatmap.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)


def _build_sweep_case(case_dir: Path, trunk, knee, load, overwrite: bool) -> Path:
    """Create (or reuse) one sweep case directory and return its main file."""
    if case_dir.exists():
        if not overwrite:
            existing = next(case_dir.glob("*.main.any"), None)
            if existing is not None:
                return existing
        shutil.rmtree(case_dir)

    created = create_project_from_named_template(
        destination=str(case_dir), template_name="human-standing", overwrite=True,
    )

    state: dict = {"right_knee_flexion": knee, "left_knee_flexion": knee}
    if trunk > 0:
        # Pelvis is dropped and shifted forward as the trunk flexes, so the
        # quasi-static pose stays balanced.
        state.update({
            "pelvis_pos_x": round(0.05 + trunk * 0.002, 4),
            "pelvis_pos_y": round(0.97 - trunk * 0.003, 4),
            "thorax_extension": trunk,
        })
    apply_state_description(str(case_dir), state)

    if load > 0:
        apply_hand_load(str(case_dir), load)

    main_file = Path(created["default_main"])
    # Quasi-static sweep: one step is enough and keeps each case to a single solve.
    text = main_file.read_text(encoding="utf-8").replace("nStep = 11;", "nStep = 1;")
    main_file.write_text(text, encoding="utf-8")
    return main_file


def run_full_chain_parametric_sweep(
    project_dir: str,
    trunk_angles: list[float],
    knee_angles: list[float],
    loads_kg: list[float],
    overwrite: bool = False,
    timeout: int = 300,
    anybodycon: str | None = None,
    output_dir: str | None = None,
    max_cases: int | None = None,
    retries: int = 1,
    write_csv: bool = True,
    write_chart: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
) -> dict:
    """Run a parametric sweep across trunk/knee/load combinations, extracting full-chain joint forces.

    Runs len(trunk_angles) * len(knee_angles) * len(loads_kg) cases. Each completed case is
    written to <output_dir>/cases/ so an interrupted sweep resumes instead of restarting;
    pass overwrite=True to force a clean rebuild.

    Args:
        overwrite: Rebuild cases even when a stored result exists. Default False resumes.
        max_cases: Refuse to start above this case count. Default 512.
        retries: Extra attempts per case after the first failure. Default 1.
        write_csv: Also emit sweep_results.csv alongside the JSON.
        write_chart: Emit an L5/S1 heatmap PNG (requires matplotlib).
        progress_callback: Called as (completed, total, case_name) after each case.
        cancel_event: threading.Event; checked between cases to stop early.
    """
    base_dir = Path(project_dir)
    out_dir = Path(output_dir) if output_dir else (base_dir.parent / "sweep_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = out_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    combos = [(t, k, l) for t in trunk_angles for k in knee_angles for l in loads_kg]
    total = len(combos)
    limit = DEFAULT_MAX_SWEEP_CASES if max_cases is None else int(max_cases)
    if total == 0:
        raise AnyBodyRunnerError("Sweep needs at least one trunk angle, knee angle and load.")
    if total > limit:
        raise AnyBodyRunnerError(
            f"Sweep would run {total} cases "
            f"({len(trunk_angles)} trunk x {len(knee_angles)} knee x {len(loads_kg)} load), "
            f"above the {limit}-case limit. Narrow the parameter lists or raise max_cases."
        )

    results: list[dict] = []
    reused = 0
    cancelled = False

    for case_id, (trunk, knee, load) in enumerate(combos, start=1):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break

        case_dir = base_dir / f"case_{case_id:03d}_T{trunk}_K{knee}_L{load}"
        case_json = cases_dir / f"{case_dir.name}.json"

        if not overwrite and case_json.exists():
            try:
                results.append(json.loads(case_json.read_text(encoding="utf-8")))
                reused += 1
                if progress_callback:
                    progress_callback(case_id, total, f"{case_dir.name} (reused)")
                continue
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring unreadable stored case %s: %s", case_json.name, exc)

        entry: dict | None = None
        last_error: str | None = None
        case_overwrite = overwrite

        for attempt in range(max(0, int(retries)) + 1):
            try:
                main_file = _build_sweep_case(case_dir, trunk, knee, load, case_overwrite)
                full_chain = summarize_full_chain_joint_forces(
                    model=str(main_file), anybodycon=anybodycon,
                    timeout=timeout, cancel_event=cancel_event,
                )
                if not full_chain["success"]:
                    raise _CaseFailed("inverse dynamic analysis did not complete")
                entry = {
                    "case_id": case_id,
                    "trunk_angle": trunk,
                    "knee_angle": knee,
                    "load_kg": load,
                    "success": True,
                    "attempts": attempt + 1,
                    "error": None,
                    "l5s1": full_chain["l5s1"],
                    "right_hip": full_chain["right_hip"],
                    "left_hip": full_chain["left_hip"],
                    "right_knee": full_chain["right_knee"],
                    "left_knee": full_chain["left_knee"],
                    "right_ankle": full_chain["right_ankle"],
                    "left_ankle": full_chain["left_ankle"],
                }
                break
            except Exception as exc:  # one bad case must not lose the whole sweep
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Sweep case %s attempt %d failed: %s", case_dir.name, attempt + 1, last_error)
                case_overwrite = True  # a retry must rebuild rather than reuse a broken case
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break

        if cancelled and entry is None:
            break

        if entry is None:
            entry = {
                "case_id": case_id,
                "trunk_angle": trunk,
                "knee_angle": knee,
                "load_kg": load,
                "success": False,
                "attempts": max(0, int(retries)) + 1,
                "error": last_error,
                "l5s1": None,
                "right_hip": None,
                "left_hip": None,
                "right_knee": None,
                "left_knee": None,
                "right_ankle": None,
                "left_ankle": None,
            }

        results.append(entry)
        try:
            case_json.write_text(json.dumps(entry, ensure_ascii=True, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist case %s: %s", case_dir.name, exc)
        if progress_callback:
            progress_callback(case_id, total, case_dir.name)

    json_path = out_dir / "sweep_results.json"
    json_path.write_text(json.dumps(results, ensure_ascii=True, indent=2), encoding="utf-8")

    success_results = [r for r in results if r.get("success")]
    summary = {
        "total_cases": total,
        "attempted_cases": len(results),
        "success_count": len(success_results),
        "failure_count": len(results) - len(success_results),
        "reused_cases": reused,
        "cancelled": cancelled,
        "output_dir": str(out_dir),
        "results_file": str(json_path),
    }

    if success_results:
        summary["l5s1_compression"] = _stats([_case_metric(r, "l5s1") for r in success_results])
        for joint in ("hip", "knee", "ankle"):
            for side in ("right", "left"):
                summary[f"{side}_{joint}"] = _stats([
                    _case_metric(r, f"{side}_{joint}") for r in success_results
                ])
            symmetry = [
                _symmetry_percent(r.get(f"right_{joint}"), r.get(f"left_{joint}"))
                for r in success_results
            ]
            summary[f"{joint}_symmetry_percent"] = _stats(symmetry)["mean"]

    if write_csv and results:
        import csv as _csv

        csv_path = out_dir / "sweep_results.csv"
        columns = ["right_hip", "left_hip", "right_knee", "left_knee", "right_ankle", "left_ankle"]
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.writer(handle)
            writer.writerow(
                ["case_id", "trunk_angle", "knee_angle", "load_kg", "success", "l5s1_compression", *columns]
            )
            for row in results:
                writer.writerow([
                    row["case_id"], row["trunk_angle"], row["knee_angle"], row["load_kg"], row["success"],
                    _case_metric(row, "l5s1"), *[_case_metric(row, c) for c in columns],
                ])
        summary["csv_file"] = str(csv_path)

    if write_chart and success_results and loads_kg:
        chart_path = _write_sweep_chart(success_results, out_dir, trunk_angles, knee_angles, loads_kg[0])
        if chart_path:
            summary["chart_file"] = chart_path

    return {
        "summary": summary,
        "results": results,
    }


_SPEC_REQUIRED_KEYS = {
    "gait-knee-force": ("project_dir",),
    "new-posture": ("project_dir", "preset_name"),
    "state": ("project_dir", "state"),
    "full-chain": ("project_dir",),
    "parametric-sweep": ("project_dir", "trunk_angles", "knee_angles", "loads_kg"),
}


def run_task_spec(spec: dict) -> dict:
    workflow = spec.get("workflow")
    if workflow not in _SPEC_REQUIRED_KEYS:
        raise AnyBodyRunnerError(
            f"Unknown task spec workflow: {workflow!r}. "
            f"Supported workflows: {sorted(_SPEC_REQUIRED_KEYS)}"
        )
    missing = [key for key in _SPEC_REQUIRED_KEYS[workflow] if key not in spec]
    if missing:
        raise AnyBodyRunnerError(
            f"Task spec for workflow {workflow!r} is missing required key(s): {missing}. "
            f"Required: {list(_SPEC_REQUIRED_KEYS[workflow])}"
        )

    project_dir = spec["project_dir"]
    overwrite = bool(spec.get("overwrite", False))
    timeout = int(spec.get("timeout", 900))

    if workflow == "gait-knee-force":
        return no_gui_gait_knee_force_workflow(project_dir=project_dir, overwrite=overwrite, timeout=timeout)
    if workflow == "new-posture":
        return no_gui_new_posture_workflow(
            project_dir=project_dir,
            preset_name=spec["preset_name"],
            overwrite=overwrite,
            timeout=timeout,
        )
    if workflow == "state":
        return no_gui_state_workflow(
            project_dir=project_dir,
            state=spec["state"],
            overwrite=overwrite,
            timeout=timeout,
        )
    if workflow == "full-chain":
        model = spec.get("model")
        if model:
            model_path = str(Path(project_dir) / model) if not Path(model).is_absolute() else model
        else:
            created = create_project_from_named_template(destination=project_dir, template_name="human-standing", overwrite=overwrite)
            model_path = created["default_main"]
        return summarize_full_chain_joint_forces(model=model_path, timeout=timeout)
    if workflow == "parametric-sweep":
        return run_full_chain_parametric_sweep(
            project_dir=project_dir,
            trunk_angles=spec["trunk_angles"],
            knee_angles=spec["knee_angles"],
            loads_kg=spec["loads_kg"],
            output_dir=spec.get("output_dir"),
            overwrite=overwrite,
            timeout=int(spec.get("case_timeout", spec.get("timeout", 300))),
            max_cases=spec.get("max_cases"),
            retries=int(spec.get("retries", 1)),
            write_csv=bool(spec.get("write_csv", True)),
            write_chart=bool(spec.get("write_chart", False)),
        )
    raise AnyBodyRunnerError(f"Unhandled task spec workflow: {workflow!r}")


def run_task_spec_file(spec_file: str) -> dict:
    spec_path = Path(spec_file)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result = run_task_spec(spec)
    result["task_spec_file"] = str(spec_path)
    return result


def scan_project(project_dir: str) -> dict:
    """List the AnyBody source files in a project directory.

    `any_files` is every .any file and therefore includes `main_files`; the
    main/support split is reported explicitly so the two counts cannot be misread
    as disjoint.
    """
    base = Path(project_dir)
    if not base.exists():
        raise AnyBodyRunnerError(f"Project directory not found: {base}")
    main_files = sorted(str(path) for path in base.rglob("*.main.any"))
    any_files = sorted(str(path) for path in base.rglob("*.any"))
    support_files = [path for path in any_files if path not in set(main_files)]
    return {
        "project_dir": str(base),
        "main_files": main_files,
        "any_files": any_files,
        "support_any_files": support_files,
        "main_file_count": len(main_files),
        "any_file_count": len(any_files),
        "support_any_file_count": len(support_files),
    }


def run_and_dump_json(output_path: str, model: str | None = None, commands: Iterable[str] | None = None) -> dict:
    result = run_macro(model=model, commands=commands)
    # This artifact is explicitly the raw run record, so it keeps the full console.
    payload = result.to_dict(include_stdout=True)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


_ANIMATION_JOINT_CONFIGS = [
    ("RightHip", "Right.Leg.JntDOF.Hip.Pos", 3),
    ("RightKnee", "Right.Leg.JntDOF.Knee.Pos", 1),
    ("RightAnkle", "Right.Leg.JntDOF.Ankle.Pos", 1),
    ("LeftHip", "Left.Leg.JntDOF.Hip.Pos", 3),
    ("LeftKnee", "Left.Leg.JntDOF.Knee.Pos", 1),
    ("LeftAnkle", "Left.Leg.JntDOF.Ankle.Pos", 1),
]


def _parse_per_step_arrays(dump_text: str) -> dict[str, list[list[float]]]:
    """Parse AnyBody Dump output text and extract per-step arrays for configured joints."""
    result: dict[str, list[list[float]]] = {}
    for name, path_suffix, _ in _ANIMATION_JOINT_CONFIGS:
        pat = re.compile(
            rf"Main\.Study\.Output\.Model\.BodyModel\.{re.escape(path_suffix)}\s*=\s*({{.+?}})\s*;",
            re.DOTALL,
        )
        m = pat.search(dump_text)
        if not m:
            continue
        content = m.group(1).strip()
        steps: list[list[float]] = []
        depth = 0
        current = ""
        for ch in content:
            if ch == "{":
                depth += 1
                if depth == 2:
                    current = ""
                    continue
            elif ch == "}":
                depth -= 1
                if depth == 1:
                    vals = [float(x.strip()) for x in current.split(",") if x.strip()]
                    steps.append(vals)
                    current = ""
                    continue
            if depth >= 2:
                current += ch
        result[name] = steps
    return result


def extract_animation_data(
    model: str,
    n_frames: int | None = None,
    anybodycon: str | None = None,
    timeout: int = 600,
) -> dict:
    """Extract per-step joint angle time series from an AnyBody model using native BodyModel Dump.

    Creates a writable project copy, runs the model with configured nStep, then uses
    AnyBody classoperation 'Dump' on the BodyModel output folder to read per-step
    joint DOF positions. Returns structured data for BVH/CSV/stick-figure export.
    """
    model_path = Path(resolve_model(model))

    # Determine template type from model path    _tmpl = "human-standing"
    if str(model_path.parent).startswith(str(DEFAULT_GAIT_VAUGHAN_MAIN.parent)):
        _tmpl = "gait-vaughan"
    elif str(model_path).startswith(str(DEFAULT_HUMAN_STANDING)):
        _tmpl = "human-standing"

    # Create writable project copy
    tmp_root = Path(tempfile.mkdtemp())
    tmp_dir = tmp_root / "anim_project"
    try:
        created = create_project_from_named_template(
            destination=str(tmp_dir),
            template_name=_tmpl,
            overwrite=True,
        )
        main_file = Path(created["default_main"])

        # Keep generated animation projects deterministic for requested frame counts.
        if n_frames is not None and n_frames > 1:
            main_text = main_file.read_text(encoding="utf-8")
            main_text = re.sub(r"nStep\s*=\s*\d+;", f"nStep = {n_frames - 1};", main_text)
            main_file.write_text(main_text, encoding="utf-8")

        result = run_macro(
            model=str(main_file),
            commands=[
                "operation Main.RunApplication",
                "run",
                'classoperation Main.Study.Output.Model.BodyModel "Dump"',
            ],
            anybodycon=anybodycon,
            timeout=timeout,
        )
        if not result.ok:
            raise AnyBodyRunnerError(f"Model run failed: {result.stderr[:500]}")

        joint_data = _parse_per_step_arrays(result.stdout)
        n_frames_actual = len(next(iter(joint_data.values()), []))
        if n_frames_actual == 0:
            raise AnyBodyRunnerError("No per-step joint data extracted from Dump output")

        seg_result = run_macro(
            model=str(model_path),
            commands=["print Main.HumanModel.Anthropometrics.SegmentDimensions"],
            anybodycon=anybodycon,
            timeout=timeout,
        )
        seg_text = seg_result.stdout
        seg = {}
        for key, pat in [
            ("ThighLength", r"ThighLength\s*=\s*([\d.]+)"),
            ("ShankLength", r"ShankLength\s*=\s*([\d.]+)"),
            ("FootLength", r"FootLength\s*=\s*([\d.]+)"),
            ("TrunkHeight", r"TrunkHeight\s*=\s*([\d.]+)"),
            ("PelvisWidth", r"PelvisWidth\s*=\s*([\d.]+)"),
        ]:
            m = re.search(pat, seg_text)
            if m:
                seg[key] = float(m.group(1))

        return {
            "n_frames": n_frames_actual,
            "joints": joint_data,
            "segment_lengths": seg,
            "extraction_method": "native_anybody_dump",
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def export_animation_bvh(animation_data: dict, output_path: str) -> dict:
    """Export extracted joint animation data as a BVH file."""
    joints = animation_data["joints"]
    seg = animation_data["segment_lengths"]
    n_frames = animation_data["n_frames"]

    tl = seg.get("ThighLength", 0.41)
    sl = seg.get("ShankLength", 0.42)
    fl = seg.get("FootLength", 0.26)
    th = seg.get("TrunkHeight", 0.6)
    pw = seg.get("PelvisWidth", 0.088)

    lines = [
        "HIERARCHY",
        "ROOT Hip",
        "{",
        f"\tOFFSET 0 {th} 0",
        "\tCHANNELS 3 Xposition Yposition Zposition Zrotation Xrotation Yrotation",
        # Right leg
        "\tJOINT RightHip",
        "\t{",
        f"\t\tOFFSET {pw / 2} 0 0",
        "\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\tJOINT RightKnee",
        "\t\t{",
        f"\t\t\tOFFSET 0 -{tl} 0",
        "\t\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\t\tJOINT RightAnkle",
        "\t\t\t{",
        f"\t\t\t\tOFFSET 0 -{sl} 0",
        "\t\t\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\t\t\tEnd Site",
        "\t\t\t\t{",
        f"\t\t\t\t\tOFFSET 0 -{fl} 0",
        "\t\t\t\t}",
        "\t\t\t}",
        "\t\t}",
        "\t}",
        # Left leg
        "\tJOINT LeftHip",
        "\t{",
        f"\t\tOFFSET -{pw / 2} 0 0",
        "\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\tJOINT LeftKnee",
        "\t\t{",
        f"\t\t\tOFFSET 0 -{tl} 0",
        "\t\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\t\tJOINT LeftAnkle",
        "\t\t\t{",
        f"\t\t\t\tOFFSET 0 -{sl} 0",
        "\t\t\t\tCHANNELS 3 Zrotation Xrotation Yrotation",
        "\t\t\t\tEnd Site",
        "\t\t\t\t{",
        f"\t\t\t\t\tOFFSET 0 -{fl} 0",
        "\t\t\t\t}",
        "\t\t\t}",
        "\t\t}",
        "\t}",
        "}",
        "MOTION",
        f"Frames: {n_frames}",
        "Frame Time: 0.033333",
    ]

    for frame in range(n_frames):
        def get_deg(name, idx=0):
            return math.degrees(_joint_value(joints, name, frame, idx))

        frame_parts = [
            "0 0 0",  # root position
            "0 0 0",  # root rotation
            # Right: hip(flex, abd, rot), knee(flex), ankle(flex)
            f"{get_deg('RightHip', 0):.4f} {get_deg('RightHip', 1):.4f} {get_deg('RightHip', 2):.4f}",
            f"{get_deg('RightKnee', 0):.4f} 0 0",
            f"{get_deg('RightAnkle', 0):.4f} 0 0",
            # Left
            f"{get_deg('LeftHip', 0):.4f} {get_deg('LeftHip', 1):.4f} {get_deg('LeftHip', 2):.4f}",
            f"{get_deg('LeftKnee', 0):.4f} 0 0",
            f"{get_deg('LeftAnkle', 0):.4f} 0 0",
        ]
        lines.append(" ".join(frame_parts))

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return {"bvh_file": output_path, "n_frames": n_frames, "joint_count": len(joints)}


def render_stick_figure_animation(animation_data: dict, output_path: str, fps: int = 30) -> dict:
    """Render a sagittal-plane stick figure MP4/GIF from extracted animation data."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.animation as animation
    except ImportError:
        return {"error": "matplotlib not available, install with: pip install matplotlib"}

    joints = animation_data["joints"]
    seg = animation_data["segment_lengths"]
    n_frames = animation_data["n_frames"]

    tl = seg.get("ThighLength", 0.41)
    sl = seg.get("ShankLength", 0.42)
    fl = seg.get("FootLength", 0.26)
    th = seg.get("TrunkHeight", 0.6)
    pw = seg.get("PelvisWidth", 0.088)

    def compute_pose(frame: int) -> dict:
        def ang(name: str, idx: int = 0) -> float:
            return _joint_value(joints, name, frame, idx)

        rh = ang("RightHip", 0)
        rk = ang("RightKnee", 0)
        ra = ang("RightAnkle", 0)
        lh = ang("LeftHip", 0)
        lk = ang("LeftKnee", 0)
        la = ang("LeftAnkle", 0)

        # Right leg
        rhx, rhy = pw / 2, th
        rkx = rhx + tl * math.sin(rh)
        rky = rhy - tl * math.cos(rh)
        rax = rkx + sl * math.sin(rh + rk)
        ray = rky - sl * math.cos(rh + rk)
        rfx = rax + fl * math.sin(rh + rk + ra)
        rfy = ray - fl * math.cos(rh + rk + ra)

        # Left leg
        lhx, lhy = -pw / 2, th
        lkx = lhx + tl * math.sin(lh)
        lky = lhy - tl * math.cos(lh)
        lax = lkx + sl * math.sin(lh + lk)
        lay = lky - sl * math.cos(lh + lk)
        lfx = lax + fl * math.sin(lh + lk + la)
        lfy = lay - fl * math.cos(lh + lk + la)

        return {
            "trunk": [(0, th), (0, th + 0.4)],
            "r_leg": [(rhx, rhy), (rkx, rky), (rax, ray), (rfx, rfy)],
            "l_leg": [(lhx, lhy), (lkx, lky), (lax, lay), (lfx, lfy)],
        }

    fig, ax = plt.subplots(figsize=(9, 8))
    max_extent = max(tl + sl + fl, th + 0.4) + 0.1

    def update(frame):
        ax.clear()
        ax.set_xlim(-0.3, 0.3)
        ax.set_ylim(-0.1, max_extent)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.3)
        ax.set_title(f"AnyBody Stick Figure - Frame {frame + 1}/{n_frames}")

        pose = compute_pose(frame)
        for name, seg_pts, color, ls in [
            ("Trunk", pose["trunk"], "k", "-"),
            ("Right", pose["r_leg"], "b", "-o"),
            ("Left", pose["l_leg"], "r", "-o"),
        ]:
            xs = [p[0] for p in seg_pts]
            ys = [p[1] for p in seg_pts]
            ax.plot(xs, ys, color + ls, linewidth=2, markersize=5, label=name)

        ax.legend(loc="upper right", fontsize=8)

        # Joint angle values (vertical display)
        text_str = "\n".join(_angle_text_lines(joints, frame, n_frames))
        ax.text(1.02, 0.98, text_str, transform=ax.transAxes, fontsize=9,
                fontfamily="monospace", verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.9))

    ext = Path(output_path).suffix.lower()
    writer = "pillow" if ext in (".gif",) else "ffmpeg"
    ani = animation.FuncAnimation(fig, update, frames=n_frames, interval=1000 // fps, repeat=True)
    ani.save(output_path, writer=writer, fps=fps, dpi=100)
    plt.close(fig)
    return {"animation_file": output_path, "n_frames": n_frames, "fps": fps}


def _rot_x(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _rot_y(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rot_z(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mat_mul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    return [[sum(a * b for a, b in zip(A_row, B_col)) for B_col in zip(*B)] for A_row in A]


def _mat_vec_mul(A: list[list[float]], v: list[float]) -> list[float]:
    return [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]


def _vec_add(a: list[float], b: list[float]) -> list[float]:
    return [a[i] + b[i] for i in range(3)]


def _vec_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def compute_3d_pose(
    frame_angles: dict[str, list[float]],
    seg_lengths: dict[str, float],
) -> dict[str, list[list[float]]]:
    tl = seg_lengths.get("ThighLength", 0.41)
    sl = seg_lengths.get("ShankLength", 0.42)
    fl = seg_lengths.get("FootLength", 0.26)
    th = seg_lengths.get("TrunkHeight", 0.6)
    pw = seg_lengths.get("PelvisWidth", 0.088)

    def ang(name: str, idx: int = 0) -> float:
        arr = frame_angles.get(name, [0])
        return arr[idx] if len(arr) > idx else 0.0

    rh_flex = ang("RightHip", 0)
    rh_abd = ang("RightHip", 1)
    rh_rot = ang("RightHip", 2)
    rk = ang("RightKnee", 0)
    ra = ang("RightAnkle", 0)
    lh_flex = ang("LeftHip", 0)
    lh_abd = ang("LeftHip", 1)
    lh_rot = ang("LeftHip", 2)
    lk = ang("LeftKnee", 0)
    la = ang("LeftAnkle", 0)

    def leg_chain(hip_center, hip_flex, hip_abd, hip_rot, knee_flex, ankle_flex):
        R_hip = _mat_mul(_mat_mul(_rot_z(hip_flex), _rot_x(hip_abd)), _rot_y(hip_rot))
        knee = _vec_add(hip_center, _mat_vec_mul(R_hip, [0, -tl, 0]))
        R_knee = _mat_mul(R_hip, _rot_z(knee_flex))
        ankle = _vec_add(knee, _mat_vec_mul(R_knee, [0, -sl, 0]))
        R_ankle = _mat_mul(R_knee, _rot_z(ankle_flex))
        foot = _vec_add(ankle, _mat_vec_mul(R_ankle, [0, 0, fl]))
        return {
            "hip": hip_center,
            "knee": knee,
            "ankle": ankle,
            "foot": foot,
        }

    r = leg_chain([pw / 2, th, 0], rh_flex, rh_abd, rh_rot, rk, ra)
    l = leg_chain([-pw / 2, th, 0], lh_flex, lh_abd, lh_rot, lk, la)

    pelvis_center = [0, th, 0]
    trunk_top = [0, th + 0.45, 0]

    return {
        "segments": {
            "trunk": [pelvis_center, trunk_top],
            "right_thigh": [r["hip"], r["knee"]],
            "right_shank": [r["knee"], r["ankle"]],
            "right_foot": [r["ankle"], r["foot"]],
            "left_thigh": [l["hip"], l["knee"]],
            "left_shank": [l["knee"], l["ankle"]],
            "left_foot": [l["ankle"], l["foot"]],
        },
        "joints": {
            "pelvis": pelvis_center,
            "right_hip": r["hip"], "right_knee": r["knee"], "right_ankle": r["ankle"], "right_foot": r["foot"],
            "left_hip": l["hip"], "left_knee": l["knee"], "left_ankle": l["ankle"], "left_foot": l["foot"],
        },
    }


def _cylinder_between(ax, p1, p2, radius, color, alpha=0.85, n_phi=16):
    import numpy as np
    p1_a = np.array(p1, dtype=float)
    p2_a = np.array(p2, dtype=float)
    v = p2_a - p1_a
    length = np.linalg.norm(v)
    if length < 1e-10:
        return
    v_hat = v / length
    z_axis = np.array([0.0, 0.0, 1.0])
    if np.allclose(v_hat, z_axis):
        R = np.eye(3)
    elif np.allclose(v_hat, -z_axis):
        R = np.diag([1, -1, -1])
    else:
        axis = np.cross(z_axis, v_hat)
        axis = axis / np.linalg.norm(axis)
        angle = math.acos(np.clip(np.dot(z_axis, v_hat), -1.0, 1.0))
        K = np.array([[0, -axis[2], axis[1]],
                      [axis[2], 0, -axis[0]],
                      [-axis[1], axis[0], 0]])
        R = np.eye(3) + math.sin(angle) * K + (1.0 - math.cos(angle)) * (K @ K)
    phi = np.linspace(0, 2.0 * math.pi, n_phi)
    n_z = 2
    Phi, Z = np.meshgrid(phi, np.linspace(-length / 2.0, length / 2.0, n_z))
    X = radius * np.cos(Phi)
    Y = radius * np.sin(Phi)
    pts = np.stack([X, Y, Z], axis=-1)
    transformed = np.dot(pts.reshape(-1, 3), R.T).reshape(*X.shape, 3)
    mid = (p1_a + p2_a) / 2.0
    transformed = transformed + mid.reshape(1, 1, 3)
    ax.plot_surface(transformed[..., 0], transformed[..., 1], transformed[..., 2],
                    color=color, alpha=alpha, shade=True, linewidth=0)


def _sphere_at(ax, center, radius, color, alpha=0.9, n_phi=12):
    import numpy as np
    u = np.linspace(0, 2.0 * math.pi, n_phi)
    v = np.linspace(0, math.pi, n_phi)
    x = center[0] + radius * np.outer(np.cos(u), np.sin(v))
    y = center[1] + radius * np.outer(np.sin(u), np.sin(v))
    z = center[2] + radius * np.outer(np.ones(n_phi), np.cos(v))
    ax.plot_surface(x, y, z, color=color, alpha=alpha, shade=True, linewidth=0)


def render_3d_animation(animation_data: dict, output_path: str, fps: int = 30) -> dict:
    """Render a full 3D body animation with cylindrical bones from extracted AnyBody data."""
    try:
        import numpy as np
        import matplotlib.pyplot as plt
        import matplotlib.animation as animation
    except ImportError as e:
        return {"error": f"Missing dependency: {e}. Install with: pip install matplotlib numpy"}

    joints = animation_data["joints"]
    seg = animation_data["segment_lengths"]
    n_frames = animation_data["n_frames"]

    # Segment radius by body part
    segment_config = [
        ("trunk", 0.08, "#556B2F"),
        ("right_thigh", 0.055, "#4169E1"),
        ("right_shank", 0.04, "#6495ED"),
        ("right_foot", 0.025, "#4682B4"),
        ("left_thigh", 0.055, "#CD5C5C"),
        ("left_shank", 0.04, "#F08080"),
        ("left_foot", 0.025, "#B22222"),
    ]
    joint_config = [
        ("pelvis", 0.05, "#333333"),
        ("right_hip", 0.035, "#4169E1"),
        ("right_knee", 0.03, "#6495ED"),
        ("right_ankle", 0.025, "#4682B4"),
        ("left_hip", 0.035, "#CD5C5C"),
        ("left_knee", 0.03, "#F08080"),
        ("left_ankle", 0.025, "#B22222"),
    ]

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")

    # Compute bounding box
    all_pts = []
    for f_idx in range(n_frames):
        frame_keys = _frame_joint_map(joints, f_idx)
        pose = compute_3d_pose(frame_keys, seg)
        for pt in pose["joints"].values():
            all_pts.append(pt)
        for seg_pts in pose["segments"].values():
            all_pts.extend(seg_pts)
    if all_pts:
        all_pts_arr = np.array(all_pts)
        min_pt = all_pts_arr.min(axis=0)
        max_pt = all_pts_arr.max(axis=0)
        center = (min_pt + max_pt) / 2
        size = max(max_pt - min_pt) / 2 + 0.15
    else:
        center = [0, 0.5, 0]
        size = 0.75

    def update(frame):
        ax.cla()
        frame_keys = _frame_joint_map(joints, frame)
        pose = compute_3d_pose(frame_keys, seg)

        # Draw segments as cylinders
        for seg_name, radius, color in segment_config:
            pts = pose["segments"].get(seg_name)
            if pts and len(pts) == 2:
                _cylinder_between(ax, pts[0], pts[1], radius, color)

        # Draw joints as spheres
        for joint_name, radius, color in joint_config:
            pt = pose["joints"].get(joint_name)
            if pt is not None:
                _sphere_at(ax, pt, radius, color)

        ax.set_xlim(center[0] - size, center[0] + size)
        ax.set_ylim(center[1] - size, center[1] + size)
        ax.set_zlim(center[2] - size, center[2] + size)
        ax.set_xlabel("X (m)")
        ax.set_ylabel("Y (m)")
        ax.set_zlabel("Z (m)")
        ax.view_init(elev=20, azim=-60)
        ax.set_title(f"AnyBody 3D Gait - Frame {frame + 1}/{n_frames}")

        # Joint angle values (vertical display)
        ax.text2D(0.02, 0.98, "\n".join(_angle_text_lines(joints, frame, n_frames)), transform=ax.transAxes, fontsize=9,
                  fontfamily="monospace", verticalalignment="top",
                  bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.9))

    ext = Path(output_path).suffix.lower()
    if ext == ".gif":
        ani = animation.FuncAnimation(fig, update, frames=n_frames, interval=1000 // fps, repeat=True)
        ani.save(output_path, writer="pillow", fps=fps, dpi=120)
    else:
        try:
            ani = animation.FuncAnimation(fig, update, frames=n_frames, interval=1000 // fps, repeat=True)
            ani.save(output_path, writer="ffmpeg", fps=fps, dpi=120)
        except Exception as e:
            plt.close(fig)
            return {"error": f"FFmpeg not available: {e}. Install ffmpeg or use .gif output instead.",
                    "animation_file": None}
    plt.close(fig)
    return {"animation_file": output_path, "n_frames": n_frames, "fps": fps, "format": "3d"}


def export_animation_full(
    model: str,
    output_prefix: str = "anybody_animation",
    n_frames: int | None = None,
    formats: list[str] | None = None,
    anybodycon: str | None = None,
    timeout: int = 600,
) -> dict:
    """Full pipeline: extract native AnyBody animation data -> BVH -> CSV -> 2D/3D animation.

    Args:
        model: Path to AnyBody .main.any file
        output_prefix: Output path prefix (without extension)
        n_frames: Number of animation frames (None = use model's nStep)
        formats: Output formats. Default: ['bvh', 'csv', 'gif']. Supports 'mp4' for 3D cylinder rendering.
    """
    if formats is None:
        formats = ["bvh", "csv", "gif"]

    data = extract_animation_data(model=model, n_frames=n_frames, anybodycon=anybodycon, timeout=timeout)
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    outputs = {"extraction": data}

    if "bvh" in formats:
        bvh_path = prefix.with_suffix(".bvh")
        outputs["bvh"] = export_animation_bvh(data, str(bvh_path))

    if "csv" in formats:
        csv_path = prefix.with_suffix(".csv")
        import csv as csv_module
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.writer(f)
            header = ["frame"]
            for name in [j[0] for j in _ANIMATION_JOINT_CONFIGS]:
                n_dims = [j[2] for j in _ANIMATION_JOINT_CONFIGS if j[0] == name][0]
                for d in range(n_dims):
                    header.append(f"{name}_{d}")
            writer.writerow(header)
            for i in range(data["n_frames"]):
                row = [i]
                for name, _, n_dims in _ANIMATION_JOINT_CONFIGS:
                    vals = data["joints"].get(name, [[0]])[i]
                    for d in range(n_dims):
                        row.append(vals[d] if len(vals) > d else 0)
                writer.writerow(row)
        outputs["csv"] = {"csv_file": str(csv_path), "n_frames": data["n_frames"]}

    if "gif" in formats:
        gif_path = prefix.with_suffix(".gif")
        outputs["gif"] = render_stick_figure_animation(data, str(gif_path))

    if "mp4" in formats:
        mp4_path = prefix.with_suffix(".mp4")
        r3d = render_3d_animation(data, str(mp4_path))
        if "error" in r3d:
            gif3d_path = prefix.with_suffix(".gif").with_name(prefix.stem + "_3d.gif")
            r3d = render_3d_animation(data, str(gif3d_path))
            r3d["fallback_from"] = "mp4"
        outputs["mp4"] = r3d

    return outputs


CAMERA_CLASS_TEMPLATE_PATH = _env_path(
    "ANYBODY_CAMERA_TEMPLATE",
    AMMR_DIR / "Tools" / "ModelUtilities" / "Video" / "CameraClassTemplate.any",
)


def _angle_text_lines(joints: dict, frame: int, n_frames: int) -> list[str]:
    """Per-frame joint-angle readout shared by the 2D and 3D renderers."""

    def ang(name: str, idx: int = 0) -> float:
        return math.degrees(_joint_value(joints, name, frame, idx))

    return [
        f"Frame {frame + 1}/{n_frames}",
        "",
        "Right:",
        f"  Hip  flex={ang('RightHip',0):6.1f} abd={ang('RightHip',1):5.1f} rot={ang('RightHip',2):5.1f}",
        f"  Knee flex={ang('RightKnee',0):6.1f}",
        f"  Ankl flex={ang('RightAnkle',0):6.1f}",
        "",
        "Left:",
        f"  Hip  flex={ang('LeftHip',0):6.1f} abd={ang('LeftHip',1):5.1f} rot={ang('LeftHip',2):5.1f}",
        f"  Knee flex={ang('LeftKnee',0):6.1f}",
        f"  Ankl flex={ang('LeftAnkle',0):6.1f}",
    ]


_OVERLAY_FORCE_KEYS = [
    ("l5s1", "L5/S1"),
    ("right_hip", "R.Hip"), ("left_hip", "L.Hip"),
    ("right_knee", "R.Knee"), ("left_knee", "L.Knee"),
    ("right_ankle", "R.Ankl"), ("left_ankle", "L.Ankl"),
]


def _overlay_values_on_frames(
    png_files: list[Path],
    anim_data: dict | None,
    force_data: dict | None,
) -> list[Path]:
    """Overlay per-frame joint angles and end-state joint forces on each PNG frame.

    Angles are per-step and therefore differ per frame. Forces come from one solved
    configuration, so that panel is labelled as an end-state snapshot instead of
    being presented as a per-frame trace.
    """
    from PIL import Image, ImageDraw

    out_files = []
    n_total = len(png_files)

    for i, png_path in enumerate(png_files):
        img = Image.open(png_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        w, h = img.size
        fs = max(11, int(h / 40))

        def ang(name: str, idx: int = 0) -> float:
            if anim_data:
                return math.degrees(_joint_value(anim_data["joints"], name, i, idx))
            return 0.0

        lines = [
            f"Frame {i+1}/{n_total}",
            "",
            "Joint Angles:",
            f"  R.Hip  flex={ang('RightHip',0):6.1f} abd={ang('RightHip',1):5.1f} rot={ang('RightHip',2):5.1f}",
            f"  R.Knee flex={ang('RightKnee',0):6.1f}",
            f"  R.Ankl flex={ang('RightAnkle',0):6.1f}",
            f"  L.Hip  flex={ang('LeftHip',0):6.1f} abd={ang('LeftHip',1):5.1f} rot={ang('LeftHip',2):5.1f}",
            f"  L.Knee flex={ang('LeftKnee',0):6.1f}",
            f"  L.Ankl flex={ang('LeftAnkle',0):6.1f}",
        ]
        if force_data:
            lines.append("")
            # Single solved configuration, so this must not read as a per-frame curve.
            lines.append("Forces, N (final step):")
            for key, label in _OVERLAY_FORCE_KEYS:
                d = force_data.get(key)
                if d and "magnitude" in d:
                    lines.append(f"  {label}: {d['magnitude']:7.0f}")

        panel_h = len(lines) * (fs + 2) + 20
        draw.rectangle([8, 8, w // 2 - 4, 8 + panel_h], fill=(20, 20, 20, 200))
        y = 16
        for line in lines:
            draw.text((16, y), line, fill=(255, 255, 255, 255))
            y += fs + 2

        img = Image.alpha_composite(img, overlay).convert("RGB")
        out_path = png_path.with_name(png_path.stem + "_overlaid" + png_path.suffix)
        img.save(out_path)
        out_files.append(out_path)

    return out_files


def export_native_anybody_animation(
    model: str,
    output_path: str = "anybody_native_animation.mp4",
    n_frames: int | None = None,
    fps: int = 10,
    anybodycon: str | None = None,
    timeout: int = 600,
    show_values: bool = False,
) -> dict:
    """Export a native AnyBody 3D rendered animation video using AnyBodyCon headless rendering.

    Uses AnyBody's built-in AnyCamRecorder class to render the full 3D model view
    (muscles, bones, segments) without needing AnyBody.exe GUI. Frames are captured
    as PNGs, then combined into the final video.

    How it works:
        1. Creates a writable temp project copy from the model's template
        2. Enables upper body & trunk muscles in BodyModelConfiguration.any
        3. Copies CameraClassTemplate.any from AMMR/Tools into the project
        4. Injects VideoLookAtCamera instance into Main = { } block
        5. Runs: RunApplication -> AnimCam.Reset -> StartTrigger -> InverseDynamics -> StopTrigger
        6. Collects rendered PNG frames from the output directory
        7. Optionally overlays joint angles & force data on each frame
        8. Converts frames to GIF (Pillow) or MP4 (imageio/ffmpeg)

    AnyCamRecorder key parameters:
        - UP_DIRECTION = y: Y-axis is vertical
        - _CLEAN_UP_IMAGES = 0: keeps PNG frames (required for post-processing)
        - CameraDirection: vector FROM LookAtPoint TO camera position
        - CameraFieldOfView: vertical FOV in meters at LookAtPoint
        - CameraDistance: distance from LookAtPoint to camera

    Verified camera configs:
        - GaitVaughan: Distance=6, LookAt={0,0.6,0}, FOV=5, Dir={1,0.2,0}
        - Human Standing: Distance=4, LookAt={0,0.8,0}, FOV=3, Dir={1,0.3,0}

    Args:
        model: Path to AnyBody .main.any file
        output_path: Output video file path (.mp4 or .gif)
        n_frames: Number of simulation steps (None = use model's nStep)
        fps: Frames per second in output video
        anybodycon: Path to AnyBodyCon.exe
        timeout: Timeout in seconds
        show_values: Overlay joint angles and end-state reaction forces on each frame.
            Off by default: it costs three extra AnyBodyCon runs (per-step angle dump,
            segment dimensions, full-chain forces) on top of the recording run.
    """
    import re

    model_path = Path(resolve_model(model))

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not CAMERA_CLASS_TEMPLATE_PATH.exists():
        raise AnyBodyRunnerError(f"CameraClassTemplate not found: {CAMERA_CLASS_TEMPLATE_PATH}")

    # Determine template name for proper project creation
    _template_name = None
    if str(model_path.parent).startswith(str(DEFAULT_GAIT_VAUGHAN_MAIN.parent)):
        _template_name = "gait-vaughan"
    elif str(model_path).startswith(str(DEFAULT_HUMAN_STANDING)):
        _template_name = "human-standing"

    # Create temp project directory using proper project creation
    tmp_root = Path(tempfile.mkdtemp())
    proj_dir = tmp_root / "video_project"

    try:
        if _template_name:
            created = create_project_from_named_template(
                destination=str(proj_dir),
                template_name=_template_name,
                overwrite=True,
            )
            main_file = Path(created["default_main"])
        else:
            # Fall back to manual copy for unknown templates
            proj_dir.mkdir(parents=True, exist_ok=True)
            template_dir = model_path.parent
            for src in template_dir.rglob("*"):
                if src.is_file():
                    rel = src.relative_to(template_dir)
                    dst = proj_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            # Write libdef with the absolute AMMR path instead of copying a broken relative include
            libdef_path = proj_dir.parent / "libdef.any"
            libdef_path.write_text(f'#include "{AMMR_DIR}\\libdef.any"\n', encoding="utf-8")
            main_file = proj_dir / model_path.name
            if not main_file.exists():
                main_file = next(proj_dir.glob("*.main.any"), None)
            if not main_file:
                raise AnyBodyRunnerError(f"No .main.any file in template: {template_dir}")

        # Enable upper body and trunk muscles for full-body animation
        bm_config_path = proj_dir / "Model" / "BodyModelConfiguration.any"
        if bm_config_path.exists():
            bm_text = bm_config_path.read_text(encoding="utf-8")
            bm_text = bm_text.replace("#define BM_ARM_LEFT OFF", "#define BM_ARM_LEFT ON")
            bm_text = bm_text.replace("#define BM_ARM_RIGHT OFF", "#define BM_ARM_RIGHT ON")
            bm_text = bm_text.replace("#define BM_TRUNK_MUSCLES OFF", "#define BM_TRUNK_MUSCLES _MUSCLES_SIMPLE_")
            bm_config_path.write_text(bm_text, encoding="utf-8")

        # Read main file
        main_text = main_file.read_text(encoding="utf-8")

        # Set nStep if specified
        if n_frames is not None:
            main_text = re.sub(r"nStep\s*=\s*\d+;", f"nStep = {n_frames - 1};", main_text)

        # Add DefaultMannequinDrivers and overdeterminate solver for arm DOF support
        main_text = main_text.replace(
            'AnyFolder &BodyModel = .HumanModel.BodyModel;',
            'AnyFolder &BodyModel = .HumanModel.BodyModel;\n'
            '     AnyFolder &DefaultMannequinDrivers = .HumanModel.DefaultMannequinDrivers;'
        )
        main_text = main_text.replace(
            'Gravity = {0.001, 0.001,-9.81};',
            'Gravity = {0.001, 0.001,-9.81};\n'
            '    Kinematics.SolverType = KinSolOverDeterminate;\n'
            '    InitialConditions.SolverType = Kinematics.SolverType;'
        )

        # Determine file separators for output path
        out_str = str(out_path.resolve()).replace("\\", "/")

        # Inject camera via local CameraClassTemplate copy
        if CAMERA_CLASS_TEMPLATE_PATH.exists():
            camera_dst = proj_dir / "CameraClassTemplate.any"
            ct_text = CAMERA_CLASS_TEMPLATE_PATH.read_text(encoding="utf-8")
            # Remove Preview section only (keep OPERATION_DISPLAY_PRIORITY macro - AMMR also defines it
            # but the guard #ifndef prevents redefinition)
            ct_lines = ct_text.splitlines()
            preview_start = -1
            preview_end = -1
            for i, line in enumerate(ct_lines):
                if '/// Operation to show a preview' in line:
                    preview_start = i
                    depth = 0
                    seen_open = False
                    for j in range(i, len(ct_lines)):
                        co = ct_lines[j].count('{')
                        cc = ct_lines[j].count('}')
                        depth += co - cc
                        if co > 0:
                            seen_open = True
                        if depth <= 0 and seen_open:
                            preview_end = j
                            break
                    break
            if preview_start >= 0 and preview_end > preview_start:
                ct_lines[preview_start] = '  /* Preview removed for headless rendering */'
                for k in range(preview_start + 1, preview_end + 1):
                    ct_lines[k] = ''
            ct_text = '\n'.join(ct_lines)
            camera_dst.write_text(ct_text, encoding="utf-8")

        # Remove any AMMR path CameraClassTemplate include
        main_text = main_text.replace('#include "<ANYBODY_PATH_MODELUTILS>/Video/CameraClassTemplate.any"\n', '')
        # Add local CameraClassTemplate include right before Main = { (AFTER libdef, so AMMR
        # Helper.ClassTemplates.any defines OPERATION_DISPLAY_PRIORITY first — otherwise the
        # local copy's #ifndef guard causes double-definition error in Helper.ClassTemplates)
        if '#include "CameraClassTemplate.any"' not in main_text:
            main_text = main_text.replace('Main = {', '#include "CameraClassTemplate.any"\nMain = {')

        # GaitVaughan's Mannequin.any has arm vars only in PostureVel, but the arm model's
        # InitialPositions.any reads from Posture.Right (via BodyModel.Right.JointPos).
        # Add arm vars to Posture.Right/Left so the arm model can find them.
        mann_path = proj_dir / "Model" / "Mannequin.any"
        if mann_path.exists():
            mann_text = mann_path.read_text(encoding="utf-8")
            arm_block = '''
      AnyVar SternoClavicularProtraction=0;
      AnyVar SternoClavicularElevation=0;
      AnyVar SternoClavicularAxialRotation=0;
      AnyVar GlenohumeralFlexion =0;
      AnyVar GlenohumeralAbduction = 0;
      AnyVar GlenohumeralExternalRotation = 0;
      AnyVar ElbowFlexion = 0.0;
      AnyVar ElbowPronation = 0.0;
      AnyVar WristFlexion =0;
      AnyVar WristAbduction =0;
'''
            # Add arm vars to Posture.Right (before leg vars)
            mann_text = mann_text.replace(
                'AnyVar HipFlexion = 18;',
                arm_block + '      AnyVar HipFlexion = 18;'
            )
            # Add arm refs to Posture.Left
            arm_left_block = '''
      AnyVar SternoClavicularProtraction=.Right.SternoClavicularProtraction;
      AnyVar SternoClavicularElevation=.Right.SternoClavicularElevation;
      AnyVar SternoClavicularAxialRotation=.Right.SternoClavicularAxialRotation;
      AnyVar GlenohumeralFlexion = .Right.GlenohumeralFlexion;
      AnyVar GlenohumeralAbduction =.Right.GlenohumeralAbduction;
      AnyVar GlenohumeralExternalRotation =.Right.GlenohumeralExternalRotation;
      AnyVar ElbowFlexion = .Right.ElbowFlexion;
      AnyVar ElbowPronation = .Right.ElbowPronation;
      AnyVar WristFlexion =.Right.WristFlexion;
      AnyVar WristAbduction =.Right.WristAbduction;
'''
            mann_text = mann_text.replace(
                'AnyVar HipFlexion = -20;',
                arm_left_block + '      AnyVar HipFlexion = -20;'
            )
            mann_path.write_text(mann_text, encoding="utf-8")

        # AnyCamRecorder camera configuration
        # ---------------------------------------------------------------
        # AnyBody GaitVaughan coordinate system:
        #   - Person walks along -Z axis (forward)
        #   - X axis = lateral (side to side)
        #   - Y axis = small vertical offset (NOT the body's "up" direction)
        #
        # The body's long axis (head-to-foot) is along Z, NOT Y.
        # This means from the X direction (side view), the body appears
        # horizontal — this is the default AnyBody GUI behavior.
        #
        # CameraDirection = direction FROM LookAtPoint TO camera position.
        # CameraFieldOfView = vertical FOV in meters at LookAtPoint.
        #
        # Verified working parameters for GaitVaughan (side view, HD):
        #   Distance=3, LookAt={0,0.6,0.4}, FOV=2, Dir={1,0,0}
        #   -> 1920x1080, close-up, full skeleton + muscles visible
        #
        # For Human Standing / quasi-static:
        #   Distance=4, LookAt={0,0.8,0}, FOV=3, Dir={1,0.3,0}
        #
        # Known limitations:
        #   - Arms require complex Mannequin variable setup (GlenohumeralFlexion etc.)
        #     and often cause "Unresolved object" errors in InitialPositions.any
        #   - Body appears horizontal in side view (coordinate system limitation)
        #   - Front view (Dir={0,0,1}) may not capture frames in headless mode
        #
        # Key: _CLEAN_UP_IMAGES=0 keeps PNG frames for post-processing.
        # Without it, AnyCamRecorder deletes frames after ConvertVideo.
        # ---------------------------------------------------------------
        camera_block = f'''
Main = {{
  VideoLookAtCamera AnimCam (UP_DIRECTION = y, CREATE_GIF = 0, FFMPEG_ON_PATH = 0, _CLEAN_UP_IMAGES = 0) = {{
        CameraDistance = 3;
        CameraLookAtPoint = {{0, 0.6, 0.4}};
        CameraFieldOfView = 2;
        CameraDirection = {{1, 0, 0}};
       BackgroundColor = {{0.95, 0.95, 0.95}};
       VideoInputFrameRate = {fps};
       VideoResolution = {{1920, 1080}};
       VideoOutputPath = "{out_str}_frames";
       Analysis = {{
           AnyOperation &ref = Main.Study.InverseDynamics;
       }};
  }};

'''
        # Replace the original Main = { with the camera block (camera INSIDE Main per class template docs)
        main_text = re.sub(r'^Main\s*=\s*\{', camera_block, main_text, flags=re.MULTILINE)
        main_file.write_text(main_text, encoding="utf-8")

        # Create output frames directory
        frames_dir = Path(f"{out_str}_frames")
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Run the model and record frames using individual operations (avoids ffmpeg dependency in Create_Video)
        result = run_macro(
            model=str(main_file),
            commands=[
                "operation Main.RunApplication",
                "run",
                "operation Main.AnimCam.Operations.Reset",
                "run",
                "operation Main.AnimCam.Operations.StartTrigger",
                "run",
                "operation Main.Study.InverseDynamics",
                "run",
                "operation Main.AnimCam.Operations.StopTrigger",
                "run",
            ],
            anybodycon=anybodycon,
            timeout=timeout,
        )

        if not result.ok:
            raise AnyBodyRunnerError(
                f"Model run failed.\nSTDOUT[:2000]:\n{result.stdout[:2000]}\nSTDERR[:1000]:\n{result.stderr[:1000]}"
            )
        # Do not print from MCP stdio tools; stdout is reserved for JSON-RPC frames.
        logger.debug("Camera recording stdout[:2000]:\n%s", result.stdout[:2000])

        # Collect captured frames
        png_files = sorted(frames_dir.glob("*.png"))
        if not png_files:
            png_files = sorted(frames_dir.parent.glob("*.png"))
        if not png_files:
            png_files = sorted(proj_dir.glob("*.png"))

        if not png_files:
            raise AnyBodyRunnerError("No frame PNGs were captured by AnyCamRecorder")

        png_files = sorted(png_files)

        # Extract per-step data and overlay on frames
        anim_data = None
        force_data = None
        if show_values:
            try:
                anim_data = extract_animation_data(
                    model=str(model_path), n_frames=n_frames,
                    anybodycon=anybodycon, timeout=timeout,
                )
            except Exception as exc:
                logger.debug("Animation overlay data extraction failed: %s", exc)
            try:
                force_data = summarize_full_chain_joint_forces(
                    model=str(model_path), anybodycon=anybodycon, timeout=timeout,
                )
            except Exception as exc:
                logger.debug("Force overlay data extraction failed: %s", exc)
            png_files = _overlay_values_on_frames(png_files, anim_data, force_data)

        # Convert frames to video
        out_ext = out_path.suffix.lower()
        if out_ext == ".gif":
            from PIL import Image
            frames_img = [Image.open(str(f)) for f in png_files]
            frames_img[0].save(
                str(out_path),
                format="GIF",
                append_images=frames_img[1:],
                save_all=True,
                duration=1000 // fps,
                loop=0,
            )
        elif out_ext == ".mp4":
            import imageio
            writer = imageio.get_writer(str(out_path), fps=fps)
            for f in png_files:
                writer.append_data(imageio.imread(str(f)))
            writer.close()
        else:
            raise AnyBodyRunnerError(f"Unsupported output format: {out_ext}")

        return {
            "animation_file": str(out_path),
            "n_frames": len(png_files),
            "fps": fps,
            "format": out_ext.lstrip("."),
            "rendering_engine": "native_anybody",
            "show_values": show_values,
        }

    except Exception:
        raise
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run AnyBodyCon macros from Python")
    parser.add_argument("--model", help="Path to .any main file")
    parser.add_argument("--anybodycon", help="Path to AnyBodyCon.exe")
    parser.add_argument("--workdir", help="Working directory for execution")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--default-standing", action="store_true", help="Run the built-in Human Standing example")
    parser.add_argument("--command", action="append", default=[], help="Macro command to append before exit")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    if args.default_standing:
        result = run_default_standing_model(anybodycon=args.anybodycon, timeout=args.timeout)
    else:
        result = run_macro(
            model=args.model,
            commands=args.command,
            anybodycon=args.anybodycon,
            workdir=args.workdir,
            timeout=args.timeout,
        )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    else:
        print(result.stdout)
        if result.stderr:
            print(result.stderr)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
