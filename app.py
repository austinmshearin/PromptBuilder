import sqlite3
from contextlib import closing
from typing import Optional, List, Tuple
import streamlit as st
import pyperclip
import json

# -------------------------------
# Page setup
# -------------------------------
st.set_page_config(page_title="Prompt Builder (SQLite)", layout="wide")
st.header("Prompt Components & Builder")

st.markdown("""
<style>
/* Scrollable left column */
div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div:first-child {
  max-height: calc(70vh);
  overflow-y: auto;
  padding-right: 0.5rem;
}
header[data-testid="stHeader"] { display: none; }
div[data-testid="stToolbar"] { display: none; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stAppViewContainer"] > .main {padding-top: 0rem;}
div[data-testid="stMainBlockContainer"] {padding: 2rem 1rem 1rem}
</style>
""", unsafe_allow_html=True)

# -------------------------------
# DB Helpers (SQLite)
# -------------------------------
@st.cache_resource
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect("prompt_components.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS components (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_components_folder ON components(folder_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_folder_name_per_parent ON folders(parent_id, name);
        """
    )
    conn.execute("INSERT OR IGNORE INTO folders (name, parent_id) VALUES ('home', NULL)")
    home_id = conn.execute("SELECT id FROM folders WHERE parent_id IS NULL AND name='home'").fetchone()[0]
    conn.execute("UPDATE components SET folder_id = ? WHERE folder_id IS NULL", (home_id,))
    conn.commit()

def exec_commit(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> None:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
    conn.commit()

def query_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        return cur.fetchall()

def query_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        return cur.fetchone()

def get_home_folder_id(conn: sqlite3.Connection) -> int:
    row = query_one(conn, "SELECT id FROM folders WHERE parent_id IS NULL AND name='home'")
    if not row:
        exec_commit(conn, "INSERT INTO folders (name, parent_id) VALUES ('home', NULL)")
        row = query_one(conn, "SELECT id FROM folders WHERE parent_id IS NULL AND name='home'")
    return row["id"]

def list_folders_by_parent(conn: sqlite3.Connection, parent_id: Optional[int]) -> List[sqlite3.Row]:
    if parent_id is None:
        return query_all(conn, "SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name")
    return query_all(conn, "SELECT * FROM folders WHERE parent_id = ? ORDER BY name", (parent_id,))

def create_folder(conn: sqlite3.Connection, name: str, parent_id: Optional[int]) -> int:
    with closing(conn.cursor()) as cur:
        if parent_id is None:
            cur.execute("INSERT INTO folders (name, parent_id) VALUES (?, NULL)", (name,))
        else:
            cur.execute("INSERT INTO folders (name, parent_id) VALUES (?, ?)", (name, parent_id))
        conn.commit()
        return cur.lastrowid

def rename_folder(conn: sqlite3.Connection, folder_id: int, new_name: str) -> None:
    exec_commit(conn, "UPDATE folders SET name = ? WHERE id = ?", (new_name, folder_id))

def delete_folder(conn: sqlite3.Connection, folder_id: int) -> bool:
    children = list_folders_by_parent(conn, folder_id)
    comps = list_components_by_folder(conn, folder_id)
    if children or comps:
        return False
    exec_commit(conn, "DELETE FROM folders WHERE id = ?", (folder_id,))
    return True

def get_descendant_folder_ids(conn: sqlite3.Connection, folder_id: int) -> list[int]:
    ids = [folder_id]
    stack = [folder_id]
    while stack:
        pid = stack.pop()
        for row in list_folders_by_parent(conn, pid):
            ids.append(row["id"])
            stack.append(row["id"])
    return ids

def get_component_ids_in_folders(conn: sqlite3.Connection, folder_ids: list[int]) -> list[int]:
    if not folder_ids:
        return []
    placeholders = ",".join("?" * len(folder_ids))
    rows = query_all(conn, f"SELECT id FROM components WHERE folder_id IN ({placeholders})", tuple(folder_ids))
    return [r["id"] for r in rows]

def delete_folder_recursive(conn: sqlite3.Connection, folder_id: int) -> tuple[bool, str | None]:
    try:
        if folder_id == get_home_folder_id(conn):
            return False, "Cannot delete the 'home' folder."
    except Exception:
        pass

    folder_ids = get_descendant_folder_ids(conn, folder_id)
    comp_ids = get_component_ids_in_folders(conn, folder_ids)
    if comp_ids:
        placeholders = ",".join("?" * len(comp_ids))
        exec_commit(conn, f"DELETE FROM components WHERE id IN ({placeholders})", tuple(comp_ids))
        if st.session_state.get("selected_component_id") in comp_ids:
            st.session_state.selected_component_id = None
        comp_id_set = set(comp_ids)
        st.session_state.builder_list = [cid for cid in st.session_state.builder_list if cid not in comp_id_set]

    exec_commit(conn, "DELETE FROM folders WHERE id = ?", (folder_id,))
    return True, None

def list_components_by_folder(conn: sqlite3.Connection, folder_id: Optional[int]) -> List[sqlite3.Row]:
    if folder_id is None:
        return query_all(conn, "SELECT * FROM components WHERE folder_id IS NULL ORDER BY name")
    return query_all(conn, "SELECT * FROM components WHERE folder_id = ? ORDER BY name", (folder_id,))

def get_component(conn: sqlite3.Connection, component_id: int) -> Optional[sqlite3.Row]:
    return query_one(conn, "SELECT * FROM components WHERE id = ?", (component_id,))

def create_component(conn: sqlite3.Connection, name: str, folder_id: Optional[int]) -> int:
    if folder_id is None:
        folder_id = get_home_folder_id(conn)
    with closing(conn.cursor()) as cur:
        cur.execute("INSERT INTO components (name, content, folder_id) VALUES (?, '', ?)", (name, folder_id))
        conn.commit()
        return cur.lastrowid

def rename_component(conn: sqlite3.Connection, component_id: int, new_name: str) -> None:
    exec_commit(conn, "UPDATE components SET name = ?, updated_at = datetime('now') WHERE id = ?", (new_name, component_id))

def update_component_content(conn: sqlite3.Connection, component_id: int, new_content: str) -> None:
    exec_commit(conn, "UPDATE components SET content = ?, updated_at = datetime('now') WHERE id = ?", (new_content, component_id))

def move_component(conn: sqlite3.Connection, component_id: int, new_folder_id: Optional[int]) -> None:
    if new_folder_id is None:
        new_folder_id = get_home_folder_id(conn)
    exec_commit(conn, "UPDATE components SET folder_id = ?, updated_at = datetime('now') WHERE id = ?", (new_folder_id, component_id))

def delete_component(conn: sqlite3.Connection, component_id: int) -> None:
    exec_commit(conn, "DELETE FROM components WHERE id = ?", (component_id,))

def get_folder(conn: sqlite3.Connection, folder_id: int) -> Optional[sqlite3.Row]:
    return query_one(conn, "SELECT * FROM folders WHERE id = ?", (folder_id,))

def build_folder_path(conn: sqlite3.Connection, folder_id: Optional[int]) -> str:
    home_id = get_home_folder_id(conn)
    if folder_id is None or folder_id == home_id:
        return "home"
    names = []
    current = get_folder(conn, folder_id)
    while current is not None and not (current["parent_id"] is None and current["name"] == "home"):
        names.append(current["name"])
        current = get_folder(conn, current["parent_id"]) if current["parent_id"] else None
    return "home / " + " / ".join(reversed(names))

def all_folders_with_paths(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    home_id = get_home_folder_id(conn)
    results: List[Tuple[int, str]] = [(home_id, "home")]
    def dfs(parent_id: int, prefix: str):
        for f in list_folders_by_parent(conn, parent_id):
            path = prefix + " / " + f["name"]
            results.append((f["id"], path))
            dfs(f["id"], path)
    dfs(home_id, "home")
    return results

def export_db_to_json(conn: sqlite3.Connection) -> str:
    folders = query_all(conn, "SELECT id, name, parent_id FROM folders ORDER BY id")
    components = query_all(conn, """
        SELECT id, name, content, folder_id, created_at, updated_at
        FROM components ORDER BY id
    """)
    payload = {
        "schema": "prompt_builder_sqlite_v1",
        "folders": [dict(r) for r in folders],
        "components": [dict(r) for r in components],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

def import_db_from_json(conn: sqlite3.Connection, json_text: str) -> None:
    payload = json.loads(json_text)
    if not isinstance(payload, dict) or "folders" not in payload or "components" not in payload:
        raise ValueError("Invalid JSON: expecting { folders: [...], components: [...] }")

    folders = payload.get("folders", [])
    components = payload.get("components", [])

    # Import in a transaction; disable FK to insert in any order (IDs preserved)
    with closing(conn.cursor()) as cur:
        cur.execute("BEGIN")
        try:
            cur.execute("PRAGMA foreign_keys = OFF")
            cur.execute("DELETE FROM components")
            cur.execute("DELETE FROM folders")

            for f in folders:
                cur.execute(
                    "INSERT INTO folders (id, name, parent_id) VALUES (?, ?, ?)",
                    (f.get("id"), f.get("name"), f.get("parent_id")),
                )

            for c in components:
                cur.execute(
                    """INSERT INTO components
                       (id, name, content, folder_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        c.get("id"),
                        c.get("name"),
                        c.get("content", ""),
                        c.get("folder_id"),
                        c.get("created_at"),
                        c.get("updated_at"),
                    ),
                )

            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.execute("PRAGMA foreign_keys = ON")

    # Ensure 'home' exists and rootless components are moved there (aligns with app expectations)
    init_db(conn)

