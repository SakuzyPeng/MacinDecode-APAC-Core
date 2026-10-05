# 码流解析深度与 HOA 解码

`parse-packets --depth` 各阶段（SQ 频谱、CAC、TNS、BWE2、DRC、完整包、离散声道）及 HOA 系数解码的语法、报告字段、数值标识和专项验收入口。

返回 [README](../README.md)。

## SQ 与离散声道

### SQ 频谱深度

`parse-packets --depth spectrum` 沿相同配置范围继续解析 section、缩放因子、codebook 0–11、符号和逃逸。先完成左声道；独立右声道头分支完成右声道；共享头分支在标志之后以 `shared_ics_cac_deferred` 停止，保留左声道结果。两路完成后以 `sq_spectra_before_tools` 停止，尚未读取 TNS、ancillary 或组件尾部。ASP 内嵌 preroll 仍只按长度跳过，不输出其频谱。

`report` 保留原有字段并增加 `spectrum_complete`、`spectral_stage=scaled_before_cac_tns` 和 `channels`。每声道记录 ICS、global gain、section、按组／频带排列的 `scale_factors`（零码本为 null）、1024 个 `quantized` 整数和 1024 个 `scaled` Float32 值，以及声道流起点、频谱码字起点和终点。短窗数组依次为八个 128 点窗口；长窗为一个 1024 点窗口。频带外的零值由语法确定，截断输入不会补零。

`spectrum_complete=true` 只表示两路 SQ 流完成。CPE 缺席时 `channels=[]`、`spectrum_complete=false`，汇总单列 `cpe_absent_packets`；LRVQ 和范围外配置同样不伪造频谱。整包状态仍为 partial，通常退出 `2`。`prefix_complete` 保留原目标含义，`payload_bit_offset` 仍指左声道流起点，`component_end_bit_offset` 保持 null。汇总另列 `left_spectrum_packets`、`right_spectrum_packets` 和 `spectrum_complete_packets`。

库入口 `inspect::parse_spectrum(&FrameContext, &[u8]) -> Result<SpectrumReport, config::ParseError>` 提供类型化结果；`SpectrumReport.frame` 是原 `FrameReport`，JSON 序列化时平铺它。`parse_packets_with_depth(..., ParseDepth)` 提供包目录接口，原 `parse_frame`、`parse_packets` 及 CLI 默认 `--depth prefix` 保持原有行为。

缩放因子差分在所有组间连续累加，支持 `-256..255`；超界明确报错，不复现苹果的饱和恢复。逃逸幅度限制为已验证的 `16..8191`。反量化 `|q|^(4/3)` 和缩放 `2^((sf-100)/4)` 分别按最近值、平局取偶舍入为 Float32，再作 Float32 乘法。全部幅度与缩放因子的 IEEE 位模式由高精度公式离线生成，运行时不使用系统 `powf`。频谱报告新增可选 `numeric_profile`，新输出为 `apac-sq-math-v1`；旧报告缺失该字段时仍可读取。频谱尚未施加 CAC、TNS、DRC 或合成变换，不能直接解释为可播放 PCM。

码字、码长及频带常量的来源和许可见 [THIRD_PARTY.md](../THIRD_PARTY.md)；Rust 的解码表结构与 APAC 读取器为独立实现，运行和构建无需系统二进制或本地研究目录。

### CAC 深度

`parse-packets --depth cac` 及 `inspect::parse_cac(&FrameContext, &[u8]) -> Result<CacReport, config::ParseError>` 完成共享 ICS 下的右声道流与 CAC。原 `prefix`／`spectrum` 行为保持不变；`spectrum` 仍在共享头标志之后保留左流并停止。

`CacReport.spectrum` 保存原始频谱报告，JSON 平铺其字段。`channels` 中的量化整数和 `scaled` 始终表示 CAC 前的编码流；`channels_after_cac` 才是左右声道的恢复频谱，标记为 `output_stage=scaled_after_cac_before_tns`。报告另含 `shared_ics`、`cac_complete`、`cac_numeric_profile`，以及 `cac` 中的游程、按组／频带展开的增益索引、CAC 起止位。逐声道 `end_bit_offset` 仍是原始流终点，外层 `stop_bit_offset` 为本深度的停止位置；整包状态仍为 partial，组件终点仍未知。

独立声道头没有 CAC 载荷，`cac=null`，两路完整时按恒等处理完成该阶段；共享头的 `max_sfb=0` 不读取 CAC 码字，`cac.runs=[]`。CPE 缺席／LRVQ 等不产生虚构声道，`cac_complete=false`。汇总新增 `cac_complete_packets` 与 `shared_ics_packets`。

CAC 共有 35 个增益索引及 44 个重复码；普通重复码 0..42 表示 1..43 槽，游程跨组连续，终止码 43（`0000`）最多覆盖剩余 44 槽。覆盖不足、普通游程耗尽／超过范围、缺少终止码、截断或超过 120 条记录均报错。增益索引 0 不变换；其余索引按 −12..+12 dB、1.5 dB 步长及正／负相关分支定义恢复矩阵。`apac-cac-math-v1` 使用 100／200 位 Decimal 公式生成的 Float64 系数，乘加分别舍入，输出一次转换为 Float32，再交给既有合成器。

共享头人工包可独立生成：

```sh
python3 -B - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from cac_vectors import frame
from spectrum_vectors import bundle
path = Path("artifacts/demo/shared-sq-packets")
path.parent.mkdir(parents=True, exist_ok=True)
cases = [{}, {"gain": 200, "cac_gain": 9, "left": {0: (1, [1, 0, 0, 0], 200)}}, {}, {}]
bundle(path, [frame(case)[0] for case in cases])
PY
```

### TNS 深度

`parse-packets --depth tns` 及 `inspect::parse_tns(&FrameContext, &[u8]) -> Result<TnsReport, config::ParseError>` 依次读取完整左 TNS、完整右 TNS，停在 BWE2 入口。原 `prefix`／`spectrum`／`cac` 深度的默认值、结果与停止位置保持不变。

`TnsReport.cac` 保留前阶段报告，JSON 继续平铺。`channels` 是原始整数及 CAC 前频谱，`channels_after_cac` 保留 TNS 输入，`channels_after_tns` 为滤波后输出，`tns_stage=scaled_after_tns_before_bwe2`。`tns` 逐声道、窗口、滤波器记录存在位、分辨率、长度／阶数、方向／压缩、补码整数、Float64 反射系数、有效谱线区间和起止位。`tns_complete` 及汇总 `tns_complete_packets` 只表示此阶段完成；整包仍是 partial，组件终点仍未知。CPE 缺席不产生虚构频谱。

长窗支持 0..3 个滤波器、0..12 阶；短窗逐一处理八个窗口，每窗 0..1 个滤波器、0..7 阶，不依赖分组。长度必须非零，零阶不读取方向和系数但仍推进频带游标。长／短游标从完整 49／14 带开始，实际范围再裁至 `max_sfb` 和 TNS 上限（48 kHz 长窗 40、44.1 kHz 长窗 42、短窗 14）；空作用范围仍完整读完参数。截断、超阶及非有限结果明确报错，不补零或截断阶数。

`apac-tns-math-v1` 以正弦公式定义反射系数，Decimal 100／200 位计算结果须舍入到同一 Float64 位模式。运行时采用固定顺序 Float64 格型滤波，每个滤波器重置状态，各谱线最后一次转换为 Float32；无 TNS 时原有 SQ／CAC 输出不变。配置拒绝消息保持原操作名称及退出码，并列出拒绝字段、实际值和 cookie 位位置；配置范围没有扩大。

```sh
python3 -B scripts/generate_tns_math.py --check
# 4,326 个频谱用例、4,330 个 PCM 序列；独立 LPC 递推及 Decimal 直接 IMDCT
python3 -B scripts/validate_tns.py --binary target/debug/apac-tool --output reports/tns-math.json
# 同提交、同源码、同常量的逐位验收
python3 -B scripts/validate_tns.py --binary target/release/apac-tool \
  --reference-report reports/tns-math.json --output reports/tns-release.json
# 可选原生只读诊断，仍单独保留高阶密集压力差异
python3 -B scripts/validate_tns.py --binary target/debug/apac-tool --native-only --output reports/tns-native.json
```

人工包使用 `scripts/tns_vectors.py` 的 `packet(case, rate)` 与原 `bundle` 写入器生成；`left_tns`／`right_tns` 为按窗口索引的参数字典，缺失表示关闭，空字典表示存在但所有窗口零滤波器。例如 `left_tns={0: {"resolution": 4, "filters": [{"length": 49, "q": [1, -1], "direction": False, "compression": False}]}}`。同一窗口共用分辨率；压缩只改变编码宽度。

TNS 数学参考从公式重新计算系数，转换为 200 位 LPC，再作直接式递推；不复用生产格型或常量表。频谱和 PCM 保持 `atol=1e-6, rtol=1e-5`，记录最大误差、ULP 及失败坐标。另对量化整数、CAC 参数／频谱、TNS 参数／频谱和 PCM 的小端字节摘要要求完全一致。完整验收包含原有 SQ 17,800／9,948、CAC 2,912／2,984 两套矩阵；`validate_cac.py` 也支持显式 `--regression-report` 核对旧提交的固定输出。`--native-only` 属于诊断报告，不能作为便携数学或跨平台验收参考。

### BWE2 深度

`parse-packets --depth bwe2` 及 `inspect::parse_bwe2(&FrameContext, &[u8]) -> Result<Bwe2Report, config::ParseError>` 完成 TNS 后的带宽扩展，在核心对齐之前停止。旧深度不消费新增参数。`Bwe2Report.tns` 保留先前报告并在 JSON 中平铺；新增 `bwe2` 的控制位、有效启用状态、参数来源、两个 LSF 索引、按组增益及起止位。`channels_after_bwe2` 保存输出、复制区间、是否实际处理及可用的 LPC／LSF 诊断量，标识为 `bwe2_stage=scaled_after_bwe2_before_synthesis`。`bwe2_complete` 和汇总 `bwe2_complete_packets` 不替代整包完成状态，组件终点仍未知。

两个控制位先于所有载荷：00 关闭，01 读取右参数，10 读取左参数并按有效声道条件复用到右侧，11 独立读取两侧。零 max_sfb 不读取该侧载荷。复用只接受已定义增益覆盖全部目标组的情况，缺失组明确报错，不借用上包缓存。两套 512×16 LSF 码本与 64 项激励增益是固定格式常量；增益索引 0 是非零小增益。短窗仍以八个 128 点窗口输出，每组增益作用于组内窗口。

当前数值配置为 `apac-bwe2-math-v2`，保留 SQ／CAC／TNS 配置和关闭 BWE2 时的旧输出。使用 Float64 自相关、16 阶源 LPC、LSF 调理及包络恢复，乘加分别舍入，最终谱线转 Float32；准确零源分支保持输入。目标包络直接计算 LSF 的奇偶因子乘积及半角权重，避免展开 LPC 后的相消导致 PCM 超出数学容差；报告中的 `analysis.target_lpc` 仍保留展开系数供诊断，但不参与目标包络计算。该数值规则改变了部分启用 BWE2 时的输出，旧 v1 报告不能作为 v2 的逐位参考。BWE2 专用 radix-2／radix-3 内核继续用于源分析，覆盖 64、96、128、512、768、1024 点，原有 SQ 合成内核不变。旋转因子及三角多项式常量由 Decimal 100／200 位分别生成并核对；正式构建无需 Python、苹果文件、网络或 FFT 依赖。

