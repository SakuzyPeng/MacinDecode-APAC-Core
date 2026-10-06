# BWE2 公开接口测量

`scripts/bwe2_blackbox.py` 通过现有 `apac-tool replay --processing-policy drc-off` 测量 BWE2。工具使用受控的 AAC 频谱输入、公开 PCM 和属性回读，恢复 64 项激励增益候选，并检查两级 LSF 码本的可识别性。它不会应用正式数据、提交或推送。

采集需要 macOS、已构建的 `apac-tool`、Python 和 NumPy。NumPy 只用于离线数学分析，不增加 Rust 构建或产品运行依赖。输入生成器只加载公开来源的 AAC 表；重建进程拒绝打开原 BWE2 字典及其参考提取器。

## 采集与恢复

新批次需要一个本地账本目录和位于已挂载外置卷上的全新证据目录。以下命令从代码仓库根目录运行，二进制使用已有共享构建目录：

```sh
python3 -B scripts/bwe2_blackbox.py pilot \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new \
  --mount /Volumes/cache \
  --evidence /Volumes/cache/apac-evidence/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py gains \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new --seeds 8

python3 -B scripts/bwe2_blackbox.py anchors \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new --seeds 64

python3 -B scripts/bwe2_blackbox.py refine-gains \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new --seeds 64

python3 -B scripts/bwe2_blackbox.py freeze \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py validate \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new
```

每个阶段输出含摘要的 JSON 路径。`pilot` 校准 PCM 到频谱的映射，使用功率梳状输入及稳定、首项为一的滤波器分解估计绝对增益。`gains` 扫描全部索引，`anchors` 与 `refine-gains` 使用两组固定 LSF 索引、独立符号序列和分组重复改进尺度与高增益精度。

`freeze` 使用从 PCM 估计中推断的 `0.00001` 十进制网格。它明确记录该假设，保留连续估计、经验误差和全部 Float32 候选；不能把该假设解释为无条件确定原始任意 Float32 数据。只有区间内对应一个位模式才标记该项确定。高增益项通过多组独立锚点的共同尺度约束判定，原始重复离散程度仍保留。

`validate` 在冻结之后采集另一组 LSF、两档载波幅度、两种复制区间、四个新符号序列，共 512 项逐索引验证及八项绝对尺度控制。独立重复编号绑定候选摘要，防止新候选把冻结前的观测当作留出验证。

## LSF 可识别性

```sh
python3 -B scripts/bwe2_blackbox.py lsf-probe \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new

python3 -B scripts/bwe2_blackbox.py lsf-result \
  --binary /path/to/shared-target/debug/apac-tool \
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
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/bwe2-blackbox-new

PYTHONPATH=scripts python3 -B -m unittest test_bwe2_blackbox -v
```

重复同一阶段命令即续跑：已有完整阶段先核验原始证据，再返回原冻结文件；未完成阶段复用成功的原始观测。`--reanalyze` 保留旧文件并创建新分析版本；若产生新候选，其留出验证需要新的独立编号，不能用旧时间顺序通过验收。扩展 seed 数量会采集缺失的请求。更改原生环境不允许沿用该批次。

SQLite、冻结 JSON、工具快照和分析失败日志保存在本地 `--out`。输入、无损压缩 PCM、公开属性、返回状态和成功回执保存在外置证据目录。每个对象按未压缩内容的 SHA-256 校验；只有压缩副本通过逐字节核验后才移除临时原件。失败和中断文件保留。`audit` 核对全部对象、请求身份、公开输出契约及账本与持久化回执的一致性。

每批上限为 4,096 次调用、512 MiB 新证据，单次超时 30 秒，两个位置均保留至少 1 GiB 空间。证据预算按去重后的未压缩对象计费，失败或中断的临时目录继续占用预留额；报告另列实际磁盘占用。原生调用串行运行，同一批次只有一个写入者，`status` 只读。

卷 UUID 在启动及阶段完成时校验，采集期间检查挂载和文件系统身份。外置卷断开即停止，不在挂载点下新建本地替代目录。重连后重新核验相同卷及原生身份，再运行 `audit` 和未完成阶段。当前工具没有自动提高限额或搬迁证据的入口。
