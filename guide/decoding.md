# 便携解码：`decode-sq` 与库接口

纯 Rust 的 PCM 解码命令 `decode-sq`、CAF／MP4 输入、快速范围解码，以及 `apac_core::Decoder` 和 `apac_container::Reader` 库接口。支持的声道布局与 HOA 配置见 [bitstream.md](bitstream.md)。

返回 [README](../README.md)。

## `decode-sq INPUT`

从自包含包目录或 [bitstream.md](bitstream.md) 所列布局及限定 HOA 的 CAF／MP4／M4A 原文件输出独立 PCM：

```sh
target/debug/apac-tool decode-sq /path/to/input.caf --out artifacts/demo/caf-pcm
# CAF 范围解码从第 0 包预热；起点越靠后，需要处理的前置包越多
target/debug/apac-tool decode-sq /path/to/input.caf --out artifacts/demo/caf-window --start-frame 480000 --frames 8192
target/debug/apac-tool decode-sq artifacts/demo/independent-sq-packets --out artifacts/demo/rust-pcm
# 有效音频坐标，适用于包含已验证前置依赖的包目录
target/debug/apac-tool decode-sq artifacts/demo/replay-packets --out artifacts/demo/window-pcm --frames 8192
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

当前 `decode-sq` 覆盖 Mono／Stereo／5.1／7.1／7.1.4／9.1.6／22.2 及限定 HOA SQ 配置的包目录、CAF 与受限 MP4／M4A，公共 parameter_b=2，其余资格检查保持明确。双声道旧路径保留原输出；新增声道路径使用相同数学内核和固定 DRC 关闭策略。MP4／M4A 可直接读取下述单音轨封装，不需要预先导出包目录。

输出为比较器可读的 `pcm.f32le`、`pcm.json` 和 `decode-sq.json`。省略范围参数时输出包目录目标窗口或 CAF／MP4 的全部有效音频；`--start-frame` 使用绝对有效音频坐标，`--frames` 指定正的请求长度。包目录的非零导出起点必须有 `replay_window`，并满足独立起点、roll 和 preroll 约束；前置包只建立状态。packet table 的 priming／remainder 只裁剪一次，内嵌帧不增加源时间线，不追加隐含尾帧。短请求结束后仍校验目录未解码部分的完整性，不访问原始音频。

包目录声明了布局时，布局标签须与 cookie 确定的解码布局一致，bitmap 必须为零且不能带声道描述；显示名称不参与比较。布局不匹配会在创建输出目录前报错，避免声道数相同但顺序不同的布局被误标到 PCM。

CAF 输入按文件内容识别，不依赖 `.caf` 扩展名。首版支持 CAF v1、零文件 flags、首块 `desc`、`apac`、44.1／48 kHz、1／2／6／8／12／24 声道或上述 44.1／48 kHz、最多 121 个完整阶系数 HOA、可变包长、固定 1024 帧／包及零格式 flags／bits-per-channel。要求唯一的 `desc`、`kuki`、`pakt`、`data`；其余块顺序可变，未知块按长度跳过，仅末尾 `data` 允许长度 `-1`。`chan` 缺席时从 cookie 的已支持布局取值；存在时仅接受与 cookie 一致的上述标准标签（HOA 为 family 190）、零 bitmap、零描述项。edit count 可非零；不支持其他容器形式时返回明确错误，不回退到原生解码。

CAF 默认顺序模式从第 0 包解码，前置 PCM 被丢弃，以建立 overlap、DRC 与内嵌帧状态；不生成未经验证的随机访问依赖信息，也不套用包目录的 4096 包依赖搜索限额。短范围结束后仍读取剩余包作结构与摘要核验，不宣称其 APAC 语法已完成。`pakt` 决定有效帧及 priming／remainder，包长总和须精确覆盖音频数据，空有效区间不增加隐含帧。

`decode-sq.json` 新增 `input`，记录输入类型；CAF 另记录输入规则版本（双声道 `apac-caf-input-v1`，新增布局 `apac-caf-input-v2`）、容器参数、块范围、布局来源、edit count、cookie／音频／包边界摘要和核验状态。音频摘要是顺序拼接的包字节 SHA-256；包边界摘要依次包含每包的索引、数据内偏移、长度、1024 帧数（四个小端 u64）及该包 SHA-256 原始字节。元数据摘要覆盖文件头、所有块头及已使用的块载荷（data 仅 edit count），未知块的载荷不计入。读取前后检查这些摘要、文件长度与修改时间；这是读取一致性检查，不是 CAF 自带校验和或文件真实性证明。容器错误附带 `chunk_type` 与文件 `byte_offset`。历史报告没有 `input` 仍可读取。

CAF 读取器不缓存完整包表或整文件，cookie 上限 8 MiB、单包上限 16 MiB，输出沿用 128 MiB 累计限额。`parse-packets` 继续接受包目录；现有 `inspect`／`dump` 仍为 macOS 原生工具。

```sh
# 人工容器、PCM 数学与逐位验收：无需苹果文件或研究目录
python3 -B scripts/generate_caf_manifest.py --check
python3 -B scripts/validate_caf.py --binary target/debug/apac-tool --report reports/caf-math.json
python3 -B scripts/validate_caf.py --binary target/release/apac-tool \
  --reference-report reports/caf-math.json --report reports/caf-release.json
