# MAA 通用机制与踩坑（所有玩法共用）

## MAA GUI 自动化（pywinauto UIA backend）

走 pywinauto 的 UIA backend 操作 WPF 控件（不要改 `gui.json`，MAA 退出时会覆写）。

### 不抢焦点：后台驱动 MAA

**脚本全程不需要把 MAA 置前**，你可以继续在别的窗口干活。实测结论：

| 操作 | 抢焦点？ | 说明 |
|---|---|---|
| 读控件（`element_info` / `window_text` / `get_toggle_state`） | 否 | 纯 UIA 读 |
| `iface_value.SetValue`（填路径框） | 否 | 替代 `send_keys`，不经过键盘队列 |
| UIA `Toggle` / `Invoke`（勾选框、按钮） | **是** | WPF 控件自己调 `Focus()`，绕不开 |
| 填 `prts://` 后 MAA 载入作业集 | **是** | MAA 内部行为，会激活自己的窗口 |

所以策略是「操作完立刻把焦点还回去」：`keep_focus()` 上下文管理器先快照前台窗口，
操作结束后用 `AttachThreadInput` 绕过系统的前台锁把焦点还回原窗口。

```python
with M.keep_focus():
    M.set_path(w, "prts://s55511", settle=3.0)   # settle 等 MAA 抓完作业集
```

- **不要用 `send_keys()` 输入文字**——它往当前有焦点的窗口打字。路径框用 `ValuePattern.SetValue`。
- **不要用 PostMessage 点击**。MAA 是 WPF，WPF 不处理 `WM_LBUTTONDOWN`，静默丢弃（已实测）。
- `CheckBox.click_input()` 常常**不翻转** WPF 的 toggle（模板吞掉事件）。用 `el.toggle()`。
- 「开始」按钮用 UIA `invoke()` 而不是 `click_input()`：后者移动真实鼠标，模拟器挡住 MAA 时点空。
- 作业页的 Tab 文字是**「自动战斗」**不是「作业」；`el.select()` 会静默失败，要用 `iface_selection_item.Select()`。
- 作业列表是虚拟化的，`descendants()` 会返回视口外的行（坐标是负的或极大），不能靠坐标判断可见性。
- `SetForegroundWindow` 抛 `pywintypes.error(1400)` 时 UIA click 依然有效，直接吞掉异常。

复选框清单（作业页）：`自动编队`、`使用编队`、`忽略干员属性要求`、`借助战`、
`补充低信赖干员`、`追加自定干员`、`使用一图流数据辅助编队`、`多作业模式`。

### wait_done 的判定顺序

错误标志（`任务出错`/`战斗出错`/`CopilotStartFailed`/`CopilotError`）**先于**
`Completed Task Chain` 检查——MAA 失败后也会写「完成任务: 自动战斗」，
只看完成行会把败仗当胜利。

## 作业 JSON 修补（patch_job）

有些作业在特定账号上必然跑不了，但有救：

```python
# 组里只剩自己确定拥有的干员（组内任选其一，MAA 会挑一个）
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
低了 MAA 解析直接抛 `CopilotFileReadError / 读取文件失败`：

```
parse_oper_usage | Oper 凯尔希·思衡托 has higher elite requirement: 2 , but elite requirement is set to 1
```

**反过来的坑：不要把全组 `elite` 一把改成 2。** 低星挂件（`Lancet-2`、`苏苏洛`、`安赛尔`…）
实际只有 E0/E1，提到 E2 会报一串 `干员不可用: X, 原因: 精英化不足` 然后停在选人页。
**正确做法是按 `asst.log` 点名的干员逐个改**，其余不动。注意改的是
`requirements.elite`（MAA 校验这个），顶层 `elite` 也写上最省事：

```python
def fix(o, elite):
    o["elite"] = elite
    o.setdefault("requirements", {})["elite"] = elite
