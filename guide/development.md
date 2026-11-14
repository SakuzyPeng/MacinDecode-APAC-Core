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

## CI 与构建产物

[Build 工作流](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml) 默认执行单个 Linux debug 开发检查。手动 `full_validation` 启动三平台 debug／release 构建、独立数学验收和六构建一致性检查。

完整运行的 **Artifacts** 提供 `mapac-TARGET`。其中包含 `mapac-TARGET-SHA12` 压缩包和相邻 `.sha256`，开发包保留三天。Linux 与 macOS 使用 `.tar.gz`，Windows 使用 `.zip`。

解压 Artifacts 下载文件后，在包含压缩包和校验文件的目录核验下载。

Linux

```sh
sha256sum --check mapac-TARGET-SHA12.tar.gz.sha256
```

macOS

```sh
shasum -a 256 -c mapac-TARGET-SHA12.tar.gz.sha256
```

Windows PowerShell

```powershell
Get-FileHash .\mapac-TARGET-SHA12.zip -Algorithm SHA256
Get-Content .\mapac-TARGET-SHA12.zip.sha256
```

版本包使用 `mapac-vVERSION-TARGET`，下载和上手说明随版本保存。[v0.2.0 发布说明](releases/v0.2.0.md)

### 准备 CLI Release

1. 更新根 `Cargo.toml` 的 workspace 版本及 `Cargo.lock`，重新生成依赖许可清单，并准备 `guide/releases/vVERSION.md`。v0.2.0 对应标签为 `v0.2.0`，程序版本为 `mapac 0.2.0`。
2. 提交并推送待发布代码。在 Build 的 **Run workflow** 中选择对应分支，填写 `release_tag=v0.2.0`。该输入会启动三平台完整验收，也可通过推送同名标签触发。[Build 工作流](https://github.com/SakuzyPeng/MacinDecode-APAC-Core/actions/workflows/build.yml)
3. 构建、完整数学与六构建一致性、来源及全历史审计通过后，工作流核对版本和远端标签指向，检查三个安装包的 SHA-256，并创建带六个附件的 Release 草稿。
4. 审阅中英文说明、三个安装包及三个校验附件后，在 GitHub 发布草稿。手动输入产生的草稿绑定工作流完整提交 SHA，发布时 GitHub 将尚不存在的标签创建到该提交。

同版本草稿更新串行执行。已有远端标签的最终提交必须等于构建提交。已发布版本的标签推送运行核对成功后跳过草稿操作，手动运行遇到同名正式版时失败。

本地打包也会校验版本标签格式、workspace、程序版本和说明文件。复用共享构建目录，并显式提供完整代码提交。

```sh
python3 -B scripts/package_release.py --target aarch64-apple-darwin \
  --binary /path/to/shared-target/release/mapac \
  --commit FULL_COMMIT_SHA --release-tag v0.2.0 --out artifacts/release
```

`LICENSES/Rust-dependencies.txt` 保留 `Cargo.lock` 中全部外部 Rust 依赖的许可及版权声明，随三平台开发包和版本包分发。更新锁定依赖后重新生成，CI 会核对清单和上游许可文件。

```sh
python3 -B scripts/generate_dependency_licenses.py
python3 -B scripts/generate_dependency_licenses.py --check
```

CI 复用依赖缓存；构建后的内部程序与验收记录保留一天，最终一致性汇总保留七天。完整覆盖与报告规则见 [验证范围](validation.md)。
