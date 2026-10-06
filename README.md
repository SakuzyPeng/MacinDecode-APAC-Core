# MacinDecode-APAC-Core

[English](README.en.md) | 中文

Apple Positional Audio Codec 独立解码核心 · Rust 2024 · Rust 1.98.0 · 核心 `no_std` · `unsafe` 禁用

## 这是什么

APAC（Apple Positional Audio Codec）是苹果用于空间音频的编解码器，承载声道音频与高阶 Ambisonics（HOA）。MacinDecode-APAC-Core 的目标是不调用苹果接口，在任意平台把 APAC 码流还原为**交错 Float32 PCM**：声道布局输出各声道，HOA 输出 ACN/SN3D 系数或码流声明的源声道；同时给出配置与逐包语法的结构化报告。

本项目**不负责**空间渲染、DRC／响度／EQ 音频处理或实时播放。

解码器按独立公式定义的固定数值模型实现，并刻意不复现苹果原生实现的数值细节，因此在设计上不与 AudioToolbox 参考逐位一致；两者按容差比较，原因见[与苹果参考的数值关系](guide/support.md#与苹果参考的数值关系)。

本项目是为互操作和研究目的独立编写的实现，与 Apple Inc. 不存在隶属、赞助或认可关系。Apple 和 AudioToolbox 是 Apple Inc. 的商标；文中提及 APAC 等名称仅用于说明兼容对象。

## 特性

- **容器与输入**：CAF v1、非分片单音轨 MP4／M4A 和导出的包目录；打开时完整读取并核验，每一遍读取都与首遍比对
- **配置解析**：magic cookie／ASC 的逐字段语法与 cookie 位位置，含 DRC、HOA、场景图和被动 renderer 元数据
- **声道解码**：频谱 Huffman、反量化、CAC、TNS、BWE2、IMDCT 与叠加，覆盖 Mono 至 22.2
- **HOA 解码**：零至三阶，salient／ambient、动态选择、空间控制、源布局还原，以及与声道组成的组合流
- **范围访问**：顺序解码或快速前缀扫描，双向 `seek`，结果与在该点新建的解码器逐位相同
- **独立数值模型**：常量由公式以高精度生成，固定运算顺序，确定性输出；SQ 反量化（`apac-sq-math-v2`）以 Float64 相乘后只舍入一次到 Float32
- **逐包语法报告**：`parse-packets` 按阶段输出字段、位偏移和中间频谱
- **`#![no_std]` 核心**：解码库只依赖 `alloc`、`sha2` 与 `libm`；CAC 逆混合是可选 crate，可以不编入构建
- **苹果参考对照（macOS）**：AudioToolbox 编解码、测试信号与 PCM 比较，用于验收

## 快速开始

### 前置条件

- Rust 1.98.0（[安装](https://rustup.rs/)；数值验收固定这一版本）
- macOS 上的参考工具另需 Xcode Command Line Tools

### 构建与测试

```bash
cargo build --release
cargo test --workspace
```

### 解码

```bash
# 解码全部有效音频：输出 pcm.f32le（交错小端 Float32）、pcm.json 和 decode-sq.json
# 长文件或多声道时用 --max-output-mib 提高默认 128 MiB 的输出上限
target/release/apac-tool decode-sq input.m4a --out decoded

# 解码一个窗口：从第 480000 帧起 8192 帧，快速定位
target/release/apac-tool decode-sq input.caf --out window \
  --start-frame 480000 --frames 8192 --access fast
```

### 作为库使用

```rust
use apac_core::{Config, Decoder};

let config = Config::parse(&cookie)?;          // magic cookie
let mut decoder = Decoder::new(&config)?;      // 不支持的配置在这里返回带原因的错误
let info = decoder.info();                     // 采样率、声道数、每包 1024 帧、布局
for packet in packets {
    let pcm: Vec<f32> = decoder.decode_vec(packet)?;  // 1024 × 声道数个交错样本
}
```

解码使用 CAC 的流（共享声道头的声道对）需要开启 `cac` feature：`apac-core = { ..., features = ["cac"] }`。`apac_core::CAC_ENABLED` 表示当前 core 构建在 Cargo 合并依赖 feature 后是否包含 CAC 逆混合；关闭时完整解码和快速前缀扫描都拒绝非零 CAC 增益。

从文件读取时，`apac_container::Reader` 封装了 CAF／MP4 读取、范围裁剪和双向 `seek`，完整示例见 `crates/apac-container/examples/decode_file.rs`；无 std 用法见 `crates/apac-no-std-example`。播放器使用 `apac_container::Playback`：打开时只读元数据，按帧精确 `seek`，代价由解码器检查点限定，索引可由 `Indexer` 在后台线程建立，示例见 `crates/apac-container/examples/playback.rs`，说明见[播放：`Media` 与 `Playback`](guide/decoding.md#播放media-与-playback)。API 文档：

```bash
cargo doc --no-deps -p apac-core -p apac-container --open
```

## 基础用法

以下是最常用的命令，全部子命令见[命令行参考](guide/commands.md)。

**解码为 PCM**——声道或 HOA 系数，交错 Float32：

```bash
target/release/apac-tool decode-sq input.m4a --out decoded
```

**查看配置**——magic cookie 的逐字段解析与位位置：

```bash
target/release/apac-tool parse-cookie cookie.bin
```

**逐包解析**——按阶段输出语法与中间频谱，例如到 CAC 为止：

```bash
target/release/apac-tool parse-packets packets/ --depth cac --output cac.jsonl
```

**比较 PCM**——按容差 `1e-6 + 1e-5·|reference|` 比较两份输出：

```bash
target/release/apac-tool compare reference/pcm.json decoded/pcm.json
```

结果写 stdout，进度和错误写 stderr。`--out` 必须是尚不存在的目录。退出码：`0` 成功；`1` 运行、输入或完整性错误；`2` PCM 超出容差，或解析结果为 `partial`／`unsupported`。

## 项目结构

```text
apac-tool ──→ research / core / native（仅 macOS）
apac-native ──→ research / core
apac-research ──→ container / core
apac-container ──→ core
apac-core ──→ cac（可选 `cac` feature）
apac-no-std-example ──→ core
```

| Crate | 职责 | `no_std` |
|---|---|---|
| [`apac-core`](crates/apac-core) | cookie／ASC 配置、包与帧解析（SQ、CAC 语法、TNS、BWE2、DRC、HOA、ASP、场景图）、独立 Float64 合成 | ✅ |
| [`apac-cac`](crates/apac-cac) | CAC 逆混合与 `apac-cac-math-v1` 旋转表；经 `apac-core` 的 `cac` feature 接入 | ✅ |
| [`apac-container`](crates/apac-container) | CAF／MP4 读取、完整性核验、帧范围计算与 `Reader` 解码循环；播放用的 `Media`／`Playback` | — |
| [`apac-research`](crates/apac-research) | `parse-cookie`、`parse-packets`、`decode-sq` 的报告组装，包目录、输出限额、PCM 比较、测试信号 | — |
| [`apac-native`](crates/apac-native) | macOS AudioToolbox 参考工具：采集、导出、回放、参考解码与测试信号编码 | — |
| [`apac-tool`](crates/apac-tool) | `apac-tool` 命令行及其集成测试 | — |
| [`apac-no-std-example`](crates/apac-no-std-example) | 不发布的示例：在 `no_std` 库中把包解码到调用方缓冲 | ✅ |

`cac` feature 是一道隔离边界：不开启时，`apac-core` 仍完整读取并报告 CAC 语法，只解码 CAC 增益全为 0 的帧，其余以 `cac-unavailable` 明确拒绝。`cargo build -p apac-tool --no-default-features` 构建不含 `apac-cac` 的工具。

### 平台

| 命令 | 平台 |
| --- | --- |
| `decode-sq`、`parse-cookie`、`parse-packets`、`compare` | Linux、Windows、macOS（纯 Rust） |
| `inspect`、`scan`、`collect-configs`、`dump`、`replay`、`decode`、`fixture` | 仅 macOS（AudioToolbox） |

## 数据流

```text
CAF / MP4 / 包目录
    → 容器读取与完整性核验                      (apac-container)
    → magic cookie → Config                     (apac-core::config)
    → 逐包解析：SQ 频谱 → CAC → TNS → BWE2       (apac-core::frame)
    → HOA 恢复、动态选择、源布局还原
    → IMDCT、窗口与叠加（Float64）               (apac-core::synthesis)
    → 交错 Float32 PCM
```

DRC、响度、场景图和 renderer 元数据只读取语法、不处理音频；状态按外层包原子提交，失败的包不留下半个包的状态。

## 支持范围

| 能力 | 状态 | 说明 |
|---|---|---|
| 配置语法 | ✅ | cookie／ASC、DRC、HOA、场景图、被动 renderer 元数据 |
| 声道解码 | ✅ | Mono／Stereo／5.1／7.1／7.1.4／22.2；SQ、CAC、TNS、BWE2 |
| HOA 解码 | ✅ | 零至三阶；salient／ambient、动态选择、空间控制、源布局还原；四阶及以上明确拒绝 |
| 组合流与共享配置 | ✅ | 多 ASC、HOA 与声道组合，最多 255 声道输出 |
| 容器与访问 | ✅ | CAF、非分片单音轨 MP4／M4A、包目录；顺序或快速范围解码 |
| 采样率 | ✅ | 索引 0–12（96 kHz–7.35 kHz）；部分 HOA 配置限 44.1／48 kHz |
| DRC／响度／EQ | 只读 | 读取并报告语法，不处理音频 |
| LRVQ | ⏸ | 延期；遇到时明确拒绝 |
| 外层 ASP 重配置、帧长索引 ≠ 0 | ✗ | 参考实现未实现，明确拒绝 |
| 空间渲染、实时播放 | — | 不在范围内 |

不支持的输入一律报错并说明原因（字段名、取值和 cookie 位位置），不会猜测或静默降级。完整边界见[支持边界](guide/support.md)。

## 设计原则

1. 不支持的路径明确拒绝，不猜测、不静默降级。
2. 输入默认不可信：容器整体核验，cookie 不超过 8 MiB、包不超过 16 MiB，资源上限明确报错。
3. 数值身份冻结：profile 字符串、常量位模式和浮点运算顺序是已发布结果的一部分；新行为使用新的 profile。
4. 常量由公式独立生成，或来自许可兼容的公开来源；与苹果参考按容差比较，逐位比较只用于本项目自身的跨平台、跨构建一致性。
5. 状态按外层包原子提交；失败不提交任何描述、映射、DRC 历史或叠加，`reset()` 恢复初始状态。
6. 可移植部分不调用苹果接口，并由 workspace lint `unsafe_code = "forbid"` 禁止 `unsafe`；AudioToolbox 参考代码只在 macOS 专用的 `apac-native` 中，它是唯一不继承这条 lint 的 crate。
7. 仓库不提交苹果二进制、反编译输出、SDK 文件、源媒体或含本机路径的原始报告。

## 文档

| 文档 | 说明 |
|---|---|
| [解码与库接口](guide/decoding.md) | `decode-sq`、CAF／MP4 输入、快速范围解码、`Decoder` 与 `Reader` |
| [命令行参考](guide/commands.md) | 全部子命令、导出文件与报告字段、退出码 |
| [码流解析](guide/bitstream.md) | `parse-packets` 各解析深度、声道与 HOA 的语法和数值标识 |
| [支持边界](guide/support.md) | 实现边界、与苹果参考的数值关系、共享配置、ASP 与帧长 |
| [验收与回归](guide/validation.md) | 测试、独立数学验收、重构回归与苹果参考诊断 |
| [HOA 黑盒批处理](guide/hoa-blackbox.md) | 三阶生产边界、断点续跑与历史测量证据 |
| [HOA 空间控制均值](guide/hoa-mean-blackbox.md) | 精确抵消测量、独立验证与外置卷证据 |
| [BWE2 黑盒重建](guide/bwe2-blackbox.md) | 增益重建、LSF 可识别性试验与冻结验证 |
| [开发说明](guide/development.md) | workspace 结构、`no_std` 构建、API 分层、CAC feature、仓库约定 |
| [第三方数据](THIRD_PARTY.md) | 格式常量的来源、许可与独立推导 |

## License

项目代码以 [MIT](LICENSE) 发布。`data/sq-codebooks.json` 中的 AAC Huffman 码表和频带边界取自 vo-aacenc，适用 Apache-2.0（见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)）；它们在构建时编入 `apac-core`，因此该 crate 声明 `MIT AND Apache-2.0`，其他 crate 为 `MIT`。

### 免责声明

- 仓库不包含苹果二进制、反编译代码、SDK 文件或源媒体。`data/` 中观测或转录所得的格式常量在各自文件内注明来源，第三方数据详见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- 解码器按自身数值模型输出，设计上不与苹果参考逐位一致。软件按“原样”提供，不附带任何明示或默示担保（见 [LICENSE](LICENSE)）。
- APAC 及相关音频技术可能受第三方专利保护。本项目的许可证不授予任何专利许可；在产品中使用或分发之前，请自行评估所在法域的专利与法律要求。
- 使用本项目处理音频时，请遵守相应内容的版权与许可条款。