```sh
python3 -B scripts/verify_bwe2_format.py  # 可选，需匹配哈希的 macOS 组件
python3 -B scripts/generate_bwe2_math.py --check
python3 -B scripts/generate_bwe2_manifest.py --check
# 8,218 个频谱用例、8,218 个 PCM 序列，输入清单预先冻结
python3 -B scripts/validate_bwe2.py --binary target/debug/apac-tool --output reports/bwe2-math.json
python3 -B scripts/validate_bwe2.py --binary target/release/apac-tool \
  --reference-report reports/bwe2-math.json --output reports/bwe2-release.json
# 原生可选诊断，不作为便携数学真值
python3 -B scripts/validate_bwe2.py --binary target/debug/apac-tool --native-only --output reports/bwe2-native.json
# 每个实际运行平台显式执行 release 性能门槛
cargo test -p apac-core --release --lib bwe2_math::tests::optimized_768_is_at_least_twice_as_fast_as_direct_dft -- --exact --ignored --nocapture
```

BWE2 参考采用 Decimal 直接 DFT、独立 Toeplitz 求解和直接多项式求值，不复用生产 FFT、Levinson、LSF 因子求值或三角近似。数学容差仍为 `atol=1e-6, rtol=1e-5`；跨构建另要求所有阶段摘要逐位相同。`data/bwe2-vectors-v2.json` 在原矩阵之外加入 96 个频谱用例和 96 个 PCM 序列，覆盖易发生相消的内部 LSF 索引组合、长短窗、两档复制范围、左右声道和分数步长增益。原生采用明确的分层验收：参数／边界精确，使用相同原生 LPC 输入后的变换、复制和增益控制通过原容差；完整原生路径与输入隔离路径的浮点差异均另行保留，不把苹果 Float32/FMA 的 LPC 舍入接入默认模型。

所有验收入口显式接收二进制和报告路径，拒绝覆盖、缺失用例、指纹变化和执行中源码／二进制变化；便携测试不依赖 docs/local。旧 TNS／BWE2 矩阵也可显式使用 `--regression-report reports/previous-math.json` 重新执行并对照原摘要，语义与 SQ／CAC 一致；数值配置、常量和向量身份必须相同。BWE2 元数据另记录 `bwe2_numeric_profile`、格式字典与数学常量摘要。本深度仍只报告当前核心帧；完整包及内嵌帧的处理见下文 `packet` 深度。BWE2 深度不读取 DRC；后续载荷和关闭策略支持见下文。重配置、LRVQ 和多声道限制继续保留。

### DRC 载荷深度

`parse-packets --depth drc` 和 `inspect::parse_drc(&FrameContext, &[u8]) -> Result<DrcReport, config::ParseError>` 在受限配置下读取场景更新后的 DRC，停在 trimming 入口。支持一个 location 1 系数集合、一个增益序列、单频带、coding profile 0、线性插值、1024 帧及显式 64 采样的最小时间间隔。指令效果限定为已验证的 2／5／32（或无指令）。配置重述须保持相同编码结构；响度元数据更新会完整解析，曲线与 shape filter 只保留声明。非终止增益扩展明确停止。

报告保留 BWE2 及之前的结果，新增精确的 1/8 dB 整数增益、采样时间、码字范围、配置／元数据来源及哈希。`drc_complete` 只表示到达 trimming 入口；`drc_history_sufficient` 表示已解析的前一帧提供了当前帧起点之前的增益节点，不代表已实现插值或播放处理。`drc_processing_applied=false`；缺席 CPE 仍读取 DRC。截断、计数或时间越界、节点不推进直接报错，不复制原生的零增益恢复。

### 固定关闭策略的 DRC PCM

受支持的 DRC 配置会完整读取增益载荷，并固定采用 `drc_processing=off`、`loudness_normalization=off`。不应用播放增益、曲线或 shape filter，也没有尚未实现的开启选项。`decode-sq` 元数据记录载荷解析完成状态、规则版本、码表摘要及后端版本；历史增益节点不足会单独计数，不伪造历史状态。关闭策略无需以这些节点插值音频。保持编码结构不变的声明与响度元数据更新会正常推进，并与左右 overlap 一起按外层包原子提交。它们不会改变关闭策略下的 PCM，后续帧也不模拟苹果的交叉淡化舍入。

原生参考必须在属性和 cookie 设置后、输入前 reset。省略这一步的历史启动交叉淡化反例仍作为失败记录保留。初始化 reset 能消除启动反例，运行中元数据变化后的外层切换差异仍单列保留。原生验收 `apac-drc-off-native-v2` 要求实际选中集合为空、DRC 内核对相同输入逐位恒等、零新增延迟、整数参数与边界正确；苹果外层 Float32 舍入只作诊断。Rust 的正确性由独立数学参考及三平台 debug／release 逐位一致验证，不以复制苹果舍入为目标。完整编码控制 PCM 比较仍使用原容差并在超差时报失败。

```sh
python3 scripts/generate_drc_manifest.py --check
python3 scripts/generate_drc_pcm_manifest.py --check
python3 scripts/validate_drc.py --binary target/debug/apac-tool --report reports/drc-parser.json
python3 scripts/validate_drc_pcm.py --binary target/debug/apac-tool --report reports/drc-pcm-math.json
# 其他平台／release 用同一提交的完整数学报告逐位核对
python3 scripts/validate_drc_pcm.py --binary target/release/apac-tool \
  --reference-report reports/drc-pcm-math.json --report reports/drc-pcm-release.json
# macOS：原生整数与边界；以及完整编码媒体、关闭处理、恢复路径与随机窗口
python3 scripts/validate_drc.py --binary target/debug/apac-tool --native --report reports/drc-native-parameters.json
python3 scripts/validate_drc_native.py --binary target/debug/apac-tool --report reports/drc-native-media.json
```

人工清单分别冻结 2,516 个解析用例和 2,640 个 PCM 序列，正式构建和便携验收均不依赖研究目录、网络或苹果文件。完整编码器控制的 PCM 超差会使验收失败，且保留失败指标与输入。

### 完整包深度

`parse-packets --depth packet` 和 `inspect::parse_packet(&FrameContext, &[u8]) -> Result<PacketReport, config::ParseError>` 在限定配置下继续解析核心对齐、场景更新、DRC、关闭的 trimming 与末字节零填充。DRC 使 trimming 恰好按字节结束时，还验证编码器写出的零 custom-data 标志及其填充，不接受任意额外尾部。接受 ASP 类型 0／1，以及无重配置、含零或一个内嵌 preroll 的类型 2；内嵌帧长度限于 1–4096 字节，拒绝嵌套、类型 3、未知载荷、非零填充和额外尾部。旧深度、默认值和停止位置保持不变。

`PacketReport.bwe2` 保留前阶段结果，JSON 继续平铺；新增 `packet_complete`、`packet_state_profile=apac-asp-state-v1`、`packet_tail` 和可选 `embedded_preroll`。核心终点来自实际核心语法及对齐，随后才是 ancillary；频谱终点不充当组件终点。只有整个外层包及内嵌帧均覆盖后，整包 `status` 才为 `complete`。这仍是语法状态，不表示支持任意配置或无需解码状态。CPE 缺席时原始声道数组保持空，频谱阶段完成标志保持 false，完整包仍可完成。

根报告 `fields` 统一使用外层包坐标，内嵌字段增加 `asp.preroll.` 前缀；`embedded_preroll.report` 内的坐标则从该内嵌帧 bit 0 开始，由外层 `start_bit_offset`／`end_bit_offset` 定位。内嵌错误报告外层包坐标，并在消息中保留内嵌位置。汇总增加完整包、内嵌帧及其完成数量。

配置兼容仅新增默认中性单场景、单 source 0、单分组／预设路由和已解析的 ContentOrigin 类型 3；逐项检查控制字段，支持同一中性场景的帧内重述。DRC 关闭策略额外接受已验证的场景头 `flags[0]` 声明变体，其他控制仍逐项限定。其他场景控制、remapping、scene graph、metadata／custom data 载荷、未知扩展及非零帧内 trimming 继续明确停止。资格检查不按 cookie 长度或哈希放行，拒绝消息列出字段、实际值和位位置。

### 受限离散声道深度 `channels`

`parse-packets --depth channels` 新增单 channel ASC 的 Mono、Stereo、5.1、7.1、7.1.4、22.2 整包报告。旧 `prefix` 至 `packet` 深度继续使用原双声道入口和报告。

| 布局 | 声道数 | family / level | 元素及输出顺序 |
|---|---:|---|---|
| Mono | 1 | 100 / 0 | SCE：Mono |
| Stereo | 2 | 101 / 0 | CPE：L R |
| 5.1 | 6 | 121 / 1 | CPE、SCE、LFE、CPE：L R C LFE Ls Rs |
| 7.1 | 8 | 128 / 2 | CPE、SCE、LFE、CPE、CPE：L R C LFE Ls Rs Rls Rrs |
| 7.1.4 | 12 | 192 / 3 | CPE、SCE、LFE、四个 CPE：L R C LFE Ls Rs Rls Rrs Vhl Vhr Ltr Rtr |
| 22.2 | 24 | 204 / 4 | 固定 16 元素，顺序见下文 |

22.2 的 TCE 类型序列为 `[1,0,3,1,1,0,3,1,1,0,0,1,1,0,0,1]`（0=SCE、1=CPE、3=LFE），输出顺序固定为 `Lw Rw C LFE2 Rls Rrs L R Cs LFE3 Lss Rss Vhl Vhr Vhc Ts Ltr Rtr Ltm Rtm Ctr Cb Lb Rb`。首对是标签 35／36 的 Lw／Rw；两个 LFE 分别占输出索引 3／9，独立保持 overlap。解码不重排声道，不增加低频管理或 LFE 播放增益。其他具有相同声道数的布局仍会被拒绝。

上述 channel ASC 的 7.1.4／22.2 布局在 `ChannelPacketReport`、解码报告及 PCM 实现元数据中附带可选 `channel_layout_profile=apac-channel-layout-v2`；根据 ASC 类型与实际布局标签判定，不仅依据 12／24 声道数。HOA 输出不使用此离散布局标识，包括相同通道数、HOA 还原的扬声器布局及含离散 ASC 的 HOA 组合流；组件声明顺序不影响这一规则。历史报告缺失该字段仍可读取；旧布局不增加此字段，既有后端、状态、容器、访问及数学配置标识不变。

