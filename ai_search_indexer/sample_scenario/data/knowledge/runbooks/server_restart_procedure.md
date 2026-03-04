# Server Restart Procedure

## Overview
Standard operating procedure for restarting application servers in the production environment.

## Prerequisites
- Verify no active deployments in progress
- Check monitoring dashboard for current load
- Notify on-call team via #ops-alerts channel

## Steps

### 1. Drain Connections
```bash
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
```
Wait for active requests to complete (timeout: 60 seconds).

### 2. Stop Application
```bash
systemctl stop myapp.service
```

### 3. Verify Clean Shutdown
Check logs for "Graceful shutdown complete":
```bash
journalctl -u myapp.service --since "1 minute ago" | grep -i shutdown
```

### 4. Restart
```bash
systemctl start myapp.service
```

### 5. Health Check
```bash
curl -f http://localhost:8080/health || echo "FAILED"
```

## Rollback
If health check fails after 3 attempts (30s interval), escalate to L2 support.

## Estimated Duration
5–10 minutes per server. Full cluster rolling restart: ~45 minutes.
