# fulcra-api

Vercel Python endpoint for a normalized weekly health report from Fulcra.

## Endpoint

After deploying to Vercel:

```text
GET /api/fulcra_yesterday
```

Useful query parameters:

```text
days=7
tz=America/Bogota
start=2026-08-17T00:00:00-05:00
end=2026-08-24T00:00:00-05:00
include_raw=false
```

If `start` and `end` are omitted, the endpoint returns the last `days` complete calendar days in the configured time zone.

## Vercel environment variables

Required, at least one:

```text
FULCRA_REFRESH_TOKEN=...
FULCRA_ACCESS_TOKEN=...
```

Recommended:

```text
FULCRA_ENDPOINT_API_KEY=choose-a-private-key
DEFAULT_TIMEZONE=America/Bogota
DEFAULT_DAYS=7
```

Optional:

```text
FULCRA_ACCESS_TOKEN_EXPIRATION=2026-08-24T16:00:00Z
FULCRA_USERID=...
CORS_ALLOW_ORIGIN=*
```

When `FULCRA_ENDPOINT_API_KEY` is set, call the endpoint with:

```text
x-api-key: your-private-key
```

## Local token helper

Run this locally, authenticate in the browser, then copy the printed values into Vercel environment variables:

```powershell
python -m pip install -r requirements.txt
python scripts/auth_fulcra.py
```

## Response shape

The endpoint returns:

```json
{
  "ok": true,
  "timezone": "America/Bogota",
  "window": {
    "start": "2026-08-17T00:00:00-05:00",
    "end": "2026-08-24T00:00:00-05:00",
    "days": 7
  },
  "daily": []
}
```

Each day is normalized into:

```text
activity
cardio_recovery
sleep
events
```

The metrics are based on the prior Fulcra health-analysis plan:

```text
StepCount
ActiveCaloriesBurned
AppleWatchExerciseTime
SleepStage / sleep_agg
RestingHeartRate
HeartRateVariabilitySDNN
VO2Max
Weight
BloodOxygenSaturation
RespiratoryRate
HighHeartRateEvent
LowHeartRateEvent
IrregularHeartRhythmEvent
```