# -------------------------------
# Session State
# -------------------------------
if "builder_list" not in st.session_state:
    st.session_state.builder_list = []  # component ids in order
if "free_text" not in st.session_state:
    st.session_state.free_text = ""
if "pp_epoch" not in st.session_state:
    st.session_state.pp_epoch = 0

# -------------------------------
# Dialog Helpers
# -------------------------------
def _close(flag_key: str):
    st.session_state[flag_key] = False
    st.session_state["pp_epoch"] = st.session_state.get("pp_epoch", 0) + 1
    st.rerun()

def _open_dialog(flag_key: str):
    # Open a dialog and force the popover to close on this run
    st.session_state[flag_key] = True
    st.session_state["pp_epoch"] = st.session_state.get("pp_epoch", 0) + 1
    st.rerun()

def _pp_label(base: str = "…") -> str:
    epoch = st.session_state.get("pp_epoch", 0)
    return base + ("\u200b" * (epoch % 7))

def show_new_folder_dialog(conn, parent_id: int):
    flag = f"dlg_new_folder_{parent_id}"
    if st.session_state.get(flag):
        @st.dialog("New folder")
        def _dlg():
            with st.form(f"form_new_folder_{parent_id}"):
                name = st.text_input("Subfolder name")
                c1, c2 = st.columns(2)
                create = c1.form_submit_button("Create", type="primary", use_container_width=True)
                cancel = c2.form_submit_button("Cancel", use_container_width=True)
                if create:
                    if name.strip():
                        create_folder(conn, name.strip(), parent_id)
                        _close(flag)
                    else:
                        st.warning("Name cannot be empty.")
                elif cancel:
                    _close(flag)
        _dlg()

