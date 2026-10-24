/* ==========================================================================
 * chassis_protocol.c —— 底盘侧 VEL 协议解析参考实现
 *
 * 协议(PC -> MCU,文本行,以 \n 结尾):
 *     VEL:<v_mmps>,<deg_tenths>,<omega_mradps>
 *         v_mmps      速度大小,mm/s 整数(0.5 m/s -> 500),非负
 *         deg_tenths  方向角,0.1° 整数(90.0° -> 900)
 *         omega_mradps 自转角速度,mrad/s 整数(1.570 rad/s -> 1570)
 *     STOP                              急停
 *     SET:<name>:<value>                 通用参数
 *
 * 设计要点:
 *   - 全整数定点:本文件不做任何浮点解析,MCU 无浮点库也能用
 *   - 协议中立:参考系/正方向/底盘类型(舵轮/麦轮/差速)全在逆解层处理
 *   - 零动态内存:静态行缓冲
 *
 * 用法:
 *   1. UART 接收中断里调用 proto_feed_byte(byte)
 *   2. 实现下方 4 个回调(尤其 chassis_apply_velocity 里做舵轮逆解)
 *   3. 遥测用 proto_send_telemetry_* 上报
 * ========================================================================== */
#include <string.h>
#include <stdint.h>
#include <stdio.h>

/* ------------------------------------------------------------------ */
/* 配置                                                                */
/* ------------------------------------------------------------------ */
#define PROTO_LINE_MAX   64      /* 单行最大长度(含结尾 '\0') */
#define PROTO_V_MAX      32767   /* mm/s 上限(int16) */
#define PROTO_DEG_LIMIT  32767   /* 0.1° 上限 */
#define PROTO_OMEGA_LIMIT 32767  /* mrad/s 上限 */

/* ------------------------------------------------------------------ */
/* 需由使用者实现的接口                                                */
/* ------------------------------------------------------------------ */

/* 应用速度矢量(★ 在此做舵轮/麦轮/差速逆解,并驱动电机)
 *   v_mmps      : 速度大小,mm/s
 *   deg_tenths  : 方向角,0.1°(参考系与正方向由本层定义)
 *   omega_mradps: 自转角速度,mrad/s
 */
void chassis_apply_velocity(int32_t v_mmps, int32_t deg_tenths, int32_t omega_mradps);

/* 急停 */
void chassis_stop(void);

/* 通用参数设置 */
void chassis_set_param(const char *name, int32_t value);

/* 向上位机发送一行(内部自动补 \n) */
void chassis_send_line(const char *text);

/* ------------------------------------------------------------------ */
/* 内部状态                                                            */
/* ------------------------------------------------------------------ */
static char     s_line[PROTO_LINE_MAX];
static uint16_t s_len = 0;
static uint8_t  s_overflow = 0;      /* 本行溢出 -> 丢弃到下一个换行 */

/* ------------------------------------------------------------------ */
/* 工具:整数解析(替代 atoi,严格校验,负号支持)                       */
/* ------------------------------------------------------------------ */
static int parse_int(const char *s, const char *end, int32_t *out)
{
    if (s == NULL || s >= end) return -1;

    const char *p = s;
    int neg = 0;
    if (*p == '-' || *p == '+') { neg = (*p == '-'); p++; }
    if (p >= end) return -1;

    int32_t val = 0;
    int digits = 0;
    while (p < end && *p >= '0' && *p <= '9') {
        val = val * 10 + (*p - '0');
        if (val > 100000000L) return -1;   /* 防溢出 */
        p++; digits++;
    }
    if (digits == 0) return -1;
    if (p != end) return -1;               /* 有非法尾字符 */

    *out = neg ? -val : val;
    return 0;
}

/* 不区分大小写前缀比较 */
static int starts_with_ci(const char *s, const char *prefix)
{
    while (*prefix) {
        char a = *s, b = *prefix;
        if (a >= 'a' && a <= 'z') a -= 32;
        if (b >= 'a' && b <= 'z') b -= 32;
        if (a != b) return 0;
        s++; prefix++;
    }
    return 1;
}

