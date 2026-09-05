bl_info = {
    "name": "Eevee 360 Render",
    "author": "OpenAI",
    "version": (3, 2, 2),
    "blender": (4, 2, 0),
    "location": "Properties > Output > Eevee 360 Render",
    "description": "Render equirectangular 360 images and videos with Eevee",
    "category": "Render",
}

import gc
import math
import os
import re
import shutil
import tempfile
import traceback

import bpy
import numpy as np
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector


FACE_NAMES = ("FRONT", "RIGHT", "BACK", "LEFT", "UP", "DOWN")

# A Blender camera looks along local -Z, with local +Y pointing upward.
FACE_ROTATIONS = {
    "FRONT": Matrix.Identity(4),
    "RIGHT": Matrix.Rotation(-math.pi / 2.0, 4, "Y"),
    "BACK": Matrix.Rotation(math.pi, 4, "Y"),
    "LEFT": Matrix.Rotation(math.pi / 2.0, 4, "Y"),
    "UP": Matrix.Rotation(math.pi / 2.0, 4, "X"),
    "DOWN": Matrix.Rotation(-math.pi / 2.0, 4, "X"),
}

_ACTIVE_OPERATOR = None


def redraw_ui():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return

    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            area.tag_redraw()


def remove_handler(handler_list, callback):
    if callback is not None and callback in handler_list:
        handler_list.remove(callback)


def safe_prefix(value):
    value = (value or "pano360").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.rstrip(" .")
    return value if value and value not in {".", ".."} else "pano360"


