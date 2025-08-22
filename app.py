import sqlite3
from contextlib import closing
from typing import Optional, List, Tuple
import streamlit as st

# -------------------------------
# Page setup
# -------------------------------
st.set_page_config(page_title="Prompt Builder (SQLite)", layout="wide")
st.header("Prompt Components & Builder")

st.markdown("""
<style>
/* Make the first column inside tab contents independently scrollable */
div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div:first-child {
  /* Adjust the -Xpx to account for your header/title height */
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
# Tab Helpers (SQLite)
# -------------------------------
TAB_LABELS = {
    "components": "Components",
    "builder": "Prompt Builder",
}

def _get_query_tab():
    try:
        v = st.query_params.get("tab")
        if isinstance(v, list):
            v = v[0] if v else None
    except Exception:
        v = st.experimental_get_query_params().get("tab", [None])[0]
    return v if v in TAB_LABELS else None

def _set_query_tab(tab_key: str):
    try:
        st.query_params["tab"] = tab_key
    except Exception:
        st.experimental_set_query_params(tab=tab_key)

if "active_tab" not in st.session_state:
    st.session_state.active_tab = _get_query_tab() or "components"

if "tab_picker" not in st.session_state:
    st.session_state.tab_picker = TAB_LABELS[st.session_state.active_tab]

def _sync_tab_from_radio():
    # keep active_tab in lockstep with radio selection
    chosen = st.session_state.tab_picker
    st.session_state.active_tab = "builder" if chosen == TAB_LABELS["builder"] else "components"
    _set_query_tab(st.session_state.active_tab)

def set_active_tab(tab_key: str):
    # use this from your builder actions (add/move/clear)
    st.session_state.active_tab = tab_key
    st.session_state.tab_picker = TAB_LABELS[tab_key]   # keep radio UI in sync
    _set_query_tab(tab_key)

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
        -- Optional: keep (parent_id, name) unique so 'home' can't be duplicated at root
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_folder_name_per_parent ON folders(parent_id, name);
        """
    )
    # Ensure a single root-level 'home' folder exists
    conn.execute("INSERT OR IGNORE INTO folders (name, parent_id) VALUES ('home', NULL)")
    # Get home id
    home_id = conn.execute(
        "SELECT id FROM folders WHERE parent_id IS NULL AND name='home'"
    ).fetchone()[0]
    # Migrate any root-level components (folder_id IS NULL) into 'home'
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
    # prevent nuking 'home' if you use a protected root
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
        # clean UI state
        if st.session_state.get("selected_component_id") in comp_ids:
            st.session_state.selected_component_id = None
        comp_id_set = set(comp_ids)
        st.session_state.builder_list = [cid for cid in st.session_state.builder_list if cid not in comp_id_set]

    # child folders will cascade via folders(parent_id) ON DELETE CASCADE
    exec_commit(conn, "DELETE FROM folders WHERE id = ?", (folder_id,))
    return True, None

# -------------------------------
# Data Access
# -------------------------------
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
    # Only delete if no subfolders and no components
    children = list_folders_by_parent(conn, folder_id)
    comps = list_components_by_folder(conn, folder_id)
    if children or comps:
        return False
    exec_commit(conn, "DELETE FROM folders WHERE id = ?", (folder_id,))
    return True

def list_components_by_folder(conn: sqlite3.Connection, folder_id: Optional[int]) -> List[sqlite3.Row]:
    if folder_id is None:
        return query_all(conn, "SELECT * FROM components WHERE folder_id IS NULL ORDER BY name")
    return query_all(conn, "SELECT * FROM components WHERE folder_id = ? ORDER BY name", (folder_id,))

def get_component(conn: sqlite3.Connection, component_id: int) -> Optional[sqlite3.Row]:
    return query_one(conn, "SELECT * FROM components WHERE id = ?", (component_id,))

def create_component(conn: sqlite3.Connection, name: str, folder_id: Optional[int]) -> int:
    # Default to home if None is passed
    if folder_id is None:
        folder_id = get_home_folder_id(conn)
    with closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO components (name, content, folder_id) VALUES (?, '', ?)",
            (name, folder_id),
        )
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

# -------------------------------
# Session State
# -------------------------------
if "selected_component_id" not in st.session_state:
    st.session_state.selected_component_id = None

if "builder_list" not in st.session_state:  # list of component ids in order
    st.session_state.builder_list = []

if "search_text" not in st.session_state:
    st.session_state.search_text = ""

