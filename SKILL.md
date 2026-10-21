---
name: arknights-maa-copilot
description: 明日方舟**年度高难活动**（矢量突破 / 拟生态，VEC 编号 + 特别战线 + 补给驻防）用 MAA 抄作业逐关通关。MAA 没做这类活动的导航适配，所以由 agent 用 adb 把关卡点到「开始行动」界面、MAA 只负责自动编队和战斗。含补给/驻防配置、作业 JSON 修补、异格改名、OCR 验收。触发词：用maa打明日方舟、抄作业、拟生态、矢量突破、VEC关、特别战线、补给驻防、MAA打关、maa copilot。
---

# 明日方舟年度高难活动（矢量突破 / 拟生态）—— 逐关抄作业

这个 skill 只管**一年一次的高难活动**：编号 `VEC-01`~`VEC-12` 的核心突破、`VEC-A`~`VEC-D` 的全力以赴、`VEC-SP01`~`VEC-SP16` 的特别战线，外加**补给/驻防**这套专属机制。

**月度 SideStory 活动（YW-8 / EX-1~8 那类）不要用这个 skill**，用 `arknights-sidestory`——那边是作业集 + 多作业模式一口气连打，不需要逐关导航。

为什么这类活动必须逐关点：MAA 的 Copilot 功能很强，但**它不会帮你导航到关卡**。矢量突破的选关界面是 VEC 专属的堆叠 UI + 4×4 特别战线网格，MAA 完全没做适配，一点「开始」就报「未找到关卡」。

解决办法是把职责拆开：

| 谁 | 干什么 |
|---|---|
| **人/agent（adb）** | 把关卡点到「开始行动」界面、逐关推进、验收战果 |
| **MAA** | 只负责自动编队 + 按作业 JSON 打这一关 |

这个 skill 就是把这套拆解固化成可复用脚本。

## 环境前提

- MAA 在跑，且已连上模拟器（窗口标题里能看到设备名，如 `MuMu 模拟器 (16384)`）
- 模拟器分辨率 1920×1080（横屏）。换分辨率要重算本文所有坐标
- Python 依赖：`pywinauto`、`pillow`、`winrt-*`（Windows OCR）、`pywin32`
- 路径通过环境变量覆盖（默认值见 `scripts/config.py`）：
  `MAA_DIR`、`MAA_ADB`、`MAA_SERIAL`

## 快速开始

```bash
cd scripts

# 1. 先看游戏现在停在哪一关
python nav.py --where

# 2. 把选关器走到目标关（关卡线性解锁，用上下箭头走）
python nav.py VEC-09

# 3. 看作业排名（只排不打）
python run_stage.py VEC-09 --dry

# 4. 自动挑最优作业并开打 + 验收
python run_stage.py VEC-09

# 5. 指定某个作业 id
python run_stage.py VEC-09 --job 104031

# 6. 一键配补给 + 借干员 + 修异格名（游戏需停在特别战线页）
python run_stage.py VEC-A --job 105819 --supplies 极化,净血,拮抗,消杀,缴械,催化 --rename 凯尔希·思衡托=凯尔希
python run_stage.py VEC-C --job 105729 --borrow
```

`run_stage.py` 输出 `RESULT: CLEARED` 才算过。**MAA 日志里的
`Completed Task Chain` 只代表任务链跑完，不代表战斗胜利**——必须看屏幕验收。
脚本现在会轮询验收（最多 40 秒），避免作业以 `SkillDaemon` 结尾、任务链先结束但战斗还在打的情况。

验收的判定细节（都是踩过坑才对的）：

| 画面 | 判据 | 为什么不能想当然 |
|---|---|---|
| 胜利 | **裁剪区域** (40,230)-(760,480) OCR 出 `MISSION RESULTS` / `行动结束` | 全屏 OCR 读不到这行大字；而且胜利文案是 `行动结束`，**不是** `MISSION COMPLETED` |
| 失败 | 全屏 OCR 出 `任务失败` / `MISSION FAILED` | 失败画面裁区域反而读不出来 |

