"""
sheet.py — Load the league list from a Google Sheet instead of a local CSV.

Uses a service account (not OAuth) so batch runs can be fully unattended —
share the sheet with the service account's email as Viewer, then point
GOOGLE_SHEETS_CREDENTIALS at its downloaded JSON key.

Expected header row: Date, email, league_id, Teir
    Date        signup date for this row (any of YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY)
    email       where to send the report
    league_id   Sleeper league ID
    Teir        declared tier: free / normal / dynasty (case-insensitive)

Requires: pip install gspread google-auth
"""

from __future__ import annotations
import os

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def load_rows(sheet_id: str, worksheet: str | None = None) -> list[dict]:
    """Returns one dict per data row, keyed by the sheet's header row."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet) if worksheet else sh.sheet1
    return ws.get_all_records()
