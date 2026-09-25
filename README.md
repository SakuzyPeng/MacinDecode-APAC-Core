# MacinDecode APAC Research Tools

`apac-tool` 是苹果 APAC（Apple Positional Audio Codec）的研究工具集，提供样本索引、配置采集与纯 Rust 配置／帧前缀及 SQ 频谱解析、数据包导出、独立包目录回放、苹果参考编解码、测试信号及 PCM 比较。

参考音频编解码由 **macOS AudioToolbox** 完成。`decode-sq` 新增实验性的纯 Rust 受限 SQ PCM 后端，采用独立公式定义的固定数值模型，苹果参考输出的数值差异另行统计；`compare`、`parse-cookie`、`parse-packets` 和 `decode-sq` 的实现不调用苹果音频接口。通用命令可在 Windows／Linux 构建和运行；是否达到跨平台逐位一致，以对应提交的完整运行报告为准，跨目标编译不能代替运行验收。

## 构建

数值验收使用 Rust 1.98.0；macOS 的参考工具还需要 Xcode Command Line Tools。Windows／Linux 的纯 Rust 命令不需要苹果 SDK。

```sh
cargo build --offline
target/debug/apac-tool --help
```

当前机器所需依赖已缓存，可以离线构建。新机器首次构建可使用 `cargo build`。项目关闭开发构建的调试符号和增量编译，所有构建产物使用当前目录的 `target/`。

## 命令

输出结果写 stdout，进度和错误写 stderr。除索引文件外，导出命令的 `--out` 必须指定一个**不存在的新目录**；工具不覆盖现有结果。

```sh
APAC_SAMPLE='/path/to/APAC/sample.caf'
APAC_CORPUS='/path/to/APAC'

# 信息查询：格式、声道布局、配置哈希、有效帧与 priming/padding
target/debug/apac-tool inspect "$APAC_SAMPLE"

# 递归索引，只读元数据；不复制源音频，不全曲解码
target/debug/apac-tool scan "$APAC_CORPUS" --output artifacts/demo/corpus.jsonl

# 按配置哈希去重采集，校验配置仍与索引一致
target/debug/apac-tool collect-configs artifacts/demo/corpus.jsonl --out artifacts/demo/configs

# 从集合的 index.json 选择 cookie_file，替换以下 CONFIG_SHA256
target/debug/apac-tool parse-cookie 'artifacts/demo/configs/cookies/CONFIG_SHA256.bin'

# 默认导出前 150 包；范围可显式调整
target/debug/apac-tool dump "$APAC_SAMPLE" --out artifacts/demo/packets
target/debug/apac-tool dump "$APAC_SAMPLE" --out artifacts/demo/packets-75 --start-packet 75 --packets 32

# 包目录回放：补齐前置依赖后，回放过程不访问原始音频
target/debug/apac-tool dump "$APAC_SAMPLE" --out artifacts/demo/replay-packets --start-packet 75 --packets 32 --with-preroll
target/debug/apac-tool replay artifacts/demo/replay-packets --out artifacts/demo/replayed --frames 8192

# 逐包解析默认双声道 SQ 帧头，报告仍保留未解析的音频载荷
target/debug/apac-tool parse-packets artifacts/demo/replay-packets --output artifacts/demo/prefixes.jsonl
target/debug/apac-tool parse-packets artifacts/demo/replay-packets --output artifacts/demo/prefixes-75.jsonl --start-packet 75 --packets 8
# 继续读取基础 SQ 频谱，输出量化整数和 CAC/TNS 之前的缩放后频谱
target/debug/apac-tool parse-packets artifacts/demo/replay-packets --depth spectrum --output artifacts/demo/spectra.jsonl
# 继续读取共享头右流与 CAC，输出 TNS 之前的左右频谱
target/debug/apac-tool parse-packets artifacts/demo/replay-packets --depth cac --output artifacts/demo/cac.jsonl

# 默认从有效音频起点解码 8192 帧；帧是所有声道共享的采样时刻
target/debug/apac-tool decode "$APAC_SAMPLE" --out artifacts/demo/start
target/debug/apac-tool decode "$APAC_SAMPLE" --out artifacts/demo/one-second --start-frame 48000 --frames 48000

# 默认生成 48 kHz、双声道、两秒的六种测试信号，编码为 APAC 并生成参考 PCM
target/debug/apac-tool fixture --out artifacts/demo/fixtures

# 多声道或 HOA 的逐声道测试
target/debug/apac-tool fixture --out artifacts/demo/hoa --layout hoa3 --signals channel-solo

# 显式关闭编码端 DRC，生成短小的配置对照
target/debug/apac-tool fixture --out artifacts/demo/drc-none --signals sine --duration 0.125 --drc-configuration none

# 比较元数据所指向的 Float32 PCM
target/debug/apac-tool compare artifacts/demo/start/pcm.json artifacts/demo/start/pcm.json
```

