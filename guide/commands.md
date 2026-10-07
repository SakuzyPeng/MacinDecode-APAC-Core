# 命令行参考

`apac-tool` 各子命令的参数、输出文件、报告字段与退出码。`decode-sq`、`parse-cookie`、`parse-packets` 和 `compare` 在所有平台可用，其他命令需要 macOS AudioToolbox。便携解码见 [decoding.md](decoding.md)，逐包解析深度见 [bitstream.md](bitstream.md)。

返回 [README](../README.md)。

使用下载的 CLI 时，在解压目录的终端中运行 `./apac-tool`；Windows PowerShell 使用 `.\apac-tool.exe`。下文的 `target/debug/apac-tool` 是源码构建路径，使用安装包时替换为对应程序路径即可，PowerShell 中将多行命令写成一行。

常用命令：

| 目的 | 命令 | 平台 |
| --- | --- | --- |
| 导出 WAV／RF64／CAF 文件 | `decode-sq INPUT -o NEW_FILE`，可选 `--format` | Windows／Linux／macOS |
| 导出裸 PCM 和研究报告 | `decode-sq INPUT --out NEW_DIRECTORY` | Windows／Linux／macOS |
| 解析独立配置 | `parse-cookie COOKIE` | Windows／Linux／macOS |
| 查看导出包的语法 | `parse-packets DIRECTORY --output REPORT.jsonl` | Windows／Linux／macOS |
| 比较两份 PCM | `compare REFERENCE/pcm.json CANDIDATE/pcm.json` | Windows／Linux／macOS |
| 查看原生文件信息、导出包和配置 | `inspect`、`scan`、`dump`、`collect-configs` | macOS |
| 使用苹果参考编解码或生成测试信号 | `decode`、`replay`、`fixture` | macOS |

独立解码使用 `decode-sq`，具体参数和 PCM 输出说明见[解码指南](decoding.md)。 CAF 外部布局标签有误时，可添加 `--input-layout 9.1.6` 等预设；它必须与 APAC 配置一致，不能强制重标输出，也不适用于 MP4／M4A。

## 概览

输出结果写 stdout，进度和错误写 stderr。`decode-sq -o FILE` 输出单个音频文件；`--out` 输出研究目录，两者互斥，`--format wav|rf64|caf` 仅与 `-o` 搭配。目标文件或目录必须尚不存在；工具不覆盖现有结果。

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

`fixture` 的布局选项为 `mono`、`stereo`、`surround51`、`surround71`、`surround714`、`surround916`、`hoa1`、`hoa2`、`hoa3`、`surround222`。`surround916` 是 16 声道的 9.1.6 离散扬声器布局；三个 HOA 预设分别是 4、9、16 通道的一、二、三阶 ACN/SN3D。信号选项为 `silence`、`impulse`、`sine`、`sweep`、`noise`、`channel-solo`，可用逗号组合。还支持 `--sample-rate`、`--duration`、`--seed`、`--bitrate`、`--quality 0..127` 和 `--drc-configuration none|music|speech|movie|capture`。省略 DRC 参数保留系统默认行为；显式设置失败时返回操作名称及原始 `OSStatus`。

`decode-sq -o FILE` 默认不限总文件大小，可用 `--max-output-mib` 设置包含容器头的整文件限额。其他导出命令（包括 `decode-sq --out DIRECTORY`）默认限制累计输出为 128 MiB，包含二进制数据和元数据。需要更大导出时显式添加 `--max-output-mib 256`。读取和写入采用小块缓冲；编码器的原生文件写入回调也受此上限约束。为了给元数据留出空间，导出可能在达到限额之前拒绝请求。

退出码：`0` 表示成功、比较通过或结构解析完整；`1` 表示运行、输入或完整性错误；`2` 表示 PCM 超出容差、索引/配置采集存在未解决错误，或配置／帧解析为 `partial` / `unsupported`。命令行语法错误也由 clap 返回 `2`。`parse-packets` 达到前缀目标后通常仍返回 `2`，因为整包载荷没有解析。

## 导出数据

所有 JSON/JSONL 记录采用 `schema_version: 1`。系统可选属性统一写为 `{"value": ..., "error": null}`；不支持或读取失败时 `value` 为 `null`，`error` 保留操作名和原始 `OSStatus`。

### `inspect` / `scan`

保存容器、ASBD 格式字段、数值声道布局与可读名称、包数、packet table、magic cookie 的字节数和 SHA-256。索引按格式、布局和配置哈希分组，不将哈希组称为已经识别的 profile。索引不遍历符号链接，跳过数量写入汇总；坏文件写入错误记录后继续处理其他文件。

### `collect-configs`

