from unittest.mock import MagicMock, patch

from app.models.webhook import WebhookDeliveryStatus


def _make_delivery(status=WebhookDeliveryStatus.PENDING, attempt_count=0):
    delivery = MagicMock()
    delivery.id = "11111111-1111-1111-1111-111111111111"
    delivery.status = status
    delivery.attempt_count = attempt_count
    delivery.webhook = MagicMock()
    delivery.webhook.id = "22222222-2222-2222-2222-222222222222"
    delivery.webhook.url = "https://example.com/hook"
    delivery.webhook.max_retries = 3
    delivery.webhook.secret = None
    delivery.event = MagicMock()
    delivery.event.value = "sla.violation"
    delivery.signature_version = 1
    delivery.payload = "{}"
    delivery.next_retry_at = None
    delivery.updated_at = None
    delivery.delivered_at = None
    return delivery


def _make_webhook():
    webhook = MagicMock()
    webhook.id = "22222222-2222-2222-2222-222222222222"
    webhook.url = "https://example.com/hook"
    webhook.is_active = True
    webhook.events = '["sla.violation"]'
    webhook.max_retries = 3
    webhook.secret = None
    return webhook


def _make_celery_mock(eager: bool):
    mock_celery = MagicMock()
    mock_celery.conf.task_always_eager = eager
    return mock_celery


class TestTriggerSlaViolationAsyncDispatch:
    @patch("app.tasks.webhook_tasks.dispatch_webhook_delivery.delay")
    def test_celery_dispatches_via_task_when_broker_configured(self, mock_delay):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [_make_webhook()]
        delivery = _make_delivery()
        with patch("app.services.webhook_service.create_delivery", return_value=delivery), patch(
            "app.tasks.celery_app.celery_app",
            _make_celery_mock(eager=False),
        ):
            result = trigger_sla_violation_webhooks(mock_db, {"device_id": "d1"})

        assert len(result) == 1
        mock_delay.assert_called_once_with(str(delivery.id))

    @patch("app.services.webhook_service.dispatch_delivery")
    def test_fallback_to_sync_when_eager_mode(self, mock_dispatch):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [_make_webhook()]
        delivery = _make_delivery()
        with patch("app.services.webhook_service.create_delivery", return_value=delivery), patch(
            "app.tasks.celery_app.celery_app",
            _make_celery_mock(eager=True),
        ):
            result = trigger_sla_violation_webhooks(mock_db, {"device_id": "d1"})

        assert len(result) == 1
        mock_dispatch.assert_called_once_with(mock_db, delivery.id)

    @patch("app.services.webhook_service.dispatch_delivery")
    def test_fallback_to_sync_when_celery_import_fails(self, mock_dispatch):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [_make_webhook()]
        delivery = _make_delivery()
        with patch("app.services.webhook_service.create_delivery", return_value=delivery), patch(
            "app.tasks.celery_app.celery_app",
            side_effect=ImportError("no celery"),
        ):
            result = trigger_sla_violation_webhooks(mock_db, {"device_id": "d1"})

        assert len(result) == 1
        mock_dispatch.assert_called_once_with(mock_db, delivery.id)

    def test_no_webhook_no_deliveries(self):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = trigger_sla_violation_webhooks(mock_db, {"device_id": "d1"})
        assert result == []

    @patch("app.tasks.webhook_tasks.dispatch_webhook_delivery.delay")
    def test_multiple_webhooks_each_enqueued(self, mock_delay):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        mock_db = MagicMock()
        hooks = [_make_webhook(), _make_webhook()]
        mock_db.query.return_value.filter.return_value.all.return_value = hooks
        d1 = _make_delivery()
        d1.id = "aaaaaaa1-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        d2 = _make_delivery()
        d2.id = "bbbbbbb1-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        deliveries = [d1, d2]
        with patch("app.services.webhook_service.create_delivery", side_effect=deliveries), patch(
            "app.tasks.celery_app.celery_app",
            _make_celery_mock(eager=False),
        ):
            result = trigger_sla_violation_webhooks(mock_db, {"device_id": "d1"})

        assert len(result) == 2
        assert mock_delay.call_count == 2


class TestDispatchDeliveryIdempotency:
    def test_skips_already_succeeded_delivery(self):
        from app.services.webhook_service import dispatch_delivery

        db = MagicMock()
        delivery = _make_delivery(status=WebhookDeliveryStatus.SUCCESS)
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        db.commit.assert_not_called()

    def test_skips_dead_lettered_delivery(self):
        from app.services.webhook_service import dispatch_delivery

        db = MagicMock()
        delivery = _make_delivery(status=WebhookDeliveryStatus.DEAD_LETTER)
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        db.commit.assert_not_called()

    @patch("app.services.webhook_service._attempt_delivery", return_value=True)
    @patch("app.services.webhook_service.breaker")
    def test_dispatches_pending_delivery(self, mock_breaker, mock_attempt):
        from app.services.webhook_service import dispatch_delivery

        mock_breaker.allow_request.return_value = True
        db = MagicMock()
        delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING)
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        assert delivery.status == WebhookDeliveryStatus.SUCCESS
        mock_attempt.assert_called_once()

    @patch("app.services.webhook_service._attempt_delivery", return_value=True)
    @patch("app.services.webhook_service.breaker")
    def test_dispatches_retrying_delivery(self, mock_breaker, mock_attempt):
        from app.services.webhook_service import dispatch_delivery

        mock_breaker.allow_request.return_value = True
        db = MagicMock()
        delivery = _make_delivery(status=WebhookDeliveryStatus.RETRYING, attempt_count=1)
        db.query.return_value.filter.return_value.first.return_value = delivery

        dispatch_delivery(db, delivery.id)

        assert delivery.status == WebhookDeliveryStatus.SUCCESS
        mock_attempt.assert_called_once()
