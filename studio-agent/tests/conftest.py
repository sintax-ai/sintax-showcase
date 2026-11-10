"""Fictional configuration for offline unit tests, scoped to the pytest process.

SDK client constructors require credentials even when the tests never send an
inference request. Always supply obviously fake values, never developer secrets.
"""

import os

os.environ["SINTAX_CLIENT"] = "sample-studio"
os.environ["LIVEKIT_URL"] = "wss://fictional.example.test"
os.environ["LIVEKIT_API_KEY"] = "FICTIONAL"
os.environ["LIVEKIT_API_SECRET"] = "FICTIONAL-local-test-secret-never-used-on-network"
os.environ["LIVEKIT_INFERENCE_API_KEY"] = "FICTIONAL"
os.environ["LIVEKIT_INFERENCE_API_SECRET"] = "FICTIONAL-local-test-secret-never-used-on-network"
