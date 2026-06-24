# Hardware reference — detected USRP and host link

This document records **what UHD and the OS report** for the connected system: motherboard, daughterboards (including TwinRX), synchronization options, RFNoC graph, and the **host ↔ radio** Ethernet path. Regenerate after hardware changes by re-running the commands in [How this file was produced](#how-this-file-was-produced).

**Snapshot date:** 2026-04-17 (UHD probe snapshot), with live host transport/performance update on 2026-04-24.  

---

## How this file was produced

- `uhd_find_devices`
- `uhd_usrp_probe --args addr=192.168.40.2`
- `uhd_config_info --version`
- Host: `ip`, `ethtool`, `lspci`, `sysctl` (interface toward `192.168.40.2`)

---

## UHD software

| Item | Value |
|------|--------|
| **UHD version** | `4.9.0.0+ds1-1~noble2` |
| **Build / platform (from logs)** | GNU C++ 13.3.0; Boost 1.83; Linux |

---

## Device detection summary

| Item | Value |
|------|--------|
| **Product** | X300 |
| **UHD type** | `x300` |
| **FPGA image** | `XG` |
| **Serial** | `32CEC52` |
| **Connection address used** | `addr=192.168.40.2` |
| **RFNoC capable** | Yes |

---

## Motherboard (X300)

### Identification and firmware

| Item | Value |
|------|--------|
| **Mboard name** | X300 |
| **Revision** | 13 |
| **revision_compat** | 7 |
| **product (numeric)** | 30817 |
| **FW version** | 6.1 |
| **FPGA version** | 39.3 |
| **FPGA git hash** | da5c64b |
| **Device DNA** | 0018C5282869685C |

### Ethernet interfaces on the USRP (as reported by UHD)

| Port | MAC address | IP address | Subnet |
|------|-------------|------------|--------|
| 0 | 00:80:2f:39:51:2f | 192.168.10.2 | 255.255.255.0 |
| 1 | 00:80:2f:39:51:30 | 192.168.20.2 | 255.255.255.0 |
| 2 | *(same mboard)* | 192.168.30.2 | 255.255.255.0 |
| 3 | *(same mboard)* | 192.168.40.2 | 255.255.255.0 |

- **Reported gateway:** 192.168.10.1  

### Radio / FPGA clocking (UHD)

| Item | Value |
|------|--------|
| **Radio 1× clock** | 200 MHz |
| **Meaning** | Master clock rate for the radio/FPGA timing domain as reported at probe time (`Radio 1x clock: 200 MHz`). |

### Transport / streaming (UHD log at connect)

| Item | Value |
|------|--------|
| **Maximum frame size (observed)** | 7972 bytes (after host MTU=8000) |
| **UHD guidance** | For this link, UHD recommends **send_frame_size** and **recv_frame_size** of at least **8000** for best performance. |
| **Observed limitation** | Configuration “will only allow **7972**” — UHD still warns when requesting `recv_frame_size/send_frame_size=8000` because payload max is slightly below 8000. |
| **Practical implication** | Use `recv_frame_size/send_frame_size` at or below **7972** for clean transport settings on current link MTU. |

### Other UHD messages at probe

- **`[INFO] [GPS] No GPSDO found`** — no GPSDO module detected by UHD at initialization.
- **`[WARNING] [RFNOC::GRAPH] One or more blocks timed out during flush!`** — transient RFNoC graph flush warning during probe; note if it correlates with failures during normal streaming.

---

## Synchronization: time, clock, GPSDO, LO

### Time and clock *sources* (motherboard capabilities)

Reported **available** sources (not necessarily that hardware is installed):

| Category | Options reported |
|----------|------------------|
| **Time sources** | internal, external, gpsdo |
| **Clock sources** | internal, external, gpsdo |

### GPSDO

| Item | Status |
|------|--------|
| **GPSDO detection** | UHD reports **`No GPSDO found`** at connect. |
| **Motherboard still lists `gpsdo`** | The mboard advertises `gpsdo` as a *selectable* clock/time source when hardware is present; currently **no GPSDO unit is detected**. |

### Motherboard sensors

| Sensor | Present on mboard |
|--------|---------------------|
| **temp_fpga** | Listed |
| **ref_locked** | Listed |

### LO and frontend behavior (TwinRX, per UHD)

For each TwinRX RX frontend, UHD reports:

| Field | Typical meaning |
|-------|------------------|
| **Uses LO offset** | `No` — no separate LO offset flag in the probe output. |
| **Sensors** | `lo_locked` — LO lock status available where supported. |
| **Connection Type** | `II` on TwinRX RX0, `QQ` on TwinRX RX1 — RF path / channelization identifiers as reported by UHD (not antenna names). |

---

## RFNoC blocks and static graph

### Blocks

- `0/DDC#0`, `0/DDC#1`
- `0/DUC#0`, `0/DUC#1`
- `0/Radio#0`, `0/Radio#1`
- `0/Replay#0`

### Static connections (verbatim from UHD)

- `0/SEP#0:0==>0/DUC#0:0`
- `0/DUC#0:0==>0/Radio#0:0`
- `0/Radio#0:0==>0/DDC#0:0`
- `0/DDC#0:0==>0/SEP#0:0`
- `0/Radio#0:1==>0/DDC#0:1`
- `0/DDC#0:1==>0/SEP#1:0`
- `0/SEP#2:0==>0/DUC#1:0`
- `0/DUC#1:0==>0/Radio#1:0`
- `0/Radio#1:0==>0/DDC#1:0`
- `0/DDC#1:0==>0/SEP#2:0`
- `0/Radio#1:1==>0/DDC#1:1`
- `0/DDC#1:1==>0/SEP#3:0`
- `0/SEP#4:0==>0/Replay#0:0`
- `0/Replay#0:0==>0/SEP#4:0`
- `0/SEP#5:0==>0/Replay#0:1`
- `0/Replay#0:1==>0/SEP#5:0`

---

## Daughterboards and frontends

### Radio #0

#### TX daughterboard (`0/Radio#0`)

| Item | Value |
|------|--------|
| **ID** | Unknown (0x0094) |
| **Serial** | 34E32C8 |
| **Revision** | 8 |
| **TX Frontend name** | Unknown (0x0094) - 0 |
| **Antennas** | *(empty in probe)* |
| **Freq range** | 0.000–0.000 MHz *(probe placeholder / not enumerated)* |
| **Gain elements** | None |
| **Bandwidth range** | 0.0 Hz |
| **Connection type** | IQ |
| **Uses LO offset** | No |

#### RX daughterboard (`0/Radio#0`) — **TwinRX Rev C**

| Item | Value |
|------|--------|
| **ID** | TwinRX Rev C (0x0095) |
| **Serial** | 34E2F1D |
| **Revision** | 5 |

**TwinRX RX0 (frontend 0)**

| Item | Value |
|------|--------|
| **Name** | TwinRX RX0 |
| **Antennas** | RX1, RX2 |
| **Sensors** | lo_locked |
| **Frequency range** | 10.000–6000.000 MHz |
| **Gain** | 0.0–93.0 dB, step 1.0 dB |
| **Bandwidth** | 80 MHz (reported as 80000000.0–80000000.0 Hz) |
| **Connection type** | II |
| **Uses LO offset** | No |

**TwinRX RX1 (frontend 1)**

| Item | Value |
|------|--------|
| **Name** | TwinRX RX1 |
| **Antennas** | RX1, RX2 |
| **Sensors** | lo_locked |
| **Frequency range** | 10.000–6000.000 MHz |
| **Gain** | 0.0–93.0 dB, step 1.0 dB |
| **Bandwidth** | 80 MHz |
| **Connection type** | QQ |
| **Uses LO offset** | No |

---

### Radio #1

#### TX daughterboard (`0/Radio#1`)

| Item | Value |
|------|--------|
| **ID** | Unknown (0x0094) |
| **Serial** | 346D4DD |
| **Revision** | 8 |
| **TX Frontend** | Same placeholder pattern as Radio#0 (0 MHz range in probe) |
| **Connection type** | IQ |
| **Uses LO offset** | No |

#### RX daughterboard (`0/Radio#1`) — **TwinRX Rev C**

| Item | Value |
|------|--------|
| **ID** | TwinRX Rev C (0x0095) |
| **Serial** | 346F390 |
| **Revision** | 5 |

**TwinRX RX0 (frontend 0)** — same spec pattern as Radio#0: 10–6000 MHz, 80 MHz BW, 0–93 dB, antennas RX1/RX2, `lo_locked`, connection **II**.

**TwinRX RX1 (frontend 1)** — same except connection **QQ**.

---

## Host PC — data plane to the USRP

Measurements below are for the interface used to reach **192.168.40.2** (USRP port 3).

### NIC

| Item | Value |
|------|--------|
| **Interface** | `enp6s0f1np1` |
| **PCI address** | `06:00.1` |
| **Device** | Intel Ethernet Controller **X710** for 10GbE SFP+ `[8086:1572]` (rev 02) |
| **Kernel driver** | `i40e` |
| **Link** | Up, **10000 Mb/s**, Full duplex, FIBRE (SFP+) |

### IPv4 path

| Item | Value |
|------|--------|
| **Host IP** | 192.168.40.1/24 |
| **USRP IP (used)** | 192.168.40.2/24 |
| **Host MAC** | 40:a6:b7:06:a0:81 |

### MTU and buffering

| Item | Value |
|------|--------|
| **MTU** | 8000 |
| **UHD max frame (with MTU 8000)** | 7972 bytes (see [Transport / streaming](#transport--streaming-uhd-log-at-connect)) |

**MTU vs sample rate (Msps):** They are **not** the same thing. **Msps** is how many complex samples per second you stream (set in software / DDC). **MTU** is the **maximum payload per Ethernet frame** on the host link. UHD does **not** compute MTU from Msps. In practice they **interact**: higher aggregate throughput (more Msps × more channels) needs **more bytes per second**; smaller MTU means **more packets per second** for the same throughput, which can stress the stack. So you tune **both** — e.g. lower Msps to reduce load, **and** raise MTU (jumbo) to allow larger UHD `recv_frame_size` / `send_frame_size` on 10GbE.

### `sysctl` (snapshot)

| Parameter | Value |
|-----------|--------|
| `net.core.rmem_max` | 26214400 |
| `net.core.wmem_max` | 26214400 |

### NIC ring buffers (`ethtool -g`)

| | RX | TX |
|---|----|----|
| **Current** | 512 | 512 |
| **Maximum** | 8160 | 8160 |

### Throughput sanity at 4 channels (10/50/80 Msps)

Assuming complex sample formats:

- **Wire `sc16`** = 4 bytes/sample (I16 + Q16)
- **Host `fc32`** = 8 bytes/sample (float32 I + float32 Q)

Assumptions:

- 4 RX channels
- Wire format: `sc16` (4 bytes/sample)
- Host format: `fc32` (8 bytes/sample)

Computed aggregate:

| Target per-channel rate | Aggregate samples/s | Wire payload (`sc16`) | Host payload (`fc32`) |
|-------------------------|---------------------|----------------------|-----------------------|
| **10 Msps** | 40,000,000 | 160,000,000 B/s = **1.28 Gb/s** | 320,000,000 B/s = **0.32 GB/s** |
| **50 Msps** | 200,000,000 | 800,000,000 B/s = **6.40 Gb/s** | 1,600,000,000 B/s = **1.60 GB/s** |
| **80 Msps** | 320,000,000 | 1,280,000,000 B/s = **10.24 Gb/s** | 2,560,000,000 B/s = **2.56 GB/s** |

Comparison to current host link:

- NIC: Intel X710, link up at **10,000 Mb/s** full duplex.
- At **10 Msps/ch**, wire load (~1.28 Gb/s) is comfortably below 10GbE.
- At **50 Msps/ch**, wire load (~6.4 Gb/s) is significant but below nominal link.
- At **80 Msps/ch**, wire load (~10.24 Gb/s) is already above nominal 10GbE *before* Ethernet/IP/UDP overhead, so overflow/drops are expected unless channel/rate/path changes.

### Current host capability snapshot (for overflow triage)

| Layer | Current value | Why it matters |
|------|---------------|----------------|
| Kernel | Linux `6.17.0-20-generic` | Driver/network stack behavior and available tuning features |
| NIC model | Intel X710 SFP+ (`i40e`) | 10GbE class interface to USRP |
| Link state | 10000 Mb/s, full duplex, link detected | Physical link is healthy/up |
| MTU | 9000 live on 2026-04-24; older UHD probe snapshot used 8000 | Enables jumbo frames. Re-run `uhd_usrp_probe --args addr=192.168.40.2` if exact UHD max frame size after MTU 9000 is needed. |
| Socket buffers | `rmem_max=26214400`, `wmem_max=26214400`, defaults same | Larger UDP buffering than stock defaults |
| netdev backlog | `net.core.netdev_max_backlog=1000` | Queue depth before protocol processing |
| NIC rings (current) | RX=8160, TX=8160 live on 2026-04-24 | Receive/transmit descriptor depth is currently at maximum. |
| NIC rings (max) | RX=8160, TX=8160 | Ceiling available for tuning with `ethtool -G` |

### Live performance state (2026-04-24)

These values were checked live on 2026-04-24 and supersede older host-side values above where they differ. UHD hardware identity and TwinRX limits still come from the `uhd_usrp_probe` snapshot unless re-probed.

| Layer | Current value | Why it matters |
|---|---|---|
| CPU | Intel Core i9-14900K, `32` logical CPUs online (`0-31`) | Enough host compute is available; realtime failures should not be blamed on low core count. |
| CPU governor | All `32` cpufreq governors are `performance` | Avoids frequency-scaling lag during realtime streaming. |
| CPU frequency policy | `scaling_min_freq=3201000 kHz`, `scaling_max_freq=3201000 kHz`; boost active; observed current frequencies up to about `5.7 GHz` | Frequency policy is pinned high, with turbo still active. |
| OS power profile | `powerprofilesctl get` reports `balanced` | Power profile is not the same as cpufreq governor; cpufreq itself is already `performance`. |
| NIC | `enp6s0f1np1`, Intel X710 SFP+ / `i40e`, PCI `06:00.1` | 10GbE data path to USRP `192.168.40.2`. |
| Link | `10000 Mb/s`, full duplex, FIBRE, link detected | Physical link is up at 10GbE. |
| NIC queues | `32` TX queues and `32` RX queues | Parallel NIC queueing is available. |
| MTU | `9000` | Jumbo frames are enabled. |
| NIC rings | RX=`8160`, TX=`8160`; max RX/TX=`8160` | Rings are at the NIC maximum, reducing burst-drop risk. |
| Socket buffers | `net.core.rmem_max=26214400`, `wmem_max=26214400`, defaults also `26214400` | Large UDP socket buffers are configured. |
| netdev backlog | `net.core.netdev_max_backlog=1000` | Host packet backlog queue depth before protocol processing. |
| NIC filters/features | `ntuple-filters=on`, `receive-hashing=on`, checksum/segmentation/GRO features mostly on | Hardware receive steering/filter support is available. |

Live transport interpretation:

- MTU and sample rate are independent. MTU controls Ethernet frame payload size; sample rate controls samples per second from UHD/DDC.
- Higher sample rate increases bytes/s and packets/s. Jumbo MTU and large rings reduce per-packet overhead and burst drop risk, but they do not change Nyquist or RF bandwidth.
- For one channel, `10 Msps` is modest for 10GbE. Previous `10 MHz` GNSS-SDR test issues looked more likely to be processing, conditioning, tracking robustness, or scheduling details than raw link capacity alone.
- The older UHD max frame-size value of about `7972` bytes was measured with MTU `8000`. With live MTU `9000`, re-run `uhd_usrp_probe --args addr=192.168.40.2` to record the exact current UHD frame-size limit.

### USRP + daughterboard limits relevant to this setup

| Component | Reported capability/limit |
|----------|----------------------------|
| Motherboard | X300, RFNoC capable, radio clock 200 MHz |
| RX daughterboards | TwinRX Rev C on both `Radio#0` and `Radio#1` |
| TwinRX frontend RF range | 10–6000 MHz |
| TwinRX analog BW | 80 MHz fixed (reported as 80,000,000 Hz) |
| TwinRX gain range | 0–93 dB (1 dB steps) |
| LO status sensor | `lo_locked` available per frontend |
| Time/clock source options | `internal`, `external`, `gpsdo` |
| GPSDO presence at probe | `No GPSDO found` |

---

## Application runtime profile in this repository (cross-reference)

Runtime streaming values live in `configs/antijamming/x300_realtime.json`, loaded through `src/antijamming/config/schemas/runtime.py`, including:

- `usrp_addr` value `addr=192.168.40.2`, the fixed X300/XG 10GbE SFP path
- `recv_frame_size` / `send_frame_size` value **8000** for the jumbo-frame
  10GbE product profile
- Default `sample_rate`, `center_freq_hz`, `channels`, `antenna`, etc.

### Direct GNSS-SDR runtime observation

On 2026-04-29, a 60-second direct GNSS-SDR run against the rebuilt upstream-source binary reached the X300 successfully, tuned channel 0, and continued running, but the one-shot UHD frontend check reported:

- `Check for front-end LO: unlocked is ... UNLOCKED!`

This is relevant to later GNSS-SDR source changes in this repository:

- the direct-run path itself does not require patched UHD source code just to start and stream
- but the TwinRX/X300 path benefits from a more robust LO-lock wait/retry path instead of treating the first one-shot read as the final answer

### Runtime diagnostics logged each session (Start)

On each stream start, the app records a **fresh OS snapshot** (see `host_transport.collect_host_transport_report`):

| Log file | What to look for |
|----------|------------------|
| `logs/transport.log` | Parsed USRP IPv4, **egress iface**, **MTU**, sysfs link speed, **driver**, `ethtool -g` RX/TX ring lines (if available), `net.core.rmem_*` / `wmem_*` |
| `logs/usrp_hardware.log` | Same transport lines plus full UHD `startup_report_lines` (rates, clock/time, per-channel readback) |
| `logs/stream_health.log` | **Recv pacing:** max time between consecutive `recv()` returns (ms), per `rx_health_log_interval_chunks` |
| `logs/transport.log` | **GNSS:** if the GNSS FIFO feed **queue** fills, “dropped N chunk(s)” — tune `gnss_feed_queue_maxsize` or reduce load |

Architecture: the **RX drain thread** only calls `recv()` and enqueues a **copy** for GNSS into a **raw queue**; **beamforming** runs on `gnss_beamform`, then **FIFO write** to GNSS-SDR runs on `gnss_fifo_write`, with a **second bounded queue** between them — so **recv**, **beamform**, and **blocking FIFO I/O** are not collapsed into one thread.

The realtime product launcher uses this fixed profile directly; ad-hoc transport
sweep scripts are intentionally not part of the product path.

---

## Notes

- **X300 vs TwinRX:** The **motherboard** is **X300**. **TwinRX** is the **RX daughterboard** family installed on **both** `Radio#0` and `Radio#1` per UHD.
- **TX boards** show **Unknown (0x0094)** with **0 MHz** ranges in the probe — treat as **not fully enumerated in this output**; re-run `uhd_usrp_probe` after firmware/image updates if you expect full TX metadata.
- For Ettus guidance on 10GbE (MTU, buffers, rates), use the official **X300/X310** interface and transport documentation alongside this snapshot.

## MTU change impact (measured)

This section compares observed behavior before vs after changing host NIC MTU from 1500 to 8000 on `enp6s0f1np1`.

### A/B summary

| Item | Before (MTU 1500) | After (MTU 8000) | Result |
|------|--------------------|------------------|--------|
| UHD max frame size | 1472 bytes | 7972 bytes | **Improved ~5.41× payload per frame** |
| Frame-size mismatch warnings | `requested 8000, NIC max 1472` warnings | with `recv/send=7972`, mismatch warnings gone | **Improved** |
| UHD recommendation warning | `< 8000` for best performance | still `< 8000` (7972 max payload) | **Still present** |
| Overflow markers (`O...`) in 30s test @ 10 Msps | Present | Present | **Not solved by MTU alone** |

### What improved

- Packet payload headroom increased from **1472** to **7972** bytes.
- Transport moved out of the severe small-frame regime (MTU 1500 bottleneck).
- With runtime args `--recv-frame-size 7972 --send-frame-size 7972`, UHD no longer reports hard mismatch/clamp warnings against NIC max payload.

### What did not improve yet

- RX overflow still appears at 10 Msps in 30s runs, so remaining bottlenecks are likely host scheduling/processing path and/or other transport stack limits.
- UHD still warns that `>=8000` would be better, but at MTU 8000 the practical payload cap is 7972 due to protocol overhead.

### Latest measured 30s validation (10 Msps, matched frame sizes)

Command used at the time was an engineering override path. The product launcher now reads runtime values from `configs/antijamming/x300_realtime.json`.

Observed from logs:

- `USRP stream stopped (raw=31335, overflow=0, timeout=0, startup_overflow=0, startup_timeout=1)`
- `stream_health.log` recv pacing remained near ~1 ms max-gap windows (with a few transient spikes), and no overflow guard triggers.

Interpretation:

- At **10 Msps/ch**, with MTU 8000 and frame sizes matched to 7972, this setup completed a 30s run with **zero overflow** in the measured session.
- Previous overflow observations were likely tied to the earlier transport mismatch regime and/or prior runtime combinations.
