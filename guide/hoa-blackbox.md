# HOA 黑盒批处理

当前生产解码范围为零至三阶，salient 为一至三阶。下文保留一至十阶实验工具及历史流程，以便读取已有证据；它们不扩大生产支持范围。四至十阶字典不进入解码器构建，高阶生产来源登记的 `--write` 已停用。原始证据、冻结候选和不确定项继续保留，不将停用能力记作完成重建。

`scripts/hoa_blackbox.py` 使用公开 AudioConverter 回放接口重建一至十阶、六至九位量化的 salient 码表。支持 `mode1`、`mode2:0`、`mode2:1`、`mode3` 和 `mode4:0` 至 `mode4:3`。六位流程同时识别各自阶数的 mode 2／3 分组及四张 mode 4 矩阵；七至九位流程复用同阶已合格的六位矩阵和分组，分别输出 128、256、512 项码表候选。工具不会修改正式数据或执行提交、推送。支持某个阶数的测量不表示该阶已经完成重建；资格以该次冻结验证和最终对照为准。

原生采集需要 macOS 和已构建的 `apac-tool`。工具仅使用 Python 标准库；纯数学、调度和恢复测试通过假原生接口离线运行。已有原型与原始实验目录不需要移动。

## 启动与续跑

```sh
# 使用项目现有的共享 target，不创建独立构建缓存。
python3 -B scripts/hoa_blackbox.py run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-blackbox-batch-new

python3 -B scripts/hoa_blackbox.py status --out reports/hoa-blackbox-batch-new
python3 -B scripts/hoa_blackbox.py resume --out reports/hoa-blackbox-batch-new
```

独立校准、矩阵基向量、mode 3 通道顺序及验证用例支持 `--jobs 1` 至 `--jobs 4` 个原生采集进程；默认 1。前缀搜索、依赖前一步观测的分组识别及目标间先后关系保持顺序执行。使用相同输入时，结果仍按请求顺序验收和冻结，工作数不进入观测缓存键。

```sh
python3 -B scripts/hoa_blackbox.py run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-blackbox-parallel-new --jobs 4

# 续跑可以调整并发数；省略 --jobs 则沿用该批次保存的值。
python3 -B scripts/hoa_blackbox.py resume \
  --out reports/hoa-blackbox-parallel-new --jobs 2
```

并发数只控制采集调度，仍共用一个二进制、一个账本和一个内容寻址证据池。每个正在采集的请求按实际 PCM、输入和压缩中间文件预留空间，至少 2 MiB；回放输出上限按实际帧数、通道数以及回放元数据预留计算，再向上取整到 MiB。原生调用次数在派发前登记，重复输入在派发前合并。遇到调用次数上限时，先验收已预留的调用再停止；中断或验证失败会回收已派发任务，未验收的输出保留为未完成尝试。

`run` 要求输出目录不存在。默认包含全部八个目标，先完成 mode 1；三阶还会先复核 cluster 0。一阶、二阶的其他目标各自报告测量结果。可用 `--targets mode4:1 mode4:2 mode4:3` 或 `--targets mode2:0 mode2:1 mode3` 明确选择子集；不支持的目标在创建批次之前报错。mode 2 的两张码表与分组需要联合识别，指定其中任一目标都会将两者加入批次。

创建批次时用 `--order 1` 至 `10` 选择阶数，默认仍为三阶。输出通道数为 `(order+1)²`；一阶使用四个 salient 分量，其余阶数使用五个分量，每分量四带。四／五阶使用 profile 5、level 1，六阶使用 profile 5、level 2，七至十阶使用 profile 0、level 0。一至三阶默认输入保持原字节身份。TCE 数量按 `[5,10,16]` 逃逸编码。阶数、cookie 和通道布局参与观测身份，不能跨阶复用原始观测。

创建批次时用 `--quantization-bits 7`、`8` 或 `9` 选择精度，省略则仍为六位；精度在该批次内固定，续跑不改变它。cookie、定长编码、量化网格、符号总数和分类门槛一起按精度切换，不同精度的原始观测不会命中同一缓存键。

七至九位八张表的总工作量超过单批默认调用上限。可先验证 mode 1，再为 mode 2／3 和 mode 4 创建各自批次，并只读导入先导批次的观测以复用校准。每个批次保留自己的限额，不自动扩大预算：

