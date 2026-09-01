"""
Service for managing scheduled bulk import jobs.

Extracted from artwork_uploader.py to reduce file size and improve
maintainability.
"""

from typing import Callable, Dict, Optional

from apscheduler.job import Job  # type: ignore
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore


class SchedulerService:
    """Handles scheduling of bulk import jobs."""

    def __init__(self, check_interval: int = 1) -> None:
        """
        Initialize the scheduler service.

        Args:
            check_interval: Retained for backwards-compatible construction.
        """
        self.check_interval = check_interval
        self.scheduler = BackgroundScheduler()
        self.scheduled_jobs: Dict[str, Job] = {}
        self.scheduled_jobs_by_file: Dict[str, str] = {}
        self.run_times_by_file: Dict[str, str] = {}
        self.is_running = False

    def add_schedule(
        self, filename: str, schedule_time: str, callback: Callable[[str], None]
    ) -> str:
        """
        Add a new scheduled job.

        Args:
            filename: Name of the bulk file to process
            schedule_time: Time to run (e.g., "14:30")
            callback: Function to call with filename when job runs

        Returns:
            Unique job ID for this schedule
        """
        hour, minute = (int(part) for part in schedule_time.split(":"))
        job = self.scheduler.add_job(
            callback,
            trigger="cron",
            hour=hour,
            minute=minute,
            args=[filename],
            misfire_grace_time=None,
        )

        self.scheduled_jobs[job.id] = job
        self.scheduled_jobs_by_file[filename] = job.id
        self.run_times_by_file[filename] = schedule_time

        return job.id

    def remove_schedule(self, job_id: str) -> bool:
        """
        Remove a scheduled job.

        Args:
            job_id: Job ID to remove

        Returns:
            True if job was removed, False if not found
        """
        if job_id not in self.scheduled_jobs:
            return False

        self.scheduler.remove_job(job_id)
        del self.scheduled_jobs[job_id]

        file_to_remove = next(
            (
                filename
                for filename, jid in self.scheduled_jobs_by_file.items()
                if jid == job_id
            ),
            None,
        )
        if file_to_remove:
            del self.scheduled_jobs_by_file[file_to_remove]
            del self.run_times_by_file[file_to_remove]

        return True

    def get_job_id_by_file(self, filename: str) -> Optional[str]:
        """
        Get the job ID for a scheduled file.

        Args:
            filename: Filename to look up

        Returns:
            Job ID if found, None otherwise
        """
        return self.scheduled_jobs_by_file.get(filename)

    def start(self) -> bool:
        """
        Start the scheduler thread.

        Returns:
            True if started, False if already running
        """
        if self.scheduler.running:
            return False

        self.scheduler.start()
        self.is_running = True
        return True

    def stop(self) -> None:
        """Stop the scheduler thread."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.is_running = False

    def clear_all_schedules(self) -> None:
        """Clear all scheduled jobs."""
        self.scheduler.remove_all_jobs()
        self.scheduled_jobs.clear()
        self.scheduled_jobs_by_file.clear()
        self.run_times_by_file.clear()

    def get_all_job_ids(self) -> list[str]:
        """
        Get all scheduled job IDs.

        Returns:
            List of job IDs
        """
        return list(self.scheduled_jobs.keys())

    def has_schedules(self) -> bool:
        """
        Check if there are any scheduled jobs.

        Returns:
            True if there are schedules, False otherwise
        """
        return len(self.scheduled_jobs) > 0