配置保持 profile 31、44.1/48 kHz、1024 帧、单 ASC、无重映射及既有中性场景规则。只读取 SQ；LRVQ、LRVQ-LFE、其他元素组合和布局、多个 ASC、空间渲染、非零 trimming 与结构重配置明确停止。HOA 使用下述单独的配置和解析入口。DRC 可缺席，或使用已验证的单序列／单频带／profile 0 关闭策略；基准声道数必须与 cookie 声明一致，不应用曲线、shape filter、响度处理或额外 LFE 增益。

channel ASC 的实际读取顺序是**每个元素后立即读取该元素的 BWE2**，然后才进入下一元素；全部元素完成后作核心对齐和 ancillary。SCE 有一份 ICS／SQ／TNS，CPE 复用既有左右流、CAC、TNS，LFE 的 SQ 后没有 TNS 或 BWE2 位。SCE 的 BWE2 启用位为真时总会读取两个 LSF 索引；零 `max_sfb` 不读取组增益，也不恢复零输入的频谱。CPE 保持原先的频带门控和局部参数复用规则，参数不跨元素继承。

库入口为 `ChannelFrameContext::from_cookie` 和 `inspect::parse_channel_packet(&ChannelFrameContext, &[u8])`。上下文提供声道数、布局、标签、元素配置和资格查询；`ChannelPacketReport` 逐元素保存存在状态、编码方式、量化频谱、CAC／TNS／BWE2 数据及各阶段频谱。元素内 `channel_index` 是局部编号，`configuration.output_channels` 显式映射到输出声道。`end_bit_offset` 是元素本体终点，紧随的 BWE2 范围单独记录；只有全部元素和尾部完成才设置 `packet_complete=true`。语法错误附带元素索引和可确定的位位置。

缺席元素的编码声道数组为空，合成仅输出该元素已有 overlap 尾部并清零，不影响其他声道。内嵌帧先推进全部声道和 DRC；Mono／Stereo／5.1／7.1 已核实的 preroll 容量分别是 2048／4096／12288／16384 字节；7.1.4／22.2 的实测容量分别为 24576／49152 字节。普通包仍独立受 16 MiB 上限约束。最后一个元素、DRC、尾部或合成失败都会回滚整个外层包。`channels` 深度选择后面的包时，会先读取目录中已有的前置包以建立声明和增益节点状态。

```sh
# macOS 原生编码控制新增 5.1 预设
apac-tool fixture --layout surround51 --signals noise --out artifacts/demo/51-fixture
# 新深度仍读取包目录
apac-tool parse-packets artifacts/demo/51-packets --depth channels --output reports/51-channels.jsonl
# 2,742 个独立人工序列；便携验收不依赖苹果文件或研究目录
python3 -B scripts/generate_channel_manifest.py --check
python3 -B scripts/validate_channels.py --binary target/debug/apac-tool --report reports/channels-math.json
python3 -B scripts/validate_channels.py --binary target/release/apac-tool \
  --reference-report reports/channels-math.json --report reports/channels-release.json
```

### 7.1.4／22.2 的独立验收

新增 2,076 个数学序列、208 个三种输入及范围访问用例；单独用紧凑验收程序检查 131,328 个存在位组合，只输出摘要。旧矩阵的布局枚举和向量身份保持不变。生产解码无需 Python 或苹果文件。

```sh
cargo +1.98.0 build --offline --workspace --bins --examples
python3 -B scripts/generate_layout_manifest.py --check
python3 -B scripts/validate_layouts.py --binary target/debug/apac-tool \
  --presence-binary target/debug/examples/layout_presence --report reports/layouts-math.json
# Windows 将二进制路径替换为对应的 .exe；两个二进制必须来自同一构建。
python3 -B scripts/validate_layouts.py --binary target/release/apac-tool \
  --presence-binary target/release/examples/layout_presence \
  --reference-report reports/layouts-math.json --report reports/layouts-release.json
```

## HOA

### HOA 系数解码（实验性）

纯 Rust SQ 后端从合格 HOA ASC 输出交错系数 PCM，不进行空间渲染或播放增益处理。按 cookie 的完整字段及 ASC 类型分派，声道数量本身不构成资格。

| 能力 | 当前支持范围 |
|---|---|
| 固定恢复域 | 零至十阶完整系数域，以及显式 1–121 系数域；单系数仅纯 ambient，显式维度的 salient 描述使用模式 0–3 |
| 分量与描述 | 可变 salient／ambient 数量；salient 一至整体阶数、6–9 位量化、每分量 1–16 带、空间方法 0–2 |
| 传输元素 | HOA SCE、CPE、LFE及零通道扩展元素；核心数量 ≤ 传输通道 ≤ 输出数量，元素数量独立计算 |
| 空间恢复 | 既有静态 ambient 选择、四路变换、覆盖／叠加，以及按实际内部／输出维度的动态选择 |
| 动态分带 | 方法 0–2、1–8 有效带；内部维度小于输出时读取八组实际长度列表／位图，否则不读取映射载荷 |
| 公共配置 | 单 HOA ASC、44.1／48 kHz、1024 帧、中性场景；profile 5 level 0／1／2 及 profile 0 level 0 的已核实通道上限 |
| 输出与访问 | ACN/SN3D、显式源声道标签及下述内置源布局还原；包目录、CAF、受限 MP4／M4A，范围请求从包零顺序推进 |

LRVQ 与外层 ASP 重配置尚未开放；HOA CAF／MP4 支持下述 fast 访问模式。DRC／响度关闭，保留 `experimental=true`。下文说明各数学及状态规则的默认配置与扩展，实际支持范围以本表及完整资格检查为准。

### 源布局与归一化

保留 cookie 声明的标签及声道顺序。自定义布局支持显式 SN3D、N3D、重排的 ACN 标签以及普通声道标签；不会把 N3D 自动缩放成 SN3D。`HoaFrameContext::source_normalization()` 在标签能确定时返回归一化名称，显式标签的实际系数编号记录在报告中。AudioCodecs 7.0 的 profile 布局表接受自定义 N3D 标签，但不接受 tagged ACN/N3D 和 Ambisonic B-format；后两者可解析语法，解码资格明确拒绝。

普通源布局按已验证的 `parameter_0` 执行固定矩阵还原、带 LFE 省略的位置恢复或直接位置恢复。固定矩阵共用有界常量表，行步长使用实际恢复系数数；超出表容量的输入拒绝，不读取相邻表。内部与输出数量不同而未开启动态选择时，按输出维度裁剪或补零。若同时开启动态选择，先完成源布局恢复，再执行既有映射规则。公开布局所指的 LFE 位置用于输出；参考组件 CICP_7 别名的错误 LFE 位置仅保留在独立诊断中。

报告的可选 `hoa.source_layout` 包含源布局、归一化／系数编号、转换方式及最终 `channels[].channel_index` 频谱；`hoa.channels_after_hoa` 在此分支保留转换前的内部恢复域。PCM 元数据增加 `hoa_source_layout_profile`、格式摘要和源声道数；扬声器布局不附加 HOA 输出阶数。源布局规则为 `apac-hoa-source-layout-format-v1`，后端为 `rust_hoa_source_layout_sq_drc_off_f64_fft_v1`。这一步完成码流声明的源声道还原，不包含外部空间渲染。

```sh
python3 -B scripts/generate_hoa_source_layout_format.py --check
python3 -B scripts/hoa_source_layout_vectors.py --check
python3 -B scripts/validate_hoa_source_layouts.py --binary target/debug/apac-tool \
  --report reports/hoa-source-layout-math.json
```

### 静态核心载波重映射

SQ／CAC／TNS／BWE2 完成后，按 cookie 的固定核心前缀重排载波，再执行空间恢复、源布局还原和动态选择。索引位宽由输出数量决定，单输出为零位；有效前缀长度为 cookie 的 salient＋ambient 数量，值必须小于该核心数量。剩余输出尾项完整读取和报告，其数值不影响音频。帧内活动分量变化不会改变这份固定映射。

普通置换的线上方向为传输载波→逻辑核心槽位，例如 `[1,2,3,0]` 将输入 `[A,B,C,D]` 恢复为 `[D,A,B,C]`。参考也接受部分非置换写法；实现对索引关系进行有界归约，保留全部可终止组合，并在创建解码状态前拒绝无法终止的索引环。此规则与动态选择的目标唯一性检查独立。

`HoaFrameContext::static_remapping()` 返回可选只读映射，`core_to_transport` 提供逻辑核心槽位→实际传输载波的有效排列；`wire_indices` 和 `ignored_tail` 保留原值。可选 `hoa.static_remapping` 及 PCM implementation 中的同名字段记录规则身份。分量角色和未使用载波报告使用实际物理编号，全部载波仍数值校验。规则为 `apac-hoa-static-remapping-v1`，后端为 `rust_hoa_static_remapping_sq_drc_off_f64_fft_v1`。

```sh
python3 -B scripts/hoa_remapping_vectors.py --check
python3 -B scripts/validate_hoa_remapping.py --binary target/debug/apac-tool \
  --report reports/hoa-remapping-math.json
```

库入口为 `HoaFrameContext::from_cookie`、`inspect::parse_hoa_packet(&HoaFrameContext, &[u8])` 和 `HoaPacketReport`。报告中的 `elements` 保存传输整数及 SQ／TNS／BWE2 各阶段；新增 `hoa` 保存公共窗口、空间模式、ambient 索引、恢复后系数频谱和位范围。`hoa_complete` 仅表示恢复阶段完成，整包仍须完成 ancillary 与尾部。单包解析入口从初始 HOA／DRC 状态开始；需要连续报告时使用 `parse-packets --depth hoa`，选择中间包也会先推进已有前缀。旧深度和离散声道报告不变。

原无选择／变换扩展的一、三阶 ambient 恢复均已确认是精确恒等映射，包括短窗转置与逆转置抵消；不能将该结论推广到其他零 salient 配置。规则标识为 `apac-hoa-ambient-math-v1`，不增加浮点近似或修改既有 SQ／TNS／BWE2／合成模型。PCM 元数据记录 `hoa_numeric_profile`、实际阶数、ACN 和 SN3D，后端为 `rust_hoa_ambient_sq_drc_off_f64_fft_v1`，状态规则为 `apac-hoa-ambient-state-v1`。DRC／响度处理固定关闭，`experimental=true` 保留。

HOA 的三个输入入口均按包序建立状态；包目录必须包含包零，CAF／MP4 默认解码并丢弃前置 PCM，也可显式使用下述 `--access fast`。离散声道快速模式不变。一／二／三阶分别实测的内嵌 preroll 容量为 8,192／18,432／32,768 字节，普通包仍受独立的 16 MiB 限制。内嵌帧先于当前帧推进；HOA 模式／描述历史、全部系数 overlap 与 DRC 按外层包原子提交，错误回滚，reset 恢复初始状态。原恒等 ambient 路径的缺席 SCE 仅输出自身旧 overlap 后清零，原始频谱数组保持空；启用变换时按恢复后的输出系数管理 overlap。

