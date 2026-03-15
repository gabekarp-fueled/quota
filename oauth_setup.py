#!/usr/bin/env python3
"""Gmail OAuth2 setup script.

Run this once locally to generate a gmail_token.json file that the
Quota agent service uses to send and read email via Gmail.

Prerequisites:
  1. Enable the Gmail API in Google Cloud Console
     https://console.cloud.google.com/apis/library/gmail.googleapis.com
  2. Create OAuth 2.0 credentials (Desktop application type)
  3. Download credentials.json to this directory
  4. Run: python oauth_setup.py

The generated gmail_token.json must be copied to your deployment environment
and its path set in GMAIL_TOKEN_PATH.

Required pip packages:
  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
"""

import json
import os
import sys
from pathlib import Path

SCOPES = [
    "https://mail.google.com/",  # Full Gmail access (send + IMAP)
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "gmail_token.json"


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print(
            "Missing dependencies. Run:\n"
            "  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"
        )
        sys.exit(1)

    if not Path(CREDENTIALS_FILE).exists():
        print(
            f"Error: {CREDENTIALS_FILE} not found.\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials\n"
            "and place it in this directory."
        )
        sys.exit(1)

    creds = None

    # Load existing token if present
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token…")
            creds.refresh(Request())
        else:
            print("Opening browser for OAuth2 authorization…")
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save the token
        Path(TOKEN_FILE).write_text(creds.to_json())
        print(f"\nToken saved to {TOKEN_FILE}")
        print(f"Set GMAIL_TOKEN_PATH={TOKEN_FILE} in your .env or deployment environment.")
    else:
        print(f"Token in {TOKEN_FILE} is still valid.")

    # Verify by reading profile
    try:
        import googleapiclient.discovery
        service = googleapiclient.discovery.build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        print(f"\nConnected as: {profile['emailAddress']}")
        print(f"Total messages: {profile.get('messagesTotal', 'N/A')}")
    except Exception as e:
        print(f"\nWarning: Could not verify Gmail connection: {e}")


if __name__ == "__main__":
    main()
