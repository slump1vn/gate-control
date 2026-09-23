import type {
  AccessEvent, Camera, CameraPresets, CameraTestResult, ConfigChange, GateDevice, GateStatus, Vehicle,
} from './gate-api';

export const mockVehicle: Vehicle = {
  id: 1,
  plate_display: '30A-123.45',
  plate_normalized: '30A12345',
  owner_name: 'Nguyễn Văn An',
  owner_phone: '0901234567',
  department: 'Phòng Đào tạo',
  vehicle_type: 'car',
  valid_from: null,
  valid_until: '2026-12-31T17:00:00Z',
  is_active: true,
  access_status: 'whitelist_hit',
  allowed_now: true,
  notes: '',
  created_at: '2026-09-01T02:00:00Z',
  updated_at: '2026-09-01T02:00:00Z',
};

export const mockVehicles: Vehicle[] = [
  mockVehicle,
  {
    ...mockVehicle, id: 2, plate_display: '29B1-234.56', plate_normalized: '29B123456', owner_name: 'Trần Thị Bình',
    vehicle_type: 'motorbike', department: 'Hành chính', valid_until: '2026-06-30T17:00:00Z',
    access_status: 'expired', allowed_now: false,
  },
  {
    ...mockVehicle, id: 3, plate_display: '51G-888.88', plate_normalized: '51G88888', owner_name: 'Lê Minh Cường',
    department: 'Khách mời', is_active: false, access_status: 'inactive', allowed_now: false, valid_until: null,
  },
];

export const mockEventGranted: AccessEvent = {
  id: 101,
  timestamp: '2026-09-22T01:15:00Z',
  gate: { id: 1, name: 'Cổng chính' },
  camera: { id: 1, name: 'Camera vào' },
  direction: 'in',
  plate_raw: '30A-123.45',
  plate_normalized: '30A12345',
  confidence: 0.96,
  frames_read: 3,
  frames_agreed: 3,
  vehicle: { id: 1, plate_display: '30A-123.45', owner_name: 'Nguyễn Văn An' },
  near_miss_vehicle: null,
  decision: 'granted',
  reason: 'whitelist_hit',
  reason_display: 'Registered vehicle',
  mode: 'shadow',
  command: 'open',
  command_sent: true,
  command_result: 'opening',
  decision_latency_ms: 2140,
  operator: null,
  is_test: false,
  has_image: true,
  has_processed_image: true,
};

export const mockEventNearMiss: AccessEvent = {
  ...mockEventGranted,
  id: 102,
  timestamp: '2026-09-22T01:20:00Z',
  plate_raw: '30A-128.45',
  plate_normalized: '30A12845',
  confidence: 0.84,
  frames_agreed: 2,
  vehicle: null,
  near_miss_vehicle: { id: 1, plate_display: '30A-123.45', owner_name: 'Nguyễn Văn An' },
  decision: 'denied',
  reason: 'not_registered',
  reason_display: 'Not registered',
  command: '',
  command_sent: false,
  command_result: '',
};

export const mockEventNoPlate: AccessEvent = {
  ...mockEventNearMiss,
  id: 103,
  plate_raw: '',
  plate_normalized: '',
  confidence: null,
  frames_agreed: 0,
  near_miss_vehicle: null,
  reason: 'no_plate',
  reason_display: 'No plate detected',
  has_image: false,
  has_processed_image: false,
};

export const mockEventManual: AccessEvent = {
  ...mockEventGranted,
  id: 104,
  plate_raw: '',
  plate_normalized: '',
  confidence: null,
  frames_read: 0,
  frames_agreed: 0,
  vehicle: null,
  decision: 'manual',
  reason: 'manual_override',
  reason_display: 'Manual override',
  command: 'open',
  command_sent: false,
  command_result: 'not_sent_shadow_mode',
  operator: 'baove1',
  has_image: false,
  has_processed_image: false,
};

export const mockGateCameras = [
  { id: 1, name: 'Camera vào', direction: 'in' as const, is_enabled: true,
    roi: { x: 0.25, y: 0.45, w: 0.5, h: 0.4 }, agent_status: 'streaming' },
  { id: 2, name: 'Camera ra', direction: 'out' as const, is_enabled: true,
    roi: null, agent_status: 'streaming' },
];

export const mockGateDevice: GateDevice = {
  id: 1,
  name: 'Cổng chính',
  location: 'Cổng trước',
  cameras: mockGateCameras,
  camera_warning: '',
  exit_policy: 'registered',
  controller_type: 'simulator',
  controller_url: '',
  controller_token_set: true,
  has_safety_input: false,
  is_enabled: true,
  online: true,
  last_seen: null,
  firmware_version: 'simulator-1',
  last_command_result: 'open: opening',
  arm_state: 'up',
  arm_state_at: '2026-09-22T01:15:03Z',
};

