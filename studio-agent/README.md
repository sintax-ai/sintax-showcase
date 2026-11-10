# SINTAX voice agent

Python voice reception built on the [LiveKit Agents starter](https://github.com/livekit-examples/agent-starter-python). Shared code lives in `src/`; business facts and instructions live in `clients/<slug>/`.

## Local tests

Install uv and use Python 3.14 for the pinned dependency set:

```bash
cd studio-agent
uv sync --locked --python 3.14
uv run --locked pytest
```

The included tests use fictional local fixtures and fake storage endpoints. Paid model-evaluation tests and client-specific evaluations are excluded from this showcase.

## Optional live voice setup

Live calling is not needed to inspect this project or run its tests. It requires your own LiveKit project, model/voice access, usage charges and an appropriate test caller setup.

1. Copy `.env.example` to `.env.local` and fill in your own LiveKit values.
2. Replace `REPLACE_WITH_YOUR_VOICE_ID` in each example profile with a voice supported by the configured TTS model. The checked-in model choices reflect the application snapshot; verify their availability in your provider account.
3. For the keypad directory, leave `SINTAX_MODE=demo` and `SINTAX_INTAKE_CLIENTS` blank. Codes are `101` (club), `102` (studio), and `201` (home services).
4. Download required model assets with `uv run src/agent.py download-files`; start your test worker with `uv run src/agent.py dev`. Use your own project's dispatch setup to connect a caller. The Dockerfile is provided for inspecting the deployment structure, not a configured deployment.
5. For single-business pilot experiments, set `SINTAX_MODE=pilot` and select `SINTAX_CLIENT=sample-studio` (or another example). Saving requests requires a properly configured persistence backend. Without one, the tool must not claim successful saving.

All supplied businesses and IDs are fictional. The repository has no hosted backend setup, real customer accounts or usable provider credentials. Keep experiments separate from real business traffic. Live providers can process or record voice even when local persistence settings are blank; inspect your account's recording settings and data handling before making calls.

The optional `INQUIRY_DB_PATH`, `TRANSCRIPT_DIR`, Supabase settings and recording-access expiry are described in `.env.example` and the implementation. Recording-access expiry does not delete stored objects.

## Entry points worth reading

- `agent.py`: runtime selection, inquiry tools and session lifecycle.
- `clients.py`, `demos.py`: explicit client configuration and keypad routing.
- `calendar_dates.py`: timezone-aware date tools and output checks.
- `call_ending.py`: farewell playback and call termination.
- `cloud.py`, `calls.py`: inquiry persistence, transcripts and recording metadata.

See [the architecture notes](../docs/architecture.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).
