"""
LEADN LLC - Retell AI Call Logger
Receives post-call webhooks from Retell AI and logs them to Google Sheets.

Setup:
1. pip install flask gspread google-auth requests
2. Create a Google Cloud service account and download the JSON key
3. Share your Google Sheet with the service account email
4. Set environment variables (see .env.example)
5. Run: python app.py
"""

import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets config
GOOGLE_SHEETS_CREDS_FILE = os.getenv("GOOGLE_SHEETS_CREDS_FILE", "service_account.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "LAS VEGAS - ENGLISH - 2026")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "LEADS")

# Retell AI webhook secret (optional, for verification)
RETELL_WEBHOOK_SECRET = os.getenv("RETELL_WEBHOOK_SECRET", "")

# ============================================================
# GOOGLE SHEETS CONNECTION
# ============================================================

def get_google_sheet():
    """Connect to Google Sheets and return the worksheet."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    # Support both file-based and env-based credentials
    creds_json = os.getenv("GOOGLE_SHEETS_CREDS_JSON")
    if creds_json:
        # Credentials passed as JSON string (for Railway/cloud deployment)
        creds_dict = json.loads(creds_json)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # Credentials from file (for local development)
        credentials = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS_FILE, scopes=scopes)

    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    return worksheet


def get_call_log_sheet():
    """Get or create the CALL LOG worksheet."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds_json = os.getenv("GOOGLE_SHEETS_CREDS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        credentials = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS_FILE, scopes=scopes)

    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    # Try to get existing CALL LOG sheet, or create one
    try:
        call_log = spreadsheet.worksheet("CALL LOG")
    except gspread.exceptions.WorksheetNotFound:
        call_log = spreadsheet.add_worksheet(title="CALL LOG", rows=1000, cols=15)
        # Add headers
        headers = [
            "TIMESTAMP",
            "CALL ID",
            "FIRST NAME",
            "LAST NAME",
            "PHONE",
            "EMAIL",
            "ADDRESS",
            "CALL DURATION (sec)",
            "CALL STATUS",
            "CALL OUTCOME",
            "DISPOSITION",
            "APPOINTMENT DATE",
            "APPOINTMENT TIME",
            "TRANSCRIPT SUMMARY",
            "AGENT NAME"
        ]
        call_log.update('A1:O1', [headers])
        # Bold the header row
        call_log.format('A1:O1', {'textFormat': {'bold': True}})
        logger.info("Created new CALL LOG worksheet with headers")

    return call_log


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def determine_call_outcome(call_data):
    """
    Analyze Retell AI call data to determine the outcome.
    Returns: (status, outcome, disposition)
    """
    # Get call analysis from Retell
    call_analysis = call_data.get("call_analysis", {})
    call_status = call_data.get("call_status", "unknown")
    disconnection_reason = call_data.get("disconnection_reason", "")
    duration = call_data.get("call_duration_ms", 0) / 1000  # convert to seconds

    # Determine high-level status
    if call_status == "error":
        status = "ERROR"
        outcome = "Call Failed"
        disposition = "System Error"
    elif call_status == "ended" and duration < 5:
        status = "NO ANSWER"
        outcome = "No Answer"
        disposition = "No Pickup"
    elif disconnection_reason == "voicemail_reached":
        status = "VOICEMAIL"
        outcome = "Voicemail"
        disposition = "Left Message"
    elif disconnection_reason in ["dial_busy", "dial_no_answer"]:
        status = "NO ANSWER"
        outcome = disconnection_reason.replace("dial_", "").replace("_", " ").title()
        disposition = "No Contact"
    elif call_status == "ended" and duration >= 5:
        # Call was answered - check what happened
        user_sentiment = call_analysis.get("user_sentiment", "")
        call_successful = call_analysis.get("call_successful", False)

        if call_successful:
            status = "ANSWERED"
            outcome = "Appointment Set"
            disposition = "Booked"
        elif user_sentiment == "Negative":
            status = "ANSWERED"
            outcome = "Not Interested"
            disposition = "Declined"
        else:
            status = "ANSWERED"
            outcome = "Callback Requested" if "call back" in str(call_analysis).lower() else "Contacted"
            disposition = "Follow Up"
    else:
        status = "UNKNOWN"
        outcome = call_status
        disposition = disconnection_reason

    return status, outcome, disposition


def extract_contact_info(call_data):
    """Extract contact information from Retell AI webhook data."""
    metadata = call_data.get("metadata", {})
    call_analysis = call_data.get("call_analysis", {})

    # Retell passes custom metadata you set when creating the call
    contact = {
        "first_name": metadata.get("first_name", metadata.get("firstName", "")),
        "last_name": metadata.get("last_name", metadata.get("lastName", "")),
        "phone": call_data.get("to_number", metadata.get("phone", "")),
        "email": metadata.get("email", ""),
        "address": metadata.get("address", ""),
    }

    return contact