接受现有 schema v1 的扫描 JSONL，按 cookie SHA-256 去重。如果索引仍带有 `<manifest>.incomplete` 标记，则在读取索引和创建输出目录之前拒绝采集，退出码为 `1`。每组优先读取体积较小的来源；失败时记录原因并尝试同组其他来源，原始来源映射保留在 `index.json`。配置写为 `cookies/<sha256>.bin`。实际字节数或哈希与索引不符时记录错误，不以新配置悄然替代旧配置。`complete` 表示采集流程正常结束，`all_collected` 才表示所有配置组和输入记录均成功；来源路径使该目录仍属于本地研究数据。

### `parse-cookie`

接受完整的独立 `dapa` cookie，输入上限 8 MiB。当前支持版本字段 `0x0800` 的部分 channel/lbr、HOA ACN/SN3D 配置结构、响度／DRC 配置、场景及来源扩展；不按文件长度或哈希识别格式。输出：

- `status`：`complete`、`partial` 或 `unsupported`。
- `fields`：已确认的线上字段和值，`bit_offset` 从整个 cookie 的起点计数，`bit_length` 是实际占用的位数，位序为 MSB-first。
- `derived`：从配置字段计算的采样率、声道数、帧长度、布局等，不读取旁路容器元数据作为解析值。
- `unknown_ranges`：未解析的位范围及原始十六进制字节。`raw_hex` 从包含起始位的字节开始，首字节应跳过 `first_byte_skip_bits` 个高位。
- `diagnostics`：停止原因和位置。损坏或截断输入的错误另含 `bit_offset`。

`complete` 严格表示当前实现已覆盖整份输入的**语法结构**，包括有证据的填充位；不意味着实现了音频解码、所有配置的语义合法性检查或空间渲染。少数字段暂用 `parameter_*`、`flag_*`、`content_origin.values` 等中性名称保留数值，没有为未确认的操作含义命名。未知分支立即停止并保留剩余数据；非零且尚未核实的填充返回 `partial`。

本机 **64/64 份不同配置均完整覆盖语法**。第四阶段补齐了最后两份 HOA ASC 类型 2 配置，并验证了可重新生成的一至三阶 ACN/SN3D 样本；这不代表支持所有 APAC 配置分支或音频解码。长度只用于首批验收选样，不参与解析器分派。

DRC 字段位于 `ancillary.loudness_drc.*`，包括系数、增益集合、指令、声道关联、响度及来源记录。`derived` 中的声道增益集合索引从零计数，`-1` 是线上零值转换得到的哨兵。`*_encoded` 保留编码数值，不自动赋予 dB 等物理单位；内部版本 8 来自已确认的 APAC 调用上下文，不伪装成额外读取的版本字段。未支持的下混、依赖指令、特殊 effect、EQ 或扩展分支返回 `partial`。

HOA 字段位于 `components[i].hoa.*`。派生的 `hoa.coefficient_count`、`hoa.core_channels`、`hoa.transport_channels` 分别表示系数、内部编码通道和 TCE 提供的通道数量；`components[i].channels` 来自 cookie 中独立的输出布局。`hoa.order` 表示容纳系数所需的阶数；输出布局的阶数另以 `components[i].ambisonic_order` 表示，仅在 ACN 布局声道数为完整平方数时提供。布局标签确认 ACN/SN3D 或 ACN/N3D；自定义布局逐项保留 32 位声道标签。静态重映射保留核心前缀及被忽略的输出尾项，并派生有界的有效载波排列。语法可完整解析不表示该标签通过参考组件的 profile 检查；未验证的 TCE 类型仍保留为 `partial`。

### `dump`

- `cookie.bin`：AudioToolbox 返回的原始配置字节，不去除 `dapa` 等外层结构。
- `packets.bin`：按包顺序串联的原始载荷。
- `packets.jsonl`：源包序号、导出文件内的字节偏移、大小、帧数、哈希、原始帧位置和依赖信息。
- `manifest.json`：来源、请求范围、实际范围、总字节数和 SHA-256。

包序号从 0 开始，`export_offset` 只指向导出的 `packets.bin`。`raw_frame_position` 是尚未扣除 priming 的包时间线；不能直接当成 `decode --start-frame` 的有效音频位置。

依赖字段直接保留苹果 API 的含义：`PacketToDependencyInfo` 的 preroll 是从该独立包起解码后，为刷新状态需要继续解码的包数；`PacketToRollDistance` 是目标包之前需要解码的包数。**`independently_decodable=true` 不代表不需要 preroll。** 默认 `dump` 仍只导出原请求范围；指定 `--with-preroll` 后会选择满足这两种约束的独立起点，最多向前 4096 包。所需属性缺失、距离越界或查询没有进展时明确失败。

