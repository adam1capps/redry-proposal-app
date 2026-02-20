#!/usr/bin/env python3
"""
ReDry Proposal Builder - Flask API Server
Serves the React frontend and handles PDF generation via the reportlab engine.
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from proposal_generator import generate_proposal_pdf
import os
import io
import json
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static")
CORS(app)

# Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOGO_PATH = os.path.join(BASE_DIR, "redry_logo.jpg")
PROPOSALS_DIR = os.path.join(BASE_DIR, "proposals")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROPOSALS_DIR, exist_ok=True)


@app.route("/api/generate-pdf", methods=["POST"])
def generate_pdf():
    """
    Generate a proposal PDF from form data.
    Accepts multipart form with JSON config and optional vent map image.
    Returns the PDF file.
    """
    try:
        # Get config from form data or JSON body
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None
        
        # Save vent map if provided
        vent_map_path = None
        if vent_map:
            filename = secure_filename(vent_map.filename)
            vent_map_path = os.path.join(UPLOAD_DIR, f"ventmap_{uuid.uuid4().hex[:8]}_{filename}")
            vent_map.save(vent_map_path)
        
        # Generate PDF
        pdf_bytes = generate_proposal_pdf(
            config,
            logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None,
            vent_map_path=vent_map_path
        )
        
        # Create a filename based on project
        project_name = config.get("projectName", "Project").replace(" ", "_")
        section = config.get("projectSection", "").replace(" ", "_")
        filename = f"ReDry_Proposal_{project_name}_{section}.pdf" if section else f"ReDry_Proposal_{project_name}.pdf"
        
        # Save a copy
        proposal_id = uuid.uuid4().hex[:12]
        saved_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf")
        with open(saved_path, "wb") as f:
            f.write(pdf_bytes)
        
        # Also save the config for the client view
        config_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.json")
        with open(config_path, "w") as f:
            json.dump(config, f)
        
        if vent_map_path:
            # Copy vent map for client view reference
            import shutil
            vent_copy = os.path.join(PROPOSALS_DIR, f"{proposal_id}_ventmap{os.path.splitext(vent_map_path)[1]}")
            shutil.copy2(vent_map_path, vent_copy)
        
        # Return PDF
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
            max_age=0
        )
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-proposal-link", methods=["POST"])
def generate_proposal_link():
    """
    Generate a proposal and return a shareable client link.
    """
    try:
        if request.content_type and "multipart" in request.content_type:
            config = json.loads(request.form.get("config", "{}"))
            vent_map = request.files.get("ventMap")
        else:
            config = request.get_json() or {}
            vent_map = None
        
        proposal_id = uuid.uuid4().hex[:12]
        
        # Save vent map
        vent_map_filename = None
        if vent_map:
            ext = os.path.splitext(secure_filename(vent_map.filename))[1]
            vent_map_filename = f"{proposal_id}_ventmap{ext}"
            vent_map.save(os.path.join(PROPOSALS_DIR, vent_map_filename))
        
        # Generate and save PDF
        pdf_bytes = generate_proposal_pdf(
            config,
            logo_path=LOGO_PATH if os.path.exists(LOGO_PATH) else None,
            vent_map_path=os.path.join(PROPOSALS_DIR, vent_map_filename) if vent_map_filename else None
        )
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf"), "wb") as f:
            f.write(pdf_bytes)
        
        # Save config with vent map reference
        config["_ventMapFilename"] = vent_map_filename
        config["_createdAt"] = datetime.now().isoformat()
        with open(os.path.join(PROPOSALS_DIR, f"{proposal_id}.json"), "w") as f:
            json.dump(config, f)
        
        return jsonify({
            "proposalId": proposal_id,
            "clientUrl": f"/proposal/{proposal_id}",
            "pdfUrl": f"/api/proposal/{proposal_id}/pdf"
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proposal/<proposal_id>")
def get_proposal_config(proposal_id):
    """Return the config JSON for a proposal (used by client view)."""
    config_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.json")
    if not os.path.exists(config_path):
        return jsonify({"error": "Proposal not found"}), 404
    with open(config_path) as f:
        config = json.load(f)
    return jsonify(config)


@app.route("/api/proposal/<proposal_id>/pdf")
def get_proposal_pdf(proposal_id):
    """Return the saved PDF for a proposal."""
    pdf_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.pdf")
    if not os.path.exists(pdf_path):
        return jsonify({"error": "Proposal not found"}), 404
    return send_file(pdf_path, mimetype="application/pdf")


@app.route("/api/proposal/<proposal_id>/ventmap")
def get_proposal_ventmap(proposal_id):
    """Return the vent map image for a proposal."""
    config_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.json")
    if not os.path.exists(config_path):
        return jsonify({"error": "Proposal not found"}), 404
    with open(config_path) as f:
        config = json.load(f)
    vent_map_filename = config.get("_ventMapFilename")
    if not vent_map_filename:
        return jsonify({"error": "No vent map"}), 404
    return send_file(os.path.join(PROPOSALS_DIR, vent_map_filename))


@app.route("/api/proposal/<proposal_id>/accept", methods=["POST"])
def accept_proposal(proposal_id):
    """Record client acceptance/signature."""
    config_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}.json")
    if not os.path.exists(config_path):
        return jsonify({"error": "Proposal not found"}), 404
    
    acceptance = request.get_json()
    
    # Save acceptance record
    acceptance_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}_accepted.json")
    acceptance["_acceptedAt"] = datetime.now().isoformat()
    with open(acceptance_path, "w") as f:
        json.dump(acceptance, f)
    
    return jsonify({"status": "accepted", "acceptedAt": acceptance["_acceptedAt"]})


@app.route("/api/proposals")
def list_proposals():
    """List all generated proposals."""
    proposals = []
    for f in os.listdir(PROPOSALS_DIR):
        if f.endswith(".json") and not f.endswith("_accepted.json"):
            proposal_id = f.replace(".json", "")
            with open(os.path.join(PROPOSALS_DIR, f)) as fh:
                config = json.load(fh)
            accepted_path = os.path.join(PROPOSALS_DIR, f"{proposal_id}_accepted.json")
            proposals.append({
                "id": proposal_id,
                "projectName": config.get("projectName", ""),
                "clientCompany": config.get("clientCompany", ""),
                "createdAt": config.get("_createdAt", ""),
                "accepted": os.path.exists(accepted_path),
            })
    proposals.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
    return jsonify(proposals)


# Serve React app for all non-API routes
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print("=" * 50)
    print("ReDry Proposal Builder Server")
    print("=" * 50)
    print(f"Logo: {'✓ Found' if os.path.exists(LOGO_PATH) else '✗ Missing'}")
    print(f"Uploads: {UPLOAD_DIR}")
    print(f"Proposals: {PROPOSALS_DIR}")
    print(f"Starting on http://0.0.0.0:{port}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=debug)
