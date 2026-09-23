from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.html import format_html
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from .models import (
    UploadedImage, ProcessingLog,
    Vehicle, Camera, GateDevice, AccessEvent, GateConfigChange, SimulatedBarrier,
)
from .forms import CameraForm, GateDeviceForm
from .services import barrier_simulator, config_audit, gate_service
from .utils.auth import GATE_ADMIN, user_roles


@admin.register(UploadedImage)
class UploadedImageAdmin(admin.ModelAdmin):
    """
    Admin interface for UploadedImage model
    """
    list_display = (
        'filename',
        'file_size_mb',
        'processing_status',
        'plate_count',
        'upload_timestamp',
        'processing_timestamp',
    )
    list_filter = (
        'processing_status',
        'upload_timestamp',
        'processing_timestamp',
    )
    search_fields = ('filename',)
    readonly_fields = (
        'filename',
        'file_size',
        'upload_timestamp',
        'processing_timestamp',
        'api_response',
    )
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'original_image',
                'processed_image',
                'filename',
                'file_size',
            )
        }),
        ('Processing Information', {
            'fields': (
                'processing_status',
                'processing_timestamp',
                'error_message',
            )
        }),
        ('API Response', {
            'fields': ('api_response',),
            'classes': ('collapse',),
            'description': 'Raw API response from Qwen3-VL model'
        }),
    )
    ordering = ('-upload_timestamp',)
    date_hierarchy = 'upload_timestamp'
    
    def get_queryset(self, request):
        """
        Optimize queryset for admin interface
        """
        qs = super().get_queryset(request)
        return qs.select_related().prefetch_related('processing_logs')
    
    def file_size_mb(self, obj):
        """
        Display file size in MB
        """
        if obj.file_size_mb:
            return f"{obj.file_size_mb} MB"
        return "N/A"
    file_size_mb.short_description = 'File Size (MB)'
    
    def plate_count(self, obj):
        """
        Display number of plates detected
        """
        count = obj.get_plate_count()
        if count > 0:
            return format_html(
                '<span style="color: green; font-weight: bold;">{}</span>',
                count
            )
        return "0"
    plate_count.short_description = 'Plates Detected'
    
    def original_image_thumbnail(self, obj):
        """
        Display thumbnail of original image
        """
        if obj.original_image:
            return format_html(
                '<img src="{}" width="100" height="50" style="object-fit: cover;" />',
                obj.original_image.url
            )
        return "No image"
    original_image_thumbnail.short_description = 'Original Image'
    
    def processed_image_thumbnail(self, obj):
        """
        Display thumbnail of processed image
        """
        if obj.processed_image:
            return format_html(
                '<img src="{}" width="100" height="50" style="object-fit: cover;" />',
                obj.processed_image.url
            )
        return "No processed image"
    processed_image_thumbnail.short_description = 'Processed Image'
    
    def view_results_link(self, obj):
        """
        Link to view results page
        """
        if obj.processing_status == 'completed':
            url = reverse('lpr_app:image_detail', kwargs={'image_id': obj.id})
            return format_html(
                '<a href="{}" class="button" target="_blank">View Results</a>',
                url
            )
        return "N/A"
    view_results_link.short_description = 'View Results'
    
    def get_readonly_fields(self, request, obj=None):
        """
        Make fields readonly after processing is complete
        """
        readonly_fields = list(self.readonly_fields)
        if obj and obj.processing_status in ['completed', 'processing']:
            readonly_fields.extend(['original_image', 'filename'])
        return readonly_fields


