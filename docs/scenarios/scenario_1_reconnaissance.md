# Scenario 1: Reconnaissance

## Overview

| Field | Value |
|---|---|
| **Tactic** | Collection |
| **Techniques** | [T0802 - Automated Collection](https://attack.mitre.org/techniques/T0802/), [T0861 - Point & Tag Identification](https://attack.mitre.org/techniques/T0861/) |
| **Target** | Wildcat Dam PLC over Modbus |
| **Impact** | None - read-only operations only |

## Objective

Read the dam controller's coils, discrete inputs, holding registers, and input
registers without issuing any write. This gives a baseline point map of the
door controls, reduction rates, thresholds, water level, and surge exposed by
the PLC.

## Modbus Variant

Load `docs/sources/wildcat-dam-simulator-facts.yml` as the fact source, then
build an operation using the Modbus abilities below.

### Fact Variables

| Fact | Description | Type | Default |
|------|-------------|------|---------|
| `modbus.server.ip` | IP address of the dam controller | string | `127.0.0.1` |
| `modbus.server.port` | Modbus TCP port | int | `5020` |
| `modbus.read_coil.start` | First coil address to read | int | `0` |
| `modbus.read_coil.count` | Number of coils to read | int | `6` |
| `modbus.read_discrete.start` | First discrete input address to read | int | `0` |
| `modbus.read_discrete.count` | Number of discrete inputs to read | int | `1` |
| `modbus.read_holding.start` | First holding register address to read | int | `0` |
| `modbus.read_holding.count` | Number of holding registers to read | int | `7` |
| `modbus.read_input.start` | First input register address to read | int | `0` |
| `modbus.read_input.count` | Number of input registers to read | int | `2` |

### Caldera Operation

| Step | Ability | Ability ID | Facts Used |
|------|---------|------------|------------|
| 1 | Modbus - Scan Device | `7f43fa44-c2aa-4bb6-ba4b-2c1e58df3b51` | `modbus.server.ip`, `modbus.server.port` |
| 2 | Modbus - Read Coils | `d80b9cd5-b1d8-482a-a745-71d74f9d0885` | `modbus.server.ip`, `modbus.server.port`, `modbus.read_coil.start`, `modbus.read_coil.count` |
| 3 | Modbus - Read Discrete Inputs | `001e21ea-61b5-4b78-b79e-9d5687d819bd` | `modbus.server.ip`, `modbus.server.port`, `modbus.read_discrete.start`, `modbus.read_discrete.count` |
| 4 | Modbus - Read Holding Registers | `bc8961a2-7534-4b2a-bbc3-2456f58243be` | `modbus.server.ip`, `modbus.server.port`, `modbus.read_holding.start`, `modbus.read_holding.count` |
| 5 | Modbus - Read Input Registers | `3946b6da-c570-47cd-b63f-c13875297cb4` | `modbus.server.ip`, `modbus.server.port`, `modbus.read_input.start`, `modbus.read_input.count` |

## Expected Observations

- Coils 0-5 read back `ON,OFF,OFF,OFF,OFF,OFF` - door 1 open, doors 2-3 closed, all three overrides off.
- The discrete input reads `ON` - water is being released.
- Holding registers 0-6 read `40,1,3,6,60,75,85` - `close_level`, the three reduction rates, and the three thresholds, matching the `config.yaml` defaults.
- Input registers 0-1 read the live water level and surge, which drift tick to tick while the sim runs.
- Scan Device enumerates all four register types in one pass.
- No writes occur, so process state changes only from the sim's own control loop.

## See Also

- [Caldera for OT](https://github.com/mitre/caldera-ot)
- [ATT&CK for ICS - T0802](https://attack.mitre.org/techniques/T0802/)
- [ATT&CK for ICS - T0861](https://attack.mitre.org/techniques/T0861/)
