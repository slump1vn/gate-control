import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import LiveCameraView from './LiveCameraView';
import { mockSnapshot } from '@/lib/gate-mock-data';

const meta: Meta<typeof LiveCameraView> = {
  title: 'Gate/LiveCameraView',
  component: LiveCameraView,
  tags: ['autodocs'],
  args: {
    cameraId: 1,
    apiBase: '',
    intervalMs: 0,
    initialSrc: mockSnapshot,
    roi: { x: 0.35, y: 0.6, w: 0.3, h: 0.25 },
    errorLookup: async () => 'Authentication failed: wrong username or password',
  },
  decorators: [(Story) => <div className="max-w-xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof LiveCameraView>;

/** A frame with the read zone drawn on it. */
export const Streaming: Story = {};

export const WithoutReadZone: Story = { args: { showRoi: false } };

/** The camera refused the credentials; the endpoint's reason is shown. */
export const Failing: Story = { args: { initialSrc: '/does-not-exist.jpg' } };

export const Paused: Story = { args: { initialSrc: undefined, intervalMs: 0 } };
