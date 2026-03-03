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
import requests
from google.oauth2.service_account import Credentials

# Forward webhook data to the original voicelab.live endpoint
FORWARD_WEBHOOK_URL = os.getenv("FORWARD_WEBHOOK_URL", "https://www.voicelab.live/webhook/retell")

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
    """Get or create the CALL LOG worksheet with professional CRM styling."""
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

    try:
        call_log = spreadsheet.worksheet("CALL LOG")
    except gspread.exceptions.WorksheetNotFound:
        call_log = spreadsheet.add_worksheet(title="CALL LOG", rows=1000, cols=15)
        headers = [
            "TIMESTAMP", "CALL ID", "FIRST NAME", "LAST NAME", "PHONE",
            "EMAIL", "ADDRESS", "DURATION (s)", "STATUS", "OUTCOME",
            "DISPOSITION", "APPT DATE", "APPT TIME", "SUMMARY", "AGENT"
        ]
        call_log.update('A1:O1', [headers])

        # ---- PROFESSIONAL CRM STYLING ----

        # Dark header row - sleek charcoal/black with white text
        call_log.format('A1:O1', {
            'textFormat': {
                'bold': True,
                'fontSize': 10,
                'fontFamily': 'Inter',
                'foregroundColorStyle': {'rgbColor': {'red': 1, 'green': 1, 'blue': 1}}
            },
            'backgroundColor': {'red': 0.11, 'green': 0.11, 'blue': 0.12},
            'horizontalAlignment': 'CENTER',
            'verticalAlignment': 'MIDDLE',
            'padding': {'top': 8, 'bottom': 8, 'left': 6, 'right': 6}
        })

        # Set column widths for readability
        col_widths = {
            'A': 170, 'B': 120, 'C': 120, 'D': 120, 'E': 140,
            'F': 180, 'G': 220, 'H': 90, 'I': 110, 'J': 140,
            'K': 110, 'L': 110, 'M': 100, 'N': 300, 'O': 120
        }
        sheet_id = call_log.id
        requests_list = []
        for i, (col, width) in enumerate(col_widths.items()):
            requests_list.append({
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'COLUMNS',
                        'startIndex': i,
                        'endIndex': i + 1
                    },
                    'properties': {'pixelSize': width},
                    'fields': 'pixelSize'
                }
            })

        # Freeze header row
        requests_list.append({
            'updateSheetProperties': {
                'properties': {
                    'sheetId': sheet_id,
                    'gridProperties': {'frozenRowCount': 1}
                },
                'fields': 'gridProperties.frozenRowCount'
            }
        })

        # Set default row height
        requests_list.append({
            'updateDimensionProperties': {
                'range': {
                    'sheetId': sheet_id,
                    'dimension': 'ROWS',
                    'startIndex': 1,
                    'endIndex': 1000
                },
                'properties': {'pixelSize': 36},
                'fields': 'pixelSize'
            }
        })

        # Header row height
        requests_list.append({
            'updateDimensionProperties': {
                'range': {
                    'sheetId': sheet_id,
                    'dimension': 'ROWS',
                    'startIndex': 0,
                    'endIndex': 1
                },
                'properties': {'pixelSize': 42},
                'fields': 'pixelSize'
            }
        })

        # Conditional formatting for STATUS column (I = column index 8)
        # BOOKED - green
        rules = [
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'BOOKED',
             'bg': {'red': 0.2, 'green': 0.66, 'blue': 0.33}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'ANSWERED',
             'bg': {'red': 0.0, 'green': 0.48, 'blue': 1.0}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'VOICEMAIL',
             'bg': {'red': 0.96, 'green': 0.76, 'blue': 0.07}, 'fg': {'red': 0.2, 'green': 0.2, 'blue': 0.2}},
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'NO ANSWER',
             'bg': {'red': 0.91, 'green': 0.30, 'blue': 0.24}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'NOT CONNECTED',
             'bg': {'red': 0.6, 'green': 0.6, 'blue': 0.6}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'I2:I1000', 'type': 'TEXT_EQ', 'value': 'ERROR',
             'bg': {'red': 0.55, 'green': 0.14, 'blue': 0.14}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            # DISPOSITION column (K = index 10)
            {'range': 'K2:K1000', 'type': 'TEXT_EQ', 'value': 'Booked',
             'bg': {'red': 0.2, 'green': 0.66, 'blue': 0.33}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'K2:K1000', 'type': 'TEXT_EQ', 'value': 'Declined',
             'bg': {'red': 0.91, 'green': 0.30, 'blue': 0.24}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'K2:K1000', 'type': 'TEXT_EQ', 'value': 'Follow Up',
             'bg': {'red': 0.0, 'green': 0.48, 'blue': 1.0}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
            {'range': 'K2:K1000', 'type': 'TEXT_EQ', 'value': 'Left Message',
             'bg': {'red': 0.96, 'green': 0.76, 'blue': 0.07}, 'fg': {'red': 0.2, 'green': 0.2, 'blue': 0.2}},
            {'range': 'K2:K1000', 'type': 'TEXT_EQ', 'value': 'No Contact',
             'bg': {'red': 0.6, 'green': 0.6, 'blue': 0.6}, 'fg': {'red': 1, 'green': 1, 'blue': 1}},
        ]

        for rule in rules:
            col_letter_start = rule['range'].split(':')[0][0]
            col_letter_end = rule['range'].split(':')[1][0]
            start_col = ord(col_letter_start) - ord('A')
            end_col = ord(col_letter_end) - ord('A') + 1
            requests_list.append({
                'addConditionalFormatRule': {
                    'rule': {
                        'ranges': [{
                            'sheetId': sheet_id,
                            'startRowIndex': 1,
                            'endRowIndex': 1000,
                            'startColumnIndex': start_col,
                            'endColumnIndex': end_col
                        }],
                        'booleanRule': {
                            'condition': {
                                'type': 'TEXT_EQ',
                                'values': [{'userEnteredValue': rule['value']}]
                            },
                            'format': {
                                'backgroundColor': rule['bg'],
                                'textFormat': {
                                    'bold': True,
                                    'fontSize': 9,
                                    'foregroundColorStyle': {'rgbColor': rule['fg']}
                                }
                            }
                        }
                    },
                    'index': 0
                }
            })

        # Alternating row colors (light gray zebra stripe)
        requests_list.append({
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{
                        'sheetId': sheet_id,
                        'startRowIndex': 1,
                        'endRowIndex': 1000,
                        'startColumnIndex': 0,
                        'endColumnIndex': 15
                    }],
                    'booleanRule': {
                        'condition': {
                            'type': 'CUSTOM_FORMULA',
                            'values': [{'userEnteredValue': '=ISEVEN(ROW())'}]
                        },
                        'format': {
                            'backgroundColor': {'red': 0.96, 'green': 0.96, 'blue': 0.97}
                        }
                    }
                },
                'index': 100
            }
        })

        # Default data cell formatting
        call_log.format('A2:O1000', {
            'textFormat': {
                'fontSize': 10,
                'fontFamily': 'Inter'
            },
            'verticalAlignment': 'MIDDLE'
        })

        # Center-align specific columns
        call_log.format('H2:H1000', {'horizontalAlignment': 'CENTER'})
        call_log.format('I2:I1000', {'horizontalAlignment': 'CENTER'})
        call_log.format('K2:K1000', {'horizontalAlignment': 'CENTER'})
        call_log.format('L2:M1000', {'horizontalAlignment': 'CENTER'})

        # Execute all batch formatting
        if requests_list:
            spreadsheet.batch_update({'requests': requests_list})

        logger.info("Created CALL LOG sheet with professional CRM styling")

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
    Retell sends events: call_started, call_ended, call_analyzed
    Data is nested inside a "call" object.
    """
    try:
        raw_data = request.get_json()

        if not raw_data:
            return jsonify({"error": "No data received"}), 400

        event_type = raw_data.get("event", "")
        logger.info(f"Received webhook event: {event_type}")
        logger.info(f"Full payload keys: {list(raw_data.keys())}")

        # Only process call_ended or call_analyzed events
        if event_type not in ["call_ended", "call_analyzed"]:
            # Forward non-logging events to voicelab.live and return
            try:
                requests.post(FORWARD_WEBHOOK_URL, json=raw_data,
                            headers={"Content-Type": "application/json"}, timeout=10)
            except Exception:
                pass
            return jsonify({"success": True, "event": event_type, "action": "skipped"}), 200

        # Extract the call object - Retell nests data inside "call"
        call_data = raw_data.get("call", raw_data)

        logger.info(f"Call data keys: {list(call_data.keys())}")

        # Extract all the data from the call object
        call_id = call_data.get("call_id", "")
        call_status = call_data.get("call_status", "")
        disconnection_reason = call_data.get("disconnection_reason", "")
        duration_ms = call_data.get("duration_ms", call_data.get("call_duration_ms", 0))
        if duration_ms is None:
            duration_ms = 0
        duration = round(duration_ms / 1000, 1)
        timestamp = datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
        agent_name = call_data.get("agent_name", call_data.get("agent_id", ""))

        # Phone number
        phone = call_data.get("to_number", "")

        # Call analysis - can be at call level or nested
        call_analysis = call_data.get("call_analysis", {})
        if call_analysis is None:
            call_analysis = {}

        # Get sentiment and success
        user_sentiment = call_analysis.get("user_sentiment", "")
        call_successful = call_analysis.get("call_successful", False)
        summary = call_analysis.get("call_summary", "")

        # Custom analysis data (Retell post-call extraction fields)
        custom = call_analysis.get("custom_analysis_data", {})
        if custom is None:
            custom = {}

        # Extract contact info from custom analysis (from your Retell agent config)
        first_name = custom.get("customer_name", "")
        last_name = ""
        # Split name if it has a space
        if first_name and " " in first_name:
            parts = first_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1]

        address = custom.get("customer_address", "")
        call_outcome = custom.get("call_outcome", "")
        appointment_booked = custom.get("appointment_booked", False)
        appointment_date = custom.get("appointment_date", "")
        appointment_time = custom.get("appointment_time", "")
        city = custom.get("city", "")
        state = custom.get("state", "")
        zip_code = custom.get("zip_code", "")
        homeowner_status = custom.get("homeowner_status", "")
        utility_company = custom.get("utility_company", "")
        monthly_bill = custom.get("monthly_bill_range", "")

        # Also check metadata for contact info (passed from GHL)
        metadata = call_data.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not first_name:
            first_name = metadata.get("first_name", metadata.get("firstName", ""))
        if not last_name:
            last_name = metadata.get("last_name", metadata.get("lastName", ""))
        if not phone:
            phone = metadata.get("phone", "")
        email = metadata.get("email", "")
        if not address:
            address = metadata.get("address", "")

        # Determine call status/outcome
        if call_outcome == "booked" or appointment_booked:
            status = "BOOKED"
            outcome = "Appointment Set"
            disposition = "Booked"
        elif call_outcome == "not_interested":
            status = "ANSWERED"
            outcome = "Not Interested"
            disposition = "Declined"
        elif call_outcome == "callback":
            status = "ANSWERED"
            outcome = "Callback Requested"
            disposition = "Follow Up"
        elif call_outcome == "no_answer":
            status = "ANSWERED"
            outcome = "No Answer from Customer"
            disposition = "Follow Up"
        elif call_outcome == "voicemail" or disconnection_reason == "voicemail_reached":
            status = "VOICEMAIL"
            outcome = "Voicemail"
            disposition = "Left Message"
        elif disconnection_reason in ["dial_busy", "dial_no_answer"]:
            status = "NO ANSWER"
            outcome = "No Answer"
            disposition = "No Contact"
        elif call_status == "not_connected":
            status = "NOT CONNECTED"
            outcome = disconnection_reason.replace("_", " ").title() if disconnection_reason else "Not Connected"
            disposition = "No Contact"
        elif call_status == "error":
            status = "ERROR"
            outcome = "Call Failed"
            disposition = "System Error"
        elif call_successful:
            status = "ANSWERED"
            outcome = "Successful"
            disposition = "Follow Up"
        elif duration >= 5:
            status = "ANSWERED"
            outcome = call_outcome if call_outcome else "Contacted"
            disposition = "Follow Up"
        else:
            status = "NO ANSWER"
            outcome = call_outcome if call_outcome else "Short Call"
            disposition = "No Contact"

        # Build full address
        full_address = address
        if city or state or zip_code:
            parts = [p for p in [address, city, state, zip_code] if p]
            full_address = ", ".join(parts)

        # ---- LOG TO CALL LOG SHEET ----
        call_log = get_call_log_sheet()
        row_data = [
            timestamp,
            call_id,
            first_name,
            last_name,
            phone,
            email,
            full_address,
            str(duration),
            status,
            outcome,
            disposition,
            appointment_date,
            appointment_time,
            summary[:500] if summary else "",
            agent_name
        ]
        call_log.append_row(row_data, value_input_option="USER_ENTERED")
        logger.info(f"Logged call {call_id} to CALL LOG sheet - Status: {status}, Outcome: {outcome}")

        # ---- UPDATE STATUS ON LEADS SHEET ----
        try:
            leads_sheet = get_google_sheet()
            update_leads_sheet_status(leads_sheet, phone, status)
        except Exception as e:
            logger.error(f"Error updating LEADS sheet: {e}")

        # ---- FORWARD TO VOICELAB.LIVE ----
        try:
            forward_resp = requests.post(
                FORWARD_WEBHOOK_URL,
                json=raw_data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            logger.info(f"Forwarded to {FORWARD_WEBHOOK_URL} - Status: {forward_resp.status_code}")
        except Exception as e:
            logger.error(f"Error forwarding to voicelab.live: {e}")

        return jsonify({
            "success": True,
            "call_id": call_id,
            "status": status,
            "outcome": outcome,
            "logged": True,
            "forwarded": True
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
