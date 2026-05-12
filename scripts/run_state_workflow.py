from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.anybody_runner import no_gui_state_workflow


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a structured no-GUI AnyBody state workflow")
    parser.add_argument("--project-dir", required=True, help="Target project directory")
    parser.add_argument("--state-json", help="Structured state as a JSON string")
    parser.add_argument("--state-file", help="Path to a JSON file containing the structured state")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing project directory")
    args = parser.parse_args()

    if not args.state_json and not args.state_file:
        parser.error("one of --state-json or --state-file is required")
    if args.state_file:
        state = json.loads(Path(args.state_file).read_text(encoding="utf-8"))
    else:
        state = json.loads(args.state_json)
    result = no_gui_state_workflow(project_dir=args.project_dir, state=state, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