export const mockGateStatusSimulated: GateStatus = {
  ...mockGateDevice,
  simulator: {
    arm_state: 'moving',
    phase: 'moving_up',
    position: 0.55,
    travel_seconds: 3,
    auto_close_seconds: 10,
    last_command: 'open',
    last_command_at: '2026-09-22T01:15:00Z',
    firmware_version: 'simulator-1',
  },
  arm_state: 'moving',
  last_event: mockEventGranted,
};

export const mockGateStatusEsp32Offline: GateStatus = {
  ...mockGateDevice,
  id: 2,
  name: 'Cổng sau',
  controller_type: 'esp32',
  controller_url: 'http://192.168.1.50/',
  online: false,
  last_seen: '2026-09-22T00:40:00Z',
  firmware_version: '1.0.0',
  arm_state: 'down',
  simulator: null,
  cameras: [{ id: 3, name: 'Camera cổng sau', direction: 'in', is_enabled: true, roi: null, agent_status: 'auth_failed' }],
  camera_warning: '1 of 2 cameras assigned. A gate needs one watching vehicles arriving and one watching them leave.',
  exit_policy: 'any',
  last_event: mockEventNearMiss,
};

export const mockCameraPresets: CameraPresets = {
  hikvision: {
    main_stream_path: '/Streaming/Channels/101',
    sub_stream_path: '/Streaming/Channels/102',
    snapshot_path: '/ISAPI/Streaming/channels/101/picture',
    live_snapshot_path: '/ISAPI/Streaming/channels/102/picture',
  },
  dahua: {
    main_stream_path: '/cam/realmonitor?channel=1&subtype=0',
    sub_stream_path: '/cam/realmonitor?channel=1&subtype=1',
    snapshot_path: '/cgi-bin/snapshot.cgi',
    live_snapshot_path: '/cgi-bin/snapshot.cgi?channel=1&subtype=1',
  },
  generic: { main_stream_path: '', sub_stream_path: '', snapshot_path: '', live_snapshot_path: '' },
};

export const mockCamera: Camera = {
  id: 1,
  name: 'Camera cổng chính',
  is_enabled: true,
  host: '192.168.1.64',
  rtsp_port: 554,
  http_port: 80,
  username: 'admin',
  password_set: true,
  vendor: 'hikvision',
  ...mockCameraPresets.hikvision,
  prefer_snapshot: true,
  roi: { x: 0.25, y: 0.45, w: 0.5, h: 0.4 },
  motion_threshold: 0.02,
  settle_ms: 800,
  cooldown_s: 5,
  config_version: 4,
  updated_by: 'admin',
  updated_at: '2026-09-21T09:00:00Z',
  last_test_at: '2026-09-21T09:01:00Z',
  last_test_ok: true,
  last_test_error: '',
  agent_status: 'streaming',
  agent_status_at: '2026-09-22T01:14:50Z',
  gates: [{ id: 1, name: 'Cổng chính' }],
};

/** A stand-in camera frame (a car with a plate at the barrier), as an SVG data URL. */
export const mockSnapshot = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">'
  + '<rect width="640" height="360" fill="#3f4a55"/><rect y="220" width="640" height="140" fill="#555f69"/>'
  + '<rect x="200" y="150" width="240" height="150" rx="24" fill="#d8dde3"/>'
  + '<rect x="225" y="170" width="190" height="55" rx="8" fill="#26303a"/>'
  + '<rect x="270" y="245" width="100" height="32" rx="3" fill="#fff" stroke="#111" stroke-width="2"/>'
  + '<text x="320" y="268" font-family="monospace" font-size="16" text-anchor="middle" fill="#111">30A-123.45</text>'
  + '</svg>',
);

export const mockCameraTestOk: CameraTestResult = {
  ok: true,
  recorded: true,
  image: mockSnapshot,
  steps: [
    { name: 'host', ok: true, message: '192.168.1.64 resolves to 192.168.1.64' },
    { name: 'rtsp_port', ok: true, message: 'RTSP port 554 is reachable' },
    { name: 'snapshot', ok: true, message: 'Received 142 KB JPEG snapshot' },
  ],
};

export const mockCameraTestFailed: CameraTestResult = {
  ok: false,
  recorded: false,
  image: null,
  steps: [
    { name: 'host', ok: true, message: '192.168.1.64 resolves to 192.168.1.64' },
    { name: 'rtsp_port', ok: true, message: 'RTSP port 554 is reachable' },
    { name: 'snapshot', ok: false, message: 'Authentication failed: wrong username or password' },
  ],
};

export const mockConfigChanges: ConfigChange[] = [
  {
    id: 3, timestamp: '2026-09-21T09:00:00Z', user: 'admin', object_type: 'camera', object_id: 1,
    object_repr: 'Camera cổng chính', action: 'update',
    changes: { roi_x: { old: null, new: 0.25 }, roi_w: { old: null, new: 0.5 }, password: 'changed' },
  },
  {
    id: 1, timestamp: '2026-09-20T08:00:00Z', user: 'admin', object_type: 'camera', object_id: 1,
    object_repr: 'Camera cổng chính', action: 'create',
    changes: { name: { old: null, new: 'Camera cổng chính' }, host: { old: null, new: '192.168.1.64' } },
  },
];
