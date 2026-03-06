"""Functional tests for approval risk classifier."""
import pytest
from co_cli._approval_risk import ApprovalRisk, classify_tool_call


def test_high_risk_write_file():
    assert classify_tool_call("write_file", {"path": "foo.py", "content": "x"}) == ApprovalRisk.HIGH


def test_high_risk_edit_file():
    assert classify_tool_call("edit_file", {}) == ApprovalRisk.HIGH


def test_high_risk_shell_rm():
    assert classify_tool_call("run_shell_command", {"cmd": "rm -rf build/"}) == ApprovalRisk.HIGH


def test_high_risk_shell_redirect():
    assert classify_tool_call("run_shell_command", {"cmd": "echo hello > file.txt"}) == ApprovalRisk.HIGH


def test_low_risk_read_file():
    assert classify_tool_call("read_file", {"path": "README.md"}) == ApprovalRisk.LOW


def test_low_risk_list_directory():
    assert classify_tool_call("list_directory", {}) == ApprovalRisk.LOW


def test_low_risk_search_knowledge():
    assert classify_tool_call("search_knowledge", {"query": "foo"}) == ApprovalRisk.LOW


def test_low_risk_prefix():
    assert classify_tool_call("search_emails", {}) == ApprovalRisk.LOW


def test_medium_risk_shell_read():
    assert classify_tool_call("run_shell_command", {"cmd": "docker ps"}) == ApprovalRisk.MEDIUM


def test_medium_risk_unknown_tool():
    assert classify_tool_call("some_custom_tool", {}) == ApprovalRisk.MEDIUM
