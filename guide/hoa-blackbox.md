# HOA 黑盒批处理

`scripts/hoa_blackbox.py` 使用公开 AudioConverter 回放接口重建三阶、六位或七位量化的 salient 码表。支持 `mode1`、`mode2:0`、`mode2:1`、`mode3` 和 `mode4:0` 至 `mode4:3`。六位流程同时识别 mode 2／3 分组及 mode 4 矩阵；七位流程复用已合格的六位矩阵和分组，输出新的 128 项码表候选。工具不会修改正式数据或执行提交、推送。

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

并发数只控制采集调度，仍共用一个二进制、一个账本和一个内容寻址证据池。每个正在采集的请求预留 2 MiB 空间，原生调用次数在派发前登记，重复输入在派发前合并。遇到调用次数上限时，先验收已预留的调用再停止；中断或验证失败会回收已派发任务，未验收的输出保留为未完成尝试。

`run` 要求输出目录不存在。默认包含全部八个目标，先完成 mode 1 与 cluster 0 的复核，再开始其余目标。可用 `--targets mode4:1 mode4:2 mode4:3` 或 `--targets mode2:0 mode2:1 mode3` 明确选择子集；不支持的目标在创建批次之前报错。mode 2 的两张码表与分组需要联合识别，指定其中任一目标都会将两者加入批次。

创建批次时用 `--quantization-bits 7` 选择七位，省略则仍为六位；精度在该批次内固定，续跑不改变它。cookie、定长编码、量化网格、符号总数和分类门槛一起按精度切换，六位与七位原始观测不会命中同一缓存键。八位和九位尚未开放。

七位八张表的总工作量可能超过单批默认调用上限。可先验证 mode 1，再为 mode 2／3 和 mode 4 创建各自批次，并只读导入先导批次的观测以复用校准。每个批次保留自己的限额，不自动扩大预算：

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

六位 mode 2 通过逐比特扰动识别 16 个输出通道的线上次序和全零码字长度，再恢复第一张表并识别第二张表开始的位置，不读取旧系数分组。mode 3 在目标包前加入无声的 mode 0 帧，把历史设置为已知值；根据系数绝对值识别码字，随后分别验证正负符号、零符号后的位流位置、不同初始值及跨帧累加。普通观测包含目标与收尾共两帧；mode 3 的准备帧使一次观测包含三帧或四帧，完整输入序列与输出长度均参与缓存身份。

七位批次创建时，独立进程从摘要固定的已测六位原表中导出四张矩阵和三个分组，在 `results/_shared/priors.json` 冻结；导出结果不含任何 Huffman 码字。复用要求原生组件摘要和架构匹配。mode 2 使用新恢复的第一张七位表生成前导零系数，再探测第二张表；mode 4 用已测矩阵的逆恢复系数。正常长度验证包含分组基向量、混合向量、幅度、补齐及留出谱线，确认这些复用关系仍成立。七位输出中的 `matrix_reused`／`group_reused` 和 `prior_sha256` 明确记录依赖，不将复用矩阵计为新的矩阵重建。

保留既有门槛：六位／七位分别覆盖完整 64／128 符号码树，32 位搜索上限、矩阵条件数不超过 64、求逆残差不超过 `1e-10`，以及 8 倍安全系数、`5e-8` 半宽下限和 `<2.5e-7` 精度门槛。直接符号分类误差须小于对应量化步距的八分之一，坐标重复门槛按符号总数调整。正零和负零不能区分时保留为不确定项。验证使用独立重复编号，不能因缓存复用而省略需要独立采集的重复记录。

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
