"""MotionRunner 测试:θ 随时间按 ω 累积、定频下发、停止语义。

不接触硬件;验证"旋转前进"的核心行为:
    θ(t) = θ₀ + ∫ω·dt,按 rate_hz 持续下发,停止时自动发 STOP。
"""
import asyncio
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control.motion import MotionRunner, normalize_deg  # noqa: E402


class FakeLink:
    """记录所有下发帧的假链路。"""

    is_connected = True

    def __init__(self) -> None:
        self.frames: list[tuple[float, float, float]] = []
        self.stops = 0

    async def send_vel(self, v: float, deg: float, omega: float) -> None:
        self.frames.append((v, deg, omega))

    async def send_stop(self) -> None:
        self.stops += 1


def test_normalize() -> None:
    assert normalize_deg(0) == 0
    assert normalize_deg(90) == 90
    assert normalize_deg(370) == 10
    assert normalize_deg(-190) == 170
    assert normalize_deg(180) == -180      # 约定 [-180, 180)
    assert normalize_deg(540) == -180
    print("1) 角度归一化 [-180,180): OK")


def test_theta_accumulates_negative_default() -> None:
    """默认 SIGN=-1:θ 反向补偿车体自转 ⇒ 世界系方向恒定。

    θ_body(t) = θ₀ − ∫ω·dt;世界系方向 = θ_body + ∫ω·dt = θ₀(恒定)。
    """

    async def run() -> None:
        link = FakeLink()
        # ω = 1.5708 rad/s = 90°/s,1 秒 θ_body 应累积到约 -90°
        runner = MotionRunner(link, v=0.5, theta0=0.0, omega=1.5708, rate_hz=20)
        assert runner.sign == -1.0, "默认符号应为 -1"
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(1.0)
        runner.stop()
        await task

        assert link.stops == 1, "停止时应恰好发一次 STOP"
        n = len(link.frames)
        assert 15 <= n <= 25, f"1 秒 @20Hz 应约 20 帧,实际 {n}"
        thetas = [f[1] for f in link.frames]
        # θ_body 单调递减(负向累积)
        assert all(b <= a for a, b in zip(thetas, thetas[1:])), thetas
        # 期末 ≈ -90°
        assert -95.0 <= thetas[-1] <= -80.0, f"θ 期末={thetas[-1]} 期望≈-90"
        # v/ω 原样透传
        assert all(abs(f[0] - 0.5) < 1e-9 for f in link.frames)
        assert all(abs(f[2] - 1.5708) < 1e-9 for f in link.frames)
        print(f"2) 默认 SIGN=-1,θ 反向累积: {thetas[0]:.1f}° → {thetas[-1]:.1f}°"
              f"({n} 帧/秒): OK")

    asyncio.run(run())


def test_world_direction_constant() -> None:
    """核心验证:θ_body(t) + ∫ω·dt 应恒等于 θ₀(世界系方向不变)。"""

    async def run() -> None:
        link = FakeLink()
        theta0 = 30.0
        omega = 1.2  # rad/s
        rate = 20.0
        runner = MotionRunner(link, v=0.6, theta0=theta0, omega=omega, rate_hz=rate)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.8)
        runner.stop()
        await task

        # 逐帧重建世界系方向:world_i = θ_body_i + ω·t_i
        worst = 0.0
        for i, (_v, th, _w) in enumerate(link.frames):
            t = i / rate
            world = th + omega * t * 57.29577951308232
            err = abs(((world - theta0 + 180) % 360) - 180)
            worst = max(worst, err)
        assert worst < 1.5, f"世界系方向最大偏差 {worst:.2f}° 应≈0"
        print(f"3) 世界系方向恒定(θ₀={theta0}°,最大偏差 {worst:.2f}°): OK")

    asyncio.run(run())


def test_sign_switch_flips_direction() -> None:
    """SIGN=+1 时 θ 应正向累积(与默认相反的物理含义)。"""

    async def run() -> None:
        link = FakeLink()
        runner = MotionRunner(link, v=0.5, theta0=0.0, omega=1.5708,
                              rate_hz=20, sign=+1.0)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        runner.stop()
        await task
        thetas = [f[1] for f in link.frames]
        assert thetas[-1] > 30.0, f"SIGN=+1 应为正向累积,实际 {thetas[-1]}"
        print(f"4) SIGN=+1 正向累积: 0° → {thetas[-1]:.1f}°: OK")

    asyncio.run(run())