`fixture` 的布局选项为 `mono`、`stereo`、`surround71`、`surround714`、`hoa1`、`hoa2`、`hoa3`、`surround222`。三个 HOA 预设分别是 4、9、16 声道的一、二、三阶 ACN/SN3D。信号选项为 `silence`、`impulse`、`sine`、`sweep`、`noise`、`channel-solo`，可用逗号组合。还支持 `--sample-rate`、`--duration`、`--seed`、`--bitrate`、`--quality 0..127` 和 `--drc-configuration none|music|speech|movie|capture`。省略 DRC 参数保留系统默认行为；显式设置失败时返回操作名称及原始 `OSStatus`。

所有导出命令默认限制累计输出为 128 MiB，包含二进制数据和元数据。需要更大导出时显式添加 `--max-output-mib 256`。读取和写入采用小块缓冲；编码器的原生文件写入回调也受此上限约束。为了给元数据留出空间，导出可能在达到限额之前拒绝请求。

退出码：`0` 表示成功、比较通过或结构解析完整；`1` 表示运行、输入或完整性错误；`2` 表示 PCM 超出容差、索引/配置采集存在未解决错误，或配置／帧解析为 `partial` / `unsupported`。命令行语法错误也由 clap 返回 `2`。`parse-packets` 达到前缀目标后通常仍返回 `2`，因为整包载荷没有解析。

## 导出数据

所有 JSON/JSONL 记录采用 `schema_version: 1`。系统可选属性统一写为 `{"value": ..., "error": null}`；不支持或读取失败时 `value` 为 `null`，`error` 保留操作名和原始 `OSStatus`。

**`inspect` / `scan`**：保存容器、ASBD 格式字段、数值声道布局与可读名称、包数、packet table、magic cookie 的字节数和 SHA-256。索引按格式、布局和配置哈希分组，不将哈希组称为已经识别的 profile。索引不遍历符号链接，跳过数量写入汇总；坏文件写入错误记录后继续处理其他文件。

**`collect-configs`**：接受现有 schema v1 的扫描 JSONL，按 cookie SHA-256 去重。如果索引仍带有 `<manifest>.incomplete` 标记，则在读取索引和创建输出目录之前拒绝采集，退出码为 `1`。每组优先读取体积较小的来源；失败时记录原因并尝试同组其他来源，原始来源映射保留在 `index.json`。配置写为 `cookies/<sha256>.bin`。实际字节数或哈希与索引不符时记录错误，不以新配置悄然替代旧配置。`complete` 表示采集流程正常结束，`all_collected` 才表示所有配置组和输入记录均成功；来源路径使该目录仍属于本地研究数据。

**`parse-cookie`**：接受完整的独立 `dapa` cookie，输入上限 8 MiB。当前支持版本字段 `0x0800` 的部分 channel/lbr、HOA ACN/SN3D 配置结构、响度／DRC 配置、场景及来源扩展；不按文件长度或哈希识别格式。输出：

