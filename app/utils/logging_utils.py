import inspect
import logging
from functools import wraps
from typing import Any, AsyncIterator, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


_LOGGING_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_exceptions(logger: logging.Logger | None = None) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        log = logger or get_logger(func.__module__)

        if inspect.isasyncgenfunction(func):
            @wraps(func)
            async def async_gen_wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                except Exception:
                    log.exception("Unhandled exception in %s", func.__qualname__)
                    raise

            return async_gen_wrapper  # type: ignore[return-value]

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    log.exception("Unhandled exception in %s", func.__qualname__)
                    raise

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception:
                log.exception("Unhandled exception in %s", func.__qualname__)
                raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator
