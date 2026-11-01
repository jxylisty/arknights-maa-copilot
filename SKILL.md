---
name: arknights-maa-copilot
description: 明日方舟**年度高难活动**（矢量突破 / 拟生态，VEC 编号 + 特别战线 + 补给驻防）用 MAA 抄作业逐关通关。也覆盖**每周剿灭作战**（灰石剧场等 camp_r 关，人工点到编队页 + MAA Copilot）和**悖论模拟**（干员页 mem_ 关，停在技能弹窗交给 MAA ParadoxCopilot）。MAA 没做这类活动的导航适配，所以由 agent 用 adb 把关卡点到战斗入口、MAA 只负责自动编队和战斗。含补给/驻防配置、作业 JSON 修补、异格改名、OCR 验收。触发词：用maa打明日方舟、抄作业、拟生态、矢量突破、VEC关、特别战线、补给驻防、MAA打关、maa copilot、剿灭作战、灰石剧场、悖论模拟。
---

# 明日方舟 MAA 抄作业（年度高难 / 每周剿灭 / 悖论模拟）

MAA 的 Copilot 战斗很强，但**不会导航**。这个 skill 的核心拆分：

| 谁 | 干什么 |
|---|---|
| **人/agent（adb）** | 把游戏点到战斗入口（选关页/编队页/技能弹窗）、验收战果 |
| **MAA** | 自动编队 + 按作业 JSON 打这一关 |

## 按玩法查对应的参考文档

| 玩法 | 特征 | 文档 |
|---|---|---|
| 年度高难（矢量突破/拟生态） | VEC-01~12、VEC-A~D、VEC-SP01~16、补给/驻防机制 | [references/vec-annual.md](references/vec-annual.md) |
| 每周剿灭作战 | camp_r_<n> 关，人点「开始行动」进编队页 | [references/annihilation.md](references/annihilation.md) |
| 悖论模拟 | 干员页 mem_<id>_1 关，停在技能弹窗交给 MAA | [references/paradox.md](references/paradox.md) |
| 干员养成 / 日常 / 活动情报 | 干员升级坐标流程、MAA 一键长草队列驱动、活动 API | [references/daily.md](references/daily.md) |
| MAA 驱动/作业修补/验收/异格改名等通用坑 | 所有玩法共用 | [references/maa-pitfalls.md](references/maa-pitfalls.md) |

月度 SideStory 活动（YW-8 / EX-1~8 那类）**不要用这个 skill**，用
`arknights-sidestory`——那边是作业集 + 多作业模式一口气连打。

## 环境前提

- MAA 在跑，且已连上模拟器（窗口标题里能看到设备名，如 `MuMu 模拟器 (16384)`）
- 模拟器分辨率 1920×1080（横屏）。**换分辨率要重算所有文档里的坐标**
- Python 依赖：`pywinauto`、`pillow`、`winrt-*`（Windows OCR）、`pywin32`
- 路径通过环境变量覆盖（默认值见 `scripts/config.py`）：
  `MAA_DIR`、`MAA_ADB`、`MAA_SERIAL`

## 快速开始（以 VEC 为例）

```bash
cd scripts
python nav.py --where              # 看游戏现在停在哪
python nav.py VEC-09               # 把选关器走到目标关
python run_stage.py VEC-09 --dry   # 只排作业不打
python run_stage.py VEC-09         # 自动挑最优作业 + 开打 + 验收
python run_stage.py VEC-09 --job 104031          # 指定作业 id
python run_stage.py VEC-A --job 105819 --supplies 极化,净血,拮抗,消杀,缴械,催化 --rename 凯尔希·思衡托=凯尔希
```

`run_stage.py` 输出 `RESULT: CLEARED` 才算过。**MAA 日志里的
`Completed Task Chain` 只代表任务链跑完，不代表战斗胜利**——必须看屏幕验收。

## 核心流程速览

详细规则与代码在各参考文档；这里只留主干：

1. **找作业**（prts.plus API）：`levelKeyword` 是唯一生效的搜索参数
   （`search`/`keyword`/`stage_name` 全被忽略）；返回的 `content` 是转义 JSON 字符串要二次
   `json.loads`；单条详情 `/copilot/get/{id}`。stage code 按玩法查上面的表。
2. **可行性判定**（`copilot_api.feasible`）：
   干员组=任选其一；只把 5–6 星算真缺口；最多容忍 1 个缺口（借助战只能借 1）；
   必须扫 `actions` 里引用的干员；装置/召唤物不算干员。
   干员库来自 MAA 的 `OperBoxData.json`（只扫高星，低星靠 `rarity<=4 → 拥有` 兜底）。
   **它没有模组字段**，模组风险只能看 MAA 编队日志。
3. **驱动 MAA**（`maa_drive.py`，全程可不抢焦点）：
   `keep_focus()` 包住 UIA 操作；`set_path` 用 ValuePattern；checkbox 用 `toggle()`；
   开始按钮用 `invoke()`。
4. **验收**（OCR，不能只看 MAA 日志）：胜利找「行动结束」（裁剪区域 (40,230)-(760,480)），
   失败全屏找「任务失败」；`wait_done` 先查错误标志再查完成行。

