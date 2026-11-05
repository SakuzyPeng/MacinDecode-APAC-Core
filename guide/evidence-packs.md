# 实验证据合并存储

`scripts/pack_evidence.py` 将已有 `objects/<sha256>.gz` 无损合并为
`objects/.packs/pack-*.bin`，同目录的 `index.sqlite3` 保存对象原文件名、
偏移、压缩长度、原始长度和压缩数据校验和。每个文件保持原 gzip 字节，
不重新压缩，不改写原实验账本、原始内容哈希、冻结结果或跨实验引用。

```sh
python3 -B scripts/pack_evidence.py /path/to/evidence
python3 -B scripts/pack_evidence.py /path/to/evidence --verify-only
```

合并前停止所有相关采集程序，包括将本地账本和外置证据分开保存的 BWE2
程序。工具获取证据树内已有的 writer/runner 锁；位于树外的采集锁需由操作者
确认没有使用。默认每块最多 256 MiB 或 10,000 个对象，逐块操作，并保留
至少 1 GiB 余量。检测到卷身份变化、内容损坏或文件被修改时立即停止。

写入顺序为：验证源对象解压后的 SHA-256，写入和同步块文件，重新读取并
验证压缩字节，持久化索引，最后移除已验证的小文件。中断后重新执行同一命令
即可继续。索引已提交而小文件尚未移除时，会比较两份内容后完成清理。

更新后的 HOA/BWE2 读取、导入、重新分析和卷恢复验证工具通过
`scripts/evidence_packs.py` 解析原来的逻辑路径；新采集的松散对象可以与块文件
共存。原始数据库中的 `.gz` 路径不再一定对应一个独立文件，不能只用 Finder
或 `Path.exists()` 判断证据是否存在。旧版本脚本需要先恢复所需的小文件。

单独读取或恢复一个对象（恢复时会额外占用空间）：

```python
import gzip
from pathlib import Path
from evidence_packs import read_gzip_object

path = Path('/path/to/evidence/objects/' + object_sha256 + '.gz')
raw = read_gzip_object(path)
assert __import__('hashlib').sha256(raw).hexdigest() == object_sha256
# 仅在需要供旧程序使用时恢复，不覆盖已有文件。
with path.open('xb') as stream:
    stream.write(gzip.compress(raw, mtime=0))
```

运行该示例时将仓库的 `scripts` 目录加入 `PYTHONPATH`。备份和移动证据时，
须连同 `.packs` 及其索引一起保留，并维持原有跨实验目录关系。
打包不会解除既有工具/分析指纹约束；改版工具仍应通过导入原始观测建立新批次。