# macOS AudioFile 容器证据；显式指定受支持的真实 CAF
python3 -B scripts/validate_caf_native.py --binary target/debug/apac-tool \
  --caf /path/to/input.caf --report reports/caf-native.json
```

内嵌 preroll 先建立当前帧所需的叠加状态，其 PCM 被丢弃。缺席 CPE 输出已有叠加尾部，然后清空尾部；不能直接将整包当作静音。四种窗口由当前编码类型选择，支持全部相邻组合，不施加未由 APAC 语法要求的 AAC 窗口过渡限制。错误保留输出目录失败标记，拒绝覆盖并沿用累计输出限额。

## MP4／M4A 直接输入

支持非分片、自包含、唯一音轨且唯一 `apac` 样本描述的 ISO BMFF 文件。按内容识别，扩展名不参与判断；保持上述离散布局、限定 HOA、采样率和 SQ 配置限制。

```sh
apac-tool decode-sq input.m4a --out artifacts/mp4-pcm --start-frame 48000 --frames 8192
python3 -B scripts/generate_mp4_manifest.py --check
python3 -B scripts/validate_mp4.py --binary target/debug/apac-tool --report reports/mp4-math.json
python3 -B scripts/validate_mp4.py --binary target/release/apac-tool \
  --reference-report reports/mp4-math.json --report reports/mp4-release.json
# macOS：显式提供既有配置集合和第十阶段 B 的合格真实窗口报告
python3 -B scripts/validate_mp4_native.py --binary target/release/apac-tool \
  --collection artifacts/config-collection/index.json \
  --channel-reference reports/channels-native-qualified.json --report reports/mp4-native.json