def show_new_component_dialog(conn, folder_id: int):
    flag = f"dlg_new_comp_{folder_id}"
    if st.session_state.get(flag):
        @st.dialog("New component")
        def _dlg():
            with st.form(f"form_new_comp_{folder_id}"):
                name = st.text_input("Component name")
                c1, c2 = st.columns(2)
                create = c1.form_submit_button("Create", type="primary", use_container_width=True)
                cancel = c2.form_submit_button("Cancel", use_container_width=True)
                if create:
                    if name.strip():
                        create_component(conn, name.strip(), folder_id)
                        _close(flag)
                    else:
                        st.warning("Name cannot be empty.")
                elif cancel:
                    _close(flag)
        _dlg()

def show_rename_folder_dialog(conn, folder_id: int, current_name: str):
    flag = f"dlg_rename_folder_{folder_id}"
    if st.session_state.get(flag):
        @st.dialog("Rename folder")
        def _dlg():
            with st.form(f"form_rename_folder_{folder_id}"):
                new_name = st.text_input("Folder name", value=current_name)
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("Save", type="primary", use_container_width=True)
                cancel = c2.form_submit_button("Cancel", use_container_width=True)
                if save:
                    if new_name.strip():
                        rename_folder(conn, folder_id, new_name.strip())
                        _close(flag)
                    else:
                        st.warning("Name cannot be empty.")
                elif cancel:
                    _close(flag)
        _dlg()

def show_delete_folder_dialog(conn: sqlite3.Connection, folder_id: int, folder_name: str):
    flag = f"dlg_del_folder_{folder_id}"
    if st.session_state.get(flag):
        @st.dialog(f"Delete folder: {folder_name}")
        def _dlg():
            st.error("This will permanently delete this folder, all subfolders, and all components within them.", icon="⚠️")
            c1, c2 = st.columns(2)
            do_delete = c1.button("Delete", type="primary", use_container_width=True, key=f"df_all_{folder_id}")
            cancel = c2.button("Cancel", use_container_width=True, key=f"df_cancel_{folder_id}")
            if do_delete:
                ok, msg = delete_folder_recursive(conn, folder_id)
                if not ok and msg:
                    st.error(msg)
                st.session_state[flag] = False
                st.rerun()
            elif cancel:
                st.session_state[flag] = False
                st.rerun()
        _dlg()

