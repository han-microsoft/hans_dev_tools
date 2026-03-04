# Load Balancer — F5 BIG-IP i5800

## Identification
- **Model**: F5 BIG-IP i5800
- **Serial**: F5-i5800-2024-SYD-001
- **Location**: Sydney DC, Rack B-12
- **Firmware**: BIG-IP 17.1.1

## Specifications
- Throughput: 40 Gbps L4, 20 Gbps L7
- SSL TPS: 80,000
- Concurrent connections: 36 million
- Compression: 20 Gbps
- Interfaces: 8x 10GbE SFP+, 2x 40GbE QSFP+

## Maintenance Schedule
- Firmware updates: quarterly (maintenance window Sunday 02:00–06:00 UTC)
- Certificate rotation: annually
- Health monitoring: Nagios check every 30 seconds

## Configuration Notes
- Active-standby pair with F5-i5800-2024-SYD-002
- Virtual servers: api.example.com (HTTPS), internal.example.com (HTTP)
- iRules: rate limiting (100 req/s per IP), geo-blocking, header injection
- Persistence: cookie-based for /api/*, source-addr for /ws/*

## Support
- F5 Support Contract: SUP-2024-F5-SYD-001
- Expiry: 2026-03-31
- SLA: 4-hour hardware replacement
