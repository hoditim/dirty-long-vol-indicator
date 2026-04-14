#!/usr/bin/env python3
"""
Dirty Long Vol Indicator
Monitors VIX spike conditions and sends SMS alerts via AT&T email-to-SMS gateway.
"""

import argparse
import json
import logging
import os
import smtplib
import socket
import sys
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

# Setup
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "state.json"
LOG_FILE = SCRIPT_DIR / "indicator.log"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class State:
    """Manages persistent state for duplicate prevention and error tracking."""

    def __init__(self):
        self.data = self._load()

    def _load(self):
        """Load state from file."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state file: {e}")
                return self._default_state()
        return self._default_state()

    def _default_state(self):
        """Return default state structure."""
        return {
            "last_signal_date": None,
            "last_heartbeat_date": None,
            "consecutive_failures": 0,
            "last_success_date": None
        }

    def save(self):
        """Save state to file."""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")

    def record_signal(self, date_str):
        """Record that a signal was sent."""
        self.data["last_signal_date"] = date_str
        self.save()

    def record_heartbeat(self, date_str):
        """Record that a heartbeat was sent."""
        self.data["last_heartbeat_date"] = date_str
        self.save()

    def record_success(self):
        """Record successful data fetch."""
        self.data["consecutive_failures"] = 0
        self.data["last_success_date"] = datetime.now().strftime("%Y-%m-%d")
        self.save()

    def record_failure(self):
        """Record failed data fetch."""
        self.data["consecutive_failures"] += 1
        self.save()

    def should_send_signal(self, date_str):
        """Check if we should send signal for this date."""
        return self.data["last_signal_date"] != date_str

    def should_send_heartbeat(self, date_str):
        """Check if we should send heartbeat for this date."""
        return self.data["last_heartbeat_date"] != date_str

    def get_consecutive_failures(self):
        """Get count of consecutive failures."""
        return self.data.get("consecutive_failures", 0)


class SMSAlert:
    """Handles SMS sending via AT&T email-to-SMS gateway."""

    def __init__(self):
        load_dotenv(SCRIPT_DIR / ".env")
        self.smtp_server = os.getenv("SMTP_SERVER")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_pass = os.getenv("SMTP_PASS")
        self.sms_to = os.getenv("SMS_TO")

        if not all([self.smtp_server, self.smtp_user, self.smtp_pass, self.sms_to]):
            raise ValueError("Missing SMTP configuration in .env file")

    def send(self, message, retry=True):
        """Send SMS alert via email-to-SMS gateway."""
        logger.info(f"Attempting to send SMS to {self.sms_to}")
        logger.info(f"Message preview: {message[:100]}...")

        msg = MIMEText(message)
        msg['Subject'] = "DLV Alert"
        msg['From'] = self.smtp_user
        msg['To'] = self.sms_to

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.set_debuglevel(0)
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
                logger.info("SMS sent successfully")
                return True
        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")

            if retry:
                logger.info("Retrying in 30 seconds...")
                time.sleep(30)
                return self.send(message, retry=False)

            logger.error("Final SMS send attempt failed")
            return False


class VolatilityIndicator:
    """Calculates and evaluates the dirty long vol signal."""

    def __init__(self):
        self.vix_data = None
        self.spy_data = None
        self.last_data_date = None

    def fetch_data(self):
        """Fetch VIX and SPY data from yfinance."""
        try:
            logger.info("Fetching market data from yfinance...")

            # Fetch 30 days of data to ensure we have enough for calculations
            self.vix_data = yf.download("^VIX", period="30d", progress=False)
            self.spy_data = yf.download("SPY", period="30d", progress=False)

            if self.vix_data.empty or self.spy_data.empty:
                logger.error("yfinance returned empty data")
                return False

            # Flatten MultiIndex columns if present (yfinance returns MultiIndex for single ticker)
            if isinstance(self.vix_data.columns, pd.MultiIndex):
                self.vix_data.columns = self.vix_data.columns.get_level_values(0)
            if isinstance(self.spy_data.columns, pd.MultiIndex):
                self.spy_data.columns = self.spy_data.columns.get_level_values(0)

            # Get the most recent date
            vix_last_date = self.vix_data.index[-1].date()
            spy_last_date = self.spy_data.index[-1].date()
            self.last_data_date = min(vix_last_date, spy_last_date)

            logger.info(f"Fetched {len(self.vix_data)} VIX data points, last date: {vix_last_date}")
            logger.info(f"Fetched {len(self.spy_data)} SPY data points, last date: {spy_last_date}")

            return True

        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return False

    def validate_data(self):
        """Validate fetched data quality and freshness."""
        today = datetime.now().date()

        # Check data freshness (within 2 business days)
        days_diff = (today - self.last_data_date).days

        # Account for weekends
        if today.weekday() == 0:  # Monday
            max_days = 3
        elif today.weekday() == 6:  # Sunday
            max_days = 2
        else:
            max_days = 2

        if days_diff > max_days:
            error_msg = f"DLV DATA ERROR: yfinance returning stale data. Last date: {self.last_data_date}. Investigate immediately."
            logger.error(error_msg)
            return False, error_msg

        # Check sufficient data points
        if len(self.vix_data) < 15 or len(self.spy_data) < 15:
            logger.error(f"Insufficient data: VIX={len(self.vix_data)}, SPY={len(self.spy_data)}")
            return False, None

        # Check VIX values are in sane range
        vix_current = self.vix_data['Close'].iloc[-1]
        if vix_current < 5 or vix_current > 100:
            logger.error(f"VIX value out of sane range: {vix_current}")
            return False, None

        # Check SPY values are in sane range
        spy_current = self.spy_data['Close'].iloc[-1]
        if spy_current < 100 or spy_current > 1000:
            logger.error(f"SPY value out of sane range: {spy_current}")
            return False, None

        logger.info("Data validation passed")
        return True, None

    def calculate_signal(self):
        """Calculate all signal components and check conditions."""
        # Get current values
        vix_current = float(self.vix_data['Close'].iloc[-1])
        spy_current = float(self.spy_data['Close'].iloc[-1])

        # Calculate 3-day VIX change
        vix_3d_ago = float(self.vix_data['Close'].iloc[-4])
        vix_3d_change_pct = ((vix_current - vix_3d_ago) / vix_3d_ago) * 100

        # Calculate 10-day realized volatility
        spy_close = self.spy_data['Close'].iloc[-11:].values
        log_returns = np.log(spy_close[1:] / spy_close[:-1])
        rv10 = float(np.std(log_returns) * np.sqrt(252) * 100)

        # Calculate premium
        premium = rv10 - vix_current

        # Check all four conditions
        condition1 = vix_current > 20
        condition2 = vix_current < 55
        condition3 = vix_3d_change_pct > 25
        condition4 = rv10 > vix_current

        signal_fired = all([condition1, condition2, condition3, condition4])

        # Log all components
        logger.info("="*60)
        logger.info("SIGNAL COMPONENTS:")
        logger.info(f"  VIX spot: {vix_current:.2f} (need >20 and <55) - {'✓' if condition1 and condition2 else '✗'}")
        logger.info(f"  VIX 3-day ago: {vix_3d_ago:.2f}")
        logger.info(f"  VIX 3d change: {vix_3d_change_pct:.2f}% (need >25%) - {'✓' if condition3 else '✗'}")
        logger.info(f"  RV10: {rv10:.2f}")
        logger.info(f"  RV10 > VIX: {rv10:.2f} > {vix_current:.2f} (premium: {premium:.2f}) - {'✓' if condition4 else '✗'}")
        logger.info(f"  SPY: ${spy_current:.2f}")
        logger.info(f"  Signal fired: {signal_fired}")
        logger.info("="*60)

        return {
            "signal_fired": signal_fired,
            "vix": vix_current,
            "vix_3d_change_pct": vix_3d_change_pct,
            "rv10": rv10,
            "premium": premium,
            "spy": spy_current,
            "date": self.last_data_date.strftime("%Y-%m-%d")
        }

    def get_summary(self):
        """Get a summary of current values for heartbeat."""
        if self.vix_data is None or self.spy_data is None:
            return None

        vix_current = float(self.vix_data['Close'].iloc[-1])

        # Calculate RV10
        spy_close = self.spy_data['Close'].iloc[-11:].values
        log_returns = np.log(spy_close[1:] / spy_close[:-1])
        rv10 = float(np.std(log_returns) * np.sqrt(252) * 100)

        return {
            "vix": vix_current,
            "rv10": rv10,
            "date": self.last_data_date.strftime("%Y-%m-%d")
        }


def create_signal_message(signal_data):
    """Create SMS message for signal alert."""
    strike_approx = round(signal_data['spy'] * 0.95)

    message = f"""DIRTY LONG VOL SIGNAL
{signal_data['date']}
VIX={signal_data['vix']:.1f} (3d: {signal_data['vix_3d_change_pct']:.1f}%)
RV10={signal_data['rv10']:.1f} > VIX by {signal_data['premium']:.1f}
SPY=${signal_data['spy']:.2f}

