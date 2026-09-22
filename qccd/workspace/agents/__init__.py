"""Session adapters: how a prompt sent from Studio reaches an agent runtime.

Every adapter meets one small capability contract (`base.AdapterCapabilities`):

    connect / identify-bind a session / deliver input / observe runtime events /
    retrieve status / (optionally) steer and interrupt

and reports which of those it really supports.  The modes shipped here:

    codex      `appserver`  Codex app-server over WebSocket: turn/start delivers, turn/steer
                            corrects the running turn, turn/interrupt stops it, notifications
                            are observed, thread/turns/list reconciles uncertain deliveries.
    claude     `channel`    the QCCD MCP server declared as a Claude Code channel pushes
                            `notifications/claude/channel`; no acknowledgement, no steer, no
                            interrupt -- deliveries stay `uncertain` until the agent reads them.
    generic    `pull`       reduced capability: prompts wait until the agent next calls
                            qccd_get_context.  Not automatic delivery, and labelled as such.
"""
