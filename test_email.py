#!/usr/bin/env python3
"""Quick test script to verify email configuration."""
import os
import smtplib
from email.mime.text import MIMEText

GMAIL_SENDER = os.environ.get('GMAIL_SENDER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

def send_test_email():
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_SENDER and GMAIL_APP_PASSWORD environment variables must be set")
        print(f"  GMAIL_SENDER: {'set' if GMAIL_SENDER else 'NOT SET'}")
        print(f"  GMAIL_APP_PASSWORD: {'set' if GMAIL_APP_PASSWORD else 'NOT SET'}")
        return False

    to_email = "danielmunicio@berkeley.edu"
    subject = "[EE106A] Test Email - Lab Notification System"
    body = "This is a test email from the lab notification system. If you received this, the email configuration is working!"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = GMAIL_SENDER
    msg['To'] = to_email

    try:
        print(f"Sending test email from {GMAIL_SENDER} to {to_email}...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
        print("SUCCESS! Email sent.")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == '__main__':
    send_test_email()
