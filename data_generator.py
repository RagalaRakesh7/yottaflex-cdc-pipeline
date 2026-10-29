
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def generate_events(
    count=1000,
    seed=42,
    output_path=None,
    duplicate_rate=0.02,
    invalid_rate=0.01,
    stale_rate=0.02,
):
    if count < 0:
        raise ValueError("count must be non-negative")

    rng = random.Random(seed)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = DATA_DIR / "generated_events.jsonl"
    else:
        output_path = Path(output_path)
        if not output_path.is_absolute():
            output_path = BASE_DIR / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = []
    latest_versions = {}
    customer_count = max(10, min(10000, count // 10 or 10))

    for i in range(count):
        customer_number = rng.randint(1, customer_count)
        customer_id = f"CUST-{customer_number:06d}"
        version = latest_versions.get(customer_id, 0) + 1
        latest_versions[customer_id] = version

        operation = rng.choices(
            ["INSERT", "UPDATE", "DELETE"],
            weights=[30, 55, 15],
            k=1,
        )[0]

        event_time = base_time + timedelta(seconds=i)
        event = {
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}-{i}")),
            "customer_id": customer_id,
            "operation": operation,
            "version": version,
            "event_time": event_time.isoformat(),
            "name": f"Customer {customer_number}",
            "email": f"customer{customer_number}@example.com",
            "amount": round(rng.uniform(10, 5000), 2),
        }

        if operation == "DELETE":
            event.pop("name")
            event.pop("email")
            event.pop("amount")

        events.append(event)

    # Add duplicates, stale versions, and invalid records.
    original_events = list(events)
    extra_events = []

    for i, event in enumerate(original_events):
        if rng.random() < duplicate_rate:
            extra_events.append(dict(event))

        if rng.random() < stale_rate and event["version"] > 1:
            stale = dict(event)
            stale["event_id"] = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}-stale-{i}")
            )
            stale["version"] = event["version"] - 1
            extra_events.append(stale)

        if rng.random() < invalid_rate:
            invalid = dict(event)
            invalid["event_id"] = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}-invalid-{i}")
            )
            invalid.pop("customer_id", None)
            extra_events.append(invalid)

    # Keep generated event ordering reproducible.
    events.extend(extra_events)
    rng.shuffle(events)

    with output_path.open("w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, sort_keys=True) + "\n")

    return {
        "output_file": str(output_path),
        "requested_base_events": count,
        "total_written": len(events),
        "seed": seed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate reproducible ChangeStream JSONL events"
    )
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="data/generated_events.jsonl")
    parser.add_argument("--duplicate-rate", type=float, default=0.02)
    parser.add_argument("--invalid-rate", type=float, default=0.01)
    parser.add_argument("--stale-rate", type=float, default=0.02)
    args = parser.parse_args()

    result = generate_events(
        count=args.count,
        seed=args.seed,
        output_path=args.output,
        duplicate_rate=args.duplicate_rate,
        invalid_rate=args.invalid_rate,
        stale_rate=args.stale_rate,
    )
    print(json.dumps(result, indent=2))