def test_invalid_sign_rejected() -> None:
    """sign 只接受 ±1。"""
    try:
        MotionRunner(FakeLink(), v=0.1, theta0=0, omega=0, sign=0.0)
    except ValueError:
        print("5) 非法 sign 被拒绝: OK")
    else:
        raise AssertionError("应抛 ValueError")


def test_omega_zero_theta_constant() -> None:
    """ω=0 时 θ 不应变化(直线运动)。"""

    async def run() -> None:
        link = FakeLink()
        runner = MotionRunner(link, v=1.0, theta0=45.0, omega=0.0, rate_hz=20)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        runner.stop()
        await task
        thetas = [f[1] for f in link.frames]
        assert all(t == 45.0 for t in thetas), thetas
        print(f"6) ω=0 时 θ 恒定(直线): {len(thetas)} 帧全为 45.0°: OK")

    asyncio.run(run())


def test_negative_omega_and_wrap() -> None:
    """ω<0 且 SIGN=-1 时 θ 递增(反向补偿),穿越 ±180 边界归一化仍正确。"""

    async def run() -> None:
        link = FakeLink()
        theta0 = 170.0
        omega = -1.5708          # rad/s
        rate = 20.0
        runner = MotionRunner(link, v=0.5, theta0=theta0, omega=omega, rate_hz=rate)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        runner.stop()
        await task
        thetas = [f[1] for f in link.frames]
        assert len(thetas) > 5, thetas
        # 累积方向应为 +ω·SIGN = -1.5708 × -1 = +1.5708 rad/s → 0.5s 约 +45°
        # 170° + 45° = 215° → 归一化约 -145°
        assert -155.0 <= thetas[-1] <= -135.0, f"期末={thetas[-1]} 期望≈-145(即215°)"
        # 全部落在合法区间
        assert all(-180.0 <= t < 180.0 for t in thetas), thetas
        print(f"7) ω<0(SIGN=-1)θ 递增并跨 ±180 归一化: "
              f"{thetas[0]:.1f}° → {thetas[-1]:.1f}°(等价 215°): OK")

    asyncio.run(run())


def test_rate_accuracy() -> None:
    """下发频率应接近设定值。"""

    async def run() -> None:
        link = FakeLink()
        runner = MotionRunner(link, v=0.0, theta0=0.0, omega=0.0, rate_hz=50)
        t0 = time.monotonic()
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(1.0)
        runner.stop()
        await task
        dt = time.monotonic() - t0
        hz = len(link.frames) / dt
        assert 40 <= hz <= 60, f"50Hz 设定,实测 {hz:.1f}Hz"
        print(f"8) 定频下发精度: 设定 50Hz,实测 {hz:.1f}Hz ({len(link.frames)} 帧): OK")

    asyncio.run(run())


def test_stop_semantics() -> None:
    """停止后必须发 STOP,且状态复位。"""

    async def run() -> None:
        link = FakeLink()
        runner = MotionRunner(link, v=1.0, theta0=0.0, omega=1.0, rate_hz=20)
        assert not runner.is_running
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.2)
        assert runner.is_running, "运行中 is_running 应为 True"
        runner.stop()
        elapsed = await task
        assert not runner.is_running, "结束后 is_running 应为 False"
        assert link.stops == 1, "应发 STOP"
        assert elapsed > 0, elapsed
        print(f"9) 停止语义(发 STOP + 状态复位,运行 {elapsed:.2f}s): OK")

    asyncio.run(run())


def test_running_twice_rejected() -> None:
    """同一 runner 不可并发运行。"""

    async def run() -> None:
        link = FakeLink()
        runner = MotionRunner(link, v=1.0, theta0=0.0, omega=0.0, rate_hz=20)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)
        try:
            await runner.run()
        except RuntimeError:
            print("10) 并发运行被拒绝: OK")
        else:
            raise AssertionError("应抛 RuntimeError")
        finally:
            runner.stop()
            await task

    asyncio.run(run())


if __name__ == "__main__":
    test_normalize()
    test_theta_accumulates_negative_default()
    test_world_direction_constant()
    test_sign_switch_flips_direction()
    test_invalid_sign_rejected()
    test_omega_zero_theta_constant()
    test_negative_omega_and_wrap()
    test_rate_accuracy()
    test_stop_semantics()
    test_running_twice_rejected()
    print("\nMotionRunner 测试全部通过 ✓")