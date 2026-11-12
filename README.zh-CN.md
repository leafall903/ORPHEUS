# ORPHEUS

[English](README.md)

ORPHEUS 是面向 **《守望先锋 1》正式服 1.74.0.0.104319** 的开源服务端复刻项目，独立开展非官方研究与游戏保存工作。长期目标是还原该版本各英雄、地图和模式的服务端行为，以及服务端向客户端发送的事件。服务端仍在开发中，首个端到端验证场景是士兵 76 与训练靶场。

[在本地训练靶场测试技能](#本地训练靶场) · [加入现有服务器](#加入现有服务器)

[参与贡献](CONTRIBUTING.md) · [路线图](docs/ROADMAP.md) · [Rust 分支](https://github.com/leafall903/ORPHEUS/tree/rust) · [验证记录](docs/STATE.md) · [更新日志](CHANGELOG.md) · [待处理问题](https://github.com/leafall903/ORPHEUS/issues)

## 在本地运行

`main` 是由社区维护的 Python 服务端与 Windows 启动器分支。Rust 原生服务端在独立的 `rust` 分支继续开发。

### 本地训练靶场

每位测试者在自己的 Windows 电脑上运行 ORPHEUS 和原版客户端。准备 64 位 Python 3.12、已获授权的 **1.74.0.0.104319** 游戏安装，以及准备好的训练靶场运行数据。[下载 Python 源码 ZIP](https://github.com/leafall903/ORPHEUS/archive/refs/heads/main.zip)

#### 放置并预检运行数据

解压 ORPHEUS，将准备好的数据包放入解压目录下的 `runtime-data/`。让 `data/`、`bnet/` 和 `collision/` 直接位于 `runtime-data/` 下。

```text
ORPHEUS/
  runtime-data/
    manifest.json
    data/
    bnet/descriptors.pb
    collision/0688.col
```

ORPHEUS 会在该目录含有 `manifest.json` 或 `data/` 时自动识别数据包。显式设置的 `ORPHEUS_RUNTIME_DIR` 优先，其次是自动识别，最后使用旧版目录结构。在 ORPHEUS 目录执行启动预检，查看缺失的启动输入和签名输入长度检查结果。地图碰撞会在加载场景时检查。

```powershell
.\scripts\START.bat --check-runtime
```

#### 首次编译 Windows 中继

需要本地编译 `relay/owwfd_relay.dll` 时，先安装 x64 MSVC Build Tools，打开 x64 Native Tools 命令提示符，在 ORPHEUS 目录执行。

```bat
.\relay\build.bat
```

#### 启动游戏并测试技能

把游戏安装目录或其中的 `_retail_\Overwatch.exe` 传给启动器。省略路径时，启动器会使用已保存的客户端路径或打开文件选择框。

```powershell
.\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
```

启动器会在需要时创建 Python 环境，并保存所选安装目录，供下次启动和准备碰撞数据使用。进入训练靶场，选定要测试的英雄，每次记录一个技能的准确操作和实际结果。[原版客户端验证步骤](CONTRIBUTING.md#original-client-verification)

### 独立的部署数据

完整的授权运行数据包括协议数据、签名所需数据、玩法状态脚本图和地图碰撞缓存。运行数据指南列出了每个必需文件的来源与准备方式。仅从游戏目录生成完整运行数据仍是待完成的任务。[运行数据指南](data/README.md)

- `tools/prepare_client_catalogs.py` 使用指定版本的 DataTool，从目标游戏安装在本地提取四项 JSON 目录数据。[生成客户端目录数据](data/README.md#generate-client-catalogs)
- `tools/prepare_server_runtime.py` 打包运行数据并写入清单。可选的游戏目录和 DataTool 参数能在本地补齐缺少的目录、玩法数据和地图缓存。其余授权输入需事先放入源目录。[打包时生成缺项](data/README.md#generate-missing-client-data-while-packing)

从 ORPHEUS 目录运行以下命令，即可使用已获授权的准备目录，在默认位置生成一份新的靶场数据包。

```powershell
py -3.12 -B tools/prepare_server_runtime.py --source 'C:\path\to\prepared-checkout' --destination .\runtime-data --map 0688
```

### 纯服务端检查

放置运行数据后，可执行 `scripts/START_SERVER_LOCAL.cmd` 启动本地回环服务。管理面板地址为 `http://127.0.0.1:3725`。此入口需要 Python 环境和准备好的运行数据。

### 加入现有服务器

在 Windows 上准备 **1.74.0.0.104319** 客户端和 64 位 Python 3.12，向服主获取正在运行的兼容 ORPHEUS 服务器地址。授权的服务端运行数据由服主准备。玩家端使用游戏目录和服务器地址，无需准备本地服务端运行数据或 Windows 中继。

在同一个 ORPHEUS 目录下打开 `scripts/JOIN_SERVER.cmd`，输入 `主机:端口`，并在提示时选择游戏客户端。也可以在 PowerShell 直接传入服务器地址和游戏目录。

```powershell
.\scripts\JOIN_SERVER.cmd --server 'HOST:3724' --game-exe 'C:\path\to\Overwatch'
```

启动器以简化的锦标赛模式运行客户端，仍待原版客户端验证。

### 公开源码检查

文档以 Windows 和 64 位 Python 3.12 为运行环境。以下源码测试使用合成数据。单独的管理面板测试使用 Node.js 24。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m unittest discover
```

Python 的默认测试发现只运行公开测试。管理面板测试和本地运行时回归见[贡献者测试说明](CONTRIBUTING.md#public-source-checks)。

## 当前进度

| 领域 | 服务端实现与测试 | 原版客户端证据与待办 |
| --- | --- | --- |
| 本地登录与大厅 | 模拟本地认证，支持大厅、英雄选择和对局交接。自动检查覆盖握手及使用已准备的运行数据启动服务端。 | 已观察到登录、训练靶场加载、选择或切换英雄。更多大厅流程及对局规则仍待验证。 |
| 士兵 76 | 已实现角色身体和武器状态、移动与疾跑、步枪射击与装填、螺旋飞弹和生物力场。自动化测试覆盖部分场景，包括反复换人及重进、丢包和弹药状态。 | 测试者曾射击并击杀训练机器人。武器显示的稳定性、弹药界面、输入响应和技能行为仍待完整实机验证。 |
| 训练靶场战斗 | 已实现命中与伤害判定、机器人生命、死亡和重生，并通过网络同步原版靶场机器人的头顶血条显示图。自动化测试使用受控测试数据及目标地图的碰撞数据。 | 测试者反馈头顶血条缺失、消灭提示无目标名、右上击杀栏缺失和爆头音效缺失。血条显示图的改动已取得报文证据，实际渲染仍待记录实机结果。 |
| 网络与研究 | 已实现加密 UDP、玩家输入、实体生命周期、服务端状态快照和回放，并开展了部分丢包和乱序检查。 | 客户端预测、战斗反馈、双端一致性、长时间运行和更多地图的行为仍待验证。 |
| 英雄、模式与音频 | 已具备共用机制，并覆盖部分功能。 | 完整英雄技能、地图目标、对局流程、命中与连杀反馈和情境语音仍在开发中。 |

自动化结果适用于证据中列出的测试场景或回放。手动观察适用于实际使用的版本、提交和操作步骤。首个目标场景的完整原版客户端验收仍待完成。详见[验证记录](docs/STATE.md)和[更新日志](CHANGELOG.md)。

## 如何参与

- **开发者和使用 AI 工具的贡献者**可以从公开源码测试入手。贡献指南涵盖 Codex、Claude Code，并为所有提交规定统一的审查要求。[贡献指南](CONTRIBUTING.md)
- **逆向研究者**可以帮助确认 104319 版本中的单个字段、状态切换、事件或时序规则。请从相关证据出发，并明确说明尚不确定的部分。[研究证据](docs/RESEARCH_EVIDENCE.txt)
- **玩家**可以在自己的电脑上启动训练靶场，提交简短且可复现的游玩结果。欢迎同时报告正常行为和缺陷。[游玩验证表单](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml)
- **初次参与者**可以查找只需公开源码和合成数据即可完成的任务。[适合入门的问题](https://github.com/leafall903/ORPHEUS/labels/good%20first%20issue)

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `src/ow174/bnet`、`src/ow174/lobby` | 本地登录、账号、社交和大厅服务、匹配与对局交接 |
| `src/ow174/game`、`src/ow174/game/script` | 游戏加密通信、输入、模拟、战斗及状态脚本同步 |
| `src/ow174/dashboard`、`relay` | 本地管理和 Windows 客户端中继源码 |
| `scripts`、`src/orpheus` | Windows 启动脚本和安装后的 ORPHEUS 入口 |
| `tests/public`、`tests/runtime` | 公开测试和本地运行时回归 |
| `tools`、`docs` | 提取、回放工具、研究记录及致谢 |

[路线图](docs/ROADMAP.md)跟踪整个目标版本的还原范围。[致谢](docs/CREDITS.md)记录了原始 1.74 大厅项目、研究来源和上游作者。公开源码保留原有的 [MIT 许可证声明](LICENSE)。《守望先锋》及其资源归各自权利人所有。请将游戏客户端、商业游戏资源、密钥、私有日志、原始抓包和转储留在本地。