- `status`：`complete`、`partial` 或 `unsupported`。
- `fields`：已确认的线上字段和值，`bit_offset` 从整个 cookie 的起点计数，`bit_length` 是实际占用的位数，位序为 MSB-first。
- `derived`：从配置字段计算的采样率、声道数、帧长度、布局等，不读取旁路容器元数据作为解析值。
- `unknown_ranges`：未解析的位范围及原始十六进制字节。`raw_hex` 从包含起始位的字节开始，首字节应跳过 `first_byte_skip_bits` 个高位。
- `diagnostics`：停止原因和位置。损坏或截断输入的错误另含 `bit_offset`。

`complete` 严格表示当前实现已覆盖整份输入的**语法结构**，包括有证据的填充位；不意味着实现了音频解码、所有配置的语义合法性检查或空间渲染。少数字段暂用 `parameter_*`、`flag_*`、`content_origin.values` 等中性名称保留数值，没有为未确认的操作含义命名。未知分支立即停止并保留剩余数据；非零且尚未核实的填充返回 `partial`。

本机 **64/64 份不同配置均完整覆盖语法**。第四阶段补齐了最后两份 HOA ASC 类型 2 配置，并验证了可重新生成的一至三阶 ACN/SN3D 样本；这不代表支持所有 APAC 配置分支或音频解码。长度只用于首批验收选样，不参与解析器分派。

DRC 字段位于 `ancillary.loudness_drc.*`，包括系数、增益集合、指令、声道关联、响度及来源记录。`derived` 中的声道增益集合索引从零计数，`-1` 是线上零值转换得到的哨兵。`*_encoded` 保留编码数值，不自动赋予 dB 等物理单位；内部版本 8 来自已确认的 APAC 调用上下文，不伪装成额外读取的版本字段。未支持的下混、依赖指令、特殊 effect、EQ 或扩展分支返回 `partial`。

HOA 字段位于 `components[i].hoa.*`。派生的 `hoa.coefficient_count`、`hoa.core_channels`、`hoa.transport_channels` 分别表示系数、内部编码通道和 TCE 提供的通道数量；`components[i].channels` 来自 cookie 中独立的输出布局。`hoa.order` 表示容纳系数所需的阶数；输出布局的阶数另以 `components[i].ambisonic_order` 表示，仅在布局声道数为完整平方数时提供。ACN/SN3D 标识由序列化的布局标签确认。自定义布局、N3D、未验证的 TCE 类型，以及内部／输出通道数不同的重映射分支保留为 `partial`。

**`dump`**：

- `cookie.bin`：AudioToolbox 返回的原始配置字节，不去除 `dapa` 等外层结构。
- `packets.bin`：按包顺序串联的原始载荷。
- `packets.jsonl`：源包序号、导出文件内的字节偏移、大小、帧数、哈希、原始帧位置和依赖信息。
- `manifest.json`：来源、请求范围、实际范围、总字节数和 SHA-256。

包序号从 0 开始，`export_offset` 只指向导出的 `packets.bin`。`raw_frame_position` 是尚未扣除 priming 的包时间线；不能直接当成 `decode --start-frame` 的有效音频位置。

依赖字段直接保留苹果 API 的含义：`PacketToDependencyInfo` 的 preroll 是从该独立包起解码后，为刷新状态需要继续解码的包数；`PacketToRollDistance` 是目标包之前需要解码的包数。**`independently_decodable=true` 不代表不需要 preroll。** 默认 `dump` 仍只导出原请求范围；指定 `--with-preroll` 后会选择满足这两种约束的独立起点，最多向前 4096 包。所需属性缺失、距离越界或查询没有进展时明确失败。

preroll 导出的 `start_packet`、`actual_packets` 描述实际存储范围，`requested_packets` 仍是用户请求的目标包数，因此实际包数可能更大。新增 `replay_window` 保存 `requested_start_packet`、`requested_packets`、`actual_target_packets`、`included_preroll_packets` 和 `target_raw_start/end`。这些原始帧边界不含有效音频裁剪。

