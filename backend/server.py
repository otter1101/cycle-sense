from __future__ import annotations
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.db import (
    get_connection,
    init_db,
    get_config,
    set_config,
    get_all_config,
    insert_measurement,
    get_latest_measurement,
    get_previous_measurement,
    get_valid_measurement_count,
    insert_feedback,
    get_baseline,
    get_hr_baseline,
    search_feedback,
)
from backend.engine import CycleEngine

# ---------------------------------------------------------------------------
#  App  &  CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="CycleSense", version="9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
#  全局单例
# ---------------------------------------------------------------------------

engine = CycleEngine()

# --- 心跳去重内存缓存 ------------------------------------------------------
_last_hb: dict = {
    "id": None,
    "timestamp": None,
    "heart_rate": None,
    "hrv_rmssd": None,
}

# --- 测量状态机 ------------------------------------------------------------
# 空闲 → start-test → 测量中（持续返回 start）→ stop-test 或超时 → 空闲
MEASUREMENT_ACTIVE: bool = False
MEASUREMENT_START_TIME: float = 0.0
MEASUREMENT_TIMEOUT: int = 40  # 秒，前端30秒动画结束会主动stop，此为保底

# ---------------------------------------------------------------------------
#  枚举常量
# ---------------------------------------------------------------------------

VALID_REGULARITY = {"regular", "irregular", "pcos", "perimenopause"}
VALID_AGE_GROUP = {"12-18", "18-28", "28-36", "36-45", "45+"}
VALID_PERIOD_PATTERN = {"regular", "somewhat_irregular", "very_irregular", "changing"}
VALID_SYMPTOMS = {"热醒/出汗", "心跳加速", "忘事/注意力", "情绪波动"}
VALID_COMM_STYLE = {"direct", "warm", "humorous"}
VALID_PUSH_SENSITIVITY = {"high", "medium", "low"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
#  工具函数
# ---------------------------------------------------------------------------

def _err(code: str, message: str) -> dict:
    return {"status": "error", "code": code, "message": message}


def _is_configured(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM config WHERE key = 'regularity'").fetchone()
    return row is not None and row["value"] != ""


def _parse_last_period(raw: str | None) -> date | None:
    if raw is None or raw == "":
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_cycle_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        v = int(raw)
        return v if 21 <= v <= 90 else None
    except (ValueError, TypeError):
        return None


def _humanize_ago(ts_str: str | None) -> str:
    if ts_str is None:
        return "尚未测量"
    try:
        measured_at = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return "尚未测量"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = now - measured_at
    seconds = delta.total_seconds()

    if seconds < 0:
        return "刚刚"
    if seconds < 60:
        return "刚刚"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}小时前"
    days = delta.days
    if days <= 30:
        return f"{days}天前"
    return "超过30天未测"


def _regularity_to_user_type_str(regularity: str) -> str:
    """将 regularity 值映射为 user_type 字符串。"""
    mapping = {
        "regular": "young_regular",
        "irregular": "young_regular",
        "pcos": "young_pcos",
        "perimenopause": "perimenopause",
    }
    return mapping.get(regularity, regularity)


# ---------------------------------------------------------------------------
#  determine_user_type  ——  v9 新增
# ---------------------------------------------------------------------------

def determine_user_type(
    age_group: str, period_pattern: str, symptoms_list: list[str]
) -> str:
    """根据年龄 + 月经情况 + 症状，自动判定 regularity 值。

    返回 "regular" / "irregular" / "pcos" / "perimenopause"。
    """
    symptom_count = len(symptoms_list)

    # 围绝经期判定
    if age_group == "45+" and (period_pattern == "changing" or symptom_count >= 2):
        return "perimenopause"
    if age_group == "36-45" and period_pattern == "changing" and symptom_count >= 2:
        return "perimenopause"

    # 不规律用户保留（HRV 权重 0.6）
    if period_pattern == "somewhat_irregular":
        return "irregular"

    # PCOS 判定
    if age_group in ("12-18", "18-28", "28-36") and period_pattern == "very_irregular":
        return "pcos"
    if age_group == "36-45" and period_pattern == "very_irregular" and symptom_count < 2:
        return "pcos"

    # 其余 → regular
    return "regular"


# ---------------------------------------------------------------------------
#  get_recent_trend  ——  v8 新增
# ---------------------------------------------------------------------------

def get_recent_trend(db_conn: sqlite3.Connection) -> str:
    """取最近 3 个自然日（不含今天）每天最后一次测量的 hrv_rmssd，判断趋势。

    返回 "declining" / "rising" / "stable" / "insufficient_data"。
    """
    today = datetime.utcnow().date()
    daily_vals: list[float] = []

    for offset in range(1, 4):  # 昨天、前天、大前天
        target_date = today - timedelta(days=offset)
        day_start = datetime(
            target_date.year, target_date.month, target_date.day
        ).isoformat()
        day_end = datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59
        ).isoformat()

        row = db_conn.execute(
            "SELECT hrv_rmssd FROM measurements "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "AND hrv_rmssd > 0 AND heart_rate <= 110 "
            "ORDER BY id DESC LIMIT 1",
            (day_start, day_end),
        ).fetchone()

        if row and row["hrv_rmssd"] is not None:
            daily_vals.append(float(row["hrv_rmssd"]))

    if len(daily_vals) < 3:
        return "insufficient_data"

    # daily_vals 收集顺序：昨天(0)、前天(1)、大前天(2)
    # 翻转为时间正序：大前天(0)、前天(1)、昨天(2)
    vals = list(reversed(daily_vals))

    # 连续下降：每天比前一天低 5%+
    if all(vals[i] < vals[i - 1] * 0.95 for i in range(1, len(vals))):
        return "declining"

    # 连续上升：每天比前一天高 5%+
    if all(vals[i] > vals[i - 1] * 1.05 for i in range(1, len(vals))):
        return "rising"

    return "stable"