```sh
apac-tool parse-packets artifacts/hoa-packets --depth hoa --output reports/hoa.jsonl
apac-tool decode-sq input.m4a --out artifacts/hoa-pcm --frames 8192
python3 -B scripts/generate_hoa_manifest.py --check
# 原三阶 48 kHz ambient 矩阵；需要复核该路径时运行
python3 -B scripts/validate_hoa.py --binary target/debug/apac-tool --report reports/hoa-math.json
python3 -B scripts/validate_hoa.py --binary target/release/apac-tool \
  --reference-report reports/hoa-math.json --report reports/hoa-release.json
```

HOA 验证只运行新增用例和受影响接口的精简回归，不要求重跑旧完整矩阵。真实媒体在开发和发布时都只取有 DRC、无 DRC各一份代表。先用 `cargo +1.98.0 test --offline -p apac-research --release --lib --no-run --message-format=json` 构建，再将输出中的库测试 `executable` 路径传给 `scripts/validate_hoa_media.py --test-binary PATH --with-drc INPUT --without-drc INPUT --report REPORT`。该工具逐包完整解码、核验输入并仅保留 PCM 摘要，不落盘整曲 PCM；报告绑定代码、源码、测试二进制、工具链和输入摘要。`scripts/validate_hoa_native.py` 可复核已有的哈希约束只读 HOA 跟踪，无需重复跟踪已确认的 SQ 工具。

### 默认 salient HOA

接受二／三阶、5 个声明 salient 槽位、零 ambient，每个分量默认 4 个空间子带（1–16 扩展见下文）、与输出相同的阶数、6 位描述量化。完整读取 9／16 个传输 SCE；空间恢复只使用前 5 个核心分量，输出为 9／16 个 ACN 系数。`elements[].configuration.transport_channels` 记录载波槽位，`output_channels` 为空，避免误认为传输槽位与输出系数一一对应。未使用的传输槽位也必须通过语法与数值检查。

`hoa.spatial.salient` 包含 20 份分量／子带描述：模式 0–5、量化值、符号、方向或变换索引、位范围及恢复后的 Float64 向量；`history_frame_sha256` 指向上一个已处理核心帧（可为内嵌帧），初始为 null。`SalientDescriptor.restored` 为有界 `Vec<f64>`，二／三阶分别为 9／16 项，不填充额外系数；历史三阶 JSON 保持兼容。两种采样率的 `subband_ends` 都是频率优先布局中的终点 `[32,80,216,1024]`，`lines_per_window` 为每窗终点，短窗是 `[4,10,27,128]`。上下文的 `order()`、`channel_count()`、`sample_rate_hz()`、`salient_components()`、`numeric_profile()` 和 `state_profile()` 可查询分支。单包解析从初始状态开始，不能用来随机恢复中间的差分帧。

三阶规则保持 `apac-hoa-salient-math-v1`，二阶使用 `apac-hoa-salient-order2-math-v1`：固定 Float64 描述反量化、历史差分、方向恢复、矩阵变换与五分量求和，最后每条系数谱线一次舍入为 Float32。方向索引按度编码，方位角采用顺时针约定；二阶使用相应球谐向量除以 3，三阶保持除以 4，再由独立编码值覆盖前四项。这不改变最终 ACN/SN3D 输出标签。二阶不是截取已经按三阶归一化的向量，原生的百万分之一取整也不进入独立模型。二阶格式字典为 `apac-hoa-salient-order2-format-v1`；三阶格式常量与摘要不改动。两阶共用已有 Decimal 100／200 位生成的角度与根式常量，不复制常量表。

合成始终作用于恢复后的实际系数数组；单个传输 SCE 缺席不能清除同编号的系数 overlap。后端为 `rust_hoa_salient_sq_drc_off_f64_fft_v1`，状态为 `apac-hoa-salient-state-v1`，PCM 实现元数据另含 `hoa_format_sha256`、`hoa_tables_sha256`。原三阶及离散声道的报告、标识与数值保持兼容。

```sh
python3 -B scripts/generate_hoa_salient_math.py --check
python3 -B scripts/generate_hoa_salient_manifest.py --check
# 原三阶 48 kHz salient 矩阵；需要复核该路径时运行
python3 -B scripts/validate_hoa_salient.py --binary target/debug/apac-tool --report reports/salient-math.json
python3 -B scripts/validate_hoa_salient.py --binary target/release/apac-tool \
  --reference-report reports/salient-math.json --report reports/salient-release.json
# 两份短控制；默认 DRC 的回读保留原值，不把它解释成关闭处理
apac-tool fixture --layout hoa3 --signals channel-solo --duration 2 --out artifacts/salient-default
apac-tool fixture --layout hoa3 --signals channel-solo --duration 2 --drc-configuration none --out artifacts/salient-none
python3 -B scripts/validate_hoa_salient_media.py --binary target/release/apac-tool \
  --default-control artifacts/salient-default --none-control artifacts/salient-none --report reports/salient-controls.json
```

`validate_hoa_salient_native.py` 只复核新增空间参数、边界、状态和系数映射；完整原生浮点差异单列为诊断，不替代独立数学验收。日常与发布均按代表类别取样，不重跑旧全量矩阵。

### 一、二阶及采样率扩展的精简验证

新增 27 个语义序列，涵盖两个采样率、实际维度、二阶模式、状态与三个输入入口。旧生成器的默认参数、枚举范围及冻结向量身份不变。完整原生编码控制只取一阶 44.1 kHz 默认 DRC、二阶 48 kHz DRC none 各一份：

```sh
python3 -B scripts/generate_hoa_orders_manifest.py --check
python3 -B scripts/validate_hoa_orders.py --binary target/debug/apac-tool --report reports/hoa-orders-math.json
python3 -B scripts/validate_hoa_orders.py --binary target/release/apac-tool \
  --reference-report reports/hoa-orders-math.json --report reports/hoa-orders-release.json
apac-tool fixture --layout hoa1 --sample-rate 44100 --signals channel-solo --duration 2 --out artifacts/hoa1-control
apac-tool fixture --layout hoa2 --sample-rate 48000 --signals channel-solo --duration 2 --drc-configuration none --out artifacts/hoa2-control
python3 -B scripts/validate_hoa_orders_media.py --binary target/release/apac-tool \
  --hoa1-control artifacts/hoa1-control --hoa2-control artifacts/hoa2-control --report reports/hoa-orders-controls.json
```

`validate_hoa_orders_native.py` 复核已保存的新增原生边界和恢复证据；无需重新跟踪原有 SQ 工具。正式构建及便携向量不依赖苹果文件、研究目录或网络。

### 固定混合 HOA

二／三阶均接受 5 salient＋4 ambient，原四带混合配置采用四个空间子带、六位描述量化和与输出相同的阶数；每分量数量扩展见下文。传输槽位 `0..3` 是 ambient，`4..8` 是 salient；三阶 `9..15` 仍完整读取和数值校验，但不参与恢复。核心通道数均为 9，输出系数分别为 9／16。默认映射中 ACN0..3 由 ambient 精确覆盖，其他系数由五个 salient 分量恢复；DRC 基准声道数使用输出数。

`hoa.mixed` 记录两类传输映射、ambient 输出位置、未使用槽位和描述数学标识。混合描述新增可选的 `coded_coefficient_indices` 与 `ambient_omitted_coefficients`：`quantized`／`signs_positive` 只保存实际编码项，按前者所列 ACN 索引递增排列；`restored` 始终为完整的 9／16 项。模式 0–3 省略前四项并将对应恢复历史置零，模式 4／5 保留完整变换或方向描述。纯 ambient／salient 报告不新增这些可选字段，旧数组含义保持不变。

混合数值规则为 `apac-hoa-mixed-math-v1`，状态为 `apac-hoa-mixed-state-v1`，后端为 `rust_hoa_mixed_sq_drc_off_f64_fft_v1`。PCM 实现元数据另记录 `hoa_descriptor_numeric_profile`，沿用已有二／三阶格式和数学摘要；无新增常量表。上下文增加 `ambient_components()`、`core_channels()`、`transport_channels()`、`descriptor_numeric_profile()` 查询。全部输出 overlap、描述历史和 DRC 一起按外层包提交，内嵌帧优先；缺席传输不会按同编号清除输出系数。两种混合实例分别核实的 preroll 容量为 18,432／32,768 字节。

三个输入入口及顺序范围解码直接接受混合配置，CAF／MP4 的 HOA fast 访问规则见下文。新增验证使用 14 个语义序列，独立 Decimal 参考和三平台 debug／release 摘要；原生证据来自两份短人工载荷，不宣称已有真实混合歌曲覆盖。不重跑旧完整矩阵。

```sh
python3 -B scripts/generate_hoa_mixed_manifest.py --check
python3 -B scripts/validate_hoa_mixed.py --binary target/debug/apac-tool --report reports/hoa-mixed-math.json
python3 -B scripts/validate_hoa_mixed.py --binary target/release/apac-tool \
  --reference-report reports/hoa-mixed-math.json --report reports/hoa-mixed-release.json
# Windows 使用对应 .exe；必需二进制缺失会报错。
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_mixed
```

`validate_hoa_mixed_native.py --binary PATH --capture ORDER2_CAPTURE --capture ORDER3_CAPTURE --report REPORT` 复核已有混合原生捕获；省略 `--binary` 仅证明原生规则，不代表 Rust 验收完成。`validate_hoa_mixed_checks.py` 接受 CLI／库测试二进制和旧报告路径，运行受影响接口测试及旧 HOA、离散声道代表摘要检查；具体参数见 `--help`。

### 静态 ambient 选择与四路变换

上述一／三阶纯 ambient 和二／三阶 mixed 支持 cookie 中的静态选择及三套四路变换。mixed 选择必须是四个严格递增、互不重复且在输出系数范围内的 ACN 索引；纯 ambient 只接受完整系数集合的显式恒等选择。cookie 的 `parameter_3` 缺席表示关闭，值 1–3 固定选择矩阵 0–2，值 4 则在每个核心帧的空间模式头之前读取两位索引，索引 3 为恒等。帧内矩阵选择不改变静态 ACN 选择表，缺席传输或零频带也必须完整读取索引。

新路径先恢复全部 salient 描述和历史，再对前四个 ambient 传输槽位执行变换，最后按选择表覆盖输出。mixed 模式 0–3 省略的是选择集合，不限于前四项；模式 4／5 的描述仍完整保留。三套矩阵系数均为精确 `±1/2`，新变换采用固定 Float64 Neumaier 补偿求和及最终一次 Float32 舍入，避免强相消丢失小信号。纯 ambient 的其余槽位不变；缺席输入不会清空其他输入经变换产生的输出系数。

启用新语法的配置使用 `apac-hoa-static-ambient-math-v1`、`apac-hoa-static-ambient-state-v1` 和 `rust_hoa_static_ambient_sq_drc_off_f64_fft_v1`。原配置的后端、数值标识和输出保持不变。`hoa.spatial.ambient` 可选报告记录选择表、`transform_config`、`effective_index`、`index_source`、索引位范围及 `channels_after_transform`；固定模式的帧索引位范围为 null。原始传输频谱保留，旧 JSON 缺失新增字段时仍可读取。上下文提供 `ambient_selection()`、`ambient_transform()` 和 `static_ambient_enabled()` 查询，PCM 实现元数据新增格式与数学表摘要。

