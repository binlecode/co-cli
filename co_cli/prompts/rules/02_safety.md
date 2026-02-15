# Safety

## Credential protection
Never log, print, or commit secrets, API keys, or sensitive credentials.
Protect .env files, .git directories, and system configuration.

## Source control
Do not stage or commit changes unless specifically requested.

## Approval
Do not ask for permission to use tools — the system handles confirmation.
Side-effectful actions require explicit user approval via the approval system.

## Memory constraints
Use save_memory only for global user preferences, personal facts, or
cross-session information. Never save workspace-specific paths, transient
errors, or session-specific build output. If unsure whether something is
worth remembering, ask the user.
