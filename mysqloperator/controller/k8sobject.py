# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/

from typing import Optional

import datetime
import logging
from .kubeutils import api_core, ApiException, is_ignorable_event_post_error

g_component = None
g_host = None
logger = logging.getLogger(__name__)


def post_event(namespace: str, object_ref: dict, type: str, action: str,
               reason: str, message: str) -> None:
    if len(message) > 1024:
        message = message[:1024]

    body = {
        # What action was taken/failed regarding to the regarding object.
        'action': action,

        'eventTime': datetime.datetime.now().isoformat()+"Z",

        'involvedObject': object_ref,

        'message': message,
        'metadata': {
            'namespace': namespace,
            'generateName': 'mysqloperator-evt-',
        },

        # This should be a short, machine understandable string that gives the
        # reason for the transition into the object's current status.
        'reason': reason,

        'reportingComponent': f'mysql.oracle.com/mysqloperator-{g_component}',
        'reportingInstance': f'{g_host}',

        'source': {
            'component': g_component,
            'host': g_host
        },

        'type': type
    }
    try:
        api_core.create_namespaced_event(namespace, body)
    except ApiException as exc:
        if not is_ignorable_event_post_error(exc):
            raise
        logger.warning("Skipping event post for %s: %s", namespace, exc)


class K8sInterfaceObject:
    """
    Base class for objects meant to interface with Kubernetes.
    """

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        raise NotImplemented()

    @property
    def namespace(self) -> str:
        raise NotImplemented()

    def self_ref(self, field: Optional[str] = None) -> dict:
        raise NotImplemented()

    # ## Event Posting ##
    # Explicit events should only be used for high-level messages. Debugging or
    # low-level messages should go through the logging system.
    def info(self, *, action: str, reason: str, message: str,
             field: Optional[str] = None) -> None:
        post_event(self.namespace, self.self_ref(field), type="Normal",
                   action=action, reason=reason, message=message)

    def warn(self, *, action: str, reason: str, message: str,
             field: Optional[str] = None) -> None:
        post_event(self.namespace, self.self_ref(field), type="Warning",
                   action=action, reason=reason, message=message)

    def error(self, *, action: str, reason: str, message: str,
              field: Optional[str] = None) -> None:
        post_event(self.namespace, self.self_ref(field), type="Error",
                   action=action, reason=reason, message=message)
