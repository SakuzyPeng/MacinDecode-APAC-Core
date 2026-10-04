# 验收与回归

单元测试、配置与回放验收、数学常量、重构回归和苹果参考诊断。各解析深度和 HOA 功能的专项验收入口在 [bitstream.md](bitstream.md) 对应小节。

返回 [README](../README.md)。

## 基本检查与配置验收

```sh
cargo test --offline --workspace
cargo clippy --offline --workspace --all-targets -- -D warnings
python3 -B -m unittest discover -s scripts -p 'test_*.py'
```

Python 回归测试需要 macOS，使用临时生成的音频验证短样本和正常长度样本的切片，并与 `afconvert` 顺序解码结果比较；完成后自动清理。

配置覆盖和单变量编码对照可单独复核，输出路径必须未存在：

```sh
python3 scripts/validate_configs.py \
  --collection artifacts/demo/configs/index.json \
  --output reports/config-validation-new.json

# 第三阶段的严格验收：基线为先前的 12 complete / 52 partial 报告
python3 scripts/validate_configs.py \
  --collection artifacts/demo/configs/index.json \
  --drc-baseline reports/config-validation-phase2.json \
  --output reports/drc-validation-new.json

# 第四阶段：使用先前的 62 complete / 2 HOA partial 报告
python3 scripts/validate_configs.py \
  --collection artifacts/demo/configs/index.json \
  --hoa-baseline reports/drc-validation-phase3.json \
  --output reports/hoa-validation-new.json
cargo check --offline --workspace --target x86_64-unknown-linux-gnu
cargo check --offline --workspace --target x86_64-pc-windows-msvc
```

配置验收默认预期 64 份配置、3 份首批目标，可通过 `--expected-configs` / `--expected-targets` 调整。指定 `--hoa-baseline` 时，从第三阶段报告的 ASC 类型 2 停止点固定两份目标，要求当前集合与基线哈希一致，并强制全部 64 份 complete。`--drc-baseline` 继续要求原有 50 份 DRC 目标及 12 份完整配置通过，同时允许其余两份 HOA 从原停止点升级为 complete。两个基线参数互斥。

严格验收要求已有字段的名称、偏移、JSON 类型、数值及派生值保持一致；每个文件都保留成功或失败结果及其解析报告，未达标返回非零退出码。

`--skip-fixtures` 只复核已有 cookie，可用于没有 AudioToolbox 的环境；默认另生成 23 组短小控制样本（原有 16 组加 7 组 HOA 对照），全部要求完整解析。新增 HOA 对照以 HOA3 为基线，分别改变阶数、DRC 设置、采样率、质量或信号，检查解析值、系统元数据和参数回读，结束后清理。跨目标 `cargo check` 不是对应操作系统的原生运行测试。

逐包回放验收使用第一阶段报告中记录的五类代表来源，只解码短窗口：

```sh
python3 -B scripts/validate_replay.py \
  --representatives reports/validation.json \
  --output reports/replay-validation-new.json
```

该脚本验证 15 个代表范围和 42 个短向量，均使用 1/7/64 包输入批次；短向量在删除临时原始音频后回放，并与 `afconvert` 交叉核对。保持比较器默认容差，报告逐位一致性及每项失败原因。整个流程不生成完整歌曲的参考 PCM。

第六阶段的 SQ／ASP 前缀验收复用一份成功的第五阶段报告定位相同窗口，另生成 15 组短控制样本：

```sh
python3 -B scripts/validate_frames.py \
  --replay-baseline reports/replay-validation-a76d2f4.json \
  --output reports/frame-validation-new.json
```

脚本分别统计前缀目标和整包状态，并使用独立序列化规则与苹果解码器入口的实际读位位置核对结果。原生入口检查通过 Xcode 的 LLDB 启动本工具自己的回放进程，只读取状态；需要本机调试权限，当前固定验证 Apple Silicon 和指定组件哈希。组件改变或调试不可用会使验收明确失败，不影响 `parse-packets` 的跨平台实现。该原生跟踪不调用私有函数、不修改编码器或解码器状态。临时音频及跟踪产物按组清理。

第七阶段的频谱验收可以在已构建工具的任何受支持平台运行；`--native` 额外启用当前 macOS 组件的只读频谱快照和真实／控制窗口验收：

```sh
python3 -B scripts/validate_spectra.py --output reports/spectrum-portable-new.json
python3 -B scripts/validate_spectra.py --native --output reports/spectrum-native-new.json
```

