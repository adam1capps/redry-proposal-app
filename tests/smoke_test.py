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

# 10. Whitelist sanitizer keeps allowed form keys and drops everything else.
sample = {
    "clientCompany": "Acme",
    "projectName": "Crockett",
    "leaseType": "performance",
    "wetSF": "11600",
    "_proposalId": "abcdef123456",
    # Junk that must be dropped:
    "error": "Not found",
    "foo": "bar",
    "_baseUrl": "https://evil.example.com",
    "_createdAt": "1970-01-01",
}
server._sanitize_incoming_config(sample)
check("sanitize keeps clientCompany", sample.get("clientCompany") == "Acme")
check("sanitize keeps _proposalId", sample.get("_proposalId") == "abcdef123456")
check("sanitize drops error", "error" not in sample)
check("sanitize drops arbitrary foo", "foo" not in sample)
check("sanitize drops client-sent _baseUrl", "_baseUrl" not in sample)
check("sanitize drops client-sent _createdAt", "_createdAt" not in sample)
check("sanitize tolerates non-dict input", server._sanitize_incoming_config(None) is None)

# 11. proposal_value: the revenue lens for analytics. Every assertion below
#     pins a behavior that, if wrong, would silently mis-report the pipeline.
check("value perf strings",
      server.proposal_value({"wetSF":"11600","ratePSF":"2.00","scanCost":"4500","numScans":"4"}) == 41200.0)
check("value perf waived scans drop to base only",
      server.proposal_value({"wetSF":"1000","ratePSF":"2","scanCost":"4500","numScans":"4","waiveScans":True}) == 2000.0)
check("value fixed (leaseTerm is NOT multiplied)",
      server.proposal_value({"leaseType":"fixed","numVents":"5","ventRate":"1000","leaseTerm":"12","installFee":"500"}) == 5500.0)
check("value hidePricing is 0",
      server.proposal_value({"wetSF":"1000","ratePSF":"2","hidePricing":True}) == 0.0)
check("value empty config is 0", server.proposal_value({}) == 0.0)
check("value non-dict is 0", server.proposal_value(None) == 0.0)
check("value garbage strings tolerated",
      server.proposal_value({"wetSF":"abc","ratePSF":None,"scanCost":"","numScans":"x"}) == 0.0)

# 12. Analytics endpoint degrades to a shaped empty when DB is unavailable.
r = client.get("/api/analytics")
check("GET /api/analytics -> 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    a = r.get_json() or {}
    for key in ("pipeline", "funnel", "staleBids", "monthly", "periods", "timezone", "generatedAt"):
        check(f"analytics has '{key}'", key in a, f"missing {key}")
    check("analytics ok=False when no DB", a.get("ok") is False, f"got {a.get('ok')}")
    check("analytics periods is a list", isinstance(a.get("periods"), list))

# 13. Dashboard endpoint shape (engagement fields don't break the no-DB path).
r = client.get("/api/dashboard")
check("GET /api/dashboard -> 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    d = r.get_json() or {}
    for key in ("stats", "proposals", "signatures", "payments"):
        check(f"dashboard has '{key}'", key in d, f"missing {key}")
    check("dashboard proposals is a list", isinstance(d.get("proposals"), list))

# 14a. _period_windows: prev "same-elapsed" windows must NEVER spill into the
#      current period (double counting). Mar 30 is the canonical trap: Feb has
#      28 days, so Feb 1 + 29.6 elapsed days would reach into March.
from datetime import datetime as _dt, timezone as _tz
_btz = server._business_tz()
for probe in (_dt(2026, 3, 30, 15, 0, tzinfo=_btz),    # Feb shorter than elapsed
              _dt(2026, 5, 31, 23, 0, tzinfo=_btz),    # Apr shorter than May
              _dt(2026, 9, 30, 18, 0, tzinfo=_btz),    # Q3 (92d) vs Q2 (91d)
              _dt(2024, 12, 31, 23, 0, tzinfo=_btz)):  # leap-year YTD
    for key, label, start, end, ps, pe in server._period_windows(probe):
        if ps is None:
            continue
        check(f"window '{key}' prev does not overlap current ({probe.date()})",
              pe <= start, f"prev_end={pe} > start={start}")
        check(f"window '{key}' prev is non-empty ({probe.date()})",
              ps < pe, f"prev_start={ps} >= prev_end={pe}")

# 14b. Stale bids: filter is on LAST ACTIVITY, not send date.
_now = _dt(2026, 6, 10, 12, 0, tzinfo=_tz.utc)
def _row(pid, status, sent_days_ago):
    from datetime import timedelta as _td
    return {"id": pid, "status": status, "config": {},
            "project_name": pid, "client_company": "",
            "sent_at": _now - _td(days=sent_days_ago), "created_at": _now - _td(days=sent_days_ago)}
from datetime import timedelta as _td
_rows = [
    _row("oldnoview", "sent", 30),                 # stale: 30d, never engaged
    _row("oldviewedyday", "viewed", 30),           # NOT stale: viewed yesterday
    _row("freshsent", "sent", 2),                  # NOT stale: only 2d old
    _row("signedone", "signed", 40),               # excluded: already signed
]
_engagement = {"oldviewedyday": _now - _td(days=1)}
_stale = server._compute_stale_bids(_rows, _engagement, 10, _now)
_ids = [s["id"] for s in _stale]
check("stale includes never-engaged old bid", _ids == ["oldnoview"], f"got {_ids}")
check("stale daysStale measured from last activity", _stale[0]["daysStale"] == 30, f"got {_stale[0]['daysStale']}")

# 14c. _request_is_admin is False when auth is disabled (otherwise no view
#      would ever be logged on no-password deployments).
with server.app.test_request_context("/api/proposal/abc123abc123"):
    check("_request_is_admin False without TEAM_PASSWORD", server._request_is_admin() is False)

# 14. Events endpoint: malformed id rejected, well-formed unknown returns []
r = client.get("/api/proposal/NOT-A-VALID-ID/events")
check("GET /api/proposal/<bad>/events -> 400", r.status_code == 400, f"got {r.status_code}")
r = client.get("/api/proposal/abcdef123456/events")
check("GET /api/proposal/<unknown>/events -> 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    check("events without DB is empty list", r.get_json() == [], f"got {r.data!r}")

print()
if failures:
    print(f"{len(failures)} smoke check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("All smoke checks passed.")
