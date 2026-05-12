from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ANYBODYCON = Path(r"C:\Program Files\AnyBody Technology\AnyBody.7.3\AnyBodyCon.exe")
DEFAULT_HUMAN_STANDING = Path(
    r"C:\Program Files\AnyBody Technology\AnyBody.7.3\AMMR\Tools\Templates\Human Standing\Human Standing.main.any"
)
DEFAULT_HUMAN_TEMPLATE_DIR = DEFAULT_HUMAN_STANDING.parent
DEFAULT_GAIT_VAUGHAN_MAIN = Path(
    r"C:\Program Files\AnyBody Technology\AnyBody.7.3\AMMR\Application\Validation\GaitVaughan\GaitVaughan.main.any"
)
DEFAULT_GAIT_VAUGHAN_TEMPLATE_DIR = DEFAULT_GAIT_VAUGHAN_MAIN.parent
DEFAULT_AMMR_LIBDEF = Path(r"C:\Program Files\AnyBody Technology\AnyBody.7.3\AMMR\libdef.any")
DEFAULT_VALIDATION_LIBDEF_INCLUDE = '#include "<ANYBODY_PATH_INSTALLDIR>/AMMR/Application/Validation/libdef.any"\n'
DEFAULT_AMMR_LIBDEF_INCLUDE = '#include "<ANYBODY_PATH_INSTALLDIR>/AMMR/libdef.any"\n'

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


@dataclass
class RunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    macro_file: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and "Loaded successfully." in self.stdout

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "macro_file": self.macro_file,
            "ok": self.ok,
        }


def resolve_anybodycon(path: str | None = None) -> Path:
    candidate = Path(path) if path else DEFAULT_ANYBODYCON
    if not candidate.exists():
        raise AnyBodyRunnerError(f"AnyBodyCon not found: {candidate}")
    return candidate


