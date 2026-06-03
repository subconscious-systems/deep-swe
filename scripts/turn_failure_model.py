"""Custom litellm model for mini-swe-agent: timeouts and max-token truncations
become FAILED TURNS the agent recovers from, instead of retry storms or dead
episodes.

Why: the endpoint sometimes enters runaway repetition loops (greedy-decoding
attractors), and under concurrency the endpoint can queue requests long enough
to trip the per-request timeout. A timed-out request would normally be retried
up to 10x by mini's tenacity wrapper — and since a runaway loop is
conversation-state-dependent, the retries tend to run away too, grinding
~timeout x 10 per step before the trial's wall clock kills the whole instance.
Instead we:

  * Timeout            -> no retry; append a corrective user message and let
                          the agent take its next turn.
  * finish_reason=length (max_tokens truncation) -> drop the truncated
                          assistant text entirely (keeps loop garbage out of
                          subsequent prompts) and append a targeted "you were
                          truncated, don't repeat yourself" message instead of
                          the generic "No tool calls found" error.

Other API errors (5xx, connection resets) still retry as before — transient
infra blips should not fail turns.

Wiring (mini-swe-agent/*.yaml):
    agents[].kwargs:
      model_class: turn_failure_model.TurnFailureModel
      version: "2.3.0"            # pin — this file subclasses mswea internals
      model_kwargs:
        timeout: <seconds>        # per-request litellm timeout (never retried)
    agents[].env:
      PYTHONPATH: /opt/deep-swe/scripts   # dir containing this file
    environment.mounts:
      - {type: bind, source: ${DEEP_SWE_ROOT}/scripts,
         target: /opt/deep-swe/scripts, read_only: true}

Pier passes model_class through as `-c model.model_class=...` to the
mini-swe-agent CLI, whose get_model() imports any dotted path found on
sys.path (hence the PYTHONPATH mount). Both raised FormatErrors count as
agent steps, and the trial's [agent] timeout_sec still bounds the episode,
so failure spirals stay bounded.

Ported from ../SWE-bench/mini-swe-runs/turn_failure_model.py (verbatim logic);
written against mini-swe-agent 2.3.0 (LitellmModel.query/_parse_actions/
abort_exceptions).
"""

import litellm

from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel

_RETRY_HINT = (
    "Do not repeat yourself. Respond concisely and include exactly one bash "
    "tool call with the next command to run."
)


class TurnFailureModel(LitellmModel):
    # Timeout aborts the tenacity retry loop immediately (reraised as-is),
    # then query() below converts it into a failed turn.
    abort_exceptions = LitellmModel.abort_exceptions + [litellm.exceptions.Timeout]

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        try:
            return super().query(messages, **kwargs)
        except litellm.exceptions.Timeout:
            raise FormatError(
                {
                    "role": "user",
                    "content": (
                        "Your previous response timed out before it finished "
                        "generating and was discarded. " + _RETRY_HINT
                    ),
                    "extra": {"interrupt_type": "Timeout"},
                }
            ) from None

    def _parse_actions(self, response) -> list[dict]:
        # Raised from inside query() before the assistant message is built, so
        # the truncated (often loop-degenerate) text never enters the
        # conversation — only the corrective user message below does.
        if (response.choices[0].finish_reason or "") == "length":
            raise FormatError(
                {
                    "role": "user",
                    "content": (
                        "Your previous response exceeded the maximum generation "
                        "length and was truncated and discarded. " + _RETRY_HINT
                    ),
                    "extra": {"interrupt_type": "MaxTokens"},
                }
            )
        return super()._parse_actions(response)