@admin.register(ProcessingLog)
class ProcessingLogAdmin(admin.ModelAdmin):
    """
    Admin interface for ProcessingLog model
    """
    list_display = (
        'uploaded_image',
        'status',
        'message',
        'duration_ms',
        'timestamp',
    )
    list_filter = (
        'status',
        'timestamp',
        'uploaded_image',
    )
    search_fields = ('message',)
    readonly_fields = (
        'uploaded_image',
        'status',
        'message',
        'duration_ms',
        'timestamp',
    )
    ordering = ('-timestamp',)
    date_hierarchy = 'timestamp'
    
    def get_queryset(self, request):
        """
        Optimize queryset for admin interface
        """
        qs = super().get_queryset(request)
        return qs.select_related('uploaded_image')
    
    def uploaded_image_link(self, obj):
        """
        Link to the uploaded image
        """
        if obj.uploaded_image:
            url = reverse('admin:lpr_app_uploadedImage_change', 
                        kwargs={'object_id': obj.uploaded_image.id})
            return format_html(
                '<a href="{}">{}</a>',
                url,
                obj.uploaded_image.filename
            )
        return "N/A"
    uploaded_image_link.short_description = 'Uploaded Image'
    
    def duration_display(self, obj):
        """
        Format duration for display
        """
        if obj.duration_ms:
            if obj.duration_ms < 1000:
                return f"{obj.duration_ms}ms"
            else:
                return f"{obj.duration_ms/1000:.2f}s"
        return "N/A"
    duration_display.short_description = 'Duration'
    
    def status_badge(self, obj):
        """
        Display status as colored badge
        """
        status_colors = {
            'started': 'orange',
            'api_call': 'blue',
            'success': 'green',
            'error': 'red',
        }
        
        color = status_colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; '
            'border-radius: 3px; font-size: 0.8em;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = (
        'plate_display', 'plate_normalized', 'owner_name', 'department',
        'vehicle_type', 'is_active', 'valid_until',
    )
    list_filter = ('is_active', 'vehicle_type', 'department')
    search_fields = ('plate_normalized', 'plate_display', 'owner_name', 'department')
    readonly_fields = ('plate_normalized', 'created_at', 'updated_at')


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    form = CameraForm
    list_display = ('name', 'host', 'vendor', 'is_enabled', 'password_set', 'last_test_ok', 'agent_status', 'config_version')
    list_filter = ('vendor', 'is_enabled')
    search_fields = ('name', 'host')
    readonly_fields = (
        'config_version', 'updated_by', 'created_at', 'updated_at',
        'last_test_at', 'last_test_ok', 'last_test_error', 'agent_status', 'agent_status_at',
    )

    actions = ['test_connection']

    @admin.display(boolean=True, description='Password set')
    def password_set(self, obj):
        return obj.password_set

    @admin.action(description='Test connection (RTSP port and snapshot)')
    def test_connection(self, request, queryset):
        from django.utils import timezone
        from .services import camera_service
        from .utils.secrets import SecretDecryptError, SecretKeyMissing

        for camera in queryset:
            try:
                password = camera.get_password()
            except (SecretKeyMissing, SecretDecryptError) as exc:
                self.message_user(request, f'{camera.name}: stored password unreadable: {exc}', messages.ERROR)
                continue
            camera_service.apply_preset(camera)
            result = camera_service.test_connection(camera, password)
            Camera.objects.filter(pk=camera.pk).update(
                last_test_at=timezone.now(), last_test_ok=result.ok, last_test_error=result.error,
            )
            steps = '; '.join(f"{s.name}: {'ok' if s.ok else 'skipped' if s.ok is None else 'FAILED'} ({s.message})"
                              for s in result.steps)
            self.message_user(request, f'{camera.name}: {steps}', messages.SUCCESS if result.ok else messages.ERROR)

    def save_model(self, request, obj, form, change):
        config_audit.save_camera(obj, request.user, password=form.cleaned_data.get('password'))

    def delete_model(self, request, obj):
        config_audit.delete_with_audit(obj, request.user)


class SimulatedBarrierInline(admin.StackedInline):
    model = SimulatedBarrier
    fields = ('travel_seconds', 'auto_close_seconds')
    max_num = 1
    can_delete = False
    verbose_name = 'Simulated barrier settings (used when controller type is Simulated)'