def show_rename_component_dialog(conn, comp_id: int, current_name: str):
    flag = f"dlg_rename_comp_{comp_id}"
    if st.session_state.get(flag):
        @st.dialog("Rename component")
        def _dlg():
            with st.form(f"form_rename_comp_{comp_id}"):
                new_name = st.text_input("Component name", value=current_name)
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("Save", type="primary", use_container_width=True)
                cancel = c2.form_submit_button("Cancel", use_container_width=True)
                if save:
                    if new_name.strip():
                        rename_component(conn, comp_id, new_name.strip())
                        _close(flag)
                    else:
                        st.warning("Name cannot be empty.")
                elif cancel:
                    _close(flag)
        _dlg()

def show_move_component_dialog(conn, comp_id: int, current_folder_id: Optional[int]):
    flag = f"dlg_move_comp_{comp_id}"
    if st.session_state.get(flag):
        @st.dialog("Move component")
        def _dlg():
            options = all_folders_with_paths(conn)
            idx = 0
            current_id = current_folder_id if current_folder_id is not None else get_home_folder_id(conn)
            for i, (fid, _) in enumerate(options):
                if fid == current_id:
                    idx = i
                    break
            sel = st.selectbox("Destination folder", options=options, index=idx, format_func=lambda x: x[1], key=f"mv_sel_{comp_id}")
            c1, c2 = st.columns(2)
            move_btn = c1.button("Move", type="primary", use_container_width=True, key=f"mv_btn_{comp_id}")
            cancel = c2.button("Cancel", use_container_width=True, key=f"mv_cancel_{comp_id}")
            if move_btn:
                move_component(conn, comp_id, sel[0])
                _close(flag)
            elif cancel:
                _close(flag)
        _dlg()

def show_delete_component_dialog(conn, comp_id: int):
    flag = f"dlg_del_comp_{comp_id}"
    if st.session_state.get(flag):
        @st.dialog("Delete component")
        def _dlg():
            st.warning("This deletes the component permanently.", icon="⚠️")
            c1, c2 = st.columns(2)
            yes = c1.button("Confirm Delete", type="primary", use_container_width=True, key=f"del_yes_{comp_id}")
            no = c2.button("Cancel", use_container_width=True, key=f"del_no_{comp_id}")
            if yes:
                delete_component(conn, comp_id)
                st.session_state.builder_list = [cid for cid in st.session_state.builder_list if cid != comp_id]
                _close(flag)
            elif no:
                _close(flag)
        _dlg()

def show_edit_component_dialog(conn, comp_id: int):
    flag = f"dlg_edit_comp_{comp_id}"
    if st.session_state.get(flag):
        comp = get_component(conn, comp_id)
        title = f"Edit component: {comp['name'] if comp else comp_id}"
        @st.dialog(title)
        def _dlg():
            if not comp:
                st.warning("Component no longer exists.")
                if st.button("Close"):
                    _close(flag)
                return
            with st.form(key=f"edit_component_form_{comp_id}"):
                new_name = st.text_input("Component name", value=comp["name"])
                folder_options = all_folders_with_paths(conn)
                idx = 0
                for i, (fid, _) in enumerate(folder_options):
                    if fid == comp["folder_id"]:
                        idx = i
                        break
                dest = st.selectbox("Folder", options=folder_options, index=idx, format_func=lambda x: x[1])
                content = st.text_area("Content", value=comp["content"], height=350)
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("💾 Save", use_container_width=True)
                cancel = c2.form_submit_button("Cancel", use_container_width=True)
                if save:
                    if new_name.strip() != comp["name"]:
                        rename_component(conn, comp_id, new_name.strip())
                    if dest[0] != comp["folder_id"]:
                        move_component(conn, comp_id, dest[0])
                    if content != comp["content"]:
                        update_component_content(conn, comp_id, content)
                    _close(flag)
                elif cancel:
                    _close(flag)
        _dlg()