ACTION: Buy SPY 5% OTM puts at tomorrow's open
Strike: ~${strike_approx}
DTE: 8-14 days
Hold: 3 trading days
Risk: $500-1000 premium max"""

    return message


def create_test_message():
    """Create SMS message for test mode."""
    message = """*** TEST — NOT A REAL SIGNAL ***

This is a test of the Dirty Long Vol indicator SMS alert system.

If you receive this, the system is functioning correctly.

Timestamp: {ts}
Hostname: {host}""".format(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S ET"),
        host=socket.gethostname()
    )
    return message


def create_heartbeat_message(summary):
    """Create SMS message for heartbeat."""
    if summary is None:
        return f"""DLV HEARTBEAT ERROR: data fetch failed. Investigate.
Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M:%S ET")}"""

    message = f"""DLV heartbeat {summary['date']}. VIX={summary['vix']:.1f}, RV10={summary['rv10']:.1f}, no signal. Data as of {summary['date']}."""
    return message


def create_startup_message():
    """Create SMS message for startup confirmation."""
    message = f"""DLV indicator deployed and operational on {socket.gethostname()} at {datetime.now().strftime("%Y-%m-%d %H:%M:%S ET")}."""
    return message


def create_data_error_message(error_msg):
    """Create SMS message for data error."""
    return error_msg


