"""Keep authentication codes and callback credentials out of access logs."""

import logging


class CloudAccessFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn passes client, method, path-with-query, HTTP version and status.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            path = args[2]
            if isinstance(path, str) and "/api/cloud/" in path:
                args[2] = path.split("?", 1)[0]
                record.args = tuple(args)
        return True


def protect_cloud_access_logs():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, CloudAccessFilter) for item in logger.filters):
        logger.addFilter(CloudAccessFilter())
