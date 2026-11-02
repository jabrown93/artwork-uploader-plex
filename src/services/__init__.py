"""
Service layer for business logic.

This package contains service classes that encapsulate business logic
to reduce the size of the monolithic artwork_uploader.py file.
"""

from .arr_service import ArrService
from .artwork_processor import ArtworkProcessor, ProcessingCallbacks
from .authentication_service import AuthenticationService
from .bulk_file_service import BulkFileService
from .image_service import ImageService
from .scheduler_service import SchedulerService
from .utility_service import UtilityService
from .notify_service import NotifyService
from .asset_index import AssetIndex
from .run_history import RunHistory
from .webhook_service import WebhookService  # imports run_history, so it comes after it

__all__ = [
    'ArrService',
    'ArtworkProcessor',
    'ProcessingCallbacks',
    'BulkFileService',
    'ImageService',
    'SchedulerService',
    'UtilityService',
    'AuthenticationService',
    'NotifyService',
    'AssetIndex',
    'WebhookService',
    'RunHistory'
]
