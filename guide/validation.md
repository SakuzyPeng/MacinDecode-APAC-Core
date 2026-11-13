# 验证范围

默认构建不开启 BWE2；活跃帧的拒绝行为属于验收契约。原有含 BWE2 的实验矩阵保留在 Research，不冒充本仓库默认支持范围。

## 来源与历史

```sh
python3 -B scripts/check_sources.py
python3 -B scripts/check_sources.py --history
```

检查清单内每份数值文件和测试输入的完整 SHA-256、数值来源类别、测量登记、零符号规则对应的消费者身份，以及禁入文件和私有路径。历史检查遍历全部可达对象。测试输入是人工码流和回归数据，不作为独立数学真值。

## 可移植回归

```sh
cargo +1.98.0 test --locked -p apac-core -p apac-container -p apac-research -p apac-cac -p apac-no-std-example -p apac-bwe2
cargo +1.98.0 test --locked -p mapac --test frame_parser --test config_parser
cargo +1.98.0 test --locked -p mapac --no-default-features --test frame_parser
cargo +1.98.0 clippy --locked --workspace --all-targets -- -D warnings
```

这些检查覆盖语法、数值表、状态回滚、定位、容器输出和黄金快照。BWE2 拒绝用例包含长短窗、参数复用、增益索引 0、全零输入、HOA transport 和内嵌 preroll。黄金快照只证明当前实现的回归稳定，不等同独立数学验收；无 BWE2 默认快照与旧 opt-in 快照分别保存。

`.github/workflows/build.yml` 在 Linux x64、Windows x64 和 macOS ARM64 上运行以上可移植检查。系统编解码器采集、原生媒体和声卡播放不属于该工作流。尚未运行的远端平台检查不得宣称通过。

数学表可通过 `scripts/generate_*_math.py --check` 从公式复现；AAC 表的公开来源见 [第三方记录](../THIRD_PARTY.md)。
