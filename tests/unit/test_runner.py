"""Unit tests for the AnyBody automation layer.

Covers the logic that decides whether a result is correct — parsing, cache
invalidation, sweep bookkeeping, retry and resume behaviour. Nothing here starts
AnyBodyCon; every solver call is stubbed.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from src import anybody_runner as runner
from src.anybody_runner import AnyBodyRunnerError, RunResult


# ─────────────────────────────────────────────────────────────
# Synthetic AnyBodyCon output
# ─────────────────────────────────────────────────────────────

L5S1_FORCE = (92.1, 577.06, 0.0002)
LEG_JOINT_FORCES = {
    ("Right", "Hip"): (70.0, 700.0, 0.0),
    ("Left", "Hip"): (70.0, 700.0, 0.0),
    ("Right", "Knee"): (92.18, 1052.02, -74.42),
    ("Left", "Knee"): (92.18, 1052.02, -74.42),
    ("Right", "Ankle"): (10.0, 800.0, 5.0),
    ("Left", "Ankle"): (10.0, 800.0, 5.0),
}


def _vec(values) -> str:
    return "{" + ", ".join(str(v) for v in values) + "}"


def make_full_chain_stdout(completed: bool = True) -> str:
    """Console output shaped like a real AnyBodyCon run.

    RefFrameOutput.F prints as a nested vector ({{x, y, z}}); the L5/S1 Fout
    prints flat ({x, y, z}). The parsers depend on that difference.
    """
    lines = []
    if completed:
        lines.append("Loaded successfully.")
    lines.append(
        "Main.HumanModel.BodyModel.Trunk.JointsLumbar.L5SacrumJnt.Constraints."
        f"Reaction.Fout = {_vec(L5S1_FORCE)};"
    )
    for (side, joint), vec in LEG_JOINT_FORCES.items():
        lines.append(
            f"Main.HumanModel.BodyModel.{side}.Leg.Jnt.{joint}.Constraints."
            f"Reaction.RefFrameOutput.F = {_vec([_vec(vec)])};"
        )
    if completed:
        lines.append("Inverse dynamic analysis completed")
    return "\n".join(lines) + "\n"


def make_run_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> RunResult:
    return RunResult(
        command=["AnyBodyCon.exe", "--macro=tmp.anymcr", "--noninteractive"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        macro_commands=["operation Main.RunApplication", "run"],
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """The full-chain cache is module state; never let it leak between tests."""
    runner.clear_result_cache()
    yield
    runner.clear_result_cache()


@pytest.fixture
def human_project(tmp_path: Path) -> Path:
    """A minimal on-disk project: enough for fingerprints and file edits."""
    project = tmp_path / "proj"
    (project / "Model").mkdir(parents=True)
    (project / "Human Standing.main.any").write_text("Main = {\n  nStep = 11;\n};\n", encoding="utf-8")
    (project / "Model" / "Mannequin.any").write_text(
        "PelvisPosX=0.0502;\nKneeFlexion = 4.0;\n", encoding="utf-8"
    )
    return project / "Human Standing.main.any"


@pytest.fixture
def stub_solver(monkeypatch):
    """Replace run_macro with a controllable stub; returns the call recorder."""
    calls = []

    def _stub(model=None, commands=None, **kwargs):
        calls.append({"model": model, "commands": list(commands or [])})
        return make_run_result(make_full_chain_stdout())

    monkeypatch.setattr(runner, "run_macro", _stub)
    return calls


# ─────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────


def test_truncate_leaves_short_text_alone():
    assert runner._truncate("hello", 100) == "hello"


def test_truncate_keeps_both_ends_and_reports_omission():
    text = "A" * 5000 + "B" * 5000
    out = runner._truncate(text, 100)
    assert out.startswith("A")
    assert out.endswith("B")
    assert "chars omitted" in out
    assert len(out) < len(text)


def test_truncate_limit_zero_is_passthrough():
    text = "x" * 100
    assert runner._truncate(text, 0) == text


def test_stats_empty_is_explicit_not_zero():
    assert runner._stats([]) == {"n": 0, "mean": None, "std": None, "min": None, "max": None}


def test_stats_ignores_none_entries():
    stats = runner._stats([1.0, None, 3.0])
    assert stats["n"] == 2
    assert stats["mean"] == 2.0


def test_stats_mean_std_min_max():
    stats = runner._stats([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert stats["n"] == 8
    assert stats["mean"] == 5.0
    assert stats["std"] == 2.0
    assert stats["min"] == 2.0 and stats["max"] == 9.0


def test_symmetry_percent():
    assert runner._symmetry_percent({"magnitude": 100.0}, {"magnitude": 100.0}) == 100.0
    assert runner._symmetry_percent({"magnitude": 100.0}, {"magnitude": 95.0}) == 95.0
    assert runner._symmetry_percent(None, {"magnitude": 1.0}) is None
    assert runner._symmetry_percent({"magnitude": 0.0}, {"magnitude": 0.0}) is None


def test_case_metric_reads_compression_for_l5s1_and_magnitude_elsewhere():
    entry = {"l5s1": {"compression": 577.0}, "right_knee": {"magnitude": 1052.0}, "left_knee": None}
    assert runner._case_metric(entry, "l5s1") == 577.0
    assert runner._case_metric(entry, "right_knee") == 1052.0
    assert runner._case_metric(entry, "left_knee") is None
    assert runner._case_metric(entry, "missing") is None


def test_reframe_object_builds_expected_path():
    assert runner._reframe_object("Right", "knee") == (
        "Main.HumanModel.BodyModel.Right.Leg.Jnt.Knee.Constraints.Reaction.RefFrameOutput.F"
    )
    with pytest.raises(AnyBodyRunnerError):
        runner._reframe_object("Right", "elbow")


def test_angle_text_lines_reports_frame_and_both_legs():
    joints = {"RightHip": [[0.0, 0.0, 0.0]], "LeftKnee": [[math.radians(30.0)]]}
    lines = runner._angle_text_lines(joints, 0, 3)
    text = "\n".join(lines)
    assert "Frame 1/3" in text
    assert "Right:" in text and "Left:" in text
    assert "30.0" in text


def test_vector_magnitude():
    assert runner.vector_magnitude([3.0, 4.0, 0.0]) == 5.0


# ─────────────────────────────────────────────────────────────
# RunResult reporting
# ─────────────────────────────────────────────────────────────


def test_run_result_truncates_stdout_by_default():
    payload = make_run_result(stdout="Loaded successfully.\n" + "noise\n" * 5000).to_dict()
    assert payload["stdout_truncated"] is True
    assert "chars omitted" in payload["stdout"]


def test_run_result_include_stdout_keeps_everything():
    payload = make_run_result(stdout="Loaded successfully.\n" + "noise\n" * 5000).to_dict(
        include_stdout=True
    )
    assert payload["stdout_truncated"] is False
    assert len(payload["stdout"]) > runner.STDOUT_LIMIT


def test_run_result_reports_macro_commands_instead_of_deleted_temp_path():
    payload = make_run_result(stdout="Loaded successfully.").to_dict()
    assert payload["macro_commands"] == ["operation Main.RunApplication", "run"]
    # The old field pointed at a temp file that run_macro had already deleted.
    assert "macro_file" not in payload


def test_run_result_ok_requires_load_success_and_no_error_text():
    assert make_run_result("Loaded successfully.").ok is True
    assert make_run_result("").ok is False
    assert make_run_result("Loaded successfully.\nERROR: Unresolved object").ok is False
    assert make_run_result("Loaded successfully.", returncode=1).ok is False


def test_run_result_has_errors_scans_stderr_too():
    assert make_run_result("Loaded successfully.", stderr="FATAL ERROR").has_errors is True


# ─────────────────────────────────────────────────────────────
# Macro construction and parsing
# ─────────────────────────────────────────────────────────────


def test_build_macro_wraps_load_and_exit():
    assert runner.build_macro(Path("m.main.any"), ["run"]) == 'load "m.main.any"\nrun\nexit\n'


def test_build_macro_without_commands():
    assert runner.build_macro(Path("m.main.any"), None) == 'load "m.main.any"\nexit\n'


def test_parse_anybody_value_handles_vector_and_scalar():
    assert runner.parse_anybody_value("X = {1.5, -2, 3e-2};") == [1.5, -2, 0.03]
    assert runner.parse_anybody_value("Y = 4.25;") == 4.25


def test_parse_anybody_value_raises_when_absent():
    with pytest.raises(AnyBodyRunnerError):
        runner.parse_anybody_value("nothing printed here")


# ─────────────────────────────────────────────────────────────
# Project file editing
# ─────────────────────────────────────────────────────────────


def test_replace_text_in_file_respects_count(tmp_path: Path):
    path = tmp_path / "a.any"
    path.write_text("x x x", encoding="utf-8")
    result = runner.replace_text_in_file(str(path), "x", "y", count=2)
    assert result == {"file": str(path), "replacements": 2, "found": 3}
    assert path.read_text(encoding="utf-8") == "y y x"


def test_replace_text_in_file_treats_none_as_deletion(tmp_path: Path):
    path = tmp_path / "a.any"
    path.write_text("keep\nremove me\nkeep\n", encoding="utf-8")
    runner.replace_text_in_file(str(path), "remove me\n", None)
    assert path.read_text(encoding="utf-8") == "keep\nkeep\n"


def test_replace_text_in_file_missing_file_raises(tmp_path: Path):
    with pytest.raises(AnyBodyRunnerError):
        runner.replace_text_in_file(str(tmp_path / "nope.any"), "a", "b")


def test_apply_state_description_flags_ignored_and_unapplied_keys(human_project: Path):
    project = human_project.parent
    result = runner.apply_state_description(
        str(project), {"right_knee_flexion": 25.0, "not_a_real_key": 1}
    )
    assert result["ignored_keys"] == ["not_a_real_key"]
    assert result["unapplied_keys"] == []
    assert (project / "Model" / "Mannequin.any").read_text(encoding="utf-8").count(
        "KneeFlexion = 25.0;"
    ) == 1


def test_apply_state_description_reports_unapplied_when_pattern_absent(human_project: Path):
    project = human_project.parent
    (project / "Model" / "Mannequin.any").write_text("nothing relevant\n", encoding="utf-8")
    result = runner.apply_state_description(str(project), {"right_knee_flexion": 10.0})
    assert result["unapplied_keys"] == ["right_knee_flexion"]


def test_apply_posture_preset_modifies_main_and_mannequin(human_project: Path):
    project = human_project.parent
    (project / "Model" / "Mannequin.any").write_text(
        "PelvisPosX=0.0502;\nPelvisThoraxExtension=0;\nKneeFlexion = 4.0;\n",
        encoding="utf-8",
    )
    result = runner.apply_posture_preset(str(project), "forward-lean")
    mannequin = (project / "Model" / "Mannequin.any").read_text(encoding="utf-8")
    assert "PelvisThoraxExtension=18;" in mannequin
    assert "KneeFlexion = 12.0;" in mannequin
    assert result["preset_name"] == "forward-lean"


def test_apply_posture_preset_rejects_unknown_name(human_project: Path):
    with pytest.raises(AnyBodyRunnerError):
        runner.apply_posture_preset(str(human_project.parent), "cartwheel")


def test_scan_project_separates_main_from_support_files(human_project: Path):
    result = runner.scan_project(str(human_project.parent))
    assert result["main_file_count"] == 1
    # any_files is the superset; support_any_files excludes the main files.
    assert result["any_file_count"] == 2
    assert result["support_any_file_count"] == 1
    assert result["main_files"] == [str(human_project)]


# ─────────────────────────────────────────────────────────────
# Task spec validation
# ─────────────────────────────────────────────────────────────


def test_run_task_spec_unknown_workflow_names_the_supported_set():
    with pytest.raises(AnyBodyRunnerError) as excinfo:
        runner.run_task_spec({"workflow": "bogus", "project_dir": "x"})
    assert "bogus" in str(excinfo.value)
    assert "parametric-sweep" in str(excinfo.value)


def test_run_task_spec_missing_keys_lists_them():
    with pytest.raises(AnyBodyRunnerError) as excinfo:
        runner.run_task_spec({"workflow": "parametric-sweep", "project_dir": "x"})
    message = str(excinfo.value)
    assert "trunk_angles" in message and "knee_angles" in message and "loads_kg" in message


def test_run_task_spec_does_not_raise_bare_keyerror():
    with pytest.raises(AnyBodyRunnerError):
        runner.run_task_spec({"workflow": "state", "state": {}})


# ─────────────────────────────────────────────────────────────
# Full-chain parsing and caching
# ─────────────────────────────────────────────────────────────


def test_full_chain_parses_every_joint(human_project: Path, stub_solver):
    out = runner.summarize_full_chain_joint_forces(str(human_project), use_cache=False)

    assert out["success"] is True
    assert out["l5s1"]["compression"] == pytest.approx(577.06)
    assert out["right_knee"]["magnitude"] == pytest.approx(
        runner.vector_magnitude(list(LEG_JOINT_FORCES[("Right", "Knee")]))
    )
    assert out["right_knee"]["secondary"] == [-v for v in out["right_knee"]["primary"]]
    for key in ("right_hip", "left_hip", "right_knee", "left_knee", "right_ankle", "left_ankle"):
        assert out[key] is not None
    assert out["cached"] is False


def test_full_chain_second_call_is_cached_and_skips_solver(human_project: Path, stub_solver):
    first = runner.summarize_full_chain_joint_forces(str(human_project))
    second = runner.summarize_full_chain_joint_forces(str(human_project))

    assert len(stub_solver) == 1, "second query should not re-run the solver"
    assert first["cached"] is False
    assert second["cached"] is True
    # A cache hit cannot replay the console, so it must say so rather than lie.
    assert second["stdout"] == ""
    assert second["right_knee"]["magnitude"] == first["right_knee"]["magnitude"]


def test_cache_invalidated_when_support_file_changes(human_project: Path, stub_solver):
    runner.summarize_full_chain_joint_forces(str(human_project))
    assert len(stub_solver) == 1

    # Editing Mannequin.any leaves the main file's mtime untouched.
    mannequin = human_project.parent / "Model" / "Mannequin.any"
    mannequin.write_text("KneeFlexion = 40.0;\n", encoding="utf-8")

    result = runner.summarize_full_chain_joint_forces(str(human_project))
    assert len(stub_solver) == 2, "an edited support file must invalidate the cache"
    assert result["cached"] is False


def test_failed_run_is_not_cached(human_project: Path, monkeypatch):
    calls = []

    def _stub(model=None, commands=None, **kwargs):
        calls.append(1)
        return make_run_result("Loaded successfully.\n")  # no completion line

    monkeypatch.setattr(runner, "run_macro", _stub)
    runner.summarize_full_chain_joint_forces(str(human_project))
    runner.summarize_full_chain_joint_forces(str(human_project))
    assert len(calls) == 2


def test_use_cache_false_always_solves(human_project: Path, stub_solver):
    runner.summarize_full_chain_joint_forces(str(human_project), use_cache=False)
    runner.summarize_full_chain_joint_forces(str(human_project), use_cache=False)
    assert len(stub_solver) == 2


def test_clear_result_cache_reports_count(human_project: Path, stub_solver):
    runner.summarize_full_chain_joint_forces(str(human_project))
    assert runner.clear_result_cache() == {"cleared_entries": 1}
    assert runner.clear_result_cache() == {"cleared_entries": 0}


# ─────────────────────────────────────────────────────────────
# Bilateral queries
# ─────────────────────────────────────────────────────────────


def test_bilateral_uses_one_solver_run_not_two(human_project: Path, stub_solver):
    result = runner.summarize_bilateral_knee_reaction_forces(str(human_project))

    assert len(stub_solver) == 1, "one full-chain solve must cover both sides"
    assert result["solver_runs"] == 1
    assert result["fallback_sides"] == []
    assert result["right"]["primary_magnitude"] == result["left"]["primary_magnitude"]
    assert result["primary_magnitude_difference"] == pytest.approx(0.0)
    assert result["symmetry_percent"] == 100.0


def test_bilateral_falls_back_only_for_the_unparsed_side(human_project: Path, monkeypatch):
    parsed = {
        "success": True,
        "l5s1": {"vector": [1.0, 2.0, 3.0], "compression": 2.0, "magnitude": 3.7},
        "right_knee": {"primary": [10.0, 500.0, 0.0], "secondary": [-10.0, -500.0, 0.0], "magnitude": 500.1},
        # left_knee deliberately missing
    }
    monkeypatch.setattr(
        runner, "summarize_full_chain_joint_forces", lambda **kwargs: dict(parsed)
    )
    fallback_calls = []

    def _fallback(model, joint, side="Right", anybodycon=None, timeout=300):
        fallback_calls.append((joint, side))
        return {
            "side": side.capitalize(), "joint": joint, "object": "o",
            "force_vectors": [[1.0, 500.0, 0.0], [-1.0, -500.0, 0.0]],
            "primary_vector": [1.0, 500.0, 0.0], "secondary_vector": [-1.0, -500.0, 0.0],
            "primary_magnitude": 500.0, "secondary_magnitude": 500.0,
        }

    monkeypatch.setattr(runner, "summarize_joint_reaction_force", _fallback)
    result = runner.summarize_bilateral_joint_forces(str(human_project), "knee")

    assert fallback_calls == [("knee", "Left")]
    assert result["fallback_sides"] == ["left"]
    assert result["solver_runs"] == 2


def test_single_side_query_builds_the_right_object_path(human_project: Path, monkeypatch):
    seen = {}

    def _extract(model, object_name, operation, anybodycon=None, timeout=300):
        seen["object"] = object_name
        return {"value": [[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]]}

    monkeypatch.setattr(runner, "extract_object_value_after_operation", _extract)
    result = runner.summarize_joint_reaction_force(str(human_project), "ankle", "Left")

    assert seen["object"].startswith("Main.HumanModel.BodyModel.Left.Leg.Jnt.Ankle")
    assert result["side"] == "Left"
    assert result["primary_magnitude"] == pytest.approx(math.sqrt(14.0))


# ─────────────────────────────────────────────────────────────
# Sweep: guardrails, resume, retry, outputs
# ─────────────────────────────────────────────────────────────


def _stub_sweep_case_builder(monkeypatch, calls):
    def _build(case_dir, trunk, knee, load, overwrite):
        calls.append(("build", case_dir.name, overwrite))
        case_dir.mkdir(parents=True, exist_ok=True)
        main = case_dir / "Human Standing.main.any"
        main.write_text("Main = {};\n", encoding="utf-8")
        return main

    monkeypatch.setattr(runner, "_build_sweep_case", _build)


def test_sweep_rejects_empty_parameter_lists(tmp_path: Path):
    with pytest.raises(AnyBodyRunnerError) as excinfo:
        runner.run_full_chain_parametric_sweep(str(tmp_path / "p"), [], [0], [0])
    assert "at least one" in str(excinfo.value)


def test_sweep_refuses_to_start_above_max_cases(tmp_path: Path):
    with pytest.raises(AnyBodyRunnerError) as excinfo:
        runner.run_full_chain_parametric_sweep(
            str(tmp_path / "p"),
            trunk_angles=list(range(10)),
            knee_angles=list(range(10)),
            loads_kg=list(range(10)),
            max_cases=100,
        )
    message = str(excinfo.value)
    assert "1000 cases" in message and "100-case limit" in message


def test_sweep_runs_cases_and_writes_outputs(tmp_path: Path, monkeypatch, stub_solver):
    calls = []
    _stub_sweep_case_builder(monkeypatch, calls)
    out_dir = tmp_path / "out"

    result = runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"),
        trunk_angles=[0.0],
        knee_angles=[0.0, 15.0],
        loads_kg=[0.0],
        output_dir=str(out_dir),
        write_csv=True,
    )

    summary = result["summary"]
    assert summary["total_cases"] == 2
    assert summary["success_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["cancelled"] is False
    assert summary["knee_symmetry_percent"] == 100.0
    assert Path(summary["results_file"]).exists()
    assert Path(summary["csv_file"]).exists()
    # One checkpoint per case, so an interrupted sweep can resume.
    assert len(list((out_dir / "cases").glob("*.json"))) == 2


def test_sweep_csv_has_one_row_per_case_plus_header(tmp_path: Path, monkeypatch, stub_solver):
    _stub_sweep_case_builder(monkeypatch, [])
    out_dir = tmp_path / "out"
    runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"), [0.0], [0.0, 15.0], [0.0], output_dir=str(out_dir), write_csv=True
    )
    rows = (out_dir / "sweep_results.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3
    assert rows[0].startswith("case_id,trunk_angle,knee_angle,load_kg,success,l5s1_compression")


def test_sweep_resume_reuses_stored_cases_without_solving(tmp_path: Path, monkeypatch, stub_solver):
    calls = []
    _stub_sweep_case_builder(monkeypatch, calls)
    out_dir = tmp_path / "out"

    kwargs = dict(
        project_dir=str(tmp_path / "p"),
        trunk_angles=[0.0],
        knee_angles=[0.0, 15.0],
        loads_kg=[0.0],
        output_dir=str(out_dir),
    )
    runner.run_full_chain_parametric_sweep(**kwargs)
    assert len(stub_solver) == 2

    second = runner.run_full_chain_parametric_sweep(**kwargs)
    assert len(stub_solver) == 2, "a resumed sweep must not re-solve finished cases"
    assert second["summary"]["reused_cases"] == 2


def test_sweep_overwrite_forces_a_rebuild(tmp_path: Path, monkeypatch, stub_solver):
    calls = []
    _stub_sweep_case_builder(monkeypatch, calls)
    out_dir = tmp_path / "out"

    kwargs = dict(
        project_dir=str(tmp_path / "p"),
        trunk_angles=[0.0],
        knee_angles=[0.0],
        loads_kg=[0.0],
        output_dir=str(out_dir),
    )
    runner.run_full_chain_parametric_sweep(**kwargs)
    result = runner.run_full_chain_parametric_sweep(**kwargs, overwrite=True)

    assert len(stub_solver) == 2
    assert result["summary"]["reused_cases"] == 0
    # overwrite=False must be honoured by the case builder, not silently ignored.
    assert [c[2] for c in calls if c[0] == "build"] == [False, True]


def test_sweep_retries_a_failing_case_then_records_the_error(tmp_path: Path, monkeypatch):
    _stub_sweep_case_builder(monkeypatch, [])

    def _always_fail(**kwargs):
        raise AnyBodyRunnerError("solver exploded")

    monkeypatch.setattr(runner, "summarize_full_chain_joint_forces", _always_fail)

    result = runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"), [0.0], [0.0], [0.0], output_dir=str(tmp_path / "out"), retries=2
    )

    entry = result["results"][0]
    assert entry["success"] is False
    assert entry["attempts"] == 3
    assert "solver exploded" in entry["error"]
    assert result["summary"]["failure_count"] == 1


def test_sweep_one_bad_case_does_not_abort_the_rest(tmp_path: Path, monkeypatch):
    _stub_sweep_case_builder(monkeypatch, [])
    seen = []

    def _fail_first(**kwargs):
        seen.append(kwargs["model"])
        if len(seen) == 1:
            raise AnyBodyRunnerError("boom")
        return {"success": True, "l5s1": {"compression": 1.0, "vector": [0, 1, 0], "magnitude": 1.0}, **{
            key: {"primary": [1.0, 2.0, 3.0], "secondary": [-1.0, -2.0, -3.0], "magnitude": 3.74}
            for key in ("right_hip", "left_hip", "right_knee", "left_knee", "right_ankle", "left_ankle")
        }}

    monkeypatch.setattr(runner, "summarize_full_chain_joint_forces", _fail_first)

    result = runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"), [0.0], [0.0, 15.0], [0.0],
        output_dir=str(tmp_path / "out"), retries=0,
    )
    assert result["summary"]["success_count"] == 1
    assert result["summary"]["failure_count"] == 1


def test_sweep_stops_at_cancellation(tmp_path: Path, monkeypatch, stub_solver):
    import threading

    _stub_sweep_case_builder(monkeypatch, [])

    class StopAfterFirst(threading.Event):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def is_set(self):
            self._calls += 1
            return self._calls > 1

    event = StopAfterFirst()
    result = runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"), [0.0], [0.0, 15.0, 30.0], [0.0],
        output_dir=str(tmp_path / "out"), cancel_event=event,
    )
    assert result["summary"]["cancelled"] is True
    assert result["summary"]["attempted_cases"] < 3


def test_sweep_progress_callback_reports_each_case(tmp_path: Path, monkeypatch, stub_solver):
    _stub_sweep_case_builder(monkeypatch, [])
    seen = []

    runner.run_full_chain_parametric_sweep(
        str(tmp_path / "p"), [0.0], [0.0, 15.0], [0.0],
        output_dir=str(tmp_path / "out"),
        progress_callback=lambda done, total, message: seen.append((done, total, message)),
    )
    assert [(d, t) for d, t, _ in seen] == [(1, 2), (2, 2)]


def test_build_sweep_case_reuses_existing_main_unless_overwrite(tmp_path: Path, monkeypatch):
    case_dir = tmp_path / "case_001_T0_K0_L0"
    case_dir.mkdir()
    existing = case_dir / "Human Standing.main.any"
    existing.write_text("Main = {};\n", encoding="utf-8")

    built = runner._build_sweep_case(case_dir, 0.0, 0.0, 0.0, overwrite=False)
    assert built == existing

    def _fail_if_called(**kwargs):
        raise AssertionError("overwrite=True must rebuild from the template")

    monkeypatch.setattr(runner, "create_project_from_named_template", _fail_if_called)
    with pytest.raises(AssertionError):
        runner._build_sweep_case(case_dir, 0.0, 0.0, 0.0, overwrite=True)


# ─────────────────────────────────────────────────────────────
# Animation export (no solver required)
# ─────────────────────────────────────────────────────────────


def test_export_animation_bvh_writes_one_line_per_frame(tmp_path: Path):
    data = {
        "n_frames": 3,
        "joints": {
            "RightHip": [[0.1, 0.2, 0.3]] * 3,
            "RightKnee": [[0.4]] * 3,
            "RightAnkle": [[0.5]] * 3,
            "LeftHip": [[0.1, 0.2, 0.3]] * 3,
            "LeftKnee": [[0.4]] * 3,
            "LeftAnkle": [[0.5]] * 3,
        },
        "segment_lengths": {"ThighLength": 0.4, "ShankLength": 0.4, "FootLength": 0.2},
    }
    target = tmp_path / "out.bvh"
    result = runner.export_animation_bvh(data, str(target))

    text = target.read_text(encoding="utf-8")
    assert result["n_frames"] == 3
    motion = text.split("MOTION")[1].strip().splitlines()
    # "Frames: n", "Frame Time: ...", then one line per frame.
    assert motion[0] == "Frames: 3"
    assert len(motion) == 5
    assert len(motion[2:]) == 3


def test_export_animation_bvh_tolerates_missing_joints(tmp_path: Path):
    data = {"n_frames": 2, "joints": {}, "segment_lengths": {}}
    result = runner.export_animation_bvh(data, str(tmp_path / "out.bvh"))
    assert result["joint_count"] == 0


def test_parse_per_step_arrays_reads_nested_step_vectors():
    dump = (
        "Main.Study.Output.Model.BodyModel.Right.Leg.JntDOF.Knee.Pos = "
        "{{0.1}, {0.2}, {0.3}};\n"
        "Main.Study.Output.Model.BodyModel.Right.Leg.JntDOF.Hip.Pos = "
        "{{0.0, 0.1, 0.2}, {0.1, 0.2, 0.3}};\n"
    )
    parsed = runner._parse_per_step_arrays(dump)
    assert parsed["RightKnee"] == [[0.1], [0.2], [0.3]]
    assert parsed["RightHip"] == [[0.0, 0.1, 0.2], [0.1, 0.2, 0.3]]


def test_parse_per_step_arrays_ignores_unrelated_output():
    assert runner._parse_per_step_arrays("nothing here") == {}
