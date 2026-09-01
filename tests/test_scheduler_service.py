import pytest

from services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    service = SchedulerService()
    yield service
    service.stop()
    service.clear_all_schedules()


def test_add_and_remove_schedule(scheduler_service):
    processed = []
    job_id = scheduler_service.add_schedule("nightly.txt", "14:30", processed.append)

    job = scheduler_service.scheduled_jobs[job_id]
    job.func(*job.args)

    assert processed == ["nightly.txt"]
    assert scheduler_service.get_job_id_by_file("nightly.txt") == job_id
    assert scheduler_service.get_all_job_ids() == [job_id]
    assert scheduler_service.has_schedules()

    assert scheduler_service.remove_schedule(job_id)
    assert not scheduler_service.remove_schedule(job_id)
    assert scheduler_service.get_job_id_by_file("nightly.txt") is None
    assert not scheduler_service.has_schedules()


def test_clear_all_schedules(scheduler_service):
    scheduler_service.add_schedule("first.txt", "10:00", lambda _: None)
    scheduler_service.add_schedule("second.txt", "11:00", lambda _: None)

    scheduler_service.clear_all_schedules()

    assert scheduler_service.get_all_job_ids() == []
    assert not scheduler_service.has_schedules()


def test_start_is_idempotent(scheduler_service):
    assert scheduler_service.start()
    assert not scheduler_service.start()
