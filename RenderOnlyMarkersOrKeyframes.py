bl_info = {
    "name": "Render Only Markers or Keyframes + Marker Playback",
    "author": "ChatGPT",
    "version": (2, 5),
    "blender": (4, 3, 0),
    "location": "Render > Render Only Markers or Keyframes",
    "description": "Renderiza apenas os frames com markers ou keyframes e reproduz apenas os frames com markers.",
    "category": "Render"
}

import bpy
import os

# === Coleta de Frames ===
def get_marker_frames(scene, use_playback_range=False):
    frame_start = scene.frame_preview_start if use_playback_range else scene.frame_start
    frame_end = scene.frame_preview_end if use_playback_range else scene.frame_end
    return sorted([
        marker.frame for marker in scene.timeline_markers
        if frame_start <= marker.frame <= frame_end
    ])

def get_keyframes(scene, use_playback_range=False):
    keyframes = set()
    frame_start = scene.frame_preview_start if use_playback_range else scene.frame_start
    frame_end = scene.frame_preview_end if use_playback_range else scene.frame_end
    for obj in scene.objects:
        if obj.animation_data and obj.animation_data.action:
            for fcurve in obj.animation_data.action.fcurves:
                for keyframe_point in fcurve.keyframe_points:
                    frame = int(keyframe_point.co.x)
                    if frame_start <= frame <= frame_end:
                        keyframes.add(frame)
    return sorted(list(keyframes))

# === Atualizadores ===
def update_markers(self, context):
    if self.enable_render_only_markers:
        self.enable_render_only_keyframes = False

def update_keyframes(self, context):
    if self.enable_render_only_keyframes:
        self.enable_render_only_markers = False

# === Operador: Play apenas os Markers ===
class PLAY_OT_play_marker_frames(bpy.types.Operator):
    bl_idname = "screen.play_only_markers"
    bl_label = "▶ Play Only Markers"

    _timer = None
    _marker_frames = []
    _index = 0
    _timer_time = 0.041
    _is_running = False

    def modal(self, context, event):
        props = context.scene.render_selective_props

        if event.type == 'ESC':
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            if self._index >= len(self._marker_frames):
                if props.loop_playback:
                    self._index = 0
                else:
                    self.cancel(context)
                    return {'CANCELLED'}

            context.scene.frame_set(self._marker_frames[self._index])
            self._index += 1
        return {'RUNNING_MODAL'}

    def execute(self, context):
        scene = context.scene
        props = scene.render_selective_props
        self._marker_frames = get_marker_frames(scene, use_playback_range=props.use_playback_range)
        self._index = 0

        if not self._marker_frames:
            self.report({'WARNING'}, "Nenhum marker encontrado.")
            return {'CANCELLED'}

        fps = props.preview_fps if props.preview_fps > 0 else 24
        self._timer_time = 1.0 / fps

        wm = context.window_manager
        self._timer = wm.event_timer_add(self._timer_time, window=context.window)
        wm.modal_handler_add(self)
        PLAY_OT_play_marker_frames._is_running = True
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        PLAY_OT_play_marker_frames._is_running = False
        return {'CANCELLED'}

class PLAY_OT_stop_marker_playback(bpy.types.Operator):
    bl_idname = "screen.stop_only_markers"
    bl_label = "■ Stop Playback"

    def execute(self, context):
        if PLAY_OT_play_marker_frames._is_running:
            context.window_manager.event_timer_remove(PLAY_OT_play_marker_frames._timer)
            PLAY_OT_play_marker_frames._is_running = False
        return {'FINISHED'}

# === Propriedades da Interface ===
class RenderSelectiveProperties(bpy.types.PropertyGroup):
    use_playback_range: bpy.props.BoolProperty(
        name="Usar Playback Range",
        description="Limitar ao intervalo de reprodução (atalho P na timeline)",
        default=False
    )

    enable_render_only_markers: bpy.props.BoolProperty(
        name="Renderizar só Markers",
        description="Renderizar apenas os frames com markers",
        default=False,
        update=update_markers
    )

    enable_render_only_keyframes: bpy.props.BoolProperty(
        name="Renderizar só Keyframes",
        description="Renderizar apenas os frames com keyframes",
        default=False,
        update=update_keyframes
    )

    output_path: bpy.props.StringProperty(
        name="Caminho de saída",
        description="Pasta onde os frames serão salvos. Deixe em branco para usar a pasta do arquivo .blend",
        subtype='DIR_PATH',
        default=""
    )

    preview_fps: bpy.props.IntProperty(
        name="FPS do Preview",
        description="Taxa de quadros por segundo para visualizar a animação renderizada",
        default=24,
        min=1,
        max=120
    )

    loop_playback: bpy.props.BoolProperty(
        name="Loop de reprodução",
        description="Faz a reprodução entre markers repetir em loop",
        default=True
    )

