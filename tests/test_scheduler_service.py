import pytest

from services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    service = SchedulerService()
    yield service
    service.clear_all_schedules()
    if service.scheduler.running:
        service.scheduler.shutdown(wait=False)


def test_add_and_remove_schedule(scheduler_service):
    processed = []
    job_id = scheduler_service.add_schedule("nightly.txt", "14:30", processed.append)

    job = scheduler_service.scheduled_jobs[job_id]
    job.func(*job.args)

    assert processed == ["nightly.txt"]
    assert scheduler_service.get_job_id_by_file("nightly.txt") == job_id
    assert scheduler_service.get_all_job_ids() == [job_id]
    assert scheduler_service.run_times_by_file == {"nightly.txt": "14:30"}
    assert job.misfire_grace_time is None
    assert scheduler_service.has_schedules()

    assert scheduler_service.remove_schedule(job_id)
    assert not scheduler_service.remove_schedule(job_id)
    assert scheduler_service.get_job_id_by_file("nightly.txt") is None
    assert "nightly.txt" not in scheduler_service.run_times_by_file
    assert not scheduler_service.has_schedules()


def test_add_schedule_accepts_seconds(scheduler_service):
    job_id = scheduler_service.add_schedule(
        "precise.txt", "14:30:15", lambda _: None
    )
    fields = {
        field.name: str(field)
        for field in scheduler_service.scheduled_jobs[job_id].trigger.fields
    }

    assert fields["hour"] == "14"
    assert fields["minute"] == "30"
    assert fields["second"] == "15"


def test_clear_all_schedules(scheduler_service):
    scheduler_service.add_schedule("first.txt", "10:00", lambda _: None)
    scheduler_service.add_schedule("second.txt", "11:00", lambda _: None)

    scheduler_service.clear_all_schedules()

    assert scheduler_service.get_all_job_ids() == []
    assert scheduler_service.run_times_by_file == {}
    assert not scheduler_service.has_schedules()


def test_start_is_idempotent(scheduler_service):
    assert scheduler_service.start()
    assert not scheduler_service.start()


def test_stop_and_start_preserves_jobs(scheduler_service):
    job_id = scheduler_service.add_schedule("nightly.txt", "14:30", lambda _: None)

    assert scheduler_service.start()
    running_state = scheduler_service.scheduler.state
    scheduler_service.stop()

    assert scheduler_service.scheduler.state != running_state
    assert scheduler_service.scheduler.get_job(job_id) is not None
    assert scheduler_service.start()
    assert scheduler_service.scheduler.state == running_state
    assert scheduler_service.scheduler.get_job(job_id) is not None
