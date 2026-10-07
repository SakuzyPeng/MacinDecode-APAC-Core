# HOA 空间控制均值测量

`scripts/hoa_mean_blackbox.py` 使用公开 AudioConverter PCM 恢复 121 项 HOA 空间控制均值。实验限定 48 kHz、长窗、十阶 ACN/SN3D、九位 mode 0 和关闭 DRC；三阶及九阶用于独立验证共享前缀。工具输出本地候选和证据，不修改正式数据。

## 运行与阶段

需要 macOS、已有的 `mapac` 和已挂载的证据卷。继续使用项目的共享构建目录，无需额外构建。以下路径均由调用者提供：

```sh
python3 -B scripts/hoa_mean_blackbox.py run \
  --binary /path/to/shared-target/debug/mapac \
  --volume /path/to/evidence-volume \
  --out /path/to/evidence-volume/hoa-spatial-means-v1

python3 -B scripts/hoa_mean_blackbox.py status \
  --out /path/to/evidence-volume/hoa-spatial-means-v1

python3 -B scripts/hoa_mean_blackbox.py resume \
  --out /path/to/evidence-volume/hoa-spatial-means-v1

# 候选冻结并完成验证后，单独启动最终对照进程。
python3 -B scripts/hoa_mean_blackbox.py compare \
  --out /path/to/evidence-volume/hoa-spatial-means-v1
```

`run` 要求输出目录尚不存在。`run` 和 `resume` 默认完成验证，可用 `--until calibration` 或 `--until candidate` 在对应冻结阶段停止。`compare` 只接受已有候选和验证记录；未确定项与不一致项均保留，不反馈到恢复算法。

## 方法与资格

均值直接加到各输出通道的每条谱线上。校准用公开 AAC 表构造覆盖全部 1024 条谱线的平坦载波，通过 mode 0 的精确二进制系数组合产生已知幅度；仅改变 cookie 的 `flag_a` 即可观察均值开关。

初始 PCM 投影给出近似值，随后以五个载波的 256 进制分解编码候选的相反数。它们的幅度和 mode 0 系数均为二的幂或其精确倍数。按幅度从高到低抵消，使用剩余 PCM 修正候选，最多六轮。搜索没有假设十进制网格；源码访问保护拒绝加载旧 HOA 表及使用旧表的测试生成器。

候选冻结后，每项必须通过新的解码器重复抵消和额外补齐，输出精确为零；两侧相邻 Float32 候选必须产生符号及大小正确的非零残差。另将载波挖去第 1、37、511 条谱线，与关闭均值后的独立单谱线控制比较，再做三阶、九阶三帧的抵消验证。报告记录逐位 PCM 一致性；非零残差及谱线验证的相对容差为 `2e-6`，零输出检查不使用容差。

该方法的精确载波最小网格为 `2^-48`。无法编码的值、分类失败、投影形状不符或不能区分的相邻值均停止，不靠参考表补齐。正负零无法区分时保留两个候选，不能把该项标记为已确定。

## 证据与续跑

工具保存输入包、cookie、PCM、返回状态、公开处理属性、源码快照和环境身份。SQLite 在派发前登记尝试；全部输出通过校验并写入按内容寻址的无损 gzip 对象后，才将观测记为成功并移除其临时原件。候选、验证和对照按顺序冻结在 `results/spatial-means/`。源码、二进制或组件变化时停止，不覆盖旧结果。

成功探针可复用，独立重复采集使用不同编号。未完成尝试在续跑时重新采集；失败记录和已有证据保留。完成后的空续跑不增加原生调用，也不改变冻结产物。

生产者修复后，可新建批次并只读引用兼容的原始观测，不导入旧候选或执行旧命令：

```sh
python3 -B scripts/hoa_mean_blackbox.py run \
  --binary /path/to/shared-target/debug/mapac \
  --volume /path/to/evidence-volume \
  --out /path/to/evidence-volume/hoa-spatial-means-v2 \
  --import-evidence /path/to/evidence-volume/hoa-spatial-means-v1
```

导入会检查原始证据和原生环境，递归保留先前证据目录的资源消耗。旧目录必须保持可访问；新目录不会复制其 PCM，导入不清零调用或证据预算。

## 资源与外置卷

本工具串行采集，每个完整实验默认最多 4,096 次新原生调用、512 MiB 证据，单次超时 30 秒，至少保留 1 GiB 可用空间。派发前预留完整 PCM、输入和压缩临时空间；达到限制时保存现有记录并停止。

开始时固定证据卷 UUID，本次打开还固定设备号。断盘或挂载被替换时停止，不在系统盘创建替代缓存。重新挂载后，`resume` 核对 UUID 和全部成功证据再恢复；不能删除或改名原证据目录来绕过检查。

纯模拟和假原生接口测试不读取目标均值：

```sh
PYTHONPATH=scripts python3 -B -m unittest test_hoa_mean_blackbox -v
```

## 正式测量来源

`data/hoa-spatial-means-measured-v1.json` 保存已合格的 121 项 Float32 位模式及测量来源。Rust 构建直接读取该文件并校验固定摘要，原 `hoa-spatial-controls-format-v1.json` 中的数组是需要一致核对的副本。原语义摘要保持不变；子带表保留原有来源。

```sh
python3 -B scripts/generate_hoa_spatial_means_measured.py --check

# 从已登记摘要的冻结候选及完整验证、对照重新生成正式来源。
python3 -B scripts/generate_hoa_spatial_means_measured.py --write \
  --candidate /path/to/frozen/candidate.json \
  --validation /path/to/frozen/validation.json \
  --comparison /path/to/frozen/comparison.json
```

不提供证据参数时，生成器使用仓库内的合格原表；`--write` 重建格式副本及来源说明。三个证据参数必须一起提供，不接受未登记、不完整或有歧义的候选。正常构建和核验无需苹果组件、外置盘或研究目录。

均值专用的几何与码流支持位于 `hoa_mean_blackbox_wire.py`，不依赖尚未接入的高阶码表批处理。工具整理改变生产者指纹时，旧实验身份保持不变；使用原始证据导入在新目录复核，不改写旧冻结产物。
