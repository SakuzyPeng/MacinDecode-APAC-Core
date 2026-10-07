# BWE2 公开接口测量

`scripts/bwe2_blackbox.py` 通过现有 `mapac replay --processing-policy drc-off` 测量 BWE2。工具使用受控的 AAC 频谱输入、公开 PCM 和属性回读，恢复 64 项激励增益候选，并检查两级 LSF 码本的可识别性。它不会应用正式数据、提交或推送。

采集需要 macOS、已构建的 `mapac`、Python 和 NumPy。NumPy 只用于离线数学分析，不增加 Rust 构建或产品运行依赖。输入生成器只加载公开来源的 AAC 表；重建进程拒绝打开原 BWE2 字典及其参考提取器。

## 采集与恢复

新批次需要一个本地账本目录和位于已挂载外置卷上的全新证据目录。以下命令从代码仓库根目录运行，二进制使用已有共享构建目录：

```sh
python3 -B scripts/bwe2_blackbox.py pilot \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new \
  --mount /Volumes/cache \
  --evidence /Volumes/cache/apac-evidence/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py gains \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new --seeds 8

python3 -B scripts/bwe2_blackbox.py anchors \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new --seeds 64

python3 -B scripts/bwe2_blackbox.py refine-gains \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new --seeds 64

python3 -B scripts/bwe2_blackbox.py freeze \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py validate \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new
```

每个阶段输出含摘要的 JSON 路径。`pilot` 校准 PCM 到频谱的映射，使用功率梳状输入及稳定、首项为一的滤波器分解估计绝对增益。`gains` 扫描全部索引，`anchors` 与 `refine-gains` 使用两组固定 LSF 索引、独立符号序列和分组重复改进尺度与高增益精度。

`freeze` 使用从 PCM 估计中推断的 `0.00001` 十进制网格。它明确记录该假设，保留连续估计、经验误差和全部 Float32 候选；不能把该假设解释为无条件确定原始任意 Float32 数据。只有区间内对应一个位模式才标记该项确定。高增益项通过多组独立锚点的共同尺度约束判定，原始重复离散程度仍保留。

`validate` 在冻结之后采集另一组 LSF、两档载波幅度、两种复制区间、四个新符号序列，共 512 项逐索引验证及八项绝对尺度控制。独立重复编号绑定候选摘要，防止新候选把冻结前的观测当作留出验证。

## LSF 可识别性

LSF 独立重建目前按项目优先级暂缓为 TODO，原 LSF 数据和来源保留。以下工具用于复核既有证据或日后恢复试验，不代表两级原表已完成重建。

```sh
python3 -B scripts/bwe2_blackbox.py lsf-probe \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py lsf-result \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new
```

当前试验使用七个第一层索引与七个第二层索引的 49 种组合，输出合成频率估计、组合检查和加法分解歧义的可复现示例。两表相加后的输出与原始两表不是同一个测量对象；还须解决两级偏移自由度及间距调理丢失的信息。

`lsf-result` 成功保存报告不表示 LSF 已完成。当前报告的 `status=incomplete`、`eligible_lsf=false`，不会生成可接入的两级原表，也不自动展开全部 1,024 个向量。示例证明当前拟合模型存在不同的 Float32 分解，不宣称已证明所有可能的公开接口试验都无解。

## 冻结后的独立对照

独立脚本只对照增益。将前面实际输出的冻结及验证文件传入，不使用示例名称猜测文件摘要：

```sh
python3 -B scripts/bwe2_blackbox_compare.py \
  --out reports/bwe2-blackbox-new \
  --candidate /path/to/freeze-HASH.json \
  --validation /path/to/validate-HASH.json
```

对照前检查 64 项唯一候选、512 项验证覆盖、候选摘要绑定和冻结先于独立采集的时间顺序。输出只报告一致项数及不一致索引，不把参考数值交回重建器；LSF 不参与这一步。`eligible_gain` 只表示该增益候选完成测量、验证和对照，不表示正式来源已替换。

## 正式增益来源

已经合格的 64 项增益现由 `data/bwe2-gains-measured-v1.json` 提供。原格式文件保留增益副本及逐项来源范围，LSF 仍使用原观测来源；重建中的网格假设、冻结和验证身份均写入测量原表。

```sh
# 离线核验原表、副本与来源记录，无需苹果组件或本地测量目录。
python3 -B scripts/generate_bwe2_gains_measured.py --check

# 从已登记且摘要匹配的冻结产物复现正式原表。
python3 -B scripts/generate_bwe2_gains_measured.py --check \
  --candidate /path/to/frozen-candidate.json \
  --validation /path/to/heldout-validation.json \
  --comparison /path/to/final-comparison.json
```

