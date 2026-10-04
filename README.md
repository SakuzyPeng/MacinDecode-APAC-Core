# MacinDecode-APAC-Core

Apple Positional Audio Codec（APAC）的独立 Rust 实现：码流解析、PCM 解码与分析工具。

> **实验性项目**，与 Apple 无关。解码器按独立公式定义的固定数值模型实现，不调用苹果音频接口，并刻意不复现苹果原生实现的数值细节，因此在设计上不与 AudioToolbox 参考逐位一致；SQ 默认数值配置为 `apac-sq-math-v2`，反量化相乘后只舍入一次到 Float32。两者的差异由验收脚本按容差统计，原因见 [guide/support.md](guide/support.md#与苹果参考的数值关系)。

- **`apac-core`**：`#![no_std]` + `alloc` 的解码库，解析 magic cookie 并把数据包解码为交错 Float32 PCM。不依赖文件系统、时钟或平台浮点库，可在嵌入式和 WebAssembly 目标上构建。
- **`apac-cac`**：CAC（声道对齐编码）的逆混合运算，独立成 crate。`apac-core` 通过 `cac` feature 接入它，`apac-tool` 默认开启。不开启时仍完整读取和报告 CAC 语法，但遇到使用非零 CAC 增益的帧会明确拒绝。
- **`apac-container`**：CAF 与非分片 MP4／M4A 读取，支持顺序与快速范围定位。
- **`apac-tool`**：命令行工具。在 Linux、Windows 和 macOS 上把 APAC 文件解码为 PCM，并输出配置与逐包语法报告；macOS 上另有基于 AudioToolbox 的参考编解码，用于对照验证。

## 支持范围

| 能力 | 状态 |
| --- | --- |
| Mono／Stereo／5.1／7.1／7.1.4／22.2 声道解码（SQ、CAC、TNS、BWE2） | 支持 |
| HOA 零至十阶系数解码（salient／ambient、动态选择、空间控制）及 HOA 与声道的组合流 | 支持 |
| 输入：CAF、非分片单音轨 MP4／M4A、导出的包目录；顺序或快速范围定位 | 支持 |
| 采样率索引 0–12（96 kHz–7.35 kHz；部分 HOA 配置限 44.1／48 kHz） | 支持 |
| DRC、响度、场景图与 renderer 元数据 | 只读取语法，不处理音频 |
| LRVQ、外层 ASP 重配置、帧长索引 ≠ 0 | 明确拒绝 |
| DRC／响度／EQ 音频处理、空间渲染、实时播放 | 不在范围内 |

CAC 逆混合由 `apac-cac` 提供；不含它的构建只解码所有 CAC 增益为 0 的帧，其余明确拒绝。

不支持的输入一律报错并说明原因（字段名、取值和 cookie 位位置），不会猜测或静默降级。完整边界见 [guide/support.md](guide/support.md)。

## 快速开始

使用 Rust 1.98.0（edition 2024；数值验收固定这一版本）。

```sh
cargo build --release

# 解码全部有效音频：输出 pcm.f32le（交错小端 Float32）、pcm.json 和 decode-sq.json
# 长文件或多声道时用 --max-output-mib 提高默认 128 MiB 的输出上限
target/release/apac-tool decode-sq input.m4a --out decoded

# 解码一个窗口：从第 480000 帧起 8192 帧，快速定位
target/release/apac-tool decode-sq input.caf --out window \
  --start-frame 480000 --frames 8192 --access fast

# 查看配置（magic cookie）的逐字段解析
target/release/apac-tool parse-cookie cookie.bin

# 不含 CAC 逆混合的构建
cargo build --release -p apac-tool --no-default-features
```

`--out` 必须是一个尚不存在的目录，导出总量默认上限 128 MiB（`--max-output-mib` 可调）。退出码：`0` 成功；`1` 运行、输入或完整性错误；`2` PCM 超出容差，或解析结果为 `partial`／`unsupported`。

## 作为库使用

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

需要从文件读取时，`apac_container::Reader` 封装了 CAF／MP4 读取、范围裁剪和双向 `seek`；完整示例见 `crates/apac-container/examples/decode_file.rs`。无 std 用法见 `crates/apac-no-std-example`。API 文档：

```sh
cargo doc --no-deps -p apac-core -p apac-container --open
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [guide/decoding.md](guide/decoding.md) | `decode-sq`、CAF／MP4 输入、快速范围解码、`Decoder` 与 `Reader` 接口 |
| [guide/commands.md](guide/commands.md) | 全部子命令、导出文件与报告字段、退出码 |
| [guide/bitstream.md](guide/bitstream.md) | `parse-packets` 各解析深度、离散声道与 HOA 的语法和数值标识 |
| [guide/support.md](guide/support.md) | 实现边界、共享配置与组合 HOA 流、ASP 与帧长 |
| [guide/validation.md](guide/validation.md) | 测试、独立数学验收、重构回归与苹果参考诊断 |
| [guide/development.md](guide/development.md) | workspace 结构、`no_std` 构建、API 分层、仓库约定 |

## 平台

| 命令 | 平台 |
| --- | --- |
| `decode-sq`、`parse-cookie`、`parse-packets`、`compare` | Linux、Windows、macOS（纯 Rust） |
| `inspect`、`scan`、`collect-configs`、`dump`、`replay`、`decode`、`fixture` | 仅 macOS（AudioToolbox；需要 Xcode Command Line Tools） |

跨目标编译不能代替运行验收；是否达到跨平台逐位一致，以对应提交的完整验收报告为准。

## 许可

项目代码以 [MIT 许可](LICENSE) 发布。

`data/sq-codebooks.json` 中的 AAC Huffman 码表和频带边界取自 vo-aacenc，适用 Apache-2.0 许可（见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)）。这些表在构建时编入 `apac-core`，因此该 crate 的许可声明为 `MIT AND Apache-2.0`，其他 crate 为 `MIT`。

## 免责声明

- 本项目是为互操作和研究目的独立编写的实现，与 Apple Inc. 无关，未获 Apple 授权、赞助或认可。Apple 和 AudioToolbox 是 Apple Inc. 的商标；文中提及 APAC 等名称仅用于说明兼容对象。
- 仓库不包含苹果二进制、反编译代码、SDK 文件或源媒体。`data/` 中观测或转录所得的格式常量在各自文件内注明来源，第三方数据详见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- 解码器是实验性实现，按自身数值模型输出，设计上不与苹果参考逐位一致，也未经生产环境验证。软件按“原样”提供，不附带任何明示或默示担保（见 [LICENSE](LICENSE)）。
- APAC 及相关音频技术可能受第三方专利保护。本项目的许可证不授予任何专利许可；在产品中使用或分发之前，请自行评估所在法域的专利与法律要求。
- 使用本项目处理音频时，请遵守相应内容的版权与许可条款。