# -------------------------------
# UI helpers
# -------------------------------
def small_button(label: str, key: str) -> bool:
    return st.button(label, key=key, use_container_width=False)

def select_component(component_id: int):
    st.session_state.selected_component_id = component_id

def add_to_builder(component_id: int):
    st.session_state.builder_list.append(component_id)
    set_active_tab("builder")

def move_up(idx: int):
    if idx > 0:
        L = st.session_state.builder_list
        L[idx-1], L[idx] = L[idx], L[idx-1]
    set_active_tab("builder")

def move_down(idx: int):
    L = st.session_state.builder_list
    if idx < len(L)-1:
        L[idx+1], L[idx] = L[idx], L[idx+1]
    set_active_tab("builder")

def remove_from_builder(idx: int):
    st.session_state.builder_list.pop(idx)
    set_active_tab("builder")

def clear_builder():
    st.session_state.builder_list = []
    set_active_tab("builder")

def name_editor_row(initial: str, save_callback, key: str, placeholder: str = "Name"):
    with st.form(key=key):
        new_name = st.text_input(" ", value=initial, label_visibility="collapsed", placeholder=placeholder)
        c1, c2 = st.columns(2)
        with c1:
            submitted = st.form_submit_button("Save", use_container_width=True)
        with c2:
            cancel = st.form_submit_button("Cancel", use_container_width=True)
        if submitted:
            if new_name.strip():
                save_callback(new_name.strip())
            else:
                st.warning("Name cannot be empty.")
        elif cancel:
            st.rerun()

def render_component_item(conn: sqlite3.Connection, comp: sqlite3.Row, for_builder: bool = False):
    left, right = st.columns([9, 1])
    with left:
        if for_builder:
            # Prompt Builder: plain text (no button)
            st.markdown(f"**{comp['name']}**")
        else:
            open_key = f"{'builder_' if for_builder else ''}open_comp_{comp['id']}"
            if st.button(f"{comp['name']}", key=open_key):
                select_component(comp["id"])
    with right:
        if for_builder:
            st.button("➕", key=f"add_{comp['id']}", on_click=add_to_builder, args=(comp["id"],))
        else:
            label = _pp_label("…")
            with st.popover(label):
                if st.button("Rename", key=f"pp_rename_{comp['id']}", use_container_width=True):
                    st.session_state[f"dlg_rename_comp_{comp['id']}"] = True
                if st.button("Move", key=f"pp_move_{comp['id']}", use_container_width=True):
                    st.session_state[f"dlg_move_comp_{comp['id']}"] = True
                if st.button("Delete", key=f"pp_del_{comp['id']}", use_container_width=True):
                    st.session_state[f"dlg_del_comp_{comp['id']}"] = True

            # Render dialogs if open
            show_rename_component_dialog(conn, comp["id"], comp["name"])
            show_move_component_dialog(conn, comp["id"], comp["folder_id"])
            show_delete_component_dialog(conn, comp["id"])

def render_folder_node(conn: sqlite3.Connection, folder: sqlite3.Row, for_builder: bool = False, depth: int = 0, home_id: Optional[int] = None):
    is_home = home_id is not None and folder["id"] == home_id
    with st.expander(f"📁 {folder['name']}", expanded=is_home):
        if not for_builder:
            act_cols = st.columns(4)
            # New folder
            if act_cols[0].button("New folder", key=f"nf_btn_{folder['id']}"):
                st.session_state[f"dlg_new_folder_{folder['id']}"] = True
            # New component (disable here if you don't want components at home root)
            if act_cols[1].button("New component", key=f"nc_btn_{folder['id']}"):
                st.session_state[f"dlg_new_comp_{folder['id']}"] = True
            # Rename/Delete only for non-home
            if not is_home:
                if act_cols[2].button("Rename", key=f"rf_btn_{folder['id']}"):
                    st.session_state[f"dlg_rename_folder_{folder['id']}"] = True
                # Put delete button just below to avoid accidental clicks next to rename
                if act_cols[3].button("Delete folder", key=f"df_btn_{folder['id']}"):
                    st.session_state[f"dlg_del_folder_{folder['id']}"] = True

            # Render folder dialogs
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
                render_component_item(conn, c, for_builder=for_builder)
        else:
            st.caption("No components here yet.")

        # Subfolders
        children = list_folders_by_parent(conn, folder["id"])
        if children:
            st.markdown("**Subfolders**")
            for child in children:
                render_folder_node(conn, child, for_builder=for_builder, depth=depth + 1, home_id=home_id)

