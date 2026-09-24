def prepare_workspace(request, tool, session_key=None):
    """Clear stale tool state on a normal GET, preserving explicit continuations."""
    if request.method != "GET":
        return

    continuation_key = f"workspace_continue_{tool}"
    is_continuation = request.session.pop(continuation_key, False)
    is_reopen = request.session.pop("activity_reopen_tool", None) == tool
    if is_continuation or is_reopen:
        return

    if session_key:
        request.session.pop(session_key, None)
    request.session.pop(f"activity_initial_{tool}", None)


def continue_workspace(request, tool):
    """Keep a workspace across the next internal redirect only."""
    request.session[f"workspace_continue_{tool}"] = True
