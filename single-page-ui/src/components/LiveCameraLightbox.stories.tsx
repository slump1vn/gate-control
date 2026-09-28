import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import LiveCameraLightbox from './LiveCameraLightbox';
import { mockGateCameras, mockSnapshot } from '@/lib/gate-mock-data';

const meta: Meta<typeof LiveCameraLightbox> = {
  title: 'Gate/LiveCameraLightbox',
  component: LiveCameraLightbox,
  args: {
    camera: mockGateCameras[0],
    gateName: 'Cổng tây',
    apiBase: '',
    intervalMs: 0,
    showRoi: true,
    initialSrc: mockSnapshot,
    onClose: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof LiveCameraLightbox>;

/** The lane as large as the screen allows, with its read zone and trigger readout. */
export const Open: Story = {};

export const ClosesOnButtonEscapeAndBackdrop: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    // Clicking the picture keeps it open
    await userEvent.click(canvas.getByAltText('Live camera view'));
    await expect(args.onClose).not.toHaveBeenCalled();
    await userEvent.click(canvas.getByRole('button', { name: 'Đóng' }));
    await expect(args.onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard('{Escape}');
    await expect(args.onClose).toHaveBeenCalledTimes(2);
    await userEvent.click(canvas.getByRole('dialog'));
    await expect(args.onClose).toHaveBeenCalledTimes(3);
  },
};
