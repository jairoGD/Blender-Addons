bl_info = {
    "name": "Project Time Tracker",
    "author": "Jairo",
    "version": (1, 3, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Project Time",
    "description": "Tracks project work time after the first save, with log and automatic screenshots.",
    "category": "System",
}

import bpy
import time
import os
import re
from datetime import datetime
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.app.handlers import persistent


# ------------------------------------------------------------
# Timing constants
#
# The clock lives in a *persistent* bpy.app timer (the watchdog), not inside the
# modal operator.  Blender removes non-persistent app timers and cancels modal
# operators every time a .blend is loaded, so anything that depends on them
# being alive is unreliable - that is what used to freeze the counter.
# ------------------------------------------------------------

WATCHDOG_INTERVAL = 0.5     # seconds between watchdog ticks
MODAL_TICK = 0.5            # interval of the activity-detection modal timer
HEARTBEAT_TIMEOUT = 5.0     # no modal tick for this long -> modal is dead
RESTART_BACKOFF = 2.0       # seconds between modal (re)start attempts
GENERIC_EVENT_TYPES = {"TIMER", "NONE"}


# ------------------------------------------------------------
# Shared tracker state
# ------------------------------------------------------------

class PTT_State:
    """Module level state shared by the watchdog, the modal and the panel."""

    modal_running = False      # a modal activity listener is alive
    generation = 0             # bumped on every (re)start: stale modals bail out
    timer = None               # event timer handle owned by the live modal

    heartbeat = 0.0            # monotonic time of the last modal tick
    last_activity = 0.0        # monotonic time of the last user input
    last_count = 0.0           # clock is counted up to this monotonic time

    last_log_save = 0.0
    last_screenshot = 0.0
    last_start_attempt = 0.0

    auto_start_pending = False
    last_known_filepath = ""


def ptt_now():
    return time.monotonic()


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def format_seconds(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def format_seconds_display(seconds: float) -> str:
    return format_seconds(seconds).replace("-", ":")


def get_blend_filepath():
    # bpy.data is a restricted object while addons register/load, so guard it.
    try:
        return bpy.data.filepath or ""
    except Exception:
        return ""


def is_blend_saved():
    return bool(get_blend_filepath())


def get_project_folder():
    filepath = get_blend_filepath()
    if not filepath:
        return None
    return os.path.dirname(filepath)


def get_project_name():
    filepath = get_blend_filepath()
    if not filepath:
        return "Untitled"
    return os.path.splitext(os.path.basename(filepath))[0]


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip()
    return name if name else "Untitled"


def tag_redraw_all_view3d():
    try:
        wm = bpy.context.window_manager
    except Exception:
        return

    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def get_log_filepath():
    folder = get_project_folder()
    if not folder:
        return None

    project_name = sanitize_filename(get_project_name())
    return os.path.join(folder, f"{project_name}_project_time.log")


def get_screenshot_folder():
    folder = get_project_folder()
    if not folder:
        return None

    screenshot_folder = os.path.join(folder, "screenshots")
    os.makedirs(screenshot_folder, exist_ok=True)
    return screenshot_folder


def get_next_screenshot_index(folder, project_name):
    max_index = 0

    if not os.path.exists(folder):
        return 1

    pattern = re.compile(rf"^{re.escape(project_name)}_screenshot_(\d+)_")

    for filename in os.listdir(folder):
        match = pattern.match(filename)
        if match:
            try:
                index = int(match.group(1))
                max_index = max(max_index, index)
            except Exception:
                pass

    return max_index + 1


def write_log_file(scene):
    if scene is None or not hasattr(scene, "ptt_settings"):
        return False

    settings = scene.ptt_settings
    filepath = get_log_filepath()

    if not filepath:
        settings.last_log_status = "Save the .blend first"
        settings.last_log_error = ""
        return False

    try:
        project_name = get_project_name()
        elapsed = settings.elapsed_seconds
        elapsed_text = format_seconds_display(elapsed)
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        content = (
            f"Project Time Log\n"
            f"================\n\n"
            f"Project Name: {project_name}\n"
            f"Blend File: {bpy.data.filepath}\n"
            f"Elapsed Time: {elapsed_text}\n"
            f"Elapsed Seconds: {int(elapsed)}\n"
            f"Last Updated: {now_text}\n"
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        settings.last_log_status = f"Log saved: {os.path.basename(filepath)}"
        settings.last_log_error = ""
        return True

    except Exception as e:
        settings.last_log_status = "Failed to save log"
        settings.last_log_error = str(e)
        return False


def take_screenshot(scene):
    if scene is None or not hasattr(scene, "ptt_settings"):
        return False

    settings = scene.ptt_settings

    if not settings.enable_screenshots:
        return False

    folder = get_screenshot_folder()

    if not folder:
        settings.last_screenshot_status = "Save the .blend first"
        settings.last_screenshot_error = ""
        return False

    try:
        project_name = sanitize_filename(get_project_name())
        index = get_next_screenshot_index(folder, project_name)
        elapsed_text = format_seconds(settings.elapsed_seconds)

        filename = f"{project_name}_screenshot_{index:04d}_{elapsed_text}.png"
        filepath = os.path.join(folder, filename)

        bpy.ops.screen.screenshot(filepath=filepath)

        settings.last_screenshot_status = f"Screenshot saved: {filename}"
        settings.last_screenshot_error = ""
        return True

    except Exception as e:
        settings.last_screenshot_status = "Failed to save screenshot"
        settings.last_screenshot_error = str(e)
        return False


# ------------------------------------------------------------
# Tracker state handling
# ------------------------------------------------------------

def ptt_is_modal_alive():
    """True only when the modal activity listener is ticking right now."""
    if not PTT_State.modal_running:
        return False

    heartbeat = PTT_State.heartbeat
    return heartbeat > 0.0 and (ptt_now() - heartbeat) < HEARTBEAT_TIMEOUT


def ptt_reset_modal_state():
    """Forget the modal listener and return its event timer handle, if any."""
    PTT_State.modal_running = False
    PTT_State.heartbeat = 0.0
    timer = PTT_State.timer
    PTT_State.timer = None
    return timer


def ptt_drop_stale_modal(context):
    """
    Clean up after a modal operator that died without notice.

    Blender cancels modal operators on file load, on window close and on Python
    errors.  `cancel()` normally handles that, but a stale flag/heartbeat is
    still possible, so the watchdog always clears the leftovers before starting
    a new instance.
    """
    PTT_State.generation += 1

    timer = ptt_reset_modal_state()

    if timer is not None and context is not None:
        try:
            context.window_manager.event_timer_remove(timer)
        except Exception:
            pass


def ptt_stop_modal(context):
    """Stop the modal listener (keeps elapsed_seconds untouched)."""
    ptt_drop_stale_modal(context)
    tag_redraw_all_view3d()


def ptt_note_activity(settings, now=None):
    """Register user activity; the idle gap is never counted."""
    now = ptt_now() if now is None else now

    if now - PTT_State.last_activity >= settings.inactivity_timeout:
        # Coming back from an idle period: restart counting from now.
        PTT_State.last_count = now

    PTT_State.last_activity = now


def ptt_advance_clock(settings, now=None):
    """
    Add the time that passed since the previous tick.

    Time is only counted while the user was active, and never past
    last_activity + inactivity_timeout.
    """
    now = ptt_now() if now is None else now

    active_until = PTT_State.last_activity + settings.inactivity_timeout
    count_until = min(now, active_until)

    delta = count_until - PTT_State.last_count

    if delta > 0.0:
        settings.elapsed_seconds += delta
        PTT_State.last_count = count_until

    return delta


def ptt_request_modal_start():
    """(Re)start the modal listener. Kept separate so it can be stubbed in tests."""
    bpy.ops.wm.ptt_start_tracker()
    return True


def ptt_ensure_modal(context, settings, now=None):
    """Make sure the modal activity listener is alive while tracking."""
    now = ptt_now() if now is None else now

    if ptt_is_modal_alive():
        return True

    if now - PTT_State.last_start_attempt < RESTART_BACKOFF:
        return False

    PTT_State.last_start_attempt = now

    try:
        ptt_request_modal_start()
    except Exception:
        return False

    return ptt_is_modal_alive()


def ptt_periodic_tasks(context, settings, now=None):
    """Automatic log writing and screenshots (used to live in the modal loop)."""
    now = ptt_now() if now is None else now

    if now - PTT_State.last_log_save >= settings.auto_log_interval:
        write_log_file(context.scene)
        PTT_State.last_log_save = now

    is_active = (now - PTT_State.last_activity) < settings.inactivity_timeout

    if is_active and now - PTT_State.last_screenshot >= settings.screenshot_interval:
        take_screenshot(context.scene)
        PTT_State.last_screenshot = now


def ptt_current_status(context):
    settings = context.scene.ptt_settings

    if not is_blend_saved():
        return "Waiting First Save"

    if not settings.running:
        return "Paused"

    if not ptt_is_modal_alive():
        return "Restarting..."

    now = ptt_now()

    if now - PTT_State.last_activity >= settings.inactivity_timeout:
        return "Inactive"

    return "Running"


# ------------------------------------------------------------
# Properties saved inside .blend
# ------------------------------------------------------------

class PTT_Settings(PropertyGroup):
    elapsed_seconds: FloatProperty(
        name="Elapsed Time",
        description="Total tracked time in seconds",
        default=0.0,
    )

    running: BoolProperty(
        name="Running",
        description="Whether the tracker is currently running",
        default=False,
    )

    auto_start: BoolProperty(
        name="Auto Start",
        description="Automatically start tracking after first save or when a saved file is opened",
        default=True,
    )

    inactivity_timeout: IntProperty(
        name="Inactivity Timeout",
        description="Pause tracking after this many seconds without mouse movement",
        default=10,
        min=1,
        max=3600,
    )

    auto_log_interval: IntProperty(
        name="Auto Log Interval",
        description="Automatically save log every X seconds",
        default=60,
        min=10,
        max=3600,
    )

    enable_screenshots: BoolProperty(
        name="Auto Screenshots",
        description="Automatically save screenshots while tracking",
        default=True,
    )

    screenshot_interval: IntProperty(
        name="Screenshot Interval",
        description="Take one screenshot every X seconds",
        default=60,
        min=10,
        max=3600,
    )

    last_log_status: StringProperty(
        name="Last Log Status",
        default="",
    )

    last_log_error: StringProperty(
        name="Last Log Error",
        default="",
    )

    last_screenshot_status: StringProperty(
        name="Last Screenshot Status",
        default="",
    )

    last_screenshot_error: StringProperty(
        name="Last Screenshot Error",
        default="",
    )


# ------------------------------------------------------------
# Modal activity listener
#
# Its only job is to report user activity.  The clock itself is advanced by the
# watchdog timer, so this operator being cancelled (file load, window close,
# another modal operator taking over, a long render) can no longer freeze or
# lose tracked time.
# ------------------------------------------------------------

class PTT_OT_start_tracker(Operator):
    bl_idname = "wm.ptt_start_tracker"
    bl_label = "Start Project Timer"
    bl_description = "Start tracking project time"
    bl_options = {'REGISTER'}

    _generation = 0

    @classmethod
    def current_status(cls, context):
        return ptt_current_status(context)

    @classmethod
    def stop_modal(cls, context):
        ptt_stop_modal(context)

    def execute(self, context):
        now = ptt_now()

        if not is_blend_saved():
            context.scene.ptt_settings.running = False
            context.scene.ptt_settings.last_log_status = "Waiting first save"
            tag_redraw_all_view3d()
            return {'CANCELLED'}

        settings = context.scene.ptt_settings
        settings.running = True

        if ptt_is_modal_alive():
            # Already ticking: do not create a second listener.
            ptt_note_activity(settings, now)
            PTT_State.last_start_attempt = now
            tag_redraw_all_view3d()
            return {'FINISHED'}

        if context.window is None:
            # Background mode has no window to listen to.
            return {'CANCELLED'}

        # Drop whatever a dead modal instance left behind.
        ptt_drop_stale_modal(context)

        PTT_State.generation += 1
        self._generation = PTT_State.generation
        PTT_State.modal_running = True
        PTT_State.heartbeat = now
        PTT_State.last_start_attempt = now
        PTT_State.last_activity = now
        PTT_State.last_count = now
        PTT_State.last_log_save = now
        PTT_State.last_screenshot = now

        try:
            PTT_State.timer = context.window_manager.event_timer_add(
                MODAL_TICK, window=context.window
            )
            context.window_manager.modal_handler_add(self)
        except Exception:
            ptt_drop_stale_modal(context)
            return {'CANCELLED'}

        write_log_file(context.scene)
        tag_redraw_all_view3d()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # A newer instance (or a pause) took over: leave the event loop.
        if self._generation != PTT_State.generation or not PTT_State.modal_running:
            return {'CANCELLED'}

        settings = context.scene.ptt_settings if context.scene else None

        if settings is None or not settings.running:
            write_log_file(context.scene)
            ptt_stop_modal(context)
            return {'FINISHED'}

        now = ptt_now()
        PTT_State.heartbeat = now

        if event.type not in GENERIC_EVENT_TYPES:
            # Any real input counts as activity: mouse, keyboard, wheel, tablet.
            ptt_note_activity(settings, now)
            tag_redraw_all_view3d()

        return {'PASS_THROUGH'}

    def cancel(self, context):
        # Blender calls this when it cancels the modal operator (file load,
        # window close, ESC, errors). Without it the "modal is running" flag
        # stayed True forever and the watchdog never restarted the tracker.
        if self._generation == PTT_State.generation:
            ptt_reset_modal_state()
        return None


# ------------------------------------------------------------
# Operators
# ------------------------------------------------------------

class PTT_OT_pause_tracker(Operator):
    bl_idname = "wm.ptt_pause_tracker"
    bl_label = "Pause Project Timer"
    bl_description = "Pause tracking project time"
    bl_options = {'REGISTER'}

    def execute(self, context):
        context.scene.ptt_settings.running = False
        write_log_file(context.scene)
        PTT_OT_start_tracker.stop_modal(context)
        return {'FINISHED'}


class PTT_OT_reset_tracker(Operator):
    bl_idname = "wm.ptt_reset_tracker"
    bl_label = "Reset Project Timer"
    bl_description = "Reset tracked project time"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.ptt_settings.elapsed_seconds = 0.0
        write_log_file(context.scene)
        tag_redraw_all_view3d()
        return {'FINISHED'}


class PTT_OT_save_log_now(Operator):
    bl_idname = "wm.ptt_save_log_now"
    bl_label = "Save Log Now"
    bl_description = "Save the project time log now"
    bl_options = {'REGISTER'}

    def execute(self, context):
        ok = write_log_file(context.scene)

        if ok:
            self.report({'INFO'}, "Project time log saved")
        else:
            self.report({'WARNING'}, context.scene.ptt_settings.last_log_status)

        tag_redraw_all_view3d()
        return {'FINISHED'}


class PTT_OT_take_screenshot_now(Operator):
    bl_idname = "wm.ptt_take_screenshot_now"
    bl_label = "Take Screenshot Now"
    bl_description = "Save a screenshot now"
    bl_options = {'REGISTER'}

    def execute(self, context):
        ok = take_screenshot(context.scene)

        if ok:
            self.report({'INFO'}, "Screenshot saved")
        else:
            self.report({'WARNING'}, context.scene.ptt_settings.last_screenshot_status)

        tag_redraw_all_view3d()
        return {'FINISHED'}


# ------------------------------------------------------------
# Panel
# ------------------------------------------------------------

class PTT_PT_panel(Panel):
    bl_label = "Project Time Tracker"
    bl_idname = "PTT_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Project Time"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ptt_settings

        status = ptt_current_status(context)
        project_folder = get_project_folder()

        box = layout.box()
        box.label(text="Tracked Time")
        box.label(text=format_seconds_display(settings.elapsed_seconds), icon='TIME')
        box.label(text=f"Status: {status}")

        if not project_folder:
            box.label(text="Timer starts after first save.", icon='ERROR')

        layout.separator()

        row = layout.row(align=True)

        if settings.running:
            row.operator("wm.ptt_pause_tracker", text="Pause", icon='PAUSE')
        else:
            row.operator("wm.ptt_start_tracker", text="Start", icon='PLAY')

        row.operator("wm.ptt_reset_tracker", text="Reset", icon='LOOP_BACK')

        layout.separator()

        layout.prop(settings, "inactivity_timeout")
        layout.prop(settings, "auto_start")

        layout.separator()

        log_box = layout.box()
        log_box.label(text="Auto Log", icon='TEXT')
        log_box.prop(settings, "auto_log_interval")
        log_box.operator("wm.ptt_save_log_now", icon='FILE_TICK')

        if project_folder:
            log_box.label(text=f"Folder: {project_folder}")

        if settings.last_log_status:
            log_box.label(text=settings.last_log_status, icon='INFO')

        if settings.last_log_error:
            log_box.label(text=settings.last_log_error, icon='ERROR')

        layout.separator()

        shot_box = layout.box()
        shot_box.label(text="Auto Screenshots", icon='IMAGE_DATA')
        shot_box.prop(settings, "enable_screenshots")
        shot_box.prop(settings, "screenshot_interval")
        shot_box.operator("wm.ptt_take_screenshot_now", icon='RENDER_STILL')

        if project_folder:
            shot_box.label(text="Folder: screenshots")

        if settings.last_screenshot_status:
            shot_box.label(text=settings.last_screenshot_status, icon='INFO')

        if settings.last_screenshot_error:
            shot_box.label(text=settings.last_screenshot_error, icon='ERROR')

        if status == "Inactive":
            layout.separator()
            layout.label(text="Paused by inactivity.", icon='INFO')
        elif status == "Restarting...":
            layout.separator()
            layout.label(text="Reconnecting tracker...", icon='FILE_REFRESH')


# ------------------------------------------------------------
# Watchdog timer
# ------------------------------------------------------------

def ptt_watchdog(context=None, now=None):
    """
    Persistent timer that owns the clock and keeps the tracker alive.

    Persistent means Blender keeps it across file loads (non-persistent timers
    are removed whenever a .blend is opened).  It runs every WATCHDOG_INTERVAL
    seconds and:
      * advances elapsed_seconds while the user is active,
      * writes the log / takes screenshots on schedule,
      * starts or restarts the modal listener when it died for any reason.

    `context` and `now` are optional hooks for automated tests.
    """
    if bpy.app.background:
        return WATCHDOG_INTERVAL

    if context is None:
        try:
            context = bpy.context
        except Exception:
            return WATCHDOG_INTERVAL

    window = getattr(context, "window", None)
    scene = getattr(context, "scene", None)

    if window is None or scene is None or not hasattr(scene, "ptt_settings"):
        return WATCHDOG_INTERVAL

    settings = scene.ptt_settings

    if not is_blend_saved():
        # Nothing is tracked until the .blend has a path on disk.
        if PTT_State.modal_running:
            ptt_stop_modal(context)
        return WATCHDOG_INTERVAL

    now = ptt_now() if now is None else now

    if PTT_State.auto_start_pending:
        PTT_State.auto_start_pending = False

        if settings.auto_start and not settings.running:
            settings.running = True
            PTT_State.last_activity = now
            PTT_State.last_count = now
            tag_redraw_all_view3d()

    if settings.running:
        if PTT_State.last_count <= 0.0:
            PTT_State.last_count = now

        if ptt_advance_clock(settings, now) > 0.0:
            tag_redraw_all_view3d()

        ptt_periodic_tasks(context, settings, now)
        ptt_ensure_modal(context, settings, now)

    return WATCHDOG_INTERVAL


# ------------------------------------------------------------
# Handlers
# ------------------------------------------------------------

@persistent
def ptt_load_post_handler(dummy):
    """A .blend was opened: arm auto-start and clean up after the previous file."""
    PTT_State.auto_start_pending = True
    PTT_State.last_known_filepath = get_blend_filepath()

    try:
        # Blender already killed the modal operator of the previous file.
        ptt_drop_stale_modal(bpy.context)
    except Exception:
        pass


@persistent
def ptt_save_post_handler(dummy):
    scene = bpy.context.scene

    if scene is None or not hasattr(scene, "ptt_settings"):
        return

    write_log_file(scene)

    filepath = get_blend_filepath()

    # First save of this file (or Save As): start tracking automatically.
    if filepath and filepath != PTT_State.last_known_filepath:
        PTT_State.last_known_filepath = filepath
        PTT_State.auto_start_pending = True


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    PTT_Settings,
    PTT_OT_start_tracker,
    PTT_OT_pause_tracker,
    PTT_OT_reset_tracker,
    PTT_OT_save_log_now,
    PTT_OT_take_screenshot_now,
    PTT_PT_panel,
)


def register():
    # Running the file twice (Text Editor > Run Script, or addon reload) must not
    # fail with "already registered" errors.
    if hasattr(bpy.types.Scene, "ptt_settings"):
        try:
            unregister()
        except Exception:
            pass

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ptt_settings = PointerProperty(type=PTT_Settings)

    if ptt_load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(ptt_load_post_handler)

    if ptt_save_post_handler not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(ptt_save_post_handler)

    PTT_State.last_known_filepath = get_blend_filepath()

    # persistent=True: this timer must survive opening a .blend.
    if not bpy.app.timers.is_registered(ptt_watchdog):
        bpy.app.timers.register(ptt_watchdog, first_interval=1.0, persistent=True)


def unregister():
    if ptt_load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(ptt_load_post_handler)

    if ptt_save_post_handler in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(ptt_save_post_handler)

    if bpy.app.timers.is_registered(ptt_watchdog):
        bpy.app.timers.unregister(ptt_watchdog)

    try:
        if bpy.context.scene and hasattr(bpy.context.scene, "ptt_settings"):
            write_log_file(bpy.context.scene)
    except Exception:
        pass

    try:
        ptt_stop_modal(bpy.context)
    except Exception:
        pass

    if hasattr(bpy.types.Scene, "ptt_settings"):
        del bpy.types.Scene.ptt_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
