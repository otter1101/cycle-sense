from __future__ import annotations
import math
from datetime import date
from typing import Any


class CycleEngine:
    """无状态推算引擎 —— §7.4  (v9.0)

    所有方法均为纯计算，不产生副作用，不访问数据库。
    调用方负责从 config / measurements 表读取入参后传入。
    """

    # ---- 阶段显示名映射 ------------------------------------------------
    PHASE_DISPLAY: dict[str, str] = {
        "menstrual":    "月经期",
        "follicular":   "卵泡期",
        "ovulatory":    "排卵期",
        "luteal_early": "黄体前期",
        "luteal_late":  "黄体晚期",
        "unknown":      "数据感知阶段",
    }

    # ---- 正常周期（cycle_length ≤ 35）比例法阶段映射 --------------------
    # (ratio_upper_bound, phase_key, base_energy)
    _NORMAL_PHASE_RULES: list[tuple[float, str, int]] = [
        (0.18, "menstrual",    35),
        (0.46, "follicular",   75),
        (0.57, "ovulatory",    90),
        (0.78, "luteal_early", 60),
        (1.00, "luteal_late",  45),
    ]

    # ---- 长周期（cycle_length > 35）绝对天数锚定法 ----------------------
    # (day_upper_bound, phase_key, base_energy)
    _LONG_CYCLE_ANCHOR_RULES: list[tuple[int, str, int]] = [
        (5,  "menstrual",    35),
        (14, "follicular",   75),
        (17, "ovulatory",    90),
        (28, "luteal_early", 60),
    ]
    # Day 29+ → luteal_late, base_energy = 45

    # ---- Step 3：精力 → 认知状态 + 策略（普通用户）-----------------------
    _ENERGY_TIER: list[tuple[int, int, str, list[str], list[str], str]] = [
        # (lo, hi, cognitive_state, suitable, avoid, instruction_to_agent)
        (
            75, 100,
            "高认知负荷",
            ["学新技能", "创意工作", "重要决策", "启动新项目", "演讲/谈判", "高强度协作"],
            [],
            "正常回答，可鼓励挑战",
        ),
        (
            50, 74,
            "中等认知负荷",
            ["收尾任务", "执行确定性工作", "轻量整理", "常规会议"],
            ["启动全新大项目", "高强度运动"],
            "建议不超过5条，适合常规执行",
        ),
        (
            30, 49,
            "低认知负荷",
            ["已想清楚的执行", "散步", "复盘", "规划"],
            ["多线程", "重大决策", "高情绪消耗对话"],
            "回答控制在3条以内。不用\"应该/必须\"。先共情再方案。",
        ),
        (
            10, 29,
            "极低/休息",
            ["休息", "极轻量活动"],
            ["所有高负荷任务"],
            "不主动安排任务。\"今天最重要的任务是照顾自己\"",
        ),
    ]

    # ---- Step 3：精力 → 策略（围绝经期专属）—— v9 改动5 -----------------
    _PERIMENOPAUSE_TIER: list[tuple[int, int, str, list[str], list[str], str]] = [
        (
            75, 100,
            "高认知负荷",
            ["重要决策", "创意工作", "社交沟通"],
            [],
            "状态好，抓住窗口处理重要事。",
        ),
        (
            50, 74,
            "中等认知负荷",
            ["确定性工作", "常规执行", "轻量沟通"],
            ["启动新项目", "高强度多线程"],
            "稳定执行，不加码。",
        ),
        (
            30, 49,
            "低认知负荷",
            ["已想清楚的执行", "轻量整理", "散步"],
            ["重大决策", "多线程", "高情绪消耗对话", "需快速记忆的任务"],
            "今天做好一件事就够了。",
        ),
        (
            10, 29,
            "极低/休息",
            ["休息", "极轻量活动"],
            ["所有高负荷任务"],
            "今天最重要的任务是照顾自己。",
        ),
    ]

    # ---- 围绝经期专属 instruction_to_agent —— v9 改动6 -------------------
    _PERIMENOPAUSE_INSTRUCTIONS: list[tuple[int, int, str]] = [
        (
            75, 100,
            "用户为围绝经期支持模式，精力{energy}%。正常回答。"
            "可提建议但不催促。尊重她的判断力。"
            "不使用'更年期/绝经'等标签词。",
        ),
        (
            50, 74,
            "用户为围绝经期支持模式，精力{energy}%。"
            "建议不超过3条。不说'你应该'。"
            "她知道自己该做什么。不暴露任何健康标签。",
        ),
        (
            30, 49,
            "用户为围绝经期支持模式，精力{energy}%。"
            "回答1-2条。先共情。"
            "如果她提到睡眠不好/潮热/忘事，回应"
            "'身体负荷让可用余量变少了，这不是能力下降'。"
            "不诊断、不贴标签。",
        ),
        (
            10, 29,
            "用户为围绝经期支持模式，精力{energy}%。"
            "不安排任务。纯陪伴。不暴露任何健康标签。",
        ),
    ]

    # ---- 阶段级策略覆盖 ------------------------------------------------
    _PHASE_OVERLAY: dict[str, dict[str, Any]] = {
        "menstrual": {
            "core_strategy": "恢复期不是停工期",
            "suitable": ["复盘", "轻量整理", "规划下阶段"],
            "avoid": ["高强度运动", "重大决策"],
        },
        "follicular": {
            "core_strategy": "上升通道，抓窗口",
            "suitable": ["学新技能", "创意", "重要决策", "启动新项目"],
            "avoid": [],
        },
        "ovulatory": {
            "core_strategy": "社交表达黄金期",
            "suitable": ["演讲", "谈判", "面试", "团队协作"],
            "avoid": [],
        },
        "luteal_early": {
            "core_strategy": "收尾执行最佳期",
            "suitable": ["收尾任务", "执行确定性工作", "打磨细节"],
            "avoid": ["启动全新大项目"],
        },
        "luteal_late": {
            "core_strategy": "减量不减质",
            "suitable": ["只做1件核心事", "已想清楚的执行"],
            "avoid": ["多线程", "重大决策", "硬撑清空todo"],
        },
        "unknown": {
            "core_strategy": "依靠HRV实时数据导航，关注身体信号",
            "suitable": [],
            "avoid": [],
        },
    }

    # ---- recommend_task 关键词分类 -------------------------------------
    _HIGH_COGNITIVE_KEYWORDS: list[str] = [
        "写", "总结", "方案", "决策", "规划", "学习",
        "代码", "创意", "设计", "分析", "演讲", "谈判",
        "面试", "主持",
    ]
    _LOW_COGNITIVE_KEYWORDS: list[str] = [
        "整理", "清理", "收尾", "散步", "复盘",
        "汇报", "打扫", "回复", "归档", "填表",
    ]
    # v9 新增：需临场记忆的高负荷任务关键词
    _MEMORY_LOAD_KEYWORDS: list[str] = [
        "演讲", "面试", "谈判", "汇报", "主持",
    ]

    # ==================================================================
    #  核心入口  ——  §7.4.2
    # ==================================================================

    def calculate(
        self,
        last_period: date | None,
        cycle_length: int,
        regularity: str,
        hrv_current: float | None,
        hr_current: int | None,
        hrv_baseline: float,
        hr_baseline: float,
        recent_hrv_trend: str,
        prev_hrv_ratio: float | None,
        hours_since_last_measurement: float,
        measurement_count: int,
        symptoms_list: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行完整的周期阶段 + 精力推算，返回状态字典。"""

        if symptoms_list is None:
            symptoms_list = []

        # -------- PCOS / 围绝经期 无测量 安全兜底（最先判断）----------------
        if regularity in ("pcos", "perimenopause") and hrv_current is None:
            return self._no_data_fallback(regularity)

        # -------- Step 1：日期 → 阶段 + 基准精力 --------------------------
        phase: str
        phase_display: str
        day_in_cycle: int | None
        base_energy: int

        if regularity in ("pcos", "perimenopause") or last_period is None:
            # PCOS / 围绝经期 / 无末次月经 → 跳过日期推算
            phase = "unknown"
            if regularity == "perimenopause":
                phase_display = "当前状态"
            else:
                phase_display = self.PHASE_DISPLAY["unknown"]
            day_in_cycle = None
            base_energy = 0

        else:
            today = date.today()

            # v8 修正：显式校验未来日期，移除 abs() 盲包裹
            if last_period > today:
                return {
                    "status": "error",
                    "code": "INVALID_PARAM",
                    "message": "末次月经日期不能是未来日期",
                }

            day_in_cycle = (((today - last_period).days % cycle_length) + 1)
            day_ratio = (day_in_cycle - 1) / cycle_length

            if cycle_length > 35:
                phase, base_energy = self._long_cycle_anchor(day_in_cycle)
            else:
                phase, base_energy = self._normal_cycle_phase(day_ratio)

            phase_display = self.PHASE_DISPLAY[phase]

        # -------- Step 2：HRV 修正精力值 -----------------------------------
        energy: int | None
        confidence: str = "high"
        hrv_deviation: str
        hrv_ratio: float | None = None

        if hrv_current is not None and hrv_baseline > 0:
            hrv_ratio = hrv_current / hrv_baseline

        if regularity == "pcos":
            # 模式 B：纯 HRV 连续区间映射
            energy = self._pcos_hrv_energy_from_ratio(hrv_ratio)

        elif regularity == "perimenopause":
            # 模式 C：纯 HRV 连续区间映射 + 围绝经期症状加权
            energy = self._pcos_hrv_energy_from_ratio(hrv_ratio)
            if energy is not None:
                energy = self._apply_perimenopause_symptom_penalty(
                    energy, symptoms_list
                )

        else:
            # 模式 A：日期 + HRV 混合（regular / irregular）
            energy, confidence = self._hybrid_energy(
                base_energy, hrv_current, hrv_baseline, regularity
            )

        # HRV deviation 计算
        hrv_deviation = self._calc_deviation(hrv_current, hrv_baseline)

        # -------- Step 2.5：噪声检测 + 周期关联度 + 漏测容错（v8 新增）------
        noise_likelihood: str = "low"
        cycle_related_confidence: str = "medium"

        if hrv_current is not None and hrv_ratio is not None:
            noise_likelihood = self._calc_noise_likelihood(
                hr_current, hrv_ratio, prev_hrv_ratio
            )
            cycle_related_confidence = self._calc_cycle_confidence(
                recent_hrv_trend=recent_hrv_trend,
                noise_likelihood=noise_likelihood,
                measurement_count=measurement_count,
                hrv_ratio=hrv_ratio,
                hr_current=hr_current,
                hr_baseline=hr_baseline,
                phase=phase,
            )

        # 漏测容错分级
        if hours_since_last_measurement > 7 * 24:
            confidence = "low"
        elif hours_since_last_measurement > 48:
            if confidence == "high":
                confidence = "medium"

        # confidence 综合降级
        if noise_likelihood == "high" and confidence == "high":
            confidence = "medium"
        if cycle_related_confidence == "low" and confidence == "high":
            confidence = "medium"

        # -------- Step 3：精力 → 认知状态 + 策略 ---------------------------
        if regularity == "perimenopause":
            cognitive_state, suitable, avoid, core_strategy = (
                self._perimenopause_energy_to_strategy(energy)
            )
        else:
            cognitive_state, suitable, avoid, instruction = self._energy_to_strategy(energy)
            core_strategy = ""

        # -------- 阶段级覆盖（围绝经期跳过）--------------------------------
        if regularity != "perimenopause":
            overlay = self._PHASE_OVERLAY.get(phase)
            if overlay:
                suitable = _dedup_merge(suitable, overlay["suitable"])
                avoid = _dedup_merge(avoid, overlay["avoid"])
                core_strategy = overlay["core_strategy"]
            elif not core_strategy:
                core_strategy = "请先完成首次测量，以便CycleSense感知您的状态"

        # -------- 精力百分比文案 -------------------------------------------
        if regularity == "perimenopause":
            if energy is not None:
                instruction_to_agent = self._perimenopause_instruction(energy)
            else:
                instruction_to_agent = (
                    "用户尚未完成首次测量，数据不足。"
                    "请引导用户先进行测量，在此之前不安排任何任务。"
                )
        else:
            if energy is not None:
                instruction_to_agent = f"用户精力{energy}%。{instruction}"
            else:
                instruction_to_agent = (
                    "不安排任何任务。\"请先完成首次测量，以便我感知您的状态。\""
                )

        # -------- 心率异常降级（v7 已有，v8 阈值保持 110）--------------------
        if hr_current is not None and hr_current > 110:
            if confidence == "high":
                confidence = "medium"
            instruction_to_agent += (
                "\n[提示：用户当前心率偏高，请保持语气温和共情]"
            )

        # -------- user_type 映射（v9 新增）----------------------------------
        user_type = self._regularity_to_user_type(regularity)

        return {
            "phase": phase,
            "phase_display": phase_display,
            "day_in_cycle": day_in_cycle,
            "energy": energy,
            "cognitive_state": cognitive_state,
            "hrv": {
                "current": hrv_current,
                "baseline": hrv_baseline,
                "deviation": hrv_deviation,
            },
            "recommendations": {
                "suitable_tasks": suitable,
                "avoid_tasks": avoid,
                "core_strategy": core_strategy,
            },
            "instruction_to_agent": instruction_to_agent,
            "confidence": confidence,
            "noise_likelihood": noise_likelihood,
            "cycle_related_confidence": cycle_related_confidence,
            "user_type": user_type,
        }

    # ==================================================================
    #  任务适合度评估  ——  §7.3.2  ⑥   §7.4.3
    # ==================================================================

    def recommend_task(self, task_description: str, status: dict[str, Any]) -> dict[str, Any]:
        energy = status.get("energy")
        confidence = status.get("confidence", "high")
        phase = status.get("phase", "unknown")
        user_type = status.get("user_type", "")

        task_level = self._classify_task(task_description)
        is_memory_task = self._is_memory_load_task(task_description)

        suitability, reasons, alternative, suggest_postpone_to = self._task_matrix(
            task_level, energy, confidence
        )

        # -------- v9 新增：围绝经期用户记忆负荷任务门槛调整 -----------------
        if user_type == "perimenopause" and is_memory_task:
            if energy is not None and energy < 60:
                suitability = "not_recommended"
                reasons = [
                    "围绝经期用户对需临场记忆的任务负荷较高，"
                    "当前精力不建议此类任务"
                ]
                alternative = None
                suggest_postpone_to = "建议推迟到精力高峰日"
            elif energy is not None and energy < 75:
                if suitability == "suitable":
                    suitability = "caution"
                    reasons = [
                        "围绝经期用户处理临场记忆任务时消耗更大，"
                        "建议预留充足准备时间"
                    ]
                    alternative = "如果必须今天完成，建议提前准备并预留休息间隔"

        # 构造推迟建议文案
        postpone = None
        if suitability == "not_recommended":
            postpone = suggest_postpone_to or self._default_postpone(phase)

        result: dict[str, Any] = {
            "task": task_description,
            "suitability": suitability,
            "reasons": reasons,
        }
        if alternative:
            result["alternative"] = alternative
        if postpone:
            result["suggest_postpone_to"] = postpone

        return result

    # ==================================================================
    #  内部 —— Step 1 辅助
    # ==================================================================

    def _normal_cycle_phase(self, day_ratio: float) -> tuple[str, int]:
        for bound, phase_key, base_energy in self._NORMAL_PHASE_RULES:
            if day_ratio <= bound:
                return phase_key, base_energy
        return "luteal_late", 45

    def _long_cycle_anchor(self, day_in_cycle: int) -> tuple[str, int]:
        for day_bound, phase_key, base_energy in self._LONG_CYCLE_ANCHOR_RULES:
            if day_in_cycle <= day_bound:
                return phase_key, base_energy
        return "luteal_late", 45

    # ==================================================================
    #  内部 —— Step 2 辅助
    # ==================================================================

    def _hybrid_energy(
        self,
        base_energy: int,
        hrv_current: float | None,
        hrv_baseline: float,
        regularity: str,
    ) -> tuple[int, str]:
        """模式 A：日期 + HRV 混合推算精力。"""
        if hrv_current is None:
            return base_energy, "medium"

        hrv_weight = 0.4 if regularity == "regular" else 0.6
        hrv_ratio = hrv_current / hrv_baseline
        correction = (hrv_ratio - 1.0) * 50.0 * hrv_weight
        raw = base_energy + correction
        energy = max(10, min(100, round(raw)))
        return energy, "high"

    def _pcos_hrv_energy_from_ratio(self, hrv_ratio: float | None) -> int | None:
        """模式 B/C 共用：纯 HRV 连续区间映射。

        hrv_ratio = hrv_current / hrv_baseline
        """
        if hrv_ratio is None:
            return None
        if hrv_ratio >= 1.10:
            return 85
        if hrv_ratio >= 0.90:
            return 65
        if hrv_ratio >= 0.70:
            return 45
        return 25

    def _apply_perimenopause_symptom_penalty(
        self, energy: int, symptoms_list: list[str]
    ) -> int:
        """围绝经期症状加权扣减。"""
        if "热醒/出汗" in symptoms_list:
            energy -= 5
        if len(symptoms_list) >= 3:
            energy -= 5
        return max(10, energy)

    def _calc_deviation(
        self, hrv_current: float | None, hrv_baseline: float
    ) -> str:
        if hrv_current is None:
            return "N/A"
        pct = round(((hrv_current - hrv_baseline) / hrv_baseline) * 100, 1)
        return f"{pct:+.1f}%"

    # ==================================================================
    #  内部 —— Step 2.5  噪声检测 + 周期关联度（v8 新增）
    # ==================================================================

    def _calc_noise_likelihood(
        self,
        hr_current: int | None,
        hrv_ratio: float,
        prev_hrv_ratio: float | None,
    ) -> str:
        """判断本次数据被非周期因素干扰的可能性。

        返回 "high"（高干扰）或 "low"（低干扰）。
        """
        if hr_current is not None and hr_current > 110:
            return "high"
        if prev_hrv_ratio is not None and abs(hrv_ratio - prev_hrv_ratio) > 0.30:
            return "high"
        return "low"

    def _calc_cycle_confidence(
        self,
        recent_hrv_trend: str,
        noise_likelihood: str,
        measurement_count: int,
        hrv_ratio: float,
        hr_current: int | None,
        hr_baseline: float,
        phase: str,
    ) -> str:
        """判断本次 RMSSD 变化和月经周期相关的可信度。

        返回 "low" / "medium" / "high"。
        """
        # v9 前置：PCOS 和围绝经期用户的 phase 均为 "unknown"，无法评估周期关联性
        if phase == "unknown":
            return "medium"

        if measurement_count < 10:
            return "low"
        if noise_likelihood == "high":
            return "low"

        expected_declining = phase in ("luteal_late", "luteal_early", "menstrual")
        expected_rising = phase in ("follicular", "ovulatory")

        rhr_elevated = (
            hr_current is not None
            and hr_baseline is not None
            and hr_current > hr_baseline + 5
        )

        if recent_hrv_trend == "declining" and expected_declining:
            return "high"
        if recent_hrv_trend == "rising" and expected_rising:
            return "high"
        if rhr_elevated and hrv_ratio < 0.90 and expected_declining:
            return "high"
        if recent_hrv_trend == "stable" and abs(hrv_ratio - 1.0) > 0.15:
            return "low"

        return "medium"

    # ==================================================================
    #  内部 —— Step 3 辅助
    # ==================================================================

    def _energy_to_strategy(
        self, energy: int | None
    ) -> tuple[str, list[str], list[str], str]:
        """普通用户 / PCOS：精力 → 认知状态 + 策略。"""
        if energy is None:
            return (
                "数据不足",
                ["轻量整理", "散步"],
                ["重大决策", "多线程", "高强度运动"],
                "不安排任何任务。\"请先完成首次测量，以便我感知您的状态。\"",
            )
        for lo, hi, cog, suitable, avoid, instr in self._ENERGY_TIER:
            if lo <= energy <= hi:
                return cog, list(suitable), list(avoid), instr
        return "数据不足", [], [], ""

    def _perimenopause_energy_to_strategy(
        self, energy: int | None
    ) -> tuple[str, list[str], list[str], str]:
        """围绝经期用户：精力 → 认知状态 + 专属策略。"""
        if energy is None:
            return (
                "数据不足",
                ["轻量整理", "散步"],
                ["重大决策", "多线程"],
                "请先完成首次测量",
            )
        for lo, hi, cog, suitable, avoid, core in self._PERIMENOPAUSE_TIER:
            if lo <= energy <= hi:
                return cog, list(suitable), list(avoid), core
        return "数据不足", [], [], ""

    def _perimenopause_instruction(self, energy: int) -> str:
        """围绝经期专属 instruction_to_agent 文本。"""
        for lo, hi, template in self._PERIMENOPAUSE_INSTRUCTIONS:
            if lo <= energy <= hi:
                return template.format(energy=energy)
        return ""

    # ==================================================================
    #  内部 —— PCOS / 围绝经期 无测量兜底
    # ==================================================================

    def _no_data_fallback(self, regularity: str) -> dict[str, Any]:
        """PCOS 或围绝经期用户从未测量时的安全兜底。"""
        if regularity == "perimenopause":
            phase_display = "当前状态"
            avoid_tasks = ["重大决策", "多线程"]
        else:
            phase_display = self.PHASE_DISPLAY["unknown"]
            avoid_tasks = ["重大决策", "多线程", "高强度运动"]

        return {
            "phase": "unknown",
            "phase_display": phase_display,
            "day_in_cycle": None,
            "energy": None,
            "cognitive_state": "数据不足",
            "hrv": {
                "current": None,
                "baseline": 50.0,
                "deviation": "N/A",
            },
            "recommendations": {
                "suitable_tasks": ["轻量整理", "散步"],
                "avoid_tasks": avoid_tasks,
                "core_strategy": "请先完成首次测量，以便CycleSense感知您的状态",
            },
            "instruction_to_agent": (
                "用户尚未完成首次测量，数据不足。"
                "请引导用户先进行测量，在此之前不安排任何任务。"
            ),
            "confidence": "low",
            "noise_likelihood": "low",
            "cycle_related_confidence": "medium",
            "user_type": self._regularity_to_user_type(regularity),
        }

    # ==================================================================
    #  内部 —— 任务分类与矩阵判定
    # ==================================================================

    def _classify_task(self, task_description: str) -> str:
        is_high = any(kw in task_description for kw in self._HIGH_COGNITIVE_KEYWORDS)
        is_low = any(kw in task_description for kw in self._LOW_COGNITIVE_KEYWORDS)
        if is_high:
            return "high"
        if is_low:
            return "low"
        return "unknown"

    def _is_memory_load_task(self, task_description: str) -> bool:
        """v9 新增：判断任务是否涉及临场记忆负荷。"""
        return any(kw in task_description for kw in self._MEMORY_LOAD_KEYWORDS)

    def _task_matrix(
        self, task_level: str, energy: int | None, confidence: str
    ) -> tuple[str, list[str], str | None, str | None]:
        if energy is None or confidence == "low":
            return (
                "caution",
                ["当前数据不足，建议先完成测量后再评估"],
                None,
                None,
            )

        if energy >= 75:
            return ("suitable", ["当前精力充沛，适合处理该任务"], None, None)

        if energy >= 50:
            if task_level == "high":
                return (
                    "caution",
                    ["精力中等，高认知任务可能消耗较大"],
                    "如果必须今天完成，建议拆分为多个小步骤逐步推进",
                    None,
                )
            return ("suitable", ["当前精力适合处理执行类任务"], None, None)

        if energy >= 30:
            if task_level == "high":
                return (
                    "not_recommended",
                    ["精力偏低，不建议进行高认知负荷任务"],
                    None,
                    None,
                )
            return (
                "caution",
                ["精力偏低，建议只做已想清楚的执行类任务"],
                "如果必须今天完成，可以拆分为最小第一步先开始",
                None,
            )

        # energy < 30
        return (
            "not_recommended",
            ["当前精力极低，应优先休息恢复"],
            None,
            None,
        )

    def _default_postpone(self, phase: str) -> str:
        if phase in ("unknown", "menstrual", "luteal_late"):
            return "建议推迟到卵泡期或排卵期（精力高峰日）"
        return "建议在精力回升时再处理"

    # ==================================================================
    #  内部 —— user_type 映射（v9 新增）
    # ==================================================================

    @staticmethod
    def _regularity_to_user_type(regularity: str) -> str:
        """将 regularity 值映射为 user_type。"""
        mapping = {
            "regular": "young_regular",
            "irregular": "young_regular",
            "pcos": "young_pcos",
            "perimenopause": "perimenopause",
        }
        return mapping.get(regularity, regularity)


# ======================================================================
#  模块级工具
# ======================================================================

def _dedup_merge(base: list[str], overlay: list[str]) -> list[str]:
    seen = set(base)
    result = list(base)
    for item in overlay:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
