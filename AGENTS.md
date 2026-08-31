# Agent Instructions

- Do not give abstract answers, even partially. Answer from the concrete code, logs, files, equations as implemented, and hardware layout in this repository.
- If a theoretical statement is useful, first state whether it is actually true for the current code and hardware. Do not present theory as if it describes the implementation.
- When discussing RF, DSP, DoA, beamforming, GNSS-SDR, UHD, or GUI behavior, tie conclusions to specific code paths, config values, log evidence, or measured hardware facts.
- If sudo is needed on this workstation and the user has already provided sudo access in the conversation, use it non-interactively instead of stopping to ask again. Do not write sudo passwords into repository files, docs, logs, or scripts.
- For active work, read and maintain `docs/progress_tracker.md`. It is the one
  living implementation/evidence ledger, not proof that every repository line
  or concurrency schedule has been examined. Preserve exact run artifacts in
  `docs/audits/` and link them from the tracker instead of creating another
  progress document.
