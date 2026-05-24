# CycleSense

**让 AI 感知女性身体状态的开放协议。**

硬件采集 HRV（心率变异性）→ 推算当前恢复状态与精力值 → 通过 MCP + Skill 协议暴露 → 任何 AI Agent 给出"适合今天的"而非"正确的"建议。

> 她启 神笔"码"良黑客松 · 故事赛道一「中场潮汐」参赛作品

---

## 为什么做这件事

很多中年女性会同时面对工作、家庭、照护、关系和身体节奏变化。她们不是没有能力，也不是不够自律，而是某些日子里身体可调用的余量变少了。

常见的场景是：夜里热醒或睡眠被打断，早上还要正常开会；明明很熟悉的内容，临场突然忘词或注意力断片；心跳变快、身体发沉，却很难解释"我现在哪里不对"。

女性的身体状态以约 28 天为周期持续波动——这不是疾病，是生理规律：

- **卵泡期 → 排卵期**：雌激素上升，精力、创造力、社交能力攀升至全月峰值
- **黄体晚期 → 月经期**：双激素骤降，工作记忆下降、情绪阈值降低、决策质量变差
- **围绝经期**（42–55 岁）：激素剧烈无规律抖动，潮热、脑雾、失眠、情绪波动交替出现
- **PCOS**：周期极不规律，传统日期追踪完全失效

这些变化直接影响体能、情感、思维、意志四个维度的可用资源——但没有任何工具告诉 AI 这件事。

**所有 AI 助手给建议时，默认你永远精力满格。** 它不知道你昨晚热醒了四次、深睡只有 27 分钟——只会给"正确的"建议，而不是"适合今天的你"的建议。

CycleSense 做的事：**让 AI 在回答前，先知道她今天身体状态怎么样。**

---

## 架构：硬件 → 协议 → Agent

```
┌─────────────────┐     ┌──────────────────────────┐     ┌─────────────────────┐
│  硬件 · 采集     │     │     协议 · 暴露 + 指导     │     │   Agent · 行动       │
│                 │     │                          │     │                     │
│ MAX30102 PPG    │────▶│  推算引擎 (engine.py)     │────▶│ Claude Code / Codex │
│ ESP32C3 WiFi    │     │  MCP Server (5 Tools)    │     │ Trae / WorkBuddy    │
│ 30秒测脉搏波    │     │  Skill（行为规则）         │     │ 等本地 Agent         │
│ → 心率 + RMSSD  │     │  前端看板（可视化辅助）    │     │                     │
└─────────────────┘     └──────────────────────────┘     └─────────────────────┘
      「采集」                  「理解 + 暴露 + 指导」              「行动」
```

**定位：CycleSense 不是 App，是协议层。**

就像 GPS 之于地图——我们提供"你现在在什么状态"，AI 怎么用是上层应用的事。

完整链路：

```
手指放上传感器，开始 30 秒测量
  ↓
ESP32C3 + MAX30102 采集 PPG，计算心率和 RMSSD
  ↓
后端接收数据，写入本地 SQLite
  ↓
CycleEngine 结合 HRV、个人基线、周期模式、症状和最近趋势，计算今日余量
  ↓
Web 看板展示余量、认知负荷、适合事项、避免事项
  ↓
MCP Server 把状态、任务评估、历史反馈开放给 AI 助手
  ↓
AI 按 Skill 规则调整语气、建议长度、任务强度和边界提醒
  ↓
用户记录"什么有用 / 什么没用"，下一次建议更贴近个人经验
```

---

## 核心功能

| 模块 | 说明 |
|------|------|
| **硬件采集** | ESP32C3 + MAX30102，指尖 PPG 30 秒出结果，WiFi 上报 |
| **推算引擎** | 3 种模式：日期+HRV 混合 / 纯 HRV（PCOS）/ 纯 HRV + 围绝经期特征。含噪声检测、周期关联度判断、漏测容错 |
| **MCP Server** | 5 个 Tool + 1 个 Resource，AI 装好即用（`get_cycle_status` / `get_energy_level` / `get_task_recommendation` / `get_historical_pattern` / `log_feedback`） |
| **Skill 行为规则** | 598 行，3 层 18 模块。语言风格库、精力四维模型、月经知识库、围绝经期 6 大场景、时间感知规则 |
| **前端看板** | Web Dashboard：精力卡片、测量页、趋势图、设置页 |
| **用户适配** | 4 类用户自动判定：regular / irregular / PCOS / perimenopause |

