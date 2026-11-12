# ORPHEUS

[English](README.md)

ORPHEUS 是面向 **《守望先锋 1》正式服 1.74.0.0.104319** 的独立开源服务端复刻项目，目标是保存该版本的服务端行为和客户端事件。首个端到端验收目标是士兵 76 和训练靶场。

> **Rust 开发者预览。** 公开源码可以编译并运行合成测试。启动原生服务端需要已准备好的、获授权的 104319 基础运行数据。可公开复现的原版客户端认证流程和 Rust 一键启动器仍待完成。

本分支是 Rust 版，只包含原生服务端及贡献者所需的源码。[默认分支](https://github.com/leafall903/ORPHEUS/tree/main)包含社区维护的 Python 服务端（同时作为行为参考）及其 Windows 启动器、测试和数据工具。

[参与贡献](CONTRIBUTING.md) · [Python/Rust 对等清单](docs/RUST_PARITY.md) · [架构](docs/NATIVE_SERVER_ARCHITECTURE.md) · [Rust 开发规则（英文）](docs/NATIVE_SERVER_ARCHITECTURE.md#rust-contribution-rules) · [路线图](docs/ROADMAP.md) · [更新日志](CHANGELOG.md) · [待处理问题](https://github.com/leafall903/ORPHEUS/issues)

## 当前进度

| 范围 | Python `main` | Rust `rust` | 原版客户端证据 |
| --- | --- | --- | --- |
| 登录与单人靶场 | 使用准备数据可运行 Windows 启动器、本地登录、大厅和对局交接。 | 原生回环登录、大厅、世界创建和加密 UDP 已通过准备数据的无客户端测试。 | 早期 Rust 实机曾进入靶场。最新 Rust 版本仍待实机复测。 |
| 士兵 76 身体与武器 | 参考实现可运行原版身体脚本路径，覆盖部分武器、疾跑和技能行为。 | 原版步枪、装填、疾跑和螺旋飞弹时序有独立回归。原版身体仍卡在尚未支持的特效动作。对局测试使用夹具武器。 | Rust 实机曾报告武器缺失、技能无法使用。 |
| 机器人战斗与飞弹 | 靶场命中、伤害和重生有参考测试。 | 加密夹具对局验证命中、丢包恢复、死亡、重生和飞弹伤害。机器人和飞弹物理由房间 Bevy ECS 管理，Lua 负责巡逻决策。 | 原版客户端的伤害表现和二楼巡逻反馈仍待验证。 |
| HUD、音频和终极技能充能 | 有部分 HUD 报文证据和社区估算的终极技能充能。 | 有战斗消息和预测封包的定向测试。完整 HUD、音频和充能仍待对齐。 | 历史试玩报告过头顶血条、带名字的消灭提示、击杀栏和爆头音效缺失。 |
| 调试与负载 | 有本地管理面板和 Python 参考回放。 | 可选调试面板显示实体、碰撞采样、巡逻裁切、tick 耗时及有数量上限的房间故障记录。公开与本地运行时测试验证了一个房间故障后另一个房间仍能运行。 | 这些检查使用独立单人房间和人为故障注入。同房间的原版客户端多人验收仍待进行。 |

夹具、报文和原版客户端实机的证据各有范围。详见[对等清单](docs/RUST_PARITY.md)、[验证记录](docs/STATE.md)和[迁移指南](docs/RUST_MIGRATION.md)。

## 先跑公开 Rust 测试

安装 Git 和稳定版 Rust。Windows 还需要 x64 MSVC C++ Build Tools 和 Windows SDK 来编译内置的 Lua 运行时。公开 Rust 测试使用合成夹具，无需游戏文件或准备数据。

```powershell
git clone --branch rust https://github.com/leafall903/ORPHEUS.git
cd ORPHEUS
cargo test --locked --workspace --all-targets
```

[贡献指南](CONTRIBUTING.md#public-source-checks)列出了公开 CI 运行的默认和 `inspector` 两组检查。

## 启动原生服务端

### 独立的部署数据

需要已准备好的、获授权的 **104319 基础靶场运行包**。公开源码和游戏目录仍缺研究数据及受限本地输入。Rust 打包工具负责复制现有准备数据并生成 SHA-256 清单。详见[三档运行数据清单](data/README.md)。

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --check-runtime
cargo run --locked --bin orpheus_server -- --data-dir 'C:\path\to\local-state'
```

自检按类别列出缺项，并检查签名输入长度。服务端会在开启本地回环监听前解析运行数据，可以独立于游戏安装和中继 DLL 启动。本地状态目录保存账号和服务端状态，每个运行中的实例应使用独立目录。

默认构建关闭只读调试面板。需要使用时，启用 Cargo 的 `inspector` 功能，并指定本地端口。

```powershell
cargo run --locked --features inspector --bin orpheus_server -- --data-dir 'C:\path\to\local-state' --debug-port 3726
```

启动后可在 [127.0.0.1:3726](http://127.0.0.1:3726/) 打开面板。[架构文档](docs/NATIVE_SERVER_ARCHITECTURE.md#collision-and-live-inspection)列出了采样限制。实机验收需要已独立配置的本地连接和准确的 104319 客户端。[验收流程](CONTRIBUTING.md#original-client-verification)

## 如何参与

- **开发者和使用 AI 工具的贡献者**可以从公开 Rust 测试入手。[贡献指南](CONTRIBUTING.md)
- **逆向研究者**可以针对 104319 记录单个字段、事件或时序规则。[研究证据](docs/RESEARCH_EVIDENCE.txt)
- **已配置本地环境的玩家**可以提交可复现的实机观察，附上分支、提交号、客户端版本和操作步骤。[游玩验证表单](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml)
- **初次参与者**可以查找只需公开源码和合成夹具的任务。[适合入门的问题](https://github.com/leafall903/ORPHEUS/labels/good%20first%20issue)

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `rust/src`、`rust/scripts` | 原生服务端、单元测试和 Lua 巡逻策略 |
| `rust/crates/collision` | 碰撞缓存解码、静态查询和接触求解器 |
| `rust/crates/runtime` | 外部运行数据清单、自检和独立打包工具 |
| `rust/tests` | 原生 CLI 集成测试 |
| `relay` | 连接原版客户端所需的 C++ 中继源码 |
| `docs` | 架构、验收条件和研究记录 |

ORPHEUS 是独立的非官方保存项目。公开源码保留上游 [MIT 声明](LICENSE)和[署名](CREDITS.md)。请将游戏客户端、商业资源、签名输入、内容密钥、私有日志、抓包和转储留在本地。
