"""定频运动下发:按真实时间推进,持续下发 θ(t) = θ₀ + SIGN·∫ω dt。

设计要点
--------
* **θ 累积在上位机**:MCU 端保持无状态纯逆解(收到 v/θ/ω 即算即用),
  上位机负责"轨迹意图"的时间积分。这样 MCU 固件极简、可复现、易调试。
* **真实时钟推进**:用 ``time.monotonic()`` 计算实际 Δt,而非依赖发送次数,
  避免调度抖动导致 θ 累积误差(定时器晚触发也不会少积一分角度)。
* **定频发送**:按 ``rate_hz`` 持续下发,即使数值不变也发 —— 保证 MCU 侧
  控制环有稳定的输入节奏(丢一两帧不影响,下一帧立即补上)。
* **与链路解耦**:仅依赖 ``ChassisLink.send_vel``,串口/BLE 均可复用。

符号约定(SIGN,极重要)
------------------------
MCU 用 ``ω`` 驱动**车体自转**,车头姿态 φ(t) = ∫ω dt。
世界系下的运动方向为::

    θ_world(t) = θ_body(t) + φ(t)
               = θ_body(t) + ∫ω dt

* **SIGN = −1(默认)**:θ_body 反向补偿车体自转 ⇒ θ_world 恒定 ⇒
  车**沿固定世界方向平移 + 车体自转**(转向中心在车体之外,即"世界系方向不变")。
* **SIGN = +1**:θ_body 与车体自转同向叠加 ⇒ θ_world 以 2ω 变化 ⇒
  车**沿更急的圆弧走**(一般不是期望行为,易表现为"偏心转")。

⚠️ 若 MCU 端**自己又对 ω 积分一次**,则会与上位机形成双重积分,
   世界系方向将以 2ω 变化 —— 这正是"偏心转"的典型成因。
   MCU 端必须**不积分 θ**,只按收到的 (v, θ, ω) 做逆解。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .link import ChassisLink, LinkError

logger = logging.getLogger(__name__)

# θ 归一化到 (-180, 180]
_TAU = 6.283185307179586
_RAD2DEG = 57.29577951308232


def normalize_deg(deg: float) -> float:
    """角度归一化到 [-180, 180)。"""
    d = (deg + 180.0) % 360.0
    if d < 0:
        d += 360.0
    return d - 180.0


@dataclass
class MotionTick:
    """一次下发的快照(供回调/日志使用)。"""

    elapsed: float        # 自启动以来的秒数
    theta_deg: float      # 当前累积方向角
    v_mps: float          # 速度大小
    omega_radps: float    # 自转角速度
    index: int            # 第几次下发(0 起)


class MotionRunner:
    """定频运动下发器:θ 随时间累积并持续下发。

    用法::

        runner = MotionRunner(link, v=0.5, theta0=90.0, omega=1.57, rate_hz=20)
        await runner.run()          # 阻塞到 stop() 被调用
        runner.stop()               # 线程/回调安全地请求停止

    参数:
        v         速度大小 m/s(>0 才有位移;ω≠0 且 v=0 时为原地自转)
        theta0    初始方向角 deg(车体系)
        omega     自转角速度 rad/s —— θ 以其速率累积
        rate_hz   下发频率 Hz(默认 config.MOTION_RATE_HZ)
        on_tick   每次下发后的回调(可空)
    """

    def __init__(
        self,
        link: ChassisLink,
        *,
        v: float,
        theta0: float,
        omega: float,
        rate_hz: float | None = None,
        on_tick: Optional[Callable[[MotionTick], None]] = None,
        stop_on_error: bool = True,
        sign: float | None = None,
    ) -> None:
        from . import config

        self.link = link
        self.v = float(v)
        self.theta0 = float(theta0)
        self.omega = float(omega)
        self.rate_hz = float(rate_hz or config.MOTION_RATE_HZ)
        self.on_tick = on_tick
        self.stop_on_error = stop_on_error
        # θ 累积符号:默认 -1(反向补偿车体自转,世界系方向恒定)
        self.sign = float(config.THETA_ACCUM_SIGN if sign is None else sign)
        if self.sign not in (-1.0, 1.0):
            raise ValueError("sign 只能为 +1 或 -1")

        self._stop = asyncio.Event()
        self._running = False
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def period(self) -> float:
        return 1.0 / self.rate_hz if self.rate_hz > 0 else 0.05

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """请求停止(可从其它线程调用 —— 由事件循环持有,线程安全)。"""
        self._stop.set()

    def update(self, *, v: float | None = None, omega: float | None = None) -> None:
        """运行中动态调整速度/自转(θ₀ 不重置,累积连续)。"""
        if v is not None:
            self.v = float(v)
        if omega is not None:
            self.omega = float(omega)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    async def run(self, *, send_stop_on_exit: bool = True) -> float:
        """阻塞执行,直到 ``stop()`` 或链路错误。返回累计运行秒数。"""
        if self._running:
            raise RuntimeError("MotionRunner 已在运行")
        if not self.link.is_connected:
            raise LinkError("链路未连接,无法启动运动下发")

        self._stop.clear()
        self._running = True
        period = self.period
        theta = self.theta0
        t0 = time.monotonic()
        last = t0
        index = 0

        try:
            while not self._stop.is_set():
                # 1) 用真实时间差累积 θ(不依赖发送次数,避免抖动误差)
                #
                #    θ_body(t) = θ₀ + SIGN · ∫ ω dt
                #
                #    符号约定(关键):
                #      MCU 会用 ω 驱动车体自转,车头姿态 φ(t) = ∫ω dt。
                #      世界系下的运动方向 = θ_body(t) + φ(t)。
                #      若要求【世界系方向恒定】(车沿固定方向平移 + 车体自转),
                #      则 θ_body 必须反向补偿车体自转:
                #          θ_body(t) + ∫ω dt = θ₀   →   θ_body(t) = θ₀ − ∫ω dt
                #      故 SIGN = -1(默认)。
                #      若要求【车沿圆弧走、车头随切线】(世界系方向随车体转),
                #      则改为 SIGN = +1。
                now = time.monotonic()
                dt = now - last
                last = now
                if abs(self.omega) > 1e-9:
                    theta = normalize_deg(
                        theta + self.sign * self.omega * dt * _RAD2DEG
                    )

                # 2) 定频下发
                try:
                    await self.link.send_vel(self.v, theta, self.omega)
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("运动下发失败: %s", self.last_error)
                    if self.stop_on_error:
                        break

                index += 1
                if self.on_tick:
                    try:
                        self.on_tick(
                            MotionTick(
                                elapsed=now - t0,
                                theta_deg=theta,
                                v_mps=self.v,
                                omega_radps=self.omega,
                                index=index,
                            )
                        )
                    except Exception:
                        logger.exception("on_tick 回调异常")

                # 3) 按周期休眠(扣除本次发送耗时,保持频率稳定)
                await asyncio.sleep(max(0.0, period - (time.monotonic() - now)))

            elapsed = time.monotonic() - t0
            if send_stop_on_exit:
                try:
                    await self.link.send_stop()
                except Exception as exc:
                    logger.warning("停止时发送 STOP 失败: %s", exc)
            return elapsed
        finally:
            self._running = False
