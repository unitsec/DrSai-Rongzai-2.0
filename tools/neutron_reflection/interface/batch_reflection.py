"""Grouped reduction with a metadata preflight before any calculation.

No numerical/algorithm imports at module import time: preflight is read-only.
"""
import json
import math
import os
import re
from pathlib import Path

RUN_PATTERN = re.compile(r"RUN[0-9]{7}")
THRESHOLD = 0.001


def _angle(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("角度必须是以度为单位的数值")
    value = float(value)
    if not math.isfinite(value) or abs(value) >= 90:
        raise ValueError("角度必须是有限数值且绝对值小于90度")
    return value


def prepare_groups(groups, paths):
    from settings_matching import prepare_matched_groups
    return prepare_matched_groups(groups, paths, _prepare_explicit_groups, RUN_PATTERN, _angle, THRESHOLD)


def _prepare_explicit_groups(groups, paths):
    """Resolve only administrator-configured sources; return plans or questions."""
    if not isinstance(groups, list) or not groups:
        raise ValueError("groups 必须是非空列表；分组必须由用户明确指定")
    sources = dict(paths.get("data_sources", {}))
    if paths.get("data_root"):
        sources.setdefault("default", paths["data_root"])
    plans, questions = [], []
    for index, group in enumerate(groups, 1):
        allowed = {"group_name", "runs", "data_source", "data_root", "direct_run", "angles_deg", "confirm_metadata_override"}
        if not isinstance(group, dict) or set(group) - allowed:
            raise ValueError("分组必须是对象且不能包含未知字段")
        name = group.get("group_name", f"group_{index:03d}")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("group_name 必须是1至128字符的名称")
        runs = group.get("runs")
        if (not isinstance(runs, list) or len(runs) < 2
                or any(not isinstance(r, str) or not RUN_PATTERN.fullmatch(r) for r in runs)):
            raise ValueError("每组至少两个RUN，格式为RUN加七位数字")
        if len(set(runs)) != len(runs):
            raise ValueError("同一组内RUN不能重复")
        source = group.get("data_source", "default")
        if not isinstance(source, str) or source not in sources:
            raise ValueError(f"未配置数据源：{source!r}")
        root = Path(sources[source]).expanduser().resolve()
        if "data_root" in group:
            supplied = group["data_root"]
            if not isinstance(supplied, str) or not Path(supplied).is_absolute():
                raise ValueError("data_root 必须是验证工具返回的绝对路径")
            candidate = Path(supplied).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("data_root 超出管理员配置的数据根目录")
            root = candidate
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(f"数据源目录不存在：{source}")
        metadata = {}
        for run in runs:
            # Preserve the same RUN/RUN convention used by the single-group interface.
            with (root / run / run).open(encoding="utf-8") as f:
                document = json.load(f)
            value = document
            for key in ("summary", "json", "devices", "Rz"):
                value = value.get(key) if isinstance(value, dict) else None
            metadata[run] = None if value is None else _angle(value)
        direct = group.get("direct_run")
        explicit_direct = direct is not None
        if direct is not None and direct not in runs:
            raise ValueError("direct_run 必须属于本组 runs")
        overrides = group.get("angles_deg", {})
        if not isinstance(overrides, dict) or set(overrides) - set(runs):
            raise ValueError("angles_deg 必须是本组RUN到角度的映射")
        overrides = {run: _angle(value) for run, value in overrides.items()}
        confirm = group.get("confirm_metadata_override", False)
        if not isinstance(confirm, bool):
            raise ValueError("confirm_metadata_override 必须是布尔值")
        if direct is None:
            candidates = [r for r, v in metadata.items() if v is not None and abs(v) < THRESHOLD]
            # Missing metadata must not be guessed to be a reflection or direct run.
            if all(v is not None for v in metadata.values()) and len(candidates) == 1:
                direct = candidates[0]
        pending = []
        if direct is None:
            pending.append({"field": "direct_run", "message": "请用户明确本组哪个RUN是直通光"})
        if direct in overrides:
            raise ValueError("angles_deg 只填写反射RUN，直通身份由 direct_run 指定")
        rows = []
        for run in runs:
            original = metadata[run]
            value = overrides.get(run, original)
            if run == direct:
                if original is not None and abs(original) >= THRESHOLD and not confirm:
                    pending.append({"run": run, "field": "confirm_metadata_override", "metadata_rz": original,
                                    "message": "用户指定直通与元数据冲突，请用户确认覆盖"})
                rows.append({"run": run, "type": "direct", "Rz": original,
                             "angle_source": "metadata" if original is not None else "missing",
                             "role_source": "user" if explicit_direct else "metadata"})
                continue
            if value is None:
                pending.append({"run": run, "field": "angles_deg", "message": "若该RUN为反射，请用户提供反射角（度）"})
            elif abs(value) < THRESHOLD:
                pending.append({"run": run, "field": "angles_deg", "message": "反射角接近零，请确认直通身份及反射角"})
            if run in overrides and original is not None and not math.isclose(value, original, abs_tol=1e-8, rel_tol=0) and not confirm:
                pending.append({"run": run, "field": "confirm_metadata_override", "metadata_rz": original,
                                "supplied_angle_deg": value, "message": "用户角度与元数据冲突，请用户确认覆盖"})
            rows.append({"run": run, "type": "reflection", "Rz": value,
                         "angle_source": "user" if run in overrides else "metadata",
                         "role_source": "user" if explicit_direct else "metadata"})
        if pending:
            questions.append({"group_index": index, "group_name": name, "runs": runs,
                              "missing_or_conflicting": pending})
        plans.append({"group_id": f"group_{index:03d}", "group_name": name,
                      "data_source": source, "data_root": str(root),
                      "direct_run": direct, "classified": rows})
    return plans, questions


def execute_groups(plans, paths, output):
    import numpy as np
    import matplotlib.pyplot as plt
    from interface import build_modifications, calculate_mr_results, plot_results, build_summary
    from CSNS_reflection_script_forinterface import run_reflection
    from report_delivery import export_curve_data, zip_curve_data, plot_representative, method_markdown

    output = Path(output)
    summaries, group_results, warnings = [], [], []
    analysis_status = "estimated"
    data_files, methods = [], []
    representative = None
    fig, ax = plt.subplots(figsize=(10, 6))
    try:
        for plan in plans:
            folder = output / plan["group_id"]
            folder.mkdir()
            modifications = build_modifications(plan["classified"])
            modifications.update({"data_path": plan["data_root"],
                                  "param_path": str(Path(paths["param_root"]) / "BL08A")})
            datasets = run_reflection(modifications, output_dir=str(folder))
            expected = {x["run"] for x in plan["classified"] if x["type"] == "reflection"}
            if set(datasets) != expected:
                raise RuntimeError(f"{plan['group_id']} 返回的反射RUN与请求不一致")
            mr = calculate_mr_results(datasets)
            if representative is None:
                first = next(item["run"] for item in plan["classified"] if item["type"] == "reflection")
                representative = (plan["group_id"], first, datasets[first], mr[first])
            methods.extend(estimate.get("method", "local_edge") for estimate in mr.values())
            for run, estimate in mr.items():
                if estimate.get("status") == "needs_review":
                    analysis_status = "needs_review"
                warnings.extend(f"{plan['group_id']}/{run}: {message}" for message in estimate.get("warnings", []))
            plot_results(datasets, mr, str(folder))
            provenance = {x["run"]: x for x in plan["classified"]}
            rows = build_summary(plan["classified"], mr)
            for row in rows:
                item = provenance[row["Run"]]
                row.update({"group_id": plan["group_id"], "group_name": plan["group_name"],
                            "direct_run": plan["direct_run"], "data_source": plan["data_source"],
                            "angle_source": item["angle_source"], "role_source": item["role_source"]})
                row["_input_order"] = [plan.get("input_group_index", 0), plan.get("original_run_order", {}).get(row["Run"], -1)]
                if "target_m" in plan:
                    row["target_m"] = plan["target_m"]
            _write_summary(folder / "summary.csv", rows)
            estimates_file = folder / "mr_estimates.json"
            audit = {run: {key: value for key, value in estimate.items() if not isinstance(value, np.ndarray)}
                     for run, estimate in mr.items()}
            estimates_file.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
            summaries.extend(rows)
            group_results.append({"group_id": plan["group_id"], "group_name": plan["group_name"],
                                  "direct_run": plan["direct_run"], "output_path": str(folder),
                                  "mr_parameters_file": str(estimates_file),
                                  "individual_plots": [str(p) for p in sorted((folder / "plot").glob("*/*.png"))]})
            for run, dataset in datasets.items():
                data_files.append(export_curve_data(output, plan["group_id"], run, dataset))
                x = np.asarray(dataset["xvalue"].values).squeeze().reshape(-1)
                y = np.asarray(dataset["histogram"].values).squeeze().reshape(-1)
                error = np.asarray(dataset["error"].values).squeeze().reshape(-1)
                if x.shape != y.shape or error.shape != y.shape:
                    raise ValueError(f"{run} 的Q、反射率和误差长度不一致")
                valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(error) & (error >= 0) & (y > 0)
                excluded = int((~valid).sum())
                if excluded:
                    warnings.append(f"{plan['group_id']}/{run}: 总图忽略{excluded}个非有限、非正反射率或无效误差点，原始输出未修改")
                if not np.any(valid):
                    raise ValueError(f"{run} 没有可在对数反射率总图中展示的有效点")
                ax.errorbar(x[valid], y[valid], yerr=error[valid], fmt="o", linestyle="none",
                            markersize=3, capsize=2, elinewidth=.8,
                            label=f"{plan['group_id']} / {run} (direct {plan['direct_run']})")
        ax.set(xlabel="Q (Å⁻¹)", ylabel="Reflectivity", yscale="log", title="All groups — reflectivity")
        ax.legend(fontsize=8)
        fig.tight_layout()
        combined = output / "combined_reflectivity.png"
        fig.savefig(combined, dpi=180)
        summary = output / "summary.csv"
        summaries.sort(key=lambda row: row.get("_input_order", [0, 0]))
        _write_summary(summary, summaries)
        curve_zip = zip_curve_data(output, data_files)
        representative_group, representative_run, representative_data, representative_estimate = representative
        representative_plot = plot_representative(output, representative_run, representative_data, representative_estimate)
        # User-provided angles and explicit overrides are retained for reproducibility.
        (output / "resolved_groups.json").write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
        from result_comparison import compare_measurements
        comparison = compare_measurements(summaries)
        comparison["public"]["method_markdown"] = method_markdown(methods)
        diagnostics = output / "internal_diagnostics.json"
        diagnostics.write_text(json.dumps({"analysis_status": analysis_status, "warnings": warnings,
                                           "comparison": comparison, "groups": group_results}, ensure_ascii=False, indent=2), encoding="utf-8")
        # Use file events for previews/downloads, avoiding static-URL auto images.
        result = {"status": "success", "image_delivery": "files_event", "representative_plot": str(representative_plot),
                "combined_plot": str(combined), "summary_file": str(summary),
                "analysis_status": analysis_status,
                "output_path": str(output), "groups": group_results, "warnings": warnings,
                "presentation": comparison["public"], "diagnostics_file": str(diagnostics),
                "representative_run": representative_run,
                "representative_group": representative_group, "curve_data_zip": str(curve_zip),
                "curve_data_count": len(data_files),
                "public_files": [str(representative_plot), str(combined), str(summary), str(curve_zip)],
                "reflectivity_note": "summary.csv依次包含Run、m和r_envelope(%)三列，按输入分组/RUN顺序只列反射RUN；包络为平台到截止中心的外推值，不是厂家验收值。",
                "summary_row_order_file": str(output / "summary_row_order.json")}
        if os.getenv("RONGZAI_SIMPLE_RESULTS", "0").lower() in ("1", "true", "yes"):
            for key in ("analysis_status", "warnings", "reflectivity_note"):
                result.pop(key)
        # Preserve detailed paths in diagnostics only, avoiding extra auto-images.
        result["groups"] = [{key: value for key, value in group.items()
                             if key not in ("individual_plots", "mr_parameters_file")} for group in group_results]
        return result
    finally:
        plt.close(fig)


def _write_summary(path, rows):
    from summary_export import write_public_summary
    write_public_summary(path, rows)
