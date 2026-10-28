"""Read-only direct-beam matching. Never infer missing settings or search other runs."""
import json
import math
from pathlib import Path

FIELDS = ("lamda_min", "lamda_max", "S1H", "S1W", "S3H", "S3W")


def settings(document):
    info = document.get("summary", {}).get("json", {})
    devices = info.get("devices", {})
    values = {key: info.get(key) if key.startswith("lamda_") else devices.get(key) for key in FIELDS}
    invalid = [key for key, value in values.items()
               if isinstance(value, bool) or not isinstance(value, (float, int))
               or not math.isfinite(value) or value <= 0]
    if not invalid and values["lamda_min"] >= values["lamda_max"]:
        invalid = ["lamda_min", "lamda_max"]
    return values, invalid


def differences(a, b):
    return [{"field": key, "reflection_value": a[key], "direct_value": b[key],
             "difference_percent": (max(a[key], b[key]) / min(a[key], b[key]) - 1) * 100}
            for key in FIELDS if max(a[key], b[key]) / min(a[key], b[key]) > 1.01 + 1e-12]


def prepare_matched_groups(groups, paths, legacy_prepare, run_pattern, angle, threshold):
    if not isinstance(groups, list) or not groups:
        raise ValueError("groups 必须是非空列表；未指定分组时将全部RUN放在一个runs列表")
    sources = dict(paths.get("data_sources", {}))
    if paths.get("data_root"):
        sources.setdefault("default", paths["data_root"])
    plans, questions = [], []
    allowed = {"group_name", "runs", "data_source", "data_root", "direct_run", "angles_deg",
               "confirm_metadata_override", "roles", "direct_assignments", "target_m"}
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict) or set(group) - allowed:
            raise ValueError("分组必须是对象且不能包含未知字段")
        runs = group.get("runs")
        if (not isinstance(runs, list) or len(runs) < 2
                or any(not isinstance(r, str) or not run_pattern.fullmatch(r) for r in runs)
                or len(set(runs)) != len(runs)):
            raise ValueError("每项至少两个不重复的RUN，格式为RUN加七位数字")
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
        if not root.is_dir():
            raise ValueError(f"数据源目录不存在：{source}")
        name = group.get("group_name", f"group_{index:03d}")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("group_name 必须是1至128字符的名称")
        roles, angles = group.get("roles", {}), group.get("angles_deg", {})
        assignments = group.get("direct_assignments", {})
        for label, mapping in (("roles", roles), ("angles_deg", angles), ("direct_assignments", assignments)):
            if not isinstance(mapping, dict) or set(mapping) - set(runs):
                raise ValueError(f"{label} 必须是本项RUN的映射")
        if any(v not in ("direct", "reflection") for v in roles.values()):
            raise ValueError("roles 只能填写direct或reflection")
        angles = {r: angle(v) for r, v in angles.items()}
        direct = group.get("direct_run")
        if direct is not None and direct not in runs:
            raise ValueError("direct_run 必须属于本项runs")
        if any(not isinstance(v, str) or v not in runs for v in assignments.values()):
            raise ValueError("direct_assignments 的直通必须属于本项runs")
        confirm = group.get("confirm_metadata_override", False)
        if not isinstance(confirm, bool):
            raise ValueError("confirm_metadata_override 必须是布尔值")
        target = group.get("target_m")
        if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float))
                                   or not math.isfinite(target) or target <= 0):
            raise ValueError("target_m 必须是用户明确提供的正有限数值")
        explicit_roles = dict(roles)
        for r in set(assignments.values()) | ({direct} if direct else set()):
            if explicit_roles.get(r, "direct") != "direct" or r in angles:
                raise ValueError("指定直通的身份或角度填写冲突")
            explicit_roles[r] = "direct"
        for r in angles:
            if explicit_roles.get(r, "reflection") != "reflection":
                raise ValueError("直通不能填写反射角")
            explicit_roles[r] = "reflection"
        records, pending = {}, []
        for r in runs:
            with (root / r / r).open(encoding="utf-8") as f:
                doc = json.load(f)
            info = doc.get("summary", {}).get("json", {})
            raw = info.get("devices", {}).get("Rz")
            rz = None if raw is None else angle(raw)
            original_role = None if rz is None else ("direct" if abs(rz) < threshold else "reflection")
            role = explicit_roles.get(r, original_role)
            effective = angles.get(r, rz)
            values, invalid = settings(doc)
            if invalid:
                pending.append({"run": r, "field": "settings", "invalid_fields": invalid,
                                "message": "测试设置缺失或无效，无法校验；请核对测试信息文件，不以默认值代替"})
            if role is None:
                pending.append({"run": r, "field": "roles", "message": "缺少Rz，请确认直通或反射身份；反射还需补充角度（度）"})
            if original_role and role != original_role and not confirm:
                pending.append({"run": r, "field": "confirm_metadata_override", "metadata_rz": rz,
                                "message": "用户指定身份与Rz冲突，请明确确认覆盖"})
            if r in angles and rz is not None and not math.isclose(effective, rz, abs_tol=1e-8, rel_tol=0) and not confirm:
                pending.append({"run": r, "field": "confirm_metadata_override", "metadata_rz": rz,
                                "message": "用户角度与Rz冲突，请明确确认覆盖"})
            if role == "reflection" and (effective is None or abs(effective) < threshold):
                pending.append({"run": r, "field": "angles_deg", "message": "请提供非零反射角（度）"})
            records[r] = {"role": role, "settings": values, "invalid": invalid}
        if pending:
            questions.append({"group_index": index, "missing_or_conflicting": pending})
            continue
        directs = [r for r in runs if records[r]["role"] == "direct"]
        reflections = [r for r in runs if records[r]["role"] == "reflection"]
        if not reflections:
            questions.append({"group_index": index, "message": "只有直通，没有反射RUN，请补充反射RUN"})
            continue
        if set(assignments) - set(reflections):
            raise ValueError("direct_assignments 只能以反射RUN为键")
        buckets, audits = {}, {}
        for r in reflections:
            checks = {d: differences(records[r]["settings"], records[d]["settings"]) for d in directs}
            candidates = [d for d in directs if not checks[d]]
            selected = assignments.get(r, direct)
            if selected is not None and selected not in candidates:
                pending.append({"run": r, "field": "direct_assignments", "specified_direct": selected,
                                "candidates": candidates, "mismatches": checks.get(selected, []),
                                "message": "指定直通身份或测试设置不匹配，请重新提供匹配直通；确认覆盖不能豁免设置校验"})
            elif selected is None and len(candidates) != 1:
                pending.append({"run": r, "field": "direct_assignments", "candidates": candidates,
                                "mismatches": checks,
                                "message": "多个匹配直通，请分别指定或统一选择一个对相关反射均匹配的直通" if candidates else "没有匹配直通，请补充匹配的直通RUN"})
            else:
                selected = selected or candidates[0]
                buckets.setdefault(selected, []).append(r)
                audits[r] = {"direct_run": selected, "candidates": candidates,
                             "selection": "user" if r in assignments or direct else "unique_match",
                             "reflection_settings": records[r]["settings"],
                             "direct_settings": records[selected]["settings"]}
        if pending:
            questions.append({"group_index": index, "missing_or_conflicting": pending})
            continue
        for selected, reflected in buckets.items():
            members = [r for r in runs if r == selected or r in reflected]
            request = {"runs": members, "group_name": name, "data_source": source,
                       "direct_run": selected, "angles_deg": {r: v for r, v in angles.items() if r in reflected},
                       "confirm_metadata_override": confirm}
            if "data_root" in group:
                request["data_root"] = str(root)
            resolved, missing = legacy_prepare([request], paths)
            if missing:
                questions.extend(missing)
                continue
            plan = resolved[0]
            plan["group_id"] = f"group_{len(plans)+1:03d}"
            plan["input_group_index"] = index
            plan["input_run_order"] = members
            plan["original_run_order"] = {r: runs.index(r) for r in reflected}
            plan["matching"] = {r: audits[r] for r in reflected}
            plan["matching_policy"] = "six_positive_fields_max_over_min_le_1.01"
            if target is not None:
                plan["target_m"] = target
            for row in plan["classified"]:
                if row["run"] not in explicit_roles:
                    row["role_source"] = "metadata"
            plans.append(plan)
    # Whole-batch preflight: no partially ready plans may be executed.
    return ([], questions) if questions else (plans, [])
