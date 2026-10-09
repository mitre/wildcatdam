# Scenario 2: Manual Override

## Overview

| Field | Value |
|---|---|
| **Tactic** | Impair Process Control, Collection |
| **Techniques** | [T0836 - Modify Parameter](https://attack.mitre.org/techniques/T0836/), [T0861 - Point & Tag Identification](https://attack.mitre.org/techniques/T0861/) |
| **Target** | Wildcat Dam PLC over Modbus |
| **Impact** | Door 1 forced open regardless of water level; door 2's auto-open threshold lowered so it opens well below its normal water level |

## Objective

Set door 1's override-enable coil to take manual control, then write its
position open so it no longer follows the automatic door logic. Separately,
lower door 2's auto-open threshold with a holding register write so door 2
opens well below its normal water level. Door 2 is never overridden; only its
threshold changes.

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
| `modbus.write_coil.start` | Coil address to write | int | `3` for override enable, `0` for door 1 |
| `modbus.write_coil.value` | Value for a single coil write | string | `ON` |
| `modbus.write_register.start` | Holding register address to write | int | `5` |
| `modbus.write_register.value` | Value for a single register write | int | `5` |

Control data:

| Control | Modbus Address | Value |
|---|---:|---|
| door_1 override enable | Coil `3` | `ON` |
| door_1 position | Coil `0` | `ON` |
| threshold_2 | Holding register `5` | `5` |

### Caldera Operation

| Step | Ability | Ability ID | Facts Used |
|------|---------|------------|------------|
| 1 | Modbus - Read Coils | `d80b9cd5-b1d8-482a-a745-71d74f9d0885` | baseline: `read_coil.start=0`, `read_coil.count=6` |
| 2 | Modbus - Write Single Coil | `056e6289-4cbf-417f-928a-d75125e4db4f` | `write_coil.start=3`, `write_coil.value=ON` (door 1 override enable) |
| 3 | Modbus - Write Single Coil | `056e6289-4cbf-417f-928a-d75125e4db4f` | `write_coil.start=0`, `write_coil.value=ON` (door 1 forced open) |
| 4 | Modbus - Write Single Register | `d6991b6b-d3b2-4398-ad3f-d736ae09acf9` | `write_register.start=5`, `write_register.value=5` (threshold_2 low) |
| 5 | Modbus - Read Holding Registers | `bc8961a2-7534-4b2a-bbc3-2456f58243be` | verify: `read_holding.start=0`, `read_holding.count=7` |

## Expected Observations

- The baseline coil read returns `ON,OFF,OFF,OFF,OFF,OFF` - door 1 open from a prior auto cycle, override off.
- Both coil writes return "Write successful".
- The register write returns "Write successful".
- Holding registers read back `40,1,3,6,60,5,85` - register 5 (threshold_2) is now `5`, down from its default `75`.
- A raw Modbus read shows coil 0 (door 1 position) `ON` and coil 3 (door 1 override enable) `ON`.
- The HMI `/update` endpoint shows `threshold_2` at `5` and `door_1` at `1`.

## See Also

- [Caldera for OT](https://github.com/mitre/caldera-ot)
- [ATT&CK for ICS - T0836](https://attack.mitre.org/techniques/T0836/)
- [ATT&CK for ICS - T0861](https://attack.mitre.org/techniques/T0861/)
