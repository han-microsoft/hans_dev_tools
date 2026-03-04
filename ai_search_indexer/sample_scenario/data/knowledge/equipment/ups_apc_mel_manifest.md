# UPS — APC Smart-UPS SRT 10kVA

## Identification
- **Model**: APC Smart-UPS SRT 10000VA
- **Serial**: APC-SRT10K-2024-MEL-001
- **Location**: Melbourne DC, Power Room A
- **Firmware**: UPS 11.8, NMC 3.2.1

## Specifications
- Capacity: 10kVA / 10kW
- Topology: Double conversion online
- Battery runtime (full load): 6 minutes
- Battery runtime (half load): 18 minutes
- Input voltage: 200–240V AC
- Output voltage: 230V AC ±1%
- Transfer time: 0ms (no break)

## Connected Equipment
- Rack A-01 through A-04 (48 servers)
- Network switches (Rack N-01)
- Total load: ~7.2kW (72% capacity)

## Maintenance Schedule
- Battery test: monthly (automated, first Sunday 03:00 UTC)
- Battery replacement: every 3–5 years (last replaced: 2024-06)
- Capacitor check: annually
- Firmware update: as available

## Alerts
- Load > 80%: warning to #infra-alerts
- Battery < 20 minutes runtime: P2 alert
- On battery: P1 alert + SMS to DC team

## Support
- APC InfraStruxure Central monitoring
- Contract: WEXTWAR3YR-SRT-10K
- SLA: next business day on-site