---

## Demo：有 vs 没有 CycleSense

**同一句话问 AI："今天日程排满但状态很差，怎么安排？"**

| | 普通 AI | 接入 CycleSense 的 AI |
|--|---------|---------------------|
| 行为 | 列出 5 条高效计划，全部照做 | 只保 1 件核心任务，其余能推就推 |
| 语气 | 理性效率导向 | 先共情"不是能力退步，是资源少了" |
| 依据 | 无，默认你满电 | 精力 41%，基于 HRV 实时推算身体状态 |

---

## 技术栈

| 层 | 技术 |
|----|------|
| 硬件 | Arduino C++ / XIAO ESP32C3 / MAX30102 PPG 传感器 / WiFi HTTP |
| 后端 | Python 3.10+ / FastAPI / SQLite / Pydantic |
| 推算引擎 | 纯函数设计，749 行 `engine.py` |
| MCP | Python mcp-sdk / httpx / stdio 模式 |
| Skill | Markdown，通过 MCP Resource 自动注入 AI 上下文 |
| 前端 | HTML + CSS + JS，轮询后端 API |

---

## 快速开始

### 1. 安装依赖

```bash
cd cycle-sense-main
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 2. 启动后端

```bash
python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
```

访问 `http://127.0.0.1:8000/api/status`，首次未配置时返回 `NOT_CONFIGURED` 是正常的。

### 3. 打开前端

浏览器打开 `CycleSense_Dashboard前端看板.html`（需和 `language.js` 同目录）。

```js
const API = 'http://localhost:8000';  // 本机
// 或局域网 IP，如 'http://192.168.1.23:8000'
```

### 4. 配置硬件（可选）

烧录 `.ino` 固件前修改 WiFi 和后端地址：

```cpp
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL = "http://YOUR_COMPUTER_IP:8000/api/heartbeat";
const char* COMMAND_URL = "http://YOUR_COMPUTER_IP:8000/api/device-command";
```

Arduino 依赖：ESP32 board support · SparkFun MAX3010x Sensor Library

### 5. MCP 接入 AI Agent

```bash
python mcp/server.py
```

Claude Desktop 配置（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "cyclesense": {
      "command": "python",
      "args": ["path/to/cycle-sense-main/mcp/server.py"]
    }
  }
}
```

---

## 硬件说明

### 接线（4 根线，无需焊接）

| MAX30102 | XIAO ESP32C3 |
|----------|-------------|
| VIN/VCC | 3.3V |
| GND | GND |
| SDA | D4 / GPIO6 |
| SCL | D5 / GPIO7 |

母对母杜邦线直接插，USB-C 连电脑或充电宝供电。

### 工作方式

1. ESP32 每 1 秒轮询 `/api/device-command`
2. 前端点击「开始测试」→ 后端返回 `{"command":"start"}`
3. ESP32 进入测量：检测手指 → 计算 BPM + RMSSD（IBI 缓冲 20 个值，至少 10 个才上报）
4. 数据质量达标后上传 `/api/heartbeat`，单次最多 60 秒

### 原理

MAX30102 朝手指射出红光和红外光，每次心跳血液涌过时吸收光量变化 → 提取心跳间隔（IBI）→ 计算 RMSSD（相邻间隔差值均方根）= HRV 估算值。同一颗芯片原理与 Oura Ring、Apple Watch 一致，当前为指尖放置测量，产品化后封装进戒指即可。

---

## MCP Server 详细

| 类型 | 名称 | 作用 |
|------|------|------|
| Resource | `cyclesense://skill` | AI 启动对话时自动读取 Skill 规则 + 当前状态摘要 |
| Tool | `get_cycle_status()` | 获取完整状态 JSON |
| Tool | `get_energy_level()` | 轻量精力值 + 摘要 |
| Tool | `get_task_recommendation(task)` | 评估任务适合度 |
| Tool | `get_historical_pattern(query)` | 查询历史有效/无效做法 |
| Tool | `log_feedback(mood, helped, hurt)` | 记录今日反馈 |