**`replay`**：接受包含 `manifest.json`、cookie、包文件和索引的完整目录，使用独立 AudioConverter 输出 `pcm.f32le` / `pcm.json` 及诊断文件 `replay.json`。兼容旧的从包 0 开始的原始导出；旧的非零起点导出需要重新添加 `--with-preroll`。`file.source` 仅保留来源标识，不打开它。

`--start-frame` 使用与 `decode` 相同的有效音频坐标；省略时从导出目标窗口的首个有效帧开始。`--frames` 默认 8192，输出在窗口或源文件的有效音频末尾裁剪；`range.clipped_by` 区分 `window_end` 与 `source_eof`。起点超出窗口、零请求帧数或整数溢出报错。采样率、声道数和布局保持源声明，PCM 仍是交错小端 Float32。

`--input-batch-packets` 默认为 1，范围 1–64，每批数据最多 16 MiB。`replay.json` 分开记录已供给包数／帧数、实际产生的原始 PCM 帧数、前后丢弃帧数及保存帧数；供给数据可能因解码器预读多于已产生的 PCM。请求到达窗口末尾时发送 EOF 并排空；较短请求取得所需范围后停止解码，继续完成剩余文件的完整性检查。

时间换算使用索引的包帧位置与 packet table 的 priming/valid/remainder，不硬编码延迟。`converter_prime_info` 与 `converter_prime_method` 单独记录查询结果；属性不支持时保留原始 `OSStatus`，不将其当作零，也不依靠它自动裁剪容器 priming。默认不修改系统处理设置、不重采样、不下混、不归一化。

包目录校验为纯 Rust：检查完成标记、schema、包序号、连续字节／帧范围、数据大小、逐包及整体哈希、cookie 与已知格式字段的一致性。拒绝目录外引用，包括越界符号链接。manifest 上限 1 MiB、索引行上限 64 KiB、cookie 上限 8 MiB、单包上限 16 MiB。配置解析为 partial/unsupported 并不直接阻止苹果回放；已确认字段冲突或损坏输入仍会被拒绝。

cookie 已确认的 `frame_samples` 用于核对逐包帧数、绝对帧位置和 packet table 总帧数，即使容器的 `frames_per_packet=0` 也执行这些检查；容器声明的非零帧长必须与 cookie 一致。

通用库入口 `packets::PacketBundle::open` 完成初始校验，`range` 计算帧窗口，`next_batch` 提供包数据与相对批次偏移。读取部分数据的调用方应在信任结果前调用 `verify_remaining`，完成第二次流式完整性检查；CLI 已自动执行。

**`parse-packets`**：读取与 `replay` 相同的完整包目录，不访问 `file.source`，逐包写入指定 JSONL，并在 stdout 输出汇总。默认从实际存储的首包开始，包含补入的 preroll，最多 150 包；`--start-packet` 使用原文件包序号，遇目录末尾裁剪。零数量、起点越界、损坏配置、哈希或时间线冲突返回错误。

当前目标为版本 `0x0800`、44.1/48 kHz、1024 帧、双声道、单个 channel ASC 和单个 CPE，配置中的 `lbr_flag` 与公共组件参数为零。解析 ASP 包装、元素存在位、SQ 分派、左声道 ICS 的窗口类型、最大频带数及短窗分组，到左声道流载荷入口停止。ASP 类型 2 的内嵌 preroll 按已确认的字节长度界定，原始载荷保留为未知范围；它不同于 `dump --with-preroll` 补入的前置包。

报告的 `fields` 位偏移从当前包 bit 0 起算。`prefix_complete` 表示到达 SQ 载荷入口或 CPE 缺席终点；`status` 仍描述整包语法。`payload_bit_offset` 只标出已确认的载荷起点，`component_end_bit_offset` 保持 `null`，未知范围不复制原始载荷。只要仍有未解析内容，就不能称为整帧 complete。汇总中的 `complete` 仅表示报告已成功写完并通过包目录校验。

