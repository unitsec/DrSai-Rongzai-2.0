from src.core.tools.mcp_task_runner import ProcessPollingMcpToolBase


def _return_large_result(task_id=None):
    return {
        "status": "success",
        "task_id": task_id,
        "plot_file": "/tmp/refinement.png",
        "content": "x" * 1_000_000,
    }


def test_large_result_is_drained_before_process_exit():
    runner = ProcessPollingMcpToolBase(
        target_func=_return_large_result,
        timeout_sec=3,
        poll_interval=0.01,
    )

    snapshot = runner.run(task_id="large-result")

    assert snapshot["state"] == "completed"
    assert snapshot["result"]["status"] == "success"
    assert snapshot["result"]["plot_file"] == "/tmp/refinement.png"
    assert len(snapshot["result"]["content"]) == 1_000_000