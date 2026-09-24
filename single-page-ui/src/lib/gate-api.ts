import { getApiBase } from './api';

// Session-authenticated calls for the gate pages. The CSRF token comes from
// the /auth/ responses (the SPA may run on another origin than the API, so it
// cannot read the csrftoken cookie) and is sent on every unsafe request.

let _csrfToken = '';

export function setCsrfToken(token: string | null | undefined) {
  _csrfToken = token || '';
}

export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: Record<string, string[]>;
  constructor(message: string, status: number, code = '', fieldErrors: Record<string, string[]> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

// Listeners told when the API answers 401/403, so the auth context can
// notice an expired session.
type AuthFailureListener = (status: number) => void;
const _authFailureListeners = new Set<AuthFailureListener>();

export function onAuthFailure(listener: AuthFailureListener): () => void {
  _authFailureListeners.add(listener);
  return () => { _authFailureListeners.delete(listener); };
}

async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const method = (rest.method || 'GET').toUpperCase();
  const headers = new Headers(rest.headers);
  if (json !== undefined) headers.set('Content-Type', 'application/json');
  if (UNSAFE.has(method) && _csrfToken) headers.set('X-CSRFToken', _csrfToken);

  const base = await getApiBase();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...rest,
      method,
      headers,
      credentials: 'include',
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    // The browser refused or could not make the request at all: the API address
    // is unreachable, blocked as mixed content, or blocked by CORS. "Failed to
    // fetch" on its own tells nobody where to look.
    const target = base || (typeof window !== 'undefined' ? window.location.origin : '');
    throw new ApiError(
      `Cannot reach the API at ${target}${path}. Check that the address is right, is reachable `
      + 'from this browser, and uses the same scheme (http/https) as this page.',
      0, 'NETWORK',
    );
  }

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      _authFailureListeners.forEach((l) => l(res.status));
    }
    throw new ApiError(
      data?.error || data?.detail || `Request failed (${res.status})`,
      res.status,
      data?.error_code || '',
      data?.errors || {},
    );
  }
  return data as T;
}