LRVQ 当前保留为 **TODO**：读出 `coding_type=1` 后，以 `lrvq_prefix_deferred` 停止，`prefix_complete=false`，保留剩余位。当前系统的默认双声道编码路径未启用该工具；这不意味着其他编码设置、系统版本或已有媒体不会使用它。未知 ASP 类型、重配置或未验证的保留位同样明确停止。

库入口为 `frame::FrameContext::from_cookie(&[u8])` 和 `frame::parse_frame(&FrameContext, &[u8])`。上下文只使用 cookie 中已确认的字段；`is_supported()` 表示配置适合尝试当前前缀，不保证每包分支均已实现。`packets::PacketBundle::next_packet` 提供经过校验的原始包记录及字节，保留既有批次回放接口。

单包语法错误记录包序号和包内位位置，继续保留其他包的结果，退出 `1` 并留下 `<REPORT.jsonl>.incomplete`。I/O 或目录完整性失败立即停止并保留已有的不完整输出；正常的 partial/unsupported 结果不会留下失败标记。报告结束前还会校验未选中的包。沿用 128 MiB 输出限额及拒绝覆盖机制；已验证的双声道 ASP 内嵌 preroll 上限为 4096 字节。

**SQ 频谱深度**：`parse-packets --depth spectrum` 沿相同配置范围继续解析 section、缩放因子、codebook 0–11、符号和逃逸。先完成左声道；独立右声道头分支完成右声道；共享头分支在标志之后以 `shared_ics_cac_deferred` 停止，保留左声道结果。两路完成后以 `sq_spectra_before_tools` 停止，尚未读取 TNS、ancillary 或组件尾部。ASP 内嵌 preroll 仍只按长度跳过，不输出其频谱。

`report` 保留原有字段并增加 `spectrum_complete`、`spectral_stage=scaled_before_cac_tns` 和 `channels`。每声道记录 ICS、global gain、section、按组／频带排列的 `scale_factors`（零码本为 null）、1024 个 `quantized` 整数和 1024 个 `scaled` Float32 值，以及声道流起点、频谱码字起点和终点。短窗数组依次为八个 128 点窗口；长窗为一个 1024 点窗口。频带外的零值由语法确定，截断输入不会补零。

`spectrum_complete=true` 只表示两路 SQ 流完成。CPE 缺席时 `channels=[]`、`spectrum_complete=false`，汇总单列 `cpe_absent_packets`；LRVQ 和范围外配置同样不伪造频谱。整包状态仍为 partial，通常退出 `2`。`prefix_complete` 保留原目标含义，`payload_bit_offset` 仍指左声道流起点，`component_end_bit_offset` 保持 null。汇总另列 `left_spectrum_packets`、`right_spectrum_packets` 和 `spectrum_complete_packets`。

库入口 `frame::parse_spectrum(&FrameContext, &[u8]) -> Result<SpectrumReport, config::ParseError>` 提供类型化结果；`SpectrumReport.frame` 是原 `FrameReport`，JSON 序列化时平铺它。`parse_packets_with_depth(..., ParseDepth)` 提供包目录接口，原 `parse_frame`、`parse_packets` 及 CLI 默认 `--depth prefix` 保持原有行为。

缩放因子差分在所有组间连续累加，支持 `-256..255`；超界明确报错，不复现苹果的饱和恢复。逃逸幅度限制为已验证的 `16..8191`。反量化 `|q|^(4/3)` 和缩放 `2^((sf-100)/4)` 分别按最近值、平局取偶舍入为 Float32，再作 Float32 乘法。全部幅度与缩放因子的 IEEE 位模式由高精度公式离线生成，运行时不使用系统 `powf`。频谱报告新增可选 `numeric_profile`，新输出为 `apac-sq-math-v1`；旧报告缺失该字段时仍可读取。频谱尚未施加 CAC、TNS、DRC 或合成变换，不能直接解释为可播放 PCM。

