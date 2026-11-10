# Architecture and tradeoffs

## Voice application

`studio-agent/src/agent.py` is the LiveKit entrypoint. `clients.py` loads a selected business folder and validates its identity, service codes and timezone. `demos.py` handles keypad selection, caller identity filtering, cancellation and handoff. The public directory has three fictional profiles with placeholder voice IDs.

Pilot mode explicitly selects one business. Demo mode exposes a keypad directory; intake is available only for explicitly listed profiles. Neither mode guesses a business when configuration is missing. A demo without persistence does not become a live booking service.

`calendar_dates.py` grounds supported English date expressions in the business's local date and checks generated confirmations. Its parser is bounded; it is not a general scheduling engine. `call_ending.py` plays a farewell directly before disconnecting, avoiding reliance on a final language-model response.

`cloud.py` saves confirmed inquiries and attaches transcripts. It uses idempotent inserts so retries cannot overwrite owner corrections. `calls.py` records call metadata and uploads finalized recordings separately from inquiry capture. `inquiries.py` supports optional SQLite storage; `sheet_sync.py` is an optional historical Sheets integration.

## Portal

`client-portal/public/` is a static site with ES modules and no frontend build step. The modules separate validation, filtering, bulk actions, authentication/session handling and settings from DOM wiring in `app.js`.

Calls and customer requests are separate records. Call detail shows connected request fields and text before attempting audio playback. Updates include an `updated_at` condition so a stale browser cannot silently overwrite newer edits. CSV output neutralizes formula-like values.

## Access and persistence

Supabase Auth identifies the user. Memberships determine which business records the user may read. PostgreSQL grants and row-level security enforce access, with restrictions on editable columns. Triggers preserve the original AI capture. Audio uses a private Storage bucket and temporary signed access.

The numbered SQL files document the schema's evolution. **They are not a complete fresh-project installer:** the original base schema was created separately. `tests/db/baseline.sql` approximates that initial schema, while `tests/db/run.mjs` stubs provider-specific features and exercises permissions and regression checks with PGlite. These checks do not prove that provider configuration, delivery or live recording works.

## Known limits

- Inquiry capture is not booking confirmation, payment processing or dispatch.
- Alerts are best effort; the current email trigger is not a durable retry queue.
- Transcript cleanup and recording-access expiry are different mechanisms. Expiry metadata does not delete storage objects; configure and verify an actual retention process before collecting real recordings.
- Date safeguards cover bounded English expressions. Real speech, latency and model behavior require call evaluation.
- The mock portal imitates service responses and has test-control endpoints. It is a development tool, not a hosted multi-user service.
- Production domains, keys, client data and deployment workflows are intentionally absent. Configure your own endpoints, allowed authentication return origins and content-security policy before hosting.