便携矩阵在两种采样率下分别验证全部 4730 组码字／增益／逃逸条件，以及全部长短窗频带、左右位置、多 section、跨组缩放因子和负缩放因子。原生快照另选取全部增益／逃逸边界及代表码本／频带位置，读取声道流返回点的原始频谱；报告披露实际数量。真实样本允许共享头／CAC 明确停止，并分别统计左右声道覆盖率。验证脚本只使用标准库，人工生成器和正式测试不依赖 `docs/local/`；原生模式还需要已有的第五阶段成功报告定位代表源文件。大型矩阵只保留计数、误差、哈希及失败原因，临时频谱和参考 PCM 逐批清理。

本机已保存验收报告、机器验证结果、样本清单和索引汇总，位于 `reports/`；这些输出包含本地来源信息，不纳入代码版本控制。可重新生成的双声道测试向量位于 `artifacts/fixtures/stereo/`，同样只在本地保留。研究文档仓库的 `docs/validation.md` 单独记录阶段总结。

完整验收脚本仅使用 Python 标准库。再次运行需使用新的报告目录和测试向量目录：

```sh
python3 scripts/validate.py \
  --corpus '/path/to/APAC' \
  --report reports/rerun/validation.json \
  --fixtures artifacts/rerun/stereo
```

因为 `afconvert` 没有结束帧参数，验收脚本一次临时解码一个最小代表文件，再从完整顺序解码结果中抽取开头、中部和末尾，验证随机定位。外部参考文件单独限制为 512 MiB，运行前检查可用空间，每组完成后自动清理；工具自身仍采用 128 MiB 默认导出上限。本次最大的临时参考文件约 451 MiB。

## SQ 数学常量

`data/sq-math-v1.json` 保存公式生成的精确 Float32／Float64 位模式，覆盖反量化、缩放、窗、调制和 FFT 常量。生成器只使用 Python 标准库 Decimal，在 100 位和 200 位精度下分别计算并核对舍入结果；正式 Rust 构建直接包含该数据，无需 Python、苹果文件、网络或系统超越函数。未来修改数值规则须升级配置版本，不随苹果实现版本自动变化。

```sh
# 检查已提交的常量；不覆盖文件
python3 -B scripts/generate_sq_math.py --check
# 从头生成到一个新路径以供审查
python3 -B scripts/generate_sq_math.py --output artifacts/sq-math-regenerated.json

# 第一关：17,800 个频谱用例及 9,948 个 PCM 序列的独立数学验收
python3 -B scripts/validate_portable.py --binary target/debug/apac-tool \
  --output reports/sq-math-baseline.json
# 第二关：用另一个构建复现相同输入、整数、频谱与 PCM 的字节摘要
python3 -B scripts/validate_portable.py --binary target/release/apac-tool \
  --reference-report reports/sq-math-baseline.json --output reports/sq-math-release.json
```

数学参考使用 Decimal 直接 IMDCT 求和，不读取生产数值表、不调用生产 FFT，也不把候选输出当作真值。PCM 仍按 `atol=1e-6, rtol=1e-5` 验收，另记录 ULP；频谱还要求符合分别舍入的精确结果。第二关必须使用同一提交、源码与常量指纹下成功的完整数学报告，逐位比较所有阶段；不以容差代替摘要一致。必需用例缺失、非有限数值、执行中二进制或源码变化均失败。

## CAC 矩阵

新增 CAC 矩阵包含两采样率下共 2,912 个频谱用例、2,984 个 PCM 序列；它分别核对原始编码流、CAC 参数和边界、恢复后频谱以及 PCM。新旧矩阵独立运行，原有 17,800／9,948 用例及输出摘要保留。

验证旧 SQ 输出不变时，可对 `validate_portable.py` 显式使用 `--regression-report reports/previous-sq-math.json`：允许参考来自旧提交，但要求相同数值配置、常量和完整用例身份，并重新运行所有输入逐项比较摘要。它与要求同一提交／源码的 `--reference-report` 互斥，报告会记录旧参考提交；不会默许改变输入、常量或期望结果。

```sh
python3 -B scripts/generate_cac_math.py --check
python3 -B scripts/validate_cac.py --binary target/debug/apac-tool --output reports/cac-math-new.json
python3 -B scripts/validate_cac.py --binary target/release/apac-tool \
  --reference-report reports/cac-math-new.json --output reports/cac-release-new.json
# 可选：受组件哈希约束的原生参数／边界／频谱对照及真实控制样本
python3 -B scripts/verify_cac_codebooks.py
python3 -B scripts/validate_cac.py --binary target/debug/apac-tool --native --output reports/cac-native-new.json
```

