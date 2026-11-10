# SINTAX client portal

Static HTML, CSS and JavaScript for reviewing calls and customer requests. Includes account/settings modules, filtering, owner corrections, original captures, transcripts, optional private audio and trash/restore.

From the repository root, run `python3 client-portal/tests/mock/server.py`, then visit http://127.0.0.1:4174/?demo=pickleball. The mock server uses fictional records, synthetic audio and local memory. Owner settings are available with `owner@example.test` / `fictional-password`. Other fictional accounts and test controls are documented in `tests/mock/server.py`.

Only `public/` is a deployable static-site directory. Its checked-in `config.js` contains placeholders. The mock server supplies local configuration automatically. A real deployment requires your own Supabase project, public browser key, authentication return origins in `account.mjs`, and content-security policy in `_headers`. Never expose a server secret in browser code.

The historical migrations in `supabase/migrations/` assume an initial schema that is not supplied as a production installer. The isolated database tests reconstruct an approximation in `tests/db/baseline.sql` and stub external Supabase services. Do not apply the test baseline to a hosted project.

```bash
cd client-portal/tests
npm ci
npm test
npm run db
```

For the mock HTTP tests, run `python3 -m unittest discover -s client-portal/tests/mock -p 'test_*.py'` from the repository root.

`tests/call-details.browser.mjs` is an optional browser regression requiring Playwright and Chrome. Start the mock server first. Its `PORTAL_TEST_URL` must point at localhost; it verifies text-before-audio, request navigation and mobile layout.