function query(params?: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// ---------------------------------------------------------------- auth

export type Role = 'gate_admin' | 'gate_operator';

export interface Session {
  authenticated: boolean;
  username: string | null;
  roles: Role[];
  csrf_token: string;
}

export async function getSession(): Promise<Session> {
  const s = await request<Session>('/api/v1/auth/me/');
  setCsrfToken(s.csrf_token);
  return s;
}

export async function login(username: string, password: string): Promise<Session> {
  const s = await request<Session>('/api/v1/auth/login/', { method: 'POST', json: { username, password } });
  setCsrfToken(s.csrf_token);
  return s;
}

export async function logout(): Promise<Session> {
  const s = await request<Session>('/api/v1/auth/logout/', { method: 'POST' });
  setCsrfToken(s.csrf_token);
  return s;
}

// ---------------------------------------------------------------- vehicles

export type VehicleType = 'car' | 'motorbike' | 'other';

export interface VehicleRef {
  id: number;
  plate_display: string;
  owner_name: string;
}

export interface Vehicle {
  id: number;
  plate_display: string;
  plate_normalized: string;
  owner_name: string;
  owner_phone: string;
  department: string;
  vehicle_type: VehicleType;
  valid_from: string | null;
  valid_until: string | null;
  is_active: boolean;
  access_status: string;
  allowed_now: boolean;
  notes: string;
  created_at: string | null;
  updated_at: string | null;
}

export type VehicleInput = Pick<
  Vehicle,
  'plate_display' | 'owner_name' | 'owner_phone' | 'department' | 'vehicle_type' | 'valid_from' | 'valid_until' | 'is_active' | 'notes'
>;

export interface PlatePreview {
  plate: string;
  normalized: string;
  existing_vehicle: { id: number; plate_display: string } | null;
}

export function getVehicles(params?: { q?: string; is_active?: 'true' | 'false'; page?: number; page_size?: number }) {
  return request<Paginated<Vehicle>>(`/api/v1/vehicles/${query(params)}`);
}

export function createVehicle(data: VehicleInput) {
  return request<Vehicle>('/api/v1/vehicles/', { method: 'POST', json: data });
}

export function updateVehicle(id: number, data: Partial<VehicleInput>) {
  return request<Vehicle>(`/api/v1/vehicles/${id}/`, { method: 'PATCH', json: data });
}

export function deactivateVehicle(id: number) {
  return request<Vehicle>(`/api/v1/vehicles/${id}/`, { method: 'DELETE' });
}

export function previewPlate(plate: string, signal?: AbortSignal) {
  return request<PlatePreview>(`/api/v1/vehicles/plate-preview/${query({ plate })}`, { signal });
}

// ---------------------------------------------------------------- access events

export type Decision = 'granted' | 'denied' | 'manual';
export type Command = 'open' | 'close' | 'stop';

export const REASONS: Record<string, string> = {
  whitelist_hit: 'Registered vehicle',
  no_plate: 'No plate detected',
  no_consensus: 'Frames disagree',
  low_confidence: 'Low confidence',
  not_registered: 'Not registered',
  expired: 'Registration expired',
  not_yet_valid: 'Registration not yet valid',
  inactive: 'Registration inactive',
  device_disabled: 'Gate disabled',
  inference_timeout: 'Recognition timed out',
  processing_error: 'Recognition failed',
  manual_override: 'Manual override',
  exit_free: 'Exit open to every vehicle',
};

export const DIRECTION_LABELS: Record<string, string> = {
  in: 'Entry',
  out: 'Exit',
};

export interface AccessEvent {
  id: number;
  timestamp: string | null;
  gate: { id: number; name: string } | null;
  camera: { id: number; name: string } | null;
  direction: '' | Direction;
  plate_raw: string;
  plate_normalized: string;
  confidence: number | null;
  frames_read: number;
  frames_agreed: number;
  vehicle: VehicleRef | null;
  near_miss_vehicle: VehicleRef | null;
  decision: Decision;
  reason: string;
  reason_display: string;
  mode: string;
  command: string;
  command_sent: boolean;
  command_result: string;
  decision_latency_ms: number | null;
  operator: string | null;
  is_test: boolean;
  has_image: boolean;
  has_processed_image: boolean;
  /** A frame was kept for this event, but its file is gone from the media directory. */
  frame_lost: boolean;
}

export interface AccessEventFilters {
  gate?: number | string;
  direction?: string;
  decision?: string;
  reason?: string;
  plate?: string;
  date_from?: string;
  date_to?: string;
  is_test?: 'true' | 'false' | '';
  page?: number;
  page_size?: number;
}

export function getAccessEvents(params?: AccessEventFilters) {
  return request<Paginated<AccessEvent>>(`/api/v1/access-events/${query(params as Record<string, string>)}`);
}

/** Path of a live camera frame; prefix it with getApiBase() and add a cache-buster. */
export function cameraSnapshotPath(cameraId: number): string {
  return `/api/v1/gate/cameras/${cameraId}/snapshot/`;
}

/** The reason a live frame failed, read from the same endpoint's JSON error. */
export async function cameraSnapshotError(cameraId: number): Promise<string> {
  try {
    const base = await getApiBase();
    const res = await fetch(`${base}${cameraSnapshotPath(cameraId)}`, { credentials: 'include' });
    if (res.ok) return '';
    const data = await res.json().catch(() => null);
    return data?.error || `Snapshot failed (${res.status})`;
  } catch {
    return 'Cannot reach the LPR service.';
  }
}

/** Path of an event's frame; prefix it with getApiBase(). Served only to logged-in operators. */
export function eventImagePath(eventId: number, type: 'original' | 'processed'): string {
  return `/api/v1/gate/events/${eventId}/image/${type}/`;
}

// ---------------------------------------------------------------- gate status and overrides

export type ArmState = 'up' | 'down' | 'moving' | 'stopped' | 'unknown';

export interface SimulatorState {
  arm_state: ArmState;
  phase: string;
  position: number;
  travel_seconds: number;
  auto_close_seconds: number;
  last_command: string | null;
  last_command_at: string | null;
  firmware_version: string;
}

export type Direction = 'in' | 'out';

/** What the agent's trigger last measured on a camera. */
export interface TriggerReadout {
  state?: 'idle' | 'motion' | 'occupied';
  fps?: number;
  motion?: number;
  presence?: number;
  motion_threshold?: number;
  presence_threshold?: number;
  grab_seconds?: number;
  at?: string;
}

export interface GateCamera {
  id: number;
  name: string;
  direction: Direction;
  is_enabled: boolean;
  roi: Roi | null;
  agent_status: string | null;
  agent_trigger: TriggerReadout | null;
}

export interface GateDevice {
  id: number;
  name: string;
  location: string;
  /** One per direction: a gate watches vehicles arriving and leaving. */
  cameras: GateCamera[];
  /** Why the cameras are not enough yet; empty when they are. */
  camera_warning: string;
  exit_policy: 'registered' | 'any';
  controller_type: 'esp32' | 'simulator';
  controller_url: string;
  controller_token_set: boolean;
  has_safety_input: boolean;
  is_enabled: boolean;
  online: boolean;
  last_seen: string | null;
  firmware_version: string;
  last_command_result: string;
  arm_state: ArmState | null;
  arm_state_at: string | null;
}

export interface GateStatus extends GateDevice {
  simulator: SimulatorState | null;
  last_event: AccessEvent | null;
}

export interface GateStatusResponse {
  mode: 'shadow' | 'live';
  gates: GateStatus[];
}

export function getGateStatus() {
  return request<GateStatusResponse>('/api/v1/gate/status/');
}

export function sendOverride(gateId: number, command: Command) {
  return request<{ success: boolean; event: AccessEvent }>('/api/v1/gate/override/', {
    method: 'POST',
    json: { gate_id: gateId, command },
  });
}

// ---------------------------------------------------------------- cameras (admin)

export type Vendor = 'hikvision' | 'dahua' | 'generic';

export interface Roi {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Camera {
  id: number;
  name: string;
  is_enabled: boolean;
  host: string;
  rtsp_port: number;
  http_port: number;
  username: string;
  password_set: boolean;
  vendor: Vendor;
  main_stream_path: string;
  sub_stream_path: string;
  snapshot_path: string;
  live_snapshot_path: string;
  prefer_snapshot: boolean;
  roi: Roi | null;
  motion_threshold: number;
  settle_ms: number;
  cooldown_s: number;
  config_version: number;
  updated_by: string | null;
  updated_at: string | null;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_error: string;
  agent_status: string | null;
  agent_status_at: string | null;
  agent_trigger: TriggerReadout | null;
  gates: { id: number; name: string }[];
}

export interface CameraInput {
  name: string;
  is_enabled: boolean;
  host: string;
  rtsp_port: number;
  http_port: number;
  username: string;
  password?: string;
  vendor: Vendor;
  main_stream_path: string;
  sub_stream_path: string;
  snapshot_path: string;
  live_snapshot_path: string;
  prefer_snapshot: boolean;
  roi: Roi | null;
  motion_threshold: number;
  settle_ms: number;
  cooldown_s: number;
}

export type CameraPresets = Record<Vendor, {
  main_stream_path: string;
  sub_stream_path: string;
  snapshot_path: string;
  live_snapshot_path: string;
}>;

export interface CameraTestStep {
  name: string;
  ok: boolean | null;
  message: string;
}

export interface CameraTestResult {
  ok: boolean;
  steps: CameraTestStep[];
  image: string | null;
  recorded: boolean;
}

export async function getCameras(): Promise<Camera[]> {
  return (await request<{ results: Camera[] }>('/api/v1/gate/cameras/')).results;
}

export function getCamera(id: number) {
  return request<Camera>(`/api/v1/gate/cameras/${id}/`);
}

export async function getCameraPresets(): Promise<CameraPresets> {
  return (await request<{ presets: CameraPresets }>('/api/v1/gate/cameras/presets/')).presets;
}

export function createCamera(data: CameraInput) {
  return request<Camera>('/api/v1/gate/cameras/', { method: 'POST', json: data });
}

export function updateCamera(id: number, data: Partial<CameraInput>) {
  return request<Camera>(`/api/v1/gate/cameras/${id}/`, { method: 'PATCH', json: data });
}

export function deleteCamera(id: number) {
  return request<{ success: boolean }>(`/api/v1/gate/cameras/${id}/`, { method: 'DELETE' });
}

/**
 * Test a camera. With only camera_id the saved camera is tested and the result
 * recorded; with form values the unsaved settings are tested (the stored
 * password is used when none is typed).
 */
export function testCamera(data: Partial<CameraInput> & { camera_id?: number }) {
  return request<CameraTestResult>('/api/v1/gate/cameras/test/', { method: 'POST', json: data });
}

// ---------------------------------------------------------------- gate devices (admin)

export interface GateDeviceInput {
  name: string;
  location: string;
  cameras: { camera: number; direction: Direction }[];
  exit_policy: 'registered' | 'any';
  controller_type: 'esp32' | 'simulator';
  controller_url: string;
  controller_token?: string;
  has_safety_input: boolean;
  is_enabled: boolean;
}

export async function getGateDevices(): Promise<GateDevice[]> {
  return (await request<{ results: GateDevice[] }>('/api/v1/gate/devices/')).results;
}

export function createGateDevice(data: GateDeviceInput) {
  return request<GateDevice>('/api/v1/gate/devices/', { method: 'POST', json: data });
}

export function updateGateDevice(id: number, data: Partial<GateDeviceInput>) {
  return request<GateDevice>(`/api/v1/gate/devices/${id}/`, { method: 'PATCH', json: data });
}

export function deleteGateDevice(id: number) {
  return request<{ success: boolean }>(`/api/v1/gate/devices/${id}/`, { method: 'DELETE' });
}

// ---------------------------------------------------------------- config audit (admin)

export interface ConfigChange {
  id: number;
  timestamp: string | null;
  user: string | null;
  object_type: string;
  object_id: number;
  object_repr: string;
  action: 'create' | 'update' | 'delete';
  changes: Record<string, unknown>;
}

export function getConfigChanges(params?: { object_type?: string; object_id?: number; page?: number; page_size?: number }) {
  return request<Paginated<ConfigChange>>(`/api/v1/gate/config-changes/${query(params)}`);
}

// ---------------------------------------------------------------- users (admin)

export interface AppUser {
  id: number;
  username: string;
  email: string;
  role: Role | null;
  is_active: boolean;
  is_superuser: boolean;
  date_joined: string | null;
  last_login: string | null;
}

export interface UserInput {
  username: string;
  email: string;
  role: Role;
  is_active: boolean;
  password?: string;
}

export function getUsers(params?: { q?: string; is_active?: 'true' | 'false'; page?: number; page_size?: number }) {
  return request<Paginated<AppUser>>(`/api/v1/users/${query(params)}`);
}

export function getUser(id: number) {
  return request<AppUser>(`/api/v1/users/${id}/`);
}

export function createUser(data: UserInput) {
  return request<AppUser>('/api/v1/users/', { method: 'POST', json: data });
}

export function updateUser(id: number, data: Partial<UserInput>) {
  return request<AppUser>(`/api/v1/users/${id}/`, { method: 'PATCH', json: data });
}

export function deactivateUser(id: number) {
  return request<AppUser>(`/api/v1/users/${id}/`, { method: 'DELETE' });
}

export async function getDjangoAdminUrl(path: string): Promise<string> {
  const base = await getApiBase();
  return `${base}/admin/${path}`;
}
