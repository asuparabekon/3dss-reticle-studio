"""Short actionable UI messages, with complete diagnostics kept in the details pane."""


def error_summary(message):
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    role_errors = message.count("no role layer;")
    if role_errors:
        return (
            f"{role_errors} shapes need a role layer. In your SVG editor, move them into "
            "layers named paint (surface colour) or illum (emitted light)."
        )
    for index, line in enumerate(lines):
        if line.startswith("Errors ("):
            lines = lines[index + 1 :]
            break
    if not lines:
        return "The operation failed. Open Build details for more information."
    first = lines[0].removeprefix("- ").strip()
    if len(first) > 220:
        first = first[:217] + "..."
    return first + (" See Build details for more information." if len(lines) > 1 else "")
