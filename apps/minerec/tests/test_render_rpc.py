from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.render.control.contract import (
    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY,
    FULL_CLIENT_PRESENTATION_CONTRACT,
)
from minerec.render.control.rpc import RenderRpcService, _PlanLeaseKeeper
from minerec.render.control.sources import ReplayNotReadyError, ReplaySegmentSource
from minerec.render.control.transfer import ImportedRenderResult

WORKER_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CONNECTION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SESSION_ID = "20260721T000000.000Z-deadbeef"
NEW_SEGMENT = "22222222-2222-4222-8222-222222222222"
OLD_SEGMENT = "33333333-3333-4333-8333-333333333333"


def _digest(path: Path) -> tuple[str, int]:
    value = path.read_bytes()
    return hashlib.sha256(value).hexdigest(), len(value)


def _write_archive(
    path: Path,
    *,
    segment_id: str = NEW_SEGMENT,
    segment_ordinal: int = 0,
    connection_id: str = CONNECTION_ID,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema_version": 3,
        "session_id": SESSION_ID,
        "segment_id": segment_id,
        "segment_ordinal": segment_ordinal,
        "player_uuid": PLAYER_ID,
        "connection_id": connection_id,
        "hotbar_snapshot_contract": "item_stack_copy_v1",
        "flashback_capture_contract": "client_visible_scene_v1",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"uuid": str(uuid.uuid4())}))
        archive.writestr(
            "arcade_replay_meta.json",
            json.dumps({"mc_recorder": identity}),
        )
        archive.writestr("chunks/c0.flashback", b"replay")


class RenderRpcServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.runtime = root / "runtime"
        self.replays = root / "replays"
        self.captures = root / "captures"
        self.exports = root / "exports"
        for path in (self.runtime, self.replays, self.captures, self.exports):
            path.mkdir()
        (self.captures / SESSION_ID).mkdir()
        self.config = SimpleNamespace(
            paths=SimpleNamespace(
                runtime=self.runtime,
                replays=self.replays,
                captures=self.captures,
                exports=self.exports,
            )
        )
        self.service = RenderRpcService(self.config)  # ty:ignore[invalid-argument-type]
        self.service.dispatch(
            "register",
            {
                "worker_id": WORKER_ID,
                "name": "ephemeral-test",
                "capabilities": {
                    "portable_request_no_gui": True,
                    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY: (FULL_CLIENT_PRESENTATION_CONTRACT),
                    "structured_claim_failure": True,
                },
            },
        )
        self.job = self.service.queue.create(
            {
                "session_id": SESSION_ID,
                "player_uuid": PLAYER_ID,
                "connection_id": CONNECTION_ID,
                "dataset_id": "c" * 32,
                "start_tick": 10,
                "end_tick": 40,
                "render": {"width": 640, "height": 360, "fps": 20},
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source(self, segment_id: str, ordinal: int, contents: bytes) -> ReplaySegmentSource:
        path = self.replays / f"{segment_id}.zip"
        path.write_bytes(contents)
        digest, size = _digest(path)
        return ReplaySegmentSource(
            segment_id=segment_id,
            segment_ordinal=ordinal,
            player_uuid=PLAYER_ID,
            connection_id=CONNECTION_ID,
            path=path,
            replay_format="flashback",
            sha256=digest,
            size_bytes=size,
        )

    def _gui_job(self) -> dict[str, object]:
        payload = dict(self.job["payload"])  # ty:ignore[no-matching-overload]
        payload["render"] = {
            **payload["render"],
            "no_gui": False,
            "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
        }
        return self.service.queue.create(payload)

    def _claim(self, sources: list[ReplaySegmentSource], *, lease_seconds: int = 120) -> dict[str, object]:
        payload = self.job["payload"]
        hud_envelope = {
            "schema_version": 1,
            "sidecar_type": "mc-recorder-structured-hud-v1",
            "format": "jsonl",
            "sha256": "d" * 64,
            "size_bytes": 123,
            "record_count": payload["end_tick"] - payload["start_tick"] + 1,  # ty:ignore[not-subscriptable]
            "first_tick": payload["start_tick"],  # ty:ignore[not-subscriptable]
            "last_tick": payload["end_tick"],  # ty:ignore[not-subscriptable]
            "dataset_id": payload["dataset_id"],  # ty:ignore[not-subscriptable]
            "dataset_manifest_sha256": "e" * 64,
            "samples_sha256": "f" * 64,
            "session_id": payload["session_id"],  # ty:ignore[not-subscriptable]
            "player_uuid": payload["player_uuid"],  # ty:ignore[not-subscriptable]
            "connection_id": payload["connection_id"],  # ty:ignore[not-subscriptable]
        }
        with (
            mock.patch("minerec.render.control.rpc.resolve_replay_segments", return_value=sources),
            mock.patch(
                "minerec.render.control.rpc.create_structured_hud_sidecar",
                return_value=SimpleNamespace(envelope=lambda: hud_envelope),
            ),
        ):
            return self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": self.job["id"],
                    "lease_seconds": lease_seconds,
                },
            )

    @staticmethod
    def _portable_mocks() -> tuple[list[dict[str, object]], Any, Any, Any, Any]:  # noqa: ANN401
        created: list[dict[str, object]] = []

        def create(_episode: Path, _replay: Path, **kwargs: object) -> dict[str, object]:
            created.append(dict(kwargs))
            return {
                "request_id": kwargs["request_id"],
                "segment_id": kwargs["segment_id"],
                "first_tick": kwargs["first_tick"],
                "last_tick": kwargs["last_tick"],
                "range_policy": kwargs["range_policy"],
                "newer_cutoff": kwargs["newer_cutoff"],
            }

        def write(path: Path, value: dict[str, object]) -> SimpleNamespace:
            encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if path.exists() and path.read_bytes() != encoded:
                raise RecorderError("portable request conflict")
            path.write_bytes(encoded)
            return SimpleNamespace(
                data=value,
                sha256=hashlib.sha256(encoded).hexdigest(),
                request_id=value["request_id"],
            )

        return created, create, write  # ty:ignore[invalid-return-type]

    def _request(
        self,
        claimed: dict[str, object],
        segment_id: str,
        *,
        newer_cutoff: int | None = None,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        claim = claimed["claim"]
        assert isinstance(claim, dict)
        attempt = claim["attempt"]  # ty:ignore[invalid-argument-type]
        assert isinstance(attempt, dict)
        body: dict[str, object] = {
            "worker_id": WORKER_ID,
            "attempt_id": attempt["id"],  # ty:ignore[invalid-argument-type]
            "lease_token": attempt["lease_token"],  # ty:ignore[invalid-argument-type]
            "segment_id": segment_id,
        }
        if newer_cutoff is not None:
            body["newer_cutoff"] = newer_cutoff
        created, create, write = self._portable_mocks()  # ty:ignore[invalid-assignment]
        with (
            mock.patch("minerec.render.control.rpc.create_portable_render_request", create),
            mock.patch("minerec.render.control.rpc.write_portable_render_request", write),
        ):
            result = self.service.dispatch("request", body)
        return result, created

    def test_server_plan_lease_keeper_renews_during_slow_input_preparation(self) -> None:
        claim = self.service.queue.claim(WORKER_ID, job_id=self.job["id"], lease_seconds=10)  # ty:ignore[invalid-argument-type]
        assert claim is not None
        attempt = claim["attempt"]
        with mock.patch.object(
            self.service.queue,
            "heartbeat",
            wraps=self.service.queue.heartbeat,
        ) as heartbeat:
            keeper = _PlanLeaseKeeper(
                self.service.queue,
                worker_id=WORKER_ID,
                attempt_id=attempt["id"],
                lease_token=attempt["lease_token"],
                lease_seconds=10,
                interval_seconds=0.01,
            )
            keeper.start()
            time.sleep(0.035)
            keeper.finish()

        self.assertGreaterEqual(heartbeat.call_count, 3)

    def test_failed_plan_removes_only_the_owned_hud_sidecar_bytes(self) -> None:
        self.job = self._gui_job()
        source = self._source(NEW_SEGMENT, 5, b"new replay")

        def fail_after_write(_viewer: object, _dataset_id: str, output: Path, **_kwargs: object) -> object:
            output.write_bytes(b"sensitive derived HUD state")
            raise RecorderError("synthetic HUD authoring failure")

        with (
            mock.patch(
                "minerec.render.control.rpc.resolve_replay_segments",
                return_value=[source],
            ),
            mock.patch(
                "minerec.render.control.rpc.create_structured_hud_sidecar",
                side_effect=fail_after_write,
            ),
        ):
            response = self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )

        self.assertEqual("claim_failed", response["reason"])
        attempts = list((self.runtime / "render-rpc" / "attempts").iterdir())
        self.assertEqual(1, len(attempts))
        self.assertFalse((attempts[0] / "hud" / "structured-hud.jsonl").exists())
        self.assertTrue((attempts[0] / "sources").is_dir())

    def test_worker_failure_removes_hud_bytes_but_retains_plan_envelopes(self) -> None:
        self.job = self._gui_job()
        payload = self.job["payload"]
        source = self._source(NEW_SEGMENT, 5, b"new replay")

        def create_sidecar(_viewer: object, _dataset_id: str, output: Path, **_kwargs: object) -> object:
            contents = b"verified HUD bytes"
            output.write_bytes(contents)
            envelope = {
                "schema_version": 1,
                "sidecar_type": "mc-recorder-structured-hud-v1",
                "format": "jsonl",
                "sha256": hashlib.sha256(contents).hexdigest(),
                "size_bytes": len(contents),
                "record_count": payload["end_tick"] - payload["start_tick"] + 1,  # ty:ignore[not-subscriptable]
                "first_tick": payload["start_tick"],  # ty:ignore[not-subscriptable]
                "last_tick": payload["end_tick"],  # ty:ignore[not-subscriptable]
                "dataset_id": payload["dataset_id"],  # ty:ignore[not-subscriptable]
                "dataset_manifest_sha256": "e" * 64,
                "samples_sha256": "f" * 64,
                "session_id": payload["session_id"],  # ty:ignore[not-subscriptable]
                "player_uuid": payload["player_uuid"],  # ty:ignore[not-subscriptable]
                "connection_id": payload["connection_id"],  # ty:ignore[not-subscriptable]
            }
            return SimpleNamespace(envelope=lambda: envelope)

        with (
            mock.patch(
                "minerec.render.control.rpc.resolve_replay_segments",
                return_value=[source],
            ),
            mock.patch(
                "minerec.render.control.rpc.create_structured_hud_sidecar",
                side_effect=create_sidecar,
            ),
        ):
            claimed = self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )
        attempt = claimed["claim"]["attempt"]
        plan_root = self.runtime / "render-rpc" / "attempts" / attempt["id"]
        sidecar = plan_root / "hud" / "structured-hud.jsonl"
        self.assertTrue(sidecar.is_file())

        self.service.dispatch(
            "fail",
            {
                "worker_id": WORKER_ID,
                "attempt_id": attempt["id"],
                "lease_token": attempt["lease_token"],
                "error": "synthetic worker failure",
            },
        )

        self.assertFalse(sidecar.exists())
        self.assertTrue((plan_root / "plan.json").is_file())

    def test_claim_pins_exact_sources_newest_first_in_owned_atomic_plan(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        result = self._claim([old, new])

        self.assertEqual([NEW_SEGMENT, OLD_SEGMENT], [s["segment_id"] for s in result["sources"]])  # ty:ignore[not-iterable]
        claim = result["claim"]
        attempt = claim["attempt"]  # ty:ignore[not-subscriptable]
        plan_root = (self.runtime / "render-rpc" / "attempts" / attempt["id"]).resolve()
        plan = json.loads((plan_root / "plan.json").read_text())
        self.assertEqual("mc-recorder-remote-render-plan-v1", plan["plan_type"])
        self.assertNotIn(attempt["lease_token"], (plan_root / "plan.json").read_text())
        self.assertEqual(
            hashlib.sha256(attempt["lease_token"].encode()).hexdigest(),
            plan["lease_token_sha256"],
        )
        for response_source, authoritative in zip(result["sources"], [new, old]):  # ty:ignore[invalid-argument-type, not-iterable]
            pinned = Path(response_source["path"])
            self.assertTrue(pinned.is_relative_to(plan_root))
            self.assertNotEqual(authoritative.path, pinned)
            self.assertNotEqual(authoritative.path.stat().st_ino, pinned.stat().st_ino)
            self.assertEqual(authoritative.sha256, _digest(pinned)[0])
        self.assertEqual(0o600, (plan_root / "plan.json").stat().st_mode & 0o777)

    def test_claim_prepares_from_archive_metadata_without_a_control_ledger(self) -> None:
        replay = self.replays / "saved" / "segment.zip"
        _write_archive(replay)
        self.assertFalse((self.runtime / "control").exists())

        result = self.service.dispatch(
            "claim",
            {
                "worker_id": WORKER_ID,
                "job_id": self.job["id"],
                "lease_seconds": 120,
            },
        )

        self.assertEqual([NEW_SEGMENT], [source["segment_id"] for source in result["sources"]])
        self.assertNotEqual(replay, Path(result["sources"][0]["path"]))
        pinned = Path(result["sources"][0]["path"])
        self.assertNotEqual(replay.stat().st_ino, pinned.stat().st_ino)
        self.assertEqual(replay.read_bytes(), pinned.read_bytes())

    def test_claim_does_not_choose_a_saved_archive_for_another_connection(self) -> None:
        _write_archive(
            self.replays / "other-connection.zip",
            connection_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        )

        result = self.service.dispatch(
            "claim",
            {
                "worker_id": WORKER_ID,
                "job_id": self.job["id"],
                "lease_seconds": 120,
            },
        )

        self.assertEqual("claim_failed", result["reason"])
        self.assertIn("no exact saved replay", result["error"])
        self.assertEqual([], result["sources"])

    def test_claim_defers_while_server_replay_is_still_saving(self) -> None:
        with mock.patch(
            "minerec.render.control.rpc.resolve_replay_segments",
            side_effect=ReplayNotReadyError("the exact replay segment is still being saved"),
        ):
            result = self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )

        self.assertIsNone(result["claim"])
        self.assertEqual("replay_pending", result["reason"])
        self.assertEqual(self.job["id"], result["pending_job"]["id"])
        self.assertEqual(30, result["deferred_job_cooldown_seconds"])
        queued = self.service.queue.get(self.job["id"])  # ty:ignore[invalid-argument-type]
        self.assertEqual("queued", queued["state"])
        self.assertEqual(1, queued["attempt_count"])
        self.assertIsNone(queued["active_attempt"])

    def test_replay_pending_oldest_job_does_not_block_later_ready_job(self) -> None:
        created = time.time()
        pending_jobs = [self.job]
        for offset, width in enumerate((700, 720), 1):
            payload = dict(self.job["payload"])  # ty:ignore[no-matching-overload]
            payload["render"] = {**payload["render"], "width": width}
            with mock.patch("minerec.render.control.queue.time.time", return_value=created + offset):
                pending_jobs.append(self.service.queue.create(payload))
        later_payload = dict(self.job["payload"])  # ty:ignore[no-matching-overload]
        later_payload["render"] = {**later_payload["render"], "width": 800}
        with mock.patch("minerec.render.control.queue.time.time", return_value=created + 3):
            later = self.service.queue.create(later_payload)
        source = self._source(NEW_SEGMENT, 5, b"new replay")

        with mock.patch(
            "minerec.render.control.rpc.resolve_replay_segments",
            side_effect=[
                ReplayNotReadyError("the exact replay segment is still being saved"),
                ReplayNotReadyError("the exact replay segment is still being saved"),
                ReplayNotReadyError("the exact replay segment is still being saved"),
                [source],
            ],
        ):
            pending = [
                self.service.dispatch(
                    "claim",
                    {"worker_id": WORKER_ID, "lease_seconds": 120},
                )
                for _job in pending_jobs
            ]
            claimed = self.service.dispatch(
                "claim",
                {"worker_id": WORKER_ID, "lease_seconds": 120},
            )

        self.assertEqual(
            [job["id"] for job in pending_jobs],
            [response["pending_job"]["id"] for response in pending],
        )
        self.assertTrue(all(response["reason"] == "replay_pending" for response in pending))
        self.assertTrue(all(response["deferred_job_cooldown_seconds"] == 30 for response in pending))
        self.assertEqual(later["id"], claimed["claim"]["job"]["id"])

    def test_old_worker_cannot_claim_gui_mode_bound_requests(self) -> None:
        old_worker = "99999999-9999-4999-8999-999999999999"
        self.service.dispatch(
            "register",
            {"worker_id": old_worker, "name": "old-worker", "capabilities": {}},
        )

        with self.assertRaisesRegex(RecorderError, "too old for GUI-mode-bound requests"):
            self.service.dispatch(
                "claim",
                {
                    "worker_id": old_worker,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )

        self.assertEqual("queued", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]

    def test_worker_must_advertise_matching_full_client_presentation_contract(self) -> None:
        incompatible_worker = "88888888-8888-4888-8888-888888888888"
        self.service.dispatch(
            "register",
            {
                "worker_id": incompatible_worker,
                "name": "pre-spectate-worker",
                "capabilities": {"portable_request_no_gui": True},
            },
        )

        with self.assertRaisesRegex(RecorderError, "required full-client presentation contract"):
            self.service.dispatch(
                "claim",
                {
                    "worker_id": incompatible_worker,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )

        self.assertEqual("queued", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]

    def test_old_worker_receives_nonzero_error_for_a_failed_claim_plan(self) -> None:
        self.service.dispatch(
            "register",
            {
                "worker_id": WORKER_ID,
                "name": "pre-persistent-worker",
                "capabilities": {
                    "portable_request_no_gui": True,
                    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY: (FULL_CLIENT_PRESENTATION_CONTRACT),
                },
            },
        )

        with (
            mock.patch.object(
                self.service,
                "_create_plan",
                side_effect=RecorderError("plan preparation broke"),
            ),
            self.assertRaisesRegex(RecorderError, "failed claimed render job.*plan preparation broke"),
        ):
            self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": self.job["id"],
                    "lease_seconds": 120,
                },
            )

        self.assertEqual("failed", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]

    def test_process_presence_heartbeat_keeps_a_registered_worker_online(self) -> None:
        result = self.service.dispatch(
            "worker-heartbeat",
            {"worker_id": WORKER_ID},
        )

        self.assertEqual(WORKER_ID, result["worker"]["id"])
        self.assertEqual("online", result["worker"]["state"])
        workers = self.service.queue.workers()
        self.assertEqual([WORKER_ID], [worker["id"] for worker in workers])
        self.assertEqual("online", workers[0]["state"])
        self.assertLessEqual(workers[0]["heartbeat_age_seconds"], 1.0)
        with self.assertRaisesRegex(RecorderError, "unsupported fields: job_id"):
            self.service.dispatch(
                "worker-heartbeat",
                {"worker_id": WORKER_ID, "job_id": self.job["id"]},
            )

    def test_registration_advertises_the_required_presentation_contract(self) -> None:
        result = self.service.dispatch(
            "register",
            {
                "worker_id": "77777777-7777-4777-8777-777777777777",
                "name": "contract-probe",
                "capabilities": {},
            },
        )

        self.assertEqual(
            FULL_CLIENT_PRESENTATION_CONTRACT,
            result["server_capabilities"][FULL_CLIENT_PRESENTATION_CAPABILITY_KEY],
        )

    def test_claim_validates_optional_dataset_selection_bounds(self) -> None:
        base = dict(self.job["payload"])  # ty:ignore[no-matching-overload]
        cases = (
            (
                {**base, "selection_start_tick": 9},
                "selection tick bounds must be supplied together",
            ),
            (
                {
                    **base,
                    "selection_start_tick": 11,
                    "selection_end_tick": 41,
                },
                "outside its dataset selection",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                job = self.service.queue.create(payload)
                with mock.patch("minerec.render.control.rpc.resolve_replay_segments", return_value=[]):
                    result = self.service.dispatch(
                        "claim",
                        {
                            "worker_id": WORKER_ID,
                            "job_id": job["id"],
                            "lease_seconds": 120,
                        },
                    )
                self.assertEqual("claim_failed", result["reason"])
                self.assertIn(message, result["error"])
                self.assertEqual(job["id"], result["failed_job"]["id"])

    def test_requests_are_idempotent_newest_first_and_bound_older_range(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([old, new])

        newest, newest_calls = self._request(claimed, NEW_SEGMENT)
        self.assertFalse(newest["done"])
        self.assertEqual("intersection", newest_calls[0]["range_policy"])
        self.assertTrue(newest_calls[0]["no_gui"])
        self.assertEqual((10, 40), (newest_calls[0]["first_tick"], newest_calls[0]["last_tick"]))
        self.assertEqual((10, 40), newest_calls[0]["observed_connection_range"])

        older, older_calls = self._request(claimed, OLD_SEGMENT, newer_cutoff=25)
        self.assertTrue(older["done"])
        self.assertEqual(24, older_calls[0]["last_tick"])
        self.assertEqual(25, older_calls[0]["newer_cutoff"])
        repeated, repeated_calls = self._request(claimed, OLD_SEGMENT, newer_cutoff=25)
        self.assertEqual(older["request"], repeated["request"])
        self.assertEqual(older["request_sha256"], repeated["request_sha256"])
        self.assertEqual(older_calls[0]["request_id"], repeated_calls[0]["request_id"])

    def test_render_gui_mode_is_strict_and_propagates_to_portable_requests(self) -> None:
        base = dict(self.job["payload"])  # ty:ignore[no-matching-overload]
        for invalid in (0, "false", None):
            with self.subTest(invalid=invalid):
                payload = {
                    **base,
                    "render": {**base["render"], "no_gui": invalid},
                }
                job = self.service.queue.create(payload)
                result = self.service.dispatch(
                    "claim",
                    {
                        "worker_id": WORKER_ID,
                        "job_id": job["id"],
                        "lease_seconds": 120,
                    },
                )
                self.assertEqual("claim_failed", result["reason"])
                self.assertIn("no_gui must be a boolean", result["error"])

        self.job = self.service.queue.create(
            {
                **base,
                "render": {
                    **base["render"],
                    "no_gui": False,
                    "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
                },
            }
        )
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        _request, calls = self._request(claimed, NEW_SEGMENT)

        self.assertFalse(calls[0]["no_gui"])
        self.assertEqual(
            FULL_CLIENT_PRESENTATION_CONTRACT,
            calls[0]["presentation_contract"],
        )
        self.assertEqual("mc-recorder-structured-hud-v1", calls[0]["structured_hud"]["sidecar_type"])  # ty:ignore[not-subscriptable]
        self.assertEqual(self.job["payload"]["dataset_id"], calls[0]["structured_hud"]["dataset_id"])  # ty:ignore[not-subscriptable]
        self.assertNotIn("path", calls[0]["structured_hud"])  # ty:ignore[invalid-argument-type]

    def test_wider_dataset_selection_authors_only_the_renderable_sample_range(self) -> None:
        payload = {
            **self.job["payload"],  # ty:ignore[invalid-argument-type]
            "selection_start_tick": 9,
            "selection_end_tick": 41,
        }
        job = self.service.queue.create(payload)
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        with mock.patch("minerec.render.control.rpc.resolve_replay_segments", return_value=[source]):
            claimed = self.service.dispatch(
                "claim",
                {
                    "worker_id": WORKER_ID,
                    "job_id": job["id"],
                    "lease_seconds": 120,
                },
            )

        _request, calls = self._request(claimed, NEW_SEGMENT)

        self.assertEqual((10, 40), (calls[0]["first_tick"], calls[0]["last_tick"]))

    def test_request_rejects_out_of_order_conflicts_and_path_fields(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([old, new])
        claim = claimed["claim"]
        attempt = claim["attempt"]  # ty:ignore[not-subscriptable]
        base = {
            "worker_id": WORKER_ID,
            "attempt_id": attempt["id"],
            "lease_token": attempt["lease_token"],
            "segment_id": OLD_SEGMENT,
            "newer_cutoff": 25,
        }
        with self.assertRaisesRegex(RecorderError, "newest-to-oldest"):
            self.service.dispatch("request", base)
        with self.assertRaisesRegex(RecorderError, "unsupported fields"):
            self.service.dispatch("request", {**base, "replay_path": "../../etc/passwd"})
        with self.assertRaisesRegex(RecorderError, "invalid render attempt ID"):
            self.service.dispatch("request", {**base, "attempt_id": "../attempt"})

    def test_tampered_pinned_source_is_rejected_before_request_authoring(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        pinned = Path(claimed["sources"][0]["path"])  # ty:ignore[not-subscriptable]
        pinned.unlink()
        pinned.write_bytes(b"tampered")
        claim = claimed["claim"]
        attempt = claim["attempt"]  # ty:ignore[not-subscriptable]
        with (
            mock.patch("minerec.render.control.rpc.create_portable_render_request") as create,
            self.assertRaisesRegex(RecorderError, "integrity envelope"),
        ):
            self.service.dispatch(
                "request",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": attempt["lease_token"],
                    "segment_id": NEW_SEGMENT,
                },
            )
        create.assert_not_called()

    def test_expired_attempt_is_fenced_even_with_its_original_token(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source], lease_seconds=10)
        attempt = claimed["claim"]["attempt"]  # ty:ignore[not-subscriptable]
        future = time.time() + 20
        with (
            mock.patch("minerec.render.control.queue.time.time", return_value=future),
            self.assertRaisesRegex(RecorderError, "no longer active"),
        ):
            self.service.dispatch(
                "request",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": attempt["lease_token"],
                    "segment_id": NEW_SEGMENT,
                },
            )
        self.assertEqual("queued", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]

    def test_finalize_marks_uploaded_before_import_and_persists_durable_result(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        request_result, _calls = self._request(claimed, NEW_SEGMENT)
        claim = claimed["claim"]
        attempt = claim["attempt"]  # ty:ignore[not-subscriptable]
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT  # ty:ignore[invalid-argument-type]
        upload.mkdir()
        calls: list[tuple[Path, Path, Path, Path]] = []

        def fake_import(request_path: Path, bundle: Path, replay: Path, destination: Path) -> ImportedRenderResult:
            self.assertEqual("verifying", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]
            self.assertEqual(upload, bundle)
            self.assertEqual(
                (self.exports / "render-jobs" / self.job["id"] / NEW_SEGMENT).resolve(),  # ty:ignore[unsupported-operator]
                destination,
            )
            destination.mkdir(parents=True, exist_ok=True)
            result = destination / "result.json"
            manifest = destination / "import-manifest.json"
            result.write_text("{}\n")
            manifest.write_text("{}\n")
            calls.append((request_path, bundle, replay, destination))
            return ImportedRenderResult(
                directory=destination,
                result=result,
                manifest=manifest,
                request_id=request_result["request"]["request_id"],  # ty:ignore[not-subscriptable]
                status="complete",
                reused=len(calls) > 1,
            )

        body = {
            "worker_id": WORKER_ID,
            "attempt_id": attempt["id"],
            "lease_token": attempt["lease_token"],
        }
        with mock.patch("minerec.render.control.rpc.import_render_bundle", fake_import):
            finalized = self.service.dispatch("finalize", body)
            repeated = self.service.dispatch("finalize", body)
        self.assertEqual("verifying", finalized["job"]["state"])
        self.assertEqual(finalized["attachment_directories"], repeated["attachment_directories"])
        imported = finalized["imports"][0]
        self.assertEqual(NEW_SEGMENT, imported["segment_id"])
        self.assertEqual(str(calls[0][3]), imported["directory"])
        self.assertEqual(str(calls[0][3] / "result.json"), imported["result"])
        self.assertTrue((calls[0][3] / "result.json").is_file())

    def test_symlinked_upload_is_rejected_without_consuming_lease(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        self._request(claimed, NEW_SEGMENT)
        attempt = claimed["claim"]["attempt"]  # ty:ignore[not-subscriptable]
        outside = Path(self.temporary.name) / "outside-upload"
        outside.mkdir()
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT  # ty:ignore[invalid-argument-type]
        upload.symlink_to(outside, target_is_directory=True)
        with (
            mock.patch("minerec.render.control.rpc.import_render_bundle") as importer,
            self.assertRaisesRegex(RecorderError, "symlink|missing"),
        ):
            self.service.dispatch(
                "finalize",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": attempt["lease_token"],
                },
            )
        importer.assert_not_called()
        self.assertIn(self.service.queue.get(self.job["id"])["state"], {"downloading", "rendering"})  # ty:ignore[invalid-argument-type]

    def test_symlinked_durable_job_destination_fails_verification_job(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        self._request(claimed, NEW_SEGMENT)
        attempt = claimed["claim"]["attempt"]  # ty:ignore[not-subscriptable]
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT  # ty:ignore[invalid-argument-type]
        upload.mkdir()
        outside = Path(self.temporary.name) / "outside-import"
        outside.mkdir()
        job_import = self.exports / "render-jobs" / self.job["id"]  # ty:ignore[unsupported-operator]
        job_import.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RecorderError, "symlink|escapes"):
            self.service.dispatch(
                "finalize",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": attempt["lease_token"],
                },
            )
        self.assertEqual("failed", self.service.queue.get(self.job["id"])["state"])  # ty:ignore[invalid-argument-type]
        self.assertEqual([], list(outside.iterdir()))

    def test_failure_and_progress_bodies_are_bounded_and_lease_owned(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        attempt = claimed["claim"]["attempt"]  # ty:ignore[not-subscriptable]
        with self.assertRaisesRegex(RecorderError, "owned"):
            self.service.dispatch(
                "heartbeat",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": "x" * 32,
                    "phase": "rendering",
                },
            )
        heartbeat = self.service.dispatch(
            "heartbeat",
            {
                "worker_id": WORKER_ID,
                "attempt_id": attempt["id"],
                "lease_token": attempt["lease_token"],
                "phase": "rendering",
                "current": 3,
                "total": 10,
            },
        )
        self.assertEqual("rendering", heartbeat["job"]["state"])
        with self.assertRaisesRegex(RecorderError, "1..2048"):
            self.service.dispatch(
                "fail",
                {
                    "worker_id": WORKER_ID,
                    "attempt_id": attempt["id"],
                    "lease_token": attempt["lease_token"],
                    "error": "e" * 2049,
                },
            )
        with self.assertRaisesRegex(RecorderError, "size limit"):
            self.service.dispatch(
                "register",
                {
                    "worker_id": WORKER_ID,
                    "name": "oversized",
                    "capabilities": {"value": "x" * (70 * 1024)},
                },
            )


if __name__ == "__main__":
    unittest.main()
