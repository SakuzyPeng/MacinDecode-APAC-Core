# 开发：构建、crate 结构与仓库约定

workspace 布局、`no_std` 构建、公开 API 分层，以及代码仓库与研究文档仓库的关系。

返回 [README](../README.md)。

## 构建

数值验收使用 Rust 1.98.0；macOS 的参考工具还需要 Xcode Command Line Tools。Windows／Linux 的纯 Rust 命令不需要苹果 SDK。

```sh
cargo build --offline
target/debug/mapac --help
```

当前机器所需依赖已缓存，可以离线构建。新机器首次构建可使用 `cargo build`。项目关闭开发构建的调试符号和增量编译，所有构建产物使用当前目录的 `target/`。

冻结数值原表与工具指纹按文件原始字节校验，`.gitattributes` 将文本检出固定为 LF；Windows 也保持相同字节，避免 `core.autocrlf` 改变已固定的摘要。

代码是一个 Cargo workspace，默认成员是 `mapac`，因此在根目录运行的 `cargo build` 只构建命令行工具；测试和检查请加 `--workspace` 或 `-p <crate>`：

| crate | 内容 |
| --- | --- |
| `crates/apac-core` | cookie 配置、帧解析（SQ、CAC、TNS、BWE2、DRC、HOA、ASP）与独立合成；不调用苹果接口；`no_std` + `alloc`，默认只依赖 sha2 与 libm |
| `crates/apac-cac` | CAC 逆混合（`rotate`）和 `apac-cac-math-v1` 旋转表；`no_std`，无运行时依赖。`apac-core` 的 `cac` feature 接入它，`mapac` 默认开启，`apac-research` 转发同名 feature |
| `crates/apac-container` | WAV／RF64／CAF 的流式 Float32 PCM 写出，以及 CAF／MP4 读取：对任意 `Read + Seek` 来源校验结构，输出类型化的轨道信息和逐包数据；打开时完整读一遍，之后每一遍读到末尾都与首遍核对。播放用的 `Media` 只读元数据、按游标读包、不核验完整性，`Playback` 在它之上用解码器检查点做按帧 seek，`Indexer` 在另一线程上为它建索引 |
| `crates/apac-research` | `parse-cookie` 报告组装、`parse-packets`／`decode-sq` 报告驱动、输入报告组装、包目录、导出限额、PCM 比较和测试信号 |
| `crates/apac-native` | macOS AudioToolbox 参考工具（`collect`、`replay`、`fixture`、`dump`、`decode` 等），其他系统上为空 |
| `crates/mapac` | `mapac` 命令行及调用它的集成测试 |
| `crates/apac-no-std-example` | 示例：在 `no_std` + `alloc` 库中用 `apac-core` 把包解码到调用方缓冲；不发布 |

`apac-core` 的报告与状态类型只在 `serde` feature 下派生 `Serialize`（`apac-research` 开启它；结构化字段值经 serde_json 渲染以保持键排序）。`apac_core::config::Config::parse` 只做类型化解析，不记录字段；`parse_recorded` 另外返回字段记录，`parse-cookie` 输出的报告由 `apac_research::config::parse_cookie` 组装。

`apac-core` 是 `#![no_std]` + `alloc` 库，只有单元测试链接 std。平方根使用 `libm::sqrt`：它与 `f64::sqrt` 一样按 IEEE 正确舍入，结果逐位相同。cookie 上限 8 MiB、包上限 16 MiB，因此 32 位目标上的位偏移不会溢出。发布前在两个无 std 目标上构建：

```sh
rustup +1.98.0 target add thumbv7em-none-eabihf wasm32v1-none
cargo +1.98.0 build -p apac-core --target thumbv7em-none-eabihf
cargo +1.98.0 build -p apac-core --target thumbv7em-none-eabihf --features serde
cargo +1.98.0 build -p apac-core --target wasm32v1-none
cargo +1.98.0 build -p apac-core --target wasm32v1-none --features serde
cargo +1.98.0 build -p apac-core --target thumbv7em-none-eabihf --features cac
cargo +1.98.0 build -p apac-no-std-example --target thumbv7em-none-eabihf
cargo +1.98.0 build -p apac-no-std-example --target wasm32v1-none
```

`apac-core` 的公开接口分三层：

