# arknights-maa-copilot

> **起因**：我太懒，不想玩明日方舟，但周常奖励又不能不领。
> 于是我让 AI 自己去玩。这两个仓库，就是它玩着玩着长出来的肌肉记忆。

让 AI（ZCode agent）替我打活动关。思路一句话：**MAA 的仗打得很好，但它不认路**——
所以 AI 负责把路点出来，MAA 只管编队和开打。剩下的一切（找作业、验尸、领奖励、扫荡）
AI 自己用 adb + OCR 一路摸黑学会了。

配套仓库：[arknights-sidestory](https://github.com/jxylisty/arknights-sidestory)
管月度 SideStory 连打。两个 skill 共用底层脚本，修一处两边生效。

## 它现在会什么

| 玩法 | 战绩 | 打法 |
|---|---|---|
| 年度高难（矢量突破/拟生态 VEC-01~12 + A~D + SP01~16） | 「拟生态」#3 全通 | AI 点导航 → MAA 抄作业 → OCR 验收 |
| 每周剿灭 | 每周 400 满歼 | 首通 MAA 抄作业；之后游戏自带**全权委托**秒结算 |
| 悖论模拟 | 已清 | 停在技能弹窗，一个工具全包（含重试） |
| MCP 服务器 | 10 个工具 | AI 的手：截图 / 点屏幕 / 驱动 MAA / 跑扫荡 |

剿灭这段最好玩：首通之后游戏允许看 25 分钟的「代理指挥录像」重放——结果完全一样。
AI 实测后把这条路判了死刑，改用游戏自带的全权委托（一张代理卡秒结算、不扣理智），
扫荡流程自动化到 10 秒一轮。

## 快速开始

```bash
pip install -r requirements.txt
cd scripts

python nav.py --where              # 游戏现在停在哪
python nav.py VEC-09               # 把选关器走到目标关
python run_stage.py VEC-09         # 挑最优作业并开打 + 验收

# 每周剿灭扫荡（游戏停在剿灭地图页时，10 秒完事）
python -c "import annihilation as AN; print(AN.run_delegation())"

# MCP 服务器（ZCode/其他宿主自动连）
python ark_mcp.py
```

环境：Windows（UIA 与 Windows OCR 都是 Windows 专有）+ MAA 在跑且连着模拟器 +
**1920×1080 横屏**（所有坐标按此标定）+ Python 3.10+。路径可用环境变量覆盖，
默认值见 `scripts/config.py`。

## 学费清单（精选；全价版在 references/）

- MAA 的 CheckBox 要用 `TogglePattern.toggle()`，`click_input()` 静默无效；
  「开始」按钮要用 UIA `invoke()`，不然真实鼠标点到模拟器上、MAA 永远不开始
- 作业 JSON 的 `requirements.elite` 低了直接拒载（`CopilotFileReadError`），
  全组一刀切拔高又会让低星挂件卡死选人页——按报错逐个改
- 胜利判定要裁剪区域 OCR「行动结束」；失败时 MAA 日志**同时**有「任务出错」和
  「完成任务: 自动战斗」，只看完成行会把败仗当胜利
- prts 作业的**标题会瞎写**（有份剿灭作业标题叫「卫戍协议:盟约」）——
  选作业只认 `stage_name`，进游戏前先确认模式入口真的存在
- MAA 退出时会用当前界面状态覆写 gui.json：动它的勾选框之前先存原状，测完恢复
- 对游戏未知界面，**一 tap 一截图**：AI 曾把「按钮数量抖动」当进度死循环重试，
  正确姿势是盯资产变化（龙门币/合成玉跳没跳）

## 仓库结构

```
SKILL.md                 实战手册（给 agent 读的）
scripts/
  config.py adb.py winocr.py
  copilot_api.py         作业站 API、干员库匹配、排名
  maa_drive.py           MAA GUI（UIA）驱动、日志等待
  game.py nav.py run_stage.py
  annihilation.py        每周剿灭：MAA 首通 + 全权委托扫荡
  ark_mcp.py             MCP 服务器（10 工具）
data/                    干员星级 / stage map / 悖论作业缓存
references/              vec-annual / annihilation / paradox / maa-pitfalls / daily
```

## 免责

仅供个人自动化学习使用。作业 JSON 版权归 prts.plus 原作者。
打游戏的是 AI，账号是本人的——理智掉了会哭，请勿用于商业用途。
