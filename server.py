#!/usr/bin/env python3
"""
ReDry Proposal Builder - Flask API Server
PostgreSQL storage, Stripe payments, SendGrid emails, proposal lifecycle tracking.
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from proposal_generator import generate_proposal_pdf
import os, io, json, uuid, stripe, traceback, psycopg2, psycopg2.extras
from datetime import datetime, timezone
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static")
CORS(app)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "adam@re-dry.com")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "proposals@re-dry.com")

for name, val in [("STRIPE_SECRET_KEY", stripe.api_key), ("STRIPE_PUBLISHABLE_KEY", STRIPE_PK),
                   ("GOOGLE_MAPS_API_KEY", GOOGLE_MAPS_KEY), ("DATABASE_URL", DATABASE_URL),
                   ("SENDGRID_API_KEY", SENDGRID_API_KEY)]:
    if not val: print(f"WARNING: {name} not set.")

# ─── Database ───
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def init_db():
    if not DATABASE_URL:
        print("WARNING: No DATABASE_URL. Database features disabled.")
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY, config JSONB NOT NULL, status TEXT DEFAULT 'draft',
            created_at TIMESTAMPTZ DEFAULT NOW(), sent_at TIMESTAMPTZ,
            viewed_at TIMESTAMPTZ, signed_at TIMESTAMPTZ, paid_at TIMESTAMPTZ)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS signatures (
            id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
            signer_name TEXT, signer_date TEXT, selected_option INT,
            ip_address TEXT, user_agent TEXT, signed_at TIMESTAMPTZ DEFAULT NOW(), proof JSONB)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
            option_num INT, payment_number INT, amount_cents INT,
            method TEXT, stripe_session_id TEXT, paid_at TIMESTAMPTZ DEFAULT NOW(), details JSONB)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS proposal_events (
            id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
            event_type TEXT, details JSONB, created_at TIMESTAMPTZ DEFAULT NOW())""")
        conn.close()
        print("PostgreSQL: Tables ready.")
    except Exception as e:
        print(f"PostgreSQL init error: {e}")

init_db()

def db_store_proposal(pid, config, status="draft"):
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO proposals (id, config, status) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET config=%s, status=%s",
                    (pid, json.dumps(config), status, json.dumps(config), status))
        conn.close()
    except Exception as e: print(f"DB error (store_proposal): {e}")

def db_update_status(pid, status, ts_field=None):
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor(); now = datetime.now(timezone.utc)
        if ts_field:
            cur.execute(f"UPDATE proposals SET status=%s, {ts_field}=%s WHERE id=%s", (status, now, pid))
        else:
            cur.execute("UPDATE proposals SET status=%s WHERE id=%s", (status, pid))
        conn.close()
    except Exception as e: print(f"DB error (update_status): {e}")

def db_log_event(pid, event_type, details=None):
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO proposal_events (proposal_id, event_type, details) VALUES (%s, %s, %s)",
                    (pid, event_type, json.dumps(details or {})))
        conn.close()
    except Exception as e: print(f"DB error (log_event): {e}")

def db_store_signature(pid, sig_data):
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO signatures (proposal_id, signer_name, signer_date, selected_option,
                       ip_address, user_agent, proof) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (pid, sig_data.get("signerName"), sig_data.get("signerDate"),
                     sig_data.get("selectedOption"), sig_data.get("ipAddress"),
                     sig_data.get("userAgent"), json.dumps(sig_data)))
        conn.close()
    except Exception as e: print(f"DB error (store_signature): {e}")

def db_store_payment(pid, pmt_data):
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO payments (proposal_id, option_num, payment_number, amount_cents,
                       method, stripe_session_id, details) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (pid, pmt_data.get("option"), pmt_data.get("paymentNumber"),
                     pmt_data.get("amountCents"), pmt_data.get("method"),
                     pmt_data.get("stripeSessionId"), json.dumps(pmt_data)))
        conn.close()
    except Exception as e: print(f"DB error (store_payment): {e}")

