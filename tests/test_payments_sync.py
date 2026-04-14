from __future__ import annotations

import sys
import types
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analytics.metrics import compute_payment_metrics
from backend.batch_matching import normalize_batch_name
from backend.models import Base, PaymentAuditLog, PaymentBatch, PaymentHistory, Task

vision_service_stub = types.ModuleType("backend.vision_service")


class _VisionAnalyzerStub:
    @staticmethod
    def analyze_frame(*args, **kwargs):
        return {
            "detected_boxes": [],
            "suggestions": [],
            "suggestions_count": 0,
            "time_saved_estimate_seconds": 0.0,
            "processed_at": "",
        }


vision_service_stub.VisionAnalyzer = _VisionAnalyzerStub
sys.modules.setdefault("backend.vision_service", vision_service_stub)

from backend.routes import sync_payments_full
from backend.schemas import PaymentBatchRequest, PaymentFullSyncRequest, PaymentHistoryRequest


class PaymentFullSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self) -> None:
        self.db.close()

    def test_inserts_new_batches_and_history(self) -> None:
        result = sync_payments_full(
            PaymentFullSyncRequest(
                batches=[
                    PaymentBatchRequest(batch_name="batch-100-camera-a", amount_usd=12.5),
                ],
                history=[
                    PaymentHistoryRequest(
                        date=date(2026, 4, 14),
                        amount_usd=12.5,
                        amount_kes=1618.0,
                        status="completed",
                    ),
                ],
            ),
            self.db,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.batches.inserted, 1)
        self.assertEqual(result.history.inserted, 1)
        self.assertEqual(self.db.query(PaymentBatch).count(), 1)
        self.assertEqual(self.db.query(PaymentHistory).count(), 1)

    def test_identical_snapshot_only_refreshes_last_seen(self) -> None:
        payload = PaymentFullSyncRequest(
            batches=[PaymentBatchRequest(batch_name="batch-100-camera-a", amount_usd=12.5)],
            history=[
                PaymentHistoryRequest(
                    date=date(2026, 4, 14),
                    amount_usd=12.5,
                    amount_kes=1618.0,
                    status="completed",
                )
            ],
        )

        sync_payments_full(payload, self.db)
        batch_before = self.db.query(PaymentBatch).first()
        history_before = self.db.query(PaymentHistory).first()
        batch_seen_before = batch_before.last_seen_at
        history_seen_before = history_before.last_seen_at

        result = sync_payments_full(payload, self.db)

        batch_after = self.db.query(PaymentBatch).first()
        history_after = self.db.query(PaymentHistory).first()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.batches.unchanged, 1)
        self.assertEqual(result.history.unchanged, 1)
        self.assertGreaterEqual(batch_after.last_seen_at, batch_seen_before)
        self.assertGreaterEqual(history_after.last_seen_at, history_seen_before)
        self.assertEqual(self.db.query(PaymentAuditLog).filter(PaymentAuditLog.action == "updated").count(), 0)

    def test_changed_values_are_updated_and_flagged(self) -> None:
        sync_payments_full(
            PaymentFullSyncRequest(
                batches=[PaymentBatchRequest(batch_name="batch-100-camera-a", amount_usd=12.5)],
                history=[
                    PaymentHistoryRequest(
                        date=date(2026, 4, 14),
                        amount_usd=12.5,
                        amount_kes=1618.0,
                        status="pending",
                    )
                ],
            ),
            self.db,
        )

        result = sync_payments_full(
            PaymentFullSyncRequest(
                batches=[
                    PaymentBatchRequest(batch_name="batch-100-camera-a", amount_usd=19.0),
                    PaymentBatchRequest(batch_name="batch-200-camera-b", amount_usd=8.0),
                ],
                history=[
                    PaymentHistoryRequest(
                        date=date(2026, 4, 14),
                        amount_usd=19.0,
                        amount_kes=2450.0,
                        status="completed",
                    )
                ],
            ),
            self.db,
        )

        updated_batch = self.db.query(PaymentBatch).filter_by(batch_name="batch-100-camera-a").first()
        new_batch = self.db.query(PaymentBatch).filter_by(batch_name="batch-200-camera-b").first()
        updated_history = self.db.query(PaymentHistory).filter_by(date=date(2026, 4, 14)).first()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.batches.updated, 1)
        self.assertEqual(result.batches.inserted, 1)
        self.assertEqual(result.history.updated, 1)
        self.assertEqual(updated_batch.amount_usd, 19.0)
        self.assertEqual(updated_batch.is_updated, 1)
        self.assertEqual(updated_batch.is_flagged, 1)
        self.assertIsNotNone(new_batch)
        self.assertEqual(updated_history.amount_kes, 2450.0)
        self.assertEqual(updated_history.status, "completed")
        self.assertEqual(updated_history.is_updated, 1)
        self.assertEqual(updated_history.is_flagged, 1)
        self.assertEqual(self.db.query(PaymentAuditLog).filter(PaymentAuditLog.action == "updated").count(), 2)

    def test_normalizes_batch_names_and_marks_exact_task_matches_paid(self) -> None:
        task = Task(
            task_uid="task-1",
            dataset="batch_4030_sf_bev_images",
            normalized_batch_name=normalize_batch_name("batch_4030_sf_bev_images"),
            payment_status="unpaid",
            paid_amount_usd=0.0,
        )
        self.db.add(task)
        self.db.commit()

        result = sync_payments_full(
            PaymentFullSyncRequest(
                batches=[PaymentBatchRequest(batch_name="batch-4030-sf-bev", amount_usd=22.0)],
                history=[],
            ),
            self.db,
        )

        refreshed_task = self.db.query(Task).filter_by(task_uid="task-1").first()
        payment_batch = self.db.query(PaymentBatch).first()
        metrics = compute_payment_metrics(self.db)

        self.assertEqual(result.status, "success")
        self.assertEqual(payment_batch.normalized_batch_name, "batch-4030-sf-bev")
        self.assertEqual(refreshed_task.payment_status, "paid")
        self.assertEqual(refreshed_task.paid_amount_usd, 22.0)
        self.assertEqual(metrics["unpaid_batch_count"], 0)
        self.assertEqual(metrics["tracked_batches"][0]["payment_status"], "Paid")

    def test_fuzzy_batch_anchor_match_updates_paid_status(self) -> None:
        task = Task(
            task_uid="task-2",
            dataset="batch_4030_sf_bev_images",
            normalized_batch_name=normalize_batch_name("batch_4030_sf_bev_images"),
            payment_status="unpaid",
            paid_amount_usd=0.0,
        )
        self.db.add(task)
        self.db.commit()

        result = sync_payments_full(
            PaymentFullSyncRequest(
                batches=[PaymentBatchRequest(batch_name="batch-4030", amount_usd=18.0)],
                history=[],
            ),
            self.db,
        )

        refreshed_task = self.db.query(Task).filter_by(task_uid="task-2").first()
        self.assertEqual(result.status, "success")
        self.assertEqual(refreshed_task.payment_status, "paid")
        self.assertEqual(refreshed_task.paid_amount_usd, 18.0)


if __name__ == "__main__":
    unittest.main()
