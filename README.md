# MacinDecode APAC Core

**简体中文** · [English](README.en.md)

[![Build](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml/badge.svg)](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml)

一个用来**解码 Apple Positional Audio Codec（APAC）空间音频、查看码流结构**的命令行工具和 Rust 库。
命令行程序叫 `mapac`，自带独立解码器，在 Windows、Linux 和 macOS 上把音频导出为
**WAV／RF64 或 CAF 文件**，音频均为 Float32 PCM，并在标准输出返回 JSON 解码报告。

支持含 APAC 音轨的 `.m4a`、`.mp4` 和 `.caf`，以及本工具导出的包目录。输出保留声道或 HOA 系数，
供后续分析、处理或接入播放器使用；CLI 本身不播放声音。

本文介绍下载、上手和构建。**全部命令和参数见[命令行参考](guide/commands.md)，
解码方式与 Rust 接口见[使用指南](guide/decoding.md)。**

## 目录

- [能用它做什么](#能用它做什么)
- [获取 CLI](#获取-cli)
- [第一次解码](#第一次解码)
- [查看和比较](#查看和比较)
- [平台支持](#平台支持)
- [使用前了解](#使用前了解)
- [从源码构建](#从源码构建)
- [作为 Rust 库使用](#作为-rust-库使用)
- [文档](#文档)
- [许可证](#许可证)

## 能用它做什么

- **把 APAC 文件解码成音频文件**：可选 WAV／RF64 或 CAF，支持单声道、立体声、5.1、7.1、7.1.4、9.1.6 和 22.2，保持输入采样率与声道布局。
- **导出 HOA 音频**：支持零至三阶高阶 Ambisonics，可输出 ACN/SN3D 系数，或还原码流声明的源声道。
- **只取需要的一段**：按音频帧指定起点和长度，也可选择快速范围访问，适合检查较长文件中的短片段。
- **看清码流里有什么**：解析独立配置和导出的音频包，输出字段、位位置、元数据及中间频谱。
- **比较两份解码结果**：检查 PCM 格式、布局和摘要，报告逐声道误差；不自动对齐或调整增益。
- **接入自己的程序**：核心库支持 `no_std` + `alloc`；文件读取接口提供顺序解码、按帧定位和播放所需的检查点。

## 获取 CLI

请到 [GitHub Releases](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/releases) 查看版本和发布说明。
在对应版本页面下方的 **Assets** 中，按电脑选择压缩包：

| 你的电脑 | 下载哪个文件 |
| --- | --- |
| Windows 10／11，Intel / AMD 64 位电脑 | 以 `x86_64-pc-windows-msvc.zip` 结尾的文件 |
| Linux，Intel / AMD 64 位电脑，glibc 2.35 或更新（如 Ubuntu 22.04） | 以 `x86_64-unknown-linux-gnu.tar.gz` 结尾的文件 |
| Apple 芯片 Mac（M1 及更新机型；当前在 macOS 26 验证） | 以 `aarch64-apple-darwin.tar.gz` 结尾的文件 |

解压后即可使用，无需安装 Rust 或其他解码器，也不需要管理员权限。Windows 程序为 `mapac.exe`，
Linux 和 macOS 程序为 `mapac`；请在终端中运行。

同名 `.sha256` 附件用于核对下载是否完整，无需安装。包内已附使用文档、许可证和构建信息。
校验方法见[下载校验](guide/development.md#ci-与构建产物)，首版内容见 [v0.1.0 发布说明](guide/releases/v0.1.0.md)。
开发构建仍可从 [Build 工作流](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml) 的 **Artifacts** 下载。

## 第一次解码

1. 解压下载的压缩包，在终端中进入包含 `mapac` 的目录。
2. 运行 `--version` 或 `--help`，确认程序可用。
3. 把下面的 `input.m4a` 换成你的 APAC 文件路径，导出 WAV。

**Windows PowerShell：**

```powershell
.\mapac.exe --version
.\mapac.exe decode-sq "input.m4a" -o output.wav
```

**macOS / Linux：**

```sh
./mapac --version
./mapac decode-sq "input.m4a" -o output.wav
```

成功后得到 `output.wav`，可交给支持浮点 WAV 的音频工具使用。目标文件必须尚不存在；
解码报告写到标准输出，不会额外生成 JSON 文件。容器选择如下：

| 输出文件 | 格式与适用范围 |
| --- | --- |
| `.wav`／`.wave` | Float32 WAV；Mono、Stereo、5.1、7.1、7.1.4；超过 RIFF 大小限制时自动使用 RF64 |
| `.rf64` | 强制使用 RF64，可保存超过 4 GiB 的音频；布局范围与 WAV 相同 |
| `.caf` | Float32 LPCM CAF；保存全部已支持的解码输出布局，包括 9.1.6、22.2、HOA 和组合流 |

**9.1.6、22.2 或 HOA 音频使用 CAF：**

```sh
./mapac decode-sq "spatial.m4a" -o output.caf
```

输出 CAF 内是 PCM 音频。WAV 无法准确表达的布局会明确提示改用 CAF，不会静默改成匿名通道。
WAV 按标准声道顺序写入，7.1／7.1.4 的后环绕排在侧环绕之前；CAF 保留原始通道顺序。
两者都不重采样、不下混、不改变样本精度。用 `--format wav|rf64|caf` 可显式选择格式。

CAF 的布局标签有误时，可以显式指定应采用的布局：

```sh
./mapac decode-sq "input.caf" -o output.caf --input-layout 9.1.6
```

指定值必须与 APAC 配置一致，只纠正外部容器标签，并在报告中记录原始声明。CAF 缺少布局块时本来就会采用 APAC 配置；该参数不能重标输出、修改码流或用于 MP4／M4A。

文件输出**默认不限制总大小**，按块写入，内存不随时长增长。需要限额时，可添加 `--max-output-mib 1024`。
只取一段时，`--start-frame` 和 `--frames` 使用音频帧数；以下在 48 kHz 音频中从第 10 秒起取 1 秒：

```sh
./mapac decode-sq "input.caf" -o window.wav \
  --start-frame 480000 --frames 48000 --access fast
```

研究或 PCM 对比仍可使用 `--out decoded`：生成 `pcm.f32le` 裸 PCM、`pcm.json` 和 `decode-sq.json`，
默认限额保持 **128 MiB**。`--out` 与 `-o` 互斥；只有裸 PCM 需要按 JSON 手动填写采样率和声道数。

后续示例使用 macOS / Linux 写法；Windows PowerShell 将 `./mapac` 换为 `.\mapac.exe`，并将多行命令写成一行。

## 查看和比较

已经有独立配置文件或导出的包目录时，可以进一步查看语法；`compare` 比较的是 `--out` 产生的裸 PCM 包：

```sh
./mapac parse-cookie cookie.bin
./mapac parse-packets packets/ --depth cac --output cac.jsonl
./mapac compare reference/pcm.json decoded/pcm.json
```

`parse-cookie` 接受独立的 magic cookie，`parse-packets` 接受包目录；直接解码 `.m4a`／`.mp4`／`.caf` 使用 `decode-sq`。
macOS 另有 `inspect`、`dump` 等参考工具，详细用法见[命令行参考](guide/commands.md)。

命令结果写到标准输出，进度和错误写到标准错误。退出码 `0` 表示成功，`1` 表示运行、输入或完整性错误，
`2` 表示比较超出容差、解析未完成或命令行参数有误；完整约定见[命令行参考](guide/commands.md#概览)。

## 平台支持

| 功能 | Windows x64 | Linux x64 | macOS arm64 |
| --- | --- | --- | --- |
| 独立解码、配置解析、逐包解析、PCM 比较 | ✅ | ✅ | ✅ |
| AudioToolbox 参考采集、编解码和回放 | — | — | ✅ |
| 预构建 CLI | `.zip` | `.tar.gz` | `.tar.gz` |

CI 在 Windows Server 2022、Ubuntu 22.04 和 macOS 26 上构建并运行测试。macOS 首版只提供 Apple Silicon 产物。
独立解码使用 `decode-sq`；`decode` 是 macOS 的 AudioToolbox 参考命令。

## 使用前了解

- **输入必须是 APAC 音频**：普通 AAC `.m4a`、MP3、FLAC，以及加密／DRM 文件不受支持。MP4／M4A 当前限非分片、单音轨文件。
- **输出保留原始声道含义**：HOA 系数仍需空间渲染，不能直接当作普通扬声器声道播放。HOA、9.1.6、22.2 等布局请输出 CAF；CLI 不提供空间渲染、播放或 AAC／FLAC 等压缩编码。
- **不施加 DRC、响度或 EQ 处理**：相关元数据会被解析，但不会改变输出音频。
- **部分 APAC 路径尚未实现**：四阶及以上 HOA、LRVQ、外层 ASP 重配置和非零帧长索引会明确报错。各布局和采样率的完整条件见[支持边界](guide/support.md)。
- **数值结果按本项目模型定义**：与苹果 AudioToolbox 参考按容差比较，设计上不逐位一致，原因见[数值关系](guide/support.md#与苹果参考的数值关系)。
- **输出不会覆盖现有文件**：文件导出成功前只写同目录临时文件，失败时清理。研究目录中带 `.incomplete.json` 或 `.incomplete` 的输出尚未完成，不能当作有效结果。

遇到问题时，欢迎在 [Issues](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/issues) 中提供程序版本、
操作系统、使用的命令和错误提示。提交前请去掉命令里的个人路径；分享音频时请确认拥有相应权限。

## 从源码构建

需要 **Rust 1.98.0**。macOS 构建额外需要 Xcode Command Line Tools；Windows 和 Linux 的独立解码无需苹果 SDK。

```sh
cargo +1.98.0 build --locked --release
cargo +1.98.0 test --locked --workspace
```

程序位于 `target/release/mapac`，Windows 为 `target/release/mapac.exe`。默认开启 CAC 逆混合，
`--no-default-features` 构建会拒绝使用非零 CAC 增益的帧。
workspace 结构、无 std 构建、发布流程和回归检查见[开发说明](guide/development.md)及[验收与回归](guide/validation.md)。

## 作为 Rust 库使用

`apac-core` 是 `no_std` + `alloc` 的解码核心；`apac-container` 提供 CAF／MP4 读取和定位。
可移植 crate 通过 workspace lint 禁用 `unsafe`；macOS 的 AudioToolbox 参考代码单独放在 `apac-native`。

```rust
use apac_core::{Config, Decoder};

let config = Config::parse(&cookie)?;
let mut decoder = Decoder::new(&config)?;
for packet in packets {
    let pcm: Vec<f32> = decoder.decode_vec(packet)?;
    // 使用交错 Float32 PCM；采样率和声道数可从 decoder.info() 读取。
}
```

使用 CAC 的流需要开启 `apac-core` 的 `cac` feature。文件级解码与双向 seek 使用 `Reader`，
面向播放器的快速打开、按帧 seek 和后台索引使用 `Media`／`Playback`／`Indexer`。
完整接口、代码示例和限制见[解码与库接口](guide/decoding.md)。生成 API 文档：

```sh
cargo +1.98.0 doc --no-deps -p apac-core -p apac-container --open
```

## 文档

| 文档 | 说明 |
| --- | --- |
| [v0.1.0 发布说明](guide/releases/v0.1.0.md) | 下载、首版功能和使用前须知（中英文） |
| [命令行参考](guide/commands.md) | 全部子命令、参数、输出和退出码 |
| [解码与库接口](guide/decoding.md) | PCM 输出、范围解码、`Decoder`、`Reader` 与 `Playback` |
| [支持边界](guide/support.md) | 已实现路径、拒绝条件及与苹果参考的数值关系 |
| [码流解析](guide/bitstream.md) | 声道与 HOA 语法、解析深度和数值标识 |
| [开发说明](guide/development.md) | workspace、`no_std`、CAC feature 和 CLI 发布流程 |
| [验收与回归](guide/validation.md) | 独立数学验收、跨构建检查及参考诊断 |
| [HOA 黑盒批处理](guide/hoa-blackbox.md) | 三阶生产边界、断点续跑和历史测量证据 |
| [HOA 空间控制均值](guide/hoa-mean-blackbox.md) | 精确抵消测量与独立验证 |
| [BWE2 黑盒重建](guide/bwe2-blackbox.md) | 增益重建和 LSF 可识别性实验 |
| [第三方数据](THIRD_PARTY.md) | 格式常量的来源、许可与独立推导 |

## 许可证

项目代码以 [MIT](LICENSE) 发布。`data/sq-codebooks.json` 中的 AAC Huffman 码表和频带边界取自 vo-aacenc，适用 Apache-2.0（见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)）；它们在构建时编入 `apac-core`，因此该 crate 声明 `MIT AND Apache-2.0`，其他 crate 为 `MIT`。

本项目为互操作和研究目的独立编写，与 Apple Inc. 不存在隶属、赞助或认可关系。Apple 和 AudioToolbox 是 Apple Inc. 的商标；提及 APAC 等名称仅用于说明兼容对象。

### 免责声明

- 仓库不包含苹果二进制、反编译代码、SDK 文件或源媒体。`data/` 中观测或转录所得的格式常量在各自文件内注明来源，第三方数据详见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- 解码器按自身数值模型输出，设计上不与苹果参考逐位一致。软件按“原样”提供，不附带任何明示或默示担保（见 [LICENSE](LICENSE)）。
- APAC 及相关音频技术可能受第三方专利保护。本项目的许可证不授予任何专利许可；在产品中使用或分发之前，请自行评估所在法域的专利与法律要求。
- 使用本项目处理音频时，请遵守相应内容的版权与许可条款。