# ─── Email (SendGrid) ───
def send_email(to_emails, subject, html_body, attachments=None):
    if not SENDGRID_API_KEY:
        print(f"SKIP EMAIL (no key): {subject} -> {to_emails}")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
        import base64
        if isinstance(to_emails, str): to_emails = [to_emails]
        message = Mail(from_email=(FROM_EMAIL, "ReDry Proposals"), to_emails=to_emails, subject=subject, html_content=html_body)
        if attachments:
            for fname, fbytes, ftype in attachments:
                att = Attachment(FileContent(base64.b64encode(fbytes).decode()), FileName(fname),
                                FileType(ftype or "application/pdf"), Disposition("attachment"))
                message.add_attachment(att)
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"EMAIL SENT ({response.status_code}): {subject} -> {to_emails}")
        return True
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        traceback.print_exc()
        return False

# ─── File Storage ───
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOGO_PATH = os.path.join(BASE_DIR, "redry_logo.jpg")
PROPOSALS_DIR = os.path.join(BASE_DIR, "proposals")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROPOSALS_DIR, exist_ok=True)

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
OPTION_LABELS = {1: "Pay in Full", 2: "50% Now. 50% at Install.", 3: "Let\u2019s Get Going!"}

# ─── API Routes ───
@app.route("/api/tax-rate")
def get_tax_rate():
    state = request.args.get("state", "").upper().strip()
    rate = STATE_TAX_RATES.get(state, None)
    if rate is None: return jsonify({"state": state, "rate": 0, "note": "Unknown state"})
    return jsonify({"state": state, "rate": rate, "note": "State base rate. Local rates may apply."})

@app.route("/api/stripe-pk")
def get_stripe_pk():
    return jsonify({"pk": STRIPE_PK})

@app.route("/api/google-maps-key")
def get_google_maps_key():
    return jsonify({"key": GOOGLE_MAPS_KEY})

