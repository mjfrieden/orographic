import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.upload_research_artifacts_to_r2 import _put_object


def result(code, stderr=""):
    return subprocess.CompletedProcess([], code, stdout="", stderr=stderr)


def test_transient_upload_retries_identical_object():
    with patch("scripts.upload_research_artifacts_to_r2.subprocess.run", side_effect=[result(1, "500: Internal Server Error"), result(0)]) as run, patch("scripts.upload_research_artifacts_to_r2.time.sleep") as sleep:
        _put_object("bucket", "ledger.json", Path("ledger.json"))
    assert run.call_args_list[0] == run.call_args_list[1]
    sleep.assert_called_once_with(2)


@pytest.mark.parametrize("message", ["403: Forbidden", "400: Bad Request", "Invalid file"])
def test_permanent_upload_failure_is_not_retried(message):
    with patch("scripts.upload_research_artifacts_to_r2.subprocess.run", return_value=result(1, message)) as run, patch("scripts.upload_research_artifacts_to_r2.time.sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError):
            _put_object("bucket", "ledger.json", Path("ledger.json"))
    assert run.call_count == 1
    sleep.assert_not_called()


def test_transient_retries_are_bounded():
    with patch("scripts.upload_research_artifacts_to_r2.subprocess.run", return_value=result(1, "429: Too Many Requests")) as run, patch("scripts.upload_research_artifacts_to_r2.time.sleep") as sleep:
        with pytest.raises(subprocess.CalledProcessError):
            _put_object("bucket", "ledger.json", Path("ledger.json"))
    assert run.call_count == 4
    assert [call.args[0] for call in sleep.call_args_list] == [2, 4, 8]