# ---------------------------------------------------------------------------
#  引擎入参装配器  ——  v8+v9 新增
# ---------------------------------------------------------------------------

def _assemble_engine_inputs(
    conn: sqlite3.Connection,
    cfg: dict[str, str],
    latest,
) -> dict:
    """从数据库装配 engine.calculate() 所需的全部 12 个入参。

    返回字典，可直接 ** 解包传入 engine.calculate()。
    """
    last_period = _parse_last_period(cfg.get("last_period"))
    cycle_length = _parse_cycle_length(cfg.get("cycle_length")) or 28
    regularity = cfg.get("regularity", "regular")

    if latest:
        hrv_current = (
            float(latest["hrv_rmssd"])
            if latest["hrv_rmssd"] is not None
            else None
        )
        hr_current = latest["heart_rate"]
    else:
        hrv_current = None
        hr_current = None

    hrv_baseline = get_baseline(conn)
    hr_baseline = get_hr_baseline(conn)
    recent_hrv_trend = get_recent_trend(conn)
    measurement_count = get_valid_measurement_count(conn)

    # prev_hrv_ratio：今天之前最近一条有效记录的 hrv / baseline
    prev_meas = get_previous_measurement(conn)
    if (
        prev_meas
        and prev_meas["hrv_rmssd"] is not None
        and hrv_baseline > 0
    ):
        prev_hrv_ratio = float(prev_meas["hrv_rmssd"]) / hrv_baseline
    else:
        prev_hrv_ratio = None

    # hours_since_last_measurement
    if latest:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        measured_at = datetime.fromisoformat(latest["timestamp"])
        hours_since = (now - measured_at).total_seconds() / 3600.0
    else:
        hours_since = 999.0

    # symptoms_list：从 config 逗号分隔字符串还原为 Python 列表
    symptoms_raw = cfg.get("symptoms", "")
    if symptoms_raw:
        symptoms_list = [
            s.strip() for s in symptoms_raw.split(",") if s.strip()
        ]
    else:
        symptoms_list = []

    return {
        "last_period": last_period,
        "cycle_length": cycle_length,
        "regularity": regularity,
        "hrv_current": hrv_current,
        "hr_current": hr_current,
        "hrv_baseline": hrv_baseline,
        "hr_baseline": hr_baseline,
        "recent_hrv_trend": recent_hrv_trend,
        "prev_hrv_ratio": prev_hrv_ratio,
        "hours_since_last_measurement": hours_since,
        "measurement_count": measurement_count,
        "symptoms_list": symptoms_list,
    }


