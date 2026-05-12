from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.anybody_runner import run_task_spec_file


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run an AnyBody task spec JSON file")
    parser.add_argument("--spec-file", required=True, help="Path to task spec JSON file")
    args = parser.parse_args()

    result = run_task_spec_file(args.spec_file)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
