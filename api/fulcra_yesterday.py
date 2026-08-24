import json
import os
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fulcra_api.core import FulcraAPI


DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Bogota")

CUMULATIVE_METRICS = {
    "StepCount": {
        "key": "steps",
        "unit": "count",
        "description": "Pasos",
    },
    "ActiveCaloriesBurned": {
        "key": "active_calories",
        "unit": "cal",
        "description": "Calorias activas",
    },
    "AppleWatchExerciseTime": {
        "key": "exercise_minutes",
        "unit": "min",
        "description": "Minutos de ejercicio",
    },
}

INSTANT_METRICS = {
    "RestingHeartRate": {
        "key": "resting_heart_rate",
        "unit": "bpm",
        "description": "Frecuencia cardiaca en reposo",
    },
    "HeartRateVariabilitySDNN": {
        "key": "hrv_sdnn",
        "unit": "ms",
        "description": "HRV SDNN",
    },
    "VO2Max": {
        "key": "vo2max",
        "unit": "ml/kg/min",
        "description": "VO2 Max",
    },
    "Weight": {
        "key": "weight",
        "unit": "kg",
        "description": "Peso",
    },
    "BloodOxygenSaturation": {
        "key": "blood_oxygen",
        "unit": "percent",
        "description": "Oxigeno en sangre",
    },
    "RespiratoryRate": {
        "key": "respiratory_rate",
        "unit": "breaths/min",
        "description": "Frecuencia respiratoria",
    },
}

EVENT_METRICS = {
    "HighHeartRateEvent": {
        "key": "high_heart_rate_events",
        "description": "Eventos de frecuencia cardiaca alta",
    },
    "LowHeartRateEvent": {
        "key": "low_heart_rate_events",
        "description": "Eventos de frecuencia cardiaca baja",
    },
    "IrregularHeartRhythmEvent": {
        "key": "irregular_rhythm_events",
        "description": "Eventos de ritmo irregular",
    },
}


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    return str(value)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _week_window(query: dict[str, list[str]], tz: ZoneInfo) -> tuple[datetime, datetime]:
    end_param = _first(query, "end")
    start_param = _first(query, "start")

    if end_param:
        end = _parse_datetime(end_param, tz)
    else:
        now = datetime.now(tz)
        end = datetime.combine(now.date() + timedelta(days=1), time.min, tz)

    if start_param:
        start = _parse_datetime(start_param, tz)
    else:
        days = int(_first(query, "days") or os.getenv("DEFAULT_DAYS", "7"))
        if days < 1 or days > 31:
            raise APIError(400, "days must be between 1 and 31")
        start = end - timedelta(days=days)

    if start >= end:
        raise APIError(400, "start must be before end")

    return start, end


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _date_key(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).date().isoformat()


