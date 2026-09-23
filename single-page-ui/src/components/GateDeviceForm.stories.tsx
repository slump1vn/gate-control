import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
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