`--write` 从已登记的来源重新生成文件，不能用任意新候选绕过资格登记。Rust 构建直接读取测量原表，校验完整文件和位模式摘要，并要求格式副本与来源记录一致。生成器不会修改 LSF 数值或扩大其来源声明；BWE2 数值配置和表语义摘要不变。

## 续跑和证据

```sh
python3 -B scripts/bwe2_blackbox.py status --out reports/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py audit \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-blackbox-new

PYTHONPATH=scripts python3 -B -m unittest test_bwe2_blackbox -v
```

重复同一阶段命令即续跑：已有完整阶段先核验原始证据，再返回原冻结文件；未完成阶段复用成功的原始观测。`--reanalyze` 保留旧文件并创建新分析版本；若产生新候选，其留出验证需要新的独立编号，不能用旧时间顺序通过验收。扩展 seed 数量会采集缺失的请求。更改原生环境不允许沿用该批次。

SQLite、冻结 JSON、工具快照和分析失败日志保存在本地 `--out`。输入、无损压缩 PCM、公开属性、返回状态和成功回执保存在外置证据目录。每个对象按未压缩内容的 SHA-256 校验；只有压缩副本通过逐字节核验后才移除临时原件。失败和中断文件保留。`audit` 核对全部对象、请求身份、公开输出契约及账本与持久化回执的一致性。

每批上限为 4,096 次调用、512 MiB 新证据，单次超时 30 秒，两个位置均保留至少 1 GiB 空间。证据预算按去重后的未压缩对象计费，失败或中断的临时目录继续占用预留额；报告另列实际磁盘占用。原生调用串行运行，同一批次只有一个写入者，`status` 只读。

卷 UUID 在启动及阶段完成时校验，采集期间检查挂载和文件系统身份。外置卷断开即停止，不在挂载点下新建本地替代目录。重连后重新核验相同卷及原生身份，再运行 `audit` 和未完成阶段。当前工具没有自动提高限额或搬迁证据的入口。

## LSF 后续诊断

`scripts/bwe2_lsf_probe.py` 继续使用首轮冻结的增益和原始观测，研究直接 LSF 拟合、未触发调理的组合约束、原生合成基底及公开编码器。该入口固定首轮增益候选摘要，需要对应的原始目录；它不是任意组件版本的通用 LSF 重建器。

```sh
python3 -B scripts/bwe2_lsf_probe.py refine \
  --binary /path/to/shared-target/debug/mapac \
  --observations reports/bwe2-blackbox-first \
  --out reports/bwe2-lsf-followup-new \
  --mount /Volumes/cache \
  --evidence /Volumes/cache/apac-evidence/bwe2-lsf-followup-new

# 后续阶段使用相同的 binary、observations 和 out，无需再次提供 mount/evidence。
python3 -B scripts/bwe2_lsf_probe.py encoder \
  --binary /path/to/shared-target/debug/mapac \
  --observations reports/bwe2-blackbox-first \
  --out reports/bwe2-lsf-followup-new --cases 12
```

其他阶段为 `settings`、`codec-settings`、`joint-holdout`、`native-basis` 和 `joint-envelope`。`joint-holdout` 需要先完成 `refine`；`joint-envelope` 需要已有 `joint-holdout` 结果。`native-basis` 使用 256 个单谱线输入及四个独立混合输入检查合成反演，不读取历史窗表。

编码试验通过已有 `fixture` 和 `dump` 生成、导出人工码流，只调用 `parse-packets --depth tns`，再独立读取 BWE2 的标志及字面索引。不会运行依赖旧 LSF 字典的 BWE2 数值解析。`fixture` 附带的默认参考解码只保存为原始证据，不用于 LSF 数值反演。设置查询只使用 SDK 声明的 AudioConverter／AudioCodec 接口，返回不支持或空列表也是需保留的观测。

原始观测只读引用；新编码产物与合成校准留在新证据根。编码及参考解码计两次原生操作，设置查询计一次，人工包回放逐次计数；两套账本合计受原 4,096 次、512 MiB 限额约束。命令在 30 秒后超时，同一任务使用一个写入者，串行采集。重复成功请求会核验文件摘要并复用结果；失败和中断保留现场，不被算作成功采样。

每个阶段写入新的分析版本，保留既有结果。相对表的计算规范 `A[0]=0` 只用于消除求解中的自由度，不代表发现了原表。`joint-holdout` 将诊断模型冻结后再以独立编号采集验证；`joint-envelope` 则明确标为对既有验证数据的探索性重分析，不能代替新的冻结后验收。现有结果均为 `eligible_lsf=false`。

