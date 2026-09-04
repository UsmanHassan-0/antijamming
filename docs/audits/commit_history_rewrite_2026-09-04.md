# Commit-history classification rewrite — 2026-09-04

## Scope and invariant

The `main`, `per-prn-fifo-experimental`, and
`cleanup/no-usrp-verification-20260831` project histories were rewritten so
every commit subject begins with a Conventional Commit type. The allowed types
are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`,
and `revert`.

This was a commit-metadata rewrite. At the rewrite checkpoint, before this
audit record was added, the old and new tree IDs matched exactly:

| Branch | Tree ID |
| --- | --- |
| `main` | `cfd4f8980f13343908989d2b9ba8c89371da1181` |
| `per-prn-fifo-experimental` | `724d5ee3eb46c61fe77df902d04b0097432a8c27` |
| `cleanup/no-usrp-verification-20260831` at `50b94f1` | `d24d8fefc175161e1fffe485b994a1010ca90ebb` |

Historical run records intentionally retain the commit IDs that actually
existed when those runs occurred. Use the map below to locate the tree-equivalent
classified commit. The original tips are retained locally under
`refs/archive/antijamming/` so the provenance chain remains recoverable.

## Commit map

| Original | Classified | Original subject | Classified subject |
| --- | --- | --- | --- |
| `4f352d0` | `f30234d` | Initialize anti-jamming realtime receiver | feat: initialize real-time anti-jamming receiver |
| `4a6ebfe` | `d2387fd` | Implement realtime anti-jamming receiver pipeline | feat: implement real-time anti-jamming receiver pipeline |
| `5ad8276` | `2fa56fe` | Preserved cosntraint for blade rf to a certain extent | feat: add protected-bearing LCMV constraint |
| `6242d36` | `7adbdb4` | Harden measured-U1 LCMV continuity and run evidence | fix: harden measured-U1 LCMV continuity |
| `78d81f9` | `1ac4352` | Fix GNSS telemetry and carrier-phase continuity | fix: preserve GNSS telemetry and carrier-phase continuity |
| `435945a` | `966568d` | Add shared measured-U1 phase-continuous GNSS fanout | feat: add phase-continuous shared-U1 GNSS fanout |
| `65d845d` | `2c2af1c` | Optimize shared U1 realtime pipeline | perf: optimize shared-U1 real-time pipeline |
| `3419536` | `23291cb` | Make shared U1 protection dynamic and phase-safe | feat: make shared-U1 protection dynamic and phase-safe |
| `d7cfdd8` | `849db1e` | diagnostics: bound and identify GNSS FIFO stalls | fix: bound and identify GNSS FIFO stalls |
| `ec5a5e9` | `11191da` | fix: remove global GNSS FIFO clock backpressure | fix: remove global GNSS FIFO clock backpressure |
| `cdd6ceb` | `655a2a4` | docs: record failure provenance and regression gates | docs: record failure provenance and regression gates |
| `10bfda6` | `52b4aab` | docs: add host-stack and sanitizer evidence | docs: add host-stack and sanitizer evidence |
| `7d47850` | `e585297` | test: stress GNSS FIFO failure boundaries | test: stress GNSS FIFO failure boundaries |
| `0672377` | `5c2a84b` | fix: serialize and bound UHD receive lifecycle | fix: serialize and bound UHD receive lifecycle |
| `f211b1f` | `ade0c91` | Remove source_estimate_gap and effective_rank algorithms and suspicious_source_structure veto | refactor: remove obsolete DoA veto heuristics |
| `d256e61` | `ef5688f` | Simplify LCMV UI: rename to "LCMV Status", remove null-bearing label, add C/N0 fallback | refactor: simplify LCMV status presentation |
| `9a23c86` | `2acf15c` | Implement per-PRN preservation and reduce GNSS FIFO overhead | feat: add efficient per-PRN GNSS preservation |
| `ac82a9c` | `bd85f39` | diagnostics: bound and identify GNSS FIFO stalls | fix: bound and identify GNSS FIFO stalls |
| `7d258c5` | `5c337f2` | fix: remove global GNSS FIFO clock backpressure | fix: remove global GNSS FIFO clock backpressure |
| `eb03220` | `4263fd6` | test: model nonblocking GNSS FIFO fanout | test: model nonblocking GNSS FIFO fanout |
| `506ed18` | `9817e5c` | fix: serialize and bound UHD receive lifecycle | fix: serialize and bound UHD receive lifecycle |
| `696a7e1` | `96023d5` | fix: invalidate stale per-PRN beamformer state | fix: invalidate stale per-PRN beamformer state |
| `a5d632d` | `eaa34e4` | fix: retire stale desired-vector epochs | fix: retire stale desired-vector epochs |
| `0b90162` | `b60344f` | feat: add jammer-first spatial acquisition | feat: add jammer-first spatial acquisition |
| `dd9e9c1` | `94273ab` | Clean hardware-free runtime and lifecycle issues | fix: correct hardware-free runtime lifecycle issues |
| `2d9ac8e` | `5ceaefa` | Harden hardware-free runtime cleanup and evidence | fix: harden hardware-free runtime lifecycle |
| `9265a3c` | `be24dfe` | Remove stale runtime interfaces and state | refactor: remove stale runtime interfaces and state |
| `061e12f` | `4357fcb` | Complete semantic runtime cleanup | refactor: complete semantic runtime cleanup |
| `d7e4843` | `2826c50` | Record Spark hardware validation | docs: record Spark hardware validation |
| `06df103` | `45ca8cc` | fix: finalize GNSS FIFO lifecycle evidence | fix: finalize GNSS FIFO lifecycle evidence |
| `2aaab23` | `3336f3a` | docs: record Spark synchronization gate | docs: record Spark synchronization gate |
| `2bdb611` | `b0aaa3c` | test: record bounded sample-rate sweep | test: record bounded sample-rate sweep |
| `adc12c2` | `474a838` | docs: audit jammer latch release behavior | docs: audit jammer latch release behavior |
| `86eca71` | `17ec365` | Release LCMV protection after jammer evidence clears | fix: release LCMV protection after jammer evidence clears |
| `188800a` | `d462188` | Clarify GNSS multi-FIFO and phase boundaries | docs: explain GNSS multi-FIFO and phase boundaries |
| `41dc526` | `335ee7c` | Clarify GNSS source alignment ownership | docs: explain GNSS source alignment ownership |
| `b0ae54f` | `c2611a9` | Document LCMV release software verification | docs: record LCMV release software verification |
| `60f93ce` | `50b94f1` | docs: define external RF integration boundary | docs: define external RF integration boundary |

## Verification boundary

- Every unique commit reachable from the three active project branches passed
  the configured subject-prefix check.
- Each rewritten branch checkpoint was diffed against its archived original;
  all three comparisons were byte-identical. The cleanup branch then added
  this audit record as a separate `docs:` commit.
- This proves subject classification and final-tree identity. It does not
  re-run historical RF experiments or reclassify the technical validity of
  their conclusions.