def update_leads_sheet_status(worksheet, phone, status):
    """Update the STATUS column in the LEADS sheet for a matching phone number."""
    try:
        # Find the phone number in column E (PHONE)
        all_phones = worksheet.col_values(5)  # Column E = PHONE
        for i, cell_phone in enumerate(all_phones):
            # Normalize phone numbers for comparison
            clean_cell = ''.join(filter(str.isdigit, str(cell_phone)))
            clean_phone = ''.join(filter(str.isdigit, str(phone)))

            if clean_cell and clean_phone and clean_cell[-10:] == clean_phone[-10:]:
                row_num = i + 1  # 1-indexed
                worksheet.update_cell(row_num, 6, status)  # Column F = STATUS
                logger.info(f"Updated LEADS sheet row {row_num} status to: {status}")
                return True

        logger.warning(f"Phone {phone} not found in LEADS sheet")
        return False
    except Exception as e:
        logger.error(f"Error updating LEADS sheet: {e}")
        return False


# ============================================================
# WEBHOOK ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "running",
        "service": "LEADN LLC Call Logger",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    })


@app.route("/webhook/retell", methods=["POST"])
def retell_webhook():
    """
    Receives post-call webhook from Retell AI.
    
    Retell sends this data after every call ends:
    - call_id, call_status, call_duration_ms
    - to_number, from_number
    - disconnection_reason
    - call_analysis (sentiment, summary, custom fields)
    - transcript
    - metadata (whatever you passed when creating the call)
    """
    try:
        call_data = request.get_json()

        if not call_data:
            return jsonify({"error": "No data received"}), 400

        logger.info(f"Received webhook for call: {call_data.get('call_id', 'unknown')}")
        logger.info(f"Full payload: {json.dumps(call_data, indent=2)}")

        # Extract all the data
        call_id = call_data.get("call_id", "")
        duration = round(call_data.get("call_duration_ms", 0) / 1000, 1)
        call_analysis = call_data.get("call_analysis", {})
        timestamp = datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
        agent_name = call_data.get("agent_name", call_data.get("agent_id", ""))

        # Get contact info and call outcome
        contact = extract_contact_info(call_data)
        status, outcome, disposition = determine_call_outcome(call_data)

        # Extract appointment info if set
        custom_analysis = call_analysis.get("custom_analysis_data", {})
        appointment_date = custom_analysis.get("appointment_date", "")
        appointment_time = custom_analysis.get("appointment_time", "")
        summary = call_analysis.get("call_summary", "")

        # ---- LOG TO CALL LOG SHEET ----
        call_log = get_call_log_sheet()
        row_data = [
            timestamp,
            call_id,
            contact["first_name"],
            contact["last_name"],
            contact["phone"],
            contact["email"],
            contact["address"],
            str(duration),
            status,
            outcome,
            disposition,
            appointment_date,
            appointment_time,
            summary[:500] if summary else "",  # Truncate long summaries
            agent_name
        ]
        call_log.append_row(row_data, value_input_option="USER_ENTERED")
        logger.info(f"Logged call {call_id} to CALL LOG sheet")

        # ---- UPDATE STATUS ON LEADS SHEET ----
        try:
            leads_sheet = get_google_sheet()
            update_leads_sheet_status(leads_sheet, contact["phone"], status)
        except Exception as e:
            logger.error(f"Error updating LEADS sheet: {e}")

        return jsonify({
            "success": True,
            "call_id": call_id,
            "status": status,
            "outcome": outcome,
            "logged": True
        }), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/webhook/ghl", methods=["POST"])
def ghl_webhook():
    """
    Alternative: Receives webhook from GHL workflow after a call step.
    Use this if you trigger logging from GHL instead of/in addition to Retell.
    """
    try:
        data = request.get_json() or request.form.to_dict()

        if not data:
            return jsonify({"error": "No data received"}), 400

        logger.info(f"Received GHL webhook: {json.dumps(data, indent=2)}")

        timestamp = datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")

        # GHL webhook data structure
        contact_name = data.get("contact_name", data.get("full_name", ""))
        first_name = data.get("first_name", contact_name.split(" ")[0] if contact_name else "")
        last_name = data.get("last_name", " ".join(contact_name.split(" ")[1:]) if contact_name else "")

        call_log = get_call_log_sheet()
        row_data = [
            timestamp,
            data.get("call_id", data.get("id", "")),
            first_name,
            last_name,
            data.get("phone", ""),
            data.get("email", ""),
            data.get("address", ""),
            data.get("call_duration", ""),
            data.get("call_status", ""),
            data.get("call_outcome", ""),
            data.get("disposition", ""),
            data.get("appointment_date", ""),
            data.get("appointment_time", ""),
            data.get("notes", ""),
            data.get("agent_name", "AI Agent")
        ]
        call_log.append_row(row_data, value_input_option="USER_ENTERED")

        return jsonify({"success": True}), 200

    except Exception as e:
        logger.error(f"GHL webhook error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/test-connection", methods=["GET"])
def test_connection():
    """Test the Google Sheets connection."""
    try:
        call_log = get_call_log_sheet()
        leads_sheet = get_google_sheet()

        leads_count = len(leads_sheet.get_all_values()) - 1  # minus header
        call_log_count = len(call_log.get_all_values()) - 1

        return jsonify({
            "status": "connected",
            "spreadsheet": SPREADSHEET_NAME,
            "leads_sheet_rows": leads_count,
            "call_log_rows": call_log_count,
            "message": "Google Sheets connection successful!"
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"Starting LEADN Call Logger on port {port}")
    logger.info(f"Spreadsheet: {SPREADSHEET_NAME}")
    logger.info(f"Webhook URL: http://your-server:{port}/webhook/retell")

    app.run(host="0.0.0.0", port=port, debug=debug)