```sh
PYTHONPATH=scripts python3 -B -m unittest test_bwe2_lsf_probe test_bwe2_blackbox -v
```

## LSF 谱域抵消与 LPC 诊断

`scripts/bwe2_single_spectrum.py` 在原生合成之前，以公开 HOA mode 0 定长系数抵消 BWE2 输出。宽配置使用 25 个输出通道、25 个 salient 分量、16 个等宽空间频带和九位定长描述；高阶 HOA 只用于输送测量信号，不读取其 Huffman 表或矩阵。每个频带保留一个 BWE 载波及八条独立抵消谱线。

对称的稀疏功率梳状输入在数学模型中的前 16 个非零延迟自相关均为零。四种梳状相位覆盖 256 个奇数频点；所有频点在同一次公开 replay 中采集，每个查询帧后接静音帧收集 overlap。候选值拆为三个八位尾数块，分别通过二次幂增益和精确的 mode 0 系数输送。

```sh
python3 -B scripts/bwe2_single_spectrum.py controls --wide \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-single-new --mount /Volumes/cache \
  --evidence /Volumes/cache/apac-evidence/bwe2-single-new

python3 -B scripts/bwe2_single_spectrum.py measure --wide \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-single-new --pair 0 0 \
  --controls /path/to/single-controls-HASH.json \
  --profile /path/to/pcm-derived-lpc-estimate.json

python3 -B scripts/bwe2_public_lpc.py \
  --spectrum /path/to/single-spectrum-HASH.json \
  --out reports/bwe2-single-new/lpc-models

python3 -B scripts/bwe2_single_spectrum.py validate-model --wide \
  --binary /path/to/shared-target/debug/mapac \
  --out reports/bwe2-single-new --model /path/to/frozen-lpc-model.json \
  --source-gain 132 --seed 20261007 --replicate heldout-1
```

起点文件的 `continuous_lpc` 含 17 项、首项为一的独立 PCM 拟合结果；可从前述 `lsf-probe` 的相应 `lpc` 估计生成。它只决定抵消搜索中心，不决定观测值。`--seed-spectrum` 可代替 `--profile`，用已经采集的同一索引组频谱作为中心。初次测量是带有经验误差界的 Float32 网格推断：各通道须一致、误差界和距候选的偏差均小于八分之一 ULP；确切零 PCM 单独记录。单位谱线校准及已知值控制须先通过，推断不能被描述为不依赖假设的原始 LSF 测量。

`bwe2_public_lpc.py` 只在离线过程中调用公开 Accelerate 数学接口。它搜索单精度实数 DFT 的两种对齐方式、`vDSP_zvabs` 幅值及先除后乘增益的固定模型。匹配全部观测后冻结 LPC 候选；`validate-model` 把两种预先声明的完整预测向量同时送入新解码器，要求每帧只有一个不同的向量产生精确零 PCM，且整次 replay 可由同一对齐方式解释。改变载波幅度、低频符号和独立重复编号用于冻结后验证。

精确匹配只支持该 **可观测 LPC 模型**，不证明它是唯一的原生中间状态，更不等于找到了两级 LSF 原表。所有产物保持 `eligible_lsf=false`。未达到完整匹配的搜索也保存结果；不会对照、补齐或应用 LSF 数据。

早期双载波实验保留在 `bwe2_parallel_spectrum.py`。对已有连续采集的残差分析，可用 `bwe2_coherent_residual.py --binary ... --out ... --source ...` 复现；它只核验和读取已有 PCM、原始单位谱线控制，不新增解码。`bwe2_lpc_polynomial.py`、`bwe2_lpc_lattice.py` 和 `bwe2_fft_hypotheses.py` 是有界的数值假设诊断，其中的频率网格、递推顺序和局部搜索均不构成原始码本来源。

不同通道布局使用不同账本；变换布局不应被用来重置同一研究批次的预算。当前试验将前序账本的调用数及未压缩证据计费合并扣除，再为新布局分配剩余额度。原生调用仍串行，30 秒超时及磁盘、卷身份保护保持不变。

```sh
PYTHONPATH=scripts python3 -B -m unittest \
  test_bwe2_spectral_null test_bwe2_blackbox test_bwe2_lsf_probe -v
```

各重建命令在入口显式开启进程级参考数据读取保护；仅导入分析模块不会改变调用者的读取权限。保护测试在独立子进程中验证各命令入口，因此可与参考对照测试一起通过 `unittest discover` 运行。