两个方向的误判都真实发生过：早期版本找 `MISSIONCOMPLETED`，把所有胜利判成失败；
而 `wait_done` 又因为失败日志里同时有「任务出错」和「完成任务: 自动战斗」，
把失败判成成功。现在 `wait_done` 先查错误标志，`run_stage.py` 即使日志报错也会看屏幕复核。

## 核心流程

### 1. 找作业（prts.plus API）

```
https://prts.maa.plus/copilot/query?levelKeyword=act3break_09&page=1&limit=40&desc=true&orderBy=hot
```

**踩过的坑：**

- 搜索参数是 **`levelKeyword`**。`search` / `keyword` / `stage_name` 全都会被忽略，返回一堆不相关的作业。
- 返回的 `content` 是**转义过的 JSON 字符串**，要二次 `json.loads`。
- 单条详情：`https://prts.maa.plus/copilot/get/{id}`，同样 `data.content` 要二次解析。

### 2. 作业可行性判定（这是最容易翻车的地方）

`copilot_api.feasible(box, job)` 的规则，全部来自实战教训：

| 规则 | 原因 |
|---|---|
| **干员组是「任选其一」** | `groups[].opers` 是候选池；整组有一个拥有就算满足。当成必选会误报「缺 25 人」 |
| **只把 5–6 星算真缺口** | OperBox 扫描不完整，低星几乎全有，按缺失处理会误杀大量好作业 |
| **最多容忍 1 个缺口** | MAA 的「借助战」只能借 1 个干员 |
| **`requirements.level` 不是硬门槛** | 那是作者的练度，不是最低要求。低配能过的情况很常见 |
| **必须扫 `actions` 里引用的干员** | 作业经常只在 action 里引用某个干员（如战斗中上助战）。漏了会在运行时报「XXX 未拥有」 |
| **装置/召唤物不算干员** | `再度绽放`、`正义骑士号`、`Lancet-2`、`风雪之眼`、`中继器` 等见 `NON_OPER` |

干员库来自 `MAA_DIR/data/OperBoxData.json`（MAA 的仓库扫描结果）。
**它只扫高星**——真实账号有 300+ 干员时这里可能只有 80 条，低星要靠 `rarity<=4 → 视为拥有` 兜底。

### 3. MAA GUI 自动化

走 pywinauto 的 UIA backend 操作 WPF 控件（不要改 `gui.json`，MAA 退出时会覆写）。

```python
w = M.focus()
M.tab_click(w, "作业")
M.set_path(w, r"...\config\copilot\VEC09_job.json")
M.set_checkbox(w, "自动编队", True)
M.set_checkbox(w, "忽略干员属性要求", True)   # 练度不够时必须开
M.set_checkbox(w, "借助战", False)           # 有缺口才开
M.click_start(w)
```

**两个必踩的坑：**

1. **`CheckBox.click_input()` 经常不生效**（WPF 模板吞掉了点击）。必须用 `el.toggle()`（TogglePattern）。`set_checkbox` 已经封装好了。
2. **`SetForegroundWindow` 抛 `pywintypes.error(1400)`**：别的窗口抢焦点时会这样，但 UIA 的 click 依然有效，直接吞掉异常。

复选框清单（作业页）：`自动编队`、`使用编队`、`忽略干员属性要求`、`借助战`、`补充低信赖干员`、`追加自定干员`、`使用一图流数据辅助编队`、`多作业模式`。

### 4. 验收（不能只看 MAA 日志）

```python
cleared, screen = game.verify_result()
```

OCR 屏幕找 `MISSION COMPLETED` / `行动已完成` / `考核已完成`，同时排除 `任务失败`。

OCR 用 Windows 自带的 `Windows.Media.Ocr`，不需要装 tesseract。
**OCR 会把 `VEC` 读成 `VE0` / `VE008`**，所以 `detect_stage` 里做了归一化 + 容错正则。

