#!/usr/bin/env python3
"""Test the actual student notification email."""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GMAIL_SENDER = os.environ.get('GMAIL_SENDER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
BASE_URL = os.environ.get('BASE_URL', 'https://cory105-machines.eecs.berkeley.edu')
CLAIM_EXPIRY_MINUTES = 5

def send_notification_email(to_email, to_name, station_type, station):
    """Send the same email students would receive."""
    claim_token = "TEST_TOKEN_abc123"
    claim_url = f"{BASE_URL}/claim/{claim_token}"
    station_display = "Turtlebot" if station_type == 'turtlebot' else "UR7e"

    subject = f"[EE106A] Station {station} ({station_display}) Available - Claim Now!"

    html_body = f"""
    <html>
    <body>
        <h2>Station {station} ({station_display}) is now available!</h2>
        <p>Hi {to_name},</p>
        <p>You're first in the {station_display} queue, and <strong>Station {station}</strong> just became available.</p>
        <p><strong>You have {CLAIM_EXPIRY_MINUTES} minutes to claim it!</strong></p>
        <p><a href="{claim_url}" style="background-color: #4CAF50; color: white; padding: 14px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Claim Station {station}</a></p>
        <p>Or copy this link: {claim_url}</p>
        <p>If you don't claim within {CLAIM_EXPIRY_MINUTES} minutes, the next person in the queue will be notified.</p>
        <p>Best,<br>EE106A Lab System</p>
        <hr style="margin-top: 30px; border: none; border-top: 1px solid #ccc;">
        <p style="color: #666; font-size: 0.9em;">Go Patriots, Go Celtics, Nobody is Illegal on Stolen Land, Love is Love, Black Lives Matter</p>
    </body>
    </html>
    """

    text_body = f"""
Station {station} ({station_display}) is now available!

Hi {to_name},

You're first in the {station_display} queue, and Station {station} just became available.

You have {CLAIM_EXPIRY_MINUTES} minutes to claim it!

Click here to claim: {claim_url}

If you don't claim within {CLAIM_EXPIRY_MINUTES} minutes, the next person in the queue will be notified.

Best,
EE106A Lab System

---
Go Patriots, Go Celtics, Nobody is Illegal on Stolen Land, Love is Love, Black Lives Matter
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = GMAIL_SENDER
    msg['To'] = to_email

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        print(f"Sending notification email to {to_email}...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
        print("SUCCESS! Email sent.")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == '__main__':
    send_notification_email(
        to_email="danielmunicio@berkeley.edu",
        to_name="Daniel",
        station_type="turtlebot",
        station=5
    )
