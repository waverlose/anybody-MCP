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
        "object": object_name,
        "force_vectors": value,
        "primary_vector": primary,
        "secondary_vector": secondary,
        "primary_magnitude": vector_magnitude(primary),
        "secondary_magnitude": vector_magnitude(secondary),
    }


def summarize_bilateral_knee_reaction_forces(model: str, anybodycon: str | None = None, timeout: int = 300) -> dict:
    right = summarize_knee_reaction_force(model=model, side="Right", anybodycon=anybodycon, timeout=timeout)
    left = summarize_knee_reaction_force(model=model, side="Left", anybodycon=anybodycon, timeout=timeout)
    return {
        "right": right,
        "left": left,
        "primary_magnitude_difference": right["primary_magnitude"] - left["primary_magnitude"],
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