@admin.register(GateDevice)
class GateDeviceAdmin(admin.ModelAdmin):
    form = GateDeviceForm
    inlines = [SimulatedBarrierInline]
    list_display = (
        'name', 'direction', 'camera', 'controller_type', 'is_enabled', 'arm_state',
        'last_seen', 'firmware_version', 'test_link',
    )
    list_filter = ('direction', 'is_enabled', 'controller_type')
    readonly_fields = (
        'test_link', 'arm_state', 'arm_state_at', 'last_seen', 'firmware_version',
        'last_command_result', 'created_at', 'updated_at',
    )

    @admin.display(description='Test')
    def test_link(self, obj):
        if not obj or not obj.pk:
            return '-'
        return format_html(
            '<a class="button" href="{}">Test recognition</a>',
            reverse('admin:lpr_app_gatedevice_test', args=[obj.pk]),
        )

    def save_model(self, request, obj, form, change):
        config_audit.save_gate_device(obj, request.user, token=form.cleaned_data.get('controller_token'))

    def save_formset(self, request, form, formset, change):
        if formset.model is not SimulatedBarrier:
            return super().save_formset(request, form, formset, change)
        # save_gate_device may already have created the row; update it in place.
        # The admin's change message reads these attributes, normally set by formset.save().
        formset.new_objects, formset.changed_objects, formset.deleted_objects = [], [], []
        for inline_form in formset.forms:
            data = getattr(inline_form, 'cleaned_data', None)
            if inline_form.has_changed() and data:
                sim, _ = SimulatedBarrier.objects.get_or_create(gate=form.instance)
                sim.travel_seconds = data['travel_seconds']
                sim.auto_close_seconds = data['auto_close_seconds']
                sim.save(update_fields=['travel_seconds', 'auto_close_seconds'])

    def delete_model(self, request, obj):
        config_audit.delete_with_audit(obj, request.user)

    def get_urls(self):
        custom = [
            path(
                '<int:object_id>/test/',
                self.admin_site.admin_view(self.test_view),
                name='lpr_app_gatedevice_test',
            ),
        ]
        return custom + super().get_urls()

    def test_view(self, request, object_id):
        """
        Upload plate photos and run them through the real decision pipeline as
        a test event; on a simulated gate, a grant opens the simulated barrier.
        """
        if GATE_ADMIN not in user_roles(request.user):
            raise PermissionDenied
        gate = get_object_or_404(GateDevice, pk=object_id)
        from .views.gate_admin_views import run_test_decision

        result = None
        if request.method == 'POST' and request.POST.get('command'):
            command = request.POST['command']
            if not gate.is_simulated or command not in gate_service.COMMANDS:
                messages.error(request, 'Commands can only be sent to a simulated barrier.')
            else:
                event = gate_service.create_override(gate, command, request.user, is_test=True)
                gate_service.run_on_simulator(event)
                event.refresh_from_db()
                messages.info(request, f'{command}: {event.command_result}')
            return redirect('admin:lpr_app_gatedevice_test', object_id=gate.pk)

        if request.method == 'POST':
            from .views.gate_views import validate_frames
            frames, frames_error = validate_frames(request)
            if frames_error:
                messages.error(request, frames_error.content.decode())
            else:
                event, _, note = run_test_decision(gate, frames)
                result = AccessEvent.objects.select_related(
                    'vehicle', 'near_miss_vehicle', 'uploaded_image',
                ).get(pk=event.pk)
                if note:
                    messages.warning(request, note)

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': f'Test recognition: {gate.name}',
            'gate': gate,
            'mode': gate_service.effective_mode(),
            'can_actuate': gate_service.can_actuate(gate, gate_service.effective_mode()),
            'burst_frames': settings.GATE_BURST_FRAMES,
            'max_upload_mb': round(settings.UPLOAD_FILE_MAX_SIZE / (1024 * 1024), 1),
            'simulator': barrier_simulator.refresh(gate) if gate.is_simulated else None,
            'result': result,
            'recent': AccessEvent.objects.filter(gate=gate).select_related('vehicle')[:10],
        }
        return TemplateResponse(request, 'admin/lpr_app/gatedevice/test_gate.html', context)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccessEvent)
class AccessEventAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'gate', 'plate_normalized', 'decision', 'reason', 'mode', 'is_test', 'confidence', 'command_result')
    list_filter = ('decision', 'reason', 'mode', 'is_test', 'gate')
    search_fields = ('plate_normalized', 'plate_raw')
    date_hierarchy = 'timestamp'


@admin.register(GateConfigChange)
class GateConfigChangeAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'user', 'action', 'object_type', 'object_repr')
    list_filter = ('object_type', 'action')


# Customize admin site header and title
admin.site.site_header = 'VietinBankSchool LPR Admin'
admin.site.site_title = 'VietinBankSchool LPR'
admin.site.index_title = 'VietinBankSchool LPR administration'