# 开发与可选功能

使用 Rust 1.98.0。workspace 默认成员是 `mapac`；本地复用一个 `target/`，不要建立独立缓存。

| crate | 职责 |
| --- | --- |
| `apac-core` | `no_std` + `alloc` 解码、语法、状态扫描；普通依赖为 libm 和 sha2 |
| `apac-cac` | 可选 CAC 逆混合和公式生成的旋转系数 |
| `apac-bwe2` | 可选的 BWE2 数值恢复；原始 LSF 数据不分发 |
| `apac-container` | CAF／MP4 读取，WAV／RF64／CAF 输出和定位 |
| `apac-research` | 可移植报告、包目录和 CLI 驱动；不包含原始研究证据 |
| `apac-native` | macOS 公开 AudioToolbox 参考工具 |
| `mapac` | 命令行程序 |
| `apac-no-std-example` | 无 std API 示例 |

## 默认构建

```sh
cargo +1.98.0 build --locked --release -p mapac
```

默认仅开启 `cac`，不包含 BWE2 数值恢复。省略 CAC 可使用 `--no-default-features`；非零 CAC 增益会返回 `cac-unavailable`。

BWE2 的控制位、索引、增益与复用关系保留在 core。完整读取并校验语法后，不含 BWE2 的构建在首个活跃声道的控制位返回 `bwe2-unavailable`。全零输入、增益索引 0 和活跃的零频带 SCE 不绕过检查；CPE 零 `max_sfb` 仍按原规则门控。完整解码、快速扫描、HOA transport 和内嵌 preroll 使用同一检查。

`apac_core::CAC_ENABLED` 和 `BWE2_ENABLED` 表示 Cargo 合并后的实际能力。测试依赖不隐式开启 BWE2；按包运行可验证禁用路径：

```sh
cargo +1.98.0 test --locked -p mapac --test frame_parser
cargo +1.98.0 test --locked -p mapac --no-default-features --test frame_parser
```

## 显式 BWE2 外部输入

原始 LSF 表不随仓库提供，也没有自动下载或提取。仅在已自行准备外部输入时显式启用：

```sh
APAC_BWE2_TABLE=/path/outside/checkout/bwe2-format-v1.json \
  cargo +1.98.0 build --locked -p mapac --features bwe2
```

`apac-core/bwe2` 启用 `apac-bwe2/external-lsf`；缺少环境变量、文件位于检出内、维度或冻结摘要不一致时拒绝构建。普通 workspace 构建中的 `apac-bwe2` 不启用 `external-lsf`，不会加载数值表。启用该功能不属于默认分发或本仓库的无 BWE2 验收范围。

## 无 std

```sh
cargo +1.98.0 check --locked -p apac-no-std-example --target thumbv7em-none-eabihf
cargo +1.98.0 check --locked -p apac-core --target wasm32v1-none --features serde,cac
```

除 AudioToolbox FFI crate 外，workspace 禁止 unsafe。保持固定数学配置和逐次浮点舍入；运行时行为变化须同步相应验证。测试及来源规则见 [验证](validation.md) 与 [数值来源](source-provenance.md)。