> **Skill 路径说明**：`mcp/server.py` 默认读取 `skill/SKILL.md`。如需使用根目录的 `SKILL.md`，新建 `skill/` 目录并复制进去，或修改 `SKILL_PATH`。

---

## API 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/setup` | 首次设置（5 题问卷 → 自动判定用户类型） |
| POST | `/api/start-test` | 前端触发开始测量 |
| POST | `/api/stop-test` | 停止测量 |
| GET | `/api/device-command` | 硬件轮询指令 |
| POST | `/api/heartbeat` | 硬件上传心率 + HRV |
| GET | `/api/status` | 获取完整周期状态（前端 + MCP 共用） |
| POST | `/api/mark_period` | 标记今天为月经第 1 天 |
| GET | `/api/recommend_task?task_description=...` | 任务适合度评估 |
| GET | `/api/history_pattern?query=...` | 历史模式查询 |
| POST | `/api/log` | 记录心情与反馈 |

---

## Skill 设计

Skill 是给 AI 加载的"行为手册"——告诉 AI 拿到精力数据后该怎么做。598 行，3 层结构：

| 层 | 内容 |
|----|------|
| **第一层：硬规则** | 语言铁律（5 条不可违反）、语言风格库（参考李娟/伍尔夫/奥利弗文学质感）、精力卡片句库（4 档 × 关键词/比喻/一句话）、精力档位行为、边界红线 |
| **第二层：决策知识** | 月经周期五阶段完整机制、精力四维模型（体能/情感/思维/意志）、行为策略矩阵（精力×阶段交叉判断）、阶段级对话风格 |
| **第三层：场景处理** | 三路用户分流（正常/PCOS/围绝经期）、围绝经期 6 大场景应对、时间感知规则（6 时段差异化）、医疗红线（8 类需就医信号） |

没有 Skill，AI 知道你精力低但不知道该说什么、该省略什么、该怎么措辞。

---

## 产品边界

- `energy score` 是 AI 建议调节信号，**不是医学诊断分数**
- HRV 来自指尖 PPG 估算的 RMSSD，不是 ECG 医疗级 HRV
- 不诊断任何疾病，不给用药建议
- 出现明显不适、异常出血、严重心悸、胸痛、晕厥等情况，应咨询专业医生

---

## 数据与隐私

- 所有数据存储在用户本地 SQLite（`backend/cyclesense.db`），不上传云端
- 不主动暴露用户周期阶段或年龄标签
- 仓库包含演示数据库，正式使用前可删除（后端启动时自动重建）
- 开源前请替换 `.ino` 中的 WiFi 密码为占位符

---

## 项目结构

```text
cycle-sense-main/
├── backend/
│   ├── server.py          # FastAPI 后端（10 个端点）
│   ├── engine.py          # 推算引擎（749 行纯函数）
│   ├── db.py              # SQLite 存储 + 基线计算
│   └── cyclesense.db      # 演示数据库
├── mcp/
│   └── server.py          # MCP Server（5 Tools + 1 Resource）
├── CycleSense_Dashboard前端看板.html
├── language.js            # 前端动态文案引擎
├── cyclesense_hrv_demo_working_finally_web_new硬件.ino
├── SKILL.md               # AI 行为规则（598 行）
├── requirements.txt
├── LICENSE
└── README.md
```

---

## 当前版本说明

黑客松 MVP，核心链路已跑通：

```
PPG 采集 → BPM/RMSSD → FastAPI → SQLite → CycleEngine → Web 看板 / MCP → AI Agent
```

注意事项：
- 前端测量页保留了演示兜底动画（硬件不稳定时模拟 30 秒），判断真实链路是否跑通以后端数据库是否出现新测量记录为准
- `.ino` 中的 WiFi 和后端地址需按自己环境修改，公开前请不要保留真实 WiFi 信息
- `mcp/server.py` 默认读取 `skill/SKILL.md`，当前 Skill 文件在根目录，需复制或调整路径

---

## 团队

| 角色 | 成员 |
|------|------|
| 产品 + Skill + 前端 + Pitch | 陈映璇 |
| 后端 + MCP Server | 李欣 |
| 硬件 | 谢政茹 |
| 医学验证 | 韦昊 |

---

## License

MIT License. See [LICENSE](./LICENSE).
