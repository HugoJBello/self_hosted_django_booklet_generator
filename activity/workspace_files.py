import os


WORKSPACE_FILES = {
    "booklets": "booklets_items",
    "joinpdf": "joinpdf_items",
    "splitpdf": "splitpdf_state",
}


def workspace_pdf(request, tool, file_id):
    """Resolve only PDF paths already owned by the user's active workspace session."""
    session_key = WORKSPACE_FILES.get(tool)
    state = request.session.get(session_key) if session_key else None
    item = None
    if tool == "splitpdf" and isinstance(state, dict) and file_id == "source":
        item = {"path": state.get("pdf_path"), "name": state.get("pdf_name")}
    elif isinstance(state, list):
        if tool == "booklets":
            item = next((entry for entry in state if isinstance(entry, dict) and entry.get("id") == file_id), None)
            if item is None and file_id.isdigit():
                index = int(file_id)
                item = state[index] if 0 <= index < len(state) and isinstance(state[index], dict) else None
        elif tool == "joinpdf" and file_id.isdigit():
            index = int(file_id)
            item = state[index] if 0 <= index < len(state) and isinstance(state[index], dict) else None
    if not item:
        return None
    path = item.get("path")
    if not path or not os.path.isfile(path) or os.path.splitext(path)[1].lower() != ".pdf":
        return None
    return {"path": path, "name": item.get("name") or os.path.basename(path)}
