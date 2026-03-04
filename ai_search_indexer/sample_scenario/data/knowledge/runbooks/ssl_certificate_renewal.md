# SSL Certificate Renewal

## Overview
Procedure for renewing TLS/SSL certificates before expiry. Certificates expire every 90 days (Let's Encrypt) or 365 days (commercial CAs).

## Monitoring
Certificates within 14 days of expiry trigger a P2 alert in PagerDuty.

## Renewal Steps

### Automated (Let's Encrypt)
Certbot auto-renews via cron. Verify:
```bash
certbot renew --dry-run
```

### Manual (Commercial CA)
1. Generate CSR: `openssl req -new -key server.key -out server.csr`
2. Submit CSR to CA portal
3. Download signed certificate
4. Install: copy cert + chain to `/etc/ssl/certs/`
5. Reload web server: `nginx -s reload`

## Verification
```bash
openssl s_client -connect example.com:443 -servername example.com </dev/null 2>/dev/null | openssl x509 -noout -dates
```

## Impact of Missed Renewal
- Browsers show security warning
- API clients reject HTTPS connections
- Mobile apps fail certificate pinning
