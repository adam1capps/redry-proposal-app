#!/usr/bin/env python3
"""
ReDry Proposal Builder - Flask API Server
Serves the React frontend and handles PDF generation, Stripe payments, and tax lookup.
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from proposal_generator import generate_proposal_pdf
import os, io, json, uuid, stripe
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static")
CORS(app)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
if not stripe.api_key:
    print("WARNING: STRIPE_SECRET_KEY not set. Payment features will not work.")
if not STRIPE_PK:
    print("WARNING: STRIPE_PUBLISHABLE_KEY not set. Payment features will not work.")
if not GOOGLE_MAPS_KEY:
    print("WARNING: GOOGLE_MAPS_API_KEY not set. Address autocomplete will not work.")

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
            "success_url": f"{base_url}/proposal/{proposal_id}?payment=success&option={option}&pmt={payment_number}",
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
        config["_createdAt"] = datetime.now().isoformat()
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.json"), "w") as f:
            json.dump(config, f)

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
    acc = request.get_json()
    acc["_acceptedAt"] = datetime.now().isoformat()
    with open(os.path.join(PROPOSALS_DIR, f"{pid}_accepted.json"), "w") as f:
        json.dump(acc, f)
    return jsonify({"status": "accepted", "acceptedAt": acc["_acceptedAt"]})

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

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
