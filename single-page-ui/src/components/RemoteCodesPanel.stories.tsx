import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, userEvent, waitFor, within } from 'storybook/test';
import RemoteCodesPanel from './RemoteCodesPanel';
import { ApiError } from '@/lib/gate-api';
import type { ControllerJob, ControllerJobsResponse } from '@/lib/gate-api';

const NOW = new Date().toISOString();

function job(overrides: Partial<ControllerJob>): ControllerJob {
  return {
    id: 1, gate_id: 1, kind: 'capture', button: 'up', seconds: 6, state: 'queued', result: '', detail: {},
    created_by: 'installer', created_at: NOW, updated_at: NOW, ...overrides,
  };
}

const empty: ControllerJobsResponse = {
  supported: true, buttons: { up: { set: false }, down: { set: false }, stop: { set: false } }, capture: null, jobs: [],
};

const loader = (data: ControllerJobsResponse) => fn(async () => data);

const meta: Meta<typeof RemoteCodesPanel> = {
  title: 'Gate/RemoteCodesPanel',
  component: RemoteCodesPanel,
  args: { gateId: 1, pollMs: 60000, load: loader(empty), queue: fn(async () => job({})) },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof RemoteCodesPanel>;

/** Nothing stored yet. */
export const Empty: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    const buttons = await canvas.findAllByRole('button', { name: 'Capture' });
    await userEvent.click(buttons[0]);
    await expect(args.queue).toHaveBeenCalledWith(1, { kind: 'capture', button: 'up', seconds: 6 });
  },
};

/** The controller is listening for UP: hold the remote button now. */
export const Listening: Story = {
  args: { load: loader({ ...empty, capture: { state: 'capturing', button: 'up', job: 4 },
    jobs: [job({ id: 4, state: 'running', result: 'capturing' })] }) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/Giữ nút remote ngay/)).toBeInTheDocument();
    // One job at a time
    for (const b of canvas.getAllByRole('button', { name: 'Capture' })) await expect(b).toBeDisabled();
  },
};

/** UP was heard: keep it. */
export const Captured: Story = {
  args: {
    load: loader({
      ...empty,
      capture: { state: 'captured', button: 'up', job: 4, fingerprint: '525403a6', bits: 24, pulse_us: 348, frames: 6 },
      jobs: [job({ id: 4, state: 'done', result: 'captured', detail: { fingerprint: '525403a6', bits: 24, pulse_us: 348 } })],
    }),
  },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Lưu làm nút LÊN' }));
    await expect(args.queue).toHaveBeenCalledWith(1, { kind: 'save_code', button: 'up', seconds: 6 });
    await expect(canvas.getByText('525403a6')).toBeInTheDocument();
  },
};

/** The remote was too far, or pressed too briefly. */
export const NothingHeard: Story = {
  args: { load: loader({ ...empty, capture: { state: 'nothing', button: 'down', job: 5, frames: 0, edges: 40 },
    jobs: [job({ id: 5, button: 'down', state: 'failed', result: 'nothing' })] }) },
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByText(/Không nghe được mã nào hai lần/)).toBeInTheDocument();
  },
};

/** All three buttons stored. */
export const AllStored: Story = {
  args: {
    load: loader({
      ...empty,
      buttons: {
        up: { set: true, fingerprint: '525403a6', bits: 24, pulse_us: 350 },
        down: { set: true, fingerprint: '9f00c1e2', bits: 24, pulse_us: 350 },
        stop: { set: true, fingerprint: '03bd77aa', bits: 24, pulse_us: 350 },
      },
      capture: { state: 'saved', button: 'stop' },
      jobs: [job({ id: 9, kind: 'save_code', button: 'stop', state: 'done', result: 'saved' })],
    }),
  },
};

/** A second job while one is still running is refused by the service. */
export const Busy: Story = {
  args: { queue: fn(async () => { throw new ApiError('busy', 409, 'BUSY'); }) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click((await canvas.findAllByRole('button', { name: 'Capture' }))[1]);
    await waitFor(() => expect(canvas.getByText(/còn đang làm bước trước/)).toBeInTheDocument());
  },
};

export const NotSupported: Story = {
  args: { load: loader({ ...empty, supported: false }) },
};

export const Simulated: Story = { args: { simulated: true } };
