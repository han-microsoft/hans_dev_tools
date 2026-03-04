# Database Failover Runbook

## Overview
Procedure for failing over the primary PostgreSQL database to the standby replica during planned maintenance or unplanned outages.

## When to Use
- Primary database is unresponsive for > 2 minutes
- Planned maintenance window (schedule in advance)
- Storage I/O errors on primary host

## Pre-Failover Checklist
- [ ] Confirm replication lag < 10 seconds
- [ ] Verify standby is healthy: `pg_isready -h standby-host`
- [ ] Notify application teams (5-minute warning)
- [ ] Pause scheduled batch jobs

## Failover Steps

### 1. Promote Standby
```bash
pg_ctl promote -D /var/lib/postgresql/data
```

### 2. Update DNS
Point the database CNAME to the new primary:
```bash
az network dns record-set cname set-record \
  --resource-group mygroup \
  --zone-name internal.example.com \
  --record-set-name db \
  --cname standby-host.internal.example.com
```

### 3. Verify Application Connectivity
```sql
SELECT pg_is_in_recovery();  -- Should return FALSE on new primary
```

### 4. Rebuild Old Primary as Standby
```bash
pg_basebackup -h new-primary -D /var/lib/postgresql/data -R -P
```

## Recovery Time Objective
Target: < 5 minutes total downtime.

## Post-Failover
- Update monitoring alerts for new primary
- Run integration test suite
- Update runbook if any steps changed
