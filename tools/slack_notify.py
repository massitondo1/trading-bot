"""
Send trade/portfolio notifications to Slack via an Incoming Webhook.

Setup:
    1. https://api.slack.com/apps -> Create New App -> From scratch
    2. Features -> Incoming Webhooks -> Activate -> Add New Webhook to Workspace
    3. Pick the channel (or a private channel/DM to yourself)
    4. Copy the webhook URL into .env as SLACK_WEBHOOK_URL

Usage as a script:
    python tools/slack_notify.py "Bought 3x AAPL_US_EQ at market, thesis: ..."
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


class SlackNotifyError(RuntimeError):
    pass


def send_message(text: str):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise SlackNotifyError("SLACK_WEBHOOK_URL not set (check your .env)")

    resp = requests.post(webhook_url, json={"text": text}, timeout=10)
    if resp.status_code != 200:
        raise SlackNotifyError(f"Slack webhook returned {resp.status_code}: {resp.text}")
    return True


def _main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    send_message(sys.argv[1])
    print("Sent.")


if __name__ == "__main__":
    _main()