# === Operadores de Render e Preview ===
class RENDER_OT_render_selected_frames(bpy.types.Operator):
    bl_idname = "render.render_selected_frames"
    bl_label = "Render Markers/Keyframes"
    bl_description = "Renderiza os frames selecionados conforme as opções"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        props = scene.render_selective_props

        if props.enable_render_only_markers:
            frames = get_marker_frames(scene, use_playback_range=props.use_playback_range)
        elif props.enable_render_only_keyframes:
            frames = get_keyframes(scene, use_playback_range=props.use_playback_range)
        else:
            self.report({'WARNING'}, "Nenhuma opção de renderização ativada.")
            return {'CANCELLED'}

        if not frames:
            self.report({'WARNING'}, "Nenhum frame encontrado nas condições especificadas.")
            return {'CANCELLED'}

        original_frame = scene.frame_current
        original_filepath = scene.render.filepath

        output_dir = bpy.path.abspath(props.output_path.strip()) if props.output_path.strip() else bpy.path.abspath("//")

        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                self.report({'ERROR'}, f"Erro ao criar diretório: {e}")
                return {'CANCELLED'}

        total_frames = len(frames)
        wm = context.window_manager
        wm.progress_begin(0, total_frames)

        for i, frame in enumerate(frames):
            scene.frame_set(frame)
            filename = f"{i+1:04d}.png"
            scene.render.filepath = os.path.join(output_dir, filename)

            bpy.ops.render.render(write_still=True)

            percent = ((i + 1) / total_frames) * 100
            wm.progress_update(i + 1)
            self.report({'INFO'}, f"Renderizado {i+1}/{total_frames} frames ({percent:.1f}%)")

        wm.progress_end()

        scene.render.filepath = original_filepath
        scene.frame_set(original_frame)

        return {'FINISHED'}

class RENDER_OT_preview_rendered_frames(bpy.types.Operator):
    bl_idname = "render.preview_rendered_frames"
    bl_label = "Preview da Animação"
    bl_description = "Abre o preview dos frames renderizados (Ctrl + F11)"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        props = scene.render_selective_props

        output_dir = bpy.path.abspath(props.output_path.strip()) if props.output_path.strip() else bpy.path.abspath("//")
        scene.render.filepath = os.path.join(output_dir, "")

        original_fps = scene.render.fps
        scene.render.fps = props.preview_fps

        try:
            bpy.ops.render.play_rendered_anim()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Erro ao abrir preview: {e}")
            return {'CANCELLED'}
        finally:
            scene.render.fps = original_fps

# === Painel ===
class RENDER_PT_selective_render_panel(bpy.types.Panel):
    bl_label = "Render Only Markers / Keyframes"
    bl_idname = "RENDER_PT_selective_render_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"

    def draw(self, context):
        layout = self.layout
        props = context.scene.render_selective_props

        layout.prop(props, "enable_render_only_markers")
        layout.prop(props, "enable_render_only_keyframes")
        layout.prop(props, "use_playback_range")
        layout.prop(props, "loop_playback")
        layout.prop(props, "output_path")
        layout.prop(props, "preview_fps")

        layout.operator("render.render_selected_frames", icon="RENDER_ANIMATION")
        layout.operator("render.preview_rendered_frames", icon="PLAY")
        layout.operator("screen.play_only_markers", icon="PLAY")
        layout.operator("screen.stop_only_markers", icon="CANCEL")

# === Registro ===
def register():
    bpy.utils.register_class(RenderSelectiveProperties)
    bpy.types.Scene.render_selective_props = bpy.props.PointerProperty(type=RenderSelectiveProperties)

    bpy.utils.register_class(RENDER_OT_render_selected_frames)
    bpy.utils.register_class(RENDER_OT_preview_rendered_frames)
    bpy.utils.register_class(RENDER_PT_selective_render_panel)
    bpy.utils.register_class(PLAY_OT_play_marker_frames)
    bpy.utils.register_class(PLAY_OT_stop_marker_playback)

def unregister():
    bpy.utils.unregister_class(RENDER_OT_render_selected_frames)
    bpy.utils.unregister_class(RENDER_OT_preview_rendered_frames)
    bpy.utils.unregister_class(RENDER_PT_selective_render_panel)
    bpy.utils.unregister_class(RenderSelectiveProperties)
    bpy.utils.unregister_class(PLAY_OT_play_marker_frames)
    bpy.utils.unregister_class(PLAY_OT_stop_marker_playback)
    del bpy.types.Scene.render_selective_props

if __name__ == "__main__":
    register()
