import logging
import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
#  日志 —— 全部走 stderr，绝不禁用 stdio JSON-RPC 管道
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="[cyclesense-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("cyclesense.mcp")

# ---------------------------------------------------------------------------
#  FastMCP 实例
# ---------------------------------------------------------------------------
server = FastMCP("cyclesense")

# 后端地址（同机部署，硬编码）
BACKEND_URL = "http://127.0.0.1:8000"
HTTPX_TIMEOUT = 10.0

# SKILL.md 绝对路径
SKILL_PATH = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"

# ---------------------------------------------------------------------------
#  通用 HTTP 异步辅助
# ---------------------------------------------------------------------------


async def _async_get(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as cli:
            resp = await cli.get(f"{BACKEND_URL}{path}", params=params)
            return resp.json()
    except httpx.RequestError as exc:
        logger.warning("GET %s failed: %s", path, exc)
        return {
            "status": "error",
            "code": "BACKEND_UNREACHABLE",
            "message": "后端服务不可达，请确认已执行 cyclesense start",
        }


async def _async_post(path: str, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as cli:
            resp = await cli.post(f"{BACKEND_URL}{path}", json=body)
            return resp.json()
    except httpx.RequestError as exc:
        logger.warning("POST %s failed: %s", path, exc)
        return {
            "status": "error",
            "code": "BACKEND_UNREACHABLE",
            "message": "后端服务不可达，请确认已执行 cyclesense start",
        }


# ===================================================================
#  Resource:  cyclesense://skill
# ===================================================================

@server.resource("cyclesense://skill")
async def get_skill() -> str:
    skill_text = ""
    try:
        if SKILL_PATH.exists():
            skill_text = SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read SKILL.md")

    # 尝试拼接当前状态摘要
    status = await _async_get("/api/status")
    if status.get("status") == "ok":
        d = status["data"]

        # PCOS / 围绝经期防御：day_in_cycle 为 null 时不显示 (Day X)
        if d.get("day_in_cycle") is not None:
            day_info = f" (Day {d['day_in_cycle']})"
        else:
            day_info = ""

        # energy 为 null 时不显示具体数字
        if d.get("energy") is not None:
            energy_display = f"{d['energy']}%"
        else:
            energy_display = "数据不足"

        suitable = ", ".join(d["recommendations"]["suitable_tasks"])
        avoid = ", ".join(d["recommendations"]["avoid_tasks"])

        summary = f"""
---
##  当前用户状态（自动生成）
-  精力：{energy_display}
-  阶段：{d['phase_display']}{day_info}
-  认知状态：{d['cognitive_state']}
-  上次测量：{d['last_measured_ago']}
-  confidence：{d['confidence']}
-  适合：{suitable}
-  避免：{avoid}
-  沟通偏好：{d['user_preferences']['comm_style']}
"""

        # v9 新增：围绝经期分支 —— 追加专属向导文本
        if d.get("user_type") == "perimenopause":
            symptoms = ", ".join(
                d.get("user_context", {}).get("reported_symptoms", [])
            )
            summary += f"""
- 支持模式：围绝经期支持
- 已记录症状：{symptoms}
- 注意：语气保持温和尊重，不使用'更年期/绝经'标签词，不诊断，不归因激素。
"""

        return skill_text + summary

    return skill_text


# ===================================================================
#  Tool ①  get_cycle_status
# ===================================================================

@server.tool()
async def get_cycle_status() -> dict:
    """获取用户当前完整的周期状态、精力值、HRV数据和阶段策略建议。AI在给日程/任务/健康建议前应调用此工具。"""
    status = await _async_get("/api/status")
    if status.get("status") == "ok":
        return status["data"]
    return status


# ===================================================================
#  Tool ②  get_energy_level
# ===================================================================

@server.tool()
async def get_energy_level() -> dict:
    """轻量获取用户当前精力值及中文摘要，适合只需精力数字不需完整状态的场景。"""
    status = await _async_get("/api/status")
    if status.get("status") != "ok":
        return status

    d = status["data"]
    energy = d.get("energy")

    if energy is None:
        summary = "数据不足，建议先完成首次测量"
    elif energy >= 75:
        summary = "高精力状态"
    elif energy >= 50:
        summary = "中等精力"
    elif energy >= 30:
        summary = "低精力状态，建议减少决策类任务"
    else:
        summary = "极低精力，建议休息"

    return {"energy": energy, "summary": summary}


# ===================================================================
#  Tool ③  get_task_recommendation
# ===================================================================

@server.tool()
async def get_task_recommendation(task_description: str) -> dict:
    """评估某个任务当前是否适合做。返回适合度判定(suitable/caution/not_recommended)、原因和替代建议。

    Args:
        task_description: 用户想做的任务描述，如"写季度总结"或"打扫房间"
    """
    result = await _async_get(
        "/api/recommend_task", params={"task_description": task_description}
    )
    if result.get("status") == "ok":
        return result["data"]
    return result


# ===================================================================
#  Tool ④  get_historical_pattern
# ===================================================================

@server.tool()
async def get_historical_pattern(query: str) -> dict:
    """查询历史反馈中有效的做法和无效的做法。按当前周期阶段精准过滤后做关键词模糊匹配。

    Args:
        query: 搜索关键词，多词用空格或加号分隔，如"黄体晚期 高压"或"疲劳+压力"
    """
    result = await _async_get("/api/history_pattern", params={"query": query})
    if result.get("status") != "ok":
        return result

    d = result["data"]
    effective_lines = [
        f"  - {item['strategy']}（{item['count']}次）"
        for item in d["effective_strategies"]
    ]
    ineffective_lines = [
        f"  - {item['strategy']}（{item['count']}次）"
        for item in d["ineffective_strategies"]
    ]

    text = f"查询「{d['query']}」共匹配 {d['total_feedback']} 条历史记录\n"
    if effective_lines:
        text += "有效做法：\n" + "\n".join(effective_lines) + "\n"
    if ineffective_lines:
        text += "无效做法：\n" + "\n".join(ineffective_lines)
    if not effective_lines and not ineffective_lines:
        text += "暂无匹配的历史模式"

    return {"text": text.strip(), "data": d}


# ===================================================================
#  Tool ⑤  log_feedback
# ===================================================================

@server.tool()
async def log_feedback(mood: str = "", helped: str = "", hurt: str = "") -> dict:
    """记录今日心情与做法反馈，供后续查询历史模式使用。

    Args:
        mood: 心情描述，如"有点累"
        helped: 什么做法有效，逗号分隔，如"散步,提前睡觉"
        hurt: 什么做法无效/加重疲劳，逗号分隔，如"硬撑着开会"
    """
    body = {"mood": mood, "helped": helped, "hurt": hurt}
    return await _async_post("/api/log", body)


# ===================================================================
#  入口
# ===================================================================

if __name__ == "__main__":
    server.run()