```

首版接受 `mp41`／`mp42`／`isom`／`M4A ` 主品牌或兼容品牌，最多 64 个兼容品牌；QuickTime 主品牌暂不支持。`apac` 必须为 version 0、data-reference 1、channelcount=2、samplesize=16 的已核实封装形式，并含唯一完整 `dapa`。这里的 2／16 是容器字段，实际输出声道与布局由 cookie 决定，采样率仍严格核对。其他样本条目版本、`chan`／`wave` 扩展、多描述、外部数据引用、加密、多轨与分片文件明确拒绝。

`stsz` 支持固定／逐包长度；`stsc`、`stco`／`co64`、`stts` 联合确定包位置，chunk 须按样本顺序递增且不重叠，每包 1024 帧。支持 32／64 位 box 长度、前置／后置 `moov`、多个 `mdat`、未引用填充，以及末尾零长度 `mdat`。支持相关时间头及 `elst` v0／v1；媒体 timescale 等于采样率，必须有一条非负媒体起点、速率 1 的编辑列表。编辑时长从电影 timescale 转换到音频帧时必须整除，再推导 priming／有效帧／remainder，且与轨道、电影和包表时长一致。缺失／多段／空编辑、变速、非整帧换算、`stz2` 和非零 composition offset 不支持，不从标签猜测裁剪。

MP4 默认顺序模式也从第 0 包预热，不设 4096 包依赖搜索上限，暂不使用访问依赖表快速定位。`sgpd`／`sbgp` 作为辅助元数据处理，允许 `roll`、`prol` 等分组并存；逐份校验 box 边界并计算摘要，不解释分组的访问语义。短请求仍核验全部输入；完整尾部请求会解码全部外层包。读取器使用有界 box／表游标，不缓存完整 `moov`、包表或文件；沿用 cookie 8 MiB、单包 16 MiB 和累计输出 128 MiB 上限。

样本分组的报告范围 `input.boxes.sgpd`／`sbgp` 保留各类型首次出现的位置，`input.sample_group_box_counts` 记录两类 box 的总数；全部分组载荷均进入元数据摘要。原生验收要求参考报告的每份真实源均包含唯一且已通过的 `head`／`middle`／`refresh`／`tail` 四类窗口，缺失、重复或未通过的窗口会使验收失败。

`decode-sq.json.input` 为 `kind=mp4`、`profile=apac-mp4-input-v1`，包含品牌、轨道、原始样本条目字段、cookie 布局来源、时间线换算、box 范围和一致性状态。音频摘要按样本顺序拼接包字节；包边界摘要依次使用包序号、**文件绝对偏移**、包长、1024 帧数四个小端 u64，后接包 SHA-256 原始字节。元数据摘要按遍历顺序覆盖已读取 box 头及使用的元数据载荷；未知非解码载荷和未引用 `mdat` 填充不计入。开始和结束核对内容摘要、文件长度与修改时间；这是读取一致性检查，并非预存校验和或真实性证明。容器错误使用 `chunk_type` 表示 box 类型，附带文件 `byte_offset`。旧 CAF／包目录报告、数值配置与 PCM 保持兼容。

## 快速范围解码

受支持的离散声道、单 HOA 和 HOA 组合流 CAF／MP4 文件均可显式选择 `--access fast`；默认及原库入口仍使用顺序模式：

```sh
apac-tool decode-sq input.m4a --out artifacts/fast-window \
  --start-frame 480000 --frames 8192 --access fast
# 显式顺序模式同时输出访问计数和计时，方便同输入对照
apac-tool decode-sq input.caf --out artifacts/sequential-window \
  --start-frame 480000 --frames 8192 --access sequential
python3 -B scripts/generate_access_manifest.py --check
python3 -B scripts/validate_access.py --binary target/debug/apac-tool --report reports/access-math.json
python3 -B scripts/validate_access.py --binary target/release/apac-tool \
  --reference-report reports/access-math.json --report reports/access-release.json
