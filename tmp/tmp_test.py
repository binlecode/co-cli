from co_cli.context._history import _gather_prior_summaries, _static_marker, _summary_marker

msgs = [_summary_marker(10, "This is a great summary.")]
msgs.append(_static_marker(5))

# Mocking _SUMMARY_MARKER_PREFIX directly in _gather_prior_summaries
import co_cli.context._history

co_cli.context._history._SUMMARY_MARKER_PREFIX = "This session is being continued from a previous conversation that ran out of context. The summary below"

res = _gather_prior_summaries(msgs)
print("RESULT:")
print(res)
