# Scenario 3: Chaos

## Overview

| Field | Value |
|---|---|
| **Tactic** | Collection, Impair Process Control |
| **Techniques** | [T0861 - Point & Tag Identification](https://attack.mitre.org/techniques/T0861/), [T0806 - Brute Force I/O](https://attack.mitre.org/techniques/T0806/) |
| **Target** | Wildcat Dam PLC over Modbus |
| **Impact** | Door coils and control registers overwritten with random values, so the HMI no longer reflects the true process state |

## Objective

Write random values to the dam's coils and holding registers across their
defined ranges. The writes are untargeted: door positions, override flags,
reduction rates, and thresholds all change at once. The HMI no longer reflects
the true process state, and the control loop acts on the corrupted values on
the next simulation tick.

## Modbus Variant

Load `docs/sources/wildcat-dam-simulator-facts.yml` as the fact source, then
build an operation using the Modbus abilities below.

### Fact Variables

| Fact | Description | Type | Default |
|------|-------------|------|---------|
| `modbus.server.ip` | IP address of the dam controller | string | `127.0.0.1` |
| `modbus.server.port` | Modbus TCP port | int | `5020` |
| `modbus.read_coil.start` | First coil to read | int | `0` |
| `modbus.read_coil.count` | Number of coils to read | int | `6` |
| `modbus.read_holding.start` | First holding register to read | int | `0` |
| `modbus.read_holding.count` | Number of holding registers to read | int | `7` |
| `modbus.fuzz_coil.start` | First coil address to fuzz | int | `0` |
| `modbus.fuzz_coil.end` | Last coil address to fuzz | int | `5` |
| `modbus.fuzz_coil.count` | Number of random writes to issue | int | `10` |
| `modbus.fuzz_coil.wait` | Delay between writes (seconds) | float | `0.1` |
| `modbus.fuzz_register.start` | First holding register address to fuzz | int | `0` |
| `modbus.fuzz_register.end` | Last holding register address to fuzz | int | `6` |
| `modbus.fuzz_register.count` | Number of random writes to issue | int | `10` |
| `modbus.fuzz_register.min` | Minimum random register value | int | `0` |
| `modbus.fuzz_register.max` | Maximum random register value | int | `999` |
| `modbus.fuzz_register.wait` | Delay between writes (seconds) | float | `0.1` |

Control data:

| Control | Modbus Address | Value |
|---|---:|---|
| Coils | `0`-`5` | random `ON`/`OFF` |
| Holding registers | `0`-`6` | random `0`-`999` |

### Caldera Operation

| Step | Ability | Ability ID | Facts Used |
|------|---------|------------|------------|
| 1 | Modbus - Read Coils | `d80b9cd5-b1d8-482a-a745-71d74f9d0885` | baseline: `read_coil.start=0`, `read_coil.count=6` |
| 2 | Modbus - Read Holding Registers | `bc8961a2-7534-4b2a-bbc3-2456f58243be` | baseline: `read_holding.start=0`, `read_holding.count=7` |
| 3 | Modbus - Fuzz Coils | `40f78a8f-2aaa-4b1b-872f-7c6b0f1ddf3e` | `fuzz_coil.start=0`, `fuzz_coil.end=5`, `fuzz_coil.count=10`, `fuzz_coil.wait=0.1` |
| 4 | Modbus - Fuzz Registers | `2a6e8c8e-f350-11ed-9156-23436b8f0e58` | `fuzz_register.start=0`, `fuzz_register.end=6`, `fuzz_register.count=10`, `fuzz_register.min=0`, `fuzz_register.max=999`, `fuzz_register.wait=0.1` |

## Expected Observations

- Baseline reads return coils `ON,OFF,OFF,ON,OFF,OFF` and holding registers `40,1,3,6,60,5,85`.
- After fuzzing, coils read back `OFF,ON,OFF,ON,ON,ON` - door 1's position coil off and two override-enable coils on, none tied to water level.
- Holding registers read back `946,49,262,950,60,3,922` - `close_level` (946) and `threshold_3` (922) sit far outside their 0-100 range, and the reduction rates (49, 262, 950) far outside their 0-10 range; `threshold_1` (60) was not hit by this run's random writes.
- The HMI `/update` endpoint shows `water_level` `0`, `close_level` `946`, `threshold_3` `922`, and `door_1` `false`.
- The control loop runs against the corrupted values on every tick, so the HMI no longer shows a trustworthy process state.

## See Also

- [Caldera for OT](https://github.com/mitre/caldera-ot)
- [ATT&CK for ICS - T0861](https://attack.mitre.org/techniques/T0861/)
- [ATT&CK for ICS - T0806](https://attack.mitre.org/techniques/T0806/)