三个输入入口、顺序预热、内嵌帧优先与外层包原子回滚均保持原规则，CAF／MP4 的 HOA fast 访问规则见下文。验证只运行新增语义序列和受影响代表；原生强相消差异单独保留，独立数学容差不变。

```sh
python3 -B scripts/generate_hoa_static_ambient_tables.py --check
python3 -B scripts/generate_hoa_static_ambient_manifest.py --check
python3 -B scripts/validate_hoa_static_ambient.py --binary target/debug/apac-tool --report reports/hoa-static-math.json
python3 -B scripts/validate_hoa_static_ambient.py --binary target/release/apac-tool \
  --reference-report reports/hoa-static-math.json --report reports/hoa-static-release.json
```

`validate_hoa_static_native.py` 复核两份主序列、两个单分支探针及一个独立相消控制的已有捕获，不重复旧 SQ 工具跟踪。`validate_hoa_static_checks.py` 执行精简接口和旧代表回归。所有验收入口显式接收二进制与报告路径；Windows 使用对应 `.exe`，缺少必需二进制会失败。

### 受限动态 HOA 选择

接受内部阶数 2、九个恢复槽位、输出三阶 16 系数的两种固定配置：5 salient／0 ambient 或 5 salient／4 ambient。两者都完整读取 16 个 SCE，核心通道分别为 5／9；每个 salient 描述保持九项系数、六位量化；空间子带数扩展见下文。`order()` 与报告的 `hoa.order`／`coefficient_count` 继续表示 cookie 中内部阶数与维度；`output_order()`、`channel_count()` 以及 PCM 布局表示输出阶数 3、16 个 ACN 系数。`recovery_slot_count()` 和 `dynamic_selection_enabled()` 可查询新分支。

原八带动态配置支持参数 0 的感知锚点划分、参数 1 的 AAC 频带插值及参数 2 的等宽划分；参数 3 仍拒绝；1–7 个有效子带见下文扩展。短窗每窗使用相应长窗终点除以八，选择行不等于短窗编号，也不受短窗分组控制。每个核心帧在空间描述之后读取八组映射：索引列表保留九个目标的线上顺序，位图按 ACN 升序形成九个目标。重复列表目标及位图数量不符均报错，不使用旧映射补足损坏载荷。

恢复先在九槽空间完成原有描述与 ambient 数值处理，再将每个子带的九个 Float32 值逐位复制到 16 个输出位置，其余位置为正零。mixed 的静态 ambient 选择属于内部 `0..8`，现有固定／帧内四路变换继续适用；输出阶数不会改变二阶描述除以 3 的规则。DRC 基准声道数为 16，描述历史留在内部槽位，overlap 始终属于最终 ACN。内嵌帧、错误回滚及 reset 保持原子语义。

`hoa.dynamic_selection` 是可选类型化报告，记录编码方式、八组映射、频带边界、位范围、基础恢复规则与 `before_selection` 九槽频谱；内部结果使用 `slot_index`。内部 ambient 结果放在 `dynamic_selection.internal_ambient`，不使用全局 `acn_index` 标注。`hoa.channels_after_hoa` 始终是最终 16 个 ACN 频谱，新报告另有 `output_order`／`output_coefficient_count`；旧报告不新增这些可选字段。

新规则为 `apac-hoa-dynamic-selection-math-v1`、状态为 `apac-hoa-dynamic-selection-state-v1`、后端为 `rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v1`。PCM 元数据分别记录基础恢复、二阶描述和动态划分格式摘要；原有常量和旧配置标识不变。动态复制不引入音频乘加或舍入，内部数值错误优先于后续映射错误。新实例的 preroll 容量均实测为 32,768 字节。

包目录、CAF、受限 MP4／M4A 入口及范围规则保持不变，输出预算与交错步长使用 16 个声道；默认 HOA 仍从包零预热，CAF／MP4 可显式使用 fast。新增语义序列只覆盖必要分支，原生验证使用两份主控制和感知边界短探针，旧路径只取受影响代表。

```sh
python3 -B scripts/generate_hoa_dynamic_format.py --check
python3 -B scripts/generate_hoa_dynamic_manifest.py --check
python3 -B scripts/validate_hoa_dynamic.py --binary target/debug/apac-tool --report reports/hoa-dynamic-math.json
python3 -B scripts/validate_hoa_dynamic.py --binary target/release/apac-tool \
  --reference-report reports/hoa-dynamic-math.json --report reports/hoa-dynamic-release.json
```

`validate_hoa_dynamic_native.py` 复核指定的哈希约束捕获；其隔离映射参考由原生描述历史及已知传输激励重建，不伪称原生中间缓冲快照。`validate_hoa_dynamic_checks.py` 执行相关接口测试与旧代表摘要检查；正式构建和便携验证不读取苹果文件或研究目录。

### Ambient 与 salient 叠加

在上述固定二阶、固定三阶和动态九槽→十六系数的 5 salient＋4 ambient 配置中，接受 cookie `flag_d=true`。传输槽位 `0..3` 为 ambient、`4..8` 为 salient；静态选择、三套四路变换、帧内变换索引及既有动态划分均可组合。模式 0–3 完整读取九／十六项描述及规定的符号，差分历史不省略 ambient 位置。模式 4／5 沿用完整恢复。`flag_d=false` 继续使用原有省略与覆盖规则；纯 ambient／salient 的新标志组合仍拒绝。

`inspect::AmbientCombination::{Replace, Add}` 和 `HoaFrameContext::ambient_combination()` 提供类型化查询。叠加先计算五个 salient 乘积，再按 ambient 输入 `0..3` 加入矩阵贡献，以 Float64、固定顺序 Neumaier 补偿求和，合并后一次转换为 Float32；不将两路分别舍入后再相加。恒等变换直接使用对应 ambient 输入，非有限结果报错。新增规则为 `apac-hoa-additive-math-v1`、状态为 `apac-hoa-additive-state-v1`，后端为 `rust_hoa_additive_sq_drc_off_f64_fft_v1`。动态分支继续记录动态复制标识，并以 `hoa_recovery_numeric_profile` 标明叠加恢复；旧配置的标识和输出不变。

可选 `hoa.additive` 记录策略、选择表、变换索引、恢复阶段与 `ambient_contributions`。这些诊断贡献采用 Float64；其 `recovery_index` 依 `coordinate_space` 指向固定路径的 ACN 或动态路径的内部槽位，不用诊断值回写计算。新增路径的原 `channels_after_transform` 数组为空，避免伪造中途 Float32 舍入；原始传输频谱及最终 ACN 频谱仍完整保留。描述报告的实际编码索引和空省略集合明确表示线上完整读取。历史报告缺少新增字段时仍可读取。

包目录、CAF、受限 MP4／M4A 使用原入口，无需新增开关。输出 overlap 属于最终 ACN，内嵌帧优先，全部历史、DRC 和 overlap 原子提交。默认范围请求仍从包零预热，CAF／MP4 可显式使用下述 HOA fast。叠加实例的内嵌容量分别为二阶 18,432 bytes、三阶及动态配置 32,768 bytes。

```sh
python3 -B scripts/generate_hoa_additive_manifest.py --check
python3 -B scripts/validate_hoa_additive.py --binary target/debug/apac-tool --report reports/hoa-additive-math.json
python3 -B scripts/validate_hoa_additive.py --binary target/release/apac-tool \
  --reference-report reports/hoa-additive-math.json --report reports/hoa-additive-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_additive
```

`validate_hoa_additive_native.py` 复核三份指定原生捕获；`validate_hoa_additive_checks.py` 运行新增／受影响接口检查及旧代表摘要核对。人工控制不代表真实叠加媒体覆盖，日常和发布均不重跑旧全量矩阵或媒体库。

### 动态 HOA 的 1–8 子带

在内部二阶九槽、输出三阶十六系数的纯 salient、覆盖式 mixed 和叠加式 mixed 路径中，接受 cookie 声明的 1–8 个有效频率子带，支持既有方法 0／1／2。动态选择子带与下述每分量空间子带、八个短窗及短窗分组独立。`HoaFrameContext::dynamic_subband_count() -> Option<usize>` 返回有效数量；非动态配置返回 `None`。

线上载荷始终包含 **八组映射**：列表模式为一个模式位加八组九个四位索引，位图模式为一个模式位加八组十六位位图。只使用前 N 组进行恢复，后续组仍完整读取、校验和报告；损坏或截断未使用组同样失败。合法修改未使用组不会改变 PCM。数量来自 cookie，不提供帧内修改或额外 CLI 开关。

`DynamicSelectionData.subband_ends` 和 `lines_per_window` 现为有界 `Vec<usize>`，长度等于有效数量，`mappings` 始终为八组。1–7 带报告增加 `active_subband_count`、`subband_profile=apac-hoa-dynamic-subbands-v1` 和 `format_sha256`；PCM 实现元数据增加 `hoa_dynamic_subband_count`／`hoa_dynamic_subband_profile`，动态格式摘要指向 `apac-hoa-dynamic-selection-format-v2`。原八带报告省略这些新增字段，继续使用 v1 格式、原标识与原输出；旧 JSON 仍可读取。

新边界使用经过验证的整数表，运行时不做浮点插值。原描述、覆盖／叠加、动态复制、合成及状态／后端规则不变。包目录、CAF、受限 MP4／M4A 使用原入口；默认范围继续从包零预热，也可选择 CAF／MP4 HOA fast，所有失败按外层包回滚。

```sh
python3 -B scripts/generate_hoa_dynamic_subbands_format.py --check
python3 -B scripts/generate_hoa_dynamic_subbands_manifest.py --check
python3 -B scripts/validate_hoa_dynamic_subbands.py --binary target/debug/apac-tool --report reports/hoa-subbands-math.json
python3 -B scripts/validate_hoa_dynamic_subbands.py --binary target/release/apac-tool \
  --reference-report reports/hoa-subbands-math.json --report reports/hoa-subbands-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_dynamic_subbands
```

新增验收把轻量边界／复制检查与代表 PCM 序列分开；`validate_hoa_dynamic_subbands_native.py` 复核指定的原生捕获，`validate_hoa_dynamic_subbands_checks.py` 执行相关接口及旧代表回归。原八带生成器和清单保持不变。

### 每分量 1–16 个 salient 空间子带

五个 salient 分量可以各自声明不同数量，例如 `[1,3,4,9,16]`。支持既有固定二／三阶和动态九槽→十六系数的纯 salient、覆盖式 mixed、叠加式 mixed；默认感知划分为 `parameter_1=0`，方法 1／2 的扩展见下文；保持六位量化；原配置的分量阶数等于内部阶数，固定三阶的二／三阶分量混合见下文。原纯 ambient 路径不变，不开放其他分量数、超出下述范围的阶数组合或帧内重配置。

