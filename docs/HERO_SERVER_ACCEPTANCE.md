# 合版服务器验收记录

目标客户端为 Overwatch **1.74.0.0.104319**。本候选版使用各线共同开发的 Python 运行时。GitHub `main` 的 Rust 迁移有独立的覆盖范围。

**合版通过**指测试在本候选工作树运行成功。**线内通过**指对应私有版本有证据，合版仍需移植或重跑。**未完成**指仍有实现、冲突或整合工作。**未验收**指缺少该范围的合版测试或原版客户端证据。

## 合版通过

- **里阿尔托 / McCree**：生成、移除、准星选择、停止攻击、掩体与导航、尸体留存、左键、右键、近战、翻滚、闪光弹和午时已到有定向服务器回归。历史原版窗口验收覆盖了五种 NPC、主要战斗流程以及部分客户端表现。合版尚未重跑原版客户端矩阵。
- **Ana**：两个 Biotic Rifle 输入路径可伤害敌人，也可治疗受伤的友军玩家和 NPC。服务器测试覆盖满血友军透过、尸体不复活和治疗上限。
- **Mei**：喷雾与冰锥伤害、冰锥爆头、阵营、掩体、保留尸体和被攻击有服务器回归。爆头判定沿用服务器的简化模型。
- **Winston**：Tesla Cannon 的输入、持续伤害、范围、掩体、多目标、阵营、装甲和 NPC 交互有服务器回归。目标锥和持续伤害装甲规则为重建。
- **Pharah**：7 项补入的原线测试通过，覆盖火箭直击与溅射、掩体两侧、友军、NPC 尸体留存及被攻击。火箭冲量和其它技能未纳入此次复测。
- **Genji 线共享机制**：相同数值的状态栈插入与移除可以刷新优先级条件，普通数值监听和快照仍通过回归。该结果只覆盖共享机制。
- **Mercy**：被动回血、受击重置、死亡与换英雄、输入重放及基础伤害有定向回归。杖、飞行、复活和大招仍待整合或验收。
- **Lúcio**：音波伤害、范围、静态遮挡、阵营和 NPC 交互有定向回归。角度判定为服务器重建，击退和其余技能未纳入此次验收。

## 万圣节原版验收

在独立服务器上使用同一份合成运行时。原版 104319 客户端以 1280×720 窗口进入街机先锋 → 怪鼠复仇 → 普通，选择麦克雷后进入第一人称场景。实际播放了怪鼠登场、机械僵尸镜头和「弗兰狂斯鼠复仇记」片名，随后出现「守住大门」与倒计时。服务端记录了 C9A3 的地图加载、英雄选择和首波战斗，包含机械僵尸击杀玩家和玩家击杀机械僵尸。验收覆盖进图、开场过场和首波战斗。整局通关、所有难度及变体仍待验收。

## 各线范围

