from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from config.settings import get_redis_settings

settings = get_redis_settings()


result_backend = RedisAsyncResultBackend(
    redis_url=settings.redis_url.get_secret_value(),
    result_ex_time=3600,
)


broker = RedisStreamBroker(
    url=settings.redis_url.get_secret_value()
).with_result_backend(result_backend)
