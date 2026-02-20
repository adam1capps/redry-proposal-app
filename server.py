#!/usr/bin/env python3
"""
ReDry Proposal Builder - Flask API Server
Serves the React frontend and handles PDF generation, Stripe payments, Firebase storage, and email notifications.
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from proposal_generator import generate_proposal_pdf
import os, io, json, uuid, stripe, smtplib, traceback
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

app = Flask(__name__, static_folder="static")
CORS(app)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
SMTP_USER = os.environ.get("SMTP_USER", "proposals@re-dry.com")
SMTP_PASS = os.environ.get("SMTP_APP_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "adam@re-dry.com")

if not stripe.api_key:
    print("WARNING: STRIPE_SECRET_KEY not set. Payment features will not work.")
if not STRIPE_PK:
    print("WARNING: STRIPE_PUBLISHABLE_KEY not set. Payment features will not work.")
if not GOOGLE_MAPS_KEY:
    print("WARNING: GOOGLE_MAPS_API_KEY not set. Address autocomplete will not work.")
if not SMTP_PASS:
    print("WARNING: SMTP_APP_PASSWORD not set. Email notifications will not work.")

# ─── Firebase Setup ───
db = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    fb_creds_json = os.environ.get("FIREBASE_CREDENTIALS", "")
    if fb_creds_json:
        cred = credentials.Certificate(json.loads(fb_creds_json))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("Firebase Firestore connected.")
    else:
        print("WARNING: FIREBASE_CREDENTIALS not set. Database features will not work.")
except Exception as e:
    print(f"WARNING: Firebase init failed: {e}")


# ─── Email Helper ───
def send_email(to_emails, subject, html_body, attachments=None):
    """Send email via Google Workspace SMTP. attachments = list of (filename, bytes)"""
    if not SMTP_PASS:
        print(f"SKIP EMAIL (no password): {subject} -> {to_emails}")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = f"ReDry Proposals <{SMTP_USER}>"
        msg["To"] = ", ".join(to_emails) if isinstance(to_emails, list) else to_emails
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))
        if attachments:
            for fname, fbytes in attachments:
                part = MIMEApplication(fbytes, Name=fname)
                part["Content-Disposition"] = f'attachment; filename="{fname}"'
                msg.attach(part)
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"EMAIL SENT: {subject} -> {to_emails}")
        return True
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        traceback.print_exc()
        return False


# ─── Firestore Helpers ───
def store_signature(pid, sig_data):
    """Store signature proof in Firestore."""
    if not db:
        print(f"SKIP FIRESTORE (not connected): signature for {pid}")
        return None
    try:
        doc_ref = db.collection("signatures").document(pid)
        doc_ref.set(sig_data)
        print(f"FIRESTORE: Signature stored for {pid}")
        return doc_ref.id
    except Exception as e:
        print(f"FIRESTORE ERROR (signature): {e}")
        return None

def store_proposal(pid, config):
    """Store proposal data in Firestore."""
    if not db:
        return None
    try:
        doc_ref = db.collection("proposals").document(pid)
        doc_ref.set(config)
        return doc_ref.id
    except Exception as e:
        print(f"FIRESTORE ERROR (proposal): {e}")
        return None

def store_payment(pid, payment_data):
    """Store payment event in Firestore."""
    if not db:
        return None
    try:
        doc_ref = db.collection("payments").document(f"{pid}_{payment_data.get('payment_number', 1)}")
        doc_ref.set(payment_data)
        print(f"FIRESTORE: Payment stored for {pid}")
        return doc_ref.id
    except Exception as e:
        print(f"FIRESTORE ERROR (payment): {e}")
        return None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOGO_PATH = os.path.join(BASE_DIR, "redry_logo.jpg")
PROPOSALS_DIR = os.path.join(BASE_DIR, "proposals")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROPOSALS_DIR, exist_ok=True)

# State base sales/rental tax rates for equipment
STATE_TAX_RATES = {
    "AL": 0.04, "AK": 0.00, "AZ": 0.056, "AR": 0.065, "CA": 0.0725,
    "CO": 0.029, "CT": 0.0635, "DE": 0.00, "FL": 0.06, "GA": 0.04,
    "HI": 0.04, "ID": 0.06, "IL": 0.0625, "IN": 0.07, "IA": 0.06,
    "KS": 0.065, "KY": 0.06, "LA": 0.05, "ME": 0.055, "MD": 0.06,
    "MA": 0.0625, "MI": 0.06, "MN": 0.06875, "MS": 0.07, "MO": 0.04225,
    "MT": 0.00, "NE": 0.055, "NV": 0.0685, "NH": 0.00, "NJ": 0.06625,
    "NM": 0.05125, "NY": 0.04, "NC": 0.0475, "ND": 0.05, "OH": 0.0575,
    "OK": 0.045, "OR": 0.00, "PA": 0.06, "RI": 0.07, "SC": 0.06,
    "SD": 0.045, "TN": 0.07, "TX": 0.0625, "UT": 0.0610, "VT": 0.06,
    "VA": 0.053, "WA": 0.065, "WV": 0.06, "WI": 0.05, "WY": 0.04, "DC": 0.06
}


@app.route("/api/tax-rate")
def get_tax_rate():
    state = request.args.get("state", "").upper().strip()
    rate = STATE_TAX_RATES.get(state, None)
    if rate is None:
        return jsonify({"state": state, "rate": 0, "note": "Unknown state"})
    return jsonify({"state": state, "rate": rate, "note": "State base rate. Local rates may apply."})


@app.route("/api/stripe-pk")
def get_stripe_pk():
    return jsonify({"pk": STRIPE_PK})


@app.route("/api/google-maps-key")
def get_google_maps_key():
    return jsonify({"key": GOOGLE_MAPS_KEY})


@app.route("/api/create-checkout", methods=["POST"])
def create_checkout_session():
    try:
        data = request.get_json()
        amount_cents = int(data.get("amountCents", 0))
        if amount_cents <= 0:
            return jsonify({"error": "Invalid amount"}), 400

        proposal_id = data.get("proposalId", "")
        option = data.get("option", 2)
        payment_number = data.get("paymentNumber", 1)
        description = data.get("description", "ReDry Vent System Lease")
        payment_method = data.get("paymentMethod", "card")
        client_company = data.get("clientCompany", "")
        project_name = data.get("projectName", "")

        pmt_types = ["us_bank_account"] if payment_method == "ach" else ["card"]

        base_url = request.host_url.rstrip("/")
        params = {
            "payment_method_types": pmt_types,
            "line_items": [{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": description, "description": f"{project_name} | {client_company}"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "success_url": f"{base_url}/proposal/{proposal_id}?payment=success&option={option}&pmt={payment_number}&amt={amount_cents}&method={payment_method}",
            "cancel_url": f"{base_url}/proposal/{proposal_id}?payment=cancelled",
            "metadata": {"proposal_id": proposal_id, "option": str(option), "payment_number": str(payment_number)},
        }
        if payment_method == "ach":
            params["payment_method_options"] = {
                "us_bank_account": {"financial_connections": {"permissions": ["payment_method"]}}
            }

        session = stripe.checkout.Session.create(**params)
        return jsonify({"url": session.url, "sessionId": session.id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-pdf", methods=["POST"])
def generate_pdf():
    try:
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None

        vent_map_path = None
        if vent_map:
            filename = secure_filename(vent_map.filename)
            vent_map_path = os.path.join(UPLOAD_DIR, f"ventmap_{uuid.uuid4().hex[:8]}_{filename}")
            vent_map.save(vent_map_path)

        pdf_bytes = generate_proposal_pdf(config, logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None, vent_map_path=vent_map_path)
        project_name = config.get("projectName", "Project").replace(" ", "_")
        section = config.get("projectSection", "").replace(" ", "_")
        fn = f"ReDry_Proposal_{project_name}_{section}.pdf" if section else f"ReDry_Proposal_{project_name}.pdf"

        pid = uuid.uuid4().hex[:12]
        with open(os.path.join(PROPOSALS_DIR, f"{pid}.pdf"), "wb") as f:
            f.write(pdf_bytes)
        with open(os.path.join(PROPOSALS_DIR, f"{pid}.json"), "w") as f:
            json.dump(config, f)

        if vent_map_path:
            import shutil
            shutil.copy2(vent_map_path, os.path.join(PROPOSALS_DIR, f"{pid}_ventmap{os.path.splitext(vent_map_path)[1]}"))

        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=fn, max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-proposal-link", methods=["POST"])
def generate_proposal_link():
    try:
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None

        proposal_id = uuid.uuid4().hex[:12]
        vent_map_filename = None
        if vent_map:
            ext = os.path.splitext(secure_filename(vent_map.filename))[1]
            vent_map_filename = f"{proposal_id}_ventmap{ext}"
            vent_map.save(os.path.join(PROPOSALS_DIR, vent_map_filename))

        pdf_bytes = generate_proposal_pdf(config, logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None,
            vent_map_path=os.path.join(PROPOSALS_DIR, vent_map_filename) if vent_map_filename else None)
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf"), "wb") as f:
            f.write(pdf_bytes)

        config["_ventMapFilename"] = vent_map_filename
        config["_createdAt"] = datetime.now(timezone.utc).isoformat()
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.json"), "w") as f:
            json.dump(config, f)

        # Store in Firestore
        store_proposal(proposal_id, {**config, "_proposalId": proposal_id})

        return jsonify({"proposalId": proposal_id, "clientUrl": f"/proposal/{proposal_id}", "pdfUrl": f"/api/proposal/{proposal_id}/pdf"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proposal/<pid>")
def get_proposal_config(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: return jsonify(json.load(f))

@app.route("/api/proposal/<pid>/pdf")
def get_proposal_pdf(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.pdf")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    return send_file(p, mimetype="application/pdf")

@app.route("/api/proposal/<pid>/ventmap")
def get_proposal_ventmap(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: cfg = json.load(f)
    vm = cfg.get("_ventMapFilename")
    if not vm: return jsonify({"error": "No vent map"}), 404
    return send_file(os.path.join(PROPOSALS_DIR, vm))

@app.route("/api/proposal/<pid>/accept", methods=["POST"])
def accept_proposal(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404

    # Load proposal config
    with open(p) as f: cfg = json.load(f)

    acc = request.get_json()
    now = datetime.now(timezone.utc)

    # Build signature proof record
    sig_proof = {
        "proposalId": pid,
        "signerName": acc.get("name", ""),
        "signerDate": acc.get("date", ""),
        "selectedOption": acc.get("selectedOption", None),
        "ipAddress": request.headers.get("X-Forwarded-For", request.remote_addr),
        "userAgent": request.headers.get("User-Agent", ""),
        "acceptedAtUTC": now.isoformat(),
        "acceptedAtUnix": int(now.timestamp()),
        "projectName": cfg.get("projectName", ""),
        "clientCompany": cfg.get("clientCompany", ""),
        "clientContact": cfg.get("clientContact", ""),
        "clientEmail": cfg.get("clientEmail", ""),
    }

    # Save locally
    acc["_acceptedAt"] = now.isoformat()
    acc["_ipAddress"] = sig_proof["ipAddress"]
    acc["_userAgent"] = sig_proof["userAgent"]
    with open(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"), "w") as f:
        json.dump(acc, f)

    # Store in Firestore
    store_signature(pid, sig_proof)

    # Load PDF for email attachment
    pdf_path = os.path.join(PROPOSALS_DIR, f"{pid}.pdf")
    pdf_bytes = None
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

    # Build email
    project = cfg.get("projectName", "Project")
    company = cfg.get("clientCompany", "Client")
    contact = cfg.get("clientContact", "")
    client_email = cfg.get("clientEmail", "")
    section = cfg.get("projectSection", "")
    signer = acc.get("name", "Unknown")
    option_num = acc.get("selectedOption", "?")

    option_labels = {1: "Pay in Full", 2: "50% Now. 50% at Install.", 3: "Let\u2019s Get Going!"}
    option_label = option_labels.get(option_num, f"Option {option_num}")

    base_url = request.host_url.rstrip("/")
    subject = f"Proposal Accepted: {project} | {company}"

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1B2A4A;">
        <div style="background: #1B2A4A; padding: 20px; text-align: center;">
            <span style="color: #fff; font-size: 18px; font-weight: 700; letter-spacing: 1px;">RE<span style="color: #E8943A;">DRY</span></span>
        </div>
        <div style="padding: 28px; background: #fff; border: 1px solid #e2e8f0;">
            <h2 style="color: #16a34a; margin-top: 0;">&#10003; Proposal Accepted</h2>
            <table style="font-size: 14px; line-height: 1.8; border-collapse: collapse; width: 100%;">
                <tr><td style="font-weight: 700; padding-right: 16px; white-space: nowrap;">Project:</td><td>{project}{f' - {section}' if section else ''}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Client:</td><td>{company}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Signed By:</td><td>{signer}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Date Signed:</td><td>{acc.get('date', '')}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Payment Option:</td><td>{option_label}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Signed At (UTC):</td><td>{now.strftime('%B %d, %Y at %I:%M %p UTC')}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">IP Address:</td><td style="font-size: 12px; color: #64748b;">{sig_proof['ipAddress']}</td></tr>
            </table>
            <div style="margin-top: 20px; padding: 12px; background: #f8fafc; border-radius: 6px; font-size: 13px; color: #64748b;">
                The signed proposal PDF is attached. This email serves as confirmation that the above individual electronically accepted this proposal.
            </div>
            <div style="margin-top: 16px; text-align: center;">
                <a href="{base_url}/proposal/{pid}" style="display: inline-block; background: #E8943A; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none; font-weight: 700;">View Proposal</a>
            </div>
        </div>
        <div style="padding: 16px; text-align: center; font-size: 11px; color: #94a3b8;">
            ReDry, LLC | Advancing the Science of Moisture Removal
        </div>
    </div>
    """

    attachments = []
    if pdf_bytes:
        pdf_name = f"ReDry_Proposal_{project.replace(' ','_')}_{section.replace(' ','_')}.pdf" if section else f"ReDry_Proposal_{project.replace(' ','_')}.pdf"
        attachments.append((pdf_name, pdf_bytes))

    # Send to admin
    send_email([ADMIN_EMAIL], subject, html, attachments)

    # Send to client (if email provided)
    if client_email:
        client_subject = f"Your Signed ReDry Proposal: {project}"
        client_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1B2A4A;">
            <div style="background: #1B2A4A; padding: 20px; text-align: center;">
                <span style="color: #fff; font-size: 18px; font-weight: 700; letter-spacing: 1px;">RE<span style="color: #E8943A;">DRY</span></span>
            </div>
            <div style="padding: 28px; background: #fff; border: 1px solid #e2e8f0;">
                <h2 style="color: #1B2A4A; margin-top: 0;">Thank you, {contact or signer}!</h2>
                <p style="font-size: 14px; line-height: 1.7; color: #374151;">
                    Your signed proposal for <strong>{project}</strong> has been received. A copy is attached for your records.
                </p>
                <p style="font-size: 14px; line-height: 1.7; color: #374151;">
                    Selected payment option: <strong>{option_label}</strong>
                </p>
                <p style="font-size: 14px; line-height: 1.7; color: #374151;">
                    The ReDry team will be in touch shortly to coordinate next steps.
                </p>
                <div style="margin-top: 16px; text-align: center;">
                    <a href="{base_url}/proposal/{pid}" style="display: inline-block; background: #E8943A; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none; font-weight: 700;">View Your Proposal</a>
                </div>
            </div>
            <div style="padding: 16px; text-align: center; font-size: 11px; color: #94a3b8;">
                ReDry, LLC | Advancing the Science of Moisture Removal
            </div>
        </div>
        """
        send_email([client_email], client_subject, client_html, attachments)

    return jsonify({"status": "accepted", "acceptedAt": now.isoformat()})

@app.route("/api/proposals")
def list_proposals():
    proposals = []
    for f in os.listdir(PROPOSALS_DIR):
        if f.endswith(".json") and "_accepted" not in f and "_payments" not in f:
            pid = f.replace(".json", "")
            with open(os.path.join(PROPOSALS_DIR, f)) as fh: cfg = json.load(fh)
            proposals.append({"id": pid, "projectName": cfg.get("projectName",""), "clientCompany": cfg.get("clientCompany",""),
                "createdAt": cfg.get("_createdAt",""), "accepted": os.path.exists(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"))})
    proposals.sort(key=lambda p: p.get("createdAt",""), reverse=True)
    return jsonify(proposals)


@app.route("/api/proposal/<pid>/payment-confirm", methods=["POST"])
def payment_confirm(pid):
    """Called by client after successful Stripe payment to trigger receipt emails."""
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404

    with open(p) as f: cfg = json.load(f)
    data = request.get_json() or {}
    now = datetime.now(timezone.utc)

    option = data.get("option", 1)
    payment_number = data.get("paymentNumber", 1)
    amount = data.get("amount", 0)
    method = data.get("method", "card")

    option_labels = {1: "Pay in Full", 2: "50% Now. 50% at Install.", 3: "Let\u2019s Get Going!"}
    option_label = option_labels.get(option, f"Option {option}")

    payment_labels = {1: "Deposit", 2: "Install Payment", 3: "Final Payment"}
    if option == 1:
        payment_labels = {1: "Full Payment"}
    elif option == 2:
        payment_labels = {1: "Deposit (50%)", 2: "Balance (50%)"}
    elif option == 3:
        payment_labels = {1: "Deposit (10%)", 2: "Install Payment (40%)", 3: "Final Payment (50%)"}
    pmt_label = payment_labels.get(payment_number, f"Payment {payment_number}")

    project = cfg.get("projectName", "Project")
    company = cfg.get("clientCompany", "Client")
    client_email = cfg.get("clientEmail", "")
    section = cfg.get("projectSection", "")

    # Store payment in Firestore
    store_payment(pid, {
        "proposalId": pid,
        "option": option,
        "optionLabel": option_label,
        "paymentNumber": payment_number,
        "paymentLabel": pmt_label,
        "amountCents": amount,
        "method": method,
        "projectName": project,
        "clientCompany": company,
        "paidAtUTC": now.isoformat(),
        "ipAddress": request.headers.get("X-Forwarded-For", request.remote_addr),
    })

    # Format amount
    amt_str = f"${amount/100:,.2f}" if amount else "Amount pending"

    base_url = request.host_url.rstrip("/")
    subject = f"Payment Received: {pmt_label} | {project}"

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1B2A4A;">
        <div style="background: #1B2A4A; padding: 20px; text-align: center;">
            <span style="color: #fff; font-size: 18px; font-weight: 700; letter-spacing: 1px;">RE<span style="color: #E8943A;">DRY</span></span>
        </div>
        <div style="padding: 28px; background: #fff; border: 1px solid #e2e8f0;">
            <h2 style="color: #16a34a; margin-top: 0;">&#10003; Payment Received</h2>
            <table style="font-size: 14px; line-height: 1.8; border-collapse: collapse; width: 100%;">
                <tr><td style="font-weight: 700; padding-right: 16px; white-space: nowrap;">Project:</td><td>{project}{f' - {section}' if section else ''}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Client:</td><td>{company}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Payment:</td><td>{pmt_label}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Amount:</td><td style="font-size: 18px; font-weight: 800; color: #16a34a;">{amt_str}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Method:</td><td>{'ACH / Bank Transfer' if method == 'ach' else 'Credit Card'}</td></tr>
                <tr><td style="font-weight: 700; padding-right: 16px;">Date (UTC):</td><td>{now.strftime('%B %d, %Y at %I:%M %p UTC')}</td></tr>
            </table>
            <div style="margin-top: 16px; text-align: center;">
                <a href="{base_url}/proposal/{pid}" style="display: inline-block; background: #E8943A; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none; font-weight: 700;">View Proposal</a>
            </div>
        </div>
        <div style="padding: 16px; text-align: center; font-size: 11px; color: #94a3b8;">
            ReDry, LLC | Advancing the Science of Moisture Removal
        </div>
    </div>
    """

    # Send to admin
    send_email([ADMIN_EMAIL], subject, html)

    # Send receipt to client
    if client_email:
        client_subject = f"Payment Receipt: {project} | {pmt_label}"
        client_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1B2A4A;">
            <div style="background: #1B2A4A; padding: 20px; text-align: center;">
                <span style="color: #fff; font-size: 18px; font-weight: 700; letter-spacing: 1px;">RE<span style="color: #E8943A;">DRY</span></span>
            </div>
            <div style="padding: 28px; background: #fff; border: 1px solid #e2e8f0;">
                <h2 style="color: #1B2A4A; margin-top: 0;">Payment Confirmation</h2>
                <p style="font-size: 14px; line-height: 1.7; color: #374151;">
                    Thank you! Your payment of <strong>{amt_str}</strong> for <strong>{project}</strong> has been received.
                </p>
                <table style="font-size: 14px; line-height: 1.8; border-collapse: collapse; width: 100%; margin-top: 12px;">
                    <tr><td style="font-weight: 700; padding-right: 16px;">Payment:</td><td>{pmt_label}</td></tr>
                    <tr><td style="font-weight: 700; padding-right: 16px;">Amount:</td><td>{amt_str}</td></tr>
                    <tr><td style="font-weight: 700; padding-right: 16px;">Method:</td><td>{'ACH / Bank Transfer' if method == 'ach' else 'Credit Card'}</td></tr>
                    <tr><td style="font-weight: 700; padding-right: 16px;">Date:</td><td>{now.strftime('%B %d, %Y')}</td></tr>
                </table>
                <p style="font-size: 13px; color: #64748b; margin-top: 16px;">
                    This serves as your payment receipt. The ReDry team will be in touch regarding next steps.
                </p>
            </div>
            <div style="padding: 16px; text-align: center; font-size: 11px; color: #94a3b8;">
                ReDry, LLC | Advancing the Science of Moisture Removal
            </div>
        </div>
        """
        send_email([client_email], client_subject, client_html)

    return jsonify({"status": "confirmed", "paidAt": now.isoformat()})

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
