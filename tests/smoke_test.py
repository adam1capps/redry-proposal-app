#!/usr/bin/env python3
"""Smoke test for the ReDry proposal server.

Runs with NO DATABASE_URL and NO TEAM_PASSWORD set, so every DB call
no-ops and auth is bypassed. The point is to catch deploy-breakers
before they reach Render: import errors, missing dependencies, broken
route registration, obviously-wrong status codes. It does NOT exercise
Postgres, Stripe, or SendGrid.

Run: python tests/smoke_test.py   (exits non-zero on first failure)
"""
import os
import sys

# Make sure the app boots in its "no external services" mode.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("TEAM_PASSWORD", None)

# Import from the repo root regardless of where pytest/CI invokes us.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)

# 1. Import must succeed (catches syntax errors, missing deps, bad
#    module-level code). init_db() and the startup backfills run here;
#    they must no-op cleanly without a database.
import server  # noqa: E402

client = server.app.test_client()

# 2. Health endpoint
r = client.get("/health")
check("GET /health -> 200", r.status_code == 200, f"got {r.status_code}")
check("GET /health body", r.get_json() == {"status": "ok"}, f"got {r.data!r}")

# 3. SPA index served at root
r = client.get("/")
check("GET / -> 200", r.status_code == 200, f"got {r.status_code}")

# 4. Auth check endpoint responds (auth disabled when TEAM_PASSWORD unset)
r = client.get("/api/auth/check")
check("GET /api/auth/check -> 200", r.status_code == 200, f"got {r.status_code}")

# 5. Invalid proposal id is rejected by validate_pid
r = client.get("/api/proposal/NOT-A-VALID-ID")
check("GET /api/proposal/<bad> -> 400", r.status_code == 400, f"got {r.status_code}")

# 6. Well-formed but unknown id returns 404 (no DB, load returns None)
r = client.get("/api/proposal/abcdef123456")
check("GET /api/proposal/<unknown> -> 404", r.status_code == 404, f"got {r.status_code}")

# 7. Admin diagnostics reachable (auth bypassed) and reports no DB configured
r = client.get("/api/admin/diagnostics")
check("GET /api/admin/diagnostics -> 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    d = r.get_json()
    check("diagnostics reports databaseConfigured == False",
          d.get("databaseConfigured") is False, f"got {d.get('databaseConfigured')}")
    for key in ("schemaOk", "totalProposals", "rowsWithErrorKey", "recent"):
        check(f"diagnostics has '{key}'", key in d, f"missing {key}")

# 8. Admin page renders HTML
r = client.get("/admin")
check("GET /admin -> 200", r.status_code == 200, f"got {r.status_code}")
check("GET /admin is HTML", b"<html" in r.data.lower(), "no <html> in body")

# 9. Tax-rate helper responds for a known state
r = client.get("/api/tax-rate?state=TX")
check("GET /api/tax-rate?state=TX -> 200", r.status_code == 200, f"got {r.status_code}")

print()
if failures:
    print(f"{len(failures)} smoke check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("All smoke checks passed.")