```

同一个干员可能在多个组里出现且要求不同，逐个改时只看报错的那个组。

`run_stage.py` 已内置自动预检 `elite_conflicts()` / `raise_elite()`：只把被点名的人提到实际值。
`--no-auto-elite` 关掉预检。

## 干员名不匹配（最隐蔽的坑）

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

## 干员组是「任选其一」，且 actions 会引用组名

- `groups[].opers` 是候选池，不是必选清单；**整组只要有一个拥有就算满足**。
- `actions[].name` 里既有干员名，**也有组名**（`拦人先锋`、`单奶1`、`坦克`…）。
  把组名当干员名查 box 必然查不到——`feasible()` 已先把 `groups[].name` 排除。
- `actions` 里还有召唤物和装置（`魂灵之影`、`基质合成台`），统一在 `NON_OPER` 当挂件。
- `requirements.level` 是作者的练度，不是最低要求，等级差距只报告不定罪。

## 验收 OCR（不能只看 MAA 日志）

```python
cleared, screen = game.verify_result()
```

- 胜利：**裁剪区域** (40,230)-(760,480) OCR 出 `MISSION RESULTS` / `行动结束`
  （胜利文案是 `行动结束`，**不是** `MISSION COMPLETED`）。
- 失败：全屏 OCR `任务失败` / `MISSION FAILED`（失败画面裁区域反而读不出来）。
- OCR 用 Windows 自带 `Windows.Media.Ocr`（`winocr.ocr_image(PIL.Image)`，传图不传路径）。
- OCR 会把 `VEC` 读成 `VE0`/`VE008`，`detect_stage()` 里做了归一化容错。

## 自动找新活动入口

MAA 有一张在线活动表（活动名、起止时间、关卡代码）：

```bash
python copilot_api.py --acts      # 当前还在开的 SideStory 活动
```

数据源 `https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json`。
**注意**：`--acts` 只覆盖 SideStory，年度高难和剿灭**不在里面**。

`detect_stage()` 的关卡码正则覆盖常见前缀
（`VEC`/`YW`/`EX`/`S`/`WD`/`SV`/`CB`/`IW`/`ZT`/`RS`/`DV`），并且会修正 OCR 的
数字误识（`YW-EX-I` → `YW-EX-1`、`VE008` → `VEC-08`）。

拿到关卡代码后，stage id 规律 `<活动内部id>_ex01..ex08` / `_01.._08`，内部 id 从资源目录反查：

```bash
ls "$MAA_DIR/resource/Arknights-Tile-Pos" | grep -i "<关卡代码前缀>"
# 例如 YW -> act52side_ex01 ...
```

## adb 自由探索 UI 的纪律（两次实战学费）

**成功判定必须盯「资产/状态变化」，绝不看界面元素计数。**
任务页「点击领取」在这种列表控件上 input tap 几乎不触发，而动画和 toast 会让
按钮数量在 2/3/4 之间抖动——把抖动当进度就是死循环。正确做法：
领奖后核对龙门币/合成玉是否跳变，一次不成就放弃（非关键资源不值得缠斗）。

**未知 UI：一 tap 一截图。** OCR 对游戏的艺术字（开始行动/代理指挥这类按钮）经常
整个读不出来，像素采样比 OCR 可靠（勾选框：未勾深灰 `(38,38,38)`，勾选白
`(255,255,255)`）。换算缩略图坐标前先确认图片实际分辨率，否则差 1/3。

**开关可能联动布局。** 剿灭地图页勾「全权委托」后它自己的行会隐藏、
「代理指挥」的框反而显示为勾选（那是模式指示器，见 annihilation.md）。
每次状态切换后重新截图确认布局，再点下一个目标。

**错误恢复路径绝不盲点。** 流程中断后先判定当前页（OCR 特征字），再决定
back / 点空白 / 点固定按钮；不分青红皂白补点固定坐标，会点到别的页面的
危险按钮上（实测：错误路径的补点落在了编队页橙色开始按钮上）。

**卡住 3 次就停下来问人**，别把噪声当进度反复重试。

## 经验数据

- 高星作业核心常要求 90 级+专 3+模组（作者以自己的练度为判定标准），低练度必挂。
- 有装置点放动作（`Click` + `Swipe`）的作业**天然不稳定**，说明里会写「遇到重开」。
- 单人/双人作业最省心但吃单卡练度；多核作业容错高但缺人概率大。**优先挑「零缺口 + 高播放量」**。
- 一次完整战斗 1–15 分钟（悖论 1–3 分钟、剿灭 2 倍速约 15 分钟）；`wait_done` 超时给 480s 起。
- 缺一个 6★ 时勾「借助战」往往比换作业划算。
- 作业的 `doc.details` 第一行通常就是**补给/练度清单**，抄作业前先读它。
