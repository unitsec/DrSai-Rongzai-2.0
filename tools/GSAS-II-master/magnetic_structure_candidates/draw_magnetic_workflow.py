"""Draw the neutron powder diffraction to magnetic structure workflow."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.path import Path as MplPath


OUTPUT_DIR = Path(__file__).resolve().parent


def _configure_font() -> None:
    """Use a CJK font when available, while keeping the script portable."""
    preferred = (
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "WenQuanYi Zen Hei",
        "Microsoft YaHei",
        "SimHei",
    )
    installed = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in preferred if name in installed), "DejaVu Sans")
    plt.rcParams["font.family"] = selected
    plt.rcParams["axes.unicode_minus"] = False


def _add_box(ax, x: float, title: str, details: str, color: str) -> None:
    box = FancyBboxPatch(
        (x, 0.28),
        1.62,
        1.18,
        boxstyle="round,pad=0.025,rounding_size=0.06",
        linewidth=1.4,
        edgecolor=color,
        facecolor="white",
    )
    ax.add_patch(box)
    ax.text(
        x + 0.81,
        1.12,
        title,
        ha="center",
        va="center",
        fontsize=11,
        fontweight="bold",
        color=color,
    )
    ax.text(
        x + 0.81,
        0.73,
        details,
        ha="center",
        va="center",
        fontsize=8.5,
        linespacing=1.35,
        color="#263238",
    )


def _add_arrow(ax, start_x: float, end_x: float, y: float = 0.87) -> None:
    ax.annotate(
        "",
        xy=(end_x, y),
        xytext=(start_x, y),
        arrowprops={
            "arrowstyle": "-|>",
            "linewidth": 1.5,
            "color": "#607d8b",
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )


def draw_workflow(output_stem: str | Path | None = None) -> tuple[Path, Path]:
    """Create horizontal SVG and PNG workflow diagrams and return their paths."""
    _configure_font()
    stem = Path(output_stem) if output_stem is not None else OUTPUT_DIR / "magnetic_structure_workflow"
    stem.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(20, 4.7), dpi=160)
    fig.patch.set_facecolor("#f6f8fa")
    ax.set_facecolor("#f6f8fa")
    ax.set_xlim(-0.25, 16.95)
    ax.set_ylim(-0.72, 2.25)
    ax.axis("off")

    positions = [
        (0.00, "两张中子粉末谱", "低温谱：有磁峰\n高温谱：无磁峰", "#1565c0"),
        (1.90, "数据预处理", "校正、归一化、\n背景与 d-spacing", "#1565c0"),
        (3.80, "核结构精修", "CIF + 高温谱\n空间群、晶胞、原子参数", "#00897b"),
        (5.70, "差谱与磁峰", "低温谱 - 高温谱\n提取磁峰 d-spacing", "#ef6c00"),
        (7.60, "k-vector 搜索", "高对称点 / k-path\n第一布里渊区网格", "#ef6c00"),
        (9.50, "磁空间群候选", "给定 k + 母相空间群\nBilbao 子群筛选", "#8e24aa"),
        (11.40, "初始磁结构", "磁位点、磁矩约束\n生成 GSAS-II magnetic phase", "#8e24aa"),
        (13.30, "磁性 Rietveld 精修", "磁矩大小/方向\n相位与必要的多相参数", "#c62828"),
        (15.20, "验证与输出", "Rwp / Rmag / 差谱\n最终磁空间群与磁矩", "#2e7d32"),
    ]

    for index, (x, title, details, color) in enumerate(positions):
        _add_box(ax, x, title, details, color)
        if index:
            _add_arrow(ax, positions[index - 1][0] + 1.62, x)

    ax.text(
        0.04,
        1.92,
        "Neutron powder diffraction → refined magnetic structure",
        fontsize=16,
        fontweight="bold",
        color="#263238",
        ha="left",
    )
    ax.text(
        0.04,
        1.66,
        "从两张衍射谱开始：利用高温谱定义核结构，用低温减高温识别磁散射，再进入 k、磁空间群和磁矩精修。",
        fontsize=9.5,
        color="#546e7a",
        ha="left",
    )

    # Route the feedback below the boxes so the return direction is explicit.
    feedback_path = MplPath(
        [(16.01, 0.28), (16.01, 0.02), (8.40, 0.02), (8.40, 0.28)],
        [MplPath.MOVETO, MplPath.LINETO, MplPath.LINETO, MplPath.LINETO],
    )
    feedback = FancyArrowPatch(
        path=feedback_path,
        arrowstyle="-|>",
        linewidth=1.5,
        color="#c62828",
        mutation_scale=15,
    )
    ax.add_patch(feedback)
    ax.text(
        12.20,
        -0.27,
        "拟合不合理：返回 k-vector 搜索，重新比较磁空间群和磁矩模型",
        ha="center",
        va="center",
        fontsize=8.5,
        color="#c62828",
    )

    fig.tight_layout(pad=0.7)
    svg_path = stem.with_suffix(".svg")
    png_path = stem.with_suffix(".png")
    fig.savefig(svg_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return svg_path, png_path


if __name__ == "__main__":
    svg_path, png_path = draw_workflow()
    print(f"Wrote {svg_path}")
    print(f"Wrote {png_path}")