### 5. 作业 JSON 修补

有些作业在特定账号上必然跑不了，但有救。`game.patch_job` 支持两种修补：

```python
# A. 组里只剩自己确定拥有的干员（组内任选其一，MAA 会挑一个）
groups = {"单奶1": [
    {"name": "流明", "skill": 3, "skill_usage": 1,
     "requirements": {"elite": 2, "level": 1, "skill_level": 7,
                      "module": -1, "potentiality": 1}},
    {"name": "苏苏洛", "skill": 1, "skill_usage": 0,
     "requirements": {"elite": 1, "level": 1, "skill_level": 1,
                      "module": -1, "potentiality": 1}},
]}
```

**致命陷阱：`requirements.elite` 不能低于干员的实际精英化等级。**

MAA 解析作业时会校验，写成 `elite: 1` 而干员实际是 E2，直接抛
`CopilotFileReadError / 读取文件失败`，日志里是：

```
parse_oper_usage | Oper 凯尔希·思衡托 has higher elite requirement: 2 , but elite requirement is set to 1
```

`elite` 只能**等于或高于**实际，放宽只能动 `level` / `skill_level`。

**反过来的坑：不要把全组 `elite` 一把改成 2。** 低星挂件（`Lancet-2`、`苏苏洛`、`安赛尔`…）
实际只有 E0/E1，把它们的要求提到 E2，MAA 编队时会报一串

```
干员不可用: 苏苏洛, 原因: 精英化不足
干员不可用: Lancet-2, 原因: 精英化不足
```

然后停在选人页失败。**正确做法是按 `asst.log` 点名的干员逐个改**：日志说谁
`has higher elite requirement`，就把谁的 `requirements.elite` 提到实际值，其余不动。

注意改的是 `requirements.elite`（MAA 校验这个），不是顶层的 `elite`。两个都写最省事：

```python
def fix(o, elite):
    o["elite"] = elite
    o.setdefault("requirements", {})["elite"] = elite
```

另外，同一个干员可能在多个组里出现且要求不同（`结城理` 在「傀儡师」是 0、在「地刺」是 2）。
逐个改时只看报错的那个组，别被另一处的高要求带偏。

## 明日方舟游戏侧坐标（1920×1080）

### 核心突破（VEC-01~12 堆叠界面）

| 元素 | 坐标 |
|---|---|
| 选关 +1（上箭头） | (290, 322) |
| 选关 -1（下箭头） | (290, 655) |
| 开始行动 | (1560, 968) |
| 结算「继续」 | (1690, 855) |
| 关卡码 OCR 区域 | (80, 250, 540, 720) |
| 主界面「终端」 | (1250, 375) |
| 活动页「核心突破」 | (1465, 880) |
| 活动页「特别战线」 | (1670, 470) |
| 左上返回 | (72, 47) |

### 特别战线（4×4 网格）

| 格 | 坐标 | 格 | 坐标 |
|---|---|---|---|
| r1c1 SP01 | (205, 330) | r1c2 SP02 | (410, 330) |
| r1c3 SP03 | (610, 330) | r1c4 SP04 | (810, 330) |
| r2c1 SP05 | (205, 520) | r2c2 SP06 | (410, 520) |
| r2c3 SP07 | (610, 520) | r2c4 SP08 | (810, 520) |
| r3c1 SP09 | (205, 750) | r3c2 SP10 | (410, 750) |
| r3c3 SP11 | (610, 750) | r3c4 SP12 | (810, 750) |
| r4c1 SP13 | (205, 935) | r4c2 SP14 | (410, 935) |
| r4c3 SP15 | (610, 935) | r4c4 SP16 | (810, 935) |

网格里的 **stage id 编号和显示编号不一致**，必须查 `data/stage_map.json`：

