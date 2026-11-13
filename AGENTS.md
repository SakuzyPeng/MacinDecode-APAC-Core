# Repository conventions

- Reuse one Cargo target directory; avoid duplicate caches and large worktrees.
- Keep generated reports, build products, raw media and research evidence outside Git.
- Do not add original BWE2 LSF tables, Apple binaries, disassembly or private acquisition tools.
- BWE2 remains disabled by default. Active BWE2 must return `bwe2-unavailable` when unavailable, including state scans and preroll.
- Preserve frozen numerical order, measured-source identities and signed-zero qualification boundaries.
- Keep user documentation in README.md and guide/. Use generic paths in examples.
- Run the source audit and relevant portable tests before publishing changes. Run Python with `-B`.
- The Research repository and its evidence/history are separate; never rewrite them as part of a release change.