static int32_t clamp_i32(int32_t v, int32_t lo, int32_t hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* ------------------------------------------------------------------ */
/* 单行分发                                                            */
/* ------------------------------------------------------------------ */
static void proto_handle_line(char *line)
{
    /* 去首尾空白与 \r */
    while (*line == ' ' || *line == '\t') line++;
    char *end = line + strlen(line);
    while (end > line && (end[-1] == '\r' || end[-1] == ' ' || end[-1] == '\t')) {
        *--end = '\0';
    }
    if (*line == '\0') return;

    /* ---------------- VEL:<v>,<deg>,<omega> ---------------- */
    if (starts_with_ci(line, "VEL:")) {
        char *p = line + 4;
        char *c1 = strchr(p, ',');
        if (c1 == NULL) { chassis_send_line("ERR:BAD_VEL"); return; }
        char *c2 = strchr(c1 + 1, ',');
        if (c2 == NULL) { chassis_send_line("ERR:BAD_VEL"); return; }

        int32_t v, deg, omega;
        if (parse_int(p, c1, &v) != 0 ||
            parse_int(c1 + 1, c2, &deg) != 0 ||
            parse_int(c2 + 1, end, &omega) != 0) {
            chassis_send_line("ERR:BAD_VEL_NUM");
            return;
        }

        /* 钳位:速度大小非负;角度/角速度按 int16 范围 */
        v     = clamp_i32(v, 0, PROTO_V_MAX);
        deg   = clamp_i32(deg, -PROTO_DEG_LIMIT, PROTO_DEG_LIMIT);
        omega = clamp_i32(omega, -PROTO_OMEGA_LIMIT, PROTO_OMEGA_LIMIT);

        /* ★ 交给你:逆解 + 电机驱动 */
        chassis_apply_velocity(v, deg, omega);
        return;
    }

    /* ---------------- STOP ---------------- */
    if (starts_with_ci(line, "STOP")) {
        chassis_stop();
        return;
    }

    /* ---------------- SET:<name>:<value> ---------------- */
    if (starts_with_ci(line, "SET:")) {
        char *p = line + 4;
        char *colon = strchr(p, ':');
        if (colon == NULL) { chassis_send_line("ERR:BAD_SET"); return; }
        *colon = '\0';
        int32_t value;
        if (parse_int(colon + 1, end, &value) != 0) {
            chassis_send_line("ERR:BAD_SET_NUM");
            return;
        }
        chassis_set_param(p, value);
        return;
    }

    chassis_send_line("ERR:UNKNOWN");
}

/* ------------------------------------------------------------------ */
/* 喂字节接口 —— 在 UART 接收中断 / 主循环里调用                        */
/* ------------------------------------------------------------------ */
void proto_feed_byte(uint8_t byte)
{
    if (byte == '\n') {
        if (!s_overflow) {
            s_line[s_len] = '\0';
            proto_handle_line(s_line);
        }
        s_len = 0;
        s_overflow = 0;
        return;
    }
    if (byte == '\r') return;                 /* 兼容 \r\n */

    if (s_len >= PROTO_LINE_MAX - 1) {
        s_overflow = 1;                       /* 溢出:整行丢弃 */
        return;
    }
    s_line[s_len++] = (char)byte;
}

/* ------------------------------------------------------------------ */
/* 遥测上报                                                            */
/* ------------------------------------------------------------------ */
/* 原始定点值上报(推荐:无浮点) */
void proto_send_telemetry_raw(int32_t v_mmps, int32_t deg_tenths, int32_t omega_mradps)
{
    char buf[80];
    snprintf(buf, sizeof(buf), "TEL:V:%ld,DEG:%ld,OMEGA:%ld",
             (long)v_mmps, (long)deg_tenths, (long)omega_mradps);
    chassis_send_line(buf);
}

/* ==========================================================================
 * 舵轮逆解示例(伪代码,按你的底盘几何修改)
 * --------------------------------------------------------------------------
 * 假设 N 个舵轮,第 i 个轮子安装位置 (xi, yi)(车体坐标系,单位 m),
 * 安装角 offset_i(轮子"零位"方向)。
 *
 * 目标速度矢量(车头坐标系):v (m/s), theta (deg), omega (rad/s)
 *     vx = v * cos(theta)
 *     vy = v * sin(theta)
 *
 * 轮 i 的驱动速度矢量:
 *     vix = vx - omega * yi
 *     viy = vy + omega * xi
 *     speed_i   = sqrt(vix^2 + viy^2)          // 轮子线速度 m/s
 *     steer_i   = atan2(viy, vix) - offset_i   // 舵向角 rad
 *
 * 注意:
 *   - 舵向角需处理 [-pi, pi] 归一化,以及"舵机就近转向"优化
 *     (角度超过 ±90° 时可翻转驱动方向并取补角,减少舵机行程)
 *   - speed_i 换算成电机转速:rpm = speed / (pi * D) * 60
 *
 * 本函数即为 chassis_apply_velocity() 的实现位置:
 *
 *   void chassis_apply_velocity(int32_t v_mmps, int32_t deg_tenths, int32_t omega_mradps)
 *   {
 *       float v     = v_mmps / 1000.0f;
 *       float theta = deg_tenths / 10.0f * (float)M_PI / 180.0f;
 *       float omega = omega_mradps / 1000.0f;
 *       // ... 上述逆解,逐轮设定舵角与转速 ...
 *   }
 * ========================================================================== */

/* ==========================================================================
 * 四轮速度反馈:二进制定长帧(方案 B,11 字节)
 * --------------------------------------------------------------------------
 * 帧格式(与上位机 protocol.py 严格一致):
 *
 *   [0]     0xAA                 帧头
 *   [1]     seq                  序号 0~255 循环递增(上位机据此检测丢帧)
 *   [2:10]  w0 w1 w2 w3          四轮转速,int16 小端,单位 rpm(整数)
 *   [10]    crc8                 CRC-8/ATM(多项式 0x07),覆盖 [0..9]
 *
 * 轮序约定(俯视,车头朝上,逆时针递增):
 *   w0 = 右前(RF)   w1 = 左前(LF)   w2 = 左后(LR)   w3 = 右后(RR)
 *
 * 带宽:@115200 下 11B × 100Hz = 1100 B/s,仅占约 9.5%。
 * ========================================================================== */

#define WHEEL_CNT       4
#define WHEEL_SPEED_K   1.0f      /* rpm -> 线上整数(与上位机 WHEEL_SPEED_SCALE 一致) */

static uint8_t s_wheel_seq = 0;

/* CRC-8/ATM(多项式 0x07,初值 0x00)—— 必须与上位机 crc8() 一致 */
static uint8_t proto_crc8(const uint8_t *data, uint16_t len)
{
    uint8_t crc = 0x00;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

/* 小端写入 int16 */
static void proto_put_i16le(uint8_t *p, int16_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
}

/* 发送一帧四轮速度(单位:rpm)
 *   wheel_rpm[0..3] = 右前,左前,左后,右后 的实测转速(rpm)
 *   依赖外部提供 UART 原始发送:proto_uart_send(buf, len)
 */
void proto_send_wheel_frame(const float wheel_rpm[WHEEL_CNT])
{
    uint8_t buf[11];

    buf[0] = 0xAA;
    buf[1] = s_wheel_seq++;

    for (int i = 0; i < WHEEL_CNT; i++) {
        float scaled = wheel_rpm[i] * WHEEL_SPEED_K;
        /* 钳位到 int16,避免溢出 */
        if (scaled >  32767.0f) scaled =  32767.0f;
        if (scaled < -32768.0f) scaled = -32768.0f;
        proto_put_i16le(&buf[2 + i * 2], (int16_t)scaled);
    }

    buf[10] = proto_crc8(buf, 10);

    proto_uart_send(buf, sizeof(buf));
}

/* 使用示例(建议由定时器/控制周期调用,例如 100Hz):
 *
 *   // 在控制循环(如 1kHz)中分频到 100Hz:
 *   static uint32_t last_fb_ms = 0;
 *   if (HAL_GetTick() - last_fb_ms >= 10) {       // 10ms -> 100Hz
 *       last_fb_ms = HAL_GetTick();
 *       float w[4] = { rpm_RF, rpm_LF, rpm_LR, rpm_RR };   // 右前,左前,左后,右后
 *       proto_send_wheel_frame(w);
 *   }
 */