def show_import_dialog(conn: sqlite3.Connection):
    flag = "dlg_import_json"
    if st.session_state.get(flag):
        @st.dialog("Import JSON backup")
        def _dlg():
            uploaded = st.file_uploader("Choose a JSON file", type=["json"], accept_multiple_files=False)
            c1, c2 = st.columns(2)
            do_load = c1.button("Load", type="primary", use_container_width=True)
            cancel = c2.button("Cancel", use_container_width=True)
            if do_load:
                if not uploaded:
                    st.warning("Please select a JSON file.")
                else:
                    try:
                        text = uploaded.read().decode("utf-8")
                        import_db_from_json(conn, text)
                        # Clear volatile UI state that points at old IDs/content
                        st.session_state.builder_list = []
                        st.session_state.free_text = ""
                        st.success("Import complete.")
                        st.session_state[flag] = False
                        st.rerun()
                    except Exception as e:
                        st.error(f"Import failed: {e}")
            if cancel:
                st.session_state[flag] = False
                st.rerun()
        _dlg()

# -------------------------------
# UI Helpers
# -------------------------------
def add_to_builder(component_id: int):
    st.session_state.builder_list.append(component_id)

def move_up(idx: int):
    if idx > 0:
        L = st.session_state.builder_list
        L[idx-1], L[idx] = L[idx], L[idx-1]

def move_down(idx: int):
    L = st.session_state.builder_list
    if idx < len(L)-1:
        L[idx+1], L[idx] = L[idx], L[idx+1]

def remove_from_builder(idx: int):
    st.session_state.builder_list.pop(idx)

def clear_all():
    st.session_state.builder_list = []
    st.session_state.free_text = ""

def render_component_item(conn: sqlite3.Connection, comp: sqlite3.Row):
    left, center, right = st.columns([8, 1, 1])
    with left:
        if st.button(f"{comp['name']}", key=f"open_comp_{comp['id']}"):
            st.session_state[f"dlg_edit_comp_{comp['id']}"] = True
    with center:
        st.button("➕", key=f"add_{comp['id']}", on_click=add_to_builder, args=(comp["id"],))
    with right:
        pp_holder = st.empty()
        with pp_holder.container():
            with st.popover(_pp_label("…")):
                do_rename = st.button("Rename", key=f"pp_rename_{comp['id']}", use_container_width=True)
                do_move = st.button("Move", key=f"pp_move_{comp['id']}", use_container_width=True)
                do_delete = st.button("Delete", key=f"pp_del_{comp['id']}", use_container_width=True)

                if do_rename:
                    pp_holder.empty()
                    _open_dialog(f"dlg_rename_comp_{comp['id']}")
                if do_move:
                    pp_holder.empty()
                    _open_dialog(f"dlg_move_comp_{comp['id']}")
                if do_delete:
                    pp_holder.empty()
                    _open_dialog(f"dlg_del_comp_{comp['id']}")

        show_edit_component_dialog(conn, comp["id"])
        show_rename_component_dialog(conn, comp["id"], comp["name"])
        show_move_component_dialog(conn, comp["id"], comp["folder_id"])
        show_delete_component_dialog(conn, comp["id"])

def render_folder_node(conn: sqlite3.Connection, folder: sqlite3.Row, depth: int = 0, home_id: Optional[int] = None):
    is_home = home_id is not None and folder["id"] == home_id
    with st.expander(f"📁 {folder['name']}", expanded=is_home):
        # Row with a far-right "…" popover (same spot as the old action buttons)
        _, folder_acts = st.columns([9, 1])
        with folder_acts:
            pp_holder = st.empty()
            with pp_holder.container():
                with st.popover(_pp_label("…")):
                    new_folder_clicked = st.button("New folder", key=f"nf_btn_{folder['id']}", use_container_width=True)
                    new_component_clicked = st.button("New component", key=f"nc_btn_{folder['id']}", use_container_width=True)
                    rename_clicked = False
                    delete_clicked = False
                    if not is_home:
                        rename_clicked = st.button("Rename", key=f"rf_btn_{folder['id']}", use_container_width=True)
                        delete_clicked = st.button("Delete folder", key=f"df_btn_{folder['id']}", use_container_width=True)

                    # Close the popover BEFORE opening any dialog (so it can't linger)
                    if new_folder_clicked:
                        pp_holder.empty()
                        _open_dialog(f"dlg_new_folder_{folder['id']}")
                    if new_component_clicked:
                        pp_holder.empty()
                        _open_dialog(f"dlg_new_comp_{folder['id']}")
                    if rename_clicked:
                        pp_holder.empty()
                        _open_dialog(f"dlg_rename_folder_{folder['id']}")
                    if delete_clicked:
                        pp_holder.empty()
                        _open_dialog(f"dlg_del_folder_{folder['id']}")

        # Render dialogs (unchanged)
        show_new_folder_dialog(conn, folder["id"])
        show_new_component_dialog(conn, folder["id"])
        if not is_home:
            show_rename_folder_dialog(conn, folder["id"], folder["name"])
            show_delete_folder_dialog(conn, folder["id"], folder["name"])

        # Components in this folder
        comps = list_components_by_folder(conn, folder["id"])
        if comps:
            st.markdown("**Components**")
            for c in comps:
                render_component_item(conn, c)
        else:
            st.caption("No components here yet.")

        # Subfolders
        children = list_folders_by_parent(conn, folder["id"])
        if children:
            st.markdown("**Subfolders**")
            for child in children:
                render_folder_node(conn, child, depth=depth + 1, home_id=home_id)

