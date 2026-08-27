import pytest
from app.services.api_key_store import revoke_key, is_revoked_cached
from app.core.security import get_current_user_or_service


def test_revoked_key_rejected_via_cache(db_session, redis_client, test_api_key):
    """After revoke_key, the cache should be populated and subsequent
    auth attempts should be rejected without hitting the DB."""
    key_id = test_api_key.id

    assert revoke_key(db_session, key_id) is True

    # Cache should now reflect the revocation.
    assert is_revoked_cached(key_id, redis_client) is True

    with pytest.raises(Exception) as exc_info:
        get_current_user_or_service(key_id, db_session, redis_client)
    assert "revoked" in str(exc_info.value).lower()


def test_revoked_key_rejected_on_cache_miss_falls_back_to_db(db_session, redis_client, test_api_key):
    """Even if the cache is empty (miss), a DB-revoked key must still
    be rejected -- a miss must never be treated as 'not revoked'."""
    key_id = test_api_key.id
    revoke_key(db_session, key_id)

    # Simulate a cold/expired cache.
    redis_client.delete(f"api_key:revoked:{key_id}")

    with pytest.raises(Exception) as exc_info:
        get_current_user_or_service(key_id, db_session, redis_client)
    assert "revoked" in str(exc_info.value).lower()


def test_non_revoked_key_still_passes(db_session, redis_client, test_api_key):
    """Existing behavior for valid keys must be unaffected."""
    key_id = test_api_key.id
    result = get_current_user_or_service(key_id, db_session, redis_client)
    assert result is not None