import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import GateDeviceForm from './GateDeviceForm';
import { mockGateDevice } from '@/lib/gate-mock-data';

const meta: Meta<typeof GateDeviceForm> = {
  title: 'Gate/GateDeviceForm',
  component: GateDeviceForm,
  tags: ['autodocs'],
  args: {
    cameras: [{ id: 1, name: 'Camera vào' }, { id: 2, name: 'Camera ra' }, { id: 3, name: 'Camera làn xe máy' }],
    onSubmit: fn(async () => {}),
    onCancel: fn(),
  },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof GateDeviceForm>;

/** A new gate starts with a row for each direction. */
export const NewSimulated: Story = {};
export const Esp32: Story = {
  args: { gate: { ...mockGateDevice, controller_type: 'esp32', controller_url: 'http://192.168.1.50/' } },
};

/** ESP32-S3 + CC1101 sending the barrier remote's code. */
export const Esp32Radio: Story = {
  args: { gate: { ...mockGateDevice, controller_type: 'esp32_rf', controller_url: 'http://192.168.2.60/' } },
};

/** Opening on approach, for vehicles arriving at rush hour. */
export const OpenOnApproach: Story = {
  args: { gate: { ...mockGateDevice, approach_open: 'in', approach_hours: '06:30-08:00, 16:30-18:00' } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText('Chỉ trong các khung giờ')).toHaveValue('06:30-08:00, 16:30-18:00');
    // Turned off, the hours are not asked for
    await userEvent.selectOptions(canvas.getByLabelText('Mở khi xe tới gần'), 'off');
    await expect(canvas.queryByLabelText('Chỉ trong các khung giờ')).not.toBeInTheDocument();
  },
};
