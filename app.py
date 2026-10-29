import csv
import io
import json
import sqlite3
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from cdc_engine import (
    DB_PATH,
    DATA_DIR,
    get_connection,
    get_reconciliation,
    initialize_database,
    process_file,
)
from data_generator import generate_events


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_FILE = DATA_DIR / "sample_events.jsonl"

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-local-development-key"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
initialize_database()


def query_all(sql, params=()):
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(sql, params).fetchall()
        ]


def dashboard_metrics():
    with get_connection() as conn:
        current = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) AS deleted
            FROM customers_current
            """
        ).fetchone()

        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS history_events,
                (SELECT COUNT(*) FROM processed_events) AS processed_events,
                (SELECT COUNT(*) FROM quarantine_events) AS quarantined,
                (SELECT COUNT(*) FROM pipeline_runs) AS total_runs
            FROM event_history
            """
        ).fetchone()

        latest = conn.execute(
            """
            SELECT *
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()

        throughput = 0
        if latest and latest["duration_seconds"]:
            throughput = round(
                latest["input_count"] / latest["duration_seconds"], 2
            )

    return {
        "total_customers": current["total"] or 0,
        "active_customers": current["active"] or 0,
        "deleted_customers": current["deleted"] or 0,
        "history_events": totals["history_events"] or 0,
        "processed_events": totals["processed_events"] or 0,
        "quarantined_records": totals["quarantined"] or 0,
        "total_runs": totals["total_runs"] or 0,
        "latest_run": dict(latest) if latest else None,
        "throughput": throughput,
        "reconciliation": get_reconciliation(),
    }


@app.route("/")
def dashboard():
    metrics = dashboard_metrics()

    runs = query_all(
        """
        SELECT *
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT 8
        """
    )

    with get_connection() as conn:
        operation_rows = conn.execute(
            """
            SELECT operation, COUNT(*) AS count
            FROM event_history
            GROUP BY operation
            """
        ).fetchall()

        run_rows = conn.execute(
            """
            SELECT started_at, input_count, applied_count, rejected_count
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT 7
            """
        ).fetchall()

    charts = {
        "operations": {
            "labels": [row["operation"] for row in operation_rows],
            "values": [row["count"] for row in operation_rows],
        },
        "runs": {
            "labels": [
                row["started_at"][11:19]
                for row in reversed(run_rows)
            ],
            "input": [
                row["input_count"]
                for row in reversed(run_rows)
            ],
            "applied": [
                row["applied_count"]
                for row in reversed(run_rows)
            ],
            "rejected": [
                row["rejected_count"]
                for row in reversed(run_rows)
            ],
        },
    }

    return render_template(
        "dashboard.html",
        metrics=metrics,
        runs=runs,
        charts=charts,
        active_page="dashboard",
    )


@app.route("/run-sample", methods=["POST"])
def run_sample():
    if not SAMPLE_FILE.exists():
        flash(
            "Sample file not found. Create data/sample_events.jsonl first.",
            "error",
        )
        return redirect(url_for("dashboard"))

    try:
        result = process_file(SAMPLE_FILE)
        flash(
            f"Sample run completed: {result['input_count']} input events, "
            f"{result['applied_count']} applied, "
            f"{result['rejected_count']} rejected.",
            "success",
        )
    except Exception as exc:
        app.logger.exception("Sample pipeline failed")
        flash(f"Sample run failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/generate", methods=["POST"])
def generate_data():
    try:
        count = int(request.form.get("count", "1000"))
        seed = int(request.form.get("seed", "42"))

        if count < 1 or count > 1_000_000:
            raise ValueError("Count must be between 1 and 1,000,000.")

        result = generate_events(count=count, seed=seed)

        flash(
            f"Generated {result['total_written']:,} events in "
            f"{Path(result['output_file']).name}.",
            "success",
        )
    except (ValueError, TypeError) as exc:
        flash(f"Could not generate data: {exc}", "error")
    except Exception as exc:
        app.logger.exception("Dataset generation failed")
        flash(f"Dataset generation failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/run-generated", methods=["POST"])
def run_generated():
    generated_file = DATA_DIR / "generated_events.jsonl"

    if not generated_file.exists():
        flash("Generate a dataset before running the pipeline.", "error")
        return redirect(url_for("dashboard"))

    try:
        result = process_file(generated_file)
        flash(
            f"Run completed: {result['input_count']:,} input events in "
            f"{result['duration_seconds']:.2f} seconds.",
            "success",
        )
    except Exception as exc:
        app.logger.exception("Generated pipeline failed")
        flash(f"Pipeline failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["POST"])
def upload_file():
    uploaded = request.files.get("file")

    if not uploaded or not uploaded.filename:
        flash("Select a JSONL file to upload.", "error")
        return redirect(url_for("dashboard"))

    filename = secure_filename(uploaded.filename)

    if not filename.lower().endswith((".jsonl", ".ndjson")):
        flash("Upload a .jsonl or .ndjson file.", "error")
        return redirect(url_for("dashboard"))

    if not filename:
        flash("The uploaded filename is invalid.", "error")
        return redirect(url_for("dashboard"))

    destination = UPLOAD_DIR / filename

    try:
        uploaded.save(destination)
        result = process_file(destination)

        flash(
            f"Uploaded file processed: {result['input_count']:,} events, "
            f"{result['rejected_count']} rejected.",
            "success",
        )
    except Exception as exc:
        app.logger.exception("Uploaded file processing failed")
        flash(f"File processing failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/events")
def events():
    page = max(request.args.get("page", 1, type=int), 1)
    page_size = 50
    offset = (page - 1) * page_size

    search = request.args.get("q", "").strip()
    operation = request.args.get("operation", "").upper()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    clauses = []
    params = []

    if search:
        clauses.append("(customer_id LIKE ? OR event_id LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if operation in {"INSERT", "UPDATE", "DELETE"}:
        clauses.append("operation = ?")
        params.append(operation)

    if start_date:
        clauses.append("substr(event_time, 1, 10) >= ?")
        params.append(start_date)

    if end_date:
        clauses.append("substr(event_time, 1, 10) <= ?")
        params.append(end_date)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM event_history{where}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT *
            FROM event_history{where}
            ORDER BY history_id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    return render_template(
        "events.html",
        events=[dict(row) for row in rows],
        page=page,
        pages=max(1, (total + page_size - 1) // page_size),
        total=total,
        q=search,
        operation=operation,
        start_date=start_date,
        end_date=end_date,
        active_page="events",
    )


@app.route("/events/<int:history_id>")
def event_detail(history_id):
    """Display the complete details for one immutable audit event."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM event_history
            WHERE history_id = ?
            """,
            (history_id,),
        ).fetchone()

    if row is None:
        flash("Audit event not found.", "error")
        return redirect(url_for("events"))

    event = dict(row)

    try:
        event["payload_pretty"] = json.dumps(
            json.loads(event["payload_json"]),
            indent=2,
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        event["payload_pretty"] = event["payload_json"]

    return render_template(
        "event_detail.html",
        event=event,
        active_page="events",
    )


@app.route("/quarantine")
def quarantine():
    rows = query_all(
        """
        SELECT *
        FROM quarantine_events
        ORDER BY quarantine_id DESC
        LIMIT 200
        """
    )

    return render_template(
        "quarantine.html",
        records=rows,
        active_page="quarantine",
    )


@app.route("/runs")
def runs():
    rows = query_all(
        """
        SELECT *
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT 200
        """
    )

    return render_template(
        "runs.html",
        runs=rows,
        active_page="runs",
    )


@app.route("/reports/history.csv")
def export_history():
    """Download audit history as CSV."""
    rows = query_all(
        """
        SELECT
            history_id,
            event_id,
            customer_id,
            operation,
            event_version,
            event_time,
            processed_at,
            outcome,
            source_file,
            payload_json
        FROM event_history
        ORDER BY history_id
        """
    )

    output = io.StringIO()
    columns = [
        "history_id",
        "event_id",
        "customer_id",
        "operation",
        "event_version",
        "event_time",
        "processed_at",
        "outcome",
        "source_file",
        "payload_json",
    ]

    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="cdc_audit_history.csv"'
        },
    )


@app.route("/reports/runs.csv")
def export_runs():
    """Download pipeline execution metrics as CSV."""
    rows = query_all(
        """
        SELECT
            run_id,
            source_file,
            started_at,
            ended_at,
            status,
            input_count,
            accepted_count,
            applied_count,
            duplicate_count,
            stale_count,
            rejected_count,
            duration_seconds,
            error_message
        FROM pipeline_runs
        ORDER BY started_at DESC
        """
    )

    output = io.StringIO()
    columns = [
        "run_id",
        "source_file",
        "started_at",
        "ended_at",
        "status",
        "input_count",
        "accepted_count",
        "applied_count",
        "duplicate_count",
        "stale_count",
        "rejected_count",
        "duration_seconds",
        "error_message",
    ]

    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="cdc_pipeline_runs.csv"'
        },
    )


@app.route("/reports/reconciliation.json")
def export_reconciliation():
    """Download the current reconciliation result as JSON."""
    report = get_reconciliation()

    return Response(
        json.dumps(report, indent=2, default=str),
        mimetype="application/json",
        headers={
            "Content-Disposition":
                'attachment; filename="cdc_reconciliation.json"'
        },
    )


@app.route("/api/metrics")
def api_metrics():
    return jsonify(dashboard_metrics())


@app.route("/api/reconciliation")
def api_reconciliation():
    return jsonify(get_reconciliation())


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "database": str(DB_PATH.name),
    })


@app.errorhandler(413)
def too_large(_error):
    flash("Upload exceeds the 50 MB limit.", "error")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000,
    )