- **解码**（crate 根）：`Config`、`Decoder`、`ParsedPacket`、`Checkpoint`、`StreamInfo`／`StreamKind`、`FrameInfo`、`AdvanceInfo`、`DecodeError`、`ParseError`、`ChannelLayout` 和 `MAX_PACKET_BUFFER`。`Decoder::parse` 与 `Decoder::synthesize` 把 `decode` 拆成解析和合成两段，供外层分别计时；`Decoder::checkpoint` 与 `Decoder::restore` 保存和恢复两包之间的状态，供 seek 索引使用。
- **检查**（`apac_core::inspect`）：报告层，含包／帧报告解析器、各类上下文（`FrameContext`、`ChannelFrameContext`、`HoaFrameContext`、`StreamFrameContext`、`DecodedFrameContext`）、带状态解析（`*_with_state`、`ParseMode`、`ScanWorkspace`、`DrcState`、`HoaState`、`StreamState`）、报告与状态类型和 `MetadataState`。`parse-packets` 和 `decode-sq` 的报告都由这一层组装。
- **标识**（`apac_core::identity`）：冻结的 profile 字符串和格式／数学表 SHA-256，与已发布报告中的值一致。

除 macOS 专用的 `apac-native`（AudioToolbox FFI）外，所有 crate 都通过 `[lints] workspace = true` 继承根 `Cargo.toml` 的 `unsafe_code = "forbid"`，测试、示例和构建脚本同样适用；新增 crate 也应继承。

`frame`、`synthesis` 是私有模块，没有 `#[doc(hidden)]` 接口。`apac-core` 和 `apac-container` 开启 `missing_docs`，所有公开项都有文档；序列化报告类型只在类型上写文档，字段即报告 JSON 的键。生成文档：

```sh
cargo +1.98.0 doc --no-deps -p apac-core -p apac-container --open
```

`crates/apac-no-std-example` 演示无 std 用法：只依赖不开 feature 的 `apac-core`（依赖链为 apac-core、libm、sha2），用 `Config::parse`、`Decoder::new`、`Decoder::decode` 把包解码到调用方提供的交错缓冲区。最终程序只需提供全局分配器，不需要文件系统、时钟或平台浮点库。示例不开 `cac`，因此只解码 CAC 增益全为 0 的帧；需要 CAC 时在依赖上加 `features = ["cac"]`，依赖链增加 `apac-cac`。

### CAC feature

CAC 的语法（增益索引与游程的 Huffman 码表）由 `apac-core` 读取和报告；按增益施加的 2×2 逆混合矩阵和旋转表在独立的 `crates/apac-cac` 中，经 `apac-core` 的 `cac` feature 接入。

- 开启时，行为与数值和拆分前逐位相同；`cac_tables_sha256` 等报告标识不变。
- 不开启时，增益全为 0 的帧不需要逆混合，照常解码；完整解码和状态扫描都在共用语法入口检查，第一个非零增益游程以 `cac-unavailable` 拒绝，位偏移指向该游程。`decode-sq` 的顺序和快速模式因此均返回退出码 1。
- `mapac` 默认开启；`--no-default-features` 构建不含 `apac-cac` 的工具。workspace 的单元测试、`apac-research`、`apac-container` 和 `apac-no-std-example` 的测试通过 dev-dependency 开启它。
- `apac_core::CAC_ENABLED` 查询的是 core 构建的实际能力，而非调用方 crate 的同名 feature。集成测试按这个值选择 CAC 用例，因此兼容 workspace 中 dev-dependency 的 feature 合并。
- 不含 CAC 的路径由 `apac-core` 的单元测试和单独运行的 `cargo +1.98.0 test -p mapac --no-default-features --test frame_parser` 覆盖；后者验证完整解码、状态扫描的错误一致性和失败恢复，不运行需要 CAC 运算的检查。`--workspace --no-default-features` 仍会被其他 crate 的 dev-dependency 开启 core 的 CAC，不能代替这项分包验收。

`native/audio_toolbox.c`、`data/` 和 `scripts/` 仍在仓库根目录。在非 macOS 主机上可以用 `APAC_NATIVE_RUST_CHECK=1 cargo check --workspace --target aarch64-apple-darwin` 对原生 crate 的 Rust 部分做类型检查；该开关跳过 C 编译，不能代替 macOS 上的构建与运行。

## CI 与构建产物

[Build 工作流](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml) 在推送 `main`、推送 `v*` 标签、提交 PR 及手动运行时触发。三个平台独立执行原生构建，固定使用 Rust 1.98.0、`Cargo.lock` 和 release profile；默认开启 CAC。每个任务运行打包回归、PCM 容器导出的 CLI 回归、workspace Rust 测试及 Clippy（`-D warnings`），并执行产物的 `--version`／`--help` 后上传。

| 平台 | Runner | Rust target | 压缩格式 |
| --- | --- | --- | --- |
| Linux x64 | Ubuntu 22.04 | `x86_64-unknown-linux-gnu` | `.tar.gz` |
| Windows x64 | Windows Server 2022 | `x86_64-pc-windows-msvc` | `.zip` |
| macOS arm64 | macOS 26 Apple Silicon | `aarch64-apple-darwin` | `.tar.gz` |

macOS 不构建 Intel 或 Universal 版本，任务会核验 runner 的实际架构。Windows 静态链接 CRT；Linux 在 glibc 2.35 环境构建，建议使用 Ubuntu 22.04 或更新的兼容系统。macOS 包包含 AudioToolbox 参考命令，Windows／Linux 提供纯 Rust 命令。

