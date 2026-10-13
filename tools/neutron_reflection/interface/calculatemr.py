import numpy as np
from scipy.signal import savgol_filter
from scipy.stats import linregress


def calculate_mr_from_dataset(
        dataset,
        segment1_start_q=0.0217,
        threshold_high=-0.5,
        threshold_low=0.5
):
    """
    计算单个reflection run的 Qm、m、r

    Qm:
        Segment2下降区中点对应的Q

    m:
        Qm / 0.0217

    r:
        Segment1平台区线性拟合后，
        外推到 Q = Qm 时的强度
    """

    # 1. 读取Q和R
    x = np.squeeze(dataset["xvalue"].values)
    y = np.squeeze(dataset["histogram"].values)

    # 去掉nan / inf
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]

    # 2. Segment1起始位置
    segment1_start = np.searchsorted(
        x,
        segment1_start_q
    )

    if segment1_start == len(x):
        segment1_start = len(x) - 1

    if segment1_start < 3:
        segment1_start = 3

    x_remain = x[segment1_start:]
    y_remain = y[segment1_start:]

    # 3. 平滑 + 一阶导数 + 二阶导数
    window_length = min(
        11,
        len(x_remain) // 10 * 2 + 1
    )

    if window_length % 2 == 0:
        window_length += 1

    if window_length < 5:
        window_length = 5

    # Savgol要求window不能超过数据长度
    if window_length > len(x_remain):
        window_length = len(x_remain)

        if window_length % 2 == 0:
            window_length -= 1

    if window_length < 3:
        raise ValueError(
            "有效数据点太少，无法进行平滑和分段"
        )

    polyorder = min(3, window_length - 1)

    y_smooth = savgol_filter(
        y_remain,
        window_length=window_length,
        polyorder=polyorder
    )

    dy_dx = np.gradient(
        y_smooth,
        x_remain
    )

    dy_dx_smooth = savgol_filter(
        dy_dx,
        window_length=window_length,
        polyorder=polyorder
    )

    d2 = np.gradient(
    dy_dx_smooth,
    x_remain
    )

    d2_smooth = savgol_filter(
        d2,
        window_length=window_length,
        polyorder=polyorder
    )



    # 4. Segment2起点---d2主负峰处

    idx_start = int(
        np.argmin(d2)
    )
    Q_start = x_remain[idx_start]

    #——————Segment2终点---d2主正峰处
    search_length = max(
        5,
        len(d2) // 3
    )

    search_end = min(
        len(d2),
        idx_start + search_length
    )

    if search_end <= idx_start + 1:
        raise ValueError(
            "Segment2终点搜索范围不足"
        )

    idx_end_relative = np.argmax(
        d2[idx_start + 1:search_end]
    )

    idx_end = (
        idx_start
        + 1
        + idx_end_relative
    )

    if idx_end <= idx_start:
        raise ValueError(
            "Segment2终点未位于起点之后"
        )

    Q_start = float(
        x_remain[idx_start]
    )

    Q_end = float(
        x_remain[idx_end]
    )


    x2 = x_remain[
        idx_start:idx_end + 1
    ]

    y2 = y_remain[
        idx_start:idx_end + 1
    ]


    #定义segment1
    x1 = x_remain[:idx_start]
    y1 = y_remain[:idx_start]


    if len(x1) < 2:
        raise ValueError(
            "Segment1数据点不足，无法拟合平台区"
        )

    if len(x2) < 2:
        raise ValueError(
            "Segment2数据点不足，无法确定Qm"
        )

    # 6. Segment1线性拟合
    fit = linregress(x1, y1)

    slope1 = fit.slope
    intercept1 = fit.intercept

    #计算Qm & r
    Qm = (
        Q_start + Q_end
    ) / 2.0

    m = Qm / 0.0217

    r = (
        slope1 * Qm
        + intercept1
    )



    return {
        "Qm": float(Qm),
        "m": float(m),
        "r": float(r),
        "slope1": float(slope1),
        "intercept1": float(intercept1),

        # 画图时会用
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,

        "x_remain": x_remain,
        "y_remain": y_remain,
        "d1": dy_dx_smooth,
        "d2": d2_smooth
    }