```sh
python3 -B scripts/hoa_blackbox.py run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-q7-pilot --quantization-bits 7 --targets mode1 --jobs 4

python3 -B scripts/hoa_blackbox.py import-evidence \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-q7-mode23 --quantization-bits 7 \
  --targets mode2:0 mode2:1 mode3 --jobs 4 --evidence reports/hoa-q7-pilot
python3 -B scripts/hoa_blackbox.py resume --out reports/hoa-q7-mode23
```

mode 2 的正常长度验证在同一帧中同时检查两张表的全部输出系数，并通过单通道基向量、混合输入和留出谱线验证分组。两个目标的验证记录绑定两张冻结码表，共用这组观测；独立重复编号仍区分重复采集。复用同精度先导校准后，九位 mode 2 联合批次可在默认 4,096 次限额内完成。

七位可分为 mode 1、mode 2／3、mode 4 三批；八位的 mode 4 cluster 0–1 和 2–3 分为两批；九位则将 mode 2、mode 3 和四个 mode 4 cluster 各自分批。各后续批次只读导入本阶、本精度的先导原始观测以复用校准。

`resume` 使用原批次保存的二进制路径，也可显式提供内容相同的 `--binary`。完成的阶段读取冻结结果；中断阶段重新执行确定性分析，成功探针从缓存恢复，中断中的探针重新采集。已经明确失败的目标默认跳过，用 `--retry-failed` 明确重试。

每批默认最多新增 512 MiB 证据、调用原生解码器 4,096 次；这些限额由全部工作进程共同使用，不随并发数倍增。单次超时 30 秒，采集前须保留至少 1 GiB 空间。达到限额会保存进度并停止，不自动扩大预算或删除证据。

```sh
# 显式调整原批次的预算后续跑；已有调用次数不会清零。
python3 -B scripts/hoa_blackbox.py resume \
  --out reports/hoa-blackbox-batch-new \
  --max-evidence-mib 768 --max-native-calls 6000
```

`--min-free-mib` 只能把空间底线设为至少 1024 MiB。发现原生环境变化、证据损坏或共同校准失败时停止整批；单个目标的识别失败不会阻止其他独立目标。已知目标复核失败时，尚未开始的新 cluster 不会启动。

## 导入已有观测

```sh
python3 -B scripts/hoa_blackbox.py import-evidence \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-blackbox-batch-new \
  --evidence /path/to/mode1/run-001 /path/to/mode4/run-001

python3 -B scripts/hoa_blackbox.py resume --out reports/hoa-blackbox-batch-new
```

导入命令可创建尚不存在的批次，也能向现有批次补充观测。支持原型的 `run-001` 目录和本工具的批次目录，只导入成功的原始输入、PCM、公开属性和必要元数据。旧候选、矩阵估计、比对结论与旧命令都不会被用于重建或执行。

导入校验二进制、组件、系统版本、架构、帧布局、处理属性、输入和 PCM 摘要。通过后只读引用原文件，不复制或改写旧目录；因此原目录须保持可访问。其他批次的压缩证据同样只读引用。

工具或分析指纹变化时，不能直接续写旧候选。可以新建批次并导入旧批次的原始观测，使用新程序重新分析。单纯改变 Git 提交号不会使相同工具、输入和原生环境下的原始观测失效。

## 冻结与对照

所有目标共用 mode 0 校准；矩阵权重只由校准数据确定，并在矩阵测量前冻结。码表用固定长度的极端补全探测前缀树；矩阵由单比特差分识别方向，再用单位基向量与加权校准恢复。

六位 mode 2 通过逐比特扰动识别本阶全部输出通道的线上次序和全零码字长度，再恢复第一张表并识别第二张表开始的位置，不读取旧系数分组。mode 3 在目标包前加入无声的 mode 0 帧，把历史设置为已知值；根据系数绝对值识别码字，随后分别验证正负符号、零符号后的位流位置、不同初始值及跨帧累加。普通观测包含目标与收尾共两帧；mode 3 的准备帧使一次观测包含三帧或四帧，完整输入序列与输出长度均参与缓存身份。

三阶七至九位批次创建时，独立进程从摘要固定的已测六位原表中导出四张矩阵和三个分组，在 `results/_shared/priors.json` 冻结；导出结果不含任何 Huffman 码字。复用要求原生组件摘要和架构匹配。mode 2 使用新恢复的第一张同精度表生成前导零系数，再探测第二张表；mode 4 用已测矩阵的逆恢复系数。正常长度验证包含分组基向量、混合向量、幅度、补齐及留出谱线，确认这些复用关系仍成立。七至九位输出中的 `matrix_reused`／`group_reused` 和 `prior_sha256` 明确记录依赖，不将复用矩阵计为新的矩阵重建。

