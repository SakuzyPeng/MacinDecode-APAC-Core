# MacinDecode APAC Research Tools

`apac-tool` 是苹果 APAC（Apple Positional Audio Codec）的研究工具集，提供样本索引、原始配置和数据包导出、苹果参考编解码、测试信号及 PCM 比较。

当前版本的编解码由 **macOS AudioToolbox** 完成。独立 APAC 解码算法尚未实现；通用数据格式、信号生成和比较器已与苹果接口分离。`compare` 不依赖苹果框架，但本轮平台验收仅覆盖 macOS。

## 构建

需要 Rust 和 Xcode Command Line Tools。当前机器已用 Rust 1.96.0、macOS 27.0（26A428）验证。

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

# 默认导出前 150 包；范围可显式调整
target/debug/apac-tool dump "$APAC_SAMPLE" --out artifacts/demo/packets
target/debug/apac-tool dump "$APAC_SAMPLE" --out artifacts/demo/packets-75 --start-packet 75 --packets 32

# 默认从有效音频起点解码 8192 帧；帧是所有声道共享的采样时刻
target/debug/apac-tool decode "$APAC_SAMPLE" --out artifacts/demo/start
target/debug/apac-tool decode "$APAC_SAMPLE" --out artifacts/demo/one-second --start-frame 48000 --frames 48000

# 默认生成 48 kHz、双声道、两秒的六种测试信号，编码为 APAC 并生成参考 PCM
target/debug/apac-tool fixture --out artifacts/demo/fixtures

# 多声道或 HOA 的逐声道测试
target/debug/apac-tool fixture --out artifacts/demo/hoa --layout hoa3 --signals channel-solo

# 比较元数据所指向的 Float32 PCM
target/debug/apac-tool compare artifacts/demo/start/pcm.json artifacts/demo/start/pcm.json
```

`fixture` 的布局选项为 `mono`、`stereo`、`surround71`、`surround714`、`hoa3`、`surround222`。信号选项为 `silence`、`impulse`、`sine`、`sweep`、`noise`、`channel-solo`，可用逗号组合。还支持 `--sample-rate`、`--duration`、`--seed`、`--bitrate` 和 `--quality 0..127`。不支持的编码参数由系统返回明确错误。

所有导出命令默认限制累计输出为 128 MiB，包含二进制数据和元数据。需要更大导出时显式添加 `--max-output-mib 256`。读取和写入采用小块缓冲；编码器的原生文件写入回调也受此上限约束。为了给元数据留出空间，导出可能在达到限额之前拒绝请求。

退出码：`0` 表示成功或比较通过；`1` 表示运行、输入或完整性错误；`2` 表示 PCM 超出容差或索引存在失败项。命令行语法错误也由 clap 返回 `2`。

## 导出数据

所有 JSON/JSONL 记录采用 `schema_version: 1`。系统可选属性统一写为 `{"value": ..., "error": null}`；不支持或读取失败时 `value` 为 `null`，`error` 保留操作名和原始 `OSStatus`。

**`inspect` / `scan`**：保存容器、ASBD 格式字段、数值声道布局与可读名称、包数、packet table、magic cookie 的字节数和 SHA-256。索引按格式、布局和配置哈希分组，不将哈希组称为已经识别的 profile。索引不遍历符号链接，跳过数量写入汇总；坏文件写入错误记录后继续处理其他文件。

**`dump`**：

- `cookie.bin`：AudioToolbox 返回的原始配置字节，不去除 `dapa` 等外层结构。
- `packets.bin`：按包顺序串联的原始载荷。
- `packets.jsonl`：源包序号、导出文件内的字节偏移、大小、帧数、哈希、原始帧位置和依赖信息。
- `manifest.json`：来源、请求范围、实际范围、总字节数和 SHA-256。

包序号从 0 开始，`export_offset` 只指向导出的 `packets.bin`。`raw_frame_position` 是尚未扣除 priming 的包时间线；不能直接当成 `decode --start-frame` 的有效音频位置。

依赖字段直接保留苹果 API 的含义：`PacketToDependencyInfo` 的 preroll 是从该独立包起解码后，为刷新状态需要继续解码的包数；`PacketToRollDistance` 是目标包之前需要解码的包数。**`independently_decodable=true` 不代表不需要 preroll。** 本次样本中普通包也可能返回 true，同时 preroll/roll 为 1；包 0、75 等位置则为 0。工具不自动为导出范围补齐这些依赖，`dump` 结果尚不是可单独播放的音频文件。

**`decode`**：生成 `pcm.f32le` 和 `pcm.json`。PCM 是交错、小端 32 位浮点，保持输入采样率、声道数和布局。HOA 保留 ACN 顺序、SN3D/N3D 归一化和可确定的阶数。`start_frame=0` 指系统已经处理 priming 后的有效音频起点；请求到达文件尾部时实际帧数可少于请求帧数，超出尾部的起点报错。

不执行重采样、下混、归一化或自动增益匹配。参考解码保留系统默认设置，元数据记录能查询到的 `mdrc`、`^pro`、`ptlc`、`pptl`。编码器也记录请求参数和返回的 `brat`、`cdqu`、`cdrc`；这些是原始系统属性值，不能将 `brat=0` 等值解释为文件的实测平均码率。

**`fixture`**：每种信号有一个子目录，内含 `source.f32le` / `source.json`、`encoded.caf`、`reference/pcm.f32le` / `pcm.json` 和 `manifest.json`。源信号在相同实现与运行环境下可重复生成；跨系统或编解码器版本应比较记录的哈希和环境。APAC 是有损编码，编码前的 `source` 不是要求解码结果逐位一致的参考。

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

当前工具用于建立研究基准：尚未解析 APAC cookie 的内部语法，也未实现独立解码、空间渲染或实时播放。

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