preroll 导出的 `start_packet`、`actual_packets` 描述实际存储范围，`requested_packets` 仍是用户请求的目标包数，因此实际包数可能更大。新增 `replay_window` 保存 `requested_start_packet`、`requested_packets`、`actual_target_packets`、`included_preroll_packets` 和 `target_raw_start/end`。这些原始帧边界不含有效音频裁剪。

### `replay`

接受包含 `manifest.json`、cookie、包文件和索引的完整目录，使用独立 AudioConverter 输出 `pcm.f32le` / `pcm.json` 及诊断文件 `replay.json`。兼容旧的从包 0 开始的原始导出；旧的非零起点导出需要重新添加 `--with-preroll`。`file.source` 仅保留来源标识，不打开它。

`--start-frame` 使用与 `decode` 相同的有效音频坐标；省略时从导出目标窗口的首个有效帧开始。`--frames` 默认 8192，输出在窗口或源文件的有效音频末尾裁剪；`range.clipped_by` 区分 `window_end` 与 `source_eof`。起点超出窗口、零请求帧数或整数溢出报错。采样率、声道数和布局保持源声明，PCM 仍是交错小端 Float32。

`--input-batch-packets` 默认为 1，范围 1–64，每批数据最多 16 MiB。`replay.json` 分开记录已供给包数／帧数、实际产生的原始 PCM 帧数、前后丢弃帧数及保存帧数；供给数据可能因解码器预读多于已产生的 PCM。请求到达窗口末尾时发送 EOF 并排空；较短请求取得所需范围后停止解码，继续完成剩余文件的完整性检查。

时间换算使用索引的包帧位置与 packet table 的 priming/valid/remainder，不硬编码延迟。`converter_prime_info` 与 `converter_prime_method` 单独记录查询结果；属性不支持时保留原始 `OSStatus`，不将其当作零，也不依靠它自动裁剪容器 priming。默认不修改系统处理设置、不重采样、不下混、不归一化。

包目录校验为纯 Rust：检查完成标记、schema、包序号、连续字节／帧范围、数据大小、逐包及整体哈希、cookie 与已知格式字段的一致性。拒绝目录外引用，包括越界符号链接。manifest 上限 1 MiB、索引行上限 64 KiB、cookie 上限 8 MiB、单包上限 16 MiB。配置解析为 partial/unsupported 并不直接阻止苹果回放；已确认字段冲突或损坏输入仍会被拒绝。

cookie 已确认的 `frame_samples` 用于核对逐包帧数、绝对帧位置和 packet table 总帧数，即使容器的 `frames_per_packet=0` 也执行这些检查；容器声明的非零帧长必须与 cookie 一致。

通用库入口 `packets::PacketBundle::open` 完成初始校验，`range` 计算帧窗口，`next_batch` 提供包数据与相对批次偏移。读取部分数据的调用方应在信任结果前调用 `verify_remaining`，完成第二次流式完整性检查；CLI 已自动执行。

### `parse-packets`

读取与 `replay` 相同的完整包目录，不访问 `file.source`，逐包写入指定 JSONL，并在 stdout 输出汇总。默认从实际存储的首包开始，包含补入的 preroll，最多 150 包；`--start-packet` 使用原文件包序号，遇目录末尾裁剪。零数量、起点越界、损坏配置、哈希或时间线冲突返回错误。

当前目标为版本 `0x0800`、44.1/48 kHz、1024 帧、双声道、单个 channel ASC 和单个 CPE，配置中的 `lbr_flag` 与公共组件参数为零。解析 ASP 包装、元素存在位、SQ 分派、左声道 ICS 的窗口类型、最大频带数及短窗分组，到左声道流载荷入口停止。ASP 类型 2 的内嵌 preroll 按已确认的字节长度界定，原始载荷保留为未知范围；它不同于 `dump --with-preroll` 补入的前置包。

报告的 `fields` 位偏移从当前包 bit 0 起算。`prefix_complete` 表示到达 SQ 载荷入口或 CPE 缺席终点；`status` 仍描述整包语法。`payload_bit_offset` 只标出已确认的载荷起点，`component_end_bit_offset` 保持 `null`，未知范围不复制原始载荷。只要仍有未解析内容，就不能称为整帧 complete。汇总中的 `complete` 仅表示报告已成功写完并通过包目录校验。

LRVQ 当前保留为 **TODO**：读出 `coding_type=1` 后，以 `lrvq_prefix_deferred` 停止，`prefix_complete=false`，保留剩余位。当前系统的默认双声道编码路径未启用该工具；这不意味着其他编码设置、系统版本或已有媒体不会使用它。未知 ASP 类型、重配置或未验证的保留位同样明确停止。