码字、码长及频带常量的来源和许可见 [THIRD_PARTY.md](THIRD_PARTY.md)；Rust 的解码表结构与 APAC 读取器为独立实现，运行和构建无需系统二进制或本地研究目录。

**CAC 深度**：`parse-packets --depth cac` 及 `frame::parse_cac(&FrameContext, &[u8]) -> Result<CacReport, config::ParseError>` 完成共享 ICS 下的右声道流与 CAC。原 `prefix`／`spectrum` 行为保持不变；`spectrum` 仍在共享头标志之后保留左流并停止。

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

**TNS 深度**：`parse-packets --depth tns` 及 `frame::parse_tns(&FrameContext, &[u8]) -> Result<TnsReport, config::ParseError>` 依次读取完整左 TNS、完整右 TNS，停在 BWE2 入口。原 `prefix`／`spectrum`／`cac` 深度的默认值、结果与停止位置保持不变。

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

**BWE2 深度**：`parse-packets --depth bwe2` 及 `frame::parse_bwe2(&FrameContext, &[u8]) -> Result<Bwe2Report, config::ParseError>` 完成 TNS 后的带宽扩展，在核心对齐之前停止。旧深度不消费新增参数。`Bwe2Report.tns` 保留先前报告并在 JSON 中平铺；新增 `bwe2` 的控制位、有效启用状态、参数来源、两个 LSF 索引、按组增益及起止位。`channels_after_bwe2` 保存输出、复制区间、是否实际处理及可用的 LPC／LSF 诊断量，标识为 `bwe2_stage=scaled_after_bwe2_before_synthesis`。`bwe2_complete` 和汇总 `bwe2_complete_packets` 不替代整包完成状态，组件终点仍未知。

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
cargo test --release --lib bwe2_math::tests::optimized_768_is_at_least_twice_as_fast_as_direct_dft -- --exact --ignored --nocapture
```

BWE2 参考采用 Decimal 直接 DFT、独立 Toeplitz 求解和直接多项式求值，不复用生产 FFT、Levinson、LSF 因子求值或三角近似。数学容差仍为 `atol=1e-6, rtol=1e-5`；跨构建另要求所有阶段摘要逐位相同。`data/bwe2-vectors-v2.json` 在原矩阵之外加入 96 个频谱用例和 96 个 PCM 序列，覆盖易发生相消的内部 LSF 索引组合、长短窗、两档复制范围、左右声道和分数步长增益。原生采用明确的分层验收：参数／边界精确，使用相同原生 LPC 输入后的变换、复制和增益控制通过原容差；完整原生路径与输入隔离路径的浮点差异均另行保留，不把苹果 Float32/FMA 的 LPC 舍入接入默认模型。

所有验收入口显式接收二进制和报告路径，拒绝覆盖、缺失用例、指纹变化和执行中源码／二进制变化；便携测试不依赖 docs/local。旧 TNS 矩阵可用 `validate_tns.py --regression-report reports/previous-tns-math.json` 重新执行并对照原摘要，语义与 SQ／CAC 的显式跨版本回归一致。BWE2 元数据另记录 `bwe2_numeric_profile`、格式字典与数学常量摘要。配置、源包起点、DRC、ASP preroll、缺席 CPE、LRVQ 和多声道限制继续保留。

**实验性 `decode-sq`**：从自包含包目录输出独立 PCM：

```sh
target/debug/apac-tool decode-sq artifacts/demo/independent-sq-packets --out artifacts/demo/rust-pcm
```

一个可重建的独立声道头人工包目录可这样生成（目的目录须不存在）：

```sh
python3 -B - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from spectrum_vectors import frame, bundle
path = Path("artifacts/demo/independent-sq-packets")
path.parent.mkdir(parents=True, exist_ok=True)
cases = [{}, {"gain": 200, "left": {0: (1, [1, 0, 0, 0], 200)}}, {}, {}]
bundle(path, [frame(case)[0] for case in cases])
PY
```

当前用于受限人工 SQ 序列，**不是默认双声道媒体的通用解码入口**。配置须完整、44.1/48 kHz、1024 帧、单 ASC／CPE、双声道，且 profile=31、level=0、公共 parameter_b=2、立体声布局 family=101，无 remapping、ancillary 或配置扩展。包支持 ASP 类型 0/1、CPE 存在、SQ、独立或共享声道头及已验证 CAC／TNS／BWE2；LRVQ、缺席 CPE、内嵌 preroll 和重配置明确报错。读取核心对齐、关闭的 trimming 标志及末字节零填充，额外尾部不被默默忽略。

输出为比较器可读的 `pcm.f32le`、`pcm.json` 和 `decode-sq.json`。从原文件包 0 开始建立窗口状态，使用 packet table 裁掉 priming/remainder；不访问原始音频，不凭空增加刷新包或尾部帧。只允许长窗／long-start 开始，随后验证 long/start/short/stop 的过渡。错误保留输出目录失败标记，拒绝覆盖并沿用累计输出限额。

库入口为 `synthesis::SqDecoder::from_cookie`、`decode_frame` 和 `reset`；每包产生 1024 个交错双声道 Float32 帧。出错不推进解码状态。`synthesis::decode_sq` 提供包目录导出。实现使用 Float64 调制、正弦窗、叠加状态和自行实现的 radix-2 DIT FFT；乘法与加法分别舍入，仅最终 PCM 转为 Float32，浮点零统一为正零。没有 FFT 库依赖或运行时原生解码回退。

默认数值配置为 `apac-sq-math-v1`，后端为 `rust_sq_cac_tns_bwe2_f64_fft_v7`，另记录 `cac_numeric_profile=apac-cac-math-v1` 、`tns_numeric_profile=apac-tns-math-v1` 、`bwe2_numeric_profile=apac-bwe2-math-v2` 与各工具的常量／格式摘要。保留 `experimental=true`，`numerical_qualification=independent_math_reference`；`complete` 只表示导出完整。PCM 元数据记录数值配置、常量摘要、实际编译器及 debug assertions 设置。

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

**`decode`**：生成 `pcm.f32le` 和 `pcm.json`。PCM 是交错、小端 32 位浮点，保持输入采样率、声道数和布局。HOA 保留 ACN 顺序、SN3D/N3D 归一化和可确定的阶数。`start_frame=0` 指系统已经处理 priming 后的有效音频起点；请求到达文件尾部时实际帧数可少于请求帧数，超出尾部的起点报错。

不执行重采样、下混、归一化或自动增益匹配。参考解码保留系统默认设置，元数据记录能查询到的 `mdrc`、`^pro`、`ptlc`、`pptl`。编码器也记录请求参数和返回的 `brat`、`cdqu`、`cdrc`；这些是原始系统属性值，不能将 `brat=0` 等值解释为文件的实测平均码率。

**`fixture`**：每种信号有一个子目录，内含 `source.f32le` / `source.json`、`encoded.caf`、`reference/pcm.f32le` / `pcm.json` 和 `manifest.json`。源信号在相同实现与运行环境下可重复生成；跨系统或编解码器版本应比较记录的哈希和环境。APAC 是有损编码，编码前的 `source` 不是要求解码结果逐位一致的参考。

每个信号的 `manifest.json` 记录 `requested.drc_configuration`、`actual_encoder_settings.cdrc` 和 `encoded.cookie` 的长度／哈希。`drc_configuration_verified` 为 `true` 表示显式请求与系统回读相符，`false` 表示未能核实，`null` 表示未显式请求。导出成功不等于参数对照有效；验收脚本拒绝将回读不支持或不一致的实验计为成功。系统默认回读可能是 `4294967295`，保留原值，不将它推断为某个模式。

**`compare`**：接受两个 `pcm.json` 或 `source.json`，检查有效格式、采样率、声道、布局、起点、帧数、文件长度及 SHA-256。默认容差为 `abs(reference-candidate) <= 1e-6 + 1e-5 * abs(reference)`，支持 `--atol` / `--rtol`。不自动对齐、补零或调整增益。输出逐声道最大绝对误差、RMS、SNR、超限样本数，以及独立的 `bit_identical` 标记；通过容差不等于逐位一致。

零误差、零参考能量和空音频的 SNR 使用 `null` 加 `snr_kind` 表达，避免非法 JSON 的 Infinity/NaN。音频中的 NaN/Inf 直接报错。若双方布局都未知（属性缺失或系统返回 `Unknown` 标签），仍可按声道索引比较，但结果中的 `layout_verified` 为 false。

未完成的导出目录保留 `.incomplete.json`；未完成的索引保留同名 `.incomplete` 文件。不能把这些结果当成完整参考数据，比较器拒绝带未完成标记的 PCM 包。排查后删除或改用新输出路径再运行。

## 验证

```sh
cargo test --offline
cargo clippy --offline --all-targets -- -D warnings
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
cargo check --offline --target x86_64-unknown-linux-gnu
cargo check --offline --target x86_64-pc-windows-msvc
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

