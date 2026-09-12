# Hydra Nexus

Unified threaded reader and archive for Signal groups.

## Architecture
1. **Signal Ingest:** `signal-cli daemon` captures messages in real-time.
2. **Nexus Bridge:** A daemon that maps Signal group IDs to Lemmy community slugs.
3. **Threaded Storage:** Messages are posted to Lemmy as comments/posts to preserve threading.
