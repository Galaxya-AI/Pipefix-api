"""Validation must be based on GitLab evidence, never a generated claim."""
from workflow_publication.validation import failed_job_signature, validation_record


def test_matching_success_is_verified():
    assert validation_record({'id': 7, 'sha': 'abc', 'status': 'success'}, 'abc')['verified'] is True


def test_success_for_another_commit_is_not_verified():
    result = validation_record({'id': 7, 'sha': 'old', 'status': 'success'}, 'new')
    assert result['verified'] is False
    assert result['reason'] == 'sha_mismatch'


def test_failed_pipeline_is_not_verified():
    assert validation_record({'id': 7, 'sha': 'abc', 'status': 'failed'}, 'abc')['verified'] is False


def test_timeout_is_not_verified():
    assert validation_record(None, 'abc')['reason'] == 'pipeline_timeout'


def test_no_expected_sha_cannot_be_verified():
    assert validation_record({'sha': '', 'status': 'success'}, '')['verified'] is False


def test_signature_is_stable_and_ignores_allowed_failure():
    jobs = [{'id': 13, 'name': 'lint'}, {'id': 11, 'name': 'test'}, {'name': 'optional', 'allow_failure': True}]
    assert failed_job_signature(jobs) == ('lint', 'test')