def render_root_section(conn: sqlite3.Connection, for_builder: bool = False):
    home_id = get_home_folder_id(conn)
    home = get_folder(conn, home_id)

    render_folder_node(conn, home, for_builder=for_builder, depth=0, home_id=home_id)

# -------------------------------
# Dialog Helpers
# -------------------------------
def _close(flag_key: str):
    st.session_state[flag_key] = False
    st.session_state["pp_epoch"] = st.session_state.get("pp_epoch", 0) + 1
    st.rerun()

def _pp_label(base: str = "…") -> str:
    # append 0–6 zero-width spaces so the label looks the same but is a new element
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
            st.error(
                "This will permanently delete this folder, all subfolders, and all components within them.",
                icon="⚠️",
            )
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
            # default index to current folder
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
                if st.session_state.selected_component_id == comp_id:
                    st.session_state.selected_component_id = None
                st.session_state.builder_list = [cid for cid in st.session_state.builder_list if cid != comp_id]
                _close(flag)
            elif no:
                _close(flag)
        _dlg()

# -------------------------------
# Tabs
# -------------------------------
conn = get_conn()

st.radio(
    "View",
    options=[TAB_LABELS["components"], TAB_LABELS["builder"]],
    horizontal=True,
    label_visibility="collapsed",
    key="tab_picker",                # radio value lives here
    on_change=_sync_tab_from_radio,  # syncs active_tab + URL
)

# -----------------------------------
# Tab 1: Components (edit & organize)
# -----------------------------------
if st.session_state.active_tab == "components":
    left, right = st.columns([5,7], gap="large")

    with left:
        st.subheader("Edit & Organize Components")
        render_root_section(conn, for_builder=False)

    with right:
        st.subheader("Editor")
        comp_id = st.session_state.selected_component_id
        if comp_id is None:
            st.info("Select a component on the left to edit its name, folder, and content.")
        else:
            comp = get_component(conn, comp_id)
            if comp is None:
                st.warning("Selected component no longer exists.")
            else:
                with st.form(key=f"edit_component_form_{comp_id}"):
                    new_name = st.text_input("Component name", value=comp["name"])
                    folder_options = all_folders_with_paths(conn)
                    # Find index for current folder
                    idx = 0
                    for i, (fid, _) in enumerate(folder_options):
                        if fid == comp["folder_id"]:
                            idx = i
                            break
                    dest = st.selectbox("Folder", options=folder_options, index=idx, format_func=lambda x: x[1])
                    content = st.text_area("Content", value=comp["content"], height=200)
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.form_submit_button("💾 Save"):
                            # Apply updates
                            if new_name.strip() != comp["name"]:
                                rename_component(conn, comp_id, new_name.strip())
                            if dest[0] != comp["folder_id"]:
                                move_component(conn, comp_id, dest[0])
                            if content != comp["content"]:
                                update_component_content(conn, comp_id, content)
                            st.success("Saved.")
                            st.rerun()
                    with c2:
                        if st.form_submit_button("🗑️ Delete", type="primary"):
                            delete_component(conn, comp_id)
                            st.success("Component deleted.")
                            # Remove from builder
                            st.session_state.builder_list = [cid for cid in st.session_state.builder_list if cid != comp_id]
                            st.session_state.selected_component_id = None
                            st.rerun()

# -----------------------------------
# Tab 2: Prompt Builder
# -----------------------------------
else:
    left, right = st.columns([5,7], gap="large")

    with left:
        st.subheader("Pick Components")
        st.caption("Click **Add** on any component to append it to the builder list.")
        render_root_section(conn, for_builder=True)

    with right:
        st.subheader("Assembled Prompt")
        if not st.session_state.builder_list:
            st.info("No components added yet. Use **Add** on the left.")
        else:
            # Controls for the queue
            st.markdown("**Order & Manage**")
            for i, cid in enumerate(st.session_state.builder_list):
                c = get_component(conn, cid)
                if not c:
                    # Clean up missing ones
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

            st.button("Clear All", on_click=clear_builder)

            # Build prompt text (two carriage returns between components)
            parts: List[str] = []
            for cid in st.session_state.builder_list:
                c = get_component(conn, cid)
                if c:
                    parts.append(c["content"])
            prompt_text = "\n\n".join(parts)

            st.markdown("**Prompt Preview**")
            # st.code shows a copy button by default
            st.code(prompt_text or "", language=None)
            st.download_button("Download as .txt", data=prompt_text, file_name="prompt.txt", mime="text/plain")
