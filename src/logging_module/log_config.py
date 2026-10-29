"""Logs of the application on the standard output, and errors sent to Sentry when SENTRY_DSN is set.

Usage: from logging_module.log_config import logger
"""

from __future__ import annotations

import logging
import os
import sys

import sentry_sdk
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

LOG_LEVEL = os.getenv("LOG_LEVEL") or "INFO"
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT") or "development"

# Never sent to Sentry: the visitors' GitLab tokens and access codes.
PRIVATE_FIELDS = ["x-gitlab-token", "x_gitlab_token", "gitlab_token", "access_code", "api_token"]

logging.basicConfig(
    level=LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)s %(module)s:%(lineno)d %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("pipefix")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        event_scrubber=EventScrubber(denylist=DEFAULT_DENYLIST + PRIVATE_FIELDS),
    )