```
VEC-SP09 -> act3break_sp04     VEC-SP12 -> act3break_sp08
VEC-SP10 -> act3break_sp05     VEC-SP13 -> act3break_sp12
VEC-SP11 -> act3break_sp07     VEC-SP14 -> act3break_sp13
```

**每格先点一下 OCR 确认名字再打**，不要相信位置——格子顺序在别的活动里会变。

### 补给/驻防机制（这类活动专属，务必先问清楚）

- 特别战线每通一关解锁一个「补给/装置」（`急救程序`、`拮抗装备`……）
- 补给槽位有限（本例 6/6），**启用新补给前要先停用一个**
- 补给要生效，必须**在该补给上驻防干员**；驻防用的干员**不能出战其他关**
- 所以「换个更强的干员来打」和「保住某个补给」是**冲突的**，这类取舍必须问用户，不要自作主张

#### 补给页操作坐标（1920×1080）

补给格 4×4，坐标与特别战线网格相同：

| 格 | 坐标 | 格 | 坐标 |
|---|---|---|---|
| r1c1 急救 | (190, 285) | r1c2 灵便 | (390, 285) |
| r1c3 蓄能 | (590, 285) | r1c4 凝神 | (790, 285) |
| r2c1 缴械 | (190, 490) | r2c2 迟滞 | (390, 490) |
| r2c3 催化 | (590, 490) | r2c4 消杀 | (790, 490) |
| r3c1 抵抗 | (190, 700) | r3c2 拮抗 | (390, 700) |
| r3c3 活血 | (590, 700) | r3c4 净血 | (790, 700) |
| r4c1 冷结 | (190, 910) | r4c2 冻结 | (390, 910) |
| r4c3 纯化 | (590, 910) | r4c4 极化 | (790, 910) |

点击格子后，**右下角 (1555, 845) 是开关**：
- 黄色 `(255,221,0)` = 已启用，点一下停用
- 深灰 `(59,59,59)` = 未启用，点一下启用（前提是该补给上有驻防干员）
- 浅灰 `(236,236,236)` = 该关还没通关，按钮是「夺取控制」，不能启用

**用像素颜色判断状态，不要用 OCR**——OCR 对这个按钮的识别极不稳定，经常读空。

「驻防总览」入口在右上角 (1735, 68)，能一眼看到哪些补给有驻防、哪些是「夺取控制」（未通关）。

#### 未通关的补给会卡住整套作业

本活动 ABCD 四关的作业几乎都要求「催化」，而催化来自 VEC-SP07。**先确认要用的补给都已通关**，否则作业再怎么换都过不了。检查方法：点开补给格看按钮是不是「夺取控制」。

## 常见失败模式速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `未找到关卡` / 一点开始就停 | MAA 没做该活动导航 | 人先点到关卡界面（本 skill 的核心前提） |
| 点了「开始」但 MAA 毫无反应、日志不增长 | MAA 窗口被模拟器挡住，`click_input` 的鼠标点击落到了模拟器上 | `click_start` 已改用 UIA `invoke()`，不需要窗口置顶 |
| `FileTreeBox TextBox not found` | MAA 停在别的 Tab，作业页没加载 | `tab_click` 已支持别名；作业页的 Tab 文字是**「自动战斗」**不是「作业」 |
| `干员不可用: X, 原因: 等级不足` | 练度低于作业要求 | 勾「忽略干员属性要求」；仍失败则换作业 |
| `以下干员组内的干员均未拥有: X` 但 X 明明有 | ①低星没扫进 box ②该干员在**驻防中** ③**游戏内名字和作业名字不一致** | 见下方「干员名不匹配」 |
| `读取文件失败 / CopilotFileReadError` | 作业 JSON 里 `requirements.elite` 低于干员实际等级 | 按 `asst.log` 点名的干员改，**别全组一刀切** |
| 编队时报一串 `干员不可用: X, 原因: 精英化不足` | 反向错误：把低星挂件的 `elite` 也提到了 2 | 低星恢复原值，只改报错的那个 |
| `Cannot enter quick formation page` | 上一轮失败后游戏停在选人页 | 退到关卡界面再重跑，别在残留界面上重试 |
| `正在使用｢多作业模式｣, 但未添加任何作业` | 上一轮多作业模式没关，单作业跑不了 | `run_stage.py` 现在会自动关掉；手工跑时记得取消勾选 |
| 战斗跑完但 `任务失败` | 装置点放失败 / 练度差一口气 / 轴不对 | 先重试 1–2 次（部分作业明说"遇到重开"），再换作业 |
| 战斗结束了但游戏还在打 | 作业最后一步是 `开始挂机`，MAA 提前退出 | 等一会儿再截图验收 |

### 干员名不匹配（最隐蔽的坑）

MAA 用 OCR 读游戏里的干员名，**作业写什么名字就得和游戏里显示的一字不差**。
`asst.log` 里会写 `unknown oper 凯尔希`——注意是「凯尔希」不是「凯尔希·思衡托」。

异格干员的作业名常带后缀（`凯尔希·思衡托`、`维什戴尔`……），而游戏里显示的是本体名。
修法是把作业 JSON 里的 `opers[].name` 和 `actions[].name` 一起改：

```python
for o in c['opers']:
    if o['name'] == '凯尔希·思衡托':
        o['name'] = '凯尔希'
for a in c['actions']:
    if a.get('name') == '凯尔希·思衡托':
        a['name'] = '凯尔希'
```

**先看 `asst.log` 里的 `unknown oper XXX` 再猜**，不要凭感觉改。

### 干员组是「任选其一」，且 `actions` 会引用组名

作业 JSON 的 `groups[].opers` 是候选池，不是必选清单。判断可行性时**整组只要有一个拥有就算满足**。

更隐蔽的一点：`actions[].name` 里既有干员名，**也有组名**（`拦人先锋`、`单奶1`、`坦克`…）。
把组名当干员名去查 box，必然查不到，于是整组被判成缺口——这正是早期版本
「缺 25 个干员」的真正根源。`feasible()` 现在会先把 `groups[].name` 排除掉。

另外 `actions` 里还有召唤物和装置（`魂灵之影`、`基质合成台`），它们不是干员，
统一放进 `NON_OPER` 当挂件处理。

### elite 冲突现在会自动预检

`run_stage.py` 在交给 MAA 之前会先跑 `game.elite_conflicts()`：拿作业里每个干员的
`requirements.elite` 和 `OperBoxData.json` 里的实际值比，低于实际的就只把**那几个人**
提到实际值（`game.raise_elite()`），不再需要人工看 `asst.log` 猜。

- 只改被点名的人，**不会**把全组一刀切到 E2（那会让低星挂件变成「精英化不足」）。
- `--no-auto-elite` 可以关掉，改成只打印冲突清单。
- 同一个干员在多个组里要求不同时，各自独立处理。

## 经验数据

- 「雪洁双核」系列作业（104030/104031/104034…）质量最高，但**核心必须 90 级**。作者在说明里明写「BOSS 转阶段以 90 级专 3 三级模组为判定标准」，L70 必挂。
- 有装置点放动作（`Click` + `Swipe`）的作业**天然不稳定**，说明里会写「有概率放不下去，遇到重开」。VEC-08 实际是第 3 次才过。
- 单人/双人作业最省心，但吃单卡练度；多核作业容错高但缺人概率大。**优先挑"零缺口 + 高播放量"**。
- 一次完整战斗 1–6 分钟；`wait_done` 超时给 480s 起。
- 缺一个 6★ 时勾「借助战」往往比换作业划算。VEC-C 全关卡都要「遥」，借一个直接过。
- 作业的 `doc.details` 第一行通常就是**补给清单**，抄作业前先读它，别打完才发现补给配错。

### 全力以赴（VEC-A~D）实战记录

四关的补给需求各不相同，且槽位只有 6 个，换关就得重配：