def resolve_model(path: str | None = None) -> Path:
    candidate = Path(path) if path else DEFAULT_HUMAN_STANDING
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
) -> RunResult:
    anybodycon_path = resolve_anybodycon(anybodycon)
    model_path = resolve_model(model)
    cwd = Path(workdir) if workdir else model_path.parent
    if not cwd.exists():
        raise AnyBodyRunnerError(f"Working directory not found: {cwd}")

    macro_content = build_macro(model_path, commands)
    with tempfile.NamedTemporaryFile("w", suffix=".anymcr", delete=False, encoding="utf-8") as handle:
        handle.write(macro_content)
        macro_path = Path(handle.name)

    command = [str(anybodycon_path), f'--macro={macro_path}', "--noninteractive"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        pass

    return RunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        macro_file=str(macro_path),
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


def replace_text_in_file(file_path: str, old_text: str, new_text: str, count: int = -1) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise AnyBodyRunnerError(f"File not found: {path}")
    original = path.read_text(encoding="utf-8")
    available = original.count(old_text)
    replacements = available if count < 0 else min(available, count)
    updated = original.replace(old_text, new_text) if count < 0 else original.replace(old_text, new_text, count)
    path.write_text(updated, encoding="utf-8")
    return {
        "file": str(path),
        "replacements": replacements,
    }


def apply_state_description(project_dir: str, state: dict) -> dict:
    project_path = Path(project_dir)
    replacements = []
    for key, value in state.items():
        mapping = STATE_TEXT_MAP.get(key)
        if not mapping:
            continue
        relative_file, old_text, new_template = mapping
        target_file = project_path / relative_file
        rendered = new_template.format(value=value)
        result = replace_text_in_file(str(target_file), old_text, rendered, count=1)
        replacements.append({"key": key, **result})
    main_file = next(project_path.glob("*.main.any"), None)
    return {
        "project_dir": project_dir,
        "state": state,
        "replacements": replacements,
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


def summarize_knee_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    side_name = side.capitalize()
    object_name = f"Main.HumanModel.BodyModel.{side_name}.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F"
    extracted = extract_object_value_after_operation(
        model=model,
        object_name=object_name,
        operation="Main.RunApplication",
        anybodycon=anybodycon,
        timeout=timeout,
    )
    value = extracted["value"]
    primary = value[0]
    secondary = value[1]
    return {
        "side": side_name,
        "joint": "knee",
        "object": object_name,
        "force_vectors": value,
        "primary_vector": primary,
        "secondary_vector": secondary,
        "primary_magnitude": vector_magnitude(primary),
        "secondary_magnitude": vector_magnitude(secondary),
    }


def summarize_hip_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    side_name = side.capitalize()
    object_name = f"Main.HumanModel.BodyModel.{side_name}.Leg.Jnt.Hip.Constraints.Reaction.RefFrameOutput.F"
    extracted = extract_object_value_after_operation(
        model=model, object_name=object_name, operation="Main.RunApplication",
        anybodycon=anybodycon, timeout=timeout,
    )
    value = extracted["value"]
    return {
        "side": side_name, "joint": "hip", "object": object_name,
        "force_vectors": value,
        "primary_vector": value[0], "secondary_vector": value[1],
        "primary_magnitude": vector_magnitude(value[0]),
        "secondary_magnitude": vector_magnitude(value[1]),
    }


def summarize_ankle_reaction_force(model: str, side: str = "Right", anybodycon: str | None = None, timeout: int = 300) -> dict:
    side_name = side.capitalize()
    object_name = f"Main.HumanModel.BodyModel.{side_name}.Leg.Jnt.Ankle.Constraints.Reaction.RefFrameOutput.F"
    extracted = extract_object_value_after_operation(
        model=model, object_name=object_name, operation="Main.RunApplication",
        anybodycon=anybodycon, timeout=timeout,
    )
    value = extracted["value"]
    return {
        "side": side_name, "joint": "ankle", "object": object_name,
        "force_vectors": value,
        "primary_vector": value[0], "secondary_vector": value[1],
        "primary_magnitude": vector_magnitude(value[0]),
        "secondary_magnitude": vector_magnitude(value[1]),
    }


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


def summarize_bilateral_knee_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    right = summarize_knee_reaction_force(model=model, side="Right", anybodycon=anybodycon, timeout=timeout)
    left = summarize_knee_reaction_force(model=model, side="Left", anybodycon=anybodycon, timeout=timeout)
    return {
        "right": right,
        "left": left,
        "primary_magnitude_difference": right["primary_magnitude"] - left["primary_magnitude"],
    }


def summarize_bilateral_hip_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    right = summarize_hip_reaction_force(model=model, side="Right", anybodycon=anybodycon, timeout=timeout)
    left = summarize_hip_reaction_force(model=model, side="Left", anybodycon=anybodycon, timeout=timeout)
    return {"right": right, "left": left}


def summarize_bilateral_ankle_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    right = summarize_ankle_reaction_force(model=model, side="Right", anybodycon=anybodycon, timeout=timeout)
    left = summarize_ankle_reaction_force(model=model, side="Left", anybodycon=anybodycon, timeout=timeout)
    return {"right": right, "left": left}


def summarize_full_chain_joint_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    """Extract all 4 joints (L5/S1, hip, knee, ankle) in a single model run."""
    result = run_macro(model=model, commands=[
        "operation Main.RunApplication", "run",
        "print Main.HumanModel.BodyModel.Trunk.JointsLumbar.L5SacrumJnt.Constraints.Reaction.Fout",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Hip.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Hip.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Right.Leg.Jnt.Ankle.Constraints.Reaction.RefFrameOutput.F",
        "print Main.HumanModel.BodyModel.Left.Leg.Jnt.Ankle.Constraints.Reaction.RefFrameOutput.F",
    ], anybodycon=anybodycon, timeout=timeout)
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
            except: pass
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
            except: pass
        return None

    return {
        "success": success,
        "l5s1": _parse_l5s1(),
        "right_hip": _parse_reframe("Right", "Hip"),
        "left_hip": _parse_reframe("Left", "Hip"),
        "right_knee": _parse_reframe("Right", "Knee"),
        "left_knee": _parse_reframe("Left", "Knee"),
        "right_ankle": _parse_reframe("Right", "Ankle"),
        "left_ankle": _parse_reframe("Left", "Ankle"),
        "returncode": result.returncode,
        "stdout": stdout,
    }


def no_gui_gait_knee_force_workflow(project_dir: str, overwrite: bool = False, timeout: int = 900) -> dict:
    created = create_project_from_named_template(destination=project_dir, template_name="gait-vaughan", overwrite=overwrite)
    main_file = created["default_main"]
    summary = summarize_bilateral_knee_reaction_forces(main_file, timeout=timeout)
    report_path = Path(project_dir) / "Output" / "knee-force-summary.json"
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


def run_full_chain_parametric_sweep(
    project_dir: str,
    trunk_angles: list[float],
    knee_angles: list[float],
    loads_kg: list[float],
    overwrite: bool = True,
    timeout: int = 300,
    anybodycon: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Run a parametric sweep across trunk/knee/load combinations, extracting full-chain joint forces."""
    from copy import deepcopy
    base_dir = Path(project_dir)
    out_dir = Path(output_dir) if output_dir else (base_dir.parent / "sweep_results")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    total = len(trunk_angles) * len(knee_angles) * len(loads_kg)
    case_id = 0

    for trunk in trunk_angles:
        for knee in knee_angles:
            for load in loads_kg:
                case_id += 1
                case_dir = base_dir / f"case_{case_id:03d}_T{trunk}_K{knee}_L{load}"
                if case_dir.exists():
                    shutil.rmtree(case_dir)

                created = create_project_from_named_template(
                    destination=str(case_dir), template_name="human-standing", overwrite=True)

                if trunk > 0:
                    pos_x = 0.05 + trunk * 0.002
                    pos_y = 0.97 - trunk * 0.003
                    apply_state_description(str(case_dir), {
                        "pelvis_pos_x": round(pos_x, 4),
                        "pelvis_pos_y": round(pos_y, 4),
                        "thorax_extension": trunk,
                        "right_knee_flexion": knee,
                        "left_knee_flexion": knee,
                    })
                else:
                    apply_state_description(str(case_dir), {
                        "right_knee_flexion": knee,
                        "left_knee_flexion": knee,
                    })

                if load > 0:
                    apply_hand_load(str(case_dir), load)

                main_file = created["default_main"]

                # Reduce steps for faster sweep
                main_text = Path(main_file).read_text(encoding="utf-8")
                main_text = main_text.replace("nStep = 11;", "nStep = 1;")
                Path(main_file).write_text(main_text, encoding="utf-8")

                full_chain = summarize_full_chain_joint_forces(
                    model=str(main_file), anybodycon=anybodycon, timeout=timeout)

                entry = {
                    "case_id": case_id,
                    "trunk_angle": trunk,
                    "knee_angle": knee,
                    "load_kg": load,
                    "success": full_chain["success"],
                    "l5s1": full_chain["l5s1"],
                    "right_hip": full_chain["right_hip"],
                    "left_hip": full_chain["left_hip"],
                    "right_knee": full_chain["right_knee"],
                    "left_knee": full_chain["left_knee"],
                    "right_ankle": full_chain["right_ankle"],
                    "left_ankle": full_chain["left_ankle"],
                }
                results.append(entry)

    # Save results
    json_path = out_dir / "sweep_results.json"
    json_path.write_text(json.dumps(results, ensure_ascii=True, indent=2), encoding="utf-8")

    # Summary statistics
    success_results = [r for r in results if r["success"]]
    summary = {
        "total_cases": total,
        "success_count": len(success_results),
        "output_dir": str(out_dir),
        "results_file": str(json_path),
    }
    if success_results:
        l5s1_vals = [r["l5s1"]["compression"] for r in success_results if r.get("l5s1")]
        summary["avg_l5s1_compression"] = round(sum(l5s1_vals)/len(l5s1_vals), 2) if l5s1_vals else 0
        for joint in ["hip", "knee", "ankle"]:
            right_vals = [r.get(f"right_{joint}", {}).get("magnitude", 0) for r in success_results if r.get(f"right_{joint}")]
            left_vals = [r.get(f"left_{joint}", {}).get("magnitude", 0) for r in success_results if r.get(f"left_{joint}")]
            all_vals = right_vals + left_vals
            summary[f"avg_{joint}"] = round(sum(all_vals)/len(all_vals), 2) if all_vals else 0

    return {
        "summary": summary,
        "results": results,
    }


def run_task_spec(spec: dict) -> dict:
    workflow = spec.get("workflow")
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
            timeout=timeout,
        )
    raise AnyBodyRunnerError(f"Unknown task spec workflow: {workflow}")


def run_task_spec_file(spec_file: str) -> dict:
    spec_path = Path(spec_file)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result = run_task_spec(spec)
    result["task_spec_file"] = str(spec_path)
    return result


def scan_project(project_dir: str) -> dict:
    base = Path(project_dir)
    if not base.exists():
        raise AnyBodyRunnerError(f"Project directory not found: {base}")
    main_files = sorted(str(path) for path in base.rglob("*.main.any"))
    any_files = sorted(str(path) for path in base.rglob("*.any"))
    return {
        "project_dir": str(base),
        "main_files": main_files,
        "any_files": any_files,
        "main_file_count": len(main_files),
        "any_file_count": len(any_files),
    }


def run_and_dump_json(output_path: str, model: str | None = None, commands: Iterable[str] | None = None) -> dict:
    result = run_macro(model=model, commands=commands)
    target = Path(output_path)
    target.write_text(json.dumps(result.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")
    return result.to_dict()


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