`HoaFrameContext::salient_subband_counts() -> Option<[usize; 5]>` 返回五个实际数量；无 salient 或数量不为五时返回 `None`，通用配置使用下文实际长度查询。载荷按分量、该分量的局部子带读取，描述总数为五个数量之和，最多 80 条。每条频率线分别查找各分量自己的描述区间；描述历史也按实际数量保存，不采用原生历史缓冲中的填充步长。普通／覆盖恢复仍按分量 0..4 求和，叠加仍使用原补偿规则。动态选择随后按自身有效子带恢复，完整八组线上映射规则不变。

`SalientSpatialData.subband_ends`／`lines_per_window` 改为有界 `Vec<usize>`。五个数量相同时保存真实公共端点；不同时这两个向量为空且 JSON 省略对应字段。新增可选 `component_subbands` 保存五份 `SalientSubbandInfo`（分量序号、数量和长／短窗端点），`descriptors` 只保存实际编码的描述。不要用固定四带或最大数量计算紧凑描述偏移。

新配置的报告记录 `subband_profile=apac-hoa-salient-subbands-v1` 和格式摘要；PCM 元数据增加 `hoa_salient_subband_counts`、`hoa_salient_subband_profile`、`hoa_salient_subband_format_sha256`。原方法 0 的 `[4,4,4,4,4]` 不新增字段，保留旧 JSON、数学、状态和后端标识及输出。正式构建只使用验证过的整数边界，复用现有字典、角度和矩阵常量。

包目录、CAF、受限 MP4／M4A 接口和范围规则不变。缺席或未使用载波仍完整校验；内嵌帧先处理，所有描述历史、overlap、映射和 DRC 按外层包原子提交。CAF／MP4 支持下述 HOA fast 访问规则。

```sh
python3 -B scripts/generate_hoa_salient_subbands_format.py --check
python3 -B scripts/generate_hoa_salient_subbands_manifest.py --check
python3 -B scripts/validate_hoa_salient_subbands.py --binary target/debug/apac-tool --report reports/hoa-spatial-subbands-math.json
python3 -B scripts/validate_hoa_salient_subbands.py --binary target/release/apac-tool \
  --reference-report reports/hoa-spatial-subbands-math.json --report reports/hoa-spatial-subbands-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_salient_subbands
```

`validate_hoa_salient_subbands_native.py` 复核四份短原生捕获；`validate_hoa_salient_subbands_checks.py` 运行相关接口与旧四带代表摘要检查。边界按缓存整组核对，完整数学只使用语义代表序列，不枚举五个数量的全部组合或重跑旧全量矩阵。

### salient 空间划分方法 1／2

所有上述五分量 salient、覆盖／叠加 mixed 的固定及动态路径均接受 cookie `components[0].hoa.parameter_1` 为 0／1／2。五个分量共用空间方法，各自仍可声明 1–16 带。它与 `hoa.dynamic_selection.parameter` 及动态有效带数独立；不会增加线上描述或改变紧凑历史的维度。方法 3、纯 ambient 的新增参数组合及帧内方法更新继续拒绝。

| 空间方法 | 划分依据 | 四带长窗终点 |
|---|---|---|
| 0 | 原感知频率锚点 | `[32,80,216,1024]` |
| 1 | 既有 49 个长窗频带插值 | `[56,208,568,1024]` |
| 2 | 等宽划分 | `[256,512,768,1024]` |

边界采用已核对的整数表，短窗每窗端点为长窗端点除以八，与短窗分组独立；两采样率使用相同的已验证结果。一带都覆盖 1024 线，数量不同时分别按每个分量的端点选择描述。浮点求和、描述历史、覆盖／叠加、动态复制及输出合成继续沿用原数学规则，不增加平台插值或新的数学／状态／后端标识。

`HoaFrameContext::salient_partition_method() -> Option<u8>` 返回空间方法，无 salient 时返回 `None`。方法 1／2 的 `hoa.spatial.salient` 增加可选 `partition_method`、`partition_profile=apac-hoa-salient-partition-v1`，`format_sha256` 指向 `apac-hoa-salient-subbands-format-v2`；相同数量仍报告真实公共边界，不同数量仍使用逐分量报告。PCM 实现元数据增加 `hoa_salient_partition_method`／`hoa_salient_partition_profile`，并记录五个数量及实际格式摘要，即使数量均为四带也记录。历史方法 0 的 JSON、格式摘要、标识和输出保持不变。

三个输入入口无需新参数，由 cookie 选择方法；HOA 默认顺序预热，CAF／MP4 可使用 fast。内嵌帧、数值首错、整包回滚、重置、容器核验及 128 MiB 输出保护不变。

```sh
python3 -B scripts/generate_hoa_salient_partition_format.py --check
python3 -B scripts/generate_hoa_salient_partition_manifest.py --check
python3 -B scripts/validate_hoa_salient_partition.py --binary target/debug/apac-tool --report reports/hoa-partition-math.json
python3 -B scripts/validate_hoa_salient_partition.py --binary target/release/apac-tool \
  --reference-report reports/hoa-partition-math.json --report reports/hoa-partition-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_salient_partition
```

`validate_hoa_salient_partition_native.py` 复核四份短原生捕获与整组 1–16 带缓存；`validate_hoa_salient_partition_checks.py` 检查受影响接口及五个旧代表。独立数学使用六组短序列，逐线边界测试不执行额外 IMDCT；不重跑旧完整矩阵或媒体库。

### 固定三阶中的二／三阶 salient 分量混合

整体三阶、16 个 SCE 和 16 通道 ACN/SN3D 输出保持不变，五个分量可以各自声明二阶或三阶，例如 `[2,3,2,3,3]`。纯 salient、覆盖 mixed、叠加 mixed 均支持；也允许全部为二阶。保持两采样率、每分量 1–16 带、空间方法 0／1／2、静态 ambient 选择和四路变换。一阶描述的扩展见下文；零阶描述、超出下述范围的动态阶数组合及帧内阶数更新仍拒绝。

`HoaFrameContext::salient_component_orders() -> Option<[u8; 5]>` 返回五个实际阶数；无 salient 或数量不为五时返回 `None`，通用配置使用下文实际长度查询。`order()`、`recovery_slot_count()`、`output_order()`、`channel_count()` 仍表示整体配置。本扩展中分别为 3、16、3、16，不能用第一份描述的九项维度决定 PCM 步长。

每份描述及其差分历史只保存实际九项或十六项。二阶使用既有二阶字典、9×9 矩阵及方向除以 3 的规则，三阶使用原十六维规则；较低阶分量对 ACN9..15 没有贡献，合成仍处理全部十六路输出 overlap。覆盖 mixed 的模式 0–3 仅省略 ambient 选择与该分量范围的交集，模式 4／5 完整恢复；叠加完整读取并沿用原补偿求和及一次 Float32 舍入。

新配置的 `hoa.spatial.salient.component_orders` 保存五份 `SalientComponentOrderInfo`，记录分量序号、阶数、实际系数数、所用数学规则和字典摘要。描述数组不补成十六项。组合规则为 `apac-hoa-component-orders-math-v1`，状态为 `apac-hoa-component-orders-state-v1`，后端为 `rust_hoa_component_orders_sq_drc_off_f64_fft_v1`。`descriptor_numeric_profile()` 在新配置返回组合规则，逐分量字段记录实际复用的二／三阶规则。

PCM 实现元数据增加 `hoa_salient_component_orders`、`hoa_salient_components`，并记录组合描述规则；新配置以逐分量字典摘要取代单个 `hoa_format_sha256`。整体三阶且分量全部为三阶，以及固定二阶、原动态和纯 ambient 配置的 JSON、标识与输出均不改变。

四个新实例的内嵌 preroll 容量均实测为 32,768 字节。全部传输载波仍须完整校验；描述数值检查先于整体恢复及尾部，内嵌优先、整包回滚、reset、三个输入入口及范围规则不变。CAF／MP4 可显式使用 HOA fast。

```sh
python3 -B scripts/generate_hoa_component_orders_manifest.py --check
python3 -B scripts/validate_hoa_component_orders.py --binary target/debug/apac-tool --report reports/hoa-component-orders-math.json
python3 -B scripts/validate_hoa_component_orders.py --binary target/release/apac-tool \
  --reference-report reports/hoa-component-orders-math.json --report reports/hoa-component-orders-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_component_orders
```

`validate_hoa_component_orders_native.py` 复核三份主控制及全二阶短探针；`validate_hoa_component_orders_checks.py` 执行相关检查与六个旧代表，元数据必须使用对应平台的旧报告。独立数学只运行新增短序列，不重跑旧完整矩阵或媒体库。

### 一阶 salient 描述

在固定二阶、固定三阶和动态九槽→十六系数中，五个 salient 分量可以分别声明一阶。固定二阶及动态路径接受分量阶数 1／2，固定三阶接受 1／2／3；纯 salient、覆盖 mixed、叠加 mixed 均支持，也允许全部分量为一阶。整体恢复／输出仍分别为 9／9、16／16、9／16，未新增整体一阶的五分量 salient 配置。

一阶描述使用独立 `apac-hoa-salient-order1-format-v1` 字典（八套 Huffman 表、四个 4×4 矩阵）和 `apac-hoa-salient-order1-math-v1`。沿用 Float64 反量化、差分和矩阵运算；一阶方向定义除以 2，复用旧角度和根式常量。当前显式系数标志下，模式 5 始终读取方向角和四个显式系数，再覆盖全部四项；角度保留在报告和来源摘要中，不因最终被覆盖而跳过载荷。

覆盖 mixed 的模式 0–3 可省略全部四项：当选择为 0..3 时，量化值、符号和编码索引数组为空，四项恢复向量及历史清零。描述仍然存在，统一模式下位范围可以为零长度；空数组不代表四个线上零码字。模式 4／5 仍完整读取、恢复四项。动态路径继续先恢复九槽，再读取并校验完整八组九槽映射，不能缩成四槽或跳过零槽。

接口不变：`salient_component_orders()` 可返回 1，`component_orders` 报告记录四项维度及一阶数学／格式摘要。含一阶分量的 `hoa.spatial.salient.order1_profile` 和 PCM 实现元数据 `hoa_salient_order1_profile` 为 `apac-hoa-salient-order1-v1`。固定配置复用 `component-orders` 组合数学、状态和后端；动态配置保留 `dynamic-selection` 顶层数学，并记录 `component-orders` 基础恢复。其他历史配置不增加字段或改变标识、常量、PCM。

维持五个 salient、零／四个 ambient、44.1／48 kHz、1024 帧、六位量化、每分量 1–16 带、空间方法 0／1／2、已有选择／变换范围、SQ、中性场景、DRC／响度关闭及 `experimental=true`。全部传输 SCE 仍须完整校验；overlap 和 DRC 属于实际输出，内嵌优先、数值首错、原子回滚、reset、容器核验及输出保护不变。其他已扩展的维度和帧内参数支持范围见下文；CAF／MP4 可显式使用 HOA fast。

```sh
python3 -B scripts/generate_hoa_order1_manifest.py --check
python3 -B scripts/validate_hoa_order1.py --binary target/debug/apac-tool --report reports/hoa-order1-math.json
python3 -B scripts/validate_hoa_order1.py --binary target/release/apac-tool \
  --reference-report reports/hoa-order1-math.json --report reports/hoa-order1-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_order1
```

