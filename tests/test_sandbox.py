import os
import pytest
from co_cli.sandbox import Sandbox


@pytest.mark.asyncio
async def test_sandbox_real_execution():
    """
    Test that the Sandbox can actually start a container and run a command.
    Mandate: No mocks, only real tests.
    """
    sandbox = Sandbox(image="alpine", container_name="co-test-runner")

    # Ensure cleanup before and after
    sandbox.cleanup()

    try:
        # Test command execution
        output = await sandbox.run_command("echo 'functional test'")
        assert "functional test" in output

        # Test persistence/mounting
        # Note: Sandbox mounts current dir to /workspace
        test_file = "test_mount.txt"
        with open(test_file, "w") as f:
            f.write("mount works")

        output = await sandbox.run_command(f"cat {test_file}")
        assert "mount works" in output

        os.remove(test_file)
    except Exception as e:
        pytest.fail(f"Sandbox functional test failed: {e}")
    finally:
        sandbox.cleanup()
