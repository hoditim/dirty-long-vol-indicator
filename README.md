# Dirty Long Vol Indicator

Automated market monitoring system that detects specific volatility spike conditions and sends SMS alerts.

## Signal Conditions

All four conditions must be true simultaneously:

1. VIX spot > 20
2. VIX spot < 55
3. 3-day VIX percentage change > 25%
4. 10-day realized SPY volatility (annualized) > VIX spot

## Alert Delivery

SMS alerts sent via AT&T email-to-SMS gateway when signal fires.

## Safety Features

- **Data validation**: Checks data freshness and sanity
- **Duplicate prevention**: Never sends same alert twice for same date
- **Weekly heartbeat**: Confirms system operational
- **Error alerting**: Notifies on consecutive data fetch failures
- **Comprehensive logging**: All runs logged to indicator.log

## Usage

```bash
# Normal mode (daily cron)
python3 dirty_long_vol_gate.py

# Test SMS delivery
python3 dirty_long_vol_gate.py --test

# Send heartbeat
python3 dirty_long_vol_gate.py --heartbeat

# Send startup confirmation
python3 dirty_long_vol_gate.py --startup
```

## Cron Schedule

- Daily check: 16:05 ET Mon-Fri
- Weekly heartbeat: 18:00 ET Sunday

## Configuration

Create `.env` file with:

```
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
SMS_TO=phonenumber@txt.att.net
PHONE=+1phonenumber
```

## Files

- `dirty_long_vol_gate.py` - Main indicator script
- `state.json` - Runtime state (auto-generated)
- `indicator.log` - Execution log (auto-generated)
- `cron.log` - Cron execution log (auto-generated)
- `.env` - SMTP credentials (not in git)

## Deployment

Deployed on Hetzner server 178.156.179.218 at /home/agent/dirty-long-vol-indicator/
