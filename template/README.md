# AnyBody Project Template

This folder describes the expected project layout for AI-driven AnyBody work.

## Expected layout

- `anybody-project.json`: project metadata for tooling
- `*.main.any`: main entry model file
- `Model/`: editable model fragments and parameters
- `results/`: optional output directory for exported logs and reports

## Workflow

1. Create a writable project copy with the MCP `create_project` tool.
2. Inspect the project with `inspect_project`.
3. Read and edit `.any` files with `read_project_file` and `replace_in_project_file`.
4. Run the main model with `run_model`.
5. Extract values with `print_model_object`.
