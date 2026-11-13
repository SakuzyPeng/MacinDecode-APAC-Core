# MacinDecode APAC Core

独立的 Apple Positional Audio Codec（APAC）解析与解码实现，包含 `no_std` + `alloc` 核心、CAF／MP4 容器读取和 `mapac` 命令行。

本仓库从经过来源核验的源码快照建立独立历史。原始研究记录、采集工具、旧提交和 LSF 原表保留在 Research 仓库，不随此仓库分发。

## 构建与解码

需要 Rust 1.98.0；macOS 的 AudioToolbox 参考命令还需要 Xcode Command Line Tools。

```sh
cargo +1.98.0 build --locked --release -p mapac
target/release/mapac --help
target/release/mapac decode-sq input.caf -o output.wav
```

Windows 程序名为 `mapac.exe`。输入必须是未加密的 APAC CAF 或受支持的 MP4／M4A；普通 AAC、MP3、FLAC 不属于此解码器的输入格式。HOA 系数输出保留原始含义，需要外部空间渲染；9.1.6、22.2 和 HOA 输出使用 CAF。工具不施加 DRC、响度或 EQ 音频处理。

默认启用 CAC，**BWE2 默认关闭**。语法解析保留，遇到活跃 BWE2 帧以 `bwe2-unavailable` 拒绝；完整解码与快速扫描一致，失败不提交状态。LSF 原表不在本仓库中。`bwe2` 仅保留为需要显式外部输入的可选接口，详见 [构建配置](guide/development.md)。

目前支持受限 SQ、CAC、TNS、零至十阶 HOA 恢复及源声道还原。LRVQ、外层 ASP 重配置及未确认配置明确拒绝。完整边界见 [支持说明](guide/support.md)。

## Rust API

```rust
use apac_core::{Config, Decoder};

let config = Config::parse(&cookie)?;
let mut decoder = Decoder::new(&config)?;
let pcm = decoder.decode_vec(&packet)?;
```

`apac-core` 的 `cac` feature 为显式可选依赖；`apac-container` 提供文件读取、播放缓冲所需的 `Media`／`Playback` 和定位接口。报告与状态的序列化使用 `serde` feature。

## 文档和验证

- [命令参考](guide/commands.md)、[解码与库接口](guide/decoding.md)、[码流参考](guide/bitstream.md)
- [开发与 feature](guide/development.md)、[验证范围](guide/validation.md)
- [数值来源](guide/source-provenance.md)、[第三方许可](THIRD_PARTY.md)

```sh
python3 -B scripts/check_sources.py
cargo +1.98.0 test --locked -p apac-core -p apac-container -p apac-research -p apac-cac -p apac-no-std-example -p apac-bwe2
cargo +1.98.0 test --locked -p mapac --test frame_parser --test config_parser
```

使用 MIT 许可；AAC 公共来源表及其他许可条目按 [THIRD_PARTY.md](THIRD_PARTY.md) 保留各自条款。本仓库尚未发布新的二进制 Release。

[English](README.en.md)