# ─── PDF Generation ───
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
        with open(os.path.join(PROPOSALS_DIR, f"{pid}.pdf"), "wb") as f: f.write(pdf_bytes)
        with open(os.path.join(PROPOSALS_DIR, f"{pid}.json"), "w") as f: json.dump(config, f)
        if vent_map_path:
            import shutil
            shutil.copy2(vent_map_path, os.path.join(PROPOSALS_DIR, f"{pid}_ventmap{os.path.splitext(vent_map_path)[1]}"))
        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=fn, max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Proposal Link Generation ───
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
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf"), "wb") as f: f.write(pdf_bytes)
        config["_ventMapFilename"] = vent_map_filename
        config["_createdAt"] = datetime.now(timezone.utc).isoformat()
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.json"), "w") as f: json.dump(config, f)
        db_store_proposal(proposal_id, config, "draft")
        db_log_event(proposal_id, "created")
        return jsonify({"proposalId": proposal_id, "clientUrl": f"/proposal/{proposal_id}", "pdfUrl": f"/api/proposal/{proposal_id}/pdf"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Send Proposal via Email ───
@app.route("/api/proposal/<pid>/send", methods=["POST"])
def send_proposal(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: cfg = json.load(f)
    data = request.get_json() or {}
    to_email = data.get("email") or cfg.get("clientEmail", "")
    if not to_email: return jsonify({"error": "No email address provided"}), 400
    project = cfg.get("projectName", "Project")
    company = cfg.get("clientCompany", "Client")
    contact = cfg.get("clientContact", "")
    section = cfg.get("projectSection", "")
    base_url = request.host_url.rstrip("/")
    proposal_url = f"{base_url}/proposal/{pid}"
    pdf_path = os.path.join(PROPOSALS_DIR, f"{pid}.pdf")
    pdf_bytes = None
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f: pdf_bytes = f.read()

    # Calculate pricing for email summary
    wet_sf = float(cfg.get("wetSF", 0) or 0)
    rate = float(cfg.get("ratePSF", 2.0) or 2.0)
    vent_total = wet_sf * rate
    tax_rate_val = float(cfg.get("taxRateOverride", "") or cfg.get("taxRate", "") or 0)
    tax_amount = round(vent_total * tax_rate_val, 2)
    subtotal = round(vent_total + tax_amount, 2)
    scan_cost = float(cfg.get("scanCost", 4500) or 4500)
    num_scans = int(cfg.get("numScans", 4) or 4)
    waive_scans = cfg.get("waiveScans", False)
    total_scans = 0 if waive_scans else round(scan_cost * num_scans, 2)
    grand_total = round(subtotal + total_scans, 2)
    total_vents = cfg.get("totalVents", "")
    scan_interval = cfg.get("scanInterval", "3")

    # Payment option visibility
    show_pay_full = cfg.get("showOption0", False)
    show_5050 = cfg.get("showOption1", True)
    show_easy = cfg.get("showOption2", False)

    # Format helpers
    def fc(v): return f"${v:,.2f}"

    # 50/50 deposit
    deposit_50 = round(grand_total / 2, 2)

    # Build scan line
    scan_line = ""
    if not waive_scans:
        scan_line = f"""<tr><td style="padding:6px 12px;font-size:13px;color:#374151">Moisture Monitoring ({num_scans} scans)</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(total_scans)}</td></tr>"""

    # Build payment teaser
    payment_teaser = ""
    teasers = []
    if show_pay_full:
        discount_total = round(grand_total * 0.97, 2)
        teasers.append(f'<strong>Pay in Full</strong> and save 3% ({fc(discount_total)})')
    if show_easy:
        easy_start = round(grand_total * 1.03 * 0.10, 2)
        teasers.append(f'<strong>Get started for just {fc(easy_start)}</strong> with our Easy Start plan')
    if teasers:
        teaser_items = "".join(f'<li style="margin-bottom:4px">{t}</li>' for t in teasers)
        payment_teaser = f"""
        <div style="margin-top:16px;padding:14px 16px;background:#FFF7ED;border:1px solid #FED7AA;border-radius:8px">
          <p style="font-size:13px;color:#9A3412;font-weight:700;margin:0 0 6px 0">Additional payment options available:</p>
          <ul style="font-size:13px;color:#374151;margin:0;padding-left:20px;line-height:1.7">{teaser_items}</ul>
          <p style="font-size:12px;color:#9A3412;margin:8px 0 0 0">View the full proposal to see all options.</p>
        </div>"""

    subject = f"ReDry Proposal: {project}{f' - {section}' if section else ''}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
      <div style="background:#1B2A4A;padding:20px;text-align:center">
        <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span>
      </div>
      <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
        <p style="font-size:15px;line-height:1.7;color:#374151">{f'Hi {contact},' if contact else 'Hello,'}</p>
        <p style="font-size:14px;line-height:1.7;color:#374151">Thank you for the opportunity to work with {company} on <strong>{project}</strong>{f' ({section})' if section else ''}. We appreciate your trust in ReDry to solve the moisture challenges on this roof.</p>
        <p style="font-size:14px;line-height:1.7;color:#374151">Please find your proposal attached and summarized below. You can also review the full details, select your payment option, and accept the proposal online.</p>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Project Summary</p>
          <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Project</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{project}{f' - {section}' if section else ''}</td></tr>
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Affected Area</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{wet_sf:,.0f} SF</td></tr>
            {f'<tr><td style="padding:4px 0;font-size:13px;color:#64748b">2-Way Vents</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{total_vents}</td></tr>' if total_vents else ''}
            {f'<tr><td style="padding:4px 0;font-size:13px;color:#64748b">Monitoring Program</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{num_scans} scans over {int(num_scans) * int(scan_interval)} months</td></tr>' if not waive_scans else ''}
          </table>
        </div>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Investment</p>
          <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:6px 12px;font-size:13px;color:#374151">ReDry 2-Way Vent System ({wet_sf:,.0f} SF)</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(vent_total)}</td></tr>
            {f'<tr><td style="padding:6px 12px;font-size:13px;color:#374151">Rental Tax ({tax_rate_val*100:.2f}%)</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(tax_amount)}</td></tr>' if tax_amount > 0 else ''}
            {scan_line}
            <tr style="border-top:2px solid #1B2A4A"><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A">Total</td><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A;text-align:right">{fc(grand_total)}</td></tr>
          </table>
          <p style="font-size:13px;color:#374151;margin:12px 0 0 0;line-height:1.6">Standard terms: <strong>50% deposit</strong> ({fc(deposit_50)}) upon contract execution, with the remaining <strong>50% due at installation</strong>.</p>
          {payment_teaser}
        </div>

        <div style="margin:24px 0;text-align:center">
          <a href="{proposal_url}" style="display:inline-block;background:#E8943A;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px">View &amp; Accept Proposal</a>
        </div>
        <p style="font-size:13px;color:#64748b;line-height:1.6">If you have any questions at all, just reply to this email. We're happy to walk through the proposal with you or adjust anything to fit your needs.</p>
      </div>
      <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
    </div>"""
    attachments = []
    if pdf_bytes:
        pdf_name = f"ReDry_Proposal_{project.replace(' ','_')}{'_'+section.replace(' ','_') if section else ''}.pdf"
        attachments.append((pdf_name, pdf_bytes, "application/pdf"))
    success = send_email([to_email], subject, html, attachments)
    if success:
        db_update_status(pid, "sent", "sent_at")
        db_log_event(pid, "sent", {"to": to_email})
        send_email([ADMIN_EMAIL], f"Proposal Sent: {project} | {company}",
            f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A"><div style="background:#1B2A4A;padding:16px 20px;text-align:center"><span style="color:#fff;font-size:16px;font-weight:700">RE<span style="color:#E8943A">DRY</span></span></div><div style="padding:20px;background:#fff;border:1px solid #e2e8f0"><p style="font-size:14px;color:#374151"><strong>Proposal sent</strong> to {to_email}</p><p style="font-size:13px;color:#64748b">{project} | {company} | {fc(grand_total)}</p><a href="{proposal_url}" style="font-size:13px;color:#E8943A">View proposal</a></div></div>')
    return jsonify({"sent": success, "to": to_email})

# ─── Proposal Data & Assets ───
@app.route("/api/proposal/<pid>")
def get_proposal_config(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: cfg = json.load(f)
    db_update_status(pid, "viewed", "viewed_at")
    db_log_event(pid, "viewed", {"ip": request.headers.get("X-Forwarded-For", request.remote_addr), "ua": request.headers.get("User-Agent", "")[:200]})
    return jsonify(cfg)

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

# ─── Accept / Sign Proposal ───
@app.route("/api/proposal/<pid>/accept", methods=["POST"])
def accept_proposal(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: cfg = json.load(f)
    acc = request.get_json()
    now = datetime.now(timezone.utc)
    sig_proof = {
        "proposalId": pid, "signerName": acc.get("name", ""), "signerDate": acc.get("date", ""),
        "selectedOption": acc.get("selectedOption", None),
        "ipAddress": request.headers.get("X-Forwarded-For", request.remote_addr),
        "userAgent": request.headers.get("User-Agent", ""),
        "acceptedAtUTC": now.isoformat(), "acceptedAtUnix": int(now.timestamp()),
        "projectName": cfg.get("projectName", ""), "clientCompany": cfg.get("clientCompany", ""),
        "clientContact": cfg.get("clientContact", ""), "clientEmail": cfg.get("clientEmail", ""),
    }
    acc["_acceptedAt"] = now.isoformat()
    acc["_ipAddress"] = sig_proof["ipAddress"]
    acc["_userAgent"] = sig_proof["userAgent"]
    with open(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"), "w") as f: json.dump(acc, f)
    db_store_signature(pid, sig_proof)
    db_update_status(pid, "signed", "signed_at")
    db_log_event(pid, "signed", sig_proof)
    pdf_path = os.path.join(PROPOSALS_DIR, f"{pid}.pdf")
    pdf_bytes = None
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f: pdf_bytes = f.read()
    project = cfg.get("projectName", "Project"); company = cfg.get("clientCompany", "Client")
    contact = cfg.get("clientContact", ""); client_email = cfg.get("clientEmail", "")
    section = cfg.get("projectSection", ""); signer = acc.get("name", "Unknown")
    option_num = acc.get("selectedOption", "?"); option_label = OPTION_LABELS.get(option_num, f"Option {option_num}")
    base_url = request.host_url.rstrip("/")
    admin_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
      <div style="background:#1B2A4A;padding:20px;text-align:center"><span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span></div>
      <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
        <h2 style="color:#16a34a;margin-top:0">&#10003; Proposal Accepted</h2>
        <table style="font-size:14px;line-height:1.8;border-collapse:collapse;width:100%">
          <tr><td style="font-weight:700;padding-right:16px;white-space:nowrap">Project:</td><td>{project}{f' - {section}' if section else ''}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Client:</td><td>{company}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Signed By:</td><td>{signer}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Date Signed:</td><td>{acc.get('date','')}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Payment Option:</td><td>{option_label}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Signed At (UTC):</td><td>{now.strftime('%B %d, %Y at %I:%M %p UTC')}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">IP Address:</td><td style="font-size:12px;color:#64748b">{sig_proof['ipAddress']}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">User Agent:</td><td style="font-size:11px;color:#94a3b8">{sig_proof['userAgent'][:120]}</td></tr>
        </table>
        <div style="margin-top:20px;padding:12px;background:#f8fafc;border-radius:6px;font-size:13px;color:#64748b">The signed proposal PDF is attached. This email serves as confirmation that the above individual electronically accepted this proposal.</div>
        <div style="margin-top:16px;text-align:center"><a href="{base_url}/proposal/{pid}" style="display:inline-block;background:#E8943A;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:700">View Proposal</a></div>
      </div>
      <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
    </div>"""
    attachments = []
    if pdf_bytes:
        pdf_name = f"ReDry_Proposal_{project.replace(' ','_')}{'_'+section.replace(' ','_') if section else ''}.pdf"
        attachments.append((pdf_name, pdf_bytes, "application/pdf"))
    send_email([ADMIN_EMAIL], f"Proposal Accepted: {project} | {company}", admin_html, attachments)
    if client_email:
        client_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
          <div style="background:#1B2A4A;padding:20px;text-align:center"><span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span></div>
          <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
            <h2 style="color:#1B2A4A;margin-top:0">Thank you, {contact or signer}!</h2>
            <p style="font-size:14px;line-height:1.7;color:#374151">Your signed proposal for <strong>{project}</strong> has been received. A copy is attached for your records.</p>
            <p style="font-size:14px;line-height:1.7;color:#374151">Selected payment option: <strong>{option_label}</strong></p>
            <p style="font-size:14px;line-height:1.7;color:#374151">The ReDry team will be in touch shortly to coordinate next steps.</p>
            <div style="margin-top:16px;text-align:center"><a href="{base_url}/proposal/{pid}" style="display:inline-block;background:#E8943A;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:700">View Your Proposal</a></div>
          </div>
          <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
        </div>"""
        send_email([client_email], f"Your Signed ReDry Proposal: {project}", client_html, attachments)
    return jsonify({"status": "accepted", "acceptedAt": now.isoformat()})

# ─── Stripe Checkout ───
@app.route("/api/create-checkout", methods=["POST"])
def create_checkout_session():
    try:
        data = request.get_json()
        amount_cents = int(data.get("amountCents", 0))
        if amount_cents <= 0: return jsonify({"error": "Invalid amount"}), 400
        proposal_id = data.get("proposalId", ""); option = data.get("option", 2)
        payment_number = data.get("paymentNumber", 1); description = data.get("description", "ReDry Vent System Lease")
        payment_method = data.get("paymentMethod", "card")
        client_company = data.get("clientCompany", ""); project_name = data.get("projectName", "")
        pmt_types = ["us_bank_account"] if payment_method == "ach" else ["card"]
        base_url = request.host_url.rstrip("/")
        params = {
            "payment_method_types": pmt_types,
            "line_items": [{"price_data": {"currency": "usd", "product_data": {"name": description, "description": f"{project_name} | {client_company}"}, "unit_amount": amount_cents}, "quantity": 1}],
            "mode": "payment",
            "success_url": f"{base_url}/proposal/{proposal_id}?payment=success&option={option}&pmt={payment_number}&amt={amount_cents}&method={payment_method}",
            "cancel_url": f"{base_url}/proposal/{proposal_id}?payment=cancelled",
            "metadata": {"proposal_id": proposal_id, "option": str(option), "payment_number": str(payment_number)},
        }
        if payment_method == "ach":
            params["payment_method_options"] = {"us_bank_account": {"financial_connections": {"permissions": ["payment_method"]}}}
        session = stripe.checkout.Session.create(**params)
        return jsonify({"url": session.url, "sessionId": session.id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Payment Confirmation ───
@app.route("/api/proposal/<pid>/payment-confirm", methods=["POST"])
def payment_confirm(pid):
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if not os.path.exists(p): return jsonify({"error": "Not found"}), 404
    with open(p) as f: cfg = json.load(f)
    data = request.get_json() or {}; now = datetime.now(timezone.utc)
    option = data.get("option", 1); payment_number = data.get("paymentNumber", 1)
    amount = data.get("amount", 0); method = data.get("method", "card")
    option_label = OPTION_LABELS.get(option, f"Option {option}")
    payment_labels = {1: "Deposit", 2: "Install Payment", 3: "Final Payment"}
    if option == 1: payment_labels = {1: "Full Payment"}
    elif option == 2: payment_labels = {1: "Deposit (50%)", 2: "Balance (50%)"}
    elif option == 3: payment_labels = {1: "Deposit (10%)", 2: "Install Payment (40%)", 3: "Final Payment (50%)"}
    pmt_label = payment_labels.get(payment_number, f"Payment {payment_number}")
    project = cfg.get("projectName", "Project"); company = cfg.get("clientCompany", "Client")
    client_email = cfg.get("clientEmail", ""); section = cfg.get("projectSection", "")
    db_store_payment(pid, {"proposalId": pid, "option": option, "optionLabel": option_label, "paymentNumber": payment_number,
        "paymentLabel": pmt_label, "amountCents": amount, "method": method, "paidAtUTC": now.isoformat(),
        "ipAddress": request.headers.get("X-Forwarded-For", request.remote_addr)})
    db_update_status(pid, "paid", "paid_at")
    db_log_event(pid, "payment", {"option": option, "paymentNumber": payment_number, "amountCents": amount, "method": method})
    amt_str = f"${amount/100:,.2f}" if amount else "Amount pending"
    base_url = request.host_url.rstrip("/")
    admin_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
      <div style="background:#1B2A4A;padding:20px;text-align:center"><span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span></div>
      <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
        <h2 style="color:#16a34a;margin-top:0">&#10003; Payment Received</h2>
        <table style="font-size:14px;line-height:1.8;border-collapse:collapse;width:100%">
          <tr><td style="font-weight:700;padding-right:16px">Project:</td><td>{project}{f' - {section}' if section else ''}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Client:</td><td>{company}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Payment:</td><td>{pmt_label}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Amount:</td><td style="font-size:18px;font-weight:800;color:#16a34a">{amt_str}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Method:</td><td>{'ACH / Bank Transfer' if method == 'ach' else 'Credit Card'}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Date (UTC):</td><td>{now.strftime('%B %d, %Y at %I:%M %p UTC')}</td></tr>
        </table>
        <div style="margin-top:16px;text-align:center"><a href="{base_url}/proposal/{pid}" style="display:inline-block;background:#E8943A;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:700">View Proposal</a></div>
      </div>
      <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
    </div>"""
    send_email([ADMIN_EMAIL], f"Payment Received: {pmt_label} | {project}", admin_html)
    if client_email:
        client_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
          <div style="background:#1B2A4A;padding:20px;text-align:center"><span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span></div>
          <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
            <h2 style="color:#1B2A4A;margin-top:0">Payment Confirmation</h2>
            <p style="font-size:14px;line-height:1.7;color:#374151">Thank you! Your payment of <strong>{amt_str}</strong> for <strong>{project}</strong> has been received.</p>
            <table style="font-size:14px;line-height:1.8;border-collapse:collapse;width:100%;margin-top:12px">
              <tr><td style="font-weight:700;padding-right:16px">Payment:</td><td>{pmt_label}</td></tr>
              <tr><td style="font-weight:700;padding-right:16px">Amount:</td><td>{amt_str}</td></tr>
              <tr><td style="font-weight:700;padding-right:16px">Method:</td><td>{'ACH / Bank Transfer' if method == 'ach' else 'Credit Card'}</td></tr>
              <tr><td style="font-weight:700;padding-right:16px">Date:</td><td>{now.strftime('%B %d, %Y')}</td></tr>
            </table>
            <p style="font-size:13px;color:#64748b;margin-top:16px">This serves as your payment receipt. The ReDry team will be in touch regarding next steps.</p>
          </div>
          <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
        </div>"""
        send_email([client_email], f"Payment Receipt: {project} | {pmt_label}", client_html)
    return jsonify({"status": "confirmed", "paidAt": now.isoformat()})

# ─── Proposal List / Dashboard ───
@app.route("/api/proposals")
def list_proposals():
    proposals = []
    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""SELECT p.id, p.config->>'projectName' as project_name, p.config->>'clientCompany' as client_company,
                p.config->>'clientEmail' as client_email, p.config->>'clientContact' as client_contact,
                p.status, p.created_at, p.sent_at, p.viewed_at, p.signed_at, p.paid_at FROM proposals p ORDER BY p.created_at DESC""")
            for row in cur.fetchall():
                proposals.append({"id": row["id"], "projectName": row["project_name"] or "", "clientCompany": row["client_company"] or "",
                    "clientEmail": row["client_email"] or "", "clientContact": row["client_contact"] or "",
                    "status": row["status"] or "draft",
                    "createdAt": row["created_at"].isoformat() if row["created_at"] else "",
                    "sentAt": row["sent_at"].isoformat() if row["sent_at"] else None,
                    "viewedAt": row["viewed_at"].isoformat() if row["viewed_at"] else None,
                    "signedAt": row["signed_at"].isoformat() if row["signed_at"] else None,
                    "paidAt": row["paid_at"].isoformat() if row["paid_at"] else None})
            conn.close(); return jsonify(proposals)
        except Exception as e: print(f"DB error (list_proposals): {e}")
    for f in os.listdir(PROPOSALS_DIR):
        if f.endswith(".json") and "_accepted" not in f and "_payments" not in f:
            pid = f.replace(".json", "")
            with open(os.path.join(PROPOSALS_DIR, f)) as fh: cfg = json.load(fh)
            proposals.append({"id": pid, "projectName": cfg.get("projectName",""), "clientCompany": cfg.get("clientCompany",""),
                "status": "signed" if os.path.exists(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json")) else "draft",
                "createdAt": cfg.get("_createdAt","")})
    proposals.sort(key=lambda p: p.get("createdAt",""), reverse=True)
    return jsonify(proposals)

@app.route("/api/proposal/<pid>/events")
def get_proposal_events(pid):
    if not DATABASE_URL: return jsonify([])
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT event_type, details, created_at FROM proposal_events WHERE proposal_id=%s ORDER BY created_at", (pid,))
        events = [{"type": r["event_type"], "details": r["details"], "at": r["created_at"].isoformat()} for r in cur.fetchall()]
        conn.close(); return jsonify(events)
    except Exception as e: return jsonify({"error": str(e)}), 500

# ─── Catch-all for React SPA ───
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
