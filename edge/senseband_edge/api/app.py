"""FastAPI app: REST under /api, live WebSocket at /ws/live (docs/05_DATA_MODEL_AND_API.md)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import privacy
from ..record import RecorderError
from ..rehab import library as exlib
from ..rehab.engine import RehabError
from ..reports.builder import build_report
from ..reports.pdf import render_pdf
from ..service import EdgeService


def lan_addresses() -> list[str]:
    """Best-effort LAN IPv4 address(es) of this band (no traffic is sent)."""
    import socket
    out: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            out.append(s.getsockname()[0])
    except OSError:
        pass
    return [ip for ip in out if not ip.startswith("127.")] or ["127.0.0.1"]


APP_DIST = Path(__file__).resolve().parents[3] / "app" / "dist"


class ProgramIn(BaseModel):
    type: str
    purpose: str = "general"
    duration_h: float | None = None
    recovery_case: dict[str, Any] | None = None


class CalIn(BaseModel):
    joint: str
    wait: bool = True


class RomIn(BaseModel):
    movement: str
    phase: str = "baseline"
    side: str | None = None


class GripIn(BaseModel):
    side: str
    wait: bool = True


class PainIn(BaseModel):
    score: int = Field(ge=0, le=10)
    phase: str = "baseline"


class ExStart(BaseModel):
    exercise_id: str
    item: dict[str, Any] | None = None
    pain_before: int | None = Field(default=None, ge=0, le=10)


class ExStop(BaseModel):
    pain_after: int | None = Field(default=None, ge=0, le=10)


class LimitsIn(BaseModel):
    limits: dict[str, float]
    approved: bool = False


class ApproveIn(BaseModel):
    by: str = "caregiver"


class LinkIn(BaseModel):
    url: str
    device_key: str


class EraseIn(BaseModel):
    confirm: str


class RecordIn(BaseModel):
    label: str


class AckIn(BaseModel):
    by: str = "app"


class OkIn(BaseModel):
    by: str = "app"


class PatientIn(BaseModel):
    name: str | None = None
    dob: str | None = None
    sex: str | None = None
    height_cm: float | None = Field(default=None, gt=0, lt=300)
    weight_kg: float | None = Field(default=None, gt=0, lt=500)
    conditions: list[str] | None = None
    medications: list[str] | None = None
    resting_hr: float | None = Field(default=None, gt=20, lt=200)
    emergency_contacts: list[dict[str, str]] | None = None


def create_app(svc: EdgeService, autostart: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if autostart:
            await svc.start()
        yield
        if autostart:
            await svc.stop()

    app = FastAPI(title="SenseBand edge", version="0.1.0", lifespan=lifespan)
    app.state.svc = svc
    app.add_middleware(CORSMiddleware, allow_origins=svc.cfg.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/api/pair")
    def pair() -> dict[str, Any]:
        """What the app needs to connect. The same text is printed as a QR on the band."""
        ips = lan_addresses()
        port = svc.cfg.port
        return {"name": svc.cfg.device_name, "api": 1, "port": port,
                "mode": svc.source.name, "patient_id": svc.cfg.patient.id,
                "urls": [f"http://{ip}:{port}" for ip in ips],
                "qr": f"senseband://{ips[0] if ips else 'senseband.local'}:{port}"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return svc.status()

    @app.post("/api/sync/now")
    async def sync_now() -> dict[str, Any]:
        if svc.sync is None:
            return {"enabled": False}
        r = await asyncio.to_thread(svc.sync.step, True)
        return {"enabled": True, "result": {k: v for k, v in r.items() if k != "push"}, **svc.sync.status()}

    @app.get("/api/report")
    def report(program_id: str | None = None, format: str = "pdf", hours: float = 24.0,
               day: str | None = None) -> Response:
        """PDF (or JSON) report: the last `hours`, a calendar `day` (YYYY-MM-DD, local time) or a whole program."""
        import time as _t
        svc.engine.flush()
        now = int(_t.time() * 1000)
        if program_id:
            prog = svc.store.get("programs", program_id)
            if prog is None:
                raise HTTPException(404, "unknown program")
            since, until = int(prog["start_ts"]), now
        elif day:
            try:
                start = int(_t.mktime(_t.strptime(day, "%Y-%m-%d")) * 1000)
            except ValueError as e:
                raise HTTPException(422, "day must be YYYY-MM-DD") from e
            since, until = start, start + 86_400_000
        else:
            if not 0 < hours <= 24 * 90:
                raise HTTPException(422, "hours must be between 0 and 2160")
            since, until = now - int(hours * 3_600_000), now
        rep = build_report(svc.store, svc.cfg, svc.recovery, since, until, program_id)
        if format == "json":
            return Response(json.dumps(rep.to_json(), default=str), media_type="application/json")
        if format != "pdf":
            raise HTTPException(422, "format must be pdf or json")
        return Response(render_pdf(rep), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="senseband-report.pdf"'})

    @app.get("/api/export")
    def export_data() -> Response:
        """Everything the band holds about the patient, as a ZIP of plain files (DPDP Act 2023)."""
        svc.engine.flush()
        data = privacy.export_zip(svc.store, Path(svc.cfg.storage.recordings_dir))
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="senseband-data.zip"'})

    @app.post("/api/data/erase")
    def erase_data(body: EraseIn) -> dict[str, Any]:
        """Permanently delete all data on the band. Needs {"confirm": "DELETE"}; nothing else ever deletes data."""
        try:
            counts = privacy.erase_all(svc.store, Path(svc.cfg.storage.recordings_dir), body.confirm)
        except PermissionError as e:
            raise HTTPException(422, str(e)) from e
        svc.after_erase()
        return {"erased": counts}

    @app.post("/api/record/start")
    def record_start(body: RecordIn) -> dict[str, Any]:
        try:
            svc.engine.recorder.start(body.label, svc.engine.now, {"patient": svc.cfg.patient.id})
        except RecorderError as e:
            raise HTTPException(409 if "already" in str(e) else 422, str(e)) from e
        return {"recording": True, "label": body.label}

    @app.post("/api/record/stop")
    def record_stop() -> dict[str, Any]:
        try:
            return svc.engine.recorder.stop()
        except RecorderError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/record")
    def record_state() -> dict[str, Any]:
        r = svc.engine.recorder
        return {"recording": r.active, "label": r.label if r.active else None, "seconds": round(r.seconds, 1),
                "files": r.list()}

    @app.get("/api/models")
    def models_status() -> dict[str, Any]:
        return {"models": svc.engine.models.status(), "reasons": svc.engine.models.reasons}

    @app.post("/api/cloud/link")
    def cloud_link(body: LinkIn) -> dict[str, Any]:
        try:
            return {"patient_id": svc.cfg.patient.id, **svc.link_cloud(body.url, body.device_key)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.delete("/api/cloud/link")
    def cloud_unlink() -> dict[str, Any]:
        svc.unlink_cloud()
        return {"enabled": False}

    @app.get("/api/notes")
    def notes() -> list[dict[str, Any]]:
        """Doctor notes pulled from the cloud (shown in the patient app)."""
        return svc.store.list("notes", "patient_id=?", (svc.cfg.patient.id,), order="ts DESC", limit=100)

    @app.get("/api/signal-check")
    def signal_check() -> dict[str, Any]:
        return svc.engine.signal_check()

    @app.get("/api/patient")
    def get_patient() -> dict[str, Any]:
        return svc.ensure_patient()

    @app.put("/api/patient")
    def put_patient(body: PatientIn) -> dict[str, Any]:
        return svc.update_patient(body.model_dump(exclude_none=True))

    @app.post("/api/programs", status_code=201)
    def post_program(body: ProgramIn) -> dict[str, Any]:
        try:
            return svc.start_program(body.type, body.purpose, body.duration_h,
                                     recovery_case=body.recovery_case)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/programs/{program_id}/stop")
    def stop_program(program_id: str) -> dict[str, Any]:
        p = svc.stop_program(program_id)
        if p is None:
            raise HTTPException(404, "unknown program")
        return p

    @app.post("/api/events/{event_id}/ack")
    def ack(event_id: str, body: AckIn) -> dict[str, Any]:
        a = svc.engine.alerts.ack(event_id, body.by)
        if a is None and svc.store.get("events", event_id) is None:
            raise HTTPException(404, "unknown event")
        if a is None:     # older event (not in memory any more): update the stored row
            svc.store.update("events", event_id, {
                "acknowledged_by": body.by, "ack_ts": svc.engine.epoch_ms(svc.engine.now)})
        return svc.store.get("events", event_id) or {}

    @app.get("/api/events/{event_id}/strip")
    def strip(event_id: str, max_points: int = 3000) -> dict[str, Any]:
        """ECG strip saved with an event (mV), decimated for display."""
        import numpy as np
        rows = svc.store.list("ecg_strips", "event_id=?", (event_id,), limit=1)
        if not rows:
            raise HTTPException(404, "no ECG strip for this event")
        x = np.frombuffer(rows[0]["samples"], dtype=np.float32)
        step = max(1, len(x) // max(max_points, 1))
        return {"fs": rows[0]["fs"] / step, "ts": rows[0]["ts"],
                "samples": [round(float(v), 3) for v in x[::step]]}

    @app.post("/api/imok")
    def im_ok(body: OkIn | None = None) -> dict[str, Any]:
        """'I'm OK' from the app: same effect as the button - cancels the SMS countdown."""
        done = svc.engine.i_am_ok(body.by if body else "app")
        return {"cancelled": [svc.engine.public(a) for a in done]}

    @app.post("/api/sos")
    def sos() -> dict[str, Any]:
        a = svc.engine.sos()
        return {"alert": svc.engine.public(a) if a else None}

    @app.get("/api/history")
    def history(kind: str = "vitals", limit: int = 2000,
                      since: int | None = Query(None, alias="from"),
                      until: int | None = Query(None, alias="to")) -> dict[str, Any]:
        """`from`/`to` are epoch ms. kind: vitals | events | programs | assessments | sessions."""
        table, col = {"vitals": ("vitals_minute", "minute_ts"), "events": ("events", "ts"),
                      "programs": ("programs", "start_ts"),
                      "assessments": ("assessments", "ts"),
                      "sessions": ("exercise_sessions", "start_ts")}.get(kind, (None, None))
        if table is None:
            raise HTTPException(422, "kind must be vitals|events|programs|assessments|sessions")
        svc.engine.flush()
        where, args = ["1=1"], []
        if since is not None:
            where.append(f"{col}>=?")
            args.append(since)
        if until is not None:
            where.append(f"{col}<=?")
            args.append(until)
        rows = svc.store.list(table, " AND ".join(where), tuple(args), order=col, limit=limit)
        for r in rows:
            r.pop("samples", None)
        return {"kind": kind, "count": len(rows), "rows": rows}

    # ---------------- recovery (Milestone 3) ----------------
    reh = svc.engine.rehab

    def rehab_call(fn, *a, **k):  # noqa: ANN001, ANN202
        try:
            return fn(*a, **k)
        except RehabError as e:
            code = 409 if e.code in ("not_calibrated", "busy", "not_running", "pain_too_high") else 422
            raise HTTPException(code, {"code": e.code, "message": str(e)}) from e
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    async def wait_idle(timeout: float = 25.0) -> None:
        t0 = asyncio.get_running_loop().time()
        while reh.mode != "idle" and asyncio.get_running_loop().time() - t0 < timeout:
            await asyncio.sleep(0.05)

    @app.get("/api/rehab/state")
    def rehab_state() -> dict[str, Any]:
        return reh.state()

    @app.get("/api/exercises")
    def exercises(joint: str | None = None) -> list[dict[str, Any]]:
        return [e for e in exlib.library().values() if joint in (None, e["joint"])]

    @app.get("/api/recovery")
    def recovery() -> dict[str, Any]:
        p = svc.active_program()
        return {"program": p, "case": svc.recovery.case(p["id"] if p else None),
                "calibrated": sorted(reh.angles)}

    @app.put("/api/recovery/limits")
    def recovery_limits(body: LimitsIn) -> dict[str, Any]:
        p = svc.active_program()
        try:
            return svc.recovery.set_limits(p["id"] if p else "", body.limits, body.approved)
        except KeyError as e:
            raise HTTPException(404, "no recovery case") from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/calibrate")
    async def calibrate(body: CalIn) -> dict[str, Any]:
        rehab_call(reh.start_calibration, body.joint)
        if body.wait:
            await wait_idle()
        return reh.state()

    @app.post("/api/assessments/rom/start")
    def rom_start(body: RomIn) -> dict[str, Any]:
        return rehab_call(reh.start_rom, body.movement, body.phase, body.side)

    @app.post("/api/assessments/rom/stop")
    def rom_stop() -> dict[str, Any]:
        return rehab_call(reh.stop_rom)

    @app.post("/api/assessments/grip")
    async def grip(body: GripIn) -> dict[str, Any]:
        rehab_call(reh.start_grip, body.side)
        if body.wait:
            await wait_idle(15)
        return reh.state()

    @app.post("/api/assessments/pain")
    def pain(body: PainIn) -> dict[str, Any]:
        return rehab_call(reh.record_pain, body.score, body.phase)

    @app.get("/api/plan")
    def plan() -> dict[str, Any]:
        p = svc.active_program()
        cur = svc.recovery.current_plan(p["id"] if p else None)
        if cur is None:
            raise HTTPException(404, "no plan yet: do a baseline ROM test, then POST /api/plan/generate")
        return cur

    @app.post("/api/plan/generate")
    def plan_generate() -> dict[str, Any]:
        p = svc.active_program()
        if not p:
            raise HTTPException(404, "no active program")
        try:
            return svc.recovery.generate(p["id"])
        except KeyError as e:
            raise HTTPException(404, "no recovery case") from e
        except LookupError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/plan/approve")
    def plan_approve(body: ApproveIn) -> dict[str, Any]:
        p = svc.active_program()
        try:
            return svc.recovery.approve(p["id"] if p else "", body.by)
        except KeyError as e:
            raise HTTPException(404, "no plan") from e

    @app.post("/api/exercise/start")
    def exercise_start(body: ExStart) -> dict[str, Any]:
        return rehab_call(reh.start_exercise, body.exercise_id, body.item, body.pain_before)

    @app.post("/api/exercise/stop")
    def exercise_stop(body: ExStop | None = None) -> dict[str, Any]:
        return rehab_call(reh.stop_exercise, "user", body.pain_after if body else None)

    @app.post("/api/exercise/pain")
    def exercise_pain(body: PainIn) -> dict[str, Any]:
        return rehab_call(reh.record_pain, body.score, body.phase)

    @app.get("/api/progress")
    def progress() -> dict[str, Any]:
        p = svc.active_program()
        return svc.recovery.progress(p["id"] if p else None)

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await ws.accept()
        q = svc.hub.subscribe()
        try:
            await ws.send_json({"t": "status", **svc.status()})
            if svc.engine.vitals:
                await ws.send_json(svc.engine.vitals)
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    await ws.send_json({"t": "ping"})
                    continue
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            svc.hub.unsubscribe(q)

    if APP_DIST.exists():                                  # the PWA build (Milestone 2)
        app.mount("/", StaticFiles(directory=APP_DIST, html=True), name="app")
    return app
