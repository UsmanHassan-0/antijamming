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

## 2026-09-07 amendment — cleanup implementation rewrite

Unlike the metadata-only rewrite above, this user-requested rewrite changes
behavior: the ideal-null LCMV implementation is removed in the earlier cleanup
commit. The common target now uses the same measured-U1 solve as protection.
`full_angle_analysis` is schema version 4. Old measured runs are **not** evidence
for these changed trees; original hashes and measurements remain in the dated
records. The experimental branch and main are not part of this rewrite.

| Previous cleanup commit | Rewritten cleanup commit |
| --- | --- |
| `4357fcb0308358e8e9b44d35d276defa140222b1` | `a7cb0bcb9b0146dab5732eb98c7a7357e20e9225` |
| `2826c50e862d270890c3c97fa0e27c05b78f3001` | `f6311c4a382afed2045ff3f1243621ee194a5cc3` |
| `45ca8cc8421cdb2c91fea9a0bbb5e2c20dd725c3` | `9b2e2e3df008c0ce0a78f8b5d44d77bf4749ee58` |
| `3336f3ac207135c0cc899ff7aac4bfe01e37da33` | `5402271064e068e616b36ed4046cb9502f324879` |
| `b0aaa3c104d217896f89130add6499c931b1cfff` | `67ce450bb5a505094821f80ff34651da339c882e` |
| `474a8389e5fd16e86950f65c78ff792cdc568fa5` | `9333e3c81990bd5fdf2e71ca4bf996e49d7c7829` |
| `17ec3658faabfd84d44694527d2b4ab2082ad7c1` | `7a02998bef3e0752b70c66dbcdc91f6708c78463` |
| `d4621888acfc215aa5378561f61c6343bc53140a` | `bc42f7c6243fd6bc693f6d19aa050a96de53a224` |
| `335ee7ccee5439ab90e4ed24ea40c933f74c8ee8` | `099665c7f66d80406a97598d2c9bb7e6ed59786d` |
| `c2611a9d9e36b8045644e8bc5c67f630321a22c5` | `e5299f184a8410faa263a23f11286e90fef7102a` |
| `50b94f12e18ee82fcafce3b0d982ae42a22ca95e` | `46324fc6db2e869dfbc2b848b74a7a28d7f85cc3` |
| `da58f2a0a9beed9f8c61ee533099cae6ccb1a2a3` | `a9b06ec342619e43d142d1e5a4d9a1706b7601ec` |
| `5b32a1b5afd52147aca5b7b60d06fdc426c30f58` | `1cf8f017ef4e15be392ff56fa0814d373dc59785` |
| `6b45b24b43d2e215c687ffabe185e679be206d08` | `6e1da96efe8fc5caedd6bf7a33378c1998adb0d2` |

Local recovery refs:

- `refs/archive/antijamming/pre-ideal-removal-20260907-cleanup` → `6b45b24`.
- `refs/archive/antijamming/verified-ideal-removal-20260907` → `78924a3`;
  tested pre-fold implementation snapshot, tree
  `9ac0c67cbc014aaf8ff9e5e737add4ccc2197c75`.
- `refs/archive/antijamming/pre-latch-removal-20260907-main` → `d8aef5f`;
  precautionary preservation only, not evidence main was changed.

All fourteen rewritten commits retain typed subjects and use
`UsmanHassan-0 <125034497+UsmanHassan-0@users.noreply.github.com>` for author
and committer. The folded cleanup commit contains the removal description and
breaking-schema footer. Later FIFO/release/UHD changes were replayed in their
own commits, with release regressions migrated to the measured solver and label.
The final code matches the tested snapshot exactly; only new evidence is added
after the replayed tip. Software results, counterexamples and boundaries are
recorded in `../progress_tracker.md`, including the still-open malformed-input
release-timer gap. This is not a claim that every historical commit was retested.

Publication used an explicit lease for cleanup's old remote tip `6b45b24`;
the push to `6e1da96` succeeded and a subsequent `ls-remote` confirmed it.
Remote main remained `d8aef5f` and experimental remained `b60344f`. This audit
amendment follows as a documentation-only commit; Spark's checkout was not
changed and must not be described as synchronized or hardware-verified.
