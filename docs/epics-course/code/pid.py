"""pid.py —— 一个能上真设备的 PID 控制器（第 6 章）

不要小看这一百多行。散裂中子源的温控、磁场、激光功率、样品台位置，
底层跑的都是同一个东西。控制组给你调的参数，也是这三行公式。

    P 项：现在差多少，就出多少力        —— 反应快，但会留静态误差
    I 项：过去累计差多少，就补多少力    —— 消除静态误差，但会过冲/振荡
    D 项：误差在往哪个方向变，提前刹车  —— 抑制过冲，但会放大噪声

工程上真正会遇到、而这个类已经处理掉的四个坑：

1. **积分饱和（windup）**：执行器已经到头了（加热 100% 也不能再热），
   积分项还在疯狂累积，等温度降下来时它还在输出满功率 → 巨大过冲。
   处理：积分抗饱和（output clamping）。
2. **微分噪声放大**：D 项是"误差变化率"，传感器噪声除以时间步长会爆炸。
   处理：对测量值低通滤波（derivative on measurement + filter）。
3. **执行器限幅**：输出必须夹在 [out_min, out_max]。
4. **采样周期不稳**：用真实 dt 积分，而不是假设固定周期。

用法：
    pid = PID(kp=5.0, ki=0.1, kd=0.0, out_min=0.0, out_max=100.0)
    for ...:
        out = pid.update(setpoint=60.0, measurement=temp, dt=0.1)
        heater.put(out)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PID:
    kp: float
    ki: float = 0.0
    kd: float = 0.0
    out_min: float = 0.0
    out_max: float = 100.0
    d_filter: float = 0.2       # 微分低通滤波系数（0-1，越小越平滑）
    integral: float = 0.0
    prev_measurement: float | None = None
    prev_derivative: float = 0.0
    last_output: float = 0.0
    history: list = field(default_factory=list)

    def reset(self, output: float = 0.0) -> None:
        self.integral = 0.0
        self.prev_measurement = None
        self.prev_derivative = 0.0
        self.last_output = output

    def update(self, setpoint: float, measurement: float, dt: float) -> float:
        """算一步输出。dt 用真实经过的时间（秒）。"""
        if dt <= 0:
            return self.last_output

        error = setpoint - measurement

        # --- P ---
        p_term = self.kp * error

        # --- I（先累加，最后统一夹到输出限幅，并做抗饱和） ---
        self.integral += error * dt

        # --- D：对测量值求导（避免设定值阶跃时微分冲击），并低通滤波 ---
        if self.prev_measurement is None:
            derivative = 0.0
        else:
            raw_d = -(measurement - self.prev_measurement) / dt
            derivative = (self.d_filter * raw_d
                          + (1.0 - self.d_filter) * self.prev_derivative)
        self.prev_measurement = measurement
        self.prev_derivative = derivative
        d_term = self.kd * derivative

        output = p_term + self.ki * self.integral + d_term

        # --- 抗饱和：如果输出撞到限幅，且误差还在把输出往外推，就回退积分 ---
        if output > self.out_max:
            output = self.out_max
            if error > 0:
                self.integral -= error * dt
        elif output < self.out_min:
            output = self.out_min
            if error < 0:
                self.integral -= error * dt

        self.last_output = output
        self.history.append((setpoint, measurement, output))
        return output

    def describe(self) -> str:
        return (f"PID(Kp={self.kp:g}, Ki={self.ki:g}, Kd={self.kd:g}, "
                f"输出范围 {self.out_min:g}..{self.out_max:g})")


def auto_tune_relay(setpoint: float, measurement_fn, output_fn,
                    relay_amplitude: float = 30.0, bias: float = 30.0,
                    period: float = 1.0, cycles: int = 8, verbose: bool = True):
    """继电反馈法（Åström–Hägglund）自动整定 —— 也就是"临界比例度法"的自动化版。

    做法：在设定点附近把加热功率在 bias±amplitude 之间来回切换，
    观察温度是否形成稳定振荡。记录振荡周期 Tu 和振幅 a，
    就能算出临界增益 Ku = 4·amplitude / (π·a)，然后：

        Ziegler–Nichols（经典整定）:  Kp = 0.6·Ku,  Ki = 2·Kp/Tu,  Kd = Kp·Tu/8

    返回 (kp, ki, kd)。**这只是起点，不是终点** —— 真实设备永远要人工微调。
    """
    import time

    outputs = []
    measurements = []
    high = True
    t0 = time.monotonic()
    if verbose:
        print(f"  继电整定：在 {bias - relay_amplitude:.0f}% 和 "
              f"{bias + relay_amplitude:.0f}% 之间切换，观察 {cycles} 个周期……")
    try:
        while len(measurements) < cycles * 2:
            output = bias + (relay_amplitude if high else -relay_amplitude)
            output_fn(output)
            time.sleep(period)
            value = measurement_fn()
            outputs.append(output)
            measurements.append(value)
            # 检测穿越设定点 → 翻转
            if len(measurements) >= 2:
                crossed_up = measurements[-2] < setpoint <= measurements[-1]
                crossed_down = measurements[-2] > setpoint >= measurements[-1]
                if (high and crossed_down) or (not high and crossed_up):
                    high = not high
                    if verbose:
                        print(f"    切换点 t={time.monotonic() - t0:5.1f}s "
                              f"T={value:.2f}℃")
    finally:
        output_fn(0.0)

    # 从穿越点估计振荡周期与振幅
    crossings = [i for i in range(1, len(measurements))
                 if (measurements[i - 1] - setpoint) * (measurements[i] - setpoint) <= 0]
    if len(crossings) < 3:
        raise RuntimeError("没有观察到足够的振荡，无法整定："
                           "请加大 relay_amplitude，或延长周期/增加 cycles")

    half_periods = [(crossings[i + 1] - crossings[i]) * period
                    for i in range(len(crossings) - 1)]
    tu = 2.0 * (sum(half_periods) / len(half_periods))
    peak = max(measurements[len(measurements) // 4:])
    trough = min(measurements[len(measurements) // 4:])
    amplitude = max(1e-6, (peak - trough) / 2.0)
    ku = 4.0 * relay_amplitude / (3.141592653589793 * amplitude)

    kp = 0.6 * ku
    ki = 2.0 * kp / tu
    kd = kp * tu / 8.0
    if verbose:
        print(f"  振荡周期 Tu ≈ {tu:.1f}s，振幅 a ≈ {amplitude:.2f}℃，"
              f"Ku ≈ {ku:.2f}")
        print(f"  → 建议起点：Kp={kp:.3f}  Ki={ki:.4f}  Kd={kd:.2f}")
    return kp, ki, kd