| 关 | 补给 | 作业 |
|---|---|---|
| VEC-A | 极化 净血 拮抗 消杀 缴械 催化 | 105819（需把「凯尔希·思衡托」改成「凯尔希」） |
| VEC-B | 极化 净血 拮抗 消杀 缴械 催化 | 105667 |
| VEC-C | 极化 拮抗 净血 催化 缴械 迟滞 | 105729 + 借助战（缺「遥」） |
| VEC-D | 灵便 缴械 拮抗 催化 净血 极化 | 106140 |

A/B 共用一套补给，可以连着打；C 要把「消杀」换成「迟滞」；D 要把「迟滞」换成「灵便」。
每换一次补给，都要点开格子确认右下角开关变成黄色 `(255,221,0)`。

## 作业集与多作业模式

作业集（`prts://s<id>`）+ MAA 多作业模式是**月度 SideStory 活动**的主力打法，
完整说明在 `arknights-sidestory` skill。这里只留两条对矢量突破也有用的结论：

- 作业集导入 MAA 是**追加**到任务列表，不清空旧条目；
- 一个集里每关会被 MAA 跑**普通 + 突袭各一遍**，理智按双倍算。

矢量突破这类活动**没有**整集连打的价值：补给要按关切换（见上面的 ABCD 补给表），
而且特别战线的 stage id 和显示编号不一致。逐关跑 `run_stage.py` 反而更可控。

## 自动找新活动入口

MAA 有一张在线活动表，包含活动名、起止时间和关卡代码：

```bash
python copilot_api.py --acts      # 当前还在开的活动
```

```python
import copilot_api as CA
for a in CA.current_activities():
    print(a["tip"], a["start"], a["expire"], a["stages"])
```

`https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json`，
`Official.sideStoryStage.<key>.Activity` 里有 `Tip`/`StageName`/起止时间，
`Stages[].Display` 是关卡代码。

**注意**：`--acts` 只覆盖 SideStory，矢量突破这类高难活动**不在里面**——
它每年一次、时间不固定，得靠用户告知或自己去终端页找。活动表也有更新延迟。

拿到关卡代码后，stage id 的规律是 `<活动内部id>_ex01..ex08` / `_01.._08`，
活动内部 id 可以从 MAA 的资源目录反查：

```bash
ls "$MAA_DIR/resource/Arknights-Tile-Pos" | grep -i "<关卡代码前缀>"
# 例如 YW -> act52side_ex01 ...
```

或者直接对候选 id 调 `search_stage()`，有作业就说明 id 对了。

`detect_stage()` 的关卡码正则覆盖常见前缀
（`VEC`/`YW`/`EX`/`S`/`WD`/`SV`/`CB`/`IW`/`ZT`/`RS`/`DV`），并且会修正 OCR 的
数字误识（`YW-EX-I` → `YW-EX-1`、`VE008` → `VEC-08`）。但**游戏侧坐标是按
1920×1080 量的**，换分辨率要重算。

## 脚本清单

这些脚本同时被 `arknights-sidestory` 复用（它通过 `_shared.py` 定位到这里），
所以在这里改一次，两个 skill 一起生效。

| 文件 | 作用 |
|---|---|
| `config.py` | 路径与常量（支持环境变量覆盖） |
| `adb.py` | 截图 / tap / swipe / back |
| `winocr.py` | Windows OCR 封装 + 文本归一化 |
| `copilot_api.py` | 作业站 API、干员库匹配、作业排名 |
| `maa_drive.py` | MAA GUI（UIA）驱动、日志等待 |
| `game.py` | 关卡识别、验收、作业 JSON 保存/修补/改名、补给开关 |
| `nav.py` | 关卡导航 |
| `run_stage.py` | 一键：选作业 → 配补给 → 开打 → 验收 |
| `data/rarity.json` | 干员→星级 |
| `data/stage_map.json` | 显示编号→stage id |