## 常见失败模式速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `未找到关卡` / 一点开始就停 | MAA 没做该活动导航 | 人先点到关卡界面（本 skill 的核心前提） |
| 点了「开始」但 MAA 毫无反应、日志不增长 | MAA 窗口被模拟器挡住，`click_input` 落到模拟器上 | `click_start` 已改用 UIA `invoke()` |
| `FileTreeBox TextBox not found` | MAA 停在别的 Tab | `tab_click` 已支持别名；作业页 Tab 文字是**「自动战斗」** |
| `干员不可用: X, 原因: 等级不足` | 练度低于作业要求 | 勾「忽略干员属性要求」；仍失败则换作业 |
| `干员不可用: X, 原因: 所需模组未解锁` 但照样上场 | 账号没解锁该模组（硬门槛） | **编队阶段就换作业**，打下去必输 |
| `以下干员组内的干员均未拥有: X` 但 X 明明有 | ①低星没扫进 box ②干员**驻防中** ③游戏内名字不一致 | 见 references/maa-pitfalls.md「干员名不匹配」 |
| `读取文件失败 / CopilotFileReadError` | 作业 JSON `requirements.elite` 低于实际 | 按 `asst.log` 点名的干员改，别全组一刀切 |
| 编队时报一串 `精英化不足` | 反向错误：把低星挂件的 elite 也提到了 2 | 低星恢复原值，只改报错的那个 |
| `Cannot enter quick formation page` | 上一轮失败后停在选人页 | 退到关卡界面再重跑 |
| `正在使用｢多作业模式｣, 但未添加任何作业` | 上一轮多作业模式没关 | `run_stage.py` 会自动关；手工跑记得取消勾选 |
| 战斗跑完但 `任务失败` | 装置点放失败 / 练度差 / 轴不对 | 先重试 1–2 次，再换作业 |
| 战斗结束了但游戏还在打 | 作业最后一步是 `开始挂机`，MAA 提前退出 | 等一会儿再截图验收 |

## 脚本清单

这些脚本同时被 `arknights-sidestory` 复用（它通过 `_shared.py` 定位到这里），
所以在这里改一次，两个 skill 一起生效。

| 文件 | 作用 |
|---|---|
| `config.py` | 路径与常量（支持环境变量覆盖） |
| `adb.py` | 截图 / tap / swipe / back |
| `winocr.py` | Windows OCR 封装 + 文本归一化 |
| `copilot_api.py` | 作业站 API、干员库匹配、作业排名、作业集 |
| `maa_drive.py` | MAA GUI（UIA）驱动、日志等待 |
| `game.py` | 关卡识别、验收、作业 JSON 保存/修补/改名、补给开关 |
| `nav.py` | 关卡导航（VEC） |
| `run_stage.py` | 一键：选作业 → 配补给 → 开打 → 验收 |
| `ark_mcp.py` | MCP 服务器：`screen`/`tap(expect护栏)`/`swipe`/`back`/`copilot_search`/`battle_wait`/`paradox_run`/`maa_status`/`annihilation_run`(全权委托扫荡)/`annihilation_collect`(结算解析)，已注册进 ZCode（`~/.zcode/cli/config.json` → mcp.servers） |
| `data/rarity.json` | 干员→星级 |
| `data/stage_map.json` | 显示编号→stage id |
| `data/mem_jobs.json` | mem_ 悖论作业缓存（MCP 生成，24h 过期） |

### MCP 用法要点

- ZCode 会话启动时自动连接（用户级配置）。改动 `ark_mcp.py` 后重启会话或重连 MCP 生效。
- **`tap(x, y, expect="页面特征")` 是防误点护栏**：expect 不在屏幕上就拒绝点击并返回当前
  屏幕文本。在不确定的页面上务必带 expect。
- `paradox_run(干员名)` 前置条件：游戏已停在目标干员的悖论详情页（有「开始模拟」的页面）。
  它自己做作业查找（mem_ 缓存 + 标题匹配）、技能选择、MAA 接管、失败重试。
- `annihilation_run(confirm)` 前置条件：游戏停在剿灭地图页（OCR 能读到「剿灭委托」）。
  走游戏内**全权委托**：1 张常态事务代理卡秒结算（不扣理智），收益按最高无助战歼灭数发。
  `confirm=false` 只弹确认框读预览（杀数/合成玉/周报酬进度）然后取消。
  每周首通仍要用 MAA 抄作业（见 references/annihilation.md）。
- `maa_status` 只读自检：MAA 窗口在不在、作业页勾选状态、当前路径、gui.log 尾部。
  跑 paradox_run / MAA 抄作业前先调它。
- `annihilation_run()`：剿灭扫荡（全权委托，秒结算，耗 1 张常态事务代理卡不扣理智）。
  前置：游戏停在剿灭地图页（OCR 可见「剿灭委托」）；`confirm=false` 只弹确认框读收益预览。
  详见 references/annihilation.md §全权委托扫荡。
- `maa_status`：MAA 窗口/勾选框/日志尾只读自检，跑需要 MAA 的流程前先调它。
- 环境变量 `MAA_DIR`/`MAA_ADB`/`MAA_SERIAL` 对 MCP 进程同样生效。
