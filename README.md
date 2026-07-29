# DIKAMAHA PreMatch

Causal pre-match football forecasting engine with an HTTP API and Telegram and
Discord bots. DIKAMAHA combines a Dixon-Coles structural goal prior, Kalman
temporal evolution and market simulation. Markov market models remain in
shadow mode until they pass the frozen walk-forward gates.

> Experimental statistical analysis project. It is not financial advice and
> does not demonstrate economic edge, ROI or profitability against odds.

## What it does

- Produces predictions for upcoming and identified fixtures.
- Exposes 1X2, over 2.5 and BTTS, plus corners, shots, shots on target and
  cards by team, half and full match when coverage is available.
- Uses only information available before kickoff for each prediction.
- Maintains versioned snapshots, integrity hashes and causal provenance.
- Provides league, date, fixture, play-by-play, statistics, roster and player
  profile exploration through the bots.
- Publishes compact Telegram summaries and cards in `full` or `lite` mode.
- Includes health/readiness checks, authentication, rate limits, backpressure,
  timeouts, structured logs and a persistent SQLite channel ledger.

## Architecture

```text
ESPN raw-first
      ↓
Versioned pre-match snapshots
      ↓
Dixon-Coles → Kalman → simulation → markets
                         ↘ Markov shadow (only gated lines)
      ↓
DIKAMAHA API → Telegram / Discord / Railway
```

Dixon-Coles and Kalman are part of the current official chain for approved goal
markets. Markov does not replace that chain and is enabled per line only when
there is sufficient out-of-sample evidence; otherwise the router uses a safe
baseline fallback.

## Current status

- Phase 108 repository hygiene: validated.
- Reproducible Docker image: `181,455,750` bytes.
- Compressed active snapshot: `3,098,211` bytes with its logical hash verified.
- Test suite: 442 passed and 8 optional integrations skipped.
- Phase 107 Railway readiness: validated in local smoke tests.
- Goal Markov and several market lines remain shadow or fallback; they are not
  presented as a global promotion.

The complete historical evidence is retained locally and excluded from Git.
The remote repository only needs the code, contracts, active models and
runtime snapshot selected by `.gitignore`, `.dockerignore` and `Dockerfile`.

## Quick start with Docker

1. Copy `.env.telegram.example` and/or `.env.discord.example` to a local `.env`
   file and fill in only the required secrets. Never commit `.env`.
2. Build the image:

   ```bash
   docker build -t dikamaha:local .
   ```

3. Run the service:

   ```bash
   docker run --rm --env-file .env -p 8000:8000 dikamaha:local
   ```

4. Check availability:

   ```bash
   curl http://127.0.0.1:8000/v1/health
   curl http://127.0.0.1:8000/v1/readiness
   ```

The container runs as the unprivileged `app` user. For production, mount a
persistent volume at `/data` for the channel ledger and configure secrets in
Railway, never in the repository.

## Main API

The API requires `DIKAMAHA_API_KEY` when authentication is enabled. Health and
readiness endpoints are public for the orchestrator.

| Route | Purpose |
| --- | --- |
| `GET /v1/health` | Process liveness |
| `GET /v1/readiness` | Models, snapshot and dependencies ready |
| `GET /v1/upcoming` | Available upcoming fixtures |
| `POST /v1/predict/pre-match` | Normalized pre-match prediction |
| `POST /v1/predict/upcoming` | Prediction for an upcoming fixture |
| `POST /v1/predict/fixture` | Prediction for an identified fixture |
| `GET /v1/explorer/leagues` | Available leagues |
| `GET /v1/explorer/fixtures` | Fixtures by league and date |
| `GET /v1/explorer/match/plays` | Paginated play-by-play |
| `GET /v1/explorer/match/statistics` | First-half, second-half and full-match statistics |
| `GET /v1/explorer/player` | Individual player profile |
| `GET /v1/metrics` | Sanitized operational metrics |

Versioned JSON contracts are stored under
`artifacts/phase_6_1_inference_contract` and
`artifacts/phase_6_2_local_inference_service`.

## Telegram and Discord

The adapters consume the DIKAMAHA API. They do not duplicate prediction logic
or query ESPN directly. Configure user and server allowlists before opening a
pilot. Telegram channel mode accepts:

- `full`: all eligible fixtures for the next day.
- `lite`: the three nearest fixtures.

Channel delivery groups each fixture into an identifiable dashboard and keeps
first-half, second-half and full-match cards separate.

## Testing

Install development dependencies and run:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

PostgreSQL and Discord integrations are skipped by default when they are not
configured. See `docs/status.md` for phase status and `docs/decision_log.md`
for frozen decisions, gates and known limitations.

## Repository layout

```text
src/          domain logic, models, API and adapters
scripts/      operational runners and phase reproduction tools
tests/        unit, contract and integration tests
docs/         roadmap, decisions, contracts and reports
artifacts/    runtime contracts and local historical evidence
Dockerfile    minimal Railway packaging
```

Historical scripts are retained for reproducibility but are not copied into
the container: Docker uses an explicit runtime file list.

## License and usage

Add the appropriate license before publishing the repository. Third-party
data and the ESPN, Telegram and Discord brands remain subject to their own
terms of use.
