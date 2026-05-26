#!/usr/bin/env python3
"""
ReDry Proposal Builder - Flask API Server
PostgreSQL storage, Stripe payments, SendGrid emails, proposal lifecycle tracking.
"""

from flask import Flask, request, jsonify, send_file, send_from_directory, session
from flask_cors import CORS
from proposal_generator import generate_proposal_pdf, generate_client_pdf, generate_fixed_proposal_pdf, generate_fixed_client_pdf
import os, io, json, uuid, stripe, traceback, psycopg2, psycopg2.extras, hashlib, secrets, functools, hmac, re
from datetime import datetime, timezone
from html import escape as html_escape
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else []
CORS(app, origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"], supports_credentials=True)

_PID_RE = re.compile(r'^[a-f0-9]{6,64}$')
def validate_pid(pid):
    if not _PID_RE.match(pid):
        return None
    return pid

# ─── Auth ───
TEAM_PASSWORD = os.environ.get("TEAM_PASSWORD", "")
if not TEAM_PASSWORD:
    print("WARNING: TEAM_PASSWORD not set. Auth will be disabled.")

def require_auth(f):
    """Decorator to protect admin/builder routes."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not TEAM_PASSWORD:
            return f(*args, **kwargs)
        token = request.headers.get("X-Auth-Token") or request.cookies.get("auth_token") or ""
        stored = session.get("auth_token", "")
        if not token or not stored or not hmac.compare_digest(token, stored):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "adam@re-dry.com")
NOTIFY_EMAILS = [e.strip() for e in os.environ.get("NOTIFY_EMAILS", "adam@re-dry.com,regina@re-dry.com").split(",") if e.strip()]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "adam@re-dry.com")
REPLY_TO_EMAIL = os.environ.get("REPLY_TO_EMAIL", "adam@re-dry.com")

for name, val in [("STRIPE_SECRET_KEY", stripe.api_key), ("STRIPE_PUBLISHABLE_KEY", STRIPE_PK),
                   ("GOOGLE_MAPS_API_KEY", GOOGLE_MAPS_KEY), ("DATABASE_URL", DATABASE_URL),
                   ("SENDGRID_API_KEY", SENDGRID_API_KEY)]:
    if not val: print(f"WARNING: {name} not set.")

# ─── Auth Routes ───
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json() or {}
    pw = data.get("password", "")
    if not TEAM_PASSWORD:
        token = secrets.token_hex(32)
        session["auth_token"] = token
        return jsonify({"ok": True, "token": token})
    if hmac.compare_digest(pw, TEAM_PASSWORD):
        token = secrets.token_hex(32)
        session["auth_token"] = token
        return jsonify({"ok": True, "token": token})
    return jsonify({"error": "Invalid password"}), 401

@app.route("/api/auth/check")
def auth_check():
    if not TEAM_PASSWORD:
        return jsonify({"authenticated": True, "authRequired": False})
    token = request.headers.get("X-Auth-Token") or request.cookies.get("auth_token") or ""
    stored = session.get("auth_token", "")
    if token and stored and hmac.compare_digest(token, stored):
        return jsonify({"authenticated": True, "authRequired": True})
    return jsonify({"authenticated": False, "authRequired": True})

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("auth_token", None)
    return jsonify({"ok": True})

@app.route("/health")
def health_check():
    return jsonify({"status": "ok"})

# ─── Database ───
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

_INIT_DB_OK = False
_INIT_DB_LAST_ERROR = None

def _exec_ignore_dup(cur, sql):
    """Run a DDL statement, swallowing duplicate-object errors from races
    between concurrent gunicorn workers. CREATE...IF NOT EXISTS is not
    fully atomic in Postgres' system catalog, so two workers running the
    same DDL at the same time can collide on pg_class_relname_nsp_index."""
    try:
        cur.execute(sql)
    except (psycopg2.errors.DuplicateTable,
            psycopg2.errors.DuplicateObject,
            psycopg2.errors.UniqueViolation) as e:
        print(f"init_db: ignoring expected race on concurrent DDL: {e}")

def init_db():
    global _INIT_DB_OK, _INIT_DB_LAST_ERROR
    if not DATABASE_URL:
        print("WARNING: No DATABASE_URL. Database features disabled.")
        return
    import time
    delays = [0, 2, 4, 8, 16]
    for attempt, delay in enumerate(delays):
        if delay: time.sleep(delay)
        try:
            conn = get_db()
            cur = conn.cursor()
            _exec_ignore_dup(cur, """CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, config JSONB NOT NULL, status TEXT DEFAULT 'draft',
                created_at TIMESTAMPTZ DEFAULT NOW(), sent_at TIMESTAMPTZ,
                viewed_at TIMESTAMPTZ, signed_at TIMESTAMPTZ, paid_at TIMESTAMPTZ)""")
            # Vent map binary lives with the proposal so it survives ephemeral
            # filesystem wipes on Render/Railway free tiers.
            _exec_ignore_dup(cur, "ALTER TABLE proposals ADD COLUMN IF NOT EXISTS vent_map_bytes BYTEA")
            _exec_ignore_dup(cur, "ALTER TABLE proposals ADD COLUMN IF NOT EXISTS vent_map_filename TEXT")
            _exec_ignore_dup(cur, """CREATE TABLE IF NOT EXISTS signatures (
                id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
                signer_name TEXT, signer_date TEXT, selected_option INT,
                ip_address TEXT, user_agent TEXT, signed_at TIMESTAMPTZ DEFAULT NOW(), proof JSONB)""")
            _exec_ignore_dup(cur, """CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
                option_num INT, payment_number INT, amount_cents INT,
                method TEXT, stripe_session_id TEXT, paid_at TIMESTAMPTZ DEFAULT NOW(), details JSONB)""")
            _exec_ignore_dup(cur, """CREATE TABLE IF NOT EXISTS proposal_events (
                id SERIAL PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id),
                event_type TEXT, details JSONB, created_at TIMESTAMPTZ DEFAULT NOW())""")
            _exec_ignore_dup(cur, """CREATE UNIQUE INDEX IF NOT EXISTS payments_stripe_session_unique
                ON payments(stripe_session_id) WHERE stripe_session_id IS NOT NULL AND stripe_session_id != ''""")
            conn.close()
            _INIT_DB_OK = True
            _INIT_DB_LAST_ERROR = None
            print("PostgreSQL: Tables ready.")
            return
        except Exception as e:
            _INIT_DB_LAST_ERROR = str(e)
            print(f"PostgreSQL init attempt {attempt + 1}/{len(delays)} failed: {e}")
    print(f"PostgreSQL init: gave up after {len(delays)} attempts. Last error: {_INIT_DB_LAST_ERROR}")

init_db()

def db_store_proposal(pid, config, status="draft", vent_map_bytes=None, vent_map_filename=None):
    """Persist the proposal config (and optional vent map binary) to Postgres.

    Raises on failure so callers can surface the error rather than silently
    handing the user a link that only exists on ephemeral storage.
    """
    if not DATABASE_URL: return
    conn = get_db(); cur = conn.cursor()
    try:
        # Self-heal: ensure the vent_map_* columns exist even if init_db lost
        # a startup race or never completed. Idempotent and cheap.
        _exec_ignore_dup(cur, "ALTER TABLE proposals ADD COLUMN IF NOT EXISTS vent_map_bytes BYTEA")
        _exec_ignore_dup(cur, "ALTER TABLE proposals ADD COLUMN IF NOT EXISTS vent_map_filename TEXT")
        # ON CONFLICT preserves the existing row's `status` and
        # `created_at`. The lifecycle (draft -> sent -> viewed -> signed ->
        # paid) is owned by db_update_status; refreshing a proposal's share
        # link via generate_proposal_link must not bounce status back to
        # 'draft' on an already-sent proposal.
        if vent_map_bytes is not None:
            cur.execute("""INSERT INTO proposals (id, config, status, vent_map_bytes, vent_map_filename)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET config=EXCLUDED.config,
                               vent_map_bytes=EXCLUDED.vent_map_bytes, vent_map_filename=EXCLUDED.vent_map_filename""",
                        (pid, json.dumps(config), status, psycopg2.Binary(vent_map_bytes), vent_map_filename))
        else:
            cur.execute("""INSERT INTO proposals (id, config, status) VALUES (%s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET config=EXCLUDED.config""",
                        (pid, json.dumps(config), status))
    finally:
        conn.close()

def db_load_vent_map(pid):
    """Return (bytes, filename) for the proposal's vent map, or (None, None)."""
    if not DATABASE_URL: return (None, None)
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT vent_map_bytes, vent_map_filename FROM proposals WHERE id=%s", (pid,))
        row = cur.fetchone(); conn.close()
        if not row or row[0] is None: return (None, None)
        return (bytes(row[0]), row[1])
    except Exception as e:
        print(f"DB error (load_vent_map): {e}")
        return (None, None)

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

_STATUS_ORDER = {"draft": 0, "sent": 1, "viewed": 2, "signed": 3, "paid": 4}
def db_update_status_if_earlier(pid, status, ts_field=None):
    """Only update status if the new status is later in the lifecycle."""
    if not DATABASE_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT status FROM proposals WHERE id=%s", (pid,))
        row = cur.fetchone()
        if row and _STATUS_ORDER.get(row[0], -1) >= _STATUS_ORDER.get(status, 0):
            conn.close()
            return
        conn.close()
        db_update_status(pid, status, ts_field)
    except Exception as e: print(f"DB error (update_status_if_earlier): {e}")

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
    if not DATABASE_URL: return True
    try:
        conn = get_db(); cur = conn.cursor()
        sid = pmt_data.get("stripeSessionId") or None
        cur.execute("""INSERT INTO payments (proposal_id, option_num, payment_number, amount_cents,
                       method, stripe_session_id, details) VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (pid, pmt_data.get("option"), pmt_data.get("paymentNumber"),
                     pmt_data.get("amountCents"), pmt_data.get("method"),
                     sid, json.dumps(pmt_data)))
        inserted = cur.rowcount > 0
        conn.close()
        return inserted
    except Exception as e:
        print(f"DB error (store_payment): {e}")
        return False

# ─── Email (SendGrid) ───
def send_email(to_emails, subject, html_body, attachments=None, reply_to=None):
    if not SENDGRID_API_KEY:
        print(f"SKIP EMAIL (no key): {subject} -> {to_emails}")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (Mail, Attachment, FileContent, FileName,
            FileType, Disposition, ReplyTo, Header)
        import base64
        if isinstance(to_emails, str): to_emails = [to_emails]

        message = Mail(
            from_email=(FROM_EMAIL, "Adam Capps | ReDry"),
            to_emails=to_emails,
            subject=subject,
            html_content=html_body
        )

        # Reply-To so client replies go to a real person
        message.reply_to = ReplyTo(reply_to or REPLY_TO_EMAIL, "Adam Capps")

        # Deliverability headers
        message.header = Header("X-Priority", "3")  # Normal priority (not spammy)
        message.header = Header("X-Mailer", "ReDry Proposal System")

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

def load_proposal_config(pid):
    """Load config from Postgres (authoritative) with a local-file fallback
    only when the DB is unreachable. Render's filesystem is ephemeral, so
    stale fixtures on disk must never override durable DB rows."""
    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT config FROM proposals WHERE id=%s", (pid,))
            row = cur.fetchone(); conn.close()
            if row:
                cfg = row["config"]
                if isinstance(cfg, str): cfg = json.loads(cfg)
                print(f"[load_proposal_config] Loaded {pid} from DB")
                return cfg
        except Exception as e:
            print(f"DB error (load_proposal_config): {e}; falling back to local file")
    p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
    if os.path.exists(p):
        print(f"[load_proposal_config] Loaded {pid} from file (DB miss or unreachable)")
        with open(p) as f: return json.load(f)
    return None

def is_proposal_accepted(pid):
    """Check if proposal already accepted. DB signature is authoritative;
    the local _accepted.json marker is only a fallback when the DB is
    unreachable, since ephemeral filesystem markers can disappear on redeploy
    while DB rows persist."""
    if DATABASE_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM signatures WHERE proposal_id=%s LIMIT 1", (pid,))
            row = cur.fetchone(); conn.close()
            return row is not None
        except Exception as e:
            print(f"DB error (is_proposal_accepted): {e}; falling back to local marker")
    return os.path.exists(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"))

def get_or_regenerate_pdf(pid, client_facing=False):
    """Return PDF bytes for the proposal. Uses cached file if available,
    otherwise regenerates from DB config."""
    suffix = "_client.pdf" if client_facing else ".pdf"
    p = os.path.join(PROPOSALS_DIR, f"{pid}{suffix}")
    if os.path.exists(p):
        with open(p, "rb") as f: return f.read()
    cfg = load_proposal_config(pid)
    if not cfg: return None
    lease_type = cfg.get("leaseType", "performance")
    logo = LOGO_PATH if os.path.exists(LOGO_PATH) else None
    vmap = _ensure_vent_map_file(pid, cfg)
    try:
        if client_facing:
            pdf_bytes = (generate_fixed_client_pdf(cfg, logo_path=logo, vent_map_path=vmap)
                         if lease_type == "fixed"
                         else generate_client_pdf(cfg, logo_path=logo, vent_map_path=vmap))
        else:
            pdf_bytes = (generate_fixed_proposal_pdf(cfg, logo_path=logo, vent_map_path=vmap)
                         if lease_type == "fixed"
                         else generate_proposal_pdf(cfg, logo_path=logo, vent_map_path=vmap))
        try:
            with open(p, "wb") as f: f.write(pdf_bytes)
        except Exception: pass
        return pdf_bytes
    except Exception as e:
        print(f"PDF regeneration error for {pid}: {e}")
        return None

def _ensure_vent_map_file(pid, cfg):
    """Return a local path to the vent map image, restoring it from the DB
    if the ephemeral file was wiped. Returns None when no vent map exists."""
    vmf = cfg.get("_ventMapFilename")
    if not vmf:
        return None
    vmp = os.path.join(PROPOSALS_DIR, vmf)
    if os.path.exists(vmp):
        return vmp
    data, db_name = db_load_vent_map(pid)
    if not data:
        return None
    target_name = db_name or vmf
    target = os.path.join(PROPOSALS_DIR, target_name)
    try:
        with open(target, "wb") as f: f.write(data)
        return target
    except Exception as e:
        print(f"Vent map restore error for {pid}: {e}")
        return None

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

def parse_tax_rate(cfg):
    """Parse tax rate from config. Always stored as a decimal (e.g. 0.085 for 8.5%)."""
    try:
        val = float(cfg.get("taxRate", "") or 0)
    except (ValueError, TypeError):
        return 0
    if val > 1:
        val = val / 100
    return val

#─── API Routes ───
@app.route("/api/tax-rate")
def get_tax_rate():
    state = request.args.get("state", "").upper().strip()
    rate = STATE_TAX_RATES.get(state, None)
    if rate is None: return jsonify({"state": state, "rate": 0, "note": "Unknown state"})
    return jsonify({"state": state, "rate": rate, "note": "State base rate. Local rates may apply."})

@app.route("/api/stripe-pk")
@require_auth
def get_stripe_pk():
    return jsonify({"pk": STRIPE_PK})

@app.route("/api/google-maps-key")
@require_auth
def get_google_maps_key():
    return jsonify({"key": GOOGLE_MAPS_KEY})

# ─── PDF Generation ───
# Canonical set of form keys the proposal builder produces. Mirrors the
# SPA's defaultForm + defaultFixedForm objects (static/index.html). Server-set
# metadata (_baseUrl, _createdAt, _ventMapFilename) is deliberately NOT in
# the allowlist — the handlers overwrite those after sanitize, and the client
# has no business sending them. _proposalId is allowed because the SPA round-
# trips it to request an update of an existing proposal.
ALLOWED_CONFIG_KEYS = frozenset({
    # shared
    "leaseType",
    "clientCompany", "clientContact", "clientTitle", "clientPhone", "clientEmail",
    "projectName", "projectAddress", "projectCity", "projectState", "projectZip",
    "projectSection",
    "proposalDate", "validDays", "taxRate",
    # performance-lease
    "wetSF", "ratePSF", "scanCost", "numScans", "scanInterval", "totalVents",
    "waiveScans", "hideScans", "hidePricing",
    "showOption0", "showOption1", "showOption2",
    "showCustomOption", "customOptionLabel", "customOptionAdj", "customOptionPayments",
    # fixed-lease
    "numVents", "ventRate", "leaseTerm", "installFee",
    "shipAddress", "shipCity", "shipState", "shipZip",
    # round-trip metadata
    "_proposalId",
})

def _sanitize_incoming_config(config):
    """Whitelist filter: drop any keys not in ALLOWED_CONFIG_KEYS.

    Replaces an earlier blacklist that only popped `error`. A blacklist is
    one bug behind by design — when an older SPA bug merged {"error":...}
    into form state and saveForm() persisted it, every subsequent
    /api/generate-proposal-link POST carried the junk into the DB. A
    whitelist closes the class rather than chasing individual keys.
    Mutates `config` in place to preserve the existing caller contract.
    """
    if not isinstance(config, dict):
        return config
    dropped = [k for k in list(config) if k not in ALLOWED_CONFIG_KEYS]
    for k in dropped:
        config.pop(k, None)
    if dropped:
        print(f"[sanitize] dropped non-whitelisted config keys: {dropped}")
    return config

@app.route("/api/generate-pdf", methods=["POST"])
@require_auth
def generate_pdf():
    try:
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None
        _sanitize_incoming_config(config)
        vent_map_path = None
        if vent_map:
            filename = secure_filename(vent_map.filename)
            vent_map_path = os.path.join(UPLOAD_DIR, f"ventmap_{uuid.uuid4().hex[:8]}_{filename}")
            vent_map.save(vent_map_path)
        config["_baseUrl"] = request.host_url.rstrip("/")
        lease_type = config.get("leaseType", "performance")
        if lease_type == "fixed":
            pdf_bytes = generate_fixed_proposal_pdf(config, logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None, vent_map_path=vent_map_path)
        else:
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
@require_auth
def generate_proposal_link():
    try:
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None
        _sanitize_incoming_config(config)
        # If the client sent a _proposalId AND that row already exists in
        # Postgres, treat this as an update of the existing proposal rather
        # than minting a new UUID. Prevents the duplication-on-every-Get-Link
        # behavior when a user loads an existing proposal from Past Proposals,
        # tweaks something, and re-generates the share link. _proposalId is
        # always server-managed metadata, so pop it off the incoming config
        # either way; we put it back in below at the canonical spot.
        requested_pid = config.pop("_proposalId", None)
        proposal_id = None
        existing_created_at = None
        if requested_pid and validate_pid(requested_pid) and DATABASE_URL:
            try:
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT config->>'_createdAt' FROM proposals WHERE id=%s", (requested_pid,))
                row = cur.fetchone()
                if row is not None:
                    proposal_id = requested_pid
                    existing_created_at = row[0]
                conn.close()
            except Exception as e:
                print(f"generate_proposal_link: lookup of requested_pid {requested_pid} failed, falling back to new: {e}")
        is_update = bool(proposal_id)
        if not proposal_id:
            proposal_id = uuid.uuid4().hex[:12]
        vent_map_filename = None
        vent_map_bytes = None
        if vent_map:
            ext = os.path.splitext(secure_filename(vent_map.filename))[1]
            vent_map_filename = f"{proposal_id}_ventmap{ext}"
            vent_map_bytes = vent_map.read()
            with open(os.path.join(PROPOSALS_DIR, vent_map_filename), "wb") as f:
                f.write(vent_map_bytes)
        config["_baseUrl"] = request.host_url.rstrip("/")
        lease_type = config.get("leaseType", "performance")
        _logo = LOGO_PATH if os.path.exists(LOGO_PATH) else None
        _vmap = os.path.join(PROPOSALS_DIR, vent_map_filename) if vent_map_filename else None
        if lease_type == "fixed":
            pdf_bytes = generate_fixed_proposal_pdf(config, logo_path=_logo, vent_map_path=_vmap)
        else:
            pdf_bytes = generate_proposal_pdf(config, logo_path=_logo, vent_map_path=_vmap)
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf"), "wb") as f: f.write(pdf_bytes)
        config["_ventMapFilename"] = vent_map_filename
        # Preserve the original creation timestamp when updating; only stamp
        # one for genuinely new proposals. The SPA strips _createdAt from
        # cfg on load, so the existing value has to come from the DB
        # lookup above.
        config["_createdAt"] = existing_created_at or datetime.now(timezone.utc).isoformat()
        config["_proposalId"] = proposal_id
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.json"), "w") as f: json.dump(config, f)
        # Render/Railway free tiers wipe the filesystem on every redeploy, so the
        # database is the only durable store. If we can't write to it, fail the
        # request rather than handing back a link that will 404 after the next
        # restart.
        if DATABASE_URL:
            try:
                db_store_proposal(proposal_id, config, "draft",
                                  vent_map_bytes=vent_map_bytes,
                                  vent_map_filename=vent_map_filename)
            except Exception as e:
                print(f"DB error (store_proposal): {e}")
                traceback.print_exc()
                return jsonify({"error": "Could not save proposal to database. Please try again.", "detail": str(e)}), 503
        db_log_event(proposal_id, "created")
        return jsonify({"proposalId": proposal_id, "clientUrl": f"/proposal/{proposal_id}", "pdfUrl": f"/api/proposal/{proposal_id}/pdf"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ─── Send Proposal via Email ───
@app.route("/api/proposal/<pid>/send", methods=["POST"])
@require_auth
def send_proposal(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    to_email = data.get("email") or cfg.get("clientEmail", "")
    if not to_email: return jsonify({"error": "No email address provided"}), 400
    project = html_escape(cfg.get("projectName", "Project"))
    company = html_escape(cfg.get("clientCompany", "Client"))
    contact = html_escape(cfg.get("clientContact", ""))
    section = html_escape(cfg.get("projectSection", ""))
    base_url = request.host_url.rstrip("/")
    proposal_url = f"{base_url}/proposal/{pid}"
    # Generate client-facing PDF (no pricing) and save it
    cfg["_baseUrl"] = base_url
    vent_map_filename = cfg.get("_ventMapFilename")
    vent_map_path = os.path.join(PROPOSALS_DIR, vent_map_filename) if vent_map_filename else None
    _logo = LOGO_PATH if os.path.exists(LOGO_PATH) else None
    lease_type = cfg.get("leaseType", "performance")
    if lease_type == "fixed":
        client_pdf_bytes = generate_fixed_client_pdf(cfg, logo_path=_logo, vent_map_path=vent_map_path)
    else:
        client_pdf_bytes = generate_client_pdf(cfg, logo_path=_logo, vent_map_path=vent_map_path)
    client_pdf_path = os.path.join(PROPOSALS_DIR, f"{pid}_client.pdf")
    with open(client_pdf_path, "wb") as f: f.write(client_pdf_bytes)

    # Format helpers
    def fc(v): return f"${v:,.2f}"

    if lease_type == "fixed":
        # Fixed lease pricing
        num_vents = int(cfg.get("numVents", 0) or 0)
        vent_rate = float(cfg.get("ventRate", 1000) or 1000)
        lease_term = int(cfg.get("leaseTerm", 12) or 12)
        install_fee = float(cfg.get("installFee", 0) or 0)
        lease_total = num_vents * vent_rate
        tax_rate_val = parse_tax_rate(cfg)
        tax_amount = round(lease_total * tax_rate_val, 2)
        grand_total = round(lease_total + tax_amount + install_fee, 2)

        install_line = ""
        if install_fee > 0:
            install_line = f'<tr><td style="padding:6px 12px;font-size:13px;color:#374151">Install / Setup Fee</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(install_fee)}</td></tr>'

        ship_addr_parts = [html_escape(cfg.get("shipAddress", "")), ", ".join(filter(None, [html_escape(cfg.get("shipCity", "")), html_escape(cfg.get("shipState", ""))])), html_escape(cfg.get("shipZip", ""))]
        full_ship_addr = ", ".join(filter(None, ship_addr_parts))
        ship_row = f'<tr><td style="padding:4px 0;font-size:13px;color:#64748b">Ship To</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{full_ship_addr}</td></tr>' if full_ship_addr else ''

        subject = f"ReDry Fixed Lease Proposal: {project}{f' - {section}' if section else ''}"
        html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
      <div style="background:#1B2A4A;padding:20px;text-align:center">
        <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span>
      </div>
      <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
        <p style="font-size:15px;line-height:1.7;color:#374151">{f'Hi {contact},' if contact else 'Hello,'}</p>
        <p style="font-size:14px;line-height:1.7;color:#374151">Thank you for the opportunity to work with {company} on <strong>{project}</strong>{f' ({section})' if section else ''}. We appreciate your trust in ReDry to solve the moisture challenges on this roof.</p>
        <p style="font-size:14px;line-height:1.7;color:#374151">Please find your fixed lease proposal attached and summarized below. You can also review the full details and accept the proposal online.</p>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Lease Summary</p>
          <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Project</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{project}{f' - {section}' if section else ''}</td></tr>
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Vents</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{num_vents}</td></tr>
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Rate</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{fc(vent_rate)} / vent</td></tr>
            <tr><td style="padding:4px 0;font-size:13px;color:#64748b">Lease Term</td><td style="padding:4px 0;font-size:13px;color:#1B2A4A;font-weight:600;text-align:right">{lease_term} months</td></tr>
            {ship_row}
          </table>
        </div>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Investment</p>
          <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:6px 12px;font-size:13px;color:#374151">Vent Rental ({num_vents} &times; {fc(vent_rate)})</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(lease_total)}</td></tr>
            {f'<tr><td style="padding:6px 12px;font-size:13px;color:#374151">Rental Tax ({tax_rate_val*100:.2f}%)</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(tax_amount)}</td></tr>' if tax_amount > 0 else ''}
            {install_line}
            <tr style="border-top:2px solid #1B2A4A"><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A">Total</td><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A;text-align:right">{fc(grand_total)}</td></tr>
          </table>
          <p style="font-size:13px;color:#374151;margin:12px 0 0 0;line-height:1.6">Payment of <strong>{fc(grand_total)}</strong> is due in full upon contract execution.</p>
        </div>

        <div style="margin:24px 0;text-align:center">
          <a href="{proposal_url}" style="display:inline-block;background:#E8943A;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px">View &amp; Accept Proposal</a>
        </div>
        <p style="font-size:13px;color:#64748b;line-height:1.6">If you have any questions at all, just reply to this email. We're happy to walk through the proposal with you or adjust anything to fit your needs.</p>
      </div>
      <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
    </div>"""
    else:
        # Performance lease pricing (existing)
        wet_sf = float(cfg.get("wetSF", 0) or 0)
        rate = float(cfg.get("ratePSF", 2.0) or 2.0)
        vent_total = wet_sf * rate
        tax_rate_val = parse_tax_rate(cfg)
        tax_amount = round(vent_total * tax_rate_val, 2)
        subtotal = round(vent_total + tax_amount, 2)
        scan_cost = float(cfg.get("scanCost", 4500) or 4500)
        num_scans = int(cfg.get("numScans", 4) or 4)
        waive_scans = cfg.get("waiveScans", False)
        total_scans = 0 if waive_scans else round(scan_cost * num_scans, 2)
        grand_total = round(subtotal + total_scans, 2)
        total_vents = cfg.get("totalVents", "")
        scan_interval = cfg.get("scanInterval", "3")

        show_pay_full = cfg.get("showOption0", False)
        show_5050 = cfg.get("showOption1", True)
        show_easy = cfg.get("showOption2", False)

        deposit_50 = round(grand_total / 2, 2)

        scan_line = ""
        if not waive_scans:
            scan_line = f"""<tr><td style="padding:6px 12px;font-size:13px;color:#374151">Moisture Monitoring ({num_scans} scans)</td><td style="padding:6px 12px;font-size:13px;color:#374151;text-align:right">{fc(total_scans)}</td></tr>"""

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
    if client_pdf_bytes:
        pdf_name = f"ReDry_Overview_{project.replace(' ','_')}{'_'+section.replace(' ','_') if section else ''}.pdf"
        attachments.append((pdf_name, client_pdf_bytes, "application/pdf"))
    success = send_email([to_email], subject, html, attachments)
    if success:
        db_update_status(pid, "sent", "sent_at")
        db_log_event(pid, "sent", {"to": to_email})
        send_email(NOTIFY_EMAILS, f"Proposal Sent: {project} | {company}",
            f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A"><div style="background:#1B2A4A;padding:16px 20px;text-align:center"><span style="color:#fff;font-size:16px;font-weight:700">RE<span style="color:#E8943A">DRY</span></span></div><div style="padding:20px;background:#fff;border:1px solid #e2e8f0"><p style="font-size:14px;color:#374151"><strong>Proposal sent</strong> to {html_escape(to_email)}</p><p style="font-size:13px;color:#64748b">{project} | {company} | {fc(grand_total)}</p><a href="{proposal_url}" style="font-size:13px;color:#E8943A">View proposal</a></div></div>')
    return jsonify({"sent": success, "to": to_email})

# ─── Send Proposal for Approval ───
@app.route("/api/proposal/<pid>/send-for-approval", methods=["POST"])
@require_auth
def send_for_approval(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    company = html_escape(cfg.get("clientCompany", "Client"))
    project = html_escape(cfg.get("projectName", "Project"))
    address = html_escape(cfg.get("projectAddress", ""))
    section = html_escape(cfg.get("projectSection", ""))
    contact = html_escape(cfg.get("clientContact", ""))
    client_email = cfg.get("clientEmail", "")
    base_url = request.host_url.rstrip("/")
    proposal_url = f"{base_url}/proposal/{pid}"

    # Calculate pricing for summary
    lease_type = cfg.get("leaseType", "performance")
    tax_rate_val = parse_tax_rate(cfg)
    def fc(v): return f"${v:,.2f}"

    if lease_type == "fixed":
        num_vents = int(cfg.get("numVents", 0) or 0)
        vent_rate = float(cfg.get("ventRate", 1000) or 1000)
        lease_term = int(cfg.get("leaseTerm", 12) or 12)
        install_fee = float(cfg.get("installFee", 0) or 0)
        lease_total = num_vents * vent_rate
        tax_amount = round(lease_total * tax_rate_val, 2)
        grand_total = round(lease_total + tax_amount + install_fee, 2)
        pricing_rows = f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">Vent Rental ({num_vents} × {fc(vent_rate)})</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(lease_total)}</td></tr>'
        if tax_amount > 0:
            pricing_rows += f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">Tax ({tax_rate_val*100:.2f}%)</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(tax_amount)}</td></tr>'
        if install_fee > 0:
            pricing_rows += f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">Install / Setup Fee</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(install_fee)}</td></tr>'
    else:
        wet_sf = float(cfg.get("wetSF", 0) or 0)
        rate = float(cfg.get("ratePSF", 2.0) or 2.0)
        vent_total = wet_sf * rate
        tax_amount = round(vent_total * tax_rate_val, 2)
        subtotal = round(vent_total + tax_amount, 2)
        scan_cost = float(cfg.get("scanCost", 4500) or 4500)
        num_scans = int(cfg.get("numScans", 4) or 4)
        waive_scans = cfg.get("waiveScans", False)
        total_scans = 0 if waive_scans else round(scan_cost * num_scans, 2)
        grand_total = round(subtotal + total_scans, 2)
        pricing_rows = f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">ReDry Vent System ({wet_sf:,.0f} SF @ {fc(rate)}/SF)</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(vent_total)}</td></tr>'
        if tax_amount > 0:
            pricing_rows += f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">Tax ({tax_rate_val*100:.2f}%)</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(tax_amount)}</td></tr>'
        if not waive_scans:
            pricing_rows += f'<tr><td style="padding:4px 12px;font-size:13px;color:#374151">Monitoring ({num_scans} scans)</td><td style="padding:4px 12px;font-size:13px;color:#374151;text-align:right">{fc(total_scans)}</td></tr>'

    subject = f"APPROVAL REQUESTED: {company} | {address}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1B2A4A">
      <div style="background:#1B2A4A;padding:20px;text-align:center">
        <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:1px">RE<span style="color:#E8943A">DRY</span></span>
      </div>
      <div style="padding:28px;background:#fff;border:1px solid #e2e8f0">
        <div style="padding:12px 16px;background:#FEF3C7;border:1px solid #FCD34D;border-radius:8px;margin-bottom:20px">
          <p style="font-size:14px;font-weight:700;color:#92400E;margin:0">&#9888; Approval Requested</p>
          <p style="font-size:13px;color:#92400E;margin:4px 0 0 0">A team member has submitted this proposal for your review and approval before sending to the client.</p>
        </div>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Proposal Details</p>
          <table style="width:100%;border-collapse:collapse;font-size:13px;line-height:1.8">
            <tr><td style="font-weight:700;color:#64748b;padding-right:16px">Type:</td><td style="color:#1B2A4A;font-weight:600">{'Fixed Lease' if lease_type == 'fixed' else 'Performance Lease'}</td></tr>
            <tr><td style="font-weight:700;color:#64748b;padding-right:16px">Contractor:</td><td style="color:#1B2A4A;font-weight:600">{company}</td></tr>
            {f'<tr><td style="font-weight:700;color:#64748b;padding-right:16px">Contact:</td><td style="color:#1B2A4A">{contact}</td></tr>' if contact else ''}
            {f'<tr><td style="font-weight:700;color:#64748b;padding-right:16px">Email:</td><td style="color:#1B2A4A">{html_escape(client_email)}</td></tr>' if client_email else ''}
            <tr><td style="font-weight:700;color:#64748b;padding-right:16px">Project:</td><td style="color:#1B2A4A;font-weight:600">{project}{f' - {section}' if section else ''}</td></tr>
            <tr><td style="font-weight:700;color:#64748b;padding-right:16px">Address:</td><td style="color:#1B2A4A">{address}</td></tr>
          </table>
        </div>

        <div style="margin:20px 0;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px">
          <p style="font-size:13px;font-weight:700;color:#1B2A4A;margin:0 0 10px 0;text-transform:uppercase;letter-spacing:0.5px">Pricing Summary</p>
          <table style="width:100%;border-collapse:collapse">
            {pricing_rows}
            <tr style="border-top:2px solid #1B2A4A"><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A">Total</td><td style="padding:10px 12px;font-size:15px;font-weight:800;color:#1B2A4A;text-align:right">{fc(grand_total)}</td></tr>
          </table>
        </div>

        <div style="margin:24px 0;text-align:center">
          <a href="{proposal_url}" style="display:inline-block;background:#E8943A;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px">Review Full Proposal</a>
        </div>
        <div style="margin:12px 0;text-align:center">
          <a href="{base_url}/api/proposal/{pid}/pdf" style="font-size:13px;color:#E8943A;font-weight:600;text-decoration:none">Download PDF &#8594;</a>
        </div>
      </div>
      <div style="padding:16px;text-align:center;font-size:11px;color:#94a3b8">ReDry, LLC | Advancing the Science of Moisture Removal</div>
    </div>"""
    success = send_email([ADMIN_EMAIL], subject, html)
    if success:
        db_log_event(pid, "approval_requested", {"to": ADMIN_EMAIL})
    return jsonify({"sent": success, "to": ADMIN_EMAIL})

# ─── Proposal Data & Assets ───
@app.route("/api/proposal/<pid>")
def get_proposal_config(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    db_update_status_if_earlier(pid, "viewed", "viewed_at")
    db_log_event(pid, "viewed", {"ip": request.headers.get("X-Forwarded-For", request.remote_addr), "ua": request.headers.get("User-Agent", "")[:200]})
    return jsonify(cfg)

@app.route("/api/proposal/<pid>/pdf")
def get_proposal_pdf(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    pdf_bytes = get_or_regenerate_pdf(pid)
    if not pdf_bytes: return jsonify({"error": "Not found"}), 404
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf")

@app.route("/api/proposal/<pid>/client-pdf")
def get_client_pdf(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    pdf_bytes = get_or_regenerate_pdf(pid, client_facing=True)
    if not pdf_bytes: return jsonify({"error": "Not found"}), 404
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf")

@app.route("/api/proposal/<pid>/ventmap")
def get_proposal_ventmap(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    if not cfg.get("_ventMapFilename"): return jsonify({"error": "No vent map"}), 404
    vmp = _ensure_vent_map_file(pid, cfg)
    if not vmp: return jsonify({"error": "Vent map file not available"}), 404
    return send_file(vmp)

# ─── Accept / Sign Proposal ───
@app.route("/api/proposal/<pid>/accept", methods=["POST"])
def accept_proposal(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    if is_proposal_accepted(pid): return jsonify({"error": "already_accepted", "status": "accepted"}), 409
    acc = request.get_json() or {}
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
    try:
        with open(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"), "w") as f: json.dump(acc, f)
    except Exception: pass
    db_store_signature(pid, sig_proof)
    db_update_status(pid, "signed", "signed_at")
    db_log_event(pid, "signed", sig_proof)
    pdf_bytes = get_or_regenerate_pdf(pid)
    project = html_escape(cfg.get("projectName", "Project")); company = html_escape(cfg.get("clientCompany", "Client"))
    contact = html_escape(cfg.get("clientContact", "")); client_email = cfg.get("clientEmail", "")
    section = html_escape(cfg.get("projectSection", "")); signer = html_escape(acc.get("name", "Unknown"))
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
          <tr><td style="font-weight:700;padding-right:16px">Date Signed:</td><td>{html_escape(acc.get('date',''))}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Payment Option:</td><td>{option_label}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">Signed At (UTC):</td><td>{now.strftime('%B %d, %Y at %I:%M %p UTC')}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">IP Address:</td><td style="font-size:12px;color:#64748b">{html_escape(sig_proof['ipAddress'])}</td></tr>
          <tr><td style="font-weight:700;padding-right:16px">User Agent:</td><td style="font-size:11px;color:#94a3b8">{html_escape(sig_proof['userAgent'][:120])}</td></tr>
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
    send_email(NOTIFY_EMAILS, f"Proposal Accepted: {project} | {company}", admin_html, attachments)
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
        proposal_id = data.get("proposalId", "")
        if not validate_pid(proposal_id): return jsonify({"error": "Invalid proposal ID"}), 400
        cfg = load_proposal_config(proposal_id)
        if not cfg: return jsonify({"error": "Proposal not found"}), 404
        option = data.get("option", 2)
        if cfg.get("leaseType") == "fixed" and option != 1:
            return jsonify({"error": "Fixed Lease requires full payment (option 1)"}), 400
        payment_number = data.get("paymentNumber", 1); description = data.get("description", "ReDry Vent System Lease")
        payment_method = data.get("paymentMethod", "card")
        client_company = data.get("clientCompany", ""); project_name = data.get("projectName", "")
        pmt_types = ["us_bank_account"] if payment_method == "ach" else ["card"]
        base_url = request.host_url.rstrip("/")
        params = {
            "payment_method_types": pmt_types,
            "line_items": [{"price_data": {"currency": "usd", "product_data": {"name": description, "description": f"{project_name} | {client_company}"}, "unit_amount": amount_cents}, "quantity": 1}],
            "mode": "payment",
            "success_url": f"{base_url}/proposal/{proposal_id}?payment=success&option={option}&pmt={payment_number}&amt={amount_cents}&method={payment_method}&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base_url}/proposal/{proposal_id}?payment=cancelled",
            "metadata": {"proposal_id": proposal_id, "option": str(option), "payment_number": str(payment_number)},
        }
        if payment_method == "ach":
            params["payment_method_options"] = {"us_bank_account": {"financial_connections": {"permissions": ["payment_method"]}}}
        session = stripe.checkout.Session.create(**params)
        return jsonify({"url": session.url, "sessionId": session.id})
    except Exception as e:
        print(f"Checkout error: {e}")
        return jsonify({"error": "Could not create checkout session"}), 500

# ─── Payment Confirmation ───
def _record_payment(pid, cfg, option, payment_number, amount, method, ip_address="", stripe_session_id=""):
    """Shared logic for recording a verified payment. Called from both the API route and webhook."""
    now = datetime.now(timezone.utc)
    option_label = OPTION_LABELS.get(option, f"Option {option}")
    payment_labels = {1: "Deposit", 2: "Install Payment", 3: "Final Payment"}
    if option == 1: payment_labels = {1: "Full Payment"}
    elif option == 2: payment_labels = {1: "Deposit (50%)", 2: "Balance (50%)"}
    elif option == 3: payment_labels = {1: "Deposit (10%)", 2: "Install Payment (40%)", 3: "Final Payment (50%)"}
    pmt_label = payment_labels.get(payment_number, f"Payment {payment_number}")
    project = html_escape(cfg.get("projectName", "Project")); company = html_escape(cfg.get("clientCompany", "Client"))
    client_email = cfg.get("clientEmail", ""); section = html_escape(cfg.get("projectSection", ""))
    inserted = db_store_payment(pid, {"proposalId": pid, "option": option, "optionLabel": option_label, "paymentNumber": payment_number,
        "paymentLabel": pmt_label, "amountCents": amount, "method": method, "paidAtUTC": now.isoformat(),
        "ipAddress": ip_address, "stripeSessionId": stripe_session_id})
    if not inserted:
        return False
    db_update_status(pid, "paid", "paid_at")
    db_log_event(pid, "payment", {"option": option, "paymentNumber": payment_number, "amountCents": amount, "method": method})
    amt_str = f"${amount/100:,.2f}" if amount else "Amount pending"
    base_url = os.environ.get("BASE_URL", "https://redry-proposal-app.onrender.com")
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
    send_email(NOTIFY_EMAILS, f"Payment Received: {pmt_label} | {project}", admin_html)
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
    return True

@app.route("/api/proposal/<pid>/payment-confirm", methods=["POST"])
def payment_confirm(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    cfg = load_proposal_config(pid)
    if not cfg: return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    session_id = data.get("sessionId", "")
    if session_id and stripe.api_key:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess.payment_status != "paid":
                return jsonify({"error": "Payment not completed"}), 400
            meta = sess.metadata or {}
            if meta.get("proposal_id") != pid:
                return jsonify({"error": "Session does not match proposal"}), 400
            option = int(meta.get("option", data.get("option", 1)))
            payment_number = int(meta.get("payment_number", data.get("paymentNumber", 1)))
            amount = sess.amount_total or 0
            method = "ach" if any(pt == "us_bank_account" for pt in (sess.payment_method_types or [])) else "card"
        except Exception as e:
            print(f"Stripe session verify error: {e}")
            return jsonify({"error": "Could not verify payment"}), 400
    elif not stripe.api_key:
        option = data.get("option", 1); payment_number = data.get("paymentNumber", 1)
        amount = data.get("amount", 0); method = data.get("method", "card")
    else:
        return jsonify({"error": "Missing session ID"}), 400
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    recorded = _record_payment(pid, cfg, option, payment_number, amount, method, ip, session_id)
    if recorded is False:
        return jsonify({"status": "already_recorded"})
    return jsonify({"status": "confirmed"})

@app.route("/api/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig = request.headers.get("Stripe-Signature", "")
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 400
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        print(f"Webhook signature verification failed: {e}")
        return jsonify({"error": "Invalid signature"}), 400
    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        meta = sess.get("metadata", {})
        pid = meta.get("proposal_id", "")
        if pid and validate_pid(pid):
            p = os.path.join(PROPOSALS_DIR, f"{pid}.json")
            if os.path.exists(p):
                with open(p) as f: cfg = json.load(f)
                option = int(meta.get("option", 1))
                payment_number = int(meta.get("payment_number", 1))
                amount = sess.get("amount_total", 0) or 0
                method = "ach" if "us_bank_account" in (sess.get("payment_method_types") or []) else "card"
                stripe_sid = sess.get("id", "")
                _record_payment(pid, cfg, option, payment_number, amount, method, stripe_session_id=stripe_sid)
    return jsonify({"received": True}), 200

# ─── Proposal List / Dashboard ───
@app.route("/api/proposals")
@require_auth
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
@require_auth
def get_proposal_events(pid):
    if not validate_pid(pid): return jsonify({"error": "Invalid ID"}), 400
    if not DATABASE_URL: return jsonify([])
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT event_type, details, created_at FROM proposal_events WHERE proposal_id=%s ORDER BY created_at", (pid,))
        events = [{"type": r["event_type"], "details": r["details"], "at": r["created_at"].isoformat()} for r in cur.fetchall()]
        conn.close(); return jsonify(events)
    except Exception as e: return jsonify({"error": str(e)}), 500

# ─── Dashboard API ───
@app.route("/api/dashboard")
@require_auth
def dashboard_data():
    stats = {"totalProposals": 0, "sent": 0, "viewed": 0, "signed": 0, "paid": 0, "totalRevenue": 0}
    proposals = []; signatures = []; payments = []
    if not DATABASE_URL:
        return jsonify({"stats": stats, "proposals": [], "signatures": [], "payments": []})
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Proposals
        cur.execute("""SELECT p.id, p.config->>'projectName' as project_name, p.config->>'clientCompany' as client_company,
            p.config->>'clientContact' as client_contact, p.config->>'clientEmail' as client_email,
            p.status, p.created_at, p.sent_at, p.viewed_at, p.signed_at, p.paid_at FROM proposals p ORDER BY p.created_at DESC""")
        for row in cur.fetchall():
            proposals.append({k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in {
                "id": row["id"], "projectName": row["project_name"] or "", "clientCompany": row["client_company"] or "",
                "clientContact": row["client_contact"] or "", "clientEmail": row["client_email"] or "",
                "status": row["status"] or "draft", "createdAt": row["created_at"], "sentAt": row["sent_at"],
                "viewedAt": row["viewed_at"], "signedAt": row["signed_at"], "paidAt": row["paid_at"]
            }.items()})
        stats["totalProposals"] = len(proposals)
        for p in proposals:
            s = p.get("status","")
            if s in stats: stats[s] += 1
        # Signatures
        cur.execute("""SELECT s.id, s.proposal_id, s.signer_name, s.signer_date, s.selected_option, s.signed_at,
            p.config->>'projectName' as project_name FROM signatures s
            LEFT JOIN proposals p ON p.id = s.proposal_id ORDER BY s.signed_at DESC""")
        for row in cur.fetchall():
            signatures.append({k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in {
                "id": row["id"], "proposalId": row["proposal_id"], "signerName": row["signer_name"],
                "signerDate": row["signer_date"], "selectedOption": row["selected_option"],
                "signedAt": row["signed_at"], "projectName": row["project_name"] or ""
            }.items()})
        # Payments
        cur.execute("""SELECT py.id, py.proposal_id, py.option_num, py.payment_number, py.amount_cents, py.method,
            py.paid_at, p.config->>'projectName' as project_name FROM payments py
            LEFT JOIN proposals p ON p.id = py.proposal_id ORDER BY py.paid_at DESC""")
        for row in cur.fetchall():
            payments.append({k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in {
                "id": row["id"], "proposalId": row["proposal_id"], "optionNum": row["option_num"],
                "paymentNumber": row["payment_number"], "amountCents": row["amount_cents"],
                "method": row["method"], "paidAt": row["paid_at"], "projectName": row["project_name"] or ""
            }.items()})
        stats["totalRevenue"] = sum(p.get("amountCents", 0) or 0 for p in payments)
        conn.close()
    except Exception as e:
        print(f"DB error (dashboard): {e}")
    return jsonify({"stats": stats, "proposals": proposals, "signatures": signatures, "payments": payments})

# ─── Admin diagnostics ───
def _is_session_authed():
    """True when the current request carries a Flask session that came from
    a successful /api/auth/login. Used for the /admin page so direct browser
    navigation works without an X-Auth-Token header."""
    if not TEAM_PASSWORD:
        return True
    if session.get("auth_token"):
        return True
    token = request.headers.get("X-Auth-Token") or ""
    stored = session.get("auth_token", "")
    return bool(token and stored and hmac.compare_digest(token, stored))

@app.route("/api/admin/diagnostics")
def admin_diagnostics():
    """Read-only snapshot of what's actually in Postgres + which local files
    shadow DB rows. Strictly SELECT only; no writes anywhere on this path."""
    if not _is_session_authed():
        return jsonify({"error": "Unauthorized"}), 401
    out = {
        "databaseConfigured": bool(DATABASE_URL),
        "databaseReachable": False,
        "schemaOk": False,
        "initDbOk": _INIT_DB_OK,
        "initDbLastError": _INIT_DB_LAST_ERROR,
        "totalProposals": 0,
        "rowsWithErrorKey": [],
        "localFiles": [],
        "shadowedIds": [],
        "recent": [],
    }
    try:
        out["localFiles"] = sorted([f[:-5] for f in os.listdir(PROPOSALS_DIR)
                                    if f.endswith(".json") and "_accepted" not in f])
    except Exception as e:
        print(f"diagnostics: listing local files failed: {e}")
    if not DATABASE_URL:
        return jsonify(out)
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='proposals'")
        cols = {r["column_name"] for r in cur.fetchall()}
        out["databaseReachable"] = True
        out["schemaOk"] = {"id", "config", "status", "vent_map_bytes", "vent_map_filename"} <= cols
        cur.execute("SELECT COUNT(*) AS n FROM proposals")
        out["totalProposals"] = cur.fetchone()["n"]
        cur.execute("""SELECT id,
                              config->>'error' AS error_value,
                              config->>'projectName' AS project_name,
                              config->>'clientCompany' AS client_company,
                              created_at
                       FROM proposals WHERE config ? 'error' ORDER BY created_at DESC""")
        out["rowsWithErrorKey"] = [{
            "id": r["id"],
            "errorValue": r["error_value"],
            "projectName": r["project_name"] or "",
            "clientCompany": r["client_company"] or "",
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in cur.fetchall()]
        cur.execute("""SELECT id, config->>'projectName' AS project_name,
                              config->>'clientCompany' AS client_company,
                              config->>'clientContact' AS client_contact,
                              status, created_at
                       FROM proposals ORDER BY created_at DESC LIMIT 50""")
        out["recent"] = [{
            "id": r["id"],
            "projectName": r["project_name"] or "",
            "clientCompany": r["client_company"] or "",
            "clientContact": r["client_contact"] or "",
            "status": r["status"] or "",
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in cur.fetchall()]
        # Shadow detection: for each local .json file, fetch the DB row's
        # config and compare projectName / clientCompany. Mismatch = shadow.
        shadows = []
        for fname in out["localFiles"]:
            if not validate_pid(fname): continue
            try:
                with open(os.path.join(PROPOSALS_DIR, f"{fname}.json")) as f:
                    file_cfg = json.load(f)
            except Exception:
                continue
            cur.execute("SELECT config FROM proposals WHERE id=%s", (fname,))
            row = cur.fetchone()
            if not row:
                shadows.append({"id": fname,
                                "fileProject": file_cfg.get("projectName", ""),
                                "fileCompany": file_cfg.get("clientCompany", ""),
                                "dbProject": None, "dbCompany": None,
                                "note": "file exists, no matching DB row"})
                continue
            db_cfg = row["config"]
            if isinstance(db_cfg, str): db_cfg = json.loads(db_cfg)
            if ((file_cfg.get("projectName") or "") != (db_cfg.get("projectName") or "")
                or (file_cfg.get("clientCompany") or "") != (db_cfg.get("clientCompany") or "")):
                shadows.append({"id": fname,
                                "fileProject": file_cfg.get("projectName", ""),
                                "fileCompany": file_cfg.get("clientCompany", ""),
                                "dbProject": db_cfg.get("projectName", ""),
                                "dbCompany": db_cfg.get("clientCompany", ""),
                                "note": "file and DB content differ"})
        out["shadowedIds"] = shadows
        conn.close()
    except Exception as e:
        out["initDbLastError"] = out["initDbLastError"] or str(e)
        print(f"DB error (admin_diagnostics): {e}")
    return jsonify(out)


_ADMIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>ReDry Admin Diagnostics</title>
<style>
 body{font:14px -apple-system,BlinkMacSystemFont,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}
 h1{margin:0 0 16px;font-size:22px}
 h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}
 .row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}
 .card{border:1px solid #ddd;border-radius:6px;padding:12px 16px;min-width:160px}
 .card .label{color:#666;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
 .card .v{font-size:20px;font-weight:600;margin-top:4px}
 .ok{color:#15803d} .bad{color:#b91c1c}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{padding:6px 8px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}
 th{background:#f6f6f6}
 code{font:12px ui-monospace,monospace;background:#f3f3f3;padding:1px 5px;border-radius:3px}
 .empty{color:#888;font-style:italic}
 button{padding:6px 12px;border:1px solid #999;border-radius:4px;background:#fff;cursor:pointer}
 #err{background:#fee2e2;color:#7f1d1d;padding:8px 12px;border-radius:4px;display:none;margin:12px 0}
</style></head>
<body>
<h1>ReDry Admin Diagnostics</h1>
<div><button onclick="load()">Refresh</button></div>
<div id="err"></div>
<div id="content"></div>
<script>
function pill(ok){return '<span class="'+(ok?'ok':'bad')+'">'+(ok?'YES':'NO')+'</span>'}
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
async function load(){
 document.getElementById('err').style.display='none'
 const r=await fetch('/api/admin/diagnostics',{credentials:'include'})
 if(r.status===401){document.getElementById('err').innerHTML='Not logged in. <a href="/">Open the builder</a>, log in, then come back to <a href="/admin">/admin</a>.';document.getElementById('err').style.display='block';return}
 const d=await r.json()
 const cards=[
  ['DATABASE_URL set',pill(d.databaseConfigured)],
  ['DB reachable',pill(d.databaseReachable)],
  ['Schema OK',pill(d.schemaOk)],
  ['init_db OK',pill(d.initDbOk)],
  ['Total proposals',esc(d.totalProposals)],
 ]
 let html='<div class="row">'+cards.map(c=>'<div class="card"><div class="label">'+c[0]+'</div><div class="v">'+c[1]+'</div></div>').join('')+'</div>'
 if(d.initDbLastError){html+='<div id="err" style="display:block">init_db last error: <code>'+esc(d.initDbLastError)+'</code></div>'}
 html+='<h2>Rows with <code>error</code> key in config ('+d.rowsWithErrorKey.length+')</h2>'
 if(d.rowsWithErrorKey.length){
  html+='<p><button id="cleanup-btn" onclick="cleanup()" style="background:#dc2626;color:#fff;border:none">Delete all '+d.rowsWithErrorKey.length+' contaminated rows</button></p>'
  html+='<table><tr><th>ID</th><th>error value</th><th>Project</th><th>Company</th><th>Created</th></tr>'
  d.rowsWithErrorKey.forEach(r=>{html+='<tr><td><code>'+esc(r.id)+'</code></td><td><code>'+esc(r.errorValue)+'</code></td><td>'+esc(r.projectName)+'</td><td>'+esc(r.clientCompany)+'</td><td>'+esc(r.createdAt)+'</td></tr>'})
  html+='</table>'
 } else { html+='<div class="empty">None.</div>' }
 html+='<h2>Shadowed IDs &mdash; local file disagrees with DB ('+d.shadowedIds.length+')</h2>'
 if(d.shadowedIds.length){
  html+='<table><tr><th>ID</th><th>File: project / company</th><th>DB: project / company</th><th>Note</th></tr>'
  d.shadowedIds.forEach(s=>{html+='<tr><td><code>'+esc(s.id)+'</code></td><td>'+esc(s.fileProject)+' / '+esc(s.fileCompany)+'</td><td>'+esc(s.dbProject)+' / '+esc(s.dbCompany)+'</td><td>'+esc(s.note)+'</td></tr>'})
  html+='</table>'
 } else { html+='<div class="empty">None.</div>' }
 html+='<h2>Local JSON files on this container ('+d.localFiles.length+')</h2>'
 html+=d.localFiles.length?'<ul>'+d.localFiles.map(id=>'<li><code>'+esc(id)+'</code></li>').join('')+'</ul>':'<div class="empty">None.</div>'
 html+='<h2>Recent proposals from DB ('+d.recent.length+')</h2>'
 if(d.recent.length){
  html+='<table><tr><th>ID</th><th>Project</th><th>Client company</th><th>Contact</th><th>Status</th><th>Created</th></tr>'
  d.recent.forEach(p=>{html+='<tr><td><a href="/proposal/'+esc(p.id)+'" target="_blank"><code>'+esc(p.id)+'</code></a></td><td>'+esc(p.projectName)+'</td><td>'+esc(p.clientCompany)+'</td><td>'+esc(p.clientContact)+'</td><td>'+esc(p.status)+'</td><td>'+esc(p.createdAt)+'</td></tr>'})
  html+='</table>'
 } else { html+='<div class="empty">None.</div>' }
 document.getElementById('content').innerHTML=html
}
async function cleanup(){
 const btn=document.getElementById('cleanup-btn')
 if(!confirm('Delete all rows with the legacy `error` key (contaminated duplicates)? This cascades through proposal_events, signatures, payments. Cannot be undone without a DB backup.'))return
 if(btn){btn.disabled=true;btn.textContent='Deleting…'}
 const r=await fetch('/api/admin/cleanup-error-rows',{method:'POST',credentials:'include'})
 if(!r.ok){alert('Cleanup failed: HTTP '+r.status);if(btn){btn.disabled=false;btn.textContent='Retry delete'}return}
 const d=await r.json()
 alert('Deleted '+d.deleted+' proposal rows. Cascaded: '+JSON.stringify(d.cascaded))
 load()
}
load()
</script>
</body></html>"""

@app.route("/admin")
def admin_page():
    return _ADMIN_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/admin/cleanup-error-rows", methods=["POST"])
def admin_cleanup_error_rows():
    """One-shot cleanup of proposals whose config has a stray `error` key.
    Those rows are duplicate junk from the contamination bug fixed in #22 —
    they share project/company with legitimate rows but were created by the
    SPA forwarding 404 form state. Cascades through proposal_events,
    signatures, payments to satisfy the FK constraints, all in a single
    transaction so a mid-run failure rolls back cleanly."""
    if not _is_session_authed():
        return jsonify({"error": "Unauthorized"}), 401
    if not DATABASE_URL:
        return jsonify({"error": "No database configured"}), 400
    conn = get_db()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        # Materialize the target IDs once; the WHERE-IN subquery would
        # otherwise re-run after each dependent table delete and miss rows
        # that some other request races into existence (or, in the trash
        # case, get cleaned before we get to them).
        cur.execute("SELECT id FROM proposals WHERE config ? 'error'")
        target_ids = [r[0] for r in cur.fetchall()]
        if not target_ids:
            conn.close()
            return jsonify({"deleted": 0, "ids": []})
        cur.execute("DELETE FROM proposal_events WHERE proposal_id = ANY(%s)", (target_ids,))
        events_deleted = cur.rowcount
        cur.execute("DELETE FROM signatures WHERE proposal_id = ANY(%s)", (target_ids,))
        sigs_deleted = cur.rowcount
        cur.execute("DELETE FROM payments WHERE proposal_id = ANY(%s)", (target_ids,))
        payments_deleted = cur.rowcount
        cur.execute("DELETE FROM proposals WHERE id = ANY(%s)", (target_ids,))
        proposals_deleted = cur.rowcount
        conn.commit()
        conn.close()
        return jsonify({
            "deleted": proposals_deleted,
            "ids": target_ids,
            "cascaded": {
                "proposal_events": events_deleted,
                "signatures": sigs_deleted,
                "payments": payments_deleted,
            },
        })
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"admin_cleanup_error_rows: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── Catch-all for React SPA ───
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

# ─── Backfill proposals missing from the database ───
def backfill_proposals_to_db():
    """Copy any local-only proposals (config + vent map) into Postgres so the
    DB matches the filesystem. Runs on startup so a container that still has
    local files can rescue them before the next redeploy wipes everything."""
    if not DATABASE_URL: return 0
    restored = 0
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM proposals")
        existing = {row[0] for row in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"Backfill: skipping (could not list existing proposals: {e})")
        return 0
    for fname in os.listdir(PROPOSALS_DIR):
        if not fname.endswith(".json") or "_accepted" in fname:
            continue
        pid = fname[:-len(".json")]
        if not validate_pid(pid) or pid in existing:
            continue
        try:
            with open(os.path.join(PROPOSALS_DIR, fname)) as f:
                cfg = json.load(f)
            vm_bytes = None
            vm_name = cfg.get("_ventMapFilename")
            if vm_name:
                vmp = os.path.join(PROPOSALS_DIR, vm_name)
                if os.path.exists(vmp):
                    with open(vmp, "rb") as vf: vm_bytes = vf.read()
            db_store_proposal(pid, cfg, status="draft",
                              vent_map_bytes=vm_bytes, vent_map_filename=vm_name)
            restored += 1
            print(f"  Backfilled proposal {pid} into database")
        except Exception as e:
            print(f"  Backfill error for {pid}: {e}")
    return restored

# ─── Backfill taxRate into existing proposals ───
def backfill_tax_rates():
    """Add taxRate to any proposal config that doesn't have it, using the state tax rate lookup."""
    updated = 0
    for fname in os.listdir(PROPOSALS_DIR):
        if not fname.endswith(".json") or "_accepted" in fname:
            continue
        fpath = os.path.join(PROPOSALS_DIR, fname)
        try:
            with open(fpath) as f:
                cfg = json.load(f)
            # Skip if taxRate already saved
            existing = cfg.get("taxRate")
            if existing and float(existing) > 0:
                continue
            # Look up rate from state
            state = (cfg.get("projectState") or "").upper().strip()
            if state in STATE_TAX_RATES:
                rate = STATE_TAX_RATES[state]
            else:
                rate = 0
            if rate > 0:
                cfg["taxRate"] = str(rate)
                with open(fpath, "w") as f:
                    json.dump(cfg, f)
                # Also update config in database (preserve existing status)
                pid = fname.replace(".json", "")
                try:
                    if DATABASE_URL:
                        conn = get_db(); cur = conn.cursor()
                        cur.execute("UPDATE proposals SET config=%s WHERE id=%s", (json.dumps(cfg), pid))
                        conn.close()
                except Exception:
                    pass
                updated += 1
                print(f"  Backfilled taxRate={rate} for {fname} (state={state})")
        except Exception as e:
            print(f"  Error processing {fname}: {e}")
    return updated

@app.route("/api/admin/backfill-tax", methods=["POST"])
@require_auth
def admin_backfill_tax():
    count = backfill_tax_rates()
    return jsonify({"updated": count, "message": f"Backfilled tax rates for {count} proposals"})

# Run backfills on startup
with app.app_context():
    print("Reconciling local proposals with database...")
    n = backfill_proposals_to_db()
    if n > 0:
        print(f"Backfilled {n} local proposals into the database.")
    else:
        print("Database already has all local proposals.")
    print("Checking proposals for missing tax rates...")
    n = backfill_tax_rates()
    if n > 0:
        print(f"Backfilled tax rates for {n} proposals.")
    else:
        print("All proposals have tax rates set.")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