| 线 | 合版纳入及通过 | 线内证据及未完成事项 |
| --- | --- | --- |
| 0002 Reaper | 待整合 | Death Blossom、护盾及反射交互有局部测试。完整交互矩阵及相关补丁合版待验收。 |
| 0003 Tracer | 待整合 | 手枪、Blink、Recall、Pulse Bomb、充能及受击有线内实现或局部测试。完整回放及交互矩阵仍开。 |
| 0004 Mercy | 被动回血及基础战斗局部通过，来源 `e9f55f0` | Staff、Descent、Valkyrie、飞行与复活仍待整合或验收。 |
| 0005 Hanzo | 待整合 | 方向移动修正及弓箭链有局部证据。原版攀爬接触、箭矢与防御交互矩阵仍开。 |
| 0006 Torbjörn | 待整合 | Rivet、Overload、炮塔、Molten Core 与 Matrix 局部测试。合版缺少投射物测试依赖及对应移植。 |
| 0007 Reinhardt | 待整合 | Hammer、Fire Strike、Charge、Barrier、Earthshatter 有线内验收记录。完整矩阵和合版待验收。 |
| 0008 Pharah | 主火箭、静态掩体、NPC 和被攻击局部通过，来源 `e77d37b` | 火箭冲量、其他技能及原版表现待移植或验收。 |
| 0009 Winston | Tesla Cannon 局部通过 | Barrier、Primal Rage、防御状态组合及原版效果仍开。来源 `33333e7`。 |
| 000A Widowmaker | 待整合 | 狙击与 D.Va 破甲、机甲生命周期有私有线证据。当前全矩阵、原版表现和合版待验收。 |
| 0015 Bastion | 待解决共享战斗与 NPC 代码冲突 | 交付 `034e5ff` 标记线内静态通过。原版客户端及合版尚未验收。 |
| 0016 Symmetra | 待整合 | 主射、蓄能球、炮塔和传送有局部实现或测试。共享动作冲突及完整静态矩阵仍开。 |
| 0020 Zenyatta | 待整合 | Orb、充能、反射和控制有局部回归。完整静态矩阵仍开。 |
| 0029 Genji | 共享状态栈优先级监听和快照回归通过，来源 `e4b86fa` | Deflect、防御池、Dragonblade 和终极充能有线内证据。战斗补丁及完整矩阵仍开。 |
| 0040 Roadhog | 待整合 | Hook、Breather、Whole Hog 的冷却与 Hack/EMP 打断有线内测试。EMP 屏障及完整矩阵仍开。 |
| 0042 McCree / Cassidy | 里阿尔托定向回归通过 | 原版剧情流程、狙击弹曳光精确落点及独立音轨、完整玩家对战矩阵和合版客户端复测仍开。 |
| 0065 Junkrat | 待整合 | Steel Trap 的受害者停止和释放有线内回归。完整英雄静态矩阵仍开。 |
| 0068 Zarya | 待整合 | 光束、Graviton、屏障及能量有线内覆盖。共享装甲规则、完整矩阵及合版待验收。 |
| 006E Soldier: 76 | 基础共享战斗回归覆盖部分射击与战斗流程 | 线内有 PvE 射击、视线及索敌恢复证据。该线后续补丁、完整 PvP/PvE 和原版客户端待验收。 |
| 0079 Lúcio | Soundwave 伤害及基础战斗局部通过，来源 `e491df6` | Speed Controller 与 Amp 有线内验证。击退、共享 mover 改动及完整矩阵仍开。 |
| 007A D.Va | 待整合 | 机甲、驾驶员、Matrix 以及 NPC 武器有线内证据。合版和完整验收仍开。 |
| 00DD Mei | 喷雾、冰锥伤害、爆头和双向基础战斗局部通过 | 冰墙、急冻、暴雪和完整交互待移植或合版验收。来源 `22c3f5b`。 |
| 012E Sombra | 待整合 | Hack、EMP 及伤害打断有线内工作。完整静态矩阵和合版复测仍开。 |
| 012F Doomfist | 待整合 | 私有线有受控候选及原版逻辑探针。完整静态目标仍开。 |
| 013B Ana | Biotic Rifle 局部通过 | 睡眠针、禁疗、强化及原版反馈待移植或验收。来源 `ce56a00`。 |
| 013E Orisa | 待整合 | Fusion Driver 与 Halt 对部分免疫和接触的线内结果存在。反射、吸收与原版规则仍开。 |
| 0195 Brigitte | 待解决共享战斗与状态脚本冲突 | 交付 `a3004ec` 标记线内静态通过。原版客户端及合版尚未验收。 |
| 01A2 Moira | 待整合 | Coalescence 几何及 Orb 分类有线内检查。完整能力与防御交互仍开。 |
| 01CA Wrecking Ball | 待整合 | Adaptive Shield、装填控制和被动充能有线内测试。原版筛选、时钟及完整矩阵仍开。 |
| 0200 Ashe | 待整合 | Viper 与 B.O.B. 的 Sleep、Flashbang、装填交互有线内测试。原版表现和完整矩阵仍开。 |
| 0206 Echo | 待解决共享战斗与投射物冲突 | 交付 `fe78773` 标记线内静态通过。原版客户端及合版尚未验收。 |
| 0221 Baptiste | 待整合 | Field 与 Assassin 等有局部证据。交付状态仍为未就绪，完整静态验收仍开。 |
| 023B Sigma | 待整合 | Hypersphere 弹跳、部分伤害及屏障条件有线内验证。碰撞、Grasp、终极技能与完整矩阵仍开。 |

## 运行与复测

用 `python -B tools/prepare_combined_runtime.py --junkenstein <Junkenstein 运行时> --rialto <里阿尔托运行时> --output <外部新目录>` 合成外部 104319 运行时。设置 `ORPHEUS_RUNTIME_DIR` 指向输出目录，并将 `ORPHEUS_NPC_CATALOG` 指向其中的 `data/pve_npcs_174.json`。默认端口空闲时可从该工作树运行 `START_SERVER_LOCAL.cmd`。原版客户端复测单独使用 1280×720 窗口模式。

早期使用里阿尔托运行时执行 196 项测试，193 项通过。3 项 Junkenstein 测试缺少场景图。合成运行时补齐了所需场景图，含这 3 项的 52 项场景、入口、NPC 和街机回归全部通过。独立端口的服务器启动检查确认 Battle.net 和大厅可连接、游戏 UDP 已绑定、仪表盘返回 HTTP 200。

后续增量通过 69 项状态脚本、快照、麦克雷及战斗回归，另有 7 项 Pharah 测试和 1 项 Practice Range 连接、世界创建与 Soldier 输入用例通过。

合入 Mercy 和 Lúcio 后，使用合成运行时重跑的 119 项战斗、场景、入口、NPC、快捷键及街机回归全部通过。

合版测试包括 `test_game_ana`、`test_mei_combat`、`test_game_winston`、`test_game_combat`、`test_game_npc`、`test_npc_navigation`、`test_game_npc_control`、`test_npc_hotkeys`、`test_mccree_gun`、`test_mccree_roll`、`test_mccree_eye`、`test_pve_entrance`、`test_game_scene_host` 和 `test_arcade`。运行 `unittest` 时将工作树的 `tests` 目录加入 `PYTHONPATH`。

原版呈现的既有证据与未验收范围见 [PvE combat sandbox](PVE_COMBAT_SANDBOX.txt) 和 [McCree combat](MCCREE_COMBAT.txt)。