`validate_hoa_order1_native.py` 复核四份短原生捕获，`validate_hoa_order1_checks.py` 检查受影响接口与七个旧代表（使用对应平台的旧元数据参考）。一阶码表完整检查及逐位截断属于低成本验证；完整数学仅使用新增短序列，不重跑旧完整矩阵或媒体库。

### 可变 salient 数量

既有一至三阶 SQ 恢复域允许实际数量 S 的 salient 分量；固定一／二／三阶分别最多 4／9／16 个，混合路径仍有四个 ambient，要求核心数量不超过实际传输通道。动态九槽→十六系数最多九个 salient，既有纯 salient、覆盖／叠加 mixed、分量阶数、空间划分及子带组合适用。ambient 数量和纯二阶路径见下文扩展。

`HoaFrameContext::salient_component_configurations() -> &[SalientComponentConfiguration]` 返回实际长度的只读配置；每项包含描述 `order`、`coefficient_count` 和 `subband_count`，与整体恢复／输出维度分开，无 salient 时为空切片。旧 `salient_subband_counts()` 和 `salient_component_orders()` 保留 `Option<[usize; 5]>`／`Option<[u8; 5]>`；仅五分量时返回数组，其他数量返回 `None`，不能据此判断是否含 salient。内部恢复及 PCM 元数据始终使用实际长度配置。数量不同于五时，空间报告新增 `component_count`、`count_profile=apac-hoa-salient-counts-v1`，记录全部分量的实际阶数；历史、描述和传输映射不补成五项。PCM 元数据新增 `hoa_salient_component_count`、`hoa_salient_count_profile`；恢复／状态规则为 `apac-hoa-salient-counts-math-v1`／`apac-hoa-salient-counts-state-v1`，后端为 `rust_hoa_salient_counts_sq_drc_off_f64_fft_v1`。动态路径保留动态顶层数学标识，记录新的基础恢复规则。原五分量配置的报告、标识和 PCM 保持兼容。

```sh
python3 -B scripts/generate_hoa_salient_counts_manifest.py --check
python3 -B scripts/validate_hoa_salient_counts.py --binary target/debug/apac-tool --report reports/hoa-counts-math.json
python3 -B scripts/validate_hoa_salient_counts.py --binary target/release/apac-tool \
  --reference-report reports/hoa-counts-math.json --report reports/hoa-counts-release.json
```

### 可变 ambient 数量（SCE 传输）

一至三阶固定恢复域支持 1 至实际系数数的 ambient，包括纯二阶 ambient 及只恢复所选系数的纯 ambient；与 salient 混合时仍要求核心总数不超过传输容量。九槽→十六系数动态路径沿用内部槽位选择。覆盖描述省略实际 ambient 选择与描述范围的交集；叠加路径保留全部描述。四路变换只作用于前四个 ambient，其余保持恒等；不足四个时使用无变换语法，不读取帧内变换索引。未选中且无 salient 贡献的输出为零，未使用载波仍完整校验。

新 ambient 数量配置使用 `apac-hoa-ambient-counts-math-v1`／`apac-hoa-ambient-counts-state-v1` 和 `rust_hoa_ambient_counts_sq_drc_off_f64_fft_v1`，PCM 元数据记录 `hoa_ambient_component_count` 及 `hoa_ambient_count_profile=apac-hoa-ambient-counts-v1`。原纯一／三阶完整 ambient、零／四 ambient 混合配置的标识和数值保持不变。此处的数量规则同样用于下述已验证传输组合。

```sh
python3 -B scripts/generate_hoa_ambient_counts_manifest.py --check
python3 -B scripts/validate_hoa_ambient_counts.py --binary target/debug/apac-tool --report reports/hoa-ambient-counts-math.json
python3 -B scripts/validate_hoa_ambient_counts.py --binary target/release/apac-tool \
  --reference-report reports/hoa-ambient-counts-math.json --report reports/hoa-ambient-counts-release.json
```

### 6–9 位 salient 描述量化

上述一至三阶描述支持 cookie 声明的 6／7／8／9 位精度，适用于现有数量、阶数组合、覆盖／叠加和动态选择。码表及矩阵由描述维度、精度和模式共同选择；不截断大于 255 的符号。标量恢复为 `q / 2^(bits-1) - 1`，差分使用同一尺度的带符号增量，方向及矩阵沿用已定义的 Float64 运算顺序。

Rust `SalientDescriptor.quantized` 改为 `Vec<u16>`，新增 `HoaFrameContext::quantization_bits()`。七至九位配置在空间报告中记录 `quantization_bits` 与 `quantization_profile=apac-hoa-salient-quantization-v1`，逐分量信息携带实际字典摘要；PCM 记录 `hoa_salient_quantization_bits`／`hoa_salient_quantization_profile`。新恢复／状态标识为 `apac-hoa-salient-quantization-math-v1`／`apac-hoa-salient-quantization-state-v1`，后端为 `rust_hoa_salient_quantization_sq_drc_off_f64_fft_v1`。六位配置的 JSON 整数含义、常量、规则和 PCM 保持不变。

```sh
python3 -B scripts/generate_hoa_quantization_manifest.py --check
python3 -B scripts/validate_hoa_quantization.py --binary target/debug/apac-tool --report reports/hoa-quantization-math.json
python3 -B scripts/validate_hoa_quantization.py --binary target/release/apac-tool \
  --reference-report reports/hoa-quantization-math.json --report reports/hoa-quantization-release.json
```

### 零至十阶完整系数域

固定配置扩展到 1／4／9／16／25／36／49／64／81／100／121 个系数。零阶支持纯 ambient；salient 描述支持一至十阶、6–9 位量化，数量受实际系数及传输容量约束。profile 5 的 level 0／1／2 分别允许最多 16／36／49 个输出通道；profile 0、level 0 允许最多 121 个。较高 level 可以承载较小配置，超出表中限制明确拒绝。

高阶方向采用独立关联勒让德递推、既有角度常量及 100／200 位一致舍入的归一化常量；一至三阶保留原运算顺序。高阶恢复使用 Float64 补偿求和，最后一次舍入为 Float32，保留强相消的小残差。内嵌容量按已核实的 ASP 规则取实际输出通道数乘 2048 字节，另受线上长度编码和普通包限额约束。

新增配置使用 `apac-hoa-expanded-orders-v1`、`apac-hoa-expanded-orders-math-v1`／`apac-hoa-expanded-orders-state-v1` 及 `rust_hoa_expanded_orders_sq_drc_off_f64_fft_v1`；PCM 记录 `hoa_expanded_orders_profile`、`hoa_expanded_math_sha256`，非默认 profile／level 另记录实际值。上下文新增 `profile_id()`、`level_id()`。包目录、CAF、MP4 均按 cookie 的实际 HOA 布局核对，零阶不冒充普通单声道。显式维度扩展见下文；零阶 salient 仍未开放，动态维度范围保持前述限制。

```sh
python3 -B scripts/generate_hoa_higher_order_math.py --check
python3 -B scripts/generate_hoa_expanded_orders_manifest.py --check
python3 -B scripts/validate_hoa_expanded_orders.py --binary target/debug/apac-tool --report reports/hoa-expanded-orders-math.json
python3 -B scripts/validate_hoa_expanded_orders.py --binary target/release/apac-tool \
  --reference-report reports/hoa-expanded-orders-math.json --report reports/hoa-expanded-orders-release.json
```

### HOA 字典存储

一至十阶的 40 份字典按阶数共用 `data/hoa-salient-orderN-shared-v1.json` 中的四个矩阵和三个唯一系数分组。字典文件使用存储 schema 3，`shared_file` 引用存储 schema 2 的共享文件。码表采用 `preorder-tree-msb-hex-v1`，矩阵采用 `micro21-msb-hex-v1`；两者均以小写十六进制存储。Python 的 `hoa_salient_format.format_for(order, quantization_bits)` 返回兼容旧 schema 的完整字典，仍可载入历史完整／共享字典；Rust 在构建时展开并共享常量。`tables_sha256` 始终覆盖展开后的原始表内容，已有格式标识、报告和 PCM 摘要保持不变。

码表按原二叉树先序存储：一位区分内部节点和叶子，叶子随后携带与量化位数等宽的符号索引，左右路径恢复原始码长和码字。矩阵每项使用一位符号和二十位整数幅值，幅值除以一百万后舍入至 Float32，再恢复符号位，包括负零。生成器逐项检查原始 Float32 位模式；不能精确表示的数值会报错。两种编码均按高位优先排列，末字节补零；加载器校验长度、填充位以及完整树的深度和符号唯一性。

三阶六位和七位量化的码表均使用黑盒测量结果。每种精度包含 mode 1／book 0、mode 2／book 0–1、mode 3／book 0 和 mode 4／cluster 0–3，共八张表；六位每表 64 项，七位每表 128 项。对应原表为 `data/hoa-salient-order3-qP-mode1-measured-v1.json`、`data/hoa-salient-order3-qP-mode2-bookN-measured-v1.json`（`N=0–1`）、`data/hoa-salient-order3-qP-mode3-measured-v1.json` 和 `data/hoa-salient-order3-qP-mode4-clusterN-measured-v1.json`（`N=0–3`），其中 `P=6` 或 `7`。构建直接读取原表，并检查精度、完整符号范围、映射摘要和打包副本；其他阶数及八位、九位码表沿用已有来源。

三阶 mode 4／cluster 0–3 的四张 16×16 矩阵使用 `data/hoa-salient-order3-mode4-clusterN-matrix-measured-v1.json`（`N=0–3`）中通过精度验收的各 256 个 Float32 位模式。这四张矩阵由三阶 6–9 位量化字典共享，构建直接读取测量原表，并校验共享文件中的打包副本。矩阵测量采用六位输入描述；七位码表恢复复用了这些矩阵并通过七位正常码流验证，保留原矩阵来源。八位和九位码表没有因矩阵共享而取得测量来源。数值及语义摘要不变。

三阶的三个唯一系数分组由六位 mode 2 两张码表和 mode 3 码表原表中的 `coefficient_group` 提供。构建检查分组摘要以及原有共享使用关系。mode 3 测得的完整通道顺序仍按既有结构供 mode 0／1／3／4／5 共用，mode 2 的两个分组分别独立使用；分组继续跨三阶 6–9 位字典共享。七位码表原表只记录分组复用关系，不复制分组数值或替换六位分组来源。

`generate_hoa_salient_measured.py --write` 默认检查并重建两种精度的十六份打包副本和逐码表来源说明；`--quantization-bits 6` 或 `7` 可限定一种精度。`--candidate FILE` 可从摘要固定的本地冻结码表候选重建测量原表，可重复提供不同目标的候选。生成器按精度、mode、book 区分来源，拒绝跨精度混用或把七位复用分组当作新分组来源。未提供候选的码表使用仓库内原表，矩阵候选会被拒绝。构建和普通校验无需苹果组件或本地测量记录。