def render_root_tree(conn: sqlite3.Connection):
    home_id = get_home_folder_id(conn)
    home = get_folder(conn, home_id)
    render_folder_node(conn, home, depth=0, home_id=home_id)

def build_prompt_text(conn: sqlite3.Connection) -> str:
    parts: List[str] = []
    for cid in st.session_state.builder_list:
        c = get_component(conn, cid)
        if c:
            parts.append(c["content"])
    free = st.session_state.free_text.strip()
    if free:
        parts.append(free)
    return "\n\n".join(parts)

# -------------------------------
# Sidebar "pages"
# -------------------------------
conn = get_conn()
page = st.sidebar.radio("Pages", options=["Build", "Preview", "Export/Import"], index=0)

# -------------------------------
# Page: Build (left tree + right builder)
# -------------------------------
if page == "Build":
    left, right = st.columns([5, 7], gap="large")

    with left:
        st.subheader("Folders & Components")
        render_root_tree(conn)

    with right:
        st.subheader("Prompt Builder")
        if not st.session_state.builder_list:
            st.info("Add components from the left column to start building your prompt.")
        else:
            st.markdown("**Order & Manage Components**")
            for i, cid in enumerate(st.session_state.builder_list):
                c = get_component(conn, cid)
                if not c:
                    continue
                row = st.columns([6, 1, 1, 1])
                with row[0]:
                    st.write(f"🧩 {c['name']}")
                with row[1]:
                    st.button("↑", key=f"up_{i}", on_click=move_up, args=(i,))
                with row[2]:
                    st.button("↓", key=f"down_{i}", on_click=move_down, args=(i,))
                with row[3]:
                    st.button("✖", key=f"rem_{i}", on_click=remove_from_builder, args=(i,))

        st.markdown("**Additional Text (appended to the end)**")
        st.session_state.free_text = st.text_area(" ", value=st.session_state.free_text, label_visibility="collapsed", height=180)

        prompt_text = build_prompt_text(conn)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Clear All"):
                clear_all()
                st.experimental_rerun()
        with c2:
            if st.button("Copy Prompt"):
                try:
                    pyperclip.copy(prompt_text)
                    st.success("Copied to clipboard.")
                except Exception as e:
                    st.warning(f"Copy failed: {e}")
        with c3:
            st.download_button("Download Prompt", data=prompt_text, file_name="prompt.txt", mime="text/plain")

# -------------------------------
# Page: Preview (full prompt only)
# -------------------------------
elif page == "Preview":
    st.subheader("Full Prompt Preview")
    prompt_text = build_prompt_text(conn)
    st.text_area(" ", value=prompt_text, height=420, label_visibility="collapsed", disabled=True)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Copy Prompt"):
            try:
                pyperclip.copy(prompt_text)
                st.success("Copied to clipboard.")
            except Exception as e:
                st.warning(f"Copy failed: {e}")
    with c2:
        st.download_button("Download Prompt", data=prompt_text, file_name="prompt.txt", mime="text/plain")

elif page == "Export/Import":
    st.subheader("Export / Import")

    # Export button: downloads a full JSON snapshot of folders + components
    json_text = export_db_to_json(conn)
    st.download_button(
        "Export",
        data=json_text,
        file_name="prompt_components.json",
        mime="application/json",
        use_container_width=False
    )
    # Load button: opens a dialog with a file uploader
    if st.button("Load", type="primary", use_container_width=False):
        st.session_state["dlg_import_json"] = True

    # Render the import dialog when triggered
    show_import_dialog(conn)
