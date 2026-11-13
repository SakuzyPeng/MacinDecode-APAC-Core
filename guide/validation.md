# 验证范围

默认构建关闭 BWE2。固定矩阵中的活跃 BWE2 输入按拒绝契约核对首个出错包和位位置，关闭路径继续验证独立数学和阶段摘要。

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

## CI 完整验收

日常推送和 PR 在 Linux x64 上执行来源、覆盖清单、CI 门禁、打包、Rust 回归、PCM 容器输出和 Clippy 检查。

手动勾选 `full_validation`、指定 `release_tag` 或推送 `v*` 标签会启动三平台 debug／release 验收。Linux release 计算独立数学参考，另外五个构建收集阶段摘要；固定的 `ci-fast` 和 `ci-full` 清单绑定输入、数量及覆盖身份。完整数学任务分九片运行。

`check_ci_consistency.py` 核验六个构建的代码、源码、编译器、清单及阶段摘要，所有上游成功后才通过。缺项、重复、失败、跳过、来源漂移和摘要差异均使验收失败。BWE2 拒绝记录保留原输入身份和明确的错误信息，完整数值恢复继续按默认关闭的范围处理。

三个平台的开发包在完整构建运行中生成。版本发布草稿还须通过来源／历史审计、版本一致性、校验和及远端标签指向检查。

系统参考采集和真实媒体检查由 Research 中的独立流程记录。

数学表可通过 `scripts/generate_*_math.py --check` 从公式复现；AAC 表的公开来源见 [第三方记录](../THIRD_PARTY.md)。
