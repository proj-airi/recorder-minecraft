from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.render_rpc import RenderRpcService
from mc_recorder.render_sources import ReplaySegmentSource
from mc_recorder.render_transfer import ImportedRenderResult


WORKER_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CONNECTION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SESSION_ID = "20260721T000000.000Z-deadbeef"
NEW_SEGMENT = "22222222-2222-4222-8222-222222222222"
OLD_SEGMENT = "33333333-3333-4333-8333-333333333333"


def _digest(path: Path) -> tuple[str, int]:
    value = path.read_bytes()
    return hashlib.sha256(value).hexdigest(), len(value)


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
        self.service = RenderRpcService(self.config)
        self.service.dispatch(
            "register",
            {"worker_id": WORKER_ID, "name": "ephemeral-test", "capabilities": {}},
        )
        self.job = self.service.queue.create(
            {
                "recording_id": "a" * 24,
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

    def _claim(
        self, sources: list[ReplaySegmentSource], *, lease_seconds: int = 120
    ) -> dict[str, object]:
        with mock.patch(
            "mc_recorder.render_rpc.resolve_replay_segments", return_value=sources
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
    def _portable_mocks():
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
            encoded = (
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            if path.exists() and path.read_bytes() != encoded:
                raise RecorderError("portable request conflict")
            path.write_bytes(encoded)
            return SimpleNamespace(
                data=value,
                sha256=hashlib.sha256(encoded).hexdigest(),
                request_id=value["request_id"],
            )

        return created, create, write

    def _request(
        self,
        claimed: dict[str, object],
        segment_id: str,
        *,
        newer_cutoff: int | None = None,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        claim = claimed["claim"]
        assert isinstance(claim, dict)
        attempt = claim["attempt"]
        assert isinstance(attempt, dict)
        body: dict[str, object] = {
            "worker_id": WORKER_ID,
            "attempt_id": attempt["id"],
            "lease_token": attempt["lease_token"],
            "segment_id": segment_id,
        }
        if newer_cutoff is not None:
            body["newer_cutoff"] = newer_cutoff
        created, create, write = self._portable_mocks()
        with (
            mock.patch("mc_recorder.render_rpc.create_portable_render_request", create),
            mock.patch("mc_recorder.render_rpc.write_portable_render_request", write),
        ):
            result = self.service.dispatch("request", body)
        return result, created

    def test_claim_pins_exact_sources_newest_first_in_owned_atomic_plan(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        result = self._claim([old, new])

        self.assertEqual([NEW_SEGMENT, OLD_SEGMENT], [s["segment_id"] for s in result["sources"]])
        claim = result["claim"]
        attempt = claim["attempt"]
        plan_root = (self.runtime / "render-rpc" / "attempts" / attempt["id"]).resolve()
        plan = json.loads((plan_root / "plan.json").read_text())
        self.assertEqual("mc-recorder-remote-render-plan-v1", plan["plan_type"])
        self.assertNotIn(attempt["lease_token"], (plan_root / "plan.json").read_text())
        self.assertEqual(
            hashlib.sha256(attempt["lease_token"].encode()).hexdigest(),
            plan["lease_token_sha256"],
        )
        for response_source, authoritative in zip(result["sources"], [new, old]):
            pinned = Path(response_source["path"])
            self.assertTrue(pinned.is_relative_to(plan_root))
            self.assertNotEqual(authoritative.path, pinned)
            self.assertEqual(authoritative.path.stat().st_ino, pinned.stat().st_ino)
            self.assertEqual(authoritative.sha256, _digest(pinned)[0])
        self.assertEqual(0o600, (plan_root / "plan.json").stat().st_mode & 0o777)

    def test_requests_are_idempotent_newest_first_and_bound_older_range(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([old, new])

        newest, newest_calls = self._request(claimed, NEW_SEGMENT)
        self.assertFalse(newest["done"])
        self.assertEqual("intersection", newest_calls[0]["range_policy"])
        self.assertEqual((10, 40), (newest_calls[0]["first_tick"], newest_calls[0]["last_tick"]))

        older, older_calls = self._request(claimed, OLD_SEGMENT, newer_cutoff=25)
        self.assertTrue(older["done"])
        self.assertEqual(24, older_calls[0]["last_tick"])
        self.assertEqual(25, older_calls[0]["newer_cutoff"])
        repeated, repeated_calls = self._request(claimed, OLD_SEGMENT, newer_cutoff=25)
        self.assertEqual(older["request"], repeated["request"])
        self.assertEqual(older["request_sha256"], repeated["request_sha256"])
        self.assertEqual(older_calls[0]["request_id"], repeated_calls[0]["request_id"])

    def test_request_rejects_out_of_order_conflicts_and_path_fields(self) -> None:
        old = self._source(OLD_SEGMENT, 2, b"old replay")
        new = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([old, new])
        claim = claimed["claim"]
        attempt = claim["attempt"]
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
        pinned = Path(claimed["sources"][0]["path"])
        pinned.unlink()
        pinned.write_bytes(b"tampered")
        claim = claimed["claim"]
        attempt = claim["attempt"]
        with (
            mock.patch("mc_recorder.render_rpc.create_portable_render_request") as create,
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
        attempt = claimed["claim"]["attempt"]
        future = time.time() + 20
        with (
            mock.patch("mc_recorder.render_queue.time.time", return_value=future),
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
        self.assertEqual("queued", self.service.queue.get(self.job["id"])["state"])

    def test_finalize_marks_uploaded_before_import_and_persists_durable_result(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        request_result, _calls = self._request(claimed, NEW_SEGMENT)
        claim = claimed["claim"]
        attempt = claim["attempt"]
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT
        upload.mkdir()
        calls: list[tuple[Path, Path, Path, Path]] = []

        def fake_import(
            request_path: Path, bundle: Path, replay: Path, destination: Path
        ) -> ImportedRenderResult:
            self.assertEqual("verifying", self.service.queue.get(self.job["id"])["state"])
            self.assertEqual(upload, bundle)
            self.assertEqual(
                (self.exports / "render-jobs" / self.job["id"] / NEW_SEGMENT).resolve(),
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
                request_id=request_result["request"]["request_id"],
                status="complete",
                reused=len(calls) > 1,
            )

        body = {
            "worker_id": WORKER_ID,
            "attempt_id": attempt["id"],
            "lease_token": attempt["lease_token"],
        }
        with mock.patch("mc_recorder.render_rpc.import_render_bundle", fake_import):
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
        attempt = claimed["claim"]["attempt"]
        outside = Path(self.temporary.name) / "outside-upload"
        outside.mkdir()
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT
        upload.symlink_to(outside, target_is_directory=True)
        with (
            mock.patch("mc_recorder.render_rpc.import_render_bundle") as importer,
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
        self.assertIn(self.service.queue.get(self.job["id"])["state"], {"downloading", "rendering"})

    def test_symlinked_durable_job_destination_fails_verification_job(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        self._request(claimed, NEW_SEGMENT)
        attempt = claimed["claim"]["attempt"]
        upload = Path(claimed["upload_directory"]) / NEW_SEGMENT
        upload.mkdir()
        outside = Path(self.temporary.name) / "outside-import"
        outside.mkdir()
        job_import = self.exports / "render-jobs" / self.job["id"]
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
        self.assertEqual("failed", self.service.queue.get(self.job["id"])["state"])
        self.assertEqual([], list(outside.iterdir()))

    def test_failure_and_progress_bodies_are_bounded_and_lease_owned(self) -> None:
        source = self._source(NEW_SEGMENT, 5, b"new replay")
        claimed = self._claim([source])
        attempt = claimed["claim"]["attempt"]
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