def even_multiple_of_four(value):
    return max(4, ((int(value) + 3) // 4) * 4)


class E360_Properties(PropertyGroup):
    output_dir: StringProperty(
        name="Output Folder",
        description="Folder used for panoramas and the final MP4",
        subtype="DIR_PATH",
        default="//360_render/",
    )

    prefix: StringProperty(
        name="File Prefix",
        default="pano360",
    )

    width: IntProperty(
        name="Panorama Width",
        description="Output width; it is rounded up to a multiple of four for H.264",
        default=4096,
        min=512,
        max=16384,
    )

    face_size: IntProperty(
        name="Cube Face Size",
        description="Zero automatically uses one quarter of the panorama width",
        default=0,
        min=0,
        max=8192,
    )

    rows_per_tick: IntProperty(
        name="Stitch Rows Per Update",
        description="Higher values stitch faster but make Blender's interface less responsive",
        default=32,
        min=8,
        max=128,
    )

    render_method: EnumProperty(
        name="Face Render Mode",
        description="Choose final Eevee rendering or Blender's viewport/OpenGL renderer",
        items=(
            (
                "FINAL",
                "Eevee Final Render",
                "Use the regular Eevee final renderer for every cube face",
            ),
            (
                "VIEWPORT",
                "Viewport Render Animation",
                "Capture the actual 3D Viewport shading, including Rendered and Material Preview",
            ),
        ),
        default="FINAL",
    )

    use_compositor: BoolProperty(
        name="Use Compositor for Cube Faces",
        description="Run the scene compositor separately for each of the six cube faces",
        default=False,
    )

    delete_pngs: BoolProperty(
        name="Delete PNG Frames After MP4",
        description="Remove the generated panorama sequence after successful video encoding",
        default=False,
    )

    quality: EnumProperty(
        name="MP4 Quality",
        items=(
            ("PERC_LOSSLESS", "Perceptually Lossless", "Largest file"),
            ("HIGH", "High", "Recommended quality"),
            ("MEDIUM", "Medium", "Smaller file"),
            ("LOW", "Low", "Smallest file"),
        ),
        default="HIGH",
    )

    running: BoolProperty(default=False, options={"SKIP_SAVE"})
    cancel_requested: BoolProperty(default=False, options={"SKIP_SAVE"})
    progress: FloatProperty(
        default=0.0,
        min=0.0,
        max=1.0,
        options={"SKIP_SAVE"},
    )
    status: StringProperty(default="Ready", options={"SKIP_SAVE"})


class E360_OT_Cancel(Operator):
    bl_idname = "render.e360_cancel"
    bl_label = "Cancel 360 Render"
    bl_description = "Stop after the current cube-face render; Esc also cancels Blender's active render"

    def execute(self, context):
        props = context.scene.e360
        props.cancel_requested = True
        props.status = "Cancelling after the current face..."
        redraw_ui()
        return {"FINISHED"}


class E360_OT_Render(Operator):
    bl_idname = "render.e360_render"
    bl_label = "Render Eevee 360"
    bl_options = {"REGISTER"}

    mode: EnumProperty(
        name="Mode",
        items=(
            ("STILL", "Still", "Render the current frame"),
            ("SEQUENCE", "Sequence", "Render the scene frame range to PNG files"),
            ("VIDEO", "Video", "Render the scene frame range and encode an MP4"),
        ),
        default="STILL",
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        return (
            context.scene is not None
            and context.scene.camera is not None
            and not context.scene.e360.running
            and _ACTIVE_OPERATOR is None
        )

    def invoke(self, context, event):
        global _ACTIVE_OPERATOR

        self.scene = context.scene
        self.props = self.scene.e360
        self.wm = context.window_manager
        self.source_camera = self.scene.camera
        self.timer = None
        self.encoder_scene = None
        self.cameras = {}
        self.faces = {}
        self.pngs = []
        self.temp_dir = None
        self.panorama = None
        self.sin_lon = None
        self.cos_lon = None
        self.backup = None
        self.cleaned = False
        self.handlers_installed = False

        try:
            self.pano_width = even_multiple_of_four(self.props.width)
            self.pano_height = self.pano_width // 2
            self.cube_size = self.props.face_size or max(128, self.pano_width // 4)
            self.file_prefix = safe_prefix(self.props.prefix)

            if self.mode == "STILL":
                self.frames = [self.scene.frame_current]
            else:
                self.frames = list(
                    range(
                        self.scene.frame_start,
                        self.scene.frame_end + 1,
                        max(1, self.scene.frame_step),
                    )
                )

            if not self.frames:
                raise RuntimeError("The selected frame range is empty")

            output = bpy.path.abspath(self.props.output_dir).strip()
            if not output:
                output = os.path.join(tempfile.gettempdir(), "eevee_360_render")
            self.output_dir = os.path.normpath(output)
            os.makedirs(self.output_dir, exist_ok=True)
            if not os.path.isdir(self.output_dir):
                raise RuntimeError("The output path is not a folder")

            # Keep generated frames organized and make repeated encodes easy to
            # inspect. The final MP4 remains in the user-selected root folder.
            self.frames_dir = os.path.join(
                self.output_dir,
                f"{self.file_prefix}_frames",
            )
            os.makedirs(self.frames_dir, exist_ok=True)

            self.temp_dir = tempfile.mkdtemp(prefix="eevee360_")
            self.frame_index = 0
            self.face_index = 0
            self.row = 0
            self.wait_ticks = 0
            self.encoded_frames = 0
            self.state = "START_FRAME"
            self.face_done = False
            self.face_cancelled = False
            self.encode_done = False
            self.encode_cancelled = False

            # Six face renders plus one stitching/saving unit per frame, and one
            # additional unit per frame while encoding a video.
            self.render_units = len(self.frames) * 7
            self.total_units = self.render_units + (
                len(self.frames) if self.mode == "VIDEO" else 0
            )

            self.backup_settings()
            self.force_eevee()
            self.create_cameras()
            self.install_handlers()

            _ACTIVE_OPERATOR = self
            self.props.running = True
            self.props.cancel_requested = False
            self.wm.progress_begin(0.0, 1.0)
            self.set_progress(0.0, "Preparing Eevee 360 render...")

            self.timer = self.wm.event_timer_add(0.08, window=context.window)
            self.wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        except Exception as error:
            traceback.print_exc()
            self.cleanup()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.props.cancel_requested = True
            self.props.status = "Cancelling..."
            # Let Blender consume Esc as well when its render window is active.
            return {"PASS_THROUGH"}

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        try:
            if self.face_cancelled or self.encode_cancelled:
                return self.finish(True, "360 render cancelled")

            if self.props.cancel_requested and self.state not in {
                "WAIT_FACE",
                "WAIT_ENCODE",
            }:
                return self.finish(True, "360 render cancelled")

            state_function = getattr(self, "state_" + self.state.lower())
            result = state_function(context)
            redraw_ui()
            return result or {"RUNNING_MODAL"}

        except Exception as error:
            traceback.print_exc()
            return self.finish(True, f"{type(error).__name__}: {error}")

    # Render callbacks are used only as signals. File and job state are still
    # checked by the modal loop, avoiding a race with Blender finishing a write.
    def install_handlers(self):
        def complete(scene, *args):
            if scene == self.scene and self.state == "WAIT_FACE":
                self.face_done = True
            elif scene == self.encoder_scene and self.state == "WAIT_ENCODE":
                self.encode_done = True

        def cancel(scene, *args):
            if scene == self.scene and self.state == "WAIT_FACE":
                self.face_cancelled = True
            elif scene == self.encoder_scene and self.state == "WAIT_ENCODE":
                self.encode_cancelled = True

        def post(scene, *args):
            if scene == self.encoder_scene and self.state == "WAIT_ENCODE":
                self.encoded_frames = min(len(self.frames), self.encoded_frames + 1)
                progress = (
                    self.render_units + self.encoded_frames
                ) / self.total_units
                self.set_progress(
                    progress,
                    f"Encoding MP4 {self.encoded_frames}/{len(self.frames)}",
                )

        self.handler_complete = complete
        self.handler_cancel = cancel
        self.handler_post = post
        bpy.app.handlers.render_complete.append(complete)
        bpy.app.handlers.render_cancel.append(cancel)
        bpy.app.handlers.render_post.append(post)
        self.handlers_installed = True

    def remove_handlers(self):
        remove_handler(
            bpy.app.handlers.render_complete,
            getattr(self, "handler_complete", None),
        )
        remove_handler(
            bpy.app.handlers.render_cancel,
            getattr(self, "handler_cancel", None),
        )
        remove_handler(
            bpy.app.handlers.render_post,
            getattr(self, "handler_post", None),
        )
        self.handlers_installed = False

    def backup_settings(self):
        render = self.scene.render
        image = render.image_settings
        self.backup = {
            "engine": render.engine,
            "camera": self.scene.camera,
            "frame": self.scene.frame_current,
            "filepath": render.filepath,
            "resolution_x": render.resolution_x,
            "resolution_y": render.resolution_y,
            "resolution_percentage": render.resolution_percentage,
            "pixel_aspect_x": render.pixel_aspect_x,
            "pixel_aspect_y": render.pixel_aspect_y,
            "use_border": render.use_border,
            "use_crop_to_border": render.use_crop_to_border,
            "use_multiview": render.use_multiview,
            "use_sequencer": render.use_sequencer,
            "use_compositing": render.use_compositing,
            "use_file_extension": render.use_file_extension,
            "use_lock_interface": render.use_lock_interface,
            "file_format": image.file_format,
            "color_mode": image.color_mode,
            "color_depth": image.color_depth,
            "exr_codec": getattr(image, "exr_codec", None),
        }

    def restore_settings(self):
        if not self.backup or self.scene is None:
            return

        data = self.backup
        render = self.scene.render
        image = render.image_settings
        render.engine = data["engine"]
        self.scene.camera = data["camera"]
        self.scene.frame_set(data["frame"])
        render.filepath = data["filepath"]
        render.resolution_x = data["resolution_x"]
        render.resolution_y = data["resolution_y"]
        render.resolution_percentage = data["resolution_percentage"]
        render.pixel_aspect_x = data["pixel_aspect_x"]
        render.pixel_aspect_y = data["pixel_aspect_y"]
        render.use_border = data["use_border"]
        render.use_crop_to_border = data["use_crop_to_border"]
        render.use_multiview = data["use_multiview"]
        render.use_sequencer = data["use_sequencer"]
        render.use_compositing = data["use_compositing"]
        render.use_file_extension = data["use_file_extension"]
        render.use_lock_interface = data["use_lock_interface"]
        image.file_format = data["file_format"]
        image.color_mode = data["color_mode"]
        image.color_depth = data["color_depth"]
        if data["exr_codec"] is not None and hasattr(image, "exr_codec"):
            image.exr_codec = data["exr_codec"]

    def force_eevee(self):
        # Blender 4.2 uses BLENDER_EEVEE_NEXT. Newer builds may expose the
        # engine again as BLENDER_EEVEE, so accept either identifier.
        current_engine = self.scene.render.engine
        if current_engine in {"BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"}:
            self.eevee_engine = current_engine
            return

        last_error = None
        for engine_identifier in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                self.scene.render.engine = engine_identifier
                self.eevee_engine = engine_identifier
                return
            except Exception as error:
                last_error = error

        raise RuntimeError(
            "Eevee is unavailable in this Blender build"
        ) from last_error

    def create_cameras(self):
        source_data = self.source_camera.data
        for face_name in FACE_NAMES:
            camera_data = bpy.data.cameras.new(f"__E360_{face_name}_DATA__")
            camera_data.type = "PERSP"
            camera_data.sensor_fit = "HORIZONTAL"
            camera_data.sensor_width = 36.0
            camera_data.lens = 18.0  # exactly 90 degrees with a 36 mm sensor
            camera_data.clip_start = source_data.clip_start
            camera_data.clip_end = 500.0
            camera = bpy.data.objects.new(f"__E360_{face_name}__", camera_data)
            # Register it immediately so cleanup can remove it even if linking
            # the object to the scene fails.
            self.cameras[face_name] = camera
            camera.hide_render = False
            self.scene.collection.objects.link(camera)

    def update_cameras(self, context):
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = self.source_camera.evaluated_get(depsgraph)
        location, rotation, _scale = evaluated.matrix_world.decompose()
        base_matrix = Matrix.LocRotScale(
            location,
            rotation,
            Vector((1.0, 1.0, 1.0)),
        )
        for face_name in FACE_NAMES:
            self.cameras[face_name].matrix_world = (
                base_matrix @ FACE_ROTATIONS[face_name]
            )

    def remove_cameras(self):
        for camera in list(getattr(self, "cameras", {}).values()):
            camera_data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if camera_data.users == 0:
                bpy.data.cameras.remove(camera_data)
        self.cameras.clear()

    @staticmethod
    def close_memmap(buffer):
        if not isinstance(buffer, np.memmap):
            return
        path = os.fspath(buffer.filename)
        try:
            buffer.flush()
        except Exception:
            pass
        memory_map = getattr(buffer, "_mmap", None)
        if memory_map is not None:
            try:
                memory_map.close()
            except Exception:
                pass
        try:
            os.remove(path)
        except OSError:
            pass

    def release_face_buffers(self):
        for buffer in getattr(self, "faces", {}).values():
            self.close_memmap(buffer)
        self.faces = {}

    def release_panorama_buffer(self):
        panorama = getattr(self, "panorama", None)
        if panorama is not None:
            self.close_memmap(panorama)
        self.panorama = None

    def state_start_frame(self, context):
        frame = self.frames[self.frame_index]
        self.scene.frame_set(frame)
        context.view_layer.update()
        self.update_cameras(context)
        self.face_index = 0
        self.row = 0
        self.release_face_buffers()
        self.release_panorama_buffer()
        self.panorama = None
        self.state = "START_FACE"

    def face_path(self):
        frame = self.frames[self.frame_index]
        face_name = FACE_NAMES[self.face_index]
        return os.path.join(self.temp_dir, f"{frame:06d}_{face_name}.exr")

    def configure_face_render(self):
        render = self.scene.render
        image = render.image_settings
        render.engine = self.eevee_engine
        render.resolution_x = self.cube_size
        render.resolution_y = self.cube_size
        render.resolution_percentage = 100
        render.pixel_aspect_x = 1.0
        render.pixel_aspect_y = 1.0
        render.use_border = False
        render.use_crop_to_border = False
        render.use_multiview = False
        render.use_sequencer = False
        render.use_compositing = (
            self.props.use_compositor
            if self.props.render_method == "FINAL"
            else False
        )
        render.use_file_extension = True
        render.use_lock_interface = False
        image.file_format = "OPEN_EXR"
        image.color_mode = "RGBA"
        image.color_depth = "16"
        if hasattr(image, "exr_codec"):
            image.exr_codec = "ZIP"

    def state_start_face(self, context):
        self.configure_face_render()
        face_name = FACE_NAMES[self.face_index]
        path = self.face_path()
        self.scene.camera = self.cameras[face_name]
        self.scene.render.filepath = path
        if os.path.isfile(path):
            os.remove(path)

        self.face_done = False
        self.face_cancelled = False
        self.wait_ticks = 0
        self.props.status = (
            f"Frame {self.frames[self.frame_index]} | "
            f"Face {self.face_index + 1}/6: {face_name} | "
            f"{self.cube_size} x {self.cube_size} | "
            f"{'Viewport' if self.props.render_method == 'VIEWPORT' else 'Eevee'}"
        )
        self.state = "WAIT_FACE"
        if self.props.render_method == "VIEWPORT":
            result = self.start_viewport_render(context)
        else:
            result = bpy.ops.render.render(
                "INVOKE_DEFAULT",
                write_still=True,
                scene=self.scene.name,
            )
        if "CANCELLED" in result:
            raise RuntimeError("Blender refused to start the cube-face render")

    def start_viewport_render(self, context):
        # Prefer a viewport already using Rendered or Material Preview. If there
        # is more than one with the same shading mode, use the largest one.
        candidates = []
        shading_priority = {
            "RENDERED": 3,
            "MATERIAL": 2,
            "SOLID": 1,
            "WIREFRAME": 0,
        }
        for window in self.wm.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next(
                    (item for item in area.regions if item.type == "WINDOW"),
                    None,
                )
                if region is None:
                    continue

                space = area.spaces.active
                shading_type = space.shading.type
                candidates.append(
                    (
                        shading_priority.get(shading_type, -1),
                        area.width * area.height,
                        window,
                        screen,
                        area,
                        region,
                        space,
                    )
                )

        if not candidates:
            raise RuntimeError(
                "Viewport mode needs at least one open 3D Viewport area"
            )

        (
            _priority,
            _area_size,
            window,
            screen,
            area,
            region,
            space,
        ) = max(candidates, key=lambda item: (item[0], item[1]))

        region_3d = space.region_3d
        cube_camera = self.scene.camera
        view_state = {
            "perspective": region_3d.view_perspective,
            "location": region_3d.view_location.copy(),
            "rotation": region_3d.view_rotation.copy(),
            "distance": region_3d.view_distance,
            "camera_offset": tuple(region_3d.view_camera_offset),
            "camera_zoom": region_3d.view_camera_zoom,
            "space_camera": getattr(space, "camera", None),
            "use_local_camera": getattr(space, "use_local_camera", None),
        }

        try:
            # Point this viewport through the temporary cube camera only during
            # the synchronous capture. Shading, lighting and overlays stay as the
            # user configured them, so Rendered mode is captured as Rendered.
            if hasattr(space, "use_local_camera"):
                space.use_local_camera = False
            if hasattr(space, "camera"):
                space.camera = cube_camera
            region_3d.view_perspective = "CAMERA"
            region_3d.update()
            area.tag_redraw()

            with context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
                scene=self.scene,
            ):
                return bpy.ops.render.opengl(
                    "EXEC_DEFAULT",
                    animation=False,
                    write_still=True,
                    view_context=True,
                )

        finally:
            # Restore the user's viewport immediately after each face.
            self.scene.camera = self.source_camera
            try:
                if hasattr(space, "use_local_camera"):
                    space.use_local_camera = view_state["use_local_camera"]
                if hasattr(space, "camera"):
                    space.camera = view_state["space_camera"]
                region_3d.view_location = view_state["location"]
                region_3d.view_rotation = view_state["rotation"]
                region_3d.view_distance = view_state["distance"]
                region_3d.view_camera_offset = view_state["camera_offset"]
                region_3d.view_camera_zoom = view_state["camera_zoom"]
                region_3d.view_perspective = view_state["perspective"]
                region_3d.update()
                area.tag_redraw()
            except Exception:
                traceback.print_exc()

    def state_wait_face(self, context):
        if bpy.app.is_job_running("RENDER"):
            if self.props.cancel_requested:
                self.props.status = "Waiting for the current face; press Esc to stop it now"
            return

        if self.props.cancel_requested:
            return self.finish(True, "360 render cancelled")

        path = self.face_path()
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            self.state = "LOAD_FACE"
            return

        # Some render backends signal completion just before the file becomes
        # visible. Allow a short grace period before treating that as an error.
        self.wait_ticks += 1
        if self.wait_ticks > 25:
            face_name = FACE_NAMES[self.face_index]
            raise RuntimeError(f"{face_name} finished without producing an EXR file")

    def state_load_face(self, context):
        face_name = FACE_NAMES[self.face_index]
        path = self.face_path()
        image = bpy.data.images.load(path, check_existing=False)
        try:
            width, height = image.size[:]
            if width != self.cube_size or height != self.cube_size:
                raise RuntimeError(
                    f"{face_name} produced {width} x {height}; "
                    f"expected {self.cube_size} x {self.cube_size}"
                )
            # Keep cube faces in disk-backed float16 buffers. The previous
            # implementation retained six float32 images in RAM and could push
            # Blender over the memory limit at 4K/8K resolutions.
            pixels = np.empty(width * height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            cache_path = os.path.join(
                self.temp_dir,
                f"face_{self.frame_index:06d}_{face_name}.bin",
            )
            face_buffer = np.memmap(
                cache_path,
                dtype=np.float16,
                mode="w+",
                shape=(height, width, 4),
            )
            face_buffer[:] = pixels.reshape(height, width, 4)
            face_buffer.flush()
            self.faces[face_name] = face_buffer
            del pixels
        finally:
            bpy.data.images.remove(image)

        try:
            os.remove(path)
        except OSError:
            pass

        render_result = bpy.data.images.get("Render Result")
        if render_result is not None and hasattr(render_result, "buffers_free"):
            try:
                render_result.buffers_free()
            except Exception:
                pass
        gc.collect()

        progress = (
            self.frame_index * 7 + self.face_index + 1
        ) / self.total_units
        self.set_progress(progress, f"Face {self.face_index + 1}/6 finished")
        self.face_index += 1
        self.state = "START_FACE" if self.face_index < 6 else "PREPARE_STITCH"

    def state_prepare_stitch(self, context):
        panorama_cache = os.path.join(
            self.temp_dir,
            f"panorama_{self.frame_index:06d}.bin",
        )
        self.panorama = np.memmap(
            panorama_cache,
            dtype=np.float16,
            mode="w+",
            shape=(self.pano_height, self.pano_width, 4),
        )
        horizontal = (
            np.arange(self.pano_width, dtype=np.float32) + 0.5
        ) / self.pano_width
        longitude = (horizontal - 0.5) * (2.0 * math.pi)
        self.sin_lon = np.sin(longitude)
        self.cos_lon = np.cos(longitude)
        self.row = 0
        self.state = "STITCH"

    def sample_face(self, face_name, dx, dy, dz, mask):
        if not mask.any():
            return None

        x = dx[mask]
        y = dy[mask]
        z = dz[mask]
        if face_name == "FRONT":
            camera_x, camera_y, camera_z = x, y, z
        elif face_name == "RIGHT":
            camera_x, camera_y, camera_z = z, y, -x
        elif face_name == "BACK":
            camera_x, camera_y, camera_z = -x, y, -z
        elif face_name == "LEFT":
            camera_x, camera_y, camera_z = -z, y, x
        elif face_name == "UP":
            camera_x, camera_y, camera_z = x, z, -y
        else:  # DOWN
            camera_x, camera_y, camera_z = x, -z, y

        depth = np.maximum(-camera_z, 1.0e-8)
        normalized_x = np.clip(camera_x / depth, -1.0, 1.0)
        normalized_y = np.clip(camera_y / depth, -1.0, 1.0)
        sample_x = (normalized_x + 1.0) * 0.5 * (self.cube_size - 1)
        sample_y = (normalized_y + 1.0) * 0.5 * (self.cube_size - 1)
        x0 = np.floor(sample_x).astype(np.int32)
        y0 = np.floor(sample_y).astype(np.int32)
        x1 = np.minimum(x0 + 1, self.cube_size - 1)
        y1 = np.minimum(y0 + 1, self.cube_size - 1)
        weight_x = (sample_x - x0)[:, None]
        weight_y = (sample_y - y0)[:, None]
        face = self.faces[face_name]
        top = face[y0, x0] * (1.0 - weight_x) + face[y0, x1] * weight_x
        bottom = face[y1, x0] * (1.0 - weight_x) + face[y1, x1] * weight_x
        return top * (1.0 - weight_y) + bottom * weight_y

    def state_stitch(self, context):
        row_start = self.row
        row_end = min(
            self.pano_height,
            row_start + self.props.rows_per_tick,
        )
        if row_start >= row_end:
            self.state = "SAVE"
            return

        vertical = (
            np.arange(row_start, row_end, dtype=np.float32) + 0.5
        ) / self.pano_height
        latitude = (vertical - 0.5) * math.pi
        sin_latitude = np.sin(latitude)[:, None]
        cos_latitude = np.cos(latitude)[:, None]
        dx = cos_latitude * self.sin_lon[None, :]
        dy = np.broadcast_to(sin_latitude, dx.shape)
        dz = -cos_latitude * self.cos_lon[None, :]

        major_axis = np.argmax(
            np.stack((np.abs(dx), np.abs(dy), np.abs(dz))),
            axis=0,
        )
        masks = {
            "RIGHT": (major_axis == 0) & (dx >= 0),
            "LEFT": (major_axis == 0) & (dx < 0),
            "UP": (major_axis == 1) & (dy >= 0),
            "DOWN": (major_axis == 1) & (dy < 0),
            "BACK": (major_axis == 2) & (dz >= 0),
            "FRONT": (major_axis == 2) & (dz < 0),
        }
        chunk = self.panorama[row_start:row_end]
        for face_name in FACE_NAMES:
            sampled = self.sample_face(
                face_name,
                dx,
                dy,
                dz,
                masks[face_name],
            )
            if sampled is not None:
                chunk[masks[face_name]] = sampled

        self.row = row_end
        fraction = row_end / self.pano_height
        progress = (
            self.frame_index * 7 + 6 + fraction
        ) / self.total_units
        self.set_progress(
            progress,
            f"Frame {self.frames[self.frame_index]} | Stitching {fraction * 100.0:.1f}%",
        )
        if self.row >= self.pano_height:
            self.state = "SAVE"

    def panorama_path(self):
        frame = self.frames[self.frame_index]
        return os.path.join(
            self.frames_dir,
            f"{self.file_prefix}_{frame:06d}.png",
        )

    def state_save(self, context):
        path = self.panorama_path()
        # Cube faces are no longer needed once stitching has completed. Close
        # them before Blender allocates the final image buffer.
        self.release_face_buffers()
        gc.collect()
        image = bpy.data.images.new(
            "__E360_PANORAMA__",
            width=self.pano_width,
            height=self.pano_height,
            alpha=True,
            float_buffer=True,
        )
        try:
            # Eevee render buffers and EXR files use associated alpha. Marking
            # the generated image accordingly prevents dark fringes in PNGs.
            image.alpha_mode = "PREMUL"
            self.panorama.flush()
            flattened = self.panorama.reshape(-1)
            try:
                image.pixels.foreach_set(flattened)
            except (TypeError, ValueError):
                # Some Blender/Python builds only accept float32 in foreach_set.
                # Fall back to bounded chunks rather than allocating a complete
                # float32 copy of a potentially very large panorama.
                chunk_size = 1_048_576
                for start in range(0, flattened.size, chunk_size):
                    end = min(flattened.size, start + chunk_size)
                    image.pixels[start:end] = np.asarray(
                        flattened[start:end],
                        dtype=np.float32,
                    )
            image_settings = self.scene.render.image_settings
            image_settings.file_format = "PNG"
            image_settings.color_mode = "RGBA"
            image_settings.color_depth = "8"
            image.update()
            image.save_render(path, scene=self.scene)
        finally:
            bpy.data.images.remove(image)

        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError(f"Blender failed to save {path}")

        self.pngs.append(path)
        self.release_panorama_buffer()
        self.sin_lon = None
        self.cos_lon = None
        gc.collect()
        progress = (self.frame_index * 7 + 7) / self.total_units
        self.set_progress(progress, f"Saved {os.path.basename(path)}")
        self.state = "NEXT_FRAME"

    def state_next_frame(self, context):
        self.frame_index += 1
        if self.frame_index < len(self.frames):
            self.state = "START_FRAME"
            return

        self.remove_cameras()
        self.scene.camera = self.source_camera
        self.state = "START_ENCODE" if self.mode == "VIDEO" else "DONE"

    def next_mp4_path(self):
        candidate = os.path.join(self.output_dir, self.file_prefix + ".mp4")
        if not os.path.exists(candidate):
            return candidate
        index = 1
        while True:
            candidate = os.path.join(
                self.output_dir,
                f"{self.file_prefix}_{index:03d}.mp4",
            )
            if not os.path.exists(candidate):
                return candidate
            index += 1

    def find_encoded_movie(self):
        if not self.temp_dir or not os.path.isdir(self.temp_dir):
            return None
        movies = []
        for filename in os.listdir(self.temp_dir):
            if not filename.lower().endswith((".mp4", ".m4v")):
                continue
            path = os.path.join(self.temp_dir, filename)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                movies.append(path)
        return max(movies, key=os.path.getmtime) if movies else None

    def state_start_encode(self, context):
        if not self.pngs:
            raise RuntimeError("No panorama frames were generated")
        missing = [
            path
            for path in self.pngs
            if not os.path.isfile(path) or os.path.getsize(path) == 0
        ]
        if missing:
            raise RuntimeError(f"{len(missing)} panorama frame(s) are missing")

        encoder = bpy.data.scenes.new("__E360_ENCODER__")
        self.encoder_scene = encoder
        encoder.render.resolution_x = self.pano_width
        encoder.render.resolution_y = self.pano_height
        encoder.render.resolution_percentage = 100
        encoder.render.pixel_aspect_x = 1.0
        encoder.render.pixel_aspect_y = 1.0
        encoder.render.fps = self.scene.render.fps
        encoder.render.fps_base = self.scene.render.fps_base
        encoder.frame_start = 1
        encoder.frame_end = len(self.pngs)
        encoder.frame_step = 1
        encoder.frame_set(1)
        encoder.render.use_sequencer = True
        encoder.render.use_compositing = False
        encoder.render.use_file_extension = True
        encoder.render.use_lock_interface = False

        # PNG frames are already display-referred. Standard converts the image
        # strip from sRGB and back without applying the source scene's look twice.
        try:
            encoder.view_settings.view_transform = "Standard"
            encoder.view_settings.look = "None"
        except Exception:
            pass
        encoder.view_settings.exposure = 0.0
        encoder.view_settings.gamma = 1.0

        sequence_editor = encoder.sequence_editor_create()
        sequences = getattr(sequence_editor, "strips", None)
        if sequences is None:
            sequences = getattr(sequence_editor, "sequences", None)
        if sequences is None:
            raise RuntimeError("This Blender version has no compatible Sequencer API")

        strip = sequences.new_image(
            "Eevee 360 Frames",
            self.pngs[0],
            channel=1,
            frame_start=1,
            fit_method="ORIGINAL",
        )
        for path in self.pngs[1:]:
            strip.elements.append(os.path.basename(path))
        try:
            strip.frame_final_duration = len(self.pngs)
        except Exception:
            pass
        try:
            strip.use_auto_refresh = True
        except Exception:
            pass

        image_settings = encoder.render.image_settings
        image_settings.file_format = "FFMPEG"
        image_settings.color_mode = "RGB"
        encoder.render.ffmpeg.format = "MPEG4"
        encoder.render.ffmpeg.codec = "H264"
        encoder.render.ffmpeg.audio_codec = "NONE"
        encoder.render.ffmpeg.constant_rate_factor = self.props.quality
        encoder.render.ffmpeg.ffmpeg_preset = "GOOD"
        encoder.render.filepath = os.path.join(self.temp_dir, "eevee360_video.mp4")

        self.final_video = self.next_mp4_path()
        self.encode_done = False
        self.encode_cancelled = False
        self.encoded_frames = 0
        self.wait_ticks = 0
        self.state = "WAIT_ENCODE"
        self.props.status = f"Encoding MP4 0/{len(self.pngs)}"
        result = bpy.ops.render.render(
            "INVOKE_DEFAULT",
            animation=True,
            scene=encoder.name,
        )
        if "CANCELLED" in result:
            raise RuntimeError("Blender refused to start MP4 encoding")

    def state_wait_encode(self, context):
        if bpy.app.is_job_running("RENDER"):
            if self.props.cancel_requested:
                self.props.status = "Waiting for FFmpeg; press Esc to stop it now"
            return

        if self.props.cancel_requested:
            return self.finish(True, "360 render cancelled")

        movie = self.find_encoded_movie()
        if movie is None:
            self.wait_ticks += 1
            if self.wait_ticks <= 50:
                return
            raise RuntimeError(
                "FFmpeg finished but did not create an MP4. "
                "Check Window > Toggle System Console for the encoder error."
            )

        if self.encoder_scene is not None:
            bpy.data.scenes.remove(self.encoder_scene)
            self.encoder_scene = None
        os.replace(movie, self.final_video)

        if self.props.delete_pngs:
            for path in self.pngs:
                try:
                    os.remove(path)
                except OSError:
                    pass

        return self.finish(
            False,
            f"MP4 saved: {os.path.basename(self.final_video)}",
        )

    def state_done(self, context):
        if self.mode == "STILL":
            message = f"Panorama saved: {os.path.basename(self.pngs[0])}"
        else:
            message = f"Sequence saved: {len(self.pngs)} PNG frames"
        return self.finish(False, message)

    def set_progress(self, value, message):
        value = max(0.0, min(1.0, float(value)))
        self.props.progress = value
        self.props.status = message
        try:
            self.wm.progress_update(value)
        except Exception:
            pass
        redraw_ui()

    def finish(self, cancelled, message):
        self.props.status = message
        if not cancelled:
            self.set_progress(1.0, message)
        self.cleanup()
        self.report({"WARNING" if cancelled else "INFO"}, message)
        return {"CANCELLED" if cancelled else "FINISHED"}

    def cleanup(self):
        global _ACTIVE_OPERATOR

        if getattr(self, "cleaned", False):
            return
        self.cleaned = True

        try:
            self.remove_handlers()
        except Exception:
            traceback.print_exc()

        timer = getattr(self, "timer", None)
        if timer is not None:
            try:
                self.wm.event_timer_remove(timer)
            except Exception:
                pass
            self.timer = None

        try:
            self.wm.progress_end()
        except Exception:
            pass

        try:
            if getattr(self, "cameras", None):
                self.remove_cameras()
        except Exception:
            traceback.print_exc()

        try:
            if getattr(self, "encoder_scene", None) is not None:
                bpy.data.scenes.remove(self.encoder_scene)
                self.encoder_scene = None
        except Exception:
            traceback.print_exc()

        try:
            self.restore_settings()
        except Exception:
            traceback.print_exc()

        self.release_face_buffers()
        self.release_panorama_buffer()
        self.sin_lon = None
        self.cos_lon = None
        gc.collect()

        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

        props = getattr(self, "props", None)
        if props is not None:
            props.running = False
            props.cancel_requested = False
        if _ACTIVE_OPERATOR is self:
            _ACTIVE_OPERATOR = None
        redraw_ui()


class E360_PT_Panel(Panel):
    bl_label = "Eevee 360 Render"
    bl_idname = "E360_PT_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"

    def draw(self, context):
        layout = self.layout
        props = context.scene.e360
        scene = context.scene
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "output_dir")
        layout.prop(props, "prefix")

        dimensions = layout.box()
        dimensions.label(text="Equirectangular Output", icon="IMAGE_DATA")
        dimensions.prop(props, "width")
        actual_width = even_multiple_of_four(props.width)
        dimensions.label(text=f"Actual size: {actual_width} x {actual_width // 2}")
        if actual_width >= 8192:
            dimensions.label(
                text="8K+ still needs substantial RAM/GPU memory",
                icon="ERROR",
            )
        dimensions.prop(props, "face_size")
        face_size = props.face_size or max(128, actual_width // 4)
        dimensions.label(text=f"Cube faces: 6 x {face_size} px")
        dimensions.prop(props, "rows_per_tick")
        dimensions.prop(props, "render_method")
        compositor_row = dimensions.row()
        compositor_row.enabled = props.render_method == "FINAL"
        compositor_row.prop(props, "use_compositor")

        frames_folder = f"{safe_prefix(props.prefix)}_frames"
        dimensions.label(text=f"Frames folder: {frames_folder}", icon="FILE_FOLDER")
        if props.render_method == "VIEWPORT":
            dimensions.label(
                text="Viewport mode requires an open 3D Viewport",
                icon="INFO",
            )

        video = layout.box()
        video.label(text="MP4 / H.264", icon="FILE_MOVIE")
        video.prop(props, "quality")
        video.prop(props, "delete_pngs")
        fps = scene.render.fps / scene.render.fps_base
        video.label(text=f"Frame rate: {fps:.3f} fps")
        video.label(text="MP4 output is RGB; PNG frames retain alpha", icon="INFO")

        layout.separator()
        if props.running:
            if hasattr(layout, "progress"):
                layout.progress(
                    factor=props.progress,
                    text=f"{props.progress * 100.0:.1f}%",
                    type="BAR",
                )
            else:
                row = layout.row()
                row.enabled = False
                row.prop(props, "progress", slider=True)
            layout.label(text=props.status, icon="TIME")
            layout.operator("render.e360_cancel", text="Cancel", icon="CANCEL")
        else:
            row = layout.row()
            row.enabled = scene.camera is not None
            operator = row.operator(
                "render.e360_render",
                text="Render 360 Image",
                icon="RENDER_STILL",
            )
            operator.mode = "STILL"

            row = layout.row()
            row.enabled = scene.camera is not None
            operator = row.operator(
                "render.e360_render",
                text="Render 360 PNG Sequence",
                icon="RENDER_ANIMATION",
            )
            operator.mode = "SEQUENCE"

            row = layout.row()
            row.enabled = scene.camera is not None
            operator = row.operator(
                "render.e360_render",
                text="Render 360 MP4",
                icon="FILE_MOVIE",
            )
            operator.mode = "VIDEO"

            if scene.camera is None:
                layout.label(text="Set an active scene camera first", icon="ERROR")

        layout.separator()
        layout.label(text="Uses six temporary 90-degree Eevee cameras", icon="CAMERA_DATA")
        layout.label(text="Camera position and animation are preserved")


CLASSES = (
    E360_Properties,
    E360_OT_Cancel,
    E360_OT_Render,
    E360_PT_Panel,
)


def register():
    for class_type in CLASSES:
        bpy.utils.register_class(class_type)
    bpy.types.Scene.e360 = PointerProperty(type=E360_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "e360"):
        del bpy.types.Scene.e360
    for class_type in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(class_type)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
