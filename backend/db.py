from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta

DB_FILENAME = "cyclesense.db"

_db_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_db_dir, DB_FILENAME)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS measurements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            heart_rate INTEGER,
            hrv_rmssd  REAL,
            timestamp  TEXT
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            date   TEXT,
            phase  TEXT,
            energy INTEGER,
            mood   TEXT,
            helped TEXT,
            hurt   TEXT
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# config 表 —— 单用户 KV 读写
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "last_period": "",
    "cycle_length": "28",
    "regularity": "regular",
    "comm_style": "warm",
    "push_sensitivity": "medium",
    "age_group": "",
    "period_pattern": "",
    "symptoms": "",
    "user_type": "",
}


def get_config(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULT_CONFIG.get(key)
    return row["value"]


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_all_config(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    result: dict[str, str] = {}
    for row in rows:
        result[row["key"]] = row["value"]
    for key, default in DEFAULT_CONFIG.items():
        if key not in result:
            result[key] = default
    return result


# ---------------------------------------------------------------------------
# measurements 表 —— 硬件数据写入与查询
# ---------------------------------------------------------------------------

def insert_measurement(
    conn: sqlite3.Connection, heart_rate: int, hrv_rmssd: float, timestamp: str
) -> int:
    cur = conn.execute(
        "INSERT INTO measurements (heart_rate, hrv_rmssd, timestamp) VALUES (?, ?, ?)",
        (heart_rate, hrv_rmssd, timestamp),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_measurement(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT heart_rate, hrv_rmssd, timestamp FROM measurements ORDER BY id DESC LIMIT 1"
    ).fetchone()


def get_previous_measurement(conn: sqlite3.Connection):
    """获取今天之前最近一条有效测量记录，用于计算 prev_hrv_ratio。

    有效条件：hrv_rmssd > 0 AND heart_rate <= 110。
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return conn.execute(
        "SELECT heart_rate, hrv_rmssd, timestamp FROM measurements "
        "WHERE timestamp < ? AND hrv_rmssd > 0 AND heart_rate <= 110 "
        "ORDER BY id DESC LIMIT 1",
        (today_start,),
    ).fetchone()


def get_valid_measurement_count(conn: sqlite3.Connection) -> int:
    """返回有效测量记录的总条数（hrv_rmssd > 0 AND heart_rate <= 110）。

    供 _calc_cycle_confidence() 使用。
    """
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM measurements "
        "WHERE hrv_rmssd > 0 AND heart_rate <= 110"
    ).fetchone()
    return row["cnt"]


# ---------------------------------------------------------------------------
# feedback 表 —— 每日反馈写入
# ---------------------------------------------------------------------------

def insert_feedback(
    conn: sqlite3.Connection,
    date: str,
    phase: str,
    energy: int | None,
    mood: str,
    helped: str,
    hurt: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO feedback (date, phase, energy, mood, helped, hurt) VALUES (?, ?, ?, ?, ?, ?)",
        (date, phase, energy, mood, helped, hurt),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# HRV 基线动态计算  ——  §7.5.2  ①②
# ---------------------------------------------------------------------------

def get_baseline(db_conn: sqlite3.Connection) -> float:
    """计算个人 HRV (RMSSD) 基线。

    有效数据定义：hrv_rmssd > 0 AND heart_rate <= 110。
    N < 10  → 返回冷启动默认值 50.0。
    10 ≤ N < 20 → 取全部有效数据的均值。
    N ≥ 20 → 取最近 20 条有效数据的滑动窗口均值。
    """
    row = db_conn.execute(
        "SELECT COUNT(*) AS cnt FROM measurements "
        "WHERE hrv_rmssd > 0 AND heart_rate <= 110"
    ).fetchone()
    n = row["cnt"]

    if n < 10:
        return 50.0

    if n < 20:
        row = db_conn.execute(
            "SELECT AVG(hrv_rmssd) AS avg_hrv FROM measurements "
            "WHERE hrv_rmssd > 0 AND heart_rate <= 110"
        ).fetchone()
        return float(row["avg_hrv"])

    row = db_conn.execute(
        "SELECT AVG(hrv_rmssd) AS avg_hrv FROM ("
        "  SELECT hrv_rmssd FROM measurements "
        "  WHERE hrv_rmssd > 0 AND heart_rate <= 110 "
        "  ORDER BY id DESC LIMIT 20"
        ")"
    ).fetchone()
    return float(row["avg_hrv"])


# ---------------------------------------------------------------------------
# 静息心率基线  ——  §7.5.2  ③  (v8 新增)
# ---------------------------------------------------------------------------

def get_hr_baseline(db_conn: sqlite3.Connection) -> float:
    """计算个人静息心率基线，作为 RHR 辅助信号的基准。

    有效数据定义：heart_rate BETWEEN 30 AND 220 AND heart_rate <= 110。
    N < 10  → 返回冷启动默认值 72.0 BPM。
    10 ≤ N < 20 → 取全部有效数据的均值。
    N ≥ 20 → 取最近 20 条有效数据的滑动窗口均值。
    """
    row = db_conn.execute(
        "SELECT COUNT(*) AS cnt FROM measurements "
        "WHERE heart_rate BETWEEN 30 AND 220 AND heart_rate <= 110"
    ).fetchone()
    n = row["cnt"]

    if n < 10:
        return 72.0

    if n < 20:
        row = db_conn.execute(
            "SELECT AVG(heart_rate) AS avg_hr FROM measurements "
            "WHERE heart_rate BETWEEN 30 AND 220 AND heart_rate <= 110"
        ).fetchone()
        return float(row["avg_hr"])

    row = db_conn.execute(
        "SELECT AVG(heart_rate) AS avg_hr FROM ("
        "  SELECT heart_rate FROM measurements "
        "  WHERE heart_rate BETWEEN 30 AND 220 AND heart_rate <= 110 "
        "  ORDER BY id DESC LIMIT 20"
        ")"
    ).fetchone()
    return float(row["avg_hr"])


# ---------------------------------------------------------------------------
# 反馈搜索  ——  §7.5.2  ③   /   §7.3.2  ⑦
# ---------------------------------------------------------------------------

def search_feedback(
    conn: sqlite3.Connection, query: str, current_phase: str
) -> list[sqlite3.Row]:
    keywords = [kw.strip() for kw in query.replace("+", " ").split() if kw.strip()]
    if not keywords:
        return []

    cutoff_date = (datetime.utcnow().date() - timedelta(days=60)).isoformat()

    if current_phase == "unknown":
        conditions = ["fb.mood LIKE ?" for _ in keywords]
        params = [f"%{kw}%" for kw in keywords]
        sql = (
            "SELECT * FROM feedback AS fb WHERE fb.date >= ? AND ("
            + " AND ".join(conditions)
            + ") ORDER BY fb.id DESC LIMIT 20"
        )
        params = [cutoff_date] + params
    else:
        conditions = []
        for kw in keywords:
            conditions.append(
                "(fb.mood LIKE ? OR fb.helped LIKE ? OR fb.hurt LIKE ?)"
            )
        params = []
        like_patterns = [f"%{kw}%" for kw in keywords]
        for p in like_patterns:
            params.extend([p, p, p])

        sql = (
            "SELECT * FROM feedback AS fb WHERE fb.phase = ? AND ("
            + " AND ".join(conditions)
            + ") ORDER BY fb.id DESC LIMIT 20"
        )
        params = [current_phase] + params

    return conn.execute(sql, params).fetchall()