python3 -B scripts/benchmark_access.py --binary target/release/apac-tool --report reports/access-performance.json
# HOA 与组合流：独立输入、范围／状态对照，以及配对性能测试
python3 -B scripts/validate_hoa_access.py --binary target/debug/apac-tool --report reports/hoa-access.json
python3 -B scripts/benchmark_hoa_access.py --binary target/release/apac-tool --report reports/hoa-access-performance.json
```

快速模式仍完整读取、核验文件，前缀按码流顺序解析内嵌帧及所有核心，推进空间描述、动态映射、帧内配置、场景图和 DRC 历史。HOA 载波始终完成反量化、CAC／TNS／BWE2、空间恢复、源转换及数值校验，包含最终不输出的组件；扫描省去详细字段／诊断报告和 PCM 合成。离散声道仍沿用原规则：没有有效 TNS／BWE2 运算时使用已证明的有限值界，否则执行相同数值内核。该模式不引入持久索引，不解释 MP4 分组来绕过历史校验，读取成本仍随文件大小增长。

对非空范围，按原始音频坐标从 `max(0, floor(raw_start / 1024) - 1)` 包开始合成；其中 `raw_start` 已包含容器 priming。只丢弃目标前一个外层包的 PCM，即可恢复所有组件的 overlap，之后正常解码。缺席元素、内嵌 preroll、DRC 重述／更新及错误回滚保持原规则。空输出请求仍检查语法、元数据和输入完整性，合成数为零。损坏前缀或数值错误不能被扫描模式跳过；包目录的现有依赖规则不变，`--access fast` 对目录返回错误。

显式访问模式在 `decode-sq.json` 增加 `access`，离散声道规则保留 `apac-sq-access-v1`，HOA 使用 `apac-hoa-access-v1`，区分前缀外层包／内嵌帧、执行数值内核的元素、用有限值界检查的元素、合成起点和外部预热包。`packets`／`raw_frames_decoded` 始终只计实际完整音频解码量；DRC 载荷计数包括已扫描的历史，缺席元素和内嵌音频解码计数仅描述实际合成部分。`metadata_before_output_sha256` 和 `metadata_after_processing_sha256` 可核对状态历史与声明来源；没有输出时前者为 null。扫描不会对外生成虚构频谱。

访问计时分别记录初始化核验（含配置准备）、第二遍读取／收尾核验、前缀扫描、完整包解析与合成、独立合成耗时及总耗时；跨平台摘要排除计时。总计时截至最终解码报告写入之前，性能脚本另测包括进程启动在内的端到端时间，交替模式、丢弃首次热身并比较重复测量中位数。实际收益取决于工具启用情况、目标位置和 I/O。

显式访问模式入口为 `apac_research::decode::decode_sq_with_access(input, destination, SqDecodeOptions { start_frame, frames }, SqAccessMode::Fast, limit)`；`SqDecodeOptions` 和 `SqAccessMode` 均从 `apac_research::decode` 导入，也可使用 `SqAccessMode::Sequential`。省略 CLI 访问选项时，旧报告形状和默认行为不变。数值模型、后端及容器规则标识保持原样，DRC／响度处理关闭和默认 128 MiB 限额仍适用。报告中的 `experimental` 字段是冻结报告格式的一部分，始终为 `true`，验收脚本也依赖它；它不表示解码器的成熟度。

## 库接口

使用 CAC 的流需要 `apac-core` 的 `cac` feature（`apac-tool` 默认开启）；没有它时，`Decoder` 遇到非零 CAC 增益的帧返回 `cac-unavailable` 错误，状态不提交。

`apac_core::Decoder` 是单包解码接口：`Decoder::new(&Config)`（或 `from_cookie`）按配置选择立体声、单 ASC 多声道、HOA 或组合流路径，不支持时返回带原因的错误；`info()` 给出采样率、声道数、每包 1024 帧、路径类型和布局；`decode(packet, &mut out)` 把 `1024 × channel_count` 个交错 Float32 样本写入调用方缓冲（不足时报错且不改状态），返回该包的统计 `FrameInfo`；`decode_vec` 返回新分配的样本；`advance(packet)` 只推进状态（快速定位用，之后须先完整解码前一包再导出 PCM）；`checkpoint()` 和 `restore(&checkpoint)` 保存、恢复两包之间的状态（见[播放](#播放media-与-playback)）；`reset()` 回到初始状态。内嵌帧、当前帧、尾部和全部声道合成都成功后才提交状态，失败及重置不会留下半个包的状态。解码路径不记录语法字段；backend、support_scope 等报告标识由 `apac_research::implementation` 根据所选路径给出。文件级入口为 `apac_research::decode::decode_sq` 和 `apac_research::decode::decode_sq_with_options`，后者接受 `(input, destination, SqDecodeOptions { start_frame, frames }, limit)`。这两个文件级入口均接受包目录、CAF 或受限 MP4／M4A 文件。直接使用单包接口时，调用者负责顺序与外部依赖，包目录入口会验证这些条件。

`apac_container::Reader` 把这个循环封装成范围解码接口，`decode-sq` 也走它：

```rust
let source = apac_container::CafReader::new(std::fs::File::open(path)?)?; // 或 Mp4Reader
let mut reader = apac_container::Reader::open(source, Some(start), Some(frames), Access::Fast)?;
let mut pcm = vec![0f32; 1024 * reader.decoder().info().channel_count as usize];
while let n @ 1.. = reader.read(&mut pcm)? { /* pcm[..n × 声道数] 为交错样本 */ }
reader.seek(other_start)?;                         // 双向；终点不变
let (source, decoder, stats) = reader.finish()?;   // 补读剩余包，完成一致性校验
```

- **数据来源**：`PacketSource` 统一 CAF、MP4（`apac-container`）和包目录（`apac-research`）。它提供流描述、帧范围、首包序号、是否允许快速访问，以及按遍读取的包。每一遍都从首包开始，读到末尾时与打开时的首遍核对，并重新扫描结构。
- **打开**：`Reader::open` 的配置检查顺序和拒绝文本与 `decode-sq` 一致。快速访问只用于 CAF/MP4；HOA 和组合流必须包含第 0 包；声道数和布局必须与 cookie 一致。传入已消费过包的来源（包括 `finish` 返回的来源）时，先补完当前遍的完整性校验，再倒回首包建立完整解码历史。
- **读取**：`read` 每次返回下一包中落在范围内的帧数，读完返回 0。`read_with` 另外在输出起点那一包之前回调，供报告记录 `metadata_before_output_sha256`。`Sequential` 从首包起逐包完整解码；`Fast` 对输出前一包之前的前缀只推进状态，从那一包起完整解码。
- **定位**：`seek(frame)` 接受窗口起点到当前范围终点之间的任意帧，结果与在该点新打开的 Reader 逐位相同。
  - 向前跳时，若剩余包的处理方式与新 Reader 完全一致（尚未越过它要完整解码的第一包，且没有读了却未解码的包），就在当前这一遍继续；
  - 否则先补读当前遍剩余的包并完成校验，成功后才倒回首包、重置解码器、重放前缀；新的一遍读到末尾时再次校验。此前已返回 PCM 对应的摘要不会因 seek 丢失，这也意味着回退可能需要读取剩余文件。
- **失败恢复**：包解码失败后，后续 `read` 和 `finish` 会拒绝，必须先成功 `seek`，再从首包重放前缀；失败包不会被定位操作跳过。来源读取、校验或回退失败会使该 Reader 终止使用，后续 `read`、`seek` 和 `finish` 均拒绝。
- **统计**：`stats()` 给出 `decode-sq` 报告所用的计数（前缀扫描、完整解码、预热、缺席元素、内嵌帧、DRC 帧、保存帧）和各阶段耗时。

完整示例见 `crates/apac-container/examples/decode_file.rs`。它按文件头识别 CAF 或 MP4，输出原始小端 Float32 交错 PCM，与同范围的 `decode-sq` 逐字节相同；输出文件必须不存在：

```sh
cargo run -p apac-container --example decode_file -- input.caf output.f32
cargo run -p apac-container --example decode_file -- input.mp4 output.f32 --fast --start 48000 --frames 96000
```

## 播放：`Media` 与 `Playback`

`decode-sq` 和 `Reader` 面向核验：打开时完整读一遍文件，每一遍读到末尾都与首遍核对，往回定位前要补读剩余文件，快速模式也要从第 0 包扫描前缀。播放器需要的是快速打开和有上限的定位代价，`apac-container` 为此另外提供 `Media` 和 `Playback`。它们不改变 `decode-sq` 的行为和输出。

```rust
let media = apac_container::Media::open(std::fs::File::open(path)?)?; // 只读元数据
let mut playback = apac_container::Playback::open(media)?;
playback.extend_index(u64::MAX)?;    // 可选：先建好完整索引，例如在加载线程里
playback.seek(frame)?;               // 有效音频帧，按帧精确
let mut pcm = vec![0f32; 1024 * playback.decoder().info().channel_count as usize];
while let n @ 1.. = playback.read(&mut pcm)? { /* pcm[..n × 声道数] 为交错样本 */ }
```

- **打开**：`Media::open` 按内容识别格式：以 `caff` 开头的是 CAF，其余按 MP4 处理。结构、描述、cookie、声道布局和时间线使用与 `CafReader`／`Mp4Reader` 相同的检查规则和拒绝文本，但只读必要元数据：CAF 读块头、`desc`／`kuki`／`chan` 载荷、`pakt` 的 24 字节表头和 `data` 的 edit count；MP4 读 box 头、流描述、时间线和样本表表头。打开时不计算摘要，不读取整张包表或 sample group 载荷；逐包表项（包括可选的 `ctts`／`stss`）延迟到读包时检查，音频数据也在读包时才读。
- **读包**：`Media::read_packet(&mut cursor, &mut buf)` 按游标读一个包，并检查它在包表和音频数据的边界内。游标走到表末时，检查包表和音频数据是否被恰好用完，拒绝文本与已核验读取器相同。`PacketCursor` 很小且可复制，保存后能从任意位置重读；读取失败时游标不动。这条路径不计算摘要、不重扫结构，因此不核验文件在读取期间是否变化；需要这种保证时使用 `Reader`。
- **输出**：`Playback::read` 输出有效音频（已裁掉 priming 和 remainder），交错 Float32，每次至多 1024 帧。从开头读或在任意 seek 之后读，结果都与同范围的 `decode-sq`、`Reader` 逐位相同。返回 0 前会检查剩余包表及数据边界，包括完全落在 remainder 中的包，拒绝多余表项和样本／时间计数不匹配；这一步只读表项，不读尾包音频、不推进解码器。检查失败可以重试，输出位置保持在结尾。直接 seek 到结尾也执行该检查，耗时取决于尚未检查的表项数量。DRC／响度只读不处理、帧内 trimming 只记录，这两点都与 `decode-sq` 相同。
- **检查点**：`Decoder::checkpoint` 保存两包之间的解析状态，即 DRC 历史以及 HOA 和组件状态，不含 overlap 和配置。`Decoder::restore` 只接受该解码器及其克隆的检查点，恢复后等于一个新解码器 `advance` 到同一位置。`Playback` 每隔 `checkpoint_interval` 包保留一个检查点，默认 64 包（48 kHz 下约 1.4 s）。数量超过 `max_checkpoints`（默认 1024）时，隔一个删一个并把间隔加倍，所以内存有上限；每个检查点通常只有几 KB。
- **定位**：`seek(frame)` 只选起点：当前解码器离目标更近就原地继续，否则恢复目标前一包之前的最后一个检查点。下一次 `read` 先推进到目标前一包，完整解码它以重建 overlap，再输出目标帧。读包和 `extend_index` 经过检查点位置时都会保存检查点。索引覆盖目标后，一次 seek 至多推进 `间隔 − 1` 包、完整解码 2 包；索引尚未覆盖的位置要从最后一个检查点向前推进，代价随距离增长。
- **建索引**：`extend_index(max_packets)` 用解码器的克隆只推进状态，处理至多 `max_packets` 包后返回；走到表末时返回 true，此后 `index_complete()` 也为 true。`indexed_frames()` 给出索引目前覆盖到的位置（检查点被稀疏化后，最后一个检查点可能早于结尾）。它在调用线程里执行：可以在加载线程里调用 `extend_index(u64::MAX)` 再把 `Playback` 交给解码线程（`Playback<File>` 是 `Send`），或者在解码线程空闲时分批调用。
- **后台建索引**：边播边建索引用 `Indexer`。`playback.indexer(source)` 以同一输入的第二个句柄（例如再打开一次文件）建一个索引器：它自己打开 `Media`，打开时已知的包表布局（含 MP4 表的位置、大小、条目数、宽度及可选表）、流描述、cookie、时间线、长度或修改时间与播放输入不一致时拒绝。这个检查不读取音频和延迟读取的逐包表项，调用方仍须确保提供同一输入。然后从播放当前最后一个检查点出发，用解码器的克隆只推进状态，在检查点间隔的整数倍处保存检查点。`Indexer<File>` 和 `IndexBatch` 都是 `Send`，可以移到工作线程。`run(max_packets)` 扫描至多 `max_packets` 包，走到表末时返回 true；`take_batch()` 取出至今收集的检查点。由播放器把批次发回解码线程（例如用 `std::sync::mpsc`），在两次 `read`／`seek` 之间调用 `playback.merge_index(batch)`。合并按读包时相同的规则保留应有的检查点，同样稀疏化；收到表末批次后 `index_complete()` 为 true。批次必须按取出顺序全部合并：漏掉一个、导致索引出现缺口时，后续批次以 `index batch does not continue the index` 拒绝；其他播放实例的批次以 `index batch belongs to a different playback` 拒绝；被拒绝的批次不改变索引。索引器遇到失败的包时停在那里，错误带包序号，之前收集的检查点仍可取出合并；重试不会重复保存同一检查点，包括已经取出的检查点。库内仍不开线程，线程和通道由播放器决定。

```rust
let mut indexer = playback.indexer(std::fs::File::open(path)?)?;
let (batches, received) = std::sync::mpsc::channel();
std::thread::spawn(move || loop {
    let result = indexer.run(256);
    if batches.send(indexer.take_batch()).is_err() || !matches!(result, Ok(false)) {
        break;
    }
});
// 解码线程，在两次 read 之间：
for batch in received.try_iter() {
    playback.merge_index(batch)?;
}
```
- **错误**：解码失败时，播放停在失败的包上：错误带包序号，位置不变，重试会得到相同的错误。这里不跳包、不猜测状态，所以该包之后的位置无法到达，之前的位置仍可 seek。读源错误同样不移动位置，来源恢复后可以重试。

`crates/apac-container/examples/playback.rs` 按播放器的方式解码：先打开，可选地建好完整索引（`--index`）或在第二个线程上用 `Indexer` 边解码边建（`--background`），再 seek 并写出原始 Float32，同时打印各阶段耗时。输出与同范围的 `decode_file` 示例和 `decode-sq` 逐字节相同。

```sh
cargo run --release -p apac-container --example playback -- input.m4a output.f32 --index --start 480000 --frames 8192
cargo run --release -p apac-container --example playback -- input.m4a output.f32 --background
```

### 播放性能

以下数据来自 2026-10-04 在 Apple M4 Pro（macOS，Rust 1.98.0 release 构建，开启 CAC）上对 142 个真实 APAC 文件（CAF 4 个、MP4／M4A 138 个，共 11.41 小时）的完整播放测试，代码 `c324e88`。每个文件通过 `Media`／`Playback` 从头读到结尾，整曲 PCM 摘要与 `Reader` 顺序解码完全一致，另做了 3,518 次定位检查，全部一致。吞吐为音频时长除以 `Playback::read` 累计耗时（单线程，含读源和解码）：

| 输出 | 文件数 | 加权吞吐（实时倍数） | 最慢文件（倍数） | 首播 p95（ms） | 索引后定位 p95（ms） |
|---|---:|---:|---:|---:|---:|
| 2 ch 立体声 | 1 | 207 | 207 | 1.3 | 3.7 |
| 8 ch（7.1） | 13 | 83 | 80 | 2.3 | 10.1 |
| 12 ch（7.1.4） | 21 | 59 | 55 | 2.2 | 13.6 |
| 16 ch HOA | 2 | 40 | 40 | 2.2 | 20.9 |
| 24 ch（22.2） | 105 | 33 | 23 | 2.9 | 24.3 |

- 全库加权吞吐约 38 倍实时；从打开到首次返回 PCM 中位约 1.9 ms，p95 约 2.9 ms。测试进程的峰值 RSS 不超过约 25 MiB（含参考解码和测试缓冲）。
- 建好索引后，定位加首次读取中位约 3.8 ms，p95 约 23 ms，最大约 57 ms。
- 约 192 万次读块中有 60 次超过该采样率下 1024 帧的时长。因此不要在音频回调里直接解码：在工作线程解码，通过缓冲向回调供数。
- 真实码流的状态扫描仍要完成熵解码、反量化和 TNS／BWE2 等数值运算，只省去合成，所以建索引只比完整解码快约 1.5–2.5 倍。4–11 分钟的曲目完整建索引约需 2–7 s，34.7 分钟的 24 ch 曲目约需 27.5 s；没有索引时首次定位到后段的耗时与之相当（后者约 32 s）。长曲目应在开始播放时就用 `Indexer` 在后台建索引，并在索引覆盖前提示定位较慢。

这些数字绑定上述平台、版本和素材，不构成其他平台的运行验收。

`crates/apac-container/examples/realtime.rs` 在任意 CAF／MP4 文件上测量同样的指标：每个文件从头到尾解码 `--repeat` 遍（默认 3），报告中位倍速、完整建索引的倍速，以及单次 `Playback::read`（至多一包、1024 帧）耗时的 p50／p99／最大值与 1024 帧时长之比。没有有效音频帧的文件以 `input has no valid audio frames` 报错，继续测量后续文件，最终退出码为 1。没有真实素材时，冻结状态夹具可以加长后写成 CAF／MP4 测试文件（默认每个 3000 包，约 64 s）作为可复现的基线：

```sh
APAC_BENCH_DIR=bench-streams cargo test --release -p apac-container --lib write_benchmark_streams -- --ignored
cargo run --release -p apac-container --example realtime -- bench-streams/*.caf bench-streams/*.m4a
```

夹具包只有数十到数百字节，大多不启用 TNS／BWE2，远小于真实码率，只适合比较同一机器上的版本差异，不能代表真实素材：在一颗 2.8 GHz Xeon 云端 vCPU 上，其解码倍速（22.2 约 38×、7.1.4 约 75×、7.1 约 95–115×）高于上表，建索引倍速（约 70–1100×）更比真实素材高出一个数量级。

## 离散声道状态验收

新增 Mono／5.1／7.1 路径记录 `rust_channel_sq_cac_tns_bwe2_drc_off_f64_fft_v2` 与 `apac-channel-state-v1`，额外统计元素缺席数量。旧双声道后端和状态标识保持不变。所有布局的默认数值配置为 `apac-sq-math-v2`、`apac-cac-math-v1`、`apac-tns-math-v1` 和 `apac-bwe2-math-v2`，双声道后端为 `rust_sq_cac_tns_bwe2_drc_off_f64_fft_v11`（`apac-sq-math-v2` 起由 v10 升级；v10 对应 `apac-sq-math-v1`）。随 SQ v2 一起，其余后端版本号各加 1（如 `rust_channel_…_v1` → `_v2`、`rust_hoa_salient_…_v1` → `_v2`）；HOA 空间控制的原 `_v2` 已被逐帧控制占用，因此静态控制改为 `_v3`、逐帧控制改为 `_v4`，保证同一后端名不跨数值版本复用。v1 黄金快照保留旧名，状态为 `packet_state_profile=apac-asp-state-v1`。合成仍采用固定顺序的 Float64 IMDCT、正弦窗和叠加，仅最终 PCM 转为 Float32，零统一为正零。保留 `experimental=true`、`numerical_qualification=independent_math_reference`；`complete` 描述导出完整性。报告另记录实际解码／完整性校验包数、预热包、内嵌帧和缺席 CPE 数量，以及常量摘要、编译器和 debug assertions。

```sh
# 冻结的 2,268 个状态序列；正式测试不依赖研究目录或苹果文件
python3 -B scripts/generate_packet_manifest.py --check
python3 -B scripts/validate_packets.py --binary target/debug/apac-tool --output reports/packet-math.json
python3 -B scripts/validate_packets.py --binary target/release/apac-tool \
  --reference-report reports/packet-math.json --output reports/packet-release.json
# 可选 macOS 原生状态／边界诊断，组件必须匹配已核实的哈希
python3 -B scripts/validate_packet_native.py --binary target/debug/apac-tool --output reports/packet-native.json
```

状态参考由人工生成器声明的窗口、频谱与内嵌事件驱动，用 Decimal 直接 IMDCT 求和计算 PCM。验收保留 `atol=1e-6, rtol=1e-5`，跨构建另对所有阶段及 PCM 的小端字节摘要逐位比较。原生诊断区分输入频谱差异、状态／边界证据及完整 PCM 差异；既有 TNS／BWE2 原生压力差异不会被当作新的状态真值。无 DRC 及固定关闭策略的 DRC 编码控制样本完整 PCM 仍按上述容差比较，超差指标与输入保留。`validate_packet_native.py` 和 `validate_drc_native.py` 默认将它单列为兼容性诊断；显式加 `--require-native-pcm` 时，结构通过但 PCM 超差退出 2。独立数学判断仍由便携验证器完成。