原生核对要求参数、位边界和全部单位幅度基向量通过。其他原生浮点差异记录在 `native_artificial[].numeric_passed`、误差指标及 `native_float_comparison_passed` 中，不删除压力案例、不放宽容差；独立数学与六构建逐位检查仍是硬性验收。真实样本单列 CAC 完成率和 PCM 的明确停止原因，后续工具关闭之前不能将 CAC 完成理解为整包可播放。

Windows 使用对应的 `.exe` 路径。Python CLI 单元测试通过 `APAC_TOOL_BINARY` 指定构建，默认在 `target/debug` 查找本机二进制；缺失时直接失败。报告路径必须不存在；验收分批清理临时音频，单份报告与导出沿用 128 MiB 限额。

## 重构回归

重构回归可运行 `golden_decode`，比较冻结 fixture 的解析报告、错误和 PCM 摘要；快照记录当前实现行为，不替代独立数学验收。`run_portable_suite.py` 汇总可移植 CLI 验证，输出目录必须不存在；`--jobs` 控制并发，`--only` 选择验证器，`--fast` 选择较快子集，`--skip` 显式排除验证器。只要选中了 `validate_layouts`，就必须提供存在的 `--presence-binary`，否则在启动验证前退出 2；已执行的任一验证器失败时套件退出 1。

```sh
cargo +1.98.0 test --offline -p apac-research --test golden_decode
cargo +1.98.0 build --offline --workspace --bins --examples
python3 -B scripts/run_portable_suite.py --binary target/debug/apac-tool \
  --presence-binary target/debug/examples/layout_presence --jobs 2 --out reports/portable-suite-new
python3 -B scripts/compare_reports.py reports/portable-suite-before reports/portable-suite-new --limit 50
```

`compare_reports.py` 支持 JSON、JSONL 或报告目录，忽略时间、源码／二进制构建指纹、编译器及运行环境字段，保留 PCM、常量和向量摘要等结果差异。比较相同退出 0，有差异退出 1；`--limit` 必须为正整数，只限制每个文件打印的差异数，非正值退出 2。可用 `--ignore` 显式追加忽略的字段名。

## 历史正弦窗与苹果合成参考

旧 `data/sq-sine-windows.json` 是 `26A428` 的 Float32 窗值观测，仅保留为历史诊断资料，不进入默认合成路径。下面的苹果核对与混合参考属于独立诊断，失败仍返回非零退出码，不用来改写数学模型的通过结果。

系数的逐位核对可重新运行：

```sh
python3 -B scripts/verify_sine_windows.py --output reports/sine-windows-new.json
```

该核对仅在匹配组件和 Apple Silicon 上通过 LLDB 读取自有回放进程中的窗值，不改解码状态；每种采样率核对完整的 1152 个系数。原始探针记录留在本地，代码中的数值配置单独版本管理。

可复现默认苹果参考对照：

```sh
python3 -B scripts/validate_synthesis.py --output reports/synthesis-default-new.json
```

脚本始终使用 `atol=1e-6, rtol=1e-5`，保留全部压力失败，存在失败时退出 1。另有**诊断性混合参考**：只在参考回放进程中，将公开 vDSP DFT 替换为独立的 Float64 DIF FFT，其他 codec 运算保持原生；这用于隔离数值差异，不能作为“未修改苹果解码器”的验收结果：

```sh
mkdir -p target/research
xcrun clang -dynamiclib -O2 -g0 -ffp-contract=off scripts/reference_fft.c \
  -framework Accelerate -o target/research/sq-reference-fft.dylib
python3 -B scripts/validate_synthesis.py \
  --reference-fft target/research/sq-reference-fft.dylib \
  --output reports/synthesis-controlled-new.json
```

该库不参与正式构建，也不会由 `decode-sq` 自动加载。报告分别记录参考条件、组件／工具／诊断库哈希与实际 DFT 调用数；矩阵包含全部 16 种高增益四系数符号组合的相邻帧测试，两种采样率共 9948 个序列。旧版本的受控参考通过结果不能直接沿用为新公式基线的苹果兼容性结论。

同一个诊断库还支持 `--reference-mode aligned64` 或 `offset16`；这两个模式由原始 vDSP 完成运算，只控制其输出地址对齐。以下命令比较两条原生路径的容差区间：

```sh
python3 -B scripts/check_synthesis_reference.py \
  --reference-fft target/research/sq-reference-fft.dylib \
  --output reports/synthesis-reference-consistency-new.json
```

出现互不相交的区间时报告 `reference_consistent=false` 并退出 1：任何固定候选值都不可能同时满足两条原生路径。这是参考条件不唯一的证据，不会被合并成 Rust 的成功数或用于放宽比较容差。