正式 CLI 版本从 [Releases](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/releases) 下载，文件名为 `mapac-vVERSION-TARGET`，附同名 `.sha256` 文件。

普通开发构建在成功运行的 **Artifacts** 中下载 `mapac-TARGET`。下载项包含 `mapac-TARGET-SHA12` 压缩包和同名 `.sha256` 文件，保留 14 天；需要时可对目标提交重新运行工作流。

压缩包包含可执行文件、两个 README、`guide/` 使用文档与发布说明、MIT／Apache-2.0 许可证、第三方来源说明和 `build-info.json`。构建信息记录完整提交、target、编译器版本、程序版本及程序 SHA-256，版本化安装包另记录 `release_tag`。打包使用显式文件清单，研究文档仓库、实验报告、音频、SDK 和本地缓存不进入产物；tar 包保留可执行权限。

Release 直接下载包和校验文件；Actions 产物先解开下载项的外层 ZIP。随后校验包，再解压内部压缩包：

```sh
# Linux
sha256sum -c mapac-v0.1.0-x86_64-unknown-linux-gnu.tar.gz.sha256
# macOS
shasum -a 256 -c mapac-v0.1.0-aarch64-apple-darwin.tar.gz.sha256
```

Windows 可用 PowerShell 的 `Get-FileHash PACKAGE.zip -Algorithm SHA256` 与 `.sha256` 文件中的值核对。包内程序位于顶层目录，直接运行 `./mapac --help` 或 `.\mapac.exe --help` 即可。命令中的版本及 `PACKAGE` 替换为实际文件名；开发产物使用包含提交短哈希的文件名。

### 准备 CLI Release

1. 更新根 `Cargo.toml` 的 workspace 版本、`Cargo.lock`，并编写 `guide/releases/vVERSION.md`。
2. 推送待发布提交，在 Build 工作流选择对应分支，填写手动输入 `release_tag`（首版为 `v0.1.0`）。也可推送同名 `v*` 标签触发。
3. 三个平台的构建、测试、Clippy 和打包均通过后，工作流核验全部安装包的 SHA-256，并创建带六个附件的 GitHub Release **草稿**。版本标签、workspace 版本和可执行文件 `--version` 必须一致。
4. 审阅发布说明和附件后，在 GitHub 发布草稿。手动输入不会提前创建 Git 标签；发布草稿时，GitHub 将不存在的标签创建在工作流的完整提交 SHA 上。已有同名草稿会更新附件、说明和目标提交；已发布的同名 Release 会明确拒绝，避免替换正式版附件。同一版本的草稿更新串行执行。

日常推送、PR 和不填写 `release_tag` 的手动运行只生成 Actions 产物。发布流程只分发 CLI 安装包，不执行 `cargo publish`。

需要本地打包时，使用 Python 3.11 或更新版本，复用已有二进制并指定输出目录：

```sh
python3 -B scripts/package_release.py --target aarch64-apple-darwin \
  --binary /path/to/shared-target/release/mapac \
  --commit FULL_COMMIT_SHA --release-tag v0.1.0 --out artifacts/release
```

## 仓库与数据边界

代码与研究文档使用两个独立的 Git 仓库，分别同步至各自远端：

- 当前仓库保存 Rust/C 源码、测试、构建配置、验证脚本和本 README。
- `docs/` 保存调查笔记和阶段总结，有自己的 Git 历史；整个目录被当前仓库忽略，不是子模块。只获取代码仓库时不会包含这些本地研究文档。
- `reports/`、`artifacts/`、`target/` 和本地 Xcode workspace 被代码仓库忽略。原始报告可能记录用户路径与音乐库文件名，分享时需另外生成脱敏版本。

| 仓库 | 远端 | 可见性 |
| --- | --- | --- |
| 代码 | [MacinDecode-APAC-Core](https://github.com/SakuzyPeng/MacinDecode-APAC-Core) | Public |
| 研究文档 | [MacinDecode-APAC-Docs](https://github.com/SakuzyPeng/MacinDecode-APAC-Docs) | Private |

从项目根目录使用 `git status` 检查代码，使用 `git -C docs status` 检查文档。研究结论在文档仓库中记录对应的代码提交，分别提交可以保持两套历史清晰。

首次获取项目时分别克隆，文档仓库放入已被代码仓库忽略的 `docs/`：

```sh
git clone https://github.com/SakuzyPeng/MacinDecode-APAC-Core.git
git clone https://github.com/SakuzyPeng/MacinDecode-APAC-Docs.git MacinDecode-APAC-Core/docs
```

两个仓库分别配置 `origin`。从项目根目录运行 `git push` 推送代码，运行 `git -C docs push` 推送文档。