`generate_hoa_salient_measured_matrix.py --write` 单独重建四张已测矩阵的打包副本，并更新四套字典的逐矩阵来源说明；`--candidate FILE` 可重复提供不同 cluster 的候选，只接受摘要固定且通过精度验收的产物。cluster 0 保留原加权复核来源，cluster 1–3 使用批处理工具冻结的来源。

`generate_hoa_salient_measured_groups.py --write` 从已经导出的 mode 2／3 测量原表重建三个共享分组，并更新四套字典的分组来源。三个生成器保留彼此的来源记录；其他阶数的数据不会被替换。

来源元数据固定了测量基线、工具与策略指纹，以及候选、验证和最终对照摘要。六位 mode 2／3 另外绑定冻结分组；七位 mode 2／3 和 mode 4 记录 `group_reused`／`matrix_reused` 与先验快照摘要，明确复用已测六位几何数据。只有已审核的批次产物能由生成器导入，任意新测量目录不自动获得正式来源资格。实验原始 PCM、输入和本地证据路径不随测量原表发布。

`pack_hoa_salient_formats.py --check` 校验全部表摘要、共享内容及规范存储；省略 `--check` 可从完整或共享字典重新生成去重存储。`verify_hoa_salient_format.py --write` 同时生成字典与所需共享文件，并拒绝覆盖或复用内容不同的共享文件；已测码表和矩阵须与原生观测一致，随后保留测量来源。无需原生组件即可执行存储校验：

```sh
python3 -B scripts/generate_hoa_salient_measured.py --check
python3 -B scripts/generate_hoa_salient_measured_matrix.py --check
python3 -B scripts/generate_hoa_salient_measured_groups.py --check
python3 -B scripts/pack_hoa_salient_formats.py --check
PYTHONPATH=scripts python3 -B -m unittest test_hoa_salient_format test_hoa_salient_measured test_hoa_salient_measured_matrix test_hoa_salient_measured_groups
```

### 显式 HOA 系数域

`full_order=false` 接受固定的 1–121 个实际系数，包括非平方数及显式编码的平方数。纯 ambient 可为单系数；salient 每项至少两个系数，所有分量使用实际恢复维度，支持模式 0–3、6–9 位量化、每分量 1–16 子带及已有选择、变换、覆盖／叠加规则。字典取容纳阶数，系数组按实际范围过滤；矩阵和方向描述在参考组件的此配置下被拒绝，不能用完整阶矩阵截取来补齐。固定路径保持内部／输出同维；动态显式域见下文通用动态选择。

`HoaFrameContext::full_order()` 返回线上完整阶标志；`order()` 是容纳实际系数所需的阶数，`recovery_slot_count()`、`channel_count()` 和分量配置分别返回实际维度。非完整平方输出不标注完整 `ambisonic_order`，两系数 HOA 仍按 ASC 类型走 HOA 入口。报告仅为新配置增加 `hoa.full_order=false`，PCM 记录 `hoa_full_order`、实际维度及 `hoa_partial_domain_profile=apac-hoa-partial-domain-v1`；数学／状态为 `apac-hoa-partial-domain-math-v1`／`apac-hoa-partial-domain-state-v1`，后端为 `rust_hoa_partial_domain_sq_drc_off_f64_fft_v1`。已有完整阶的标识与 PCM 保持不变。

```sh
python3 -B scripts/generate_hoa_partial_manifest.py --check
python3 -B scripts/validate_hoa_partial.py --binary target/debug/apac-tool --report reports/hoa-partial-math.json
python3 -B scripts/validate_hoa_partial.py --binary target/release/apac-tool \
  --reference-report reports/hoa-partial-math.json --report reports/hoa-partial-release.json
```

### 空间控制与帧内空间配置

在既有 SQ 系数域内支持 `flag_a` 至 `flag_f` 的已核实组合；ACN/SN3D 的 `parameter_0` 可为 1 或 2，0／3 被绑定参考组件拒绝。未分配 salient 时，未使用的空间划分参数可保留 0–3；有 salient 时仍为方法 0–2。

`flag_a=false` 在 ambient 覆盖／叠加之前加入格式定义的逐系数均值。`flag_e=false` 使方向描述只读取角度、不再读取四个显式系数。`flag_f=false` 保留未取整的频率边界；短窗按频率优先的实际谱线位置选取描述，不能将终点简单除以八。新边界来自经过读写两侧及原生缓存核验的固定整数表；表生成中的 Float32 运算用于确定码流分段，不降低音频恢复／合成的 Float64 精度。

`flag_b=true` 在每个核心帧增加空间配置存在位。当前独立帧（类型 1／2）须重述，类型 0 可沿用 cookie 或前帧配置；更新只改变活动 salient／ambient 数量、选择及 `flag_c=true` 时的分量阶数／子带数。活动数量受 cookie 的最大 salient、恢复维度和传输容量约束，子带数量受 cookie 的最大子带数约束。历史按 cookie 分配的固定分量／子带步长保存；未使用子带、停用分量及已处理描述的高位填充清零。内嵌帧先推进，配置、历史、DRC 和 overlap 仍按外层包原子提交。这与尚未实现的外层 ASP 配置替换是不同载荷。

`HoaFrameContext::spatial_controls()` 返回原始控制值；逐帧报告新增可选 `hoa.spatial.controls`／`frame_configuration`，记录活动配置和位范围。未取整短窗不提供虚构的公共 `lines_per_window`；报告标记 `unrounded_subbands` 并保留长窗终点，按实际频率优先位置解释。PCM 绑定 `apac-hoa-spatial-controls-v1`、格式摘要及控制值，普通控制使用 `apac-hoa-spatial-controls-math-v1`／`apac-hoa-spatial-controls-state-v1`；帧内配置使用 v2 数学／状态及 `rust_hoa_spatial_controls_sq_drc_off_f64_fft_v2` 后端，以遵循编码侧固定历史布局并避免原生读取侧的可变步长与残留历史缺陷。已有默认配置的字段、标识和 PCM 不变。

```sh
python3 -B scripts/generate_hoa_controls_manifest.py --check
python3 -B scripts/validate_hoa_controls.py --binary target/debug/apac-tool --report reports/hoa-controls-math.json
python3 -B scripts/validate_hoa_controls.py --binary target/release/apac-tool \
  --reference-report reports/hoa-controls-math.json --report reports/hoa-controls-release.json
```

### 实际维度的动态选择

内部恢复域 M 与输出域 N 分别使用实际数量，范围为 1–121，继续受 profile／level、描述及传输容量约束。内部可为完整阶或显式维度，输出可为非平方数；允许既有空间控制、帧内活动配置及 SCE／CPE／LFE／扩展元素组合。

当 M < N 时，每帧读取一个编码方式位和八组映射。列表每组有 M 个 `ceil(log2(N))` 位索引，位图每组有 N 位且恰有 M 个置位；全部组均检查越界、重复及数量，只有配置的有效子带参与恢复，未选择输出为正零。当 M ≥ N 时，不读取编码方式位或映射，直接保留前 N 个内部系数。此前九槽→十六输出的线上数据、标识与 PCM 保持不变。

`DynamicBandMapping::target_acn_indices` 改为实际长度 `Vec<u8>`，JSON 仍为数组。新域的报告记录 `domain_profile`、`configured_subband_count`、`wire_mapping_groups`；无映射时 `encoding` 为 `identity` 或 `prefix`，映射列表和频率表为空，有效映射带数为零。`output_order()` 查询返回容纳实际输出的阶数；非平方输出的 PCM 不伪造完整 `hoa_output_order`，改记 `hoa_output_containing_order` 和实际系数数。

新规则为 `apac-hoa-dynamic-domains-v1`、`apac-hoa-dynamic-domains-math-v1`／`apac-hoa-dynamic-domains-state-v1`，后端为 `rust_hoa_dynamic_domains_sq_drc_off_f64_fft_v1`。映射存储使用实际长度的有界集合。绑定参考组件的固定映射行只有 36 个槽位；更大的扩张域按同一已核实读写规则和独立数学验证，不执行超出该原生存储范围的跟踪，也不将原生存储限制冒充码流位宽限制。

```sh
python3 -B scripts/generate_hoa_dynamic_domains_manifest.py --check
python3 -B scripts/validate_hoa_dynamic_domains.py --binary target/debug/apac-tool --report reports/hoa-dynamic-domains-math.json
python3 -B scripts/validate_hoa_dynamic_domains.py --binary target/release/apac-tool \
  --reference-report reports/hoa-dynamic-domains-math.json --report reports/hoa-dynamic-domains-release.json
```

### HOA 传输组合

CPE 两路各占一个连续载波，LFE 占一个，扩展元素占零个。所有音频载波仍完整读取并数值校验，包括未参与空间恢复的载波。公共两位窗型供全部 HOA ICS 使用，CPE 独立右头不重复读取窗型；SCE／CPE 复用现有 SQ、CAC、TNS、逐元素 BWE2，LFE 不携带 TNS／BWE2，不增加播放增益。

扩展元素的已支持格式位为零，外层长度使用 7／8／16 逃逸，正长度包含自身头，零也表示空载荷。非空载荷先读取一个 8／8／16 逃逸的原始参数，再读取剩余的不透明字节；该参数不决定长度，参考实现不将其用于音频恢复。报告保留实际参数、边界、声明长度和 SHA-256；截断、参数头越界及不支持格式位明确失败。

`HoaFrameContext::transport_elements()` 返回元素配置和载波映射。元素序号及错误中的 `element_index` 使用 `usize`，可准确表示含大量零通道扩展元素的列表；JSON 仍使用整数。新组合增加可选 `hoa.transport_profile`／`transport_format_sha256`／`transport_element_count` 和 PCM 格式摘要、维度／映射元数据，标识为 `apac-hoa-transports-v1`、`apac-hoa-transports-state-v1` 与 `rust_hoa_transports_sq_cac_tns_bwe2_drc_off_f64_fft_v1`；恢复数学沿用原模型，原全 SCE 配置的标识和输出不变。

恢复后的每个输出系数都按公共窗型合成。已确认的原生实现缺陷包括短窗逆重排只遍历传输元素、以及从首元素（可能是扩展元素）取得合成窗型；这些情况下独立输出有意遵循公共窗语义及数学参考。原生错排行为单独重建、核对和报告，不进入生产恢复公式，也不通过放宽浮点容差处理。

```sh
python3 -B scripts/generate_hoa_transports_manifest.py --check
python3 -B scripts/validate_hoa_transports.py --binary target/debug/apac-tool --report reports/hoa-transports-math.json
python3 -B scripts/validate_hoa_transports.py --binary target/release/apac-tool \
  --reference-report reports/hoa-transports-math.json --report reports/hoa-transports-release.json
APAC_TOOL_BINARY=target/debug/apac-tool PYTHONPATH=scripts python3 -B -m unittest test_hoa_transports
```

`validate_hoa_transports_native.py --captures CAPTURE_MANIFEST --binary BINARY --report REPORT` 复核命名捕获清单；清单的路径相对自身目录。`validate_hoa_transports_checks.py` 仅检查新增用例、受影响 HOA 代表及一份旧离散声道序列，旧元数据参考必须来自相同平台。