一阶和二阶从各自已完成的六位批次导出几何先验，不使用三阶矩阵。创建七至九位批次时传入 `--prior-evidence`，可一次提供多个六位证据目录：

```sh
python3 -B scripts/hoa_blackbox.py run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out reports/hoa-order1-q7-pilot --order 1 --quantization-bits 7 \
  --targets mode1 --jobs 4 --prior-evidence reports/hoa-order1-q6
```

先验导出在独立进程中校验六位原始证据、候选、验证和最终对照的绑定，只导出已合格的矩阵位模式、误差半宽和分组，不导出 Huffman 码字。只做 mode 1 时可省略先验；其他模式需要对应目标的合格几何数据。缺失、不合格、跨阶或组件身份不同的先验会在创建批次前被拒绝。

保留既有门槛：六至九位分别覆盖完整 64／128／256／512 符号码树，32 位搜索上限、矩阵条件数不超过 64、求逆残差不超过 `1e-10`，以及 8 倍安全系数、`5e-8` 半宽下限和 `<2.5e-7` 精度门槛。直接符号分类误差须小于对应量化步距的八分之一，坐标重复门槛按符号总数调整。正零和负零不能区分时保留为不确定项。验证使用独立重复编号，不能因缓存复用而省略需要独立采集的重复记录。

v12 对九、十阶使用经批准的条件数例外：无穷范数条件数上限为 128，并额外要求归一化 Gram 偏差不超过 `1e-3`。Gram 矩阵按各行平均平方范数归一化，检查其与单位阵之差的无穷范数。求逆残差、坐标重复与分类误差、矩阵精度和正负零要求均保持原值；一至八阶仍使用 64 上限。六位方向矩阵和高精度复用的矩阵都执行对应检查，冻结结果记录实际值与策略。没有指定阶数的底层求逆调用也保留 64 上限。

mode 1 在主谱线上逐通道检查正常包与 mode 0 校准 PCM 的字节一致性，覆盖全部符号、双幅度、固定种子混合输入及额外补齐。留出谱线另有 14 个独立验证用例：五个已校准符号在两档幅度下各一次，以及四个固定种子的混合输入。五个符号为量化编号 `0`、`L/4`、`L/2`、`3L/4`、`L−1`，其中 `L` 是符号总数。每个输出通道的 RMS 误差不得超过保守量化步距的八分之一；步距由该谱线的半幅度 mode 0 波形计算，并扣除校准误差。留出谱线同时记录 PCM 是否逐位相同，允许分类门槛内的 Float32 舍入差异。这项检查不改变码字、矩阵位模式或矩阵精度门槛。

重建进程拒绝访问旧 salient 表、调试跟踪工具和比对文件。正常执行在候选冻结且验证通过后启动独立比对进程；也可单独运行：

```sh
python3 -B scripts/hoa_blackbox.py compare --out reports/hoa-blackbox-batch-new
```

比对进程只检查所选产物，不把参考码值传回重建器。`eligible_codebook` 与 `eligible_matrix` 分别记录是否满足后续来源替换条件；mode 2／3 的码表还须通过系数分组对照。矩阵部分完成不等于码表失败。工具不会自动接入这些结果。

已经冻结的比对报告在续跑时按候选和验证摘要复用，保持当时的参考版本。正式来源迁移或其他操作改变参考文件后，不覆盖旧比对报告；要对照新参考版本，应创建新批次并导入原始观测。

## 证据与故障恢复

SQLite 账本记录任务、逻辑探针、原生调用尝试及冻结阶段。输入字节、解码参数和原生环境决定缓存键；独立重复编号区分需要重新采集的验证。每次调用前落盘开始记录，完整验证并持久化结果后才登记成功。

新证据按未压缩内容的 SHA-256 去重并无损 gzip 压缩；压缩副本通过校验后才移除临时原件。输入、原始 PCM、公开属性、stdout/stderr 和返回状态均保留。中断或失败目录留存，不能当作成功观测；成功回执已写入而账本尚未更新的情况可恢复。

