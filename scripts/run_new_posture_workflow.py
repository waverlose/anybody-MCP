from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.anybody_runner import no_gui_new_posture_workflow


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a no-GUI new-posture AnyBody workflow")
    parser.add_argument("--project-dir", required=True, help="Target project directory")
    parser.add_argument("--preset", required=True, help="Posture preset name")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing project directory")
    args = parser.parse_args()

    result = no_gui_new_posture_workflow(project_dir=args.project_dir, preset_name=args.preset, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