# ---------------------------------------------------------------------------
#  启动事件 —— 建表
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup():
    conn = get_connection()
    try:
        init_db(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  请求模型
# ---------------------------------------------------------------------------

class SetupBody(BaseModel):
    age_group: str | None = None
    period_pattern: str | None = None
    symptoms: list[str] | None = None
    last_period: str | None = None
    cycle_length: int | None = None
    comm_style: str | None = None
    push_sensitivity: str | None = None


class HeartbeatBody(BaseModel):
    heart_rate: int
    hrv_rmssd: float


class LogBody(BaseModel):
    mood: str = ""
    helped: str = ""
    hurt: str = ""


# ===================================================================
#  ①  POST /api/setup
# ===================================================================

@app.post("/api/setup")
def api_setup(body: SetupBody):
    conn = get_connection()
    try:
        init_db(conn)
        configured = _is_configured(conn)

        if not configured:
            # ====== 首次设置 —— v9 硬校验 ======
            if body.age_group not in VALID_AGE_GROUP:
                return _err("INVALID_PARAM", "首次设置缺少必填字段或值不合法")
            if body.period_pattern not in VALID_PERIOD_PATTERN:
                return _err("INVALID_PARAM", "首次设置缺少必填字段或值不合法")

            symptoms = body.symptoms or []
            invalid_symptoms = [s for s in symptoms if s not in VALID_SYMPTOMS]
            if invalid_symptoms:
                return _err("INVALID_PARAM", "症状选项不合法")

            # 自动判定 regularity + user_type
            regularity = determine_user_type(
                body.age_group, body.period_pattern, symptoms
            )
            user_type = _regularity_to_user_type_str(regularity)

            # 持久化核心字段
            set_config(conn, "age_group", body.age_group)
            set_config(conn, "period_pattern", body.period_pattern)
            set_config(conn, "symptoms", ",".join(symptoms))
            set_config(conn, "regularity", regularity)
            set_config(conn, "user_type", user_type)

            # 可选字段：last_period / cycle_length
            if body.last_period is not None:
                if body.last_period == "":
                    set_config(conn, "last_period", "")
                elif DATE_RE.match(body.last_period):
                    set_config(conn, "last_period", body.last_period)
            if body.cycle_length is not None and 21 <= body.cycle_length <= 90:
                set_config(conn, "cycle_length", str(body.cycle_length))

            # 偏好字段（首次未传入时使用默认值）
            comm = (
                body.comm_style
                if body.comm_style in VALID_COMM_STYLE
                else "warm"
            )
            push = (
                body.push_sensitivity
                if body.push_sensitivity in VALID_PUSH_SENSITIVITY
                else "medium"
            )
            set_config(conn, "comm_style", comm)
            set_config(conn, "push_sensitivity", push)

        else:
            # ====== 后续调用 —— PATCH 语义 ======
            updates: dict[str, str] = {}

            if body.age_group is not None and body.age_group in VALID_AGE_GROUP:
                updates["age_group"] = body.age_group
            if body.period_pattern is not None and body.period_pattern in VALID_PERIOD_PATTERN:
                updates["period_pattern"] = body.period_pattern
            if body.symptoms is not None:
                invalid = [s for s in body.symptoms if s not in VALID_SYMPTOMS]
                if not invalid:
                    updates["symptoms"] = ",".join(body.symptoms)
            if body.last_period is not None:
                if body.last_period == "":
                    updates["last_period"] = ""
                elif DATE_RE.match(body.last_period):
                    updates["last_period"] = body.last_period
            if body.cycle_length is not None and 21 <= body.cycle_length <= 90:
                updates["cycle_length"] = str(body.cycle_length)
            if body.comm_style is not None and body.comm_style in VALID_COMM_STYLE:
                updates["comm_style"] = body.comm_style
            if body.push_sensitivity is not None and body.push_sensitivity in VALID_PUSH_SENSITIVITY:
                updates["push_sensitivity"] = body.push_sensitivity

            for k, v in updates.items():
                set_config(conn, k, v)

            # 若年龄/月经模式/症状变更，重新判定 regularity
            if (
                "age_group" in updates
                or "period_pattern" in updates
                or "symptoms" in updates
            ):
                cfg = get_all_config(conn)
                age = cfg.get("age_group", "")
                pp = cfg.get("period_pattern", "")
                sym_raw = cfg.get("symptoms", "")
                if sym_raw:
                    sym_list = [
                        s.strip() for s in sym_raw.split(",") if s.strip()
                    ]
                else:
                    sym_list = []
                if age in VALID_AGE_GROUP and pp in VALID_PERIOD_PATTERN:
                    regularity = determine_user_type(age, pp, sym_list)
                    user_type = _regularity_to_user_type_str(regularity)
                    set_config(conn, "regularity", regularity)
                    set_config(conn, "user_type", user_type)

        return {"status": "ok"}
    finally:
        conn.close()


# ===================================================================
#  ②  POST /api/heartbeat
# ===================================================================

@app.post("/api/heartbeat")
def api_heartbeat(body: HeartbeatBody):
    # 参数合法性校验
    if not (30 <= body.heart_rate <= 220) or body.hrv_rmssd <= 0:
        return _err("INVALID_PARAM", "心率或HRV数值不合法")

    conn = get_connection()
    try:
        init_db(conn)

        now_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # 去重：与内存缓存中的最新记录比较
        if _last_hb["id"] is not None:
            try:
                prev_ts = datetime.fromisoformat(_last_hb["timestamp"])
                curr_ts = datetime.fromisoformat(now_ts)
                diff = abs((curr_ts - prev_ts).total_seconds())
                if (
                    diff <= 2
                    and _last_hb["heart_rate"] == body.heart_rate
                    and _last_hb["hrv_rmssd"] == body.hrv_rmssd
                ):
                    return {"status": "ok"}
            except (ValueError, TypeError):
                pass

        new_id = insert_measurement(
            conn, body.heart_rate, body.hrv_rmssd, now_ts
        )

        # 更新内存缓存
        _last_hb["id"] = new_id
        _last_hb["timestamp"] = now_ts
        _last_hb["heart_rate"] = body.heart_rate
        _last_hb["hrv_rmssd"] = body.hrv_rmssd

        return {"status": "ok"}
    finally:
        conn.close()


# ===================================================================
#  ②-bis  POST /api/start-test  ——  前端"开始测量"按钮
# ===================================================================

@app.post("/api/start-test")
def api_start_test():
    global MEASUREMENT_ACTIVE, MEASUREMENT_START_TIME
    MEASUREMENT_ACTIVE = True
    MEASUREMENT_START_TIME = time.time()
    return {"status": "ok", "code": "SUCCESS", "message": "Trigger captured"}


# ===================================================================
#  ②-ter  POST /api/stop-test  ——  前端"停止"或自动超时
# ===================================================================

@app.post("/api/stop-test")
def api_stop_test():
    global MEASUREMENT_ACTIVE, MEASUREMENT_START_TIME
    MEASUREMENT_ACTIVE = False
    MEASUREMENT_START_TIME = 0.0
    return {"status": "ok", "code": "SUCCESS", "message": "Measurement stopped"}


# ===================================================================
#  ②-qua  GET /api/device-command  ——  硬件高频轮询
# ===================================================================

@app.get("/api/device-command")
def api_device_command():
    global MEASUREMENT_ACTIVE, MEASUREMENT_START_TIME
    # 超时自动停止
    if MEASUREMENT_ACTIVE and time.time() - MEASUREMENT_START_TIME > MEASUREMENT_TIMEOUT:
        MEASUREMENT_ACTIVE = False
        MEASUREMENT_START_TIME = 0.0
    if MEASUREMENT_ACTIVE:
        return {"command": "start"}
    return {"command": "idle"}


# ===================================================================
#  ③  GET /api/status
# ===================================================================

@app.get("/api/status")
def api_status():
    conn = get_connection()
    try:
        init_db(conn)

        if not _is_configured(conn):
            return _err("NOT_CONFIGURED", "请先完成首次设置")

        cfg = get_all_config(conn)
        latest = get_latest_measurement(conn)

        # 装配引擎入参
        engine_inputs = _assemble_engine_inputs(conn, cfg, latest)

        result = engine.calculate(**engine_inputs)

        # 若引擎返回错误（如未来日期），直接透传
        if isinstance(result, dict) and result.get("status") == "error":
            return result

        # last_measured_ago
        last_ts = latest["timestamp"] if latest else None
        last_measured_ago = _humanize_ago(last_ts)

        # user_preferences
        user_prefs = {
            "comm_style": cfg.get("comm_style", "warm"),
            "push_sensitivity": cfg.get("push_sensitivity", "medium"),
        }

        # user_context（v9 新增）
        symptoms_raw = cfg.get("symptoms", "")
        if symptoms_raw:
            reported_symptoms = [
                s.strip() for s in symptoms_raw.split(",") if s.strip()
            ]
        else:
            reported_symptoms = []

        user_context = {
            "age_group": cfg.get("age_group", ""),
            "reported_symptoms": reported_symptoms,
        }

        return {
            "status": "ok",
            "data": {
                **result,
                "last_measured_ago": last_measured_ago,
                "user_preferences": user_prefs,
                "user_context": user_context,
            },
        }
    finally:
        conn.close()


# ===================================================================
#  ④  POST /api/mark_period
# ===================================================================

@app.post("/api/mark_period")
def api_mark_period():
    conn = get_connection()
    try:
        init_db(conn)

        if not _is_configured(conn):
            return _err("NOT_CONFIGURED", "请先完成首次设置")

        today_str = date.today().isoformat()
        set_config(conn, "last_period", today_str)

        return {"status": "ok", "message": f"已标记{today_str}为月经第1天"}
    finally:
        conn.close()


# ===================================================================
#  ⑤  POST /api/log
# ===================================================================

@app.post("/api/log")
def api_log(body: LogBody):
    conn = get_connection()
    try:
        init_db(conn)

        if not _is_configured(conn):
            return _err("NOT_CONFIGURED", "请先完成首次设置")

        cfg = get_all_config(conn)
        latest = get_latest_measurement(conn)

        engine_inputs = _assemble_engine_inputs(conn, cfg, latest)
        status = engine.calculate(**engine_inputs)

        # 若引擎返回错误，不写入 feedback
        if isinstance(status, dict) and status.get("status") == "error":
            return _err("INVALID_PARAM", "当前状态异常，无法记录反馈")

        phase = status["phase"]
        energy = status["energy"]

        today_str = date.today().isoformat()
        insert_feedback(
            conn, today_str, phase, energy, body.mood, body.helped, body.hurt
        )

        return {"status": "ok"}
    finally:
        conn.close()


# ===================================================================
#  ⑥  GET /api/recommend_task
# ===================================================================

@app.get("/api/recommend_task")
def api_recommend_task(
    task_description: str = Query(..., description="任务描述")
):
    conn = get_connection()
    try:
        init_db(conn)

        if not _is_configured(conn):
            return _err("NOT_CONFIGURED", "请先完成首次设置")

        cfg = get_all_config(conn)
        latest = get_latest_measurement(conn)

        engine_inputs = _assemble_engine_inputs(conn, cfg, latest)
        status = engine.calculate(**engine_inputs)

        if isinstance(status, dict) and status.get("status") == "error":
            return _err("INVALID_PARAM", "当前状态异常，无法评估任务")

        task_result = engine.recommend_task(task_description, status)
        return {"status": "ok", "data": task_result}
    finally:
        conn.close()


# ===================================================================
#  ⑦  GET /api/history_pattern
# ===================================================================

@app.get("/api/history_pattern")
def api_history_pattern(
    query: str = Query(..., description="搜索关键词，多词用空格或加号分隔")
):
    conn = get_connection()
    try:
        init_db(conn)

        if not _is_configured(conn):
            return _err("NOT_CONFIGURED", "请先完成首次设置")

        cfg = get_all_config(conn)
        latest = get_latest_measurement(conn)

        engine_inputs = _assemble_engine_inputs(conn, cfg, latest)
        status = engine.calculate(**engine_inputs)

        if isinstance(status, dict) and status.get("status") == "error":
            current_phase = "unknown"
        else:
            current_phase = status["phase"]

        rows = search_feedback(conn, query, current_phase)

        # 去重统计：同一 date 下相同策略文本仅计 1 次
        effective: dict[str, int] = {}
        ineffective: dict[str, int] = {}
        seen_helped: set[tuple[str, str]] = set()
        seen_hurt: set[tuple[str, str]] = set()

        for row in rows:
            row_date = row["date"]
            if row["helped"]:
                for s in row["helped"].split(","):
                    s = s.strip()
                    if s and (row_date, s) not in seen_helped:
                        seen_helped.add((row_date, s))
                        effective[s] = effective.get(s, 0) + 1
            if row["hurt"]:
                for s in row["hurt"].split(","):
                    s = s.strip()
                    if s and (row_date, s) not in seen_hurt:
                        seen_hurt.add((row_date, s))
                        ineffective[s] = ineffective.get(s, 0) + 1

        top_effective = sorted(
            effective.items(), key=lambda x: (-x[1], x[0])
        )[:5]
        top_ineffective = sorted(
            ineffective.items(), key=lambda x: (-x[1], x[0])
        )[:5]

        return {
            "status": "ok",
            "data": {
                "query": query,
                "total_feedback": len(rows),
                "effective_strategies": [
                    {"strategy": s, "count": c} for s, c in top_effective
                ],
                "ineffective_strategies": [
                    {"strategy": s, "count": c} for s, c in top_ineffective
                ],
            },
        }
    finally:
        conn.close()
