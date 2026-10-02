import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import FirmwarePanel from './FirmwarePanel';
import type { ControllerFirmware, ControllerJob, ControllerJobsResponse } from '@/lib/gate-api';

const images: ControllerFirmware[] = [
  {
    id: 2, version: 'rf-756fac4', board: 'esp32dev', chip: 'esp32', size: 1074705, sha256: 'a'.repeat(64),
    notes: 'OTA', uploaded_by: 'installer', created_at: '2026-10-02T08:00:00Z',
  },
  {
    id: 1, version: 'rf-dev', board: 'esp32dev', chip: 'esp32', size: 1071376, sha256: 'b'.repeat(64),
    notes: '', uploaded_by: 'installer', created_at: '2026-10-01T08:00:00Z',
  },
  {
    id: 3, version: 'rf-756fac4', board: 'esp32s3', chip: 'esp32s3', size: 1102000, sha256: 'c'.repeat(64),
    notes: '', uploaded_by: 'installer', created_at: '2026-10-02T08:00:00Z',
  },
];

const job = (over: Partial<ControllerJob>): ControllerJob => ({
  id: 40, gate_id: 1, kind: 'update', button: '', seconds: 0, state: 'running', result: 'downloading',
  detail: { version: 'rf-756fac4', previous_version: 'rf-dev', stage: 'downloading', progress: 45 },
  created_by: 'installer', created_at: '2026-10-02T08:05:00Z', updated_at: '2026-10-02T08:05:10Z', ...over,
});

const response = (over: Partial<ControllerJobsResponse> = {}): ControllerJobsResponse => ({
  supported: true, buttons: {}, capture: null, update_supported: true, firmware_version: 'rf-dev',
  board: 'esp32dev', update: null, jobs: [], ...over,
});

const meta: Meta<typeof FirmwarePanel> = {
  title: 'Gate/FirmwarePanel',
  component: FirmwarePanel,
  tags: ['autodocs'],
  args: {
    gateId: 1,
    pollMs: 60000,
    load: async () => response(),
    loadFirmware: async () => ({ results: images }),
    upload: fn(async () => images[0]),
    remove: fn(async () => ({})),
    queue: fn(async () => job({ state: 'queued' })),
  },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof FirmwarePanel>;

export const Ready: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('esp32dev', { selector: 'dd' })).toBeInTheDocument();
    // The running version has no install button; another board's build cannot be installed
    const installs = await canvas.findAllByRole('button', { name: 'Cài' });
    await expect(installs).toHaveLength(2);
    await expect(installs[1]).toBeDisabled();
    window.confirm = () => true;
    await userEvent.click(installs[0]);
    await expect(args.queue).toHaveBeenCalledWith(1, 2);
  },
};

export const Downloading: Story = {
  args: { load: async () => response({ jobs: [job({})] }) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/Đang tải… 45%/)).toBeInTheDocument();
    for (const button of canvas.getAllByRole('button', { name: 'Cài' })) await expect(button).toBeDisabled();
  },
};

export const RolledBack: Story = {
  args: {
    load: async () => response({
      jobs: [job({ state: 'failed', result: 'rolled_back', detail: { version: 'rf-756fac4', stage: 'rolled_back' } })],
    }),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/đã tự quay về bản cũ/)).toBeInTheDocument();
  },
};

export const NotSupported: Story = {
  args: { load: async () => response({ update_supported: false }) },
};
