from django.urls import path
from .views.api_views import (
    api_health_check, api_ocr_upload, metrics_view,
    api_image_list, api_image_detail, api_download_image,
    api_config, api_health_light, api_availability
)
from .views.file_views import download_image
from .views.auth_views import api_auth_login, api_auth_logout, api_auth_me
from .views import gate_views, gate_admin_views

app_name = 'lpr_app'

urlpatterns = [
    path('download/<int:image_id>/<str:image_type>/', download_image, name='download_image'),
    path('health/', api_health_check, name='health_check'),
    path('api/v1/ocr/', api_ocr_upload, name='api_ocr_upload'),
    path('api/v1/images/', api_image_list, name='api_image_list'),
    path('api/v1/images/<int:image_id>/', api_image_detail, name='api_image_detail'),
    path('api/v1/download/<int:image_id>/<str:image_type>/', api_download_image, name='api_download_image'),
    path('api/v1/config/', api_config, name='api_config'),
    path('api/v1/health-light/', api_health_light, name='api_health_light'),
    path('api/v1/availability/', api_availability, name='api_availability'),
    path('metrics/', metrics_view, name='metrics'),
    path('api/v1/auth/login/', api_auth_login, name='api_auth_login'),
    path('api/v1/auth/logout/', api_auth_logout, name='api_auth_logout'),
    path('api/v1/auth/me/', api_auth_me, name='api_auth_me'),

    # Gate agent (GATE_AGENT_TOKEN) and controller (device token)
    path('api/v1/gate/decide/', gate_views.api_gate_decide, name='api_gate_decide'),
    path('api/v1/gate/agent-config/', gate_views.api_gate_agent_config, name='api_gate_agent_config'),
    path('api/v1/gate/agent-status/', gate_views.api_gate_agent_status, name='api_gate_agent_status'),
    path('api/v1/gate/agent-commands/', gate_views.api_gate_agent_commands, name='api_gate_agent_commands'),
    path('api/v1/gate/events/<int:event_id>/command-result/', gate_views.api_gate_command_result, name='api_gate_command_result'),
    path('api/v1/gate/heartbeat/', gate_views.api_gate_heartbeat, name='api_gate_heartbeat'),
    path('api/v1/gate/sim/<int:gate_id>/<str:command>', gate_views.api_gate_simulator_device, name='api_gate_simulator_device'),

    # Gate operators
    path('api/v1/gate/status/', gate_views.api_gate_status, name='api_gate_status'),
    path('api/v1/gate/override/', gate_views.api_gate_override, name='api_gate_override'),
    path('api/v1/gate/events/<int:event_id>/image/<str:image_type>/', gate_views.api_gate_event_image, name='api_gate_event_image'),
    path('api/v1/access-events/', gate_admin_views.api_access_events, name='api_access_events'),
    path('api/v1/access-events/<int:event_id>/', gate_admin_views.api_access_event_detail, name='api_access_event_detail'),
    path('api/v1/vehicles/', gate_admin_views.api_vehicles, name='api_vehicles'),
    path('api/v1/vehicles/plate-preview/', gate_admin_views.api_plate_preview, name='api_plate_preview'),
    path('api/v1/vehicles/<int:vehicle_id>/', gate_admin_views.api_vehicle_detail, name='api_vehicle_detail'),

    # Gate admins
    path('api/v1/gate/cameras/', gate_admin_views.api_cameras, name='api_cameras'),
    path('api/v1/gate/cameras/presets/', gate_admin_views.api_camera_presets, name='api_camera_presets'),
    path('api/v1/gate/cameras/test/', gate_admin_views.api_camera_test, name='api_camera_test'),
    path('api/v1/gate/cameras/<int:camera_id>/', gate_admin_views.api_camera_detail, name='api_camera_detail'),
    path('api/v1/gate/devices/', gate_admin_views.api_gate_devices, name='api_gate_devices'),
    path('api/v1/gate/devices/<int:gate_id>/', gate_admin_views.api_gate_device_detail, name='api_gate_device_detail'),
    path('api/v1/gate/devices/<int:gate_id>/test-decide/', gate_admin_views.api_gate_test_decide, name='api_gate_test_decide'),
    path('api/v1/gate/devices/<int:gate_id>/simulator/', gate_admin_views.api_gate_simulator, name='api_gate_simulator'),
    path('api/v1/gate/config-changes/', gate_admin_views.api_config_changes, name='api_config_changes'),
]
