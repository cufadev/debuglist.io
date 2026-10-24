"""Web interface: single check box + list upload with background jobs and CSV download."""

import io
import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, Response

from . import checks
from .tables import read_table, extract_addresses, write_table, summarize

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ADDRESSES = 20_000
MAX_RUNNING_JOBS = 3
JOB_TTL_SECONDS = 3600


def create_app(verifier, workers=8):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    jobs, guard = {}, threading.Lock()

    def purge():
        now = time.time()
        for jid in [j for j, v in jobs.items() if now - v["created"] > JOB_TTL_SECONDS]:
            del jobs[jid]

    def run(job):
        def tick(_res):
            job["done"] += 1

        try:
            job["results"] = verifier.check_many([r[job["idx"]] for r in job["rows"] if len(r) > job["idx"]],
                                                 workers=workers, on_result=tick)
            job["status"] = "done"
        except Exception as exc:                       # keep the UI informative instead of hanging
            job["status"], job["error"] = "error", str(exc)

    @app.get("/")
    def index():
        return render_template("index.html", smtp=verifier.smtp)

    @app.post("/api/check")
    def api_check():
        email = (request.get_json(silent=True) or {}).get("email", "")
        if not isinstance(email, str) or not email.strip():
            return jsonify(error="Escribí una dirección de correo."), 400
        return jsonify(verifier.check(email).as_dict())

    @app.post("/api/jobs")
    def api_jobs():
        upload = request.files.get("file")
        raw = upload.read().decode("utf-8-sig", errors="replace") if upload else request.form.get("text", "")
        if not raw.strip():
            return jsonify(error="Subí un archivo o pegá una lista."), 400
        try:
            header, rows, idx = read_table(raw if upload else "\n".join(extract_addresses(raw)),
                                           request.form.get("column") or None)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        total = len({checks.normalize(r[idx]) for r in rows if len(r) > idx and r[idx].strip()})
        if total == 0:
            return jsonify(error="No encontré direcciones de correo en la lista."), 400
        if total > MAX_ADDRESSES:
            return jsonify(error=f"Máximo {MAX_ADDRESSES} direcciones por lista (tiene {total})."), 413
        with guard:
            purge()
            if sum(j["status"] == "running" for j in jobs.values()) >= MAX_RUNNING_JOBS:
                return jsonify(error="Hay demasiadas listas en proceso; probá en unos minutos."), 429
            jid = secrets.token_urlsafe(9)
            job = jobs[jid] = dict(status="running", done=0, total=total, header=header, rows=rows, idx=idx,
                                   results={}, created=time.time())
        threading.Thread(target=run, args=(job,), daemon=True).start()
        return jsonify(id=jid, total=total), 202

    def get_job(jid):
        job = jobs.get(jid)
        return job

    @app.get("/api/jobs/<jid>")
    def api_job(jid):
        job = get_job(jid)
        if not job:
            return jsonify(error="Lista no encontrada o vencida."), 404
        body = dict(status=job["status"], done=job["done"], total=job["total"])
        if job["status"] == "done":
            body["counts"] = summarize(job["results"])
        if job["status"] == "error":
            body["error"] = job.get("error", "")
        return jsonify(body)

    @app.get("/api/jobs/<jid>/csv")
    def api_job_csv(jid):
        job = get_job(jid)
        if not job or job["status"] != "done":
            return jsonify(error="El resultado todavía no está listo."), 404
        out = io.StringIO()
        write_table(job["header"], job["rows"], job["idx"], job["results"], out)
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=bugmail-resultado.csv"})

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(error="El archivo supera los 5 MB."), 413

    return app