def main():
    parser = argparse.ArgumentParser(description="Dirty Long Vol Indicator")
    parser.add_argument("--test", action="store_true", help="Send test SMS")
    parser.add_argument("--heartbeat", action="store_true", help="Send heartbeat SMS")
    parser.add_argument("--startup", action="store_true", help="Send startup confirmation SMS")
    args = parser.parse_args()

    # Determine mode
    if args.test:
        mode = "TEST"
    elif args.heartbeat:
        mode = "HEARTBEAT"
    elif args.startup:
        mode = "STARTUP"
    else:
        mode = "NORMAL"

    logger.info(f"Starting in {mode} mode")

    # Initialize components
    state = State()
    sms = SMSAlert()
    indicator = VolatilityIndicator()

    # Handle different modes
    if args.test:
        # TEST MODE
        logger.info("TEST MODE: Sending test SMS")
        message = create_test_message()
        sms.send(message)
        return

    elif args.startup:
        # STARTUP MODE
        logger.info("STARTUP MODE: Sending startup confirmation")
        message = create_startup_message()
        sms.send(message)
        return

    elif args.heartbeat:
        # HEARTBEAT MODE
        today = datetime.now().strftime("%Y-%m-%d")

        if not state.should_send_heartbeat(today):
            logger.info("Heartbeat already sent today, skipping")
            return

        logger.info("HEARTBEAT MODE: Fetching data and sending heartbeat")

        if indicator.fetch_data():
            state.record_success()
            valid, error_msg = indicator.validate_data()

            if valid:
                summary = indicator.get_summary()
                message = create_heartbeat_message(summary)
            else:
                message = create_heartbeat_message(None)
        else:
            state.record_failure()
            message = create_heartbeat_message(None)

        if sms.send(message):
            state.record_heartbeat(today)
        return

    else:
        # NORMAL MODE
        logger.info("NORMAL MODE: Checking for signal")

        # Fetch data
        if not indicator.fetch_data():
            state.record_failure()

            # Check if we should send error alert
            failures = state.get_consecutive_failures()
            if failures >= 3:
                logger.error(f"Data fetch has failed {failures} consecutive times")
                error_msg = f"DLV ERROR: data fetch has failed {failures} consecutive days. Check yfinance / network."
                sms.send(error_msg)

            return

        # Data fetch successful
        state.record_success()

        # Validate data
        valid, error_msg = indicator.validate_data()
        if not valid:
            if error_msg:
                sms.send(error_msg)
            return

        # Calculate signal
        signal_data = indicator.calculate_signal()

        if signal_data['signal_fired']:
            today = signal_data['date']

            if state.should_send_signal(today):
                logger.info("SIGNAL FIRED! Sending SMS alert")
                message = create_signal_message(signal_data)

                if sms.send(message):
                    state.record_signal(today)
                    logger.info("Signal alert sent successfully")
                else:
                    logger.error("Failed to send signal alert")
            else:
                logger.info("Signal fired but already sent for this date")
        else:
            logger.info("No signal today")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