`results/` 下的 JSON 是冻结阶段的可读视图，`objects/` 保存唯一的压缩内容，`attempts/` 保存尚未清理的失败或中断尝试。`manifest.json` 记录身份与预算；`summary.json` 在正常批次结束时写入，运行期间以 `status` 为准。

同一批次只允许一个账本与证据池写入者，原生采集进程仅写入各自的临时尝试目录，状态查询只读。SQLite 写入、压缩、去重和成功回执都在主线程完成。续跑与对照会检查成功观测的摘要；已损坏或丢失的外部记录不会被静默重采样替代。命令返回 0 表示成功，非零时读取 JSON 中的停止原因和各目标状态。

```sh
PYTHONPATH=scripts python3 -B -m unittest test_hoa_blackbox -v
```

所有测量目录保留在被忽略的 `reports/` 中。研究结论与代码、工具、策略和组件身份另记独立文档仓库；正式数据来源迁移使用相应生成器单独处理。


## 四至十阶长任务

`campaign-run`、`campaign-resume` 和 `campaign-status` 管理四至十阶的全部八个目标及六至九位精度。默认范围为 224 张码表、28 张矩阵；不会顺带测量其他字典。`campaign-run --orders 7 8 9 10` 可声明升序且不重复的阶数子集，适用于先导已完成后分版本继续测量；范围在创建后固定，每个所选阶数仍须覆盖 32 张表和四张矩阵。输出可以放在外置盘，以下路径均由使用者提供。

```sh
python3 -B scripts/hoa_blackbox.py campaign-run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out /path/to/evidence/hoa-campaign --jobs 4 \
  --max-total-native-calls 1000000 --max-total-evidence-mib 262144 \
  --min-free-mib 10240

python3 -B scripts/hoa_blackbox.py campaign-status --out /path/to/evidence/hoa-campaign
python3 -B scripts/hoa_blackbox.py campaign-resume --out /path/to/evidence/hoa-campaign
```

针对条件数例外，先创建九阶开头的受控先导：

```sh
python3 -B scripts/hoa_blackbox.py campaign-run \
  --binary /path/to/shared-target/debug/apac-tool \
  --out /path/to/evidence/hoa-conditioning-campaign --orders 9 10 \
  --conditioning-pilot --evidence-campaign /path/to/previous-campaign \
  --max-total-native-calls 50000 --max-total-evidence-mib 32768
```

预算应从跨版本总额度中扣除旧消耗后分配。该命令在九阶六位 cluster 0 完成观测、候选冻结、验证和独立对照后返回 `pilot_complete`，先导收据绑定冻结阶段摘要。检查收据后用 `campaign-resume` 继续其他目标；中断续跑仍须先生成合格收据，不能跳过先导。精度失败、非零值歧义或对照差异会停止扩展。仅有正负零未确定时，可继续其他独立测量，原矩阵仍不具备整张逐位接入资格。工具指纹变化时创建新根，只读导入原始观测，保留旧版的失败记录。

默认的四至十阶完整流程先校准并验证四阶 mode 0 的全部通道和 overlap，再复核六位 mode 1 与 mode 4 cluster 0。先导失败时停止推广；通过后逐阶完成六位几何及七至九位码表。每阶完全合格后返回 `awaiting_application`，由独立来源接入步骤处理，随后续跑下一阶。目标失败或矩阵部分确定与缺失先验分别报告；不能把部分结果记作全阶通过。

证据池和观测索引在长任务根目录共享，各阶和精度保留自己的分析账本。每个预定采集分片最多新增 128 次探针尝试，失败和中断也计数；正常分片完成后继续下一片。`limits_scope=planned_shard` 表示原有 4,096 次调用及 512 MiB 限额适用于每个采集分片，不是该阶全部精度的合计。长任务另有累计调用和证据上限，默认 100 万次及 256 GiB；实际预算耗尽会停止，不能换一个分片重置消耗。并发预留计入资源检查。

空间底线可提高，不能降到 1 GiB 以下。外置盘断开、设备身份变化、原生环境改变或证据损坏时停止，不在原路径下改用其他磁盘继续采集。重新运行前须恢复原证据并核验身份；未完成尝试会重采，成功观测不会重复调用。原有批次命令拒绝直接修改长任务所属批次，独立对照子进程继承长任务写锁。

外置盘重新挂载后，macOS 可能分配新的设备号。可在原盘正常连接时，把卷 UUID 和长任务身份固定在本机的 `reports/`；重连后使用同一记录校验。校验器拒绝其他卷、有活动写入者或损坏的证据，完整检查共享对象、成功观测、生产者快照及冻结阶段后，才允许显式更新易变的挂载设备号。它不改变原生身份、候选、调用计数或预算；原生环境仍由下一次采集单独核验。

