"""Deterministic comparison of per-RUN measurements; never infer physical sample identity."""
from collections import Counter
import math


def compare_measurements(rows, tolerance=.10):
    """Non-overlapping groups, sorted by m, anchored at each group's minimum m.

    A group's maximum m is at most 1+tolerance times its minimum; nearby
    neighbours cannot chain into a wider group. Only estimated, finite results
    without review flags participate in rankings. Public tables still show all
    computed reflection rows; exclusions remain internal.
    """
    if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
        raise ValueError("m比较容差必须在0至1之间")
    reflection = [row for row in rows if row.get("Type") == "reflection"]
    counts = Counter(row["Run"] for row in reflection)
    eligible, excluded, table = [], [], []
    for row in reflection:
        label = row["Run"] if counts[row["Run"]] == 1 else f"{row.get('group_id', 'group')}/{row['Run']}"
        try:
            m = float(row["m"])
            raw_r = row.get("r_envelope(%)")
            r = float(row["r(%)"] if raw_r is None else raw_r)
        except (TypeError, ValueError, KeyError):
            excluded.append({"label": label, "reason": "missing_estimate"})
            continue
        if not math.isfinite(m) or not math.isfinite(r) or m <= 0 or not 0 < r <= 100:
            excluded.append({"label": label, "reason": "invalid_estimate"})
            continue
        item = {"label": label, "run": row["Run"], "group_id": row.get("group_id"),
                "m": m, "r_envelope_percent": r}
        if row.get("target_m") is not None:
            item["target_m"] = row["target_m"]
        table.append(item)
        if row.get("mr_status") != "estimated":
            excluded.append({"label": label, "reason": "requires_review_or_unknown_status"})
        else:
            eligible.append(item)
    eligible.sort(key=lambda item: (item["m"], item["label"]))
    groups = []
    for item in eligible:
        if not groups or item["m"] > groups[-1][0]["m"] * (1 + tolerance) + 1e-12:
            groups.append([item])
        else:
            groups[-1].append(item)
    comparisons, conclusions = [], []
    for group in groups:
        if len(group) < 2:
            continue
        ranked = sorted(group, key=lambda item: (-item["r_envelope_percent"], item["label"]))
        high, low = ranked[0]["r_envelope_percent"], ranked[-1]["r_envelope_percent"]
        best = [item["label"] for item in ranked if math.isclose(item["r_envelope_percent"], high, rel_tol=1e-12)]
        worst = [item["label"] for item in ranked if math.isclose(item["r_envelope_percent"], low, rel_tol=1e-12)]
        improvement = (high - low) / low * 100
        comparisons.append({"m_min": group[0]["m"], "m_max": group[-1]["m"], "ranking": ranked,
                            "best": best, "worst": worst, "relative_improvement_percent": improvement})
        if math.isclose(high, low, rel_tol=1e-12):
            conclusions.append("、".join(best) + "的包络反射率相同，没有优劣差异。")
        else:
            order = " > ".join(f"{item['label']}（{item['r_envelope_percent']:.2f}%）" for item in ranked)
            conclusions.append(f"在m相近的可比较测量结果中，包络反射率排序：{order}；"
                               f"{'、'.join(best)}最高，比{'、'.join(worst)}高{improvement:.2f}%。")
    if not conclusions:
        conclusions = ["本次没有至少两条可可靠比较且m接近的结果，暂不排名。"]
    if eligible:
        conclusions.append("优化方向：对于可比条件下包络反射率较低的测量，建议优先检查基底及层间粗糙度、界面互扩散，"
                           "并复核双层周期厚度控制精度及沉积速率标定。")
        targeted = [item for item in eligible if "target_m" in item]
        if targeted:
            for item in targeted:
                target = item["target_m"]
                relation = "高于" if item["m"] > target else "低于" if item["m"] < target else "等于"
                conclusions.append(f"{item['label']}拟合m={item['m']:.4f}，数值{relation}用户提供的目标m={target:g}。")
        conclusions.append("在设计与测量截止定义一致、其他条件相近时，如果m值比设计值高，可能与薄膜双层周期偏薄有关；"
                           "如果m值比设计值低，可能与薄膜双层周期偏厚有关。")
    markdown = "| RUN | m | 包络反射率（%） |\n|---|---:|---:|\n"
    markdown += "\n".join(f"| {item['label']} | {item['m']:.4f} | {item['r_envelope_percent']:.2f} |" for item in table)
    public = {"table_markdown": markdown, "conclusion": "\n".join(conclusions)}
    return {"tolerance": tolerance, "grouping_policy": "sorted_m_min_anchor_nonoverlapping",
            "comparisons": comparisons, "excluded": excluded, "public": public}