def _to_records(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if hasattr(result, "to_dict"):
        return result.to_dict(orient="records")
    return []


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_value(record: dict[str, Any], metric_name: str) -> float | None:
    metric_key = metric_name.lower()
    candidates = [
        "value",
        "quantity",
        "amount",
        "sum",
        metric_key,
        metric_key.replace("applewatch", "apple_watch"),
    ]
    for key in candidates:
        if key in record:
            value = _number(record[key])
            if value is not None:
                return value

    for key, raw in record.items():
        if isinstance(raw, (int, float)) and key not in {
            "start_time",
            "end_time",
            "timestamp",
            "duration",
        }:
            return float(raw)
    return None


def _find_time(record: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _empty_days(start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    current = start.date()
    while current < end.date():
        key = current.isoformat()
        days[key] = {
            "date": key,
            "activity": {},
            "cardio_recovery": {},
            "sleep": {},
            "events": {},
        }
        current += timedelta(days=1)
    return days


def _get_client() -> FulcraAPI:
    access_token = os.getenv("FULCRA_ACCESS_TOKEN")
    refresh_token = os.getenv("FULCRA_REFRESH_TOKEN")
    expiration_raw = os.getenv("FULCRA_ACCESS_TOKEN_EXPIRATION")
    expiration = None

    if expiration_raw:
        expiration = datetime.fromisoformat(expiration_raw.replace("Z", "+00:00"))
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)

    if not access_token and not refresh_token:
        raise APIError(
            500,
            "Missing FULCRA_ACCESS_TOKEN or FULCRA_REFRESH_TOKEN environment variable",
        )

    client = FulcraAPI(
        access_token=access_token,
        access_token_expiration=expiration,
        refresh_token=refresh_token,
    )

    if refresh_token and (not access_token or _token_is_expired(expiration)):
        client.refresh_access_token()

    return client


def _token_is_expired(expiration: datetime | None) -> bool:
    if expiration is None:
        return False
    return expiration <= datetime.now(timezone.utc) + timedelta(minutes=5)


def _call_metric_samples(
    client: FulcraAPI,
    metric_name: str,
    start: datetime,
    end: datetime,
    fulcra_userid: str | None,
) -> list[dict[str, Any]]:
    return _to_records(
        client.metric_samples(
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            metric=metric_name,
            fulcra_userid=fulcra_userid,
        )
    )


def _safe_call(label: str, fn: Any) -> tuple[Any, dict[str, str] | None]:
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 - API errors must not kill whole report.
        return None, {"source": label, "error": str(exc)}


def _summarize_cumulative_metric(
    records: list[dict[str, Any]],
    metric_name: str,
    tz: ZoneInfo,
) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for record in records:
        value = _find_value(record, metric_name)
        if value is None:
            continue
        record_time = _find_time(
            record,
            "start_time",
            "start_date",
            "timestamp",
            "date",
            "end_time",
            "end_date",
        )
        if record_time is None:
            continue
        totals[_date_key(record_time, tz)] += value
    return {day: round(value, 2) for day, value in totals.items()}


def _summarize_instant_metric(
    records: list[dict[str, Any]],
    metric_name: str,
    tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for record in records:
        value = _find_value(record, metric_name)
        record_time = _find_time(
            record,
            "timestamp",
            "start_time",
            "start_date",
            "end_time",
            "end_date",
            "date",
        )
        if value is None or record_time is None:
            continue
        grouped[_date_key(record_time, tz)].append((record_time, value))

    summary = {}
    for day, values in grouped.items():
        numbers = [value for _, value in values]
        latest_time, latest_value = max(values, key=lambda item: item[0])
        summary[day] = {
            "avg": round(sum(numbers) / len(numbers), 2),
            "min": round(min(numbers), 2),
            "max": round(max(numbers), 2),
            "latest": round(latest_value, 2),
            "latest_at": latest_time.isoformat(),
            "samples": len(numbers),
        }
    return summary


def _summarize_events(
    records: list[dict[str, Any]],
    tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        record_time = _find_time(
            record,
            "timestamp",
            "start_time",
            "start_date",
            "end_time",
            "end_date",
            "date",
        )
        if record_time is None:
            continue
        grouped[_date_key(record_time, tz)].append(record)
    return {
        day: {"count": len(records), "records": records[:10]}
        for day, records in grouped.items()
    }


def _summarize_sleep(client: FulcraAPI, start: datetime, end: datetime, tz: ZoneInfo, fulcra_userid: str | None) -> Any:
    result = client.sleep_agg(
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        period="1d",
        tz=str(tz),
        fulcra_userid=fulcra_userid,
    )
    rows = _to_records(result)
    by_day = {}
    for row in rows:
        day_raw = row.get("period") or row.get("date") or row.get("start_time")
        if isinstance(day_raw, datetime):
            day = _date_key(day_raw, tz)
        elif isinstance(day_raw, str):
            day = _date_key(_parse_datetime(day_raw, tz), tz)
        else:
            continue
        by_day[day] = row
    return by_day


def build_report(query: dict[str, list[str]]) -> dict[str, Any]:
    tz = ZoneInfo(_first(query, "tz") or DEFAULT_TZ)
    start, end = _week_window(query, tz)
    fulcra_userid = _first(query, "fulcra_userid") or os.getenv("FULCRA_USERID")
    include_raw = _parse_bool(_first(query, "include_raw"), False)

    client = _get_client()
    days = _empty_days(start, end)
    errors: list[dict[str, str]] = []
    raw: dict[str, Any] = {}

    for metric_name, config in CUMULATIVE_METRICS.items():
        records, error = _safe_call(
            metric_name,
            lambda metric_name=metric_name: _call_metric_samples(
                client,
                metric_name,
                start,
                end,
                fulcra_userid,
            ),
        )
        if error:
            errors.append(error)
            continue
        if include_raw:
            raw[metric_name] = records
        for day, value in _summarize_cumulative_metric(records, metric_name, tz).items():
            if day in days:
                days[day]["activity"][config["key"]] = {
                    "value": value,
                    "unit": config["unit"],
                    "description": config["description"],
                }

    for metric_name, config in INSTANT_METRICS.items():
        records, error = _safe_call(
            metric_name,
            lambda metric_name=metric_name: _call_metric_samples(
                client,
                metric_name,
                start,
                end,
                fulcra_userid,
            ),
        )
        if error:
            errors.append(error)
            continue
        if include_raw:
            raw[metric_name] = records
        for day, value in _summarize_instant_metric(records, metric_name, tz).items():
            if day in days:
                days[day]["cardio_recovery"][config["key"]] = {
                    **value,
                    "unit": config["unit"],
                    "description": config["description"],
                }

    for metric_name, config in EVENT_METRICS.items():
        records, error = _safe_call(
            metric_name,
            lambda metric_name=metric_name: _call_metric_samples(
                client,
                metric_name,
                start,
                end,
                fulcra_userid,
            ),
        )
        if error:
            errors.append(error)
            continue
        if include_raw:
            raw[metric_name] = records
        for day, value in _summarize_events(records, tz).items():
            if day in days:
                days[day]["events"][config["key"]] = {
                    **value,
                    "description": config["description"],
                }

    sleep, error = _safe_call(
        "sleep_agg",
        lambda: _summarize_sleep(client, start, end, tz, fulcra_userid),
    )
    if error:
        errors.append(error)
    else:
        for day, value in sleep.items():
            if day in days:
                days[day]["sleep"] = value

    return {
        "ok": True,
        "timezone": str(tz),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": (end.date() - start.date()).days,
        },
        "metrics": {
            "activity": list(CUMULATIVE_METRICS),
            "cardio_recovery": list(INSTANT_METRICS),
            "events": list(EVENT_METRICS),
            "sleep": ["sleep_agg"],
        },
        "daily": [days[key] for key in sorted(days)],
        "errors": errors,
        "raw": raw if include_raw else None,
    }


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", os.getenv("CORS_ALLOW_ORIGIN", "*"))
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", os.getenv("CORS_ALLOW_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "content-type,x-api-key")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            payload = build_report(query)
            _write_json(self, 200, payload)
        except APIError as exc:
            _write_json(self, exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:  # noqa: BLE001 - serverless endpoint returns JSON errors.
            _write_json(self, 500, {"ok": False, "error": str(exc)})
