# Fixed Lease vs Performance Lease — Implementation Plan

## Overview
Add a lease type selector after login. Each path gets its own builder, pricing logic, client view, PDF, and email output. Performance Lease = current app. Fixed Lease = new per-vent rental path.

## Flow
Login → **Lease Type Selector** → Fixed or Performance builder → Same output workflow (HTML email + PDF)

---

## Step 1: Frontend — Lease Type Selector Screen
**File:** `static/index.html`

- Add new `LeaseTypeSelector` component shown after login
- Two cards: "Fixed Lease" and "Performance Lease" with descriptions
- New view state `"lease-select"` inserted between login and form
- `onLogin` now goes to `"lease-select"` instead of `"form"`
- Add `leaseType` state to App (`"performance"` or `"fixed"`)
- Selecting a type sets `leaseType` and navigates to the appropriate form view

## Step 2: Frontend — Fixed Lease Builder Form
**File:** `static/index.html`

- New `defaultFixedForm` with fixed-lease-specific fields:
  - Same client/project fields (company, contact, address, etc.)
  - `numVents` (number of vents to rent)
  - `monthlyRate` ($/vent/month)
  - `leaseTerm` (months — e.g. 12, 24, 36)
  - `installFee` (optional one-time install/setup fee)
  - Same date/validity fields
  - Same tax fields
  - Same payment option toggles + custom option
- New `calcFixedOptions(form, taxRate)` — pricing based on `numVents × monthlyRate × leaseTerm`
- New `FixedLeaseBuilder` component — cloned from `BuilderForm` but with fixed-lease fields and labels
- Separate localStorage keys so the two forms don't clobber each other

## Step 3: Frontend — Fixed Lease Client View
**File:** `static/index.html`

- New `FixedLeaseClientView` component — cloned from `ClientView` but with fixed-lease language
- Shows per-vent pricing, lease term, monthly commitment
- Same accept/sign/payment flow

## Step 4: Frontend — App Routing Updates
**File:** `static/index.html`

- App component routes to `FixedLeaseBuilder` when `leaseType === "fixed"` and `view === "form"`
- Client view (`/proposal/:id`) reads `leaseType` from stored config and renders appropriate client view
- "Back to lease selection" nav option from either builder
- `leaseType` included in config sent to server

## Step 5: Backend — Store & Serve leaseType
**File:** `server.py`

- `leaseType` flows through config JSON (already stored as-is)
- Client view endpoint returns it so frontend can pick the right client component
- No schema changes needed — config is freeform JSON

## Step 6: Backend — Fixed Lease PDF Generation
**File:** `proposal_generator.py`

- New `generate_fixed_proposal_pdf(config, logo_path, vent_map_path)` — cloned from current, adapted for fixed lease language/pricing
- New `generate_fixed_client_pdf(config, logo_path, vent_map_path)` — client-facing version
- Shows: number of vents, monthly rate, lease term, total lease value, payment schedule
- Server checks `config.get("leaseType")` to pick which PDF generator to call

## Step 7: Backend — Fixed Lease Email Templates
**File:** `server.py`

- In `/api/proposal/<pid>/send`: check leaseType, use fixed-lease email template when applicable
- Different subject line, body copy, and pricing summary for fixed leases
- Same structure (SendGrid, PDF attachment, CTA button) — just different content

## Step 8: Verify & Test
- Test both paths end-to-end: login → select lease type → build proposal → generate PDF → send
- Verify existing Performance Lease flow is unchanged
- Verify Fixed Lease PDF output and email content