```sh
# 原盘连接正常时保存身份；身份文件留在内置盘。
python3 -B scripts/verify_hoa_campaign_storage.py \
  --out /path/to/evidence/hoa-campaign \
  --identity reports/hoa-volume-pin.json --pin-current

# 断开后须先停止旧采集进程；重连原盘后校验并更新设备号。
python3 -B scripts/verify_hoa_campaign_storage.py \
  --out /path/to/evidence/hoa-campaign \
  --identity reports/hoa-volume-pin.json --write-rebind
```

大量证据使 SQLite 统计变慢时，可以在采集进程停止后运行 `python3 -B scripts/index_hoa_blackbox_evidence.py --out /path/to/evidence/hoa-campaign` 添加覆盖索引。该操作持有长任务写锁并预留磁盘空间，只改变数据库索引，不改动观测、候选或预算。

工具指纹变化后可新建长任务，并用 `campaign-run --evidence-campaign /path/to/previous-campaign` 只读导入兼容的原始观测。不会复制 PCM、导入候选、执行旧命令或改写旧记录；已耗尽预算的旧长任务不能用该方式绕过限额。跨版本的累计资源另在研究报告汇总。

长任务、补测和外置存储的离线回归可一起运行，不需要原生组件或目标旧表：

```sh
PYTHONPATH=scripts python3 -B -m unittest test_hoa_blackbox_campaign test_hoa_blackbox_coordinate test_hoa_blackbox_import_pool test_hoa_campaign_storage -v
```

高阶登记器目前只允许历史证据的只读准备与核查；`--write` 会在读取证据或写入源文件之前明确拒绝：

```sh
python3 -B scripts/register_hoa_measured_campaign.py \
  --campaign /path/to/evidence/hoa-campaign --order 10 \
  --out reports/hoa-order10-registration-review.json
```

准备过程仍核对冻结顺序、候选摘要、四种精度的语义摘要和矩阵资格，并要求 mode 1 的全部符号、双幅度混合输入、补齐及 14 项正常包留出谱线检查完整。生成的准备报告不代表当前生产构建接受该阶数。历史 `applied_pending_validation` 状态也不改变目前的三阶上限。工具不提交或推送更改。

## 保留零符号歧义的独立码表补测

当六位 mode 4 的码表已验证并对照一致，而原矩阵只有正负零未确定时，`scripts/hoa_blackbox_coordinate.py` 可以单独补测七至九位码表。它复用的是六位单比特扰动所得的比例变换及其逆，原矩阵仍保留未确定状态。导出阶段不向重建器提供原矩阵位模式或 Huffman 码字；矩阵条件数、逆残差和坐标分类门槛保持不变。其他类型的矩阵资格失败不会自动转入这条路径。

原主流程必须已经冻结本阶六位方向观测，以及所选高精度的 mode 0 校准。下面命令会自动选择该阶满足条件的 cluster，只读引用原始校准证据，并采集独立的正常码流验证。默认一个采集进程，每个预定分片仍至多 128 次新尝试，单次超时 30 秒。

```sh
python3 -B scripts/hoa_blackbox_coordinate.py run --binary /path/to/shared-target/debug/apac-tool --source-campaign /path/to/primary --out /path/to/supplement --order 4 --quantization-bits 7 --jobs 1
python3 -B scripts/hoa_blackbox_coordinate.py status --out /path/to/supplement
python3 -B scripts/hoa_blackbox_coordinate.py resume --out /path/to/supplement
```

同一补测目录可以通过新的 `run --order ... --quantization-bits ...` 加入其他任务，成功观测和共享对象直接复用。`complete` 只表示已声明的补测任务完成，不表示矩阵已逐位恢复或数据已接入。原矩阵候选不会被重写，也不会因码表通过而自动获得接入资格。

补测有独立的工具指纹、冻结候选、验证和最终对照。默认总预算为 25 万次调用和 64 GiB，支持 `--max-total-native-calls`、`--max-total-evidence-mib`、`--min-free-mib`。主流程与补测的额度分别计数；合并运行时须在既定总预算内分配，且例如主流程三进程、补测一进程，保持总并发不超过四。外置盘身份校验工具也支持补测目录。
