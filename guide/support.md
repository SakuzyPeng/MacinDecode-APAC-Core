# 支持边界

当前实现明确支持与明确拒绝的范围。不支持的路径一律报错拒绝，不猜测。

返回 [README](../README.md)。

## 实现边界

Rust 处理命令行、数据模型、哈希、生成器和比较器；`native/audio_toolbox.c` 通过 SDK 头文件封装 `AudioFile`、`ExtAudioFile` 和 `AudioConverter`。原生资源由 Rust 所有权封装释放，编码结束时显式检查刷新与文件关闭错误。实现不需要 Xcode workspace 的运行目标，也不依赖 Xcode MCP 授权。

当前工具提供 SQ、CAC、TNS、BWE2、HOA 恢复及码流自带的源声道还原，包含共享配置、多 ASC、必要元数据语法和 CAF／MP4 快速范围解码。独立数学与跨平台验收保持实验标识；帧内 trimming 只记录声明，原始块仍输出 1024 帧。CAC 逆混合在独立的 `apac-cac` crate 中；不含它的构建（`apac-tool --no-default-features`，或不开 `cac` feature 的 `apac-core`）只解码 CAC 增益全为 0 的帧，其余以 `cac-unavailable` 明确拒绝。LRVQ、开启 DRC／响度／EQ 音频处理、外层 ASP 重配置、外部空间渲染和实时播放（音频输出）不在当前交付范围；播放器所需的快速打开和按帧 seek 由 `apac_container::Playback` 提供（见 [decoding.md](decoding.md#播放media-与-playback)）。

离散扬声器布局包括 Mono、Stereo、5.1、7.1、7.1.4、9.1.6 和 22.2。单 channel ASC 的 9.1.6 要求 profile 31、level 4、family 193、固定 9 个编码元素及 44.1／48 kHz；相同声道数的其他布局仍明确拒绝。它直接输出码流声明的 16 个扬声器声道，解码后不进行空间渲染。布局、声道顺序与验收入口见 [bitstream.md](bitstream.md#受限离散声道深度-channels)。

CAF 外部布局标签可通过显式 `--input-layout` 纠错，但指定布局必须与 APAC cookie 一致；默认校验和其他格式保持原规则。原始元数据仍参与完整性核验，不能借此绕过损坏结构或改变 HOA／扬声器语义。详见[CAF 布局纠错](decoding.md#caf-容器布局纠错--input-layout)。

## PCM 输出容器

`decode-sq -o FILE` 支持 Float32 WAV／RF64 和 LPCM CAF。WAV／RF64 仅接受已确认的 Mono、Stereo、5.1、7.1、7.1.4 布局标签，按 WAVE speaker mask 顺序写入。9.1.6、22.2、HOA 和不能准确映射的其他布局必须使用 CAF；不通过匿名通道或近似标签绕过限制。CAF 保留解码器的布局标签、声道描述和原始顺序。文件模式不会重采样、下混、归一化或降低样本精度；完整参数和报告契约见[文件输出](decoding.md#音频文件输出decode-sq-input--o-file)。

## 与苹果参考的数值关系

本项目的数值模型由公式和固定运算顺序定义（`apac-sq-math-v2`、`apac-cac-math-v1` 等），跨平台、跨构建逐位一致性须由同一数值版本的实际运行报告证明。以下已定位的差异来自刻意选择的数值规则；未解释的差异仍须调查，不能一概归为苹果误差：

| 环节 | 苹果原生实现（已观测） | 本项目 |
| --- | --- | --- |
| 调制常量 | 三角函数值先截断到十位小数，再转为 Float32 | 由 Decimal 按公式计算，直接正确舍入为 IEEE 位模式 |
| 窗口乘加 | `vDSP_vma`，乘、加分别舍入 | Float64，乘、加分别舍入，不使用融合乘加（FMA） |
| DFT | `vDSP_DFT_Execute` 的输出随缓冲区内存对齐而变化，同一输入不一定得到同一结果 | 固定顺序的 radix-2 DIT FFT，结果确定 |
| CAC 旋转 | arm64e 上使用 FMLA／FMADD 融合乘加，强相消时出现可观测的 FMA 残差 | 两次乘积与求和分别舍入，不融合 |

已观测的 DFT 输出依赖内存对齐，因此未指定数值路径的苹果输出不能作为唯一数学真值。因此与苹果参考比较时采用容差 `abs(reference-candidate) <= 1e-6 + 1e-5 * abs(reference)`；逐位比较只用于本项目自身的跨平台、跨构建一致性。对齐差异的诊断方法见 [validation.md](validation.md#历史正弦窗与苹果合成参考)。

## 共享配置与组合 HOA 流

正式 HOA 恢复最高开放到三阶：零阶仅支持纯 ambient，salient 为一至三阶、6–9 位量化。每个 HOA 恢复域、动态输出域以及 HOA 布局输出域最多 16 个系数；显式 ACN 标签也不能指向 ACN16 及以上。完整四至十阶、17–121 项显式域和超出三阶的动态域在创建解码器时明确拒绝，不截断成低阶代替。

这是逐 HOA 组件的系数域限制。普通多声道输出、源布局转换和由多个低阶组件组成的流可以超过 16 个总输出声道。原始 cookie 解析仍识别历史高阶语法；识别成功不表示 `HoaFrameContext::is_supported()` 或 `StreamFrameContext::is_supported()` 通过。构建只加载一至三阶 salient 字典，高阶存储文件与实验记录保留作历史对照。

共享配置与多 ASC 默认使用顺序解码，CAF／MP4 可使用 `--access fast`；跨平台一致性以对应冻结提交的验收报告为准。此前已发布配置的标识和数值运算顺序保留。新规则使用 `apac-hoa-shared-configuration-v1`、`apac-hoa-multiple-asc-v1` 及可选报告字段。外层 ASP 重配置仍按绑定参考的未实现边界拒绝。

明确的采样率索引 0–12 对应 96000、88200、64000、48000、44100、32000、24000、22050、16000、12000、11025、8000、7350 Hz。SQ、CAC、TNS 和 BWE2 使用对应的 SFB 表；7350 Hz 使用 8000 Hz 的表桶。HOA 分带方法 1 要求 49 个长窗 SFB，适用于 44.1/48 kHz。MP4 的零采样率项从 cookie 取实际采样率，非零冲突仍拒绝。CAF、MP4 和包目录保留顺序范围解码行为。

组合流包含至少一个 HOA ASC，可与已支持的 SQ 声道布局组合，最终输出最多 255 声道。每个主声明的核心帧按线上顺序读取、对齐并验证，包含重复声明和最终未输出的组件。各组件分别保存空间描述、动态选择及合成历史；DRC 与全部组件状态按外层包提交。声明范围相同且类型、长度一致的记录可重复，其他重叠范围拒绝。无场景时依次接入能完整容纳的组件；中性全源场景按源描述区间安排输出，后声明的同范围源替换前者。附加 ASC 是输出描述，不新增编码核心；有中性场景时，它们可重新划分跨组件的源区间。

`inspect::StreamFrameContext::from_cookie` 提供组合上下文。使用 `is_supported()`／`rejection()` 检查资格，`components()` 查询每个主声明的类型、源布局、`source_channels` 和 HOA 实际恢复维度。`output_ranges` 中的每项包含组件内 `source_start`、最终 `output_start` 和 `channels`；组件可能有多个区间或不输出。`output_start` 为第一段的起点，`output_channels` 为全部区间的总长度。`additional_components()` 返回附加声明。`hoa_component(index)` 返回该组件的 `HoaFrameContext`，可继续使用实际长度的分量查询、`full_order()`、`order()` 和维度查询。`apac_core::Decoder` 自动分派组合流，并提供 `components()`、`hoa_component(index)`；既有单 ASC 接口保留。

`parse-packets --depth stream` 输出组合报告；`--depth hoa` 遇到多个或附加 ASC 时自动分派。新报告使用 `components` 数组，逐组件给出绝对位边界。整体布局按实际路由保留源标签；完整保留一个组件的源顺序时沿用该组件的布局标签，其他组合使用逐声道标签，不虚构一个共同 HOA 阶数。

共享 DRC 扩展只读取声明和增益语法。支持普通、衰减、削波和常量编码，线性／样条斜率、多序列／多频带、时间对齐、独立元数据帧长、空初始配置、包内配置更新以及有明确长度的增益扩展。采用 codec 位置 1 的首个系数配置，其他位置仍完整读取；没有对应配置时不制造增益载荷。配置结构改变时清理对应的增益历史，包失败时整体回滚。`shared_parameters`、`sequences`、`configuration_changed` 和 `shared_syntax_profile` 仅在新分支出现。同时读取 downmix 引用、依赖关系、ducking／特殊指令、响度 EQ、FIR／IIR／子带 EQ 声明和有长度边界的配置扩展。DRC、响度和 EQ 音频处理均未启用。原生 None 策略仍可能启用强制指令；原生处理策略与本后端的显式关闭策略分别验证。

其他有明确长度的配置扩展、固定／可变参数辅助载荷按边界读取并报告摘要。TNS 长窗阶数字段超过 12 时，先按参考规则截到 12 再读取系数，以 `encoded_order` 和 `apac-tns-order-clamp-v1` 记录原始声明；已有数值内核不变。非零帧内 trimming 读取 11／16／20 位逃逸字段并校验两端之和不超过 1024，报告声明的有效区间；原始编解码块保留全部 1024 帧，容器 priming／remainder 继续只裁一次。输入工作状态另限制为最多 8192 个已编码源声道，超过时报告资源限制。

开发向量和独立参考入口：

```sh
python3 scripts/hoa_shared_vectors.py --check
python3 scripts/generate_hoa_shared_config_format.py --check
python3 scripts/generate_hoa_shared_drc_format.py --check
python3 scripts/validate_hoa_shared.py --binary target/debug/apac-tool --report reports/shared-check.json
```

当前共享矩阵覆盖 282 组采样率、工具、空间恢复、组合路由、DRC、被动元数据和中性场景序列；冻结验收及参考差异在独立文档仓库记录。

采样率索引 13–15 保留参考解码器之前的采样率对象，本身不携带一个可恢复的频率；独立 cookie 和容器入口明确拒绝缺失该上下文的输入，不将冷启动时的最低 SFB 表桶解释为 8 kHz 别名。索引 16–63 为无效值。

场景图配置及逐帧更新读取父节点、坐标编码、精度、旋转和长度明确的扩展；父引用及循环受检。`packet_tail.scene_graph` 提供位边界、位置数量和编码历史摘要，`processing_applied=false` 表示未运行空间渲染。零精度极坐标的预测更新不携带径向增量位。场景图历史与其他组件历史按外层包原子提交。

`flag_c=false` 时，renderer 配置中的分组、初始参数、矩阵和压缩声明作为被动元数据读取，原始音频路径不消费逐帧 renderer 载荷。`flag_c=true` 请求其他元数据输出模式，超出本 PCM 接口范围。有明确长度的未知参数保留长度与摘要；无长度边界的未实现分支不会猜测跳过。参考组件明确未实现的 HRTF 资源模式和双精度 radiation 分支继续明确停止。概率／Huffman 配置受 4096 符号和 64 位码字的资源限制，原有配置字段总数限制继续生效。

中性场景允许被动标签、语言说明、扩展、多个组和多个预设。每个预设必须完整且仅一次选中全部源；语言替代项只有在来源相同且存在明确回退时才满足固定输出条件。组控制的 `parameter_0` 选择一个成员，关闭参数标志会静音；显式增益 `parameter_1=256` 表示单位增益。类别选择、缺失源、重复选择、不同语言来源或非单位增益不得冒充中性全源路由。

## ASP 与帧长支持边界

外层 ASP 类型 0／1／3 直接读取核心，类型 2 可携带零个或一个内嵌帧。类型 0／3 允许复用 `flag_b=true` 的空间配置；类型 1／2 必须重述当前配置。内嵌类型 0／1／3 先处理但不增加输出时间线，外层仍输出 1024 帧。preroll 的字节对齐填充可以非零，报告保留其原始值；新增类型／填充分支使用可选的 `apac-asp-boundaries-v1` 派生标识。

全局帧长索引仅 0 已由绑定参考实现，其他索引明确拒绝，不视作已知格式别名。参考未实现 ASP 重配置，也不接受数量 2／3、空内嵌帧或内嵌类型 2。长度按 16 位加可选第二个 16 位读取，受输出声道数乘 2048 字节的已验证容量约束；未知内嵌尾部、截断或缺少当前帧都拒绝。错误不会提交任何组件描述、动态映射、帧内配置、DRC 或 overlap；有效重试与干净解码器一致，`reset()` 恢复初始状态。既有数学标识与有效输入报告保持兼容。

便携验收入口为 `scripts/validate_hoa_asp.py --binary BINARY --report REPORT`，独立输入由 `scripts/hoa_asp_vectors.py` 和 `data/hoa-asp-vectors-v1.json` 固定。原生成功以实际帧数、位终点和 PCM 对照确认；只有返回状态为零不能证明解码成功。

SQ v2 去掉反量化与增益各自的 Float32 中间舍入；它会改变部分频谱及 PCM 的低位。CAC、TNS、BWE2 和 HOA 的阶段间 Float32 接口、Float64 内核及合成顺序保持原规则，尚未改成全链路 Float64。v1 数值表和历史报告保留；旧六构建结论不能直接视为 v2 的跨平台运行证明。
