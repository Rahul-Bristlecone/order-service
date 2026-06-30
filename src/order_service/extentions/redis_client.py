import os
import redis

_redis_password = os.getenv("REDIS_PASSWORD") or None  # treat empty string as None

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis-service"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    password=_redis_password,  # None = no AUTH command sent to Redis
    decode_responses=True
)