库入口为 `inspect::FrameContext::from_cookie(&[u8])` 和 `inspect::parse_frame(&FrameContext, &[u8])`。上下文只使用 cookie 中已确认的字段；`is_supported()` 表示配置适合尝试当前前缀，不保证每包分支均已实现。`packets::PacketBundle::next_packet` 提供经过校验的原始包记录及字节，保留既有批次回放接口。

单包语法错误记录包序号和包内位位置，继续保留其他包的结果，退出 `1` 并留下 `<REPORT.jsonl>.incomplete`。I/O 或目录完整性失败立即停止并保留已有的不完整输出；正常的 partial/unsupported 结果不会留下失败标记。报告结束前还会校验未选中的包。沿用 128 MiB 输出限额及拒绝覆盖机制；已验证的双声道 ASP 内嵌 preroll 上限为 4096 字节。

原生参考 `replay --processing-policy drc-off` 在载入 cookie 之前显式请求压缩配置 None、DRC mode None 和目标响度 None，随后载入 cookie 并在任何输入之前调用一次公开的 `AudioConverterReset`，保存 `processing-policy.json` 的设置、reset 状态与前后回读。默认 `--processing-policy default` 保持既有原生行为。属性审计中的 `verified` 仅验证公开属性与初始化调用，音频恒等由独立的前后快照验收。完整只读证明可使用：

```sh
python3 scripts/validate_drc_off.py --binary target/debug/apac-tool \
  --bundle artifacts/demo/replay-packets --report reports/drc-off-proof.json \
  --artifacts artifacts/drc-off-proof
```

该验收同时检查实际选中集合、DRC 前后 PCM、内部处理器、错误恢复、帧数与延迟；DRC 内核不恒等、参数或时序不符、解析恢复都会导致失败。外层切换的逐位差异与隐式默认路径单独记录，不能代替独立数学与跨平台验收。

### `decode`

生成 `pcm.f32le` 和 `pcm.json`。PCM 是交错、小端 32 位浮点，保持输入采样率、声道数和布局。HOA 保留 ACN 顺序、SN3D/N3D 归一化和可确定的阶数。`start_frame=0` 指系统已经处理 priming 后的有效音频起点；请求到达文件尾部时实际帧数可少于请求帧数，超出尾部的起点报错。

不执行重采样、下混、归一化或自动增益匹配。参考解码保留系统默认设置，元数据记录能查询到的 `mdrc`、`^pro`、`ptlc`、`pptl`。编码器也记录请求参数和返回的 `brat`、`cdqu`、`cdrc`；这些是原始系统属性值，不能将 `brat=0` 等值解释为文件的实测平均码率。

### `fixture`

每种信号有一个子目录，内含 `source.f32le` / `source.json`、`encoded.caf`、`reference/pcm.f32le` / `pcm.json` 和 `manifest.json`。源信号在相同实现与运行环境下可重复生成；跨系统或编解码器版本应比较记录的哈希和环境。APAC 是有损编码，编码前的 `source` 不是要求解码结果逐位一致的参考。

每个信号的 `manifest.json` 记录 `requested.drc_configuration`、`actual_encoder_settings.cdrc` 和 `encoded.cookie` 的长度／哈希。`drc_configuration_verified` 为 `true` 表示显式请求与系统回读相符，`false` 表示未能核实，`null` 表示未显式请求。导出成功不等于参数对照有效；验收脚本拒绝将回读不支持或不一致的实验计为成功。系统默认回读可能是 `4294967295`，保留原值，不将它推断为某个模式。

### `compare`

接受两个 `pcm.json` 或 `source.json`，检查有效格式、采样率、声道、布局、起点、帧数、文件长度及 SHA-256。默认容差为 `abs(reference-candidate) <= 1e-6 + 1e-5 * abs(reference)`，支持 `--atol` / `--rtol`。不自动对齐、补零或调整增益。输出逐声道最大绝对误差、RMS、SNR、超限样本数，以及独立的 `bit_identical` 标记；通过容差不等于逐位一致。

零误差、零参考能量和空音频的 SNR 使用 `null` 加 `snr_kind` 表达，避免非法 JSON 的 Infinity/NaN。音频中的 NaN/Inf 直接报错。若双方布局都未知（属性缺失或系统返回 `Unknown` 标签），仍可按声道索引比较，但结果中的 `layout_verified` 为 false。

未完成的导出目录保留 `.incomplete.json`；未完成的索引保留同名 `.incomplete` 文件。不能把这些结果当成完整参考数据，比较器拒绝带未完成标记的 PCM 包。排查后删除或改用新输出路径再运行。
