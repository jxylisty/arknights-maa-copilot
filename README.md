# arknights-maa-copilot

用 MAA 抄作业自动通关明日方舟活动关。**专治 MAA 没做导航适配的活动**——这类活动一点「开始」就报「未找到关卡」，MAA 的 Copilot 再强也用不上。

思路是把职责拆开：**人（或 adb 脚本）负责把关卡点到「开始行动」界面，MAA 只负责自动编队 + 按作业 JSON 打这一关**。

## 能做什么

- 从 [prts.plus](https://prts.plus) 按关卡搜作业，并按你的干员库排序可行性
- 驱动 MAA 的 WPF GUI（UI Automation），不用手点
- 自动修补作业 JSON：异格干员改名、elite 要求冲突、干员组替换
- 自动找新活动：活动表、关卡代码前缀、作业集（`prts://s<id>`）
- adb 截图 + OCR 验收战果，不依赖 MAA 日志的「任务链完成」

## 环境

- Windows（MAA 是 WPF 应用，UI Automation 与 `Windows.Media.Ocr` 都是 Windows 专有）
- MAA 在跑，且已连上模拟器；模拟器分辨率 **1920×1080 横屏**（所有坐标按此标定）
- Python 3.10+

```bash
pip install -r requirements.txt
```

路径通过环境变量覆盖，默认值见 `scripts/config.py`：

| 变量 | 默认 |
|---|---|
| `MAA_DIR` | `E:\C盘搬家\工具包\MAA-v5.12.3-win-x64` |
| `MAA_ADB` | `$MAA_DIR/adb/platform-tools/adb.exe` |
| `MAA_SERIAL` | `127.0.0.1:16384` |

## 快速开始

```bash
cd scripts

python nav.py --where              # 游戏现在停在哪一关
python nav.py VEC-09               # 把选关器走到目标关（游戏内线性解锁）
python run_stage.py VEC-09 --dry   # 只看作业排名，不打
python run_stage.py VEC-09         # 挑最优作业并开打 + 验收
python run_stage.py VEC-A --job 105819 --supplies 极化,净血,拮抗,消杀,缴械,催化
```

找新活动：

```bash
python copilot_api.py --acts            # 当前还在开的活动 + 关卡代码
python copilot_api.py --sets 昨日海      # 搜作业集
python copilot_api.py --set 55511       # 展开作业集，看覆盖了哪些关
```

## 关键坑（都是实际踩过的）

**MAA 侧**

- 复选框必须用 `TogglePattern.toggle()`，`click_input()` 不会翻转 WPF 的 CheckBox
- 「开始」按钮要用 UIA `invoke()`：`click_input()` 会移动真实鼠标，模拟器挡住 MAA 时点空
- 作业页的 Tab 文字是**「自动战斗」**不是「作业」；`select()` 会静默失败，要用 `iface_selection_item.Select()`
- 作业列表是虚拟化的，`descendants()` 只返回可见的十几行，滚轮必须落在列表区域上

**作业 JSON 侧**

- 搜作业的参数是 `levelKeyword`，`keyword` / `search` 会被静默忽略
- `requirements.elite` **不能低于**干员实际精英化等级，否则 MAA 直接 `CopilotFileReadError`
- 但**也不能全组一刀切提到 E2**——低星挂件会变成「精英化不足」，卡在选人页
- `groups[].opers` 是任选其一；`actions[].name` 里还会出现**组名**，把它当干员名查会误判缺人

**验收侧**

- 胜利画面的标题是 `行动结束 / MISSION RESULTS`，**不是** `MISSION COMPLETED`
- 而且这行大字只能靠**裁剪区域** OCR 读到，全屏读不出来
- 战斗失败时日志里**同时**有「任务出错」和「完成任务: 自动战斗」，只查后者会把失败判成成功

## 仓库结构

```
SKILL.md                 完整的实战手册（给 agent 读的）
scripts/
  config.py              路径与常量（环境变量可覆盖）
  adb.py                 截图 / tap / swipe / back
  winocr.py              Windows OCR 封装 + 文本归一化
  copilot_api.py         作业站 API、干员库匹配、作业排名、作业集、活动表
  maa_drive.py           MAA GUI（UIA）驱动、日志等待
  game.py                关卡识别、验收、作业修补、elite 预检、补给开关
  nav.py                 关卡导航
  run_stage.py           一键：选作业 → 预检 → 配补给 → 开打 → 验收
data/
  rarity.json            干员 → 星级
  stage_map.json         显示编号 → stage id
```

## 状态

已验证通关：「拟生态」（矢量突破 #3）VEC-01~12 + A/B/C/D + SP01~SP16，「昨日海」YW-EX-1~8 普通 + 全部突袭。

## 免责

仅供个人自动化学习使用。作业 JSON 版权归 prts.plus 原作者。