## 实现边界

Rust 处理命令行、数据模型、哈希、生成器和比较器；`native/audio_toolbox.c` 通过 SDK 头文件封装 `AudioFile`、`ExtAudioFile` 和 `AudioConverter`。原生资源由 Rust 所有权封装释放，编码结束时显式检查刷新与文件关闭错误。实现不需要 Xcode workspace 的运行目标，也不依赖 Xcode MCP 授权。

当前工具已建立配置解析、SQ 帧前缀、基础频谱、共享头／CAC、TNS、BWE2 和逐包苹果参考回放基准。独立公式数值模型配有完整人工矩阵和跨平台逐位验收工具，受限 PCM 仍保留实验标识；ASP 内嵌 preroll 状态、DRC 增益、LRVQ 与其他帧载荷解析、空间渲染和实时播放属于后续工作。

## 仓库与数据边界

代码与研究文档使用两个独立的 Git 仓库，各自同步至 private 远端：

- 当前仓库保存 Rust/C 源码、测试、构建配置、验证脚本和本 README。
- `docs/` 保存调查笔记和阶段总结，有自己的 Git 历史；整个目录被当前仓库忽略，不是子模块。只获取代码仓库时不会包含这些本地研究文档。
- `reports/`、`artifacts/`、`target/` 和本地 Xcode workspace 被代码仓库忽略。原始报告可能记录用户路径与音乐库文件名，分享时需另外生成脱敏版本。

| 仓库 | 远端 | 可见性 |
| --- | --- | --- |
| 代码 | [MacinDecode-APAC-Core](https://github.com/SakuzyPeng/MacinDecode-APAC-Core) | Private |
| 研究文档 | [MacinDecode-APAC-Docs](https://github.com/SakuzyPeng/MacinDecode-APAC-Docs) | Private |

从项目根目录使用 `git status` 检查代码，使用 `git -C docs status` 检查文档。研究结论在文档仓库中记录对应的代码提交，分别提交可以保持两套历史清晰。

首次获取项目时分别克隆，文档仓库放入已被代码仓库忽略的 `docs/`：

```sh
git clone https://github.com/SakuzyPeng/MacinDecode-APAC-Core.git
git clone https://github.com/SakuzyPeng/MacinDecode-APAC-Docs.git MacinDecode-APAC-Core/docs
```

两个仓库分别配置 `origin`。从项目根目录运行 `git push` 推送代码，运行 `git -C docs push` 推送文档